"""mock_rpc — in-process JSON-RPC mock for the evm-token-due-diligence tests.

Built on http.server + threading; binds 127.0.0.1 only, never touches the network.

    url, server = start_mock({"eth_chainId": "0x1", "eth_getCode": lambda params: "0x6001"})
    ...
    server.stop()

Handlers map a JSON-RPC method name to either a constant result or a callable
``fn(params) -> result``. A handler may raise:

    JsonRpcError(code, message)   -> the server answers a JSON-RPC error object
    HttpStatus(code)              -> the server answers a bare HTTP status (e.g. 429)

Every request is recorded on ``server.calls`` as {"method", "params"} so tests can
assert that no non-allowlisted method was ever sent (see ``non_allowlisted_methods``
and ``assert_only_allowlisted``). Unknown methods are recorded too and answered with
JSON-RPC error -32601, which is what a real node does.

Also exports tiny ABI helpers for building return data in tests:
``abi_word``, ``abi_address``, ``abi_bool``, ``abi_string``, ``abi_bytes32_text``.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

__all__ = [
    "start_mock", "MockRpcServer", "JsonRpcError", "HttpStatus",
    "non_allowlisted_methods", "assert_only_allowlisted",
    "abi_word", "abi_address", "abi_bool", "abi_string", "abi_bytes32_text",
]


class JsonRpcError(Exception):
    """Raise from a handler to answer with a JSON-RPC error object (HTTP 200)."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class HttpStatus(Exception):
    """Raise from a handler to answer with a bare HTTP status code and no JSON body."""

    def __init__(self, status: int, body: bytes = b""):
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


class MockRpcServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], handlers: dict[str, Any], path: str):
        super().__init__(address, _Handler)
        self.handlers: dict[str, Any] = dict(handlers)
        self.path_expected = path
        self.calls: list[dict] = []
        self._lock = threading.Lock()
        self.thread: Optional[threading.Thread] = None

    # recording -----------------------------------------------------------------
    def record(self, method: Any, params: Any) -> None:
        with self._lock:
            self.calls.append({"method": method, "params": params})

    def methods(self) -> list[str]:
        """Every method name received, in order (duplicates kept)."""
        with self._lock:
            return [c["method"] for c in self.calls]

    def count(self, method: str) -> int:
        return sum(1 for m in self.methods() if m == method)

    def params_for(self, method: str) -> list[Any]:
        with self._lock:
            return [c["params"] for c in self.calls if c["method"] == method]

    def reset(self) -> None:
        with self._lock:
            self.calls.clear()

    # lifecycle -----------------------------------------------------------------
    def stop(self) -> None:
        self.shutdown()
        self.server_close()
        if self.thread is not None:
            self.thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:  # silence
        return

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_status(self, status: int, body: bytes = b"") -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (http.server naming)
        server: MockRpcServer = self.server  # type: ignore[assignment]
        if self.path != server.path_expected:
            self._send_status(404, b"unknown path")
            return
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw.decode("utf-8"))
        except Exception:
            self._send_status(400, b"bad json")
            return
        if not isinstance(req, dict):
            # batch requests are not used by the tools under test
            self._send_json({"jsonrpc": "2.0", "id": None,
                             "error": {"code": -32600, "message": "batch not supported by mock"}})
            return
        method = req.get("method")
        params = req.get("params", [])
        rid = req.get("id")
        server.record(method, params)
        if method not in server.handlers:
            self._send_json({"jsonrpc": "2.0", "id": rid,
                             "error": {"code": -32601, "message": f"the method {method} does not exist/is not available"}})
            return
        handler = server.handlers[method]
        try:
            result = handler(params) if callable(handler) else handler
        except HttpStatus as hs:
            self._send_status(hs.status, hs.body)
            return
        except JsonRpcError as je:
            err: dict[str, Any] = {"code": je.code, "message": je.message}
            if je.data is not None:
                err["data"] = je.data
            self._send_json({"jsonrpc": "2.0", "id": rid, "error": err})
            return
        self._send_json({"jsonrpc": "2.0", "id": rid, "result": result})


def start_mock(handlers: dict[str, Any], port: int = 0, path: str = "/") -> tuple[str, MockRpcServer]:
    """Start a loopback JSON-RPC mock. Returns (url, server). Call server.stop() when done.

    ``path`` lets a test serve at a URL containing a fake key segment (e.g. "/v2/<fakekey>")
    to assert that the tool under test redacts it.
    """
    if not path.startswith("/"):
        path = "/" + path
    server = MockRpcServer(("127.0.0.1", port), handlers, path)
    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    t.start()
    server.thread = t
    host, bound_port = server.server_address[0], server.server_address[1]
    return f"http://{host}:{bound_port}{path}", server


# ------------------------------------------------------------------------------------
# allowlist assertions
# ------------------------------------------------------------------------------------
def non_allowlisted_methods(server: MockRpcServer) -> list[str]:
    """Methods the server received that are NOT in ddcore.READ_ONLY_METHODS (should be empty)."""
    import ddcore  # imported lazily so the mock stays usable without the scripts path

    return sorted({m for m in server.methods() if m not in ddcore.READ_ONLY_METHODS})


def assert_only_allowlisted(server: MockRpcServer) -> None:
    bad = non_allowlisted_methods(server)
    if bad:
        raise AssertionError(f"non-allowlisted JSON-RPC methods were sent: {bad}")


# ------------------------------------------------------------------------------------
# ABI return-data helpers (hex strings, 0x-prefixed)
# ------------------------------------------------------------------------------------
def abi_word(value: int) -> str:
    if value < 0 or value >= (1 << 256):
        raise ValueError("word out of range")
    return "0x" + value.to_bytes(32, "big").hex()


def abi_address(address: str) -> str:
    body = address[2:] if address.startswith("0x") else address
    if len(body) != 40:
        raise ValueError("address must be 20 bytes")
    return "0x" + body.lower().rjust(64, "0")


def abi_bool(value: bool) -> str:
    return abi_word(1 if value else 0)


def abi_string(text: str) -> str:
    raw = text.encode("utf-8")
    padded = raw + b"\x00" * ((32 - len(raw) % 32) % 32)
    return "0x" + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex() + padded.hex()


def abi_bytes32_text(text: str) -> str:
    """A bytes32-style 'string' (legacy tokens): text left-aligned, zero padded, one word."""
    raw = text.encode("utf-8")
    if len(raw) > 32:
        raise ValueError("bytes32 text too long")
    return "0x" + raw.ljust(32, b"\x00").hex()
