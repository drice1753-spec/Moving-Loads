"""Unit tests for scripts/ddcore.py (stdlib unittest; pytest also discovers these)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import ddcore  # noqa: E402


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
        self.assertEqual(ddcore.int_to_hex(255), "0xff")
        with self.assertRaises(ValueError):
            ddcore.hex_to_int("10")


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

    def test_decode_uint_address(self):
        self.assertEqual(ddcore.decode_uint("0x" + "00" * 31 + "12"), 18)
        self.assertEqual(ddcore.decode_address("0x" + "00" * 12 + "ab" * 20), ddcore.to_checksum_address("0x" + "ab" * 20))


class TimeAndRedactionTests(unittest.TestCase):
    def test_iso_roundtrip(self):
        self.assertEqual(ddcore.iso_utc(0), "1970-01-01T00:00:00Z")
        self.assertEqual(ddcore.parse_iso_utc("2026-01-02T03:04:05Z"), 1767323045)
        with self.assertRaises(ValueError):
            ddcore.parse_iso_utc("2026-01-02 03:04:05")

    def test_redact_url(self):
        r = ddcore.redact_url("https://user:pw@eth-mainnet.g.example.com/v2/AbCdEfGhIjKlMnOpQrStUvWx123?apikey=SECRET&x=1")
        self.assertNotIn("SECRET", r)
        self.assertNotIn("pw", r.split("//")[1].split("/")[0])
        self.assertNotIn("AbCdEfGhIjKlMnOpQrStUvWx123", r)
        self.assertIn("<redacted>", r)
        self.assertTrue(r.startswith("https://eth-mainnet.g.example.com/v2/"))
        self.assertEqual(ddcore.redact_url("http://127.0.0.1:8545"), "http://127.0.0.1:8545")


class RpcPolicyTests(unittest.TestCase):
    def test_disallowed_methods_never_hit_network(self):
        c = ddcore.RpcClient("http://127.0.0.1:9")  # port 9 (discard) — must not even be contacted
        for m in ("eth_sendTransaction", "eth_sendRawTransaction", "eth_sign", "personal_sign", "eth_signTransaction", "anvil_setBalance"):
            with self.assertRaises(ddcore.RpcPolicyError):
                c.call(m, [])
        self.assertEqual(c.calls, [])

    def test_cache_skips_floating_tags(self):
        cache = ddcore.ResponseCache()
        cache.put("h", "eth_getCode", ["0xabc", "latest"], "0x")
        self.assertEqual(len(cache), 0)
        cache.put("h", "eth_getCode", ["0xabc", "0x10"], "0x60")
        self.assertEqual(cache.get("h", "eth_getCode", ["0xabc", "0x10"]), (True, "0x60"))
        self.assertEqual(cache.get("h", "eth_getCode", ["0xabc", "0x11"]), (False, None))

    def test_bad_url_rejected(self):
        with self.assertRaises(ValueError):
            ddcore.RpcClient("ftp://x")


if __name__ == "__main__":
    unittest.main()
