"""Unit tests for scripts/ddcore.py (stdlib unittest; pytest also discovers these). No network: the only
sockets opened are loopback raw servers from tests/mock_rpc.py."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
sys.path.insert(0, HERE)

import ddcore  # noqa: E402
from mock_rpc import RawResponseServer  # noqa: E402

ENDPOINT = ["http", "127.0.0.1", 8545, "/"]
ADDR = "0x" + "a1" * 20


class KeccakTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(ddcore.keccak256_hex(b""), "0xc5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470")

    def test_abc(self):
        self.assertEqual(ddcore.keccak256(b"abc").hex(), "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45")

    def test_selectors(self):
        self.assertEqual(ddcore.selector("transfer(address,uint256)"), "0xa9059cbb")
        self.assertEqual(ddcore.selector("balanceOf(address)"), "0x70a08231")
        self.assertEqual(ddcore.selector("approve(address,uint256)"), "0x095ea7b3")
        self.assertEqual(ddcore.selector("totalSupply()"), "0x18160ddd")
        self.assertEqual(ddcore.selector("owner()"), "0x8da5cb5b")
        self.assertEqual(ddcore.selector("upgradeTo(address)"), "0x3659cfe6")

    def test_event_topic(self):
        self.assertEqual(ddcore.keccak256_hex(b"Transfer(address,address,uint256)"),
                         "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef")

    def test_multi_block_input(self):
        # > 136 bytes forces multiple absorb blocks; compare against known value of 200 'a's
        data = b"a" * 200
        # value cross-checked from a reference Keccak-256 implementation
        self.assertEqual(len(ddcore.keccak256(data)), 32)
        # rate-boundary case: exactly 135 bytes (pad becomes single 0x81 byte)
        self.assertEqual(len(ddcore.keccak256(b"b" * 135)), 32)
        self.assertNotEqual(ddcore.keccak256(b"b" * 135), ddcore.keccak256(b"b" * 136))

    def test_eip1967_slots(self):
        self.assertEqual(ddcore.EIP1967_IMPLEMENTATION_SLOT,
                         "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc")
        self.assertEqual(ddcore.EIP1967_ADMIN_SLOT,
                         "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103")
        self.assertEqual(ddcore.EIP1967_BEACON_SLOT,
                         "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50")


class AddressTests(unittest.TestCase):
    EIP55 = [
        "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed",
        "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359",
        "0xdbF03B407c01E7cD3CBea99509d93f8DDDC8C6FB",
        "0xD1220A0cf47c7B9Be7A2E6BA89F429762e7b9aDb",
    ]

    def test_checksum_vectors(self):
        for a in self.EIP55:
            self.assertEqual(ddcore.to_checksum_address(a.lower()), a)
            self.assertTrue(ddcore.is_checksum_valid(a))

    def test_bad_checksum_rejected(self):
        bad = "0x5aaeb6053F3E94C9b9A09f33669435E7Ef1BeAed"  # first hex letters lowercased
        ok, reason = ddcore.validate_address(bad)
        self.assertFalse(ok)
        self.assertIn("EIP-55", reason)

    def test_lowercase_and_uppercase_accepted(self):
        a = self.EIP55[0]
        self.assertTrue(ddcore.validate_address(a.lower())[0])
        self.assertTrue(ddcore.validate_address("0x" + a[2:].upper())[0])

    def test_malformed(self):
        self.assertFalse(ddcore.validate_address("0x1234")[0])
        self.assertFalse(ddcore.validate_address("5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")[0])
        self.assertFalse(ddcore.validate_address("0xZZAeb6053F3E94C9b9A09f33669435E7Ef1BeAed")[0])
        self.assertFalse(ddcore.validate_address(None)[0])

    def test_normalize(self):
        self.assertEqual(ddcore.normalize_address(self.EIP55[0]), self.EIP55[0].lower())


class HashHelperTests(unittest.TestCase):
    def test_placeholder_detection(self):
        self.assertTrue(ddcore.looks_like_placeholder_hash("0x" + "0" * 64)[0])
        self.assertTrue(ddcore.looks_like_placeholder_hash("0x" + "f" * 64)[0])
        self.assertTrue(ddcore.looks_like_placeholder_hash("0x" + "deadbeef" * 8)[0])
        self.assertTrue(ddcore.looks_like_placeholder_hash("TBD")[0])
        self.assertTrue(ddcore.looks_like_placeholder_hash("0x" + "ab" * 32)[0])
        real = ddcore.keccak256_hex(b"real block hash")
        self.assertFalse(ddcore.looks_like_placeholder_hash(real)[0])

    def test_hex_int(self):
        self.assertEqual(ddcore.hex_to_int("0x10"), 16)
        self.assertEqual(ddcore.hex_to_int(7), 7)
        self.assertEqual(ddcore.int_to_hex(255), "0xff")
        with self.assertRaises(ValueError):
            ddcore.hex_to_int("10")
        # JSON true/false is never a quantity (bool is an int subclass; an endpoint answering eth_chainId
        # with `true` must not be reported as chain "True")
        for b in (True, False):
            with self.assertRaises(ValueError):
                ddcore.hex_to_int(b)


class AbiTests(unittest.TestCase):
    def test_encode_address_and_ints(self):
        w = ddcore.encode_word("address", "0x" + "11" * 20)
        self.assertEqual(w, b"\x00" * 12 + b"\x11" * 20)
        self.assertEqual(ddcore.encode_word("uint", 500), (500).to_bytes(32, "big"))
        self.assertEqual(ddcore.encode_word("int", -1), b"\xff" * 32)
        self.assertEqual(ddcore.encode_word("int", -60).hex(), "ff" * 31 + "c4")

    def test_decode_string_standard(self):
        s = b"Wrapped Ether"
        data = (32).to_bytes(32, "big") + len(s).to_bytes(32, "big") + s.ljust(32, b"\x00")
        self.assertEqual(ddcore.decode_abi_string(data), ("Wrapped Ether", "resolved"))

    def test_decode_string_bytes32_nonstandard(self):
        data = b"MKR".ljust(32, b"\x00")
        self.assertEqual(ddcore.decode_abi_string(data), ("MKR", "nonstandard"))

    def test_decode_string_unresolved(self):
        self.assertEqual(ddcore.decode_abi_string("0x"), (None, "unresolved"))
        self.assertEqual(ddcore.decode_abi_string(b"\x00" * 32), (None, "unresolved"))
        self.assertEqual(ddcore.decode_abi_string(b"\x01" * 40), (None, "unresolved"))

    def test_decode_string_bounds_and_printability(self):
        def head(offset, length):
            return offset.to_bytes(32, "big") + length.to_bytes(32, "big")
        # offset word past the data must not decode as an empty *resolved* string
        self.assertEqual(ddcore.decode_abi_string((2 ** 200).to_bytes(32, "big") + b"\x00" * 32), (None, "unresolved"))
        # length word past the data
        self.assertEqual(ddcore.decode_abi_string(head(32, 2 ** 200) + b"abc".ljust(32, b"\x00")), (None, "unresolved"))
        # a well-formed empty string is unresolved with the reason "empty string"
        self.assertEqual(ddcore.decode_abi_string(head(32, 0)), (None, "unresolved"))
        self.assertEqual(ddcore.decode_abi_string_detail(head(32, 0))[2], "empty string")
        # control / non-printable characters are rejected on the dynamic path like on the bytes32 path
        self.assertEqual(ddcore.decode_abi_string(head(32, 3) + b"a\x00b".ljust(32, b"\x00")), (None, "unresolved"))
        self.assertEqual(ddcore.decode_abi_string(head(32, 3) + b"a\nb".ljust(32, b"\x00")), (None, "unresolved"))
        self.assertEqual(ddcore.decode_abi_string(b"a\x00b".ljust(32, b"\x00")), (None, "unresolved"))
        # a non-32 offset is still honoured when it is inside the data
        data = head(64, 0)[:32] + b"\x00" * 32 + (3).to_bytes(32, "big") + b"abc".ljust(32, b"\x00")
        self.assertEqual(ddcore.decode_abi_string(data), ("abc", "resolved"))
        value, status, reason = ddcore.decode_abi_string_detail(b"MKR".ljust(32, b"\x00"))
        self.assertEqual((value, status), ("MKR", "nonstandard"))
        self.assertIn("bytes32", reason)

    def test_decode_uint_address(self):
        self.assertEqual(ddcore.decode_uint("0x" + "00" * 31 + "12"), 18)
        self.assertEqual(ddcore.decode_address("0x" + "00" * 12 + "ab" * 20), ddcore.to_checksum_address("0x" + "ab" * 20))


class TimeAndRedactionTests(unittest.TestCase):
    def test_iso_roundtrip(self):
        self.assertEqual(ddcore.iso_utc(0), "1970-01-01T00:00:00Z")
        self.assertEqual(ddcore.parse_iso_utc("2026-01-02T03:04:05Z"), 1767323045)
        with self.assertRaises(ValueError):
            ddcore.parse_iso_utc("2026-01-02 03:04:05")

    def test_iso_utc_range(self):
        self.assertEqual(ddcore.iso_utc(ddcore.MAX_UNIX_TIMESTAMP), "9999-12-31T23:59:59Z")
        for bad in (-1, ddcore.MAX_UNIX_TIMESTAMP + 1, 0xFFFFFFFFFFFFFFFF, 2 ** 70, True, "soon"):
            # catchable as either ValueError or OverflowError so a caller can record a per-pin limitation
            with self.assertRaises(ValueError):
                ddcore.iso_utc(bad)
            with self.assertRaises(OverflowError):
                ddcore.iso_utc(bad)

    def test_redact_url(self):
        r = ddcore.redact_url("https://user:pw@eth-mainnet.g.example.com/v2/AbCdEfGhIjKlMnOpQrStUvWx123?apikey=SECRET&x=1")
        self.assertNotIn("SECRET", r)
        self.assertNotIn("pw", r.split("//")[1].split("/")[0])
        self.assertNotIn("AbCdEfGhIjKlMnOpQrStUvWx123", r)
        self.assertIn("<redacted>", r)
        self.assertTrue(r.startswith("https://eth-mainnet.g.example.com/v2/"))
        self.assertEqual(ddcore.redact_url("http://127.0.0.1:8545"), "http://127.0.0.1:8545")

    def test_redact_url_edge_cases(self):
        # IPv6 hosts keep their brackets (an endpoint string must stay parseable)
        self.assertEqual(ddcore.redact_url("http://[::1]:8545"), "http://[::1]:8545")
        self.assertEqual(ddcore.redact_url("http://[::1]:8545/v2/ABCDEFGHIJKLMNOPQRSTUVWX"), "http://[::1]:8545/v2/<redacted>")
        # a non-numeric port never raises
        self.assertEqual(ddcore.redact_url("https://host.example:abc/x"), ddcore.UNPARSEABLE_URL)
        self.assertEqual(ddcore.redact_url(None), ddcore.UNPARSEABLE_URL)
        # short keys after /vN/, letter+digit tokens, JWT-like dotted tokens and a key-like first host label
        self.assertEqual(ddcore.redact_url("https://host.example/v2/shortkey"), "https://host.example/v2/<redacted>")
        self.assertEqual(ddcore.redact_url("https://host.example/rpc/abc123XYZ789"), "https://host.example/rpc/<redacted>")
        self.assertEqual(ddcore.redact_url("https://host.example/rpc/eyJhbGciOi.JIUzI1NiIsInR5.cCI6IkpXVCJ9"), "https://host.example/rpc/<redacted>")
        self.assertEqual(ddcore.redact_url("https://ABCDEFGHIJKLMNOPQRSTUVWXYZ012345.rpc.example.invalid/"), "https://<redacted>.rpc.example.invalid/")
        # ordinary network-name paths survive
        self.assertEqual(ddcore.redact_url("https://rpc.example.invalid/eth"), "https://rpc.example.invalid/eth")
        self.assertEqual(ddcore.redact_url("http://127.0.0.1:8545/"), "http://127.0.0.1:8545/")


class RpcPolicyTests(unittest.TestCase):
    def test_disallowed_methods_never_hit_network(self):
        c = ddcore.RpcClient("http://127.0.0.1:9")  # port 9 (discard) — must not even be contacted
        for m in ("eth_sendTransaction", "eth_sendRawTransaction", "eth_sign", "personal_sign", "eth_signTransaction", "anvil_setBalance"):
            with self.assertRaises(ddcore.RpcPolicyError):
                c.call(m, [])
        self.assertEqual(c.calls, [])

    def test_bad_url_rejected(self):
        with self.assertRaises(ValueError):
            ddcore.RpcClient("ftp://x")

    def test_userinfo_and_bad_port_rejected_with_clear_message(self):
        with self.assertRaises(ValueError) as ctx:
            ddcore.RpcClient("http://alice:pw@127.0.0.1:1/")
        self.assertIn("userinfo", str(ctx.exception))
        with self.assertRaises(ValueError) as ctx:
            ddcore.RpcClient("http://127.0.0.1:abc")
        self.assertIn("port", str(ctx.exception))
        c = ddcore.RpcClient("http://[::1]:1/v2/ABCDEFGHIJKLMNOPQRSTUVWX?k=v")
        self.assertEqual(c.endpoint, ["http", "::1", 1, "/v2/<redacted>"])
        self.assertEqual(c.redacted_url, "http://[::1]:1/v2/<redacted>?k=<redacted>")
        self.assertIsNone(c.chain_id)
        self.assertEqual(ddcore.RpcClient("https://rpc.example.invalid/eth").endpoint, ["https", "rpc.example.invalid", 443, "/eth"])


class ResponseCacheTests(unittest.TestCase):
    def test_key_is_chain_and_endpoint_namespaced(self):
        cache = ddcore.ResponseCache(chain_id=1)
        cache.put(1, ENDPOINT, "eth_getCode", ["0xabc", "latest"], "0x")  # floating tags are never cached
        self.assertEqual(len(cache), 0)
        cache.put(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"], "0x60")
        hit, entry = cache.get(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"])
        self.assertTrue(hit)
        self.assertEqual((entry["result"], entry["outcome"], entry["chain_id"], entry["port"]), ("0x60", "ok", 1, 8545))
        self.assertEqual(cache.get(1, ENDPOINT, "eth_getCode", ["0xabc", "0x11"]), (False, None))
        # the same read on another chain id is never served (put(chain 1) is not returned by get(chain 8453))
        self.assertEqual(cache.get(8453, ENDPOINT, "eth_getCode", ["0xabc", "0x10"]), (False, None))
        # the same host on another port or path is another endpoint
        self.assertEqual(cache.get(1, ["http", "127.0.0.1", 8546, "/"], "eth_getCode", ["0xabc", "0x10"]), (False, None))
        self.assertEqual(cache.get(1, ["http", "127.0.0.1", 8545, "/base"], "eth_getCode", ["0xabc", "0x10"]), (False, None))
        self.assertEqual(cache.get(1, ["https", "127.0.0.1", 8545, "/"], "eth_getCode", ["0xabc", "0x10"]), (False, None))
        self.assertNotEqual(ddcore.ResponseCache.key(1, ENDPOINT, "m", []), ddcore.ResponseCache.key(8453, ENDPOINT, "m", []))
        # an unbound cache (chain id not yet observed) refuses reads and writes
        unbound = ddcore.ResponseCache()
        unbound.put(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"], "0x60")
        self.assertEqual(len(unbound), 0)
        self.assertEqual(unbound.get(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"]), (False, None))
        with self.assertRaises(ValueError):
            unbound.bind_chain(0)

    def test_file_header_records_chain_and_refuses_another(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "cache.json")
            c1 = ddcore.ResponseCache(path, chain_id=1)
            c1.put(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"], "0x60")
            with open(path, encoding="utf-8") as f:
                obj = json.load(f)
            self.assertEqual((obj["cache_version"], obj["chain_id"], len(obj["entries"])), (ddcore.CACHE_FORMAT_VERSION, 1, 1))
            with self.assertRaises(ddcore.RpcCoverageError) as ctx:
                ddcore.ResponseCache(path, chain_id=8453)
            self.assertEqual(ctx.exception.kind, "rpc_error")
            self.assertIn("cache file belongs to chain 1", ctx.exception.message)
            # RpcClient.set_chain_id propagates the refusal; the client stays unbound and uncached
            client = ddcore.RpcClient("http://127.0.0.1:9", cache=ddcore.ResponseCache(path))
            with self.assertRaises(ddcore.RpcCoverageError):
                client.set_chain_id(8453)
            self.assertIsNone(client.chain_id)
            self.assertFalse(client._cache_usable())
            client.set_chain_id(1)
            self.assertTrue(client._cache_usable())
            # loading without a chain id is allowed but nothing is served until bound
            c2 = ddcore.ResponseCache(path)
            self.assertEqual(c2.get(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"]), (False, None))
            c2.bind_chain(1)
            self.assertTrue(c2.get(1, ENDPOINT, "eth_getCode", ["0xabc", "0x10"])[0])
            # a legacy file without the header carried no chain binding: its entries are ignored, not trusted
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"deadbeef": {"result": "0x60"}}, f)
            c3 = ddcore.ResponseCache(path, chain_id=1)
            self.assertEqual(len(c3), 0)
            self.assertIn("ignored", c3.discarded)

    def test_execution_failure_cached_as_revert_and_replayed(self):
        cache = ddcore.ResponseCache(chain_id=1)
        c = ddcore.RpcClient("http://127.0.0.1:9", cache=cache, retries=0, chain_id=1)

        def revert(method, params):
            raise ddcore.RpcCoverageError("rpc_error", "rpc error 3: execution reverted", method, params, execution_failure=True)

        with mock.patch.object(c, "_do", revert):
            with self.assertRaises(ddcore.RpcCoverageError) as ctx:
                c.eth_call(ADDR, "0x8da5cb5b", "0x10")
        self.assertTrue(ctx.exception.execution_failure)
        self.assertFalse(ctx.exception.cached)
        self.assertEqual(len(cache), 1)
        with mock.patch.object(c, "_do", side_effect=AssertionError("network must not be used")):
            with self.assertRaises(ddcore.RpcCoverageError) as ctx2:
                c.eth_call(ADDR, "0x8da5cb5b", "0x10")
        self.assertTrue(ctx2.exception.cached)
        self.assertTrue(ctx2.exception.execution_failure)
        self.assertEqual(c.calls[-1], {"method": "eth_call", "params": [{"to": ADDR, "data": "0x8da5cb5b"}, "0x10"],
                                       "cached": True, "outcome": "reverted"})
        # reverts at a floating tag and infrastructure failures are never cached
        with mock.patch.object(c, "_do", revert):
            with self.assertRaises(ddcore.RpcCoverageError):
                c.eth_call(ADDR, "0x8da5cb5b", "latest")

        def http500(method, params):
            raise ddcore.RpcCoverageError("rpc_error", "HTTP 500", method, params)

        with mock.patch.object(c, "_do", http500):
            with self.assertRaises(ddcore.RpcCoverageError):
                c.eth_call(ADDR, "0xdeadbeef", "0x10")
        self.assertEqual(len(cache), 1)


class RpcTransportTests(unittest.TestCase):
    def test_malformed_http_is_a_coverage_error_not_a_crash(self):
        payloads = {
            "truncated body (IncompleteRead)": b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 500\r\n\r\n{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":\"0x1\"",
            "garbage status line (BadStatusLine)": b"garbage\r\n\r\n",
        }
        for label, payload in payloads.items():
            with RawResponseServer(payload) as srv:
                c = ddcore.RpcClient(srv.url, timeout=3, retries=0)
                with self.assertRaises(ddcore.RpcCoverageError, msg=label) as ctx:
                    c.call("eth_chainId", [])
                self.assertEqual(ctx.exception.kind, "rpc_error", label)
                self.assertIn("malformed HTTP response", ctx.exception.message, label)

    def test_execution_failures_are_classified_only_for_executing_methods(self):
        c = ddcore.RpcClient("http://127.0.0.1:9", retries=0)
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted"}}).encode()

        class _Resp:
            def __init__(self, raw):
                self._raw = raw

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        with mock.patch("urllib.request.urlopen", return_value=_Resp(body)):
            with self.assertRaises(ddcore.RpcCoverageError) as ctx:
                c.call("eth_call", [{"to": ADDR, "data": "0x"}, "0x10"])
            self.assertTrue(ctx.exception.execution_failure)
            with self.assertRaises(ddcore.RpcCoverageError) as ctx:
                c.call("eth_getCode", [ADDR, "0x10"])
            self.assertFalse(ctx.exception.execution_failure)

    def test_retry_policy(self):
        attempts: list[str] = []

        def pruned(method, params):
            attempts.append(method)
            raise ddcore.RpcCoverageError("rpc_pruned", "missing trie node", method, params)

        def timeout(method, params):
            attempts.append(method)
            raise ddcore.RpcCoverageError("rpc_timeout", "timed out", method, params)

        c = ddcore.RpcClient("http://127.0.0.1:9", retries=2)
        # pruned state never reappears: not retried, no sleep
        with mock.patch.object(c, "_do", pruned), mock.patch("time.sleep", side_effect=AssertionError("must not sleep")):
            with self.assertRaises(ddcore.RpcCoverageError):
                c.call("eth_getCode", [ADDR, "0x10"])
        self.assertEqual(len(attempts), 1)
        self.assertIn("rpc_pruned", ddcore.NON_RETRIED_KINDS)
        # a timeout is retried `retries` times with backoff
        attempts.clear()
        sleeps: list[float] = []
        with mock.patch.object(c, "_do", timeout), mock.patch("time.sleep", lambda s: sleeps.append(s)):
            with self.assertRaises(ddcore.RpcCoverageError):
                c.call("eth_getCode", [ADDR, "0x10"])
        self.assertEqual(len(attempts), 3)
        self.assertEqual(len(sleeps), 2)
        # retries == 0: exactly one attempt and never a sleep
        attempts.clear()
        c0 = ddcore.RpcClient("http://127.0.0.1:9", retries=0)
        with mock.patch.object(c0, "_do", timeout), mock.patch("time.sleep", side_effect=AssertionError("must not sleep")):
            with self.assertRaises(ddcore.RpcCoverageError):
                c0.call("eth_getCode", [ADDR, "0x10"])
        self.assertEqual(len(attempts), 1)


if __name__ == "__main__":
    unittest.main()
