#!/usr/bin/env python3
"""ddcore — shared, dependency-free primitives for the evm-token-due-diligence skill.

Provides:
  * keccak256 (pure Python; NOT hashlib.sha3_256, whose padding differs)
  * EIP-55 checksum handling and strict address validation
  * minimal ABI word encoding/decoding for static types and strings
  * EIP-1967 / EIP-1822 storage slots and the empty-code hash
  * a read-only JSON-RPC client with a method allowlist, a response cache keyed by
    (host, method, params), URL credential redaction, and coverage-limitation error
    classification (timeouts, rate limits, DNS failures are *coverage* problems,
    never token findings)

Python 3.10+ standard library only. No web3, no requests.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

__all__ = [
    "keccak256", "keccak256_hex", "selector",
    "is_hex_address", "is_checksum_valid", "to_checksum_address", "normalize_address", "validate_address",
    "is_hex32", "looks_like_placeholder_hash", "hex_to_int", "int_to_hex",
    "EMPTY_CODE_HASH", "EIP1967_IMPLEMENTATION_SLOT", "EIP1967_ADMIN_SLOT", "EIP1967_BEACON_SLOT",
    "EIP1822_LOGIC_SLOT", "ZERO_ADDRESS",
    "encode_word", "encode_static", "decode_uint", "decode_address", "decode_abi_string",
    "redact_url", "RpcClient", "RpcPolicyError", "RpcCoverageError", "ResponseCache", "READ_ONLY_METHODS",
    "iso_utc", "parse_iso_utc", "sha256_file", "sha256_bytes",
]

# --------------------------------------------------------------------------------------
# Keccak-256 (Ethereum flavour: pad10*1 with 0x01 domain byte, rate 136 bytes)
# --------------------------------------------------------------------------------------
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_ROT = [
    [0, 36, 3, 41, 18],
    [1, 44, 10, 45, 2],
    [62, 6, 43, 15, 61],
    [28, 55, 25, 21, 56],
    [27, 20, 39, 8, 14],
]
_MASK64 = (1 << 64) - 1


def _rol(x: int, n: int) -> int:
    n %= 64
    if n == 0:
        return x
    return ((x << n) | (x >> (64 - n))) & _MASK64


def _keccak_f1600(A: list[list[int]]) -> list[list[int]]:
    for rc in _RC:
        C = [A[x][0] ^ A[x][1] ^ A[x][2] ^ A[x][3] ^ A[x][4] for x in range(5)]
        D = [C[(x - 1) % 5] ^ _rol(C[(x + 1) % 5], 1) for x in range(5)]
        A = [[A[x][y] ^ D[x] for y in range(5)] for x in range(5)]
        B = [[0] * 5 for _ in range(5)]
        for x in range(5):
            for y in range(5):
                B[y][(2 * x + 3 * y) % 5] = _rol(A[x][y], _ROT[x][y])
        A = [[B[x][y] ^ ((~B[(x + 1) % 5][y]) & B[(x + 2) % 5][y]) for y in range(5)] for x in range(5)]
        A[0][0] ^= rc
    return A


def keccak256(data: bytes) -> bytes:
    """Keccak-256 of *data* (Ethereum's hash, distinct from NIST SHA3-256)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("keccak256 expects bytes")
    rate = 136
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate:
        padded.append(0x00)
    padded[-1] |= 0x80
    A = [[0] * 5 for _ in range(5)]
    for off in range(0, len(padded), rate):
        block = padded[off:off + rate]
        for i in range(rate // 8):
            lane = int.from_bytes(block[8 * i:8 * i + 8], "little")
            A[i % 5][i // 5] ^= lane
        A = _keccak_f1600(A)
    out = bytearray()
    for i in range(4):
        out += A[i % 5][i // 5].to_bytes(8, "little")
    return bytes(out)


def keccak256_hex(data: bytes) -> str:
    return "0x" + keccak256(data).hex()


def selector(signature: str) -> str:
    """4-byte function selector for a canonical signature like 'transfer(address,uint256)'."""
    sig = signature.replace(" ", "")
    return "0x" + keccak256(sig.encode("ascii")).hex()[:8]


# --------------------------------------------------------------------------------------
# Addresses
# --------------------------------------------------------------------------------------
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_HEX32_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
EMPTY_CODE_HASH = "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def is_hex_address(s: Any) -> bool:
    return isinstance(s, str) and bool(_ADDR_RE.match(s))


def to_checksum_address(s: str) -> str:
    if not is_hex_address(s):
        raise ValueError(f"not a hex address: {s!r}")
    body = s[2:].lower()
    h = keccak256(body.encode("ascii")).hex()
    out = []
    for ch, nib in zip(body, h):
        out.append(ch.upper() if ch in "abcdef" and int(nib, 16) >= 8 else ch)
    return "0x" + "".join(out)


def is_checksum_valid(s: str) -> bool:
    """True for all-lowercase or all-uppercase hex (no checksum encoded) and for valid EIP-55 mixed case."""
    if not is_hex_address(s):
        return False
    body = s[2:]
    if body == body.lower() or body == body.upper():
        return True
    return to_checksum_address(s) == s


def normalize_address(s: str) -> str:
    if not is_hex_address(s):
        raise ValueError(f"not a hex address: {s!r}")
    return "0x" + s[2:].lower()


def validate_address(s: Any) -> tuple[bool, Optional[str]]:
    """Strict validation: shape, then EIP-55 for mixed case. Returns (ok, reason)."""
    if not isinstance(s, str):
        return False, "address is not a string"
    if not s.startswith("0x"):
        return False, "address must start with 0x"
    if len(s) != 42:
        return False, f"address must be 42 characters, got {len(s)}"
    if not re.match(r"^0x[0-9a-fA-F]{40}$", s):
        return False, "address contains non-hex characters"
    if not is_checksum_valid(s):
        return False, "mixed-case address fails EIP-55 checksum"
    return True, None


# --------------------------------------------------------------------------------------
# Hashes / hex
# --------------------------------------------------------------------------------------
def is_hex32(s: Any) -> bool:
    return isinstance(s, str) and bool(_HEX32_RE.match(s))


def looks_like_placeholder_hash(s: Any) -> tuple[bool, Optional[str]]:
    """Detect obviously fake 32-byte hashes (all zero, all one nibble, tiny entropy, non-hex)."""
    if not isinstance(s, str):
        return True, "hash is not a string"
    if not is_hex32(s):
        return True, "hash is not 0x + 64 hex characters"
    body = s[2:].lower()
    if len(set(body)) == 1:
        return True, "hash is a single repeated nibble (e.g. all zeros)"
    if len(set(body)) <= 3:
        return True, "hash has almost no entropy (<=3 distinct nibbles)"
    for pat in ("deadbeef", "cafebabe", "badf00d", "1234567890", "abcdefabcdef", "0badc0de"):
        if body.count(pat) >= 2:
            return True, f"hash repeats a placeholder pattern ({pat})"
    return False, None


def hex_to_int(s: Any) -> int:
    if isinstance(s, int):
        return s
    if not isinstance(s, str) or not re.match(r"^0x[0-9a-fA-F]+$", s):
        raise ValueError(f"not a hex quantity: {s!r}")
    return int(s, 16)


def int_to_hex(i: int) -> str:
    if i < 0:
        raise ValueError("negative quantity")
    return hex(i)


def _slot_minus_one(label: str) -> str:
    return "0x" + (int.from_bytes(keccak256(label.encode()), "big") - 1).to_bytes(32, "big").hex()


EIP1967_IMPLEMENTATION_SLOT = _slot_minus_one("eip1967.proxy.implementation")
EIP1967_ADMIN_SLOT = _slot_minus_one("eip1967.proxy.admin")
EIP1967_BEACON_SLOT = _slot_minus_one("eip1967.proxy.beacon")
EIP1822_LOGIC_SLOT = keccak256_hex(b"PROXIABLE")


# --------------------------------------------------------------------------------------
# Minimal ABI helpers (static types + string)
# --------------------------------------------------------------------------------------
def encode_word(kind: str, value: Any) -> bytes:
    if kind == "address":
        if not is_hex_address(value):
            raise ValueError(f"bad address {value!r}")
        return bytes.fromhex(value[2:]).rjust(32, b"\x00")
    if kind == "uint":
        v = int(value)
        if v < 0 or v >= (1 << 256):
            raise ValueError("uint out of range")
        return v.to_bytes(32, "big")
    if kind == "int":
        v = int(value)
        if v < -(1 << 255) or v >= (1 << 255):
            raise ValueError("int out of range")
        return (v & ((1 << 256) - 1)).to_bytes(32, "big")
    if kind == "bool":
        return (1 if value else 0).to_bytes(32, "big")
    if kind == "bytes32":
        if isinstance(value, str):
            value = bytes.fromhex(value[2:] if value.startswith("0x") else value)
        if len(value) != 32:
            raise ValueError("bytes32 must be 32 bytes")
        return bytes(value)
    raise ValueError(f"unsupported static kind {kind}")


def encode_static(items: Iterable[tuple[str, Any]]) -> bytes:
    return b"".join(encode_word(k, v) for k, v in items)


def _to_bytes(data: Any) -> bytes:
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    if isinstance(data, str):
        h = data[2:] if data.startswith("0x") else data
        return bytes.fromhex(h) if h else b""
    raise TypeError("expected hex string or bytes")


def decode_uint(data: Any) -> int:
    b = _to_bytes(data)
    if len(b) < 32:
        raise ValueError("return data shorter than 32 bytes")
    return int.from_bytes(b[:32], "big")


def decode_address(data: Any) -> str:
    b = _to_bytes(data)
    if len(b) < 32:
        raise ValueError("return data shorter than 32 bytes")
    return to_checksum_address("0x" + b[12:32].hex())


def decode_abi_string(data: Any) -> tuple[Optional[str], str]:
    """Decode an ABI `string` return. Returns (value, status) with status in
    {'resolved','nonstandard','unresolved'}. A bare 32-byte word is treated as a
    bytes32 'string' (nonstandard, e.g. some legacy tokens). Empty return data is unresolved."""
    try:
        b = _to_bytes(data)
    except Exception:
        return None, "unresolved"
    if len(b) == 0:
        return None, "unresolved"
    if len(b) == 32:
        raw = b.rstrip(b"\x00")
        try:
            txt = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, "unresolved"
        if not txt or not txt.isprintable():
            return None, "unresolved"
        return txt, "nonstandard"
    if len(b) < 64:
        return None, "unresolved"
    try:
        offset = int.from_bytes(b[:32], "big")
        length = int.from_bytes(b[offset:offset + 32], "big")
        raw = b[offset + 32: offset + 32 + length]
        if len(raw) != length:
            return None, "unresolved"
        txt = raw.decode("utf-8", errors="strict")
    except Exception:
        return None, "unresolved"
    return txt, "resolved"


# --------------------------------------------------------------------------------------
# Time / hashing helpers
# --------------------------------------------------------------------------------------
def iso_utc(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_utc(s: str) -> int:
    if not isinstance(s, str) or not re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", s):
        raise ValueError(f"not a canonical UTC timestamp: {s!r}")
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------------------
# URL redaction
# --------------------------------------------------------------------------------------
_KEYISH = re.compile(r"^[A-Za-z0-9_\-]{20,}$")


def redact_url(url: str) -> str:
    """Strip userinfo, key-like path segments and all query values so an endpoint can be
    recorded in evidence without leaking credentials."""
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return "<unparseable-url>"
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    segs = []
    for seg in p.path.split("/"):
        if seg and _KEYISH.match(seg):
            segs.append("<redacted>")
        else:
            segs.append(seg)
    path = "/".join(segs)
    query = ""
    if p.query:
        keys = [k for k, _ in urllib.parse.parse_qsl(p.query, keep_blank_values=True)]
        query = "?" + "&".join(f"{k}=<redacted>" for k in keys)
    return f"{p.scheme}://{host}{path}{query}"


# --------------------------------------------------------------------------------------
# Read-only JSON-RPC client
# --------------------------------------------------------------------------------------
READ_ONLY_METHODS = frozenset({
    "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getBlockByHash", "eth_getCode",
    "eth_call", "eth_getStorageAt", "eth_getBalance", "eth_getTransactionCount",
    "eth_getTransactionByHash", "eth_getTransactionReceipt", "eth_getLogs", "eth_getBlockReceipts",
    "eth_estimateGas", "eth_gasPrice", "eth_feeHistory", "net_version", "web3_clientVersion",
    "debug_traceTransaction", "debug_traceCall", "trace_transaction", "trace_replayTransaction",
    "trace_block", "trace_filter",
    # fork-identification methods (read-only introspection)
    "anvil_nodeInfo", "hardhat_metadata",
})
_BLOCK_TAGS = {"latest", "pending", "safe", "finalized", "earliest"}


class RpcPolicyError(Exception):
    """Raised when a caller attempts a non-allowlisted (potentially state-changing) method."""


class RpcCoverageError(Exception):
    """An RPC/infrastructure failure. This is a *coverage limitation*, never a token finding."""

    def __init__(self, kind: str, message: str, method: str = "", params: Any = None):
        super().__init__(f"[{kind}] {method}: {message}")
        self.kind = kind
        self.message = message
        self.method = method
        self.params = params

    def as_limitation(self, limitation_id: str = "L?") -> dict:
        return {
            "limitation_id": limitation_id,
            "kind": self.kind,
            "description": f"{self.method} failed: {self.message}",
            "affected_check_ids": [],
            "affected_addresses": [],
            "retry_attempts": None,
        }


def _params_have_block_tag(params: Any) -> bool:
    def walk(v: Any) -> bool:
        if isinstance(v, str):
            return v in _BLOCK_TAGS
        if isinstance(v, dict):
            return any(walk(x) for x in v.values())
        if isinstance(v, (list, tuple)):
            return any(walk(x) for x in v)
        return False
    return walk(params)


class ResponseCache:
    """Cache keyed by sha256(host, method, canonical params). Responses for floating block
    tags (latest/pending/...) are never cached because they are not reproducible."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._data: dict[str, Any] = {}
        if path and os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    @staticmethod
    def key(host: str, method: str, params: Any) -> str:
        canon = json.dumps([host, method, params], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode()).hexdigest()

    def get(self, host: str, method: str, params: Any) -> tuple[bool, Any]:
        if _params_have_block_tag(params):
            return False, None
        k = self.key(host, method, params)
        if k in self._data:
            return True, self._data[k]["result"]
        return False, None

    def put(self, host: str, method: str, params: Any, result: Any) -> None:
        if _params_have_block_tag(params):
            return
        k = self.key(host, method, params)
        self._data[k] = {"host": host, "method": method, "params": params, "result": result}
        if self.path:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f)
            os.replace(tmp, self.path)

    def __len__(self) -> int:
        return len(self._data)


class RpcClient:
    """Minimal JSON-RPC over HTTP(S). Only READ_ONLY_METHODS are permitted; anything else raises
    RpcPolicyError before any network activity. This client never signs or broadcasts."""

    def __init__(self, url: str, timeout: float = 20.0, cache: Optional[ResponseCache] = None,
                 retries: int = 2, backoff: float = 1.5, user_agent: str = "evm-token-due-diligence/1.0"):
        if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
            raise ValueError("RPC url must be http(s)")
        self.url = url
        self.redacted_url = redact_url(url)
        self.host = urllib.parse.urlsplit(url).hostname or ""
        self.timeout = timeout
        self.cache = cache
        self.retries = retries
        self.backoff = backoff
        self.user_agent = user_agent
        self.calls: list[dict] = []
        self._id = 0

    def call(self, method: str, params: Optional[list] = None) -> Any:
        params = params if params is not None else []
        if method not in READ_ONLY_METHODS:
            raise RpcPolicyError(f"method {method} is not in the read-only allowlist; this client never signs or broadcasts")
        if self.cache is not None:
            hit, val = self.cache.get(self.host, method, params)
            if hit:
                self.calls.append({"method": method, "params": params, "cached": True})
                return val
        last_err: Optional[RpcCoverageError] = None
        for attempt in range(self.retries + 1):
            try:
                result = self._do(method, params)
                self.calls.append({"method": method, "params": params, "cached": False})
                if self.cache is not None:
                    self.cache.put(self.host, method, params, result)
                return result
            except RpcCoverageError as e:
                last_err = e
                if e.kind in ("rpc_error", "auth", "malformed_response") and e.kind != "rpc_rate_limit":
                    break
                if attempt < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
        assert last_err is not None
        raise last_err

    def _do(self, method: str, params: list) -> Any:
        self._id += 1
        body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
        req = urllib.request.Request(self.url, data=body, method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": self.user_agent})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 429:
                raise RpcCoverageError("rpc_rate_limit", f"HTTP 429 from {self.redacted_url}", method, params)
            if e.code in (401, 403):
                raise RpcCoverageError("rpc_error", f"HTTP {e.code} (auth/policy) from {self.redacted_url}", method, params)
            raise RpcCoverageError("rpc_error", f"HTTP {e.code} from {self.redacted_url}", method, params)
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, socket.timeout) or "timed out" in str(reason).lower():
                raise RpcCoverageError("rpc_timeout", f"timeout after {self.timeout}s contacting {self.redacted_url}", method, params)
            if isinstance(reason, socket.gaierror) or "name or service not known" in str(reason).lower() or "nodename" in str(reason).lower():
                raise RpcCoverageError("dns_failure", f"DNS resolution failed for {self.redacted_url}", method, params)
            if isinstance(reason, ssl.SSLError):
                raise RpcCoverageError("rpc_error", f"TLS error contacting {self.redacted_url}: {reason}", method, params)
            raise RpcCoverageError("rpc_error", f"transport error contacting {self.redacted_url}: {reason}", method, params)
        except (socket.timeout, TimeoutError):
            raise RpcCoverageError("rpc_timeout", f"timeout after {self.timeout}s contacting {self.redacted_url}", method, params)
        except OSError as e:
            raise RpcCoverageError("rpc_error", f"OS error contacting {self.redacted_url}: {e}", method, params)
        try:
            obj = json.loads(raw.decode("utf-8"))
        except Exception:
            raise RpcCoverageError("malformed_response", "response was not JSON", method, params)
        if not isinstance(obj, dict):
            raise RpcCoverageError("malformed_response", "response was not a JSON object", method, params)
        if "error" in obj and obj["error"]:
            err = obj["error"]
            msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
            code = err.get("code") if isinstance(err, dict) else None
            low = msg.lower()
            if "rate" in low and "limit" in low or code in (-32005, 429):
                raise RpcCoverageError("rpc_rate_limit", f"rpc error {code}: {msg}", method, params)
            if "missing trie node" in low or "pruned" in low or "state not available" in low or "historical state" in low:
                raise RpcCoverageError("rpc_pruned", f"rpc error {code}: {msg}", method, params)
            raise RpcCoverageError("rpc_error", f"rpc error {code}: {msg}", method, params)
        if "result" not in obj:
            raise RpcCoverageError("malformed_response", "response lacked result", method, params)
        return obj["result"]

    # Convenience wrappers ---------------------------------------------------------------
    def chain_id(self) -> int:
        return hex_to_int(self.call("eth_chainId"))

    def block(self, tag: str | int = "latest", full_tx: bool = False) -> dict:
        t = tag if isinstance(tag, str) else int_to_hex(tag)
        res = self.call("eth_getBlockByNumber", [t, full_tx])
        if not isinstance(res, dict):
            raise RpcCoverageError("rpc_error", f"block {t} not returned", "eth_getBlockByNumber", [t, full_tx])
        return res

    def code(self, address: str, block: str) -> str:
        return self.call("eth_getCode", [normalize_address(address), block])

    def storage(self, address: str, slot: str, block: str) -> str:
        return self.call("eth_getStorageAt", [normalize_address(address), slot, block])

    def eth_call(self, to: str, data: str, block: str) -> str:
        return self.call("eth_call", [{"to": normalize_address(to), "data": data}, block])


if __name__ == "__main__":  # tiny self-check
    assert keccak256_hex(b"") == EMPTY_CODE_HASH
    assert selector("transfer(address,uint256)") == "0xa9059cbb"
    print("ddcore ok")
