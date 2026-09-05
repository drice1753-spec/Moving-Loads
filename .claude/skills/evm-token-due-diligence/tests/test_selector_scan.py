"""Tests for scripts/selector_scan.py using hand-assembled synthetic bytecode (stdlib unittest; no network)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import ddcore  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))
import selector_scan as sel  # noqa: E402


# --------------------------------------------------------------------------------------
# tiny assembler
# --------------------------------------------------------------------------------------
def push(data: bytes) -> bytes:
    assert 1 <= len(data) <= 32
    return bytes([0x5F + len(data)]) + data


def push4(selector_hex: str) -> bytes:
    return push(bytes.fromhex(selector_hex[2:]))


def dispatch(selector_hex: str, dest: int) -> bytes:
    # DUP1 PUSH4 <sel> EQ PUSH2 <dest> JUMPI   (the classic solc dispatcher shape)
    return b"\x80" + push4(selector_hex) + b"\x14" + push(dest.to_bytes(2, "big")) + b"\x57"


ADDR = "0x1111111111111111111111111111111111111111"
ADDR2 = "0xabcdefabcdefabcdefabcdefabcdefabcdefabcd"
SELS = {
    "mint(address,uint256)": "0x40c10f19",
    "pause()": "0x8456cb59",
    "upgradeTo(address)": "0x3659cfe6",
    "upgradeToAndCall(address,bytes)": "0x4f1ef286",
    "transferOwnership(address)": "0xf2fde38b",
}
PRELUDE = b"\x60\x00\x35\x60\xe0\x1c"  # PUSH1 0 CALLDATALOAD PUSH1 0xe0 SHR


def sample_dispatcher() -> bytes:
    code = PRELUDE
    for i, s in enumerate(SELS.values()):
        code += dispatch(s, 0x0100 + 0x10 * i)
    code += push(bytes.fromhex(ADDR[2:]))                     # PUSH20 candidate address
    code += push(b"\xf4\xf4")                                  # PUSH2 whose immediate contains 0xf4 (must NOT flag DELEGATECALL)
    code += push(b"\xff\xf4\xf0\xf5\xf1\xf2\xfa" + b"\x00" * 25)  # PUSH32 immediate holding every flagged byte
    code += b"\x00"                                            # STOP
    return code


def eip1167(impl: str) -> bytes:
    return bytes.fromhex("363d3d373d3d3d363d73") + bytes.fromhex(impl[2:]) + bytes.fromhex("5af43d82803e903d91602b57fd5bf3")


def cbor_suffix_with_f4() -> bytes:
    # a2 64 'ipfs' 58 22 <34 bytes> 64 'solc' 43 <3 bytes>  then 2-byte length (0x0033 = 51)
    ipfs = b"\x12\x20" + b"\xf4\xff\xf1" + b"\x11" * 29
    body = b"\xa2" + b"\x64ipfs" + b"\x58\x22" + ipfs + b"\x64solc" + b"\x43" + b"\x00\x08\x13"
    assert len(body) == 51
    return body + (51).to_bytes(2, "big")


class WalkTests(unittest.TestCase):
    def test_push_immediates_are_skipped(self):
        code = push(b"\xf4\xf4") + b"\xf4" + push(b"\xff") + b"\x00"
        ins = sel.walk(code)
        names = [i["name"] for i in ins]
        self.assertEqual(names, ["PUSH2", "DELEGATECALL", "PUSH1", "STOP"])
        self.assertEqual(ins[0]["immediate"], b"\xf4\xf4")
        self.assertEqual(ins[1]["offset"], 3)

    def test_truncated_push_does_not_crash(self):
        code = b"\x00" + push(b"\x01\x02")[:2]  # STOP then PUSH2 with only one immediate byte
        ins = sel.walk(code)
        self.assertTrue(ins[-1]["truncated"])
        res = sel.scan(code)
        self.assertTrue(any("truncated" in c for c in res["caveats"]))


class ScanTests(unittest.TestCase):
    def test_exact_selectors_and_no_false_opcode_flags(self):
        res = sel.scan(sample_dispatcher())
        self.assertEqual(res["selectors"], list(SELS.values()))
        for name, flag in res["opcode_flags"].items():
            self.assertFalse(flag, f"{name} must not be flagged from PUSH immediates")
        self.assertEqual(res["push20_candidates"], [ddcore.to_checksum_address(ADDR)])
        self.assertFalse(res["eip1167"]["is_minimal_proxy"])
        self.assertEqual(res["unmatched_count"], 0)
        self.assertEqual(res["code_size"], len(sample_dispatcher()))
        self.assertEqual(res["code_hash"], ddcore.keccak256_hex(sample_dispatcher()))
        # dispatcher context hint: PUSH4 followed by EQ
        self.assertTrue(all(v == "EQ" for v in res["selector_next_opcode"].values()))

    def test_matches_grouped_by_category_and_check_id(self):
        res = sel.scan(sample_dispatcher())
        by_sig = {m["signature"]: m for m in res["matched"]}
        self.assertEqual(by_sig["mint(address,uint256)"]["check_id"], "A-MINT")
        self.assertEqual(by_sig["mint(address,uint256)"]["category"], "supply")
        self.assertEqual(by_sig["pause()"]["check_id"], "A-RESTRICT")
        self.assertEqual(by_sig["upgradeTo(address)"]["check_id"], "A-UPGRADE")
        self.assertEqual(by_sig["upgradeToAndCall(address,bytes)"]["check_id"], "A-UPGRADE")
        self.assertEqual(by_sig["transferOwnership(address)"]["check_id"], "A-ADMIN")
        self.assertEqual(sorted(res["matched_by_check"]), ["A-ADMIN", "A-MINT", "A-RESTRICT", "A-UPGRADE"])
        for m in res["matched"]:
            self.assertEqual(m["selector"], SELS[m["signature"]])

    def test_real_opcodes_are_flagged(self):
        code = sample_dispatcher() + b"\xf4" + b"\xff" + b"\xf5" + b"\xf1" + b"\xf2" + b"\xfa" + b"\xf0"
        res = sel.scan(code)
        self.assertTrue(all(res["opcode_flags"].values()))
        self.assertEqual(res["opcode_counts"]["DELEGATECALL"], 1)
        self.assertTrue(any("DELEGATECALL present but no EIP-1167" in c for c in res["caveats"]))

    def test_unknown_push4_counts_as_unmatched(self):
        code = push4("0xffffffff") + push4("0x12345678") + push4("0x40c10f19") + b"\x00"
        res = sel.scan(code)
        self.assertEqual(res["selectors"], ["0xffffffff", "0x12345678", "0x40c10f19"])
        self.assertEqual(res["unmatched_count"], 2)
        self.assertEqual([m["signature"] for m in res["matched"]], ["mint(address,uint256)"])

    def test_push20_masks_excluded(self):
        code = push(b"\x00" * 20) + push(b"\xff" * 20) + push(bytes.fromhex(ADDR2[2:])) + push(bytes.fromhex(ADDR2[2:])) + b"\x00"
        res = sel.scan(code)
        self.assertEqual(res["push20_candidates"], [ddcore.to_checksum_address(ADDR2)])
        self.assertEqual(res["push20_excluded_masks"], 2)

    def test_eip1167_detection(self):
        code = eip1167(ADDR2)
        self.assertEqual(len(code), 45)
        res = sel.scan(code)
        self.assertTrue(res["eip1167"]["is_minimal_proxy"])
        self.assertEqual(res["eip1167"]["implementation"], ddcore.to_checksum_address(ADDR2))
        self.assertEqual(res["eip1167"]["variant"], "eip1167")
        self.assertTrue(res["opcode_flags"]["DELEGATECALL"])
        self.assertFalse(res["opcode_flags"]["CALL"])
        self.assertEqual(res["push20_candidates"], [ddcore.to_checksum_address(ADDR2)])

    def test_eip1167_0age_variant(self):
        code = bytes.fromhex("3d3d3d3d363d3d37363d73") + bytes.fromhex(ADDR2[2:]) + bytes.fromhex("5af43d3d93803e603057fd5bf3")
        res = sel.detect_eip1167(code)
        self.assertTrue(res["is_minimal_proxy"])
        self.assertEqual(res["variant"], "eip1167-0age")
        self.assertEqual(res["implementation"], ddcore.to_checksum_address(ADDR2))

    def test_dispatcher_with_delegatecall_is_not_forwarder_like(self):
        code = PRELUDE + dispatch(SELS["mint(address,uint256)"], 0x0100) + push(bytes.fromhex(ADDR[2:])) + b"\xf4\x00"
        res = sel.scan(code)
        self.assertLessEqual(len(code), 96)
        self.assertFalse(res["eip1167"]["is_minimal_proxy"])
        self.assertTrue(res["opcode_flags"]["DELEGATECALL"])

    def test_not_a_proxy_when_pattern_broken(self):
        code = eip1167(ADDR2)[:-1] + b"\x00"  # last byte changed: no longer canonical
        res = sel.detect_eip1167(code)
        # fallback heuristic may still call it forwarder-like, but never the canonical variant
        self.assertNotEqual(res["variant"], "eip1167")

    def test_cbor_metadata_excluded_from_walk(self):
        body = sample_dispatcher()
        code = body + cbor_suffix_with_f4()
        stripped, meta = sel.strip_cbor_metadata(code)
        self.assertEqual(stripped, body)
        self.assertEqual(meta["length"], 53)
        self.assertIn("ipfs", meta["markers"])
        res = sel.scan(code)
        self.assertTrue(res["cbor_metadata"]["detected"])
        self.assertFalse(res["opcode_flags"]["DELEGATECALL"], "0xf4 inside metadata must not flag DELEGATECALL")
        self.assertFalse(res["opcode_flags"]["CALL"])
        self.assertEqual(res["selectors"], list(SELS.values()))
        # hash and size always describe the FULL code
        self.assertEqual(res["code_size"], len(code))
        self.assertEqual(res["code_hash"], ddcore.keccak256_hex(code))
        self.assertTrue(any("CBOR metadata" in c for c in res["caveats"]))

    def test_no_metadata_false_positive_on_plain_code(self):
        _, meta = sel.strip_cbor_metadata(sample_dispatcher())
        self.assertIsNone(meta)

    def test_empty_code(self):
        res = sel.scan(b"")
        self.assertEqual(res["code_size"], 0)
        self.assertEqual(res["code_hash"], ddcore.EMPTY_CODE_HASH)
        self.assertTrue(res["empty_code_hash_matches"])
        self.assertEqual(res["selectors"], [])
        self.assertTrue(any("no runtime code" in c for c in res["caveats"]))

    def test_caveats_always_present(self):
        for code in (b"", sample_dispatcher(), eip1167(ADDR2)):
            res = sel.scan(code)
            self.assertTrue(any("presence of a selector is not proof it is reachable or privileged" in c for c in res["caveats"]))
            self.assertTrue(any("absence is not proof of safety" in c for c in res["caveats"]))


class RiskyTableTests(unittest.TestCase):
    def test_selectors_computed_at_runtime_match_known_values(self):
        table = {r["signature"]: r["selector"] for r in sel.risky_table()}
        for sig, expected in SELS.items():
            self.assertEqual(table[sig], expected)
            self.assertEqual(ddcore.selector(sig), expected)
        # LP custody signatures from the position-manager ABIs
        self.assertEqual(table["decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))"], "0x0c49ccbe")
        self.assertEqual(table["collect((uint256,address,uint128,uint128))"], "0xfc6f7865")
        self.assertEqual(table["burn(uint256)"], "0x42966c68")
        self.assertEqual(table["modifyLiquidities(bytes,uint256)"], "0xdd46508f")

    def test_every_category_maps_to_a_surface_check_id(self):
        ids = {check_id for check_id, _ in sel.RISKY_SIGNATURES.values()}
        self.assertEqual(ids, {"A-MINT", "A-RESTRICT", "A-TAX", "A-SEIZE", "A-UPGRADE", "A-EXTCALL", "A-ADMIN", "B-PRINCIPAL"})

    def test_no_hardcoded_hex_in_table_definition(self):
        for _, sigs in sel.RISKY_SIGNATURES.values():
            for s in sigs:
                self.assertFalse(s.startswith("0x"))
                self.assertIn("(", s)


class StdlibShadowingTests(unittest.TestCase):
    """the scripts directory must not shadow the standard library when scripts/ is first on sys.path."""

    def test_subprocess_and_socket_work_with_scripts_dir_first(self):
        import subprocess
        scripts = os.path.join(os.path.dirname(HERE), "scripts")
        code = (
            "import sys; sys.path.insert(0, %r); "
            "import ddcore, socket, subprocess, asyncio, selectors; "
            "assert hasattr(selectors, 'DefaultSelector') and hasattr(selectors, 'SelectSelector'), 'stdlib API missing'; "
            "print(subprocess.run([sys.executable, '-c', 'print(7)'], capture_output=True, text=True).stdout.strip())"
        ) % scripts
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=scripts)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout.strip(), "7")

    def test_cli_runs_as_script_from_its_own_directory(self):
        import subprocess
        script = os.path.join(os.path.dirname(HERE), "scripts", "selector_scan.py")
        r = subprocess.run([sys.executable, script, "--code", "0x" + sample_dispatcher().hex(), "--json"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["selectors"], list(SELS.values()))


class CliTests(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = sel.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_json_output(self):
        rc, out, _ = self._run(["--code", "0x" + sample_dispatcher().hex(), "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        for key in ("code_hash", "code_size", "selectors", "matched", "unmatched_count", "opcode_flags",
                    "push20_candidates", "eip1167", "caveats"):
            self.assertIn(key, data)
        self.assertEqual(data["selectors"], list(SELS.values()))
        self.assertIn("is_minimal_proxy", data["eip1167"])
        self.assertIn("implementation", data["eip1167"])

    def test_human_output_prints_caveats(self):
        rc, out, _ = self._run(["--code", "0x" + eip1167(ADDR2).hex()])
        self.assertEqual(rc, 0)
        self.assertIn("CAVEATS:", out)
        self.assertIn("presence of a selector is not proof it is reachable or privileged", out)
        self.assertIn("EIP-1167 minimal proxy: YES", out)
        self.assertIn(ddcore.to_checksum_address(ADDR2), out)

    def test_code_file_json_rpc_response(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "code.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"jsonrpc": "2.0", "id": 1, "result": "0x" + sample_dispatcher().hex()}, f)
            rc, out, _ = self._run(["--code-file", p, "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["selectors"], list(SELS.values()))

    def test_code_file_raw_binary_and_plain_hex(self):
        with tempfile.TemporaryDirectory() as d:
            raw = os.path.join(d, "code.bin")
            with open(raw, "wb") as f:
                f.write(sample_dispatcher())
            rc, out, _ = self._run(["--code-file", raw, "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["code_size"], len(sample_dispatcher()))
            hx = os.path.join(d, "code.hex")
            with open(hx, "w", encoding="utf-8") as f:
                f.write(sample_dispatcher().hex() + "\n")
            rc, out, _ = self._run(["--code-file", hx, "--json"])
            self.assertEqual(rc, 0)
            self.assertEqual(json.loads(out)["code_size"], len(sample_dispatcher()))

    def test_usage_errors(self):
        rc, _, err = self._run([])
        self.assertEqual(rc, 2)
        rc, _, err = self._run(["--code", "0xzz"])
        self.assertEqual(rc, 2)
        self.assertIn("cannot read bytecode", err)
        rc, _, _ = self._run(["--code-file", "/nonexistent/path/to/code.hex"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
