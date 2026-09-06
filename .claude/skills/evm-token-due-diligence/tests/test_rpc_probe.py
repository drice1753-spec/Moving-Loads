"""Tests for scripts/rpc_probe.py against an in-process JSON-RPC mock (tests/mock_rpc.py).

No network: the mock binds 127.0.0.1 only. All served values are synthetic.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
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
import rpc_probe  # noqa: E402
from mock_rpc import (  # noqa: E402
    HttpStatus,
    JsonRpcError,
    RawResponseServer,
    abi_address,
    abi_bool,
    abi_bytes32_text,
    abi_string,
    abi_word,
    non_allowlisted_methods,
    start_mock,
)

FAKE_KEY = "FAKEKEYabcdefghij0123456789ZZ"  # >= 20 chars so ddcore.redact_url treats it as key-like
MOCK_PATH = f"/v2/{FAKE_KEY}"

TOKEN = ddcore.to_checksum_address("0x" + "a1" * 20)
IMPL = ddcore.to_checksum_address("0x" + "b2" * 20)
OWNER = ddcore.to_checksum_address("0x" + "c3" * 20)
ADMIN = ddcore.to_checksum_address("0x" + "d4" * 20)


def _h(label: str) -> str:
    return "0x" + hashlib.sha256(label.encode()).hexdigest()


BLOCK_NUMBER = 20000000
HEADER = {
    "number": hex(BLOCK_NUMBER),
    "hash": _h("synthetic block hash"),
    "parentHash": _h("synthetic parent hash"),
    "timestamp": "0x66a0b1c2",
    "miner": "0x" + "00" * 19 + "01",
    "gasUsed": "0x1234",
    "gasLimit": "0x1c9c380",
    "extraData": "0x",
    "transactions": [],
}
PINNED_HEX = hex(BLOCK_NUMBER)


def _synthetic_code(seed: str, size: int = 300) -> bytes:
    out = b""
    counter = 0
    while len(out) < size:
        out += hashlib.sha256(f"{seed}:{counter}".encode()).digest()
        counter += 1
    return b"\x60\x80\x60\x40\x52" + out[: size - 5]


TOKEN_CODE = _synthetic_code("token runtime")
IMPL_CODE = _synthetic_code("implementation runtime", 400)

SEL = {
    "name": ddcore.selector("name()"),
    "symbol": ddcore.selector("symbol()"),
    "decimals": ddcore.selector("decimals()"),
    "totalSupply": ddcore.selector("totalSupply()"),
    "owner": ddcore.selector("owner()"),
    "getOwner": ddcore.selector("getOwner()"),
    "paused": ddcore.selector("paused()"),
}


def standard_calls() -> dict:
    return {
        SEL["name"]: abi_string("Mock Token"),
        SEL["symbol"]: abi_string("MOCK"),
        SEL["decimals"]: abi_word(18),
        SEL["totalSupply"]: abi_word(10 ** 24),
        SEL["owner"]: abi_address(OWNER),
        SEL["getOwner"]: JsonRpcError(3, "execution reverted"),
        SEL["paused"]: abi_bool(False),
    }


def make_handlers(chain_hex: str = "0x1", code: dict | None = None, storage: dict | None = None,
                  calls: dict | None = None, overrides: dict | None = None) -> dict:
    """Build a token-like node. eth_call dispatches on the 4-byte selector; a value that is an
    Exception instance is raised (JsonRpcError -> revert / HttpStatus -> HTTP error)."""
    code_map = {TOKEN.lower(): "0x" + TOKEN_CODE.hex()}
    code_map.update({k.lower(): v for k, v in (code or {}).items()})
    storage_map = {k.lower(): v for k, v in (storage or {}).items()}
    call_map = standard_calls()
    call_map.update(calls or {})

    def get_block(params):
        tag = params[0]
        if tag in ("latest", "safe", "finalized", PINNED_HEX):
            return dict(HEADER)
        return None

    def get_code(params):
        return code_map.get(params[0].lower(), "0x")

    def get_storage(params):
        return storage_map.get(params[1].lower(), "0x" + "00" * 32)

    def eth_call(params):
        to = params[0]["to"].lower()
        data = params[0]["data"]
        if to != TOKEN.lower():
            return "0x"
        v = call_map.get(data[:10].lower(), "0x")
        if isinstance(v, Exception):
            raise v
        return v(params) if callable(v) else v

    handlers = {
        "eth_chainId": chain_hex,
        "web3_clientVersion": "mock-node/0.0.0",
        "eth_getBlockByNumber": get_block,
        "eth_getCode": get_code,
        "eth_getStorageAt": get_storage,
        "eth_call": eth_call,
    }
    handlers.update(overrides or {})
    return handlers


class ProbeTestBase(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory(prefix="rpc_probe_test_")
        self.addCleanup(tmpdir.cleanup)  # nothing is left behind in the temp directory
        self.tmp = tmpdir.name
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            # (f) every server used in every test: only allowlisted read-only methods were ever sent
            self.assertEqual(non_allowlisted_methods(s), [], "non-allowlisted method sent to the mock")
            s.stop()

    def serve(self, handlers, path=MOCK_PATH):
        url, server = start_mock(handlers, path=path)
        self.servers.append(server)
        return url, server

    def run_probe(self, url, *extra, out_name="packet.json"):
        out = os.path.join(self.tmp, out_name)
        # --retries 0 keeps the tests fast; a test that exercises retries passes its own --retries after it
        argv = ["--rpc", url, "--address", TOKEN, "--out", out, "--retries", "0", *extra]
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = rpc_probe.main(argv)
        packet = None
        if os.path.exists(out):
            with open(out, "r", encoding="utf-8") as f:
                packet = json.load(f)
        return code, packet, so.getvalue(), se.getvalue()


class HappyPathTests(ProbeTestBase):
    def test_a_non_proxy_erc20(self):
        url, server = self.serve(make_handlers())
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 0, err)
        self.assertEqual(packet["identity"]["status"], "MATCH")
        self.assertEqual((packet["identity"]["requested"], packet["identity"]["observed"]), (1, 1))
        self.assertEqual(packet["observed"]["chain_id"], 1)
        self.assertEqual(packet["observed"]["rpc_chain_id_hex"], "0x1")
        self.assertEqual(packet["observed"]["client_version"], "mock-node/0.0.0")
        self.assertEqual(packet["requested"], {"chain_id": 1, "address": TOKEN, "address_checksum": TOKEN})
        self.assertEqual(packet["cache"], {"status": "none", "chain_id": 1, "note": None})

        pin = packet["pin"]
        self.assertEqual(pin["pin_id"], "P1")
        self.assertEqual(pin["chain_id"], 1)
        self.assertEqual(pin["block_number"], BLOCK_NUMBER)
        self.assertEqual(pin["block_hash"], HEADER["hash"])
        self.assertEqual(pin["timestamp_unix"], int(HEADER["timestamp"], 16))
        self.assertEqual(pin["timestamp_utc"], ddcore.iso_utc(int(HEADER["timestamp"], 16)))
        self.assertEqual(pin["rpc_method"], "eth_getBlockByNumber")
        self.assertEqual(pin["captured_header"], HEADER)
        self.assertGreaterEqual(ddcore.parse_iso_utc(pin["captured_at_utc"]), pin["timestamp_unix"])
        self.assertEqual(pin["rpc_endpoint_redacted"], packet["observed"]["rpc_endpoint_redacted"])

        rt = packet["runtime"]
        self.assertEqual(rt["code_hash"], ddcore.keccak256_hex(TOKEN_CODE))
        self.assertEqual(rt["code_size"], len(TOKEN_CODE))
        self.assertTrue(rt["is_contract"])
        self.assertEqual(rt["runtime_status"], "contract")  # manifest vocabulary, copied verbatim into scope_addresses
        self.assertEqual(rt["proxy"]["status"], "not_proxy")
        self.assertIn("custom proxies possible", rt["proxy"]["basis"])
        self.assertEqual(len(rt["proxy"]["slots_read"]), 4)
        self.assertIn(ddcore.EIP1967_IMPLEMENTATION_SLOT, rt["proxy"]["slots_read"])

        meta = packet["metadata"]
        self.assertEqual(meta["name"], {**meta["name"], "value": "Mock Token", "status": "resolved"})
        self.assertEqual(meta["symbol"]["value"], "MOCK")
        self.assertEqual(meta["symbol"]["status"], "resolved")
        self.assertEqual(meta["decimals"]["value"], 18)
        self.assertEqual(meta["decimals"]["status"], "resolved")
        self.assertEqual(meta["total_supply"]["value"], str(10 ** 24))
        self.assertEqual(meta["total_supply"]["status"], "resolved")
        for k in ("name", "symbol", "decimals", "total_supply"):
            self.assertTrue(meta[k]["raw"].startswith("0x"), k)
            self.assertIn("P1", meta[k]["source"])
            self.assertIsNone(meta[k]["qualifier"])

        probes = {p["name"]: p for p in packet["probes"]}
        self.assertEqual(probes["owner()"]["selector"], "0x8da5cb5b")
        self.assertEqual(probes["owner()"]["status"], "ok")
        self.assertEqual(probes["owner()"]["decoded_candidates"]["address"], OWNER)
        self.assertEqual(probes["getOwner()"]["status"], "reverted")
        self.assertEqual(probes["paused()"]["status"], "ok")
        self.assertIs(probes["paused()"]["decoded_candidates"]["bool"], False)
        self.assertTrue(all(p["qualifier"] is None for p in packet["probes"]))

        self.assertEqual(packet["coverage_status"], "complete")
        self.assertEqual(packet["limitations"], [])
        self.assertTrue(packet["calls"])
        self.assertTrue(all(c["cached"] is False and c["outcome"] == "ok" for c in packet["calls"]))
        self.assertEqual(packet["declarations"], {
            "no_real_signing": True, "no_broadcast": True,
            "no_private_keys_requested": True, "read_only_methods_only": True,
        })

        # endpoint redaction: the fake key segment must not appear anywhere in the packet or output
        dumped = json.dumps(packet)
        self.assertNotIn(FAKE_KEY, dumped)
        self.assertNotIn(FAKE_KEY, out)
        self.assertIn("<redacted>", packet["observed"]["rpc_endpoint_redacted"])

        # every read after the pin used the pinned block hex, never "latest"
        for method in ("eth_getCode", "eth_getStorageAt", "eth_call"):
            for params in server.params_for(method):
                self.assertEqual(params[-1], PINNED_HEX, f"{method} did not use the pinned block")
        self.assertEqual(server.count("eth_getBlockByNumber"), 1)
        self.assertIn("rpc_probe: target packet", out)

    def test_b_eip1967_proxy(self):
        storage = {
            ddcore.EIP1967_IMPLEMENTATION_SLOT: abi_address(IMPL),
            ddcore.EIP1967_ADMIN_SLOT: abi_address(ADMIN),
        }
        url, _ = self.serve(make_handlers(storage=storage, code={IMPL: "0x" + IMPL_CODE.hex()}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 0, err)
        proxy = packet["runtime"]["proxy"]
        self.assertEqual(proxy["status"], "eip1967")
        self.assertEqual(proxy["implementation"], IMPL)
        self.assertEqual(proxy["implementation_code_hash"], ddcore.keccak256_hex(IMPL_CODE))
        self.assertEqual(proxy["admin"], ADMIN)
        self.assertIsNone(proxy["beacon"])
        self.assertEqual(proxy["slots_read"][ddcore.EIP1967_IMPLEMENTATION_SLOT], abi_address(IMPL))
        self.assertIn("upgrade authority is NOT resolved", proxy["basis"])
        self.assertEqual(packet["coverage_status"], "complete")

    def test_b2_minimal_proxy_1167(self):
        code = bytes.fromhex("363d3d373d3d3d363d73") + bytes.fromhex(IMPL[2:]) + bytes.fromhex("5af43d82803e903d91602b57fd5bf3")
        url, _ = self.serve(make_handlers(code={TOKEN: "0x" + code.hex(), IMPL: "0x" + IMPL_CODE.hex()}))
        rc, packet, out, err = self.run_probe(url)
        self.assertEqual(rc, 0, err)
        proxy = packet["runtime"]["proxy"]
        self.assertEqual(proxy["status"], "minimal_1167")
        self.assertEqual(proxy["implementation"], IMPL)
        self.assertEqual(proxy["implementation_code_hash"], ddcore.keccak256_hex(IMPL_CODE))
        self.assertEqual(packet["identity"]["status"], "OBSERVED_ONLY")

    def test_c_nonstandard_and_unresolved_metadata_and_revert(self):
        calls = {
            SEL["symbol"]: abi_bytes32_text("MOCK"),
            SEL["name"]: "0x",
            SEL["owner"]: JsonRpcError(-32000, "execution reverted: not an owner"),
        }
        url, _ = self.serve(make_handlers(calls=calls))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 0, err)
        meta = packet["metadata"]
        self.assertEqual(meta["symbol"]["status"], "nonstandard")
        self.assertEqual(meta["symbol"]["value"], "MOCK")
        self.assertEqual(meta["symbol"]["raw"], abi_bytes32_text("MOCK"))
        self.assertEqual(meta["name"]["status"], "unresolved")
        self.assertIsNone(meta["name"]["value"])
        self.assertIn("empty return", meta["name"]["reason"])
        probes = {p["name"]: p for p in packet["probes"]}
        self.assertEqual(probes["owner()"]["status"], "reverted")
        self.assertIsNone(probes["owner()"]["raw"])
        # a revert is a fact about the contract, never a limitation
        self.assertEqual(packet["limitations"], [])
        self.assertEqual(packet["coverage_status"], "complete")
        # reverted reads stay reproducible: recorded with their exact parameters
        reverted = [f for f in packet["failed_calls"] if f["outcome"] == "reverted"]
        self.assertEqual({f["params"][0]["data"] for f in reverted}, {SEL["owner"], SEL["getOwner"]})
        self.assertTrue(all(f["params"][1] == PINNED_HEX and f["limitation_id"] is None for f in reverted))

    def test_extra_call_and_raw_selector(self):
        balance_sel = ddcore.selector("balanceOf(address)")
        calls = {balance_sel: abi_word(12345), "0xdeadbeef": abi_string("hello")}
        url, server = self.serve(make_handlers(calls=calls))
        rc, packet, out, err = self.run_probe(url, "--call", f"balanceOf(address):{OWNER}", "--call", "0xdeadbeef")
        self.assertEqual(rc, 0, err)
        probes = {p["name"]: p for p in packet["probes"]}
        bal = probes[f"balanceOf(address):{OWNER}"]
        self.assertEqual(bal["status"], "ok")
        self.assertEqual(bal["decoded_candidates"]["uint"], "12345")
        self.assertEqual(bal["calldata"], balance_sel + "0" * 24 + OWNER[2:].lower())
        self.assertEqual(probes["0xdeadbeef"]["decoded_candidates"]["string"], "hello")

    def test_block_tags_safe_and_finalized(self):
        for tag in ("safe", "finalized"):
            url, server = self.serve(make_handlers())
            rc, packet, out, err = self.run_probe(url, "--chain-id", "1", "--block", tag, out_name=f"{tag}.json")
            self.assertEqual(rc, 0, err)
            self.assertEqual(server.params_for("eth_getBlockByNumber"), [[tag, False]])
            self.assertEqual(packet["pin"]["block_number"], BLOCK_NUMBER)
            # the tag is resolved to a number: every later read is at the pinned hex, never at the tag
            for method in ("eth_getCode", "eth_getStorageAt", "eth_call"):
                for params in server.params_for(method):
                    self.assertEqual(params[-1], PINNED_HEX)
        help_text = rpc_probe.build_parser().format_help()
        self.assertIn("latest|safe|finalized|N", help_text)


class IdentityAndCoverageTests(ProbeTestBase):
    def test_d_chain_mismatch(self):
        url, server = self.serve(make_handlers(chain_hex="0x1"))
        code, packet, out, err = self.run_probe(url, "--chain-id", "8453")
        self.assertEqual(code, 3)
        self.assertEqual(packet["identity"]["status"], "CHAIN_MISMATCH")
        self.assertEqual((packet["identity"]["requested"], packet["identity"]["observed"]), (8453, 1))
        self.assertEqual(packet["requested"]["chain_id"], 8453)
        self.assertEqual(packet["observed"]["chain_id"], 1)
        self.assertIn("requested chain 8453 but RPC reports 1", err)
        self.assertIn("same-symbol token on another chain is not the target", err)
        # an identity failure is recorded under identity only, never as a coverage limitation
        self.assertEqual(packet["limitations"], [])
        self.assertIn("identity failure, not a coverage limitation", packet["identity"]["detail"])
        # no reads were made at the address on the wrong chain
        self.assertEqual(server.count("eth_getCode"), 0)
        self.assertEqual(server.count("eth_call"), 0)
        self.assertIsNone(packet["pin"])
        self.assertEqual(packet["coverage_status"], "partial")

    def test_d2_chain_id_bool_is_unverified(self):
        url, server = self.serve(make_handlers(overrides={"eth_chainId": True}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 1)
        self.assertEqual(packet["identity"]["status"], "UNVERIFIED")
        self.assertIsNone(packet["observed"]["chain_id"])
        self.assertEqual(server.count("eth_getCode"), 0)
        self.assertIn("non-quantity", packet["limitations"][0]["description"])

    def test_e_rate_limit_on_storage_is_a_limitation(self):
        def storage_429(params):
            raise HttpStatus(429)

        url, server = self.serve(make_handlers(overrides={"eth_getStorageAt": storage_429}))
        with mock.patch("time.sleep", lambda s: None):  # skip ddcore retry backoff
            code, packet, out, err = self.run_probe(url, "--chain-id", "1", "--retries", "2")
        self.assertEqual(code, 0, err)
        self.assertEqual(packet["coverage_status"], "partial")
        kinds = {lim["kind"] for lim in packet["limitations"]}
        self.assertEqual(kinds, {"rpc_rate_limit"})
        ids = [lim["limitation_id"] for lim in packet["limitations"]]
        self.assertEqual(ids, [f"L{i}" for i in range(1, len(ids) + 1)])
        for lim in packet["limitations"]:
            self.assertIn("A-UPGRADE", lim["affected_check_ids"])
            self.assertEqual(lim["affected_addresses"], [TOKEN])
            self.assertEqual(lim["retry_attempts"], 2)
            self.assertNotIn(FAKE_KEY, lim["description"])
        # the rest of the packet is still produced
        self.assertEqual(packet["runtime"]["proxy"]["status"], "unknown")
        self.assertEqual(packet["runtime"]["code_hash"], ddcore.keccak256_hex(TOKEN_CODE))
        self.assertEqual(packet["metadata"]["name"]["status"], "resolved")
        self.assertEqual({p["name"] for p in packet["probes"]}, {"owner()", "getOwner()", "paused()"})
        self.assertIn("rpc_rate_limit", out)

    def test_e2_pin_failure_is_fatal_but_packet_written(self):
        def no_block(params):
            raise JsonRpcError(-32000, "missing trie node: historical state unavailable")

        url, server = self.serve(make_handlers(overrides={"eth_getBlockByNumber": no_block}))
        # pruned state never reappears: no retry, hence no backoff sleep even with retries enabled
        with mock.patch("time.sleep", side_effect=AssertionError("rpc_pruned must not be retried")):
            code, packet, out, err = self.run_probe(url, "--chain-id", "1", "--block", "5", "--retries", "2")
        self.assertEqual(code, 1)
        self.assertIsNone(packet["pin"])
        self.assertEqual(packet["limitations"][0]["kind"], "rpc_pruned")
        self.assertEqual(packet["limitations"][0]["retry_attempts"], 0)
        self.assertEqual(server.count("eth_getBlockByNumber"), 1)
        self.assertEqual(packet["coverage_status"], "partial")
        self.assertEqual(server.count("eth_getCode"), 0)

    def test_e3_timestamp_overflow_is_a_pin_limitation(self):
        url, server = self.serve(make_handlers(overrides={"eth_getBlockByNumber": lambda p: {**HEADER, "timestamp": "0xffffffffffffffff"}}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 1)
        self.assertIsNone(packet["pin"])
        self.assertEqual(packet["limitations"][0]["kind"], "rpc_error")
        self.assertIn("timestamp out of range", packet["limitations"][0]["description"])
        self.assertEqual(server.count("eth_getCode"), 0)

    def test_e4_malformed_http_is_a_limitation(self):
        with RawResponseServer(b"garbage\r\n\r\n") as srv:
            code, packet, out, err = self.run_probe(srv.url, "--chain-id", "1", "--timeout", "3")
        self.assertEqual(code, 1)
        self.assertEqual(packet["identity"]["status"], "UNVERIFIED")
        self.assertEqual(packet["limitations"][0]["kind"], "rpc_error")
        self.assertIn("malformed HTTP response", packet["limitations"][0]["description"])

    def test_e5_non_string_code_and_storage_are_limitations(self):
        # eth_getCode -> null must never become "EOA / not a contract" with complete coverage
        url, server = self.serve(make_handlers(overrides={"eth_getCode": lambda p: None}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 0, err)
        rt = packet["runtime"]
        self.assertEqual(rt["runtime_status"], "unknown")
        self.assertIsNone(rt["is_contract"])
        self.assertIsNone(rt["code_hash"])
        self.assertEqual(rt["proxy"]["status"], "unknown")
        self.assertEqual(packet["coverage_status"], "partial")
        self.assertEqual(packet["limitations"][0]["kind"], "rpc_error")
        self.assertIn("non-string", packet["limitations"][0]["description"])
        self.assertIn("A-UPGRADE", packet["limitations"][0]["affected_check_ids"])
        # eth_getStorageAt -> null must never become "not_proxy"
        url, server = self.serve(make_handlers(overrides={"eth_getStorageAt": lambda p: None}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1", out_name="storage.json")
        self.assertEqual(code, 0, err)
        self.assertEqual(packet["runtime"]["runtime_status"], "contract")
        self.assertEqual(packet["runtime"]["proxy"]["status"], "unknown")
        self.assertEqual(packet["coverage_status"], "partial")
        self.assertEqual(len(packet["limitations"]), 4)
        self.assertTrue(all("A-UPGRADE" in lim["affected_check_ids"] for lim in packet["limitations"]))
        # a non-string eth_call result is a malformed response too, not a silent "unresolved"
        url, server = self.serve(make_handlers(overrides={"eth_call": lambda p: {"weird": 1}}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1", out_name="call.json")
        self.assertEqual(code, 0, err)
        self.assertEqual(packet["coverage_status"], "partial")
        self.assertEqual(packet["metadata"]["symbol"]["status"], "unresolved")
        self.assertIn("limitation", packet["metadata"]["symbol"]["reason"])
        self.assertTrue(all(p["status"] == "unavailable" for p in packet["probes"]))

    def test_e6_eoa_target_is_qualified(self):
        # the mock still answers eth_call for the address: a lying or misrouted endpoint must be visible
        url, server = self.serve(make_handlers(code={TOKEN: "0x"}))
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 0, err)
        rt = packet["runtime"]
        self.assertEqual(rt["runtime_status"], "eoa")
        self.assertFalse(rt["is_contract"])
        self.assertEqual(rt["code_hash"], ddcore.EMPTY_CODE_HASH)
        self.assertEqual(server.count("eth_getStorageAt"), 0)
        self.assertGreater(server.count("eth_call"), 0)  # calls are still made
        for k, m in packet["metadata"].items():
            self.assertEqual(m["status"], "unresolved", k)
            self.assertIsNone(m["value"], k)
            self.assertEqual(m["qualifier"], rpc_probe.EOA_QUALIFIER, k)
            self.assertIn(rpc_probe.EOA_QUALIFIER, m["reason"], k)
            self.assertTrue(m["raw"].startswith("0x"), k)  # what the endpoint said is preserved
        self.assertIn("inconsistent", packet["metadata"]["symbol"]["reason"])
        for p in packet["probes"]:
            self.assertEqual(p["qualifier"], rpc_probe.EOA_QUALIFIER)
            self.assertTrue(p["detail"].startswith("INCONSISTENT:"), p)
        self.assertEqual(packet["coverage_status"], "complete")
        self.assertIn("runtime_status=eoa", out)

    def test_f_only_allowlisted_methods(self):
        url, server = self.serve(make_handlers())
        code, packet, out, err = self.run_probe(url, "--chain-id", "1")
        self.assertEqual(code, 0, err)
        sent = set(server.methods())
        self.assertTrue(sent)
        self.assertTrue(sent.issubset(ddcore.READ_ONLY_METHODS), sent - ddcore.READ_ONLY_METHODS)
        self.assertEqual(non_allowlisted_methods(server), [])
        for c in packet["calls"]:
            self.assertIn(c["method"], ddcore.READ_ONLY_METHODS)

    def test_g_cache_round_trip(self):
        url, server = self.serve(make_handlers())
        cache = os.path.join(self.tmp, "cache.json")
        code1, p1, _, err1 = self.run_probe(url, "--chain-id", "1", "--block", str(BLOCK_NUMBER), "--cache", cache, out_name="p1.json")
        self.assertEqual(code1, 0, err1)
        self.assertTrue(os.path.exists(cache))
        first_counts = {m: server.count(m) for m in ("eth_getCode", "eth_getStorageAt", "eth_call", "eth_getBlockByNumber", "eth_chainId")}
        self.assertTrue(all(c["cached"] is False for c in p1["calls"]))

        self.assertEqual(p1["cache"]["status"], "used")
        self.assertEqual([f["cached"] for f in p1["failed_calls"]], [False])

        code2, p2, _, err2 = self.run_probe(url, "--chain-id", "1", "--block", str(BLOCK_NUMBER), "--cache", cache, out_name="p2.json")
        self.assertEqual(code2, 0, err2)
        pinned_reads = [c for c in p2["calls"] if c["method"] in ("eth_getCode", "eth_getStorageAt", "eth_call", "eth_getBlockByNumber")]
        self.assertTrue(pinned_reads)
        self.assertTrue(all(c["cached"] is True for c in pinned_reads), pinned_reads)
        for m in ("eth_getCode", "eth_getStorageAt", "eth_getBlockByNumber", "eth_call"):
            self.assertEqual(server.count(m), first_counts[m], f"{m} was re-sent despite the cache")
        # the reverting probe (getOwner()) is a fact about the contract at the pin: cached and replayed as a revert,
        # recorded in calls with its outcome and in failed_calls as cached
        replayed = [c for c in p2["calls"] if c["method"] == "eth_call" and c["outcome"] == "reverted"]
        self.assertEqual([c["params"][0]["data"] for c in replayed], [SEL["getOwner"]])
        self.assertEqual([(f["outcome"], f["cached"]) for f in p2["failed_calls"]], [("reverted", True)])
        self.assertEqual({p["name"]: p["status"] for p in p2["probes"]}["getOwner()"], "reverted")
        # identity facts are never served from the cache
        self.assertEqual(server.count("eth_chainId"), first_counts["eth_chainId"] + 1)
        self.assertTrue(all(c["cached"] is False for c in p2["calls"] if c["method"] == "eth_chainId"))
        self.assertEqual(p2["pin"]["block_hash"], p1["pin"]["block_hash"])
        self.assertEqual(p2["runtime"]["code_hash"], p1["runtime"]["code_hash"])
        with open(cache, encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn(FAKE_KEY, text)
        header = json.loads(text)
        self.assertEqual((header["cache_version"], header["chain_id"]), (ddcore.CACHE_FORMAT_VERSION, 1))
        self.assertTrue(all(e["chain_id"] == 1 for e in header["entries"].values()))

    def test_h_cross_chain_cache_isolation(self):
        """Two mocks on 127.0.0.1 at different ports serving different chains share one --cache file: the second
        run must NOT be served chain-1 facts (cached=False on eth_getCode, its own code hash and symbol)."""
        code_a = "0x" + (b"\x60\x80" + hashlib.sha256(b"chain 1 runtime").digest()).hex()
        code_b = "0x" + (b"\x60\x80" + hashlib.sha256(b"chain 8453 runtime").digest()).hex()
        url_a, server_a = self.serve(make_handlers(chain_hex="0x1", code={TOKEN: code_a}, calls={SEL["symbol"]: abi_string("ETHTOKEN")}), path="/eth")
        url_b, server_b = self.serve(make_handlers(chain_hex="0x2105", code={TOKEN: code_b}, calls={SEL["symbol"]: abi_string("BASETOKEN")}), path="/base")
        cache = os.path.join(self.tmp, "shared-cache.json")
        code1, pa, _, err_a = self.run_probe(url_a, "--chain-id", "1", "--block", str(BLOCK_NUMBER), "--cache", cache, out_name="a.json")
        self.assertEqual(code1, 0, err_a)
        code2, pb, out_b, err_b = self.run_probe(url_b, "--chain-id", "8453", "--block", str(BLOCK_NUMBER), "--cache", cache, out_name="b.json")
        self.assertEqual(code2, 0, err_b)
        self.assertEqual(pb["identity"]["status"], "MATCH")
        self.assertEqual([c["cached"] for c in pb["calls"] if c["method"] == "eth_getCode"], [False])
        self.assertEqual(pb["runtime"]["code_hash"], ddcore.keccak256_hex(bytes.fromhex(code_b[2:])))
        self.assertNotEqual(pb["runtime"]["code_hash"], pa["runtime"]["code_hash"])
        self.assertEqual(pb["metadata"]["symbol"]["value"], "BASETOKEN")
        self.assertEqual(server_b.count("eth_getCode"), 1)
        # the file was recorded for chain 1, so run B refused it: nothing read from or written to it
        self.assertEqual(pb["cache"]["status"], "ignored")
        self.assertIn("belongs to chain 1", pb["cache"]["note"])
        self.assertIn("WARNING", err_b)
        with open(cache, encoding="utf-8") as f:
            header = json.load(f)
        self.assertEqual(header["chain_id"], 1)
        self.assertTrue(all(e["chain_id"] == 1 for e in header["entries"].values()))
        # and a fresh run on chain 1 still uses the file
        code3, pa2, _, err3 = self.run_probe(url_a, "--chain-id", "1", "--block", str(BLOCK_NUMBER), "--cache", cache, out_name="a2.json")
        self.assertEqual(code3, 0, err3)
        self.assertEqual(pa2["cache"]["status"], "used")
        self.assertEqual([c["cached"] for c in pa2["calls"] if c["method"] == "eth_getCode"], [True])
        self.assertEqual(server_a.count("eth_getCode"), 1)


class UsageTests(ProbeTestBase):
    def test_malformed_address_exit_2(self):
        url, server = self.serve(make_handlers())
        bad = TOKEN[:-1] + ("0" if TOKEN[-1] != "0" else "1")
        bad = bad[:2] + bad[2:].swapcase()  # wrong mixed case -> EIP-55 failure
        code, packet, out, err = self.run_probe(url, "--chain-id", "1", "--address", bad)
        self.assertEqual(code, 2)
        self.assertIn("malformed --address", err)
        self.assertEqual(server.calls, [])

    def test_no_rpc_exit_2(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(rpc_probe.ENV_RPC, None)
            so, se = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
                code = rpc_probe.main(["--address", TOKEN, "--out", os.path.join(self.tmp, "x.json")])
        self.assertEqual(code, 2)
        self.assertIn(rpc_probe.ENV_RPC, se.getvalue())

    def test_env_fallback_used(self):
        url, server = self.serve(make_handlers())
        with mock.patch.dict(os.environ, {rpc_probe.ENV_RPC: url}):
            so, se = io.StringIO(), io.StringIO()
            out = os.path.join(self.tmp, "env.json")
            with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
                code = rpc_probe.main(["--address", TOKEN, "--out", out, "--chain-id", "1"])
        self.assertEqual(code, 0, se.getvalue())
        self.assertGreater(server.count("eth_chainId"), 0)

    def test_key_like_argument_refused_without_network(self):
        url, server = self.serve(make_handlers())
        code, packet, out, err = self.run_probe(url, "--chain-id", "1", "--call", "0x" + "ab" * 32)
        self.assertEqual(code, 2)
        self.assertIn("private key", err)
        self.assertEqual(server.calls, [])
        code, packet, out, err = self.run_probe(url, "--private-key", "x")
        self.assertEqual(code, 2)
        self.assertEqual(server.calls, [])

    def test_bad_call_spec_exit_2(self):
        url, server = self.serve(make_handlers())
        code, packet, out, err = self.run_probe(url, "--call", "balanceOf(address)")
        self.assertEqual(code, 2)
        self.assertIn("expects 1 argument", err)
        self.assertEqual(server.calls, [])
        # integer arguments must fit the declared width: never send calldata no ABI decoder would accept
        for spec, fragment in (("foo(uint8):300", "out of range"), ("foo(uint256):-1", "out of range"),
                               ("foo(int8):-129", "out of range"), ("foo(uint7):1", "width"), ("foo(uint8):x", "not an integer")):
            code, packet, out, err = self.run_probe(url, "--call", spec, out_name="never.json")
            self.assertEqual(code, 2, spec)
            self.assertIn(fragment, err, spec)
        self.assertEqual(server.calls, [])

    def test_userinfo_url_exit_2(self):
        url, server = self.serve(make_handlers())
        code, packet, out, err = self.run_probe(url.replace("http://", "http://alice:QUERYSECRET@"), "--chain-id", "1")
        self.assertEqual(code, 2)
        self.assertIn("userinfo", err)
        self.assertNotIn("QUERYSECRET", err)
        self.assertEqual(server.calls, [])

    def test_json_flag_prints_packet(self):
        url, server = self.serve(make_handlers())
        code, packet, out, err = self.run_probe(url, "--chain-id", "1", "--json")
        self.assertEqual(code, 0)
        parsed = json.loads(out)
        self.assertEqual(parsed["pin"]["block_hash"], HEADER["hash"])
        self.assertIn("rpc_probe: target packet", err)


class UnitHelperTests(unittest.TestCase):
    def test_minimal_proxy_detection(self):
        code = bytes.fromhex("363d3d373d3d3d363d73") + bytes.fromhex(IMPL[2:]) + bytes.fromhex("5af43d82803e903d91602b57fd5bf3")
        self.assertEqual(rpc_probe.detect_minimal_proxy(code), IMPL)
        self.assertIsNone(rpc_probe.detect_minimal_proxy(code + b"\x00"))
        self.assertIsNone(rpc_probe.detect_minimal_proxy(TOKEN_CODE))

    def test_classify_call_failure(self):
        E = ddcore.RpcCoverageError
        self.assertEqual(rpc_probe.classify_call_failure(E("rpc_error", "rpc error 3: execution reverted", "eth_call")), "reverted")
        self.assertEqual(rpc_probe.classify_call_failure(E("rpc_error", "rpc error -32000: execution reverted: x", "eth_call")), "reverted")
        self.assertIsNone(rpc_probe.classify_call_failure(E("rpc_error", "HTTP 500 from host", "eth_call")))
        self.assertIsNone(rpc_probe.classify_call_failure(E("rpc_rate_limit", "HTTP 429", "eth_call")))
        self.assertIsNone(rpc_probe.classify_call_failure(E("rpc_timeout", "timeout", "eth_call")))

    def test_word_to_address(self):
        self.assertIsNone(rpc_probe.word_to_address("0x" + "00" * 32))
        self.assertIsNone(rpc_probe.word_to_address("0x0"))
        self.assertEqual(rpc_probe.word_to_address(abi_address(IMPL)), IMPL)

    def test_parse_call_spec(self):
        label, data = rpc_probe.parse_call_spec("owner()")
        self.assertEqual((label, data), ("owner()", "0x8da5cb5b"))
        label, data = rpc_probe.parse_call_spec(f"allowance(address,address):{OWNER},{ADMIN}")
        self.assertEqual(data[:10], ddcore.selector("allowance(address,address)"))
        self.assertEqual(len(data), 10 + 128)
        with self.assertRaises(ValueError):
            rpc_probe.parse_call_spec("foo(string):x")
        with self.assertRaises(ValueError):
            rpc_probe.parse_call_spec("not a signature")
        # width validation
        self.assertTrue(rpc_probe.parse_call_spec("foo(uint8):255")[1].endswith("ff"))
        self.assertTrue(rpc_probe.parse_call_spec("foo(int8):-128")[1].endswith("ff" * 31 + "80"))
        self.assertTrue(rpc_probe.parse_call_spec("foo(uint):0x10")[1].endswith("10"))
        for bad in ("foo(uint8):256", "foo(int8):-129", "foo(int8):128", "foo(uint):-1", "foo(uint7):1", "foo(uint264):1"):
            with self.assertRaises(ValueError, msg=bad):
                rpc_probe.parse_call_spec(bad)
        self.assertEqual(rpc_probe._abi_kind("uint"), ("uint", 256))
        self.assertEqual(rpc_probe._abi_kind("int24"), ("int", 24))

    def test_find_key_material(self):
        self.assertIsNone(rpc_probe.find_key_material(["--rpc", "http://127.0.0.1:1", "--address", TOKEN]))
        self.assertIsNotNone(rpc_probe.find_key_material(["0x" + "1" * 64]))
        self.assertIsNotNone(rpc_probe.find_key_material(["a" * 64]))
        self.assertIsNotNone(rpc_probe.find_key_material(["--mnemonic=word"]))
        self.assertIsNotNone(rpc_probe.find_key_material([" ".join(["word"] * 12)]))
        self.assertIsNotNone(rpc_probe.find_key_material(["word"] * 12))
        self.assertIsNone(rpc_probe.find_key_material(["word"] * 11))

    def test_normalize_block_arg(self):
        self.assertEqual(rpc_probe.normalize_block_arg("latest"), "latest")
        self.assertEqual(rpc_probe.normalize_block_arg("safe"), "safe")
        self.assertEqual(rpc_probe.normalize_block_arg("Finalized"), "finalized")
        self.assertEqual(rpc_probe.normalize_block_arg("20000000"), "0x1312d00")
        self.assertEqual(rpc_probe.normalize_block_arg("0x01312D00"), "0x1312d00")
        with self.assertRaises(ValueError):
            rpc_probe.normalize_block_arg("pending")
        with self.assertRaises(ValueError):
            rpc_probe.normalize_block_arg("-5")


if __name__ == "__main__":
    unittest.main()
