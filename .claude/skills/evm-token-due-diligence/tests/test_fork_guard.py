"""Tests for scripts/fork_guard.py against an in-process JSON-RPC mock (tests/mock_rpc.py). No network."""
from __future__ import annotations

import contextlib
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
import fork_guard  # noqa: E402
from mock_rpc import JsonRpcError, non_allowlisted_methods, start_mock  # noqa: E402

FAKE_FORK_KEY = "UPSTREAMKEYabcdefghijklmnop0123"
FORK_URL = f"https://upstream.example.invalid/v2/{FAKE_FORK_KEY}"


def method_not_found(params):
    raise JsonRpcError(-32601, "Method not found")


def anvil_handlers(chain_hex="0x1f", block_hex="0x64", fork_block=100):
    return {
        "web3_clientVersion": "anvil/v0.2.0",
        "anvil_nodeInfo": {
            "currentBlockNumber": block_hex,
            "forkConfig": {"forkUrl": FORK_URL, "forkBlockNumber": fork_block, "forkRetryBackoff": 1000},
            "environment": {"chainId": int(chain_hex, 16)},
        },
        "hardhat_metadata": method_not_found,
        "eth_chainId": chain_hex,
        "eth_blockNumber": block_hex,
    }


def hardhat_handlers(chain_id=31337, forked_chain=1):
    return {
        "web3_clientVersion": "HardhatNetwork/2.22.0/@nomicfoundation/ethereumjs-vm/7.0.0",
        "anvil_nodeInfo": method_not_found,
        "hardhat_metadata": {
            "clientVersion": "HardhatNetwork/2.22.0",
            "chainId": chain_id,
            "latestBlockNumber": 150,
            "forkedNetwork": {"chainId": forked_chain, "forkBlockNumber": 120, "forkBlockHash": "0x" + "ab" * 32},
        },
        "eth_chainId": hex(chain_id),
        "eth_blockNumber": "0x96",
    }


def plain_node_handlers():
    return {
        "web3_clientVersion": "Geth/v1.13.0-stable/linux-amd64/go1.21",
        "anvil_nodeInfo": method_not_found,
        "hardhat_metadata": method_not_found,
        "eth_chainId": "0x1f",
        "eth_blockNumber": "0x64",
    }


class GuardTestBase(unittest.TestCase):
    def setUp(self):
        tmpdir = tempfile.TemporaryDirectory(prefix="fork_guard_test_")
        self.addCleanup(tmpdir.cleanup)  # nothing is left behind in the temp directory
        self.tmp = tmpdir.name
        self.servers = []

    def tearDown(self):
        for s in self.servers:
            self.assertEqual(non_allowlisted_methods(s), [])
            s.stop()

    def serve(self, handlers):
        url, server = start_mock(handlers)
        self.servers.append(server)
        return url, server

    def run_guard(self, *argv, out_name="attestation.json"):
        out = os.path.join(self.tmp, out_name)
        so, se = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            code = fork_guard.main([*argv, "--out", out, "--retries", "0"])
        att = None
        if os.path.exists(out):
            with open(out, "r", encoding="utf-8") as f:
                att = json.load(f)
        return code, att, so.getvalue(), se.getvalue()


class ForkGuardTests(GuardTestBase):
    def test_a_anvil_like_local_fork_passes(self):
        url, server = self.serve(anvil_handlers())
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", "--expect-fork-block", "100")
        self.assertEqual(code, 0, err)
        self.assertTrue(att["fork_verified_disposable"])
        self.assertEqual(att["fork_type"], "anvil")
        self.assertEqual(att["fork_chain_id"], 31)
        self.assertEqual(att["fork_block"], 100)
        self.assertEqual(att["current_block"], 100)
        self.assertEqual(att["client_version"], "anvil/v0.2.0")
        self.assertIn("<redacted>", att["fork_source_redacted"])
        self.assertNotIn(FAKE_FORK_KEY, json.dumps(att))
        self.assertNotIn(FAKE_FORK_KEY, out)
        self.assertEqual(att["notes"], "Results produced on this fork are COUNTERFACTUAL and must be labeled as such")
        self.assertTrue(all(c["ok"] for c in att["checks"]))
        names = [c["name"] for c in att["checks"]]
        for required in ("no_key_material_in_argv", "local_endpoint", "fork_self_identification",
                         "chain_id_matches", "block_at_or_above_expected_fork_block", "fork_config_reported"):
            self.assertIn(required, names)
        self.assertIn("fork_guard: checks", out)
        self.assertTrue(ddcore.parse_iso_utc(att["checked_at_utc"]) > 0)

    def test_b_chain_id_mismatch_fails(self):
        url, server = self.serve(anvil_handlers(chain_hex="0x1f"))
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "8453")
        self.assertEqual(code, 1)
        self.assertFalse(att["fork_verified_disposable"])
        failed = {c["name"] for c in att["checks"] if not c["ok"]}
        self.assertEqual(failed, {"chain_id_matches"})
        self.assertIn("FAIL", out)

    def test_b2_hardhat_default_chain_id_hint(self):
        url, server = self.serve(hardhat_handlers(chain_id=31337, forked_chain=1))
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "1")
        self.assertEqual(code, 1)
        self.assertEqual(att["fork_type"], "hardhat")
        self.assertEqual(att["fork_block"], 120)
        chain_check = next(c for c in att["checks"] if c["name"] == "chain_id_matches")
        self.assertFalse(chain_check["ok"])
        self.assertIn("forkedNetwork.chainId=1", chain_check["detail"])
        # and passes when the expectation matches what the node actually reports
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31337", out_name="a2.json")
        self.assertEqual(code, 0, err)
        self.assertTrue(att["fork_verified_disposable"])

    def test_c_non_local_host_fails_before_any_network(self):
        with mock.patch.object(ddcore.RpcClient, "_do", side_effect=AssertionError("network attempted")):
            code, att, out, err = self.run_guard("--rpc", "http://rpc.example.invalid:8545", "--expect-chain-id", "1")
        self.assertEqual(code, 1)
        self.assertFalse(att["fork_verified_disposable"])
        local = next(c for c in att["checks"] if c["name"] == "local_endpoint")
        self.assertFalse(local["ok"])
        self.assertIn("DNS name", local["detail"])
        self.assertIsNone(att["client_version"])
        self.assertIsNone(att["fork_chain_id"])
        # a public literal IP is rejected too
        with mock.patch.object(ddcore.RpcClient, "_do", side_effect=AssertionError("network attempted")):
            code, att, out, err = self.run_guard("--rpc", "http://203.0.113.5:8545", "--expect-chain-id", "1", out_name="b.json")
        self.assertEqual(code, 1)
        self.assertIn("not loopback", next(c for c in att["checks"] if c["name"] == "local_endpoint")["detail"])

    def test_c2_host_classification(self):
        ok_hosts = ["http://127.0.0.1:8545", "http://localhost:8545", "http://localhost.:8545", "http://LOCALHOST:8545",
                    "http://[::1]:8545", "http://[::ffff:127.0.0.1]:8545", "http://10.1.2.3:8545", "http://172.16.0.9:8545",
                    "http://172.31.255.1", "http://192.168.1.10:8545", "http://dev.localhost:8545", "http://[fd00::1]:8545"]
        bad_hosts = ["http://172.32.0.1:8545", "http://8.8.8.8", "https://rpc.example.invalid", "ws://127.0.0.1:8545",
                     "http://100.64.0.1", "http://169.254.1.1", "http://0.0.0.0:8545", "http://[::]:8545", "http://[fe80::1]:8545",
                     "http://[::ffff:8.8.8.8]:8545", "http://127.1:8545", "http://127.0.0.1.example.com:8545"]
        for u in ok_hosts:
            self.assertTrue(fork_guard.classify_host(u)[0], u)
        for u in bad_hosts:
            self.assertFalse(fork_guard.classify_host(u)[0], u)
        ok, why = fork_guard.classify_host("http://0.0.0.0:8545")
        self.assertFalse(ok)
        self.assertIn("use 127.0.0.1 instead", why)
        self.assertIn("loopback name", fork_guard.classify_host("http://localhost.:8545")[1])
        # a malformed port never raises out of classification (main() rejects it as usage)
        self.assertTrue(fork_guard.classify_host("http://127.0.0.1:abc")[0])

    def test_d_node_without_fork_identification_fails(self):
        url, server = self.serve(plain_node_handlers())
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31")
        self.assertEqual(code, 1)
        self.assertFalse(att["fork_verified_disposable"])
        self.assertEqual(att["fork_type"], "unknown_local")
        ident = next(c for c in att["checks"] if c["name"] == "fork_self_identification")
        self.assertFalse(ident["ok"])
        self.assertIn("does not self-identify as a disposable fork", ident["detail"])
        chain = next(c for c in att["checks"] if c["name"] == "chain_id_matches")
        self.assertTrue(chain["ok"])  # chain matched, but that alone is not enough
        self.assertIsNone(att["fork_block"])

    def test_d3_empty_or_unrecognised_introspection_fails_closed(self):
        # a gateway that answers unknown methods with {} must not pass as an anvil/hardhat fork
        for method in ("anvil_nodeInfo", "hardhat_metadata"):
            handlers = plain_node_handlers()
            handlers[method] = {}
            url, server = self.serve(handlers)
            code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", out_name=f"{method}.json")
            self.assertEqual(code, 1, method)
            self.assertFalse(att["fork_verified_disposable"])
            self.assertEqual(att["fork_type"], "unknown_local")
            ident = next(c for c in att["checks"] if c["name"] == "fork_self_identification")
            self.assertFalse(ident["ok"])
            self.assertIn(f"{method}=unrecognised object", ident["detail"])
        # an unexpected shape (no known key) is treated the same way
        handlers = plain_node_handlers()
        handlers["anvil_nodeInfo"] = {"unexpected": 1}
        url, server = self.serve(handlers)
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", out_name="shape.json")
        self.assertEqual(code, 1)
        self.assertFalse(att["fork_verified_disposable"])
        # a recognised anvil object identifies the node even when web3_clientVersion is unhelpful
        handlers = anvil_handlers()
        handlers["web3_clientVersion"] = "custom-build/1.0"
        url, server = self.serve(handlers)
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", out_name="obj.json")
        self.assertEqual(code, 0, err)
        self.assertEqual(att["fork_type"], "anvil")

    def test_d2_block_below_expected_fails(self):
        url, server = self.serve(anvil_handlers(block_hex="0x10"))
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", "--expect-fork-block", "100")
        self.assertEqual(code, 1)
        blk = next(c for c in att["checks"] if c["name"] == "block_at_or_above_expected_fork_block")
        self.assertFalse(blk["ok"])

    def test_e_private_key_in_argv_refused_without_contacting_server(self):
        url, server = self.serve(anvil_handlers())
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", "0x" + "ab" * 32)
        self.assertEqual(code, 2)
        self.assertIsNone(att)
        self.assertIn("refusing to run", err)
        self.assertEqual(server.calls, [])
        for bad in (["--private-key", "0xabc"], ["--mnemonic=x"], ["--seed", "y"], ["ab" * 32],
                    [" ".join(["abandon"] * 12)]):
            code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", *bad, out_name="never.json")
            self.assertEqual(code, 2, bad)
            self.assertEqual(server.calls, [], bad)

    def test_usage_errors(self):
        code, att, out, err = self.run_guard("--rpc", "http://127.0.0.1:1")
        self.assertEqual(code, 2)
        code, att, out, err = self.run_guard("--rpc", "http://127.0.0.1:1", "--expect-chain-id", "0")
        self.assertEqual(code, 2)
        # an unparseable --rpc (non-numeric port) is a usage error, never a traceback
        with mock.patch.object(ddcore.RpcClient, "_do", side_effect=AssertionError("network attempted")):
            code, att, out, err = self.run_guard("--rpc", "http://127.0.0.1:abc", "--expect-chain-id", "1", out_name="badport.json")
        self.assertEqual(code, 2)
        self.assertIsNone(att)
        self.assertIn("malformed", err)

    def test_json_output(self):
        url, server = self.serve(anvil_handlers())
        code, att, out, err = self.run_guard("--rpc", url, "--expect-chain-id", "31", "--json")
        self.assertEqual(code, 0, err)
        parsed = json.loads(out)
        self.assertTrue(parsed["fork_verified_disposable"])
        self.assertIn("fork_guard: checks", err)

    def test_unreachable_local_endpoint_fails_closed(self):
        # a loopback port with nothing listening: transport error -> checks fail, exit 1, no traceback
        url, server = self.serve(anvil_handlers())
        port = server.server_address[1]
        server.stop()
        self.servers.remove(server)
        code, att, out, err = self.run_guard("--rpc", f"http://127.0.0.1:{port}", "--expect-chain-id", "31")
        self.assertEqual(code, 1)
        self.assertFalse(att["fork_verified_disposable"])
        self.assertFalse(next(c for c in att["checks"] if c["name"] == "fork_self_identification")["ok"])


if __name__ == "__main__":
    unittest.main()
