"""Tests for scripts/ledger.py (stdlib unittest). Builds a small manifest in memory and writes it to a temp file;
deliberately does NOT depend on tests/fixtures (written by another lane)."""
from __future__ import annotations

import contextlib
import copy
import csv
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import ledger  # noqa: E402

TOKEN = "0x1111111111111111111111111111111111111111"
ADMIN = "0x2222222222222222222222222222222222222222"
TX = "0x" + "ab" * 32
BLOCK_HEX = "0x1312d00"


def small_manifest() -> dict:
    """Only the parts ledger.py reads (findings, evidence, checks, target.metadata); synthetic values."""
    return {
        "manifest_version": "1.0",
        "mode": "focused",
        "target": {"metadata": {"symbol": {"value": "SYN", "status": "resolved", "source": "eth_call symbol() at P1", "evidence_id": "E3"}}},
        "evidence": [
            {"evidence_id": "E1", "chain_id": 31337, "address": TOKEN, "pin_id": "P1", "tx_hash": None, "block_number": 20000000,
             "evidence_type": "rpc_storage", "artifact": "evidence/E1.json",
             "query": {"method": "eth_getStorageAt", "params": [TOKEN, "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103", BLOCK_HEX]},
             "decoding_basis": "EIP-1967 admin slot; low 20 bytes as address", "summary": "admin slot value"},
            {"evidence_id": "E2", "chain_id": 31337, "address": ADMIN, "pin_id": "P1", "tx_hash": None, "block_number": 20000000,
             "evidence_type": "rpc_state", "artifact": "inline", "query": "eth_call getMinDelay() at 0x1312d00",
             "decoding_basis": "uint256 return word", "summary": "getMinDelay() = 0"},
            {"evidence_id": "E3", "chain_id": 31337, "address": TOKEN, "pin_id": "P1", "tx_hash": None, "block_number": 20000000,
             "evidence_type": "rpc_state", "artifact": "evidence/E3.json", "query": "eth_call symbol()",
             "decoding_basis": "ABI string", "summary": "symbol"},
            {"evidence_id": "E4", "chain_id": 31337, "address": TOKEN, "pin_id": None, "tx_hash": TX, "block_number": 19990000,
             "evidence_type": "receipt", "artifact": "evidence/E4.json", "query": {"method": "eth_getTransactionReceipt", "params": [TX]},
             "decoding_basis": "raw", "summary": "historical sell receipt"},
        ],
        "findings": [
            {"finding_id": "F2", "proposition": "At P1 the proxy admin of " + TOKEN + " is " + ADMIN + " with getMinDelay() = 0 | no delay.",
             "surface": "token_controls", "severity": "high", "chain_id": 31337, "address": TOKEN, "pin_or_tx": "P1",
             "evidence_ids": ["E1", "E2"], "evidence_type": "rpc_storage", "confidence": "proven",
             "alternatives": "A delay enforced elsewhere; selector scan found none (E2)", "coverage": "Slots read at P1 only",
             "stale_conditions": "AdminChanged or Upgraded after P1", "is_historical": False},
            {"finding_id": "F1", "proposition": "A successful sell executed at tx " + TX + ".", "surface": "sellability_exit_depth",
             "severity": "info", "chain_id": 31337, "address": TOKEN, "pin_or_tx": TX, "evidence_ids": ["E4"],
             "evidence_type": "receipt", "confidence": "proven", "alternatives": "none identified after checking the receipt status and logs",
             "coverage": "single receipt", "stale_conditions": "not applicable (historical)", "is_historical": True},
        ],
        "checks": [
            {"check_id": "A-UPGRADE", "surface": "token_controls", "name": "Upgrade authority", "status": "finding", "severity": "high",
             "evidence_ids": ["E1", "E2"], "finding_ids": ["F2"], "reason": None, "pin_id": "P1"},
            {"check_id": "C-HIST-SELL", "surface": "sellability_exit_depth", "name": "Historical sell", "status": "pass", "severity": None,
             "evidence_ids": ["E4"], "finding_ids": ["F1"], "reason": None, "pin_id": None},
        ],
    }


def write_tmp(manifest: dict) -> tuple[tempfile.TemporaryDirectory, str]:
    d = tempfile.TemporaryDirectory()
    p = os.path.join(d.name, "manifest.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(manifest, f)
    return d, p


def run_cli(argv: list[str]):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = ledger.main(argv)
    return code, out.getvalue(), err.getvalue()


class RenderTests(unittest.TestCase):
    def test_columns_and_rows_md(self):
        rows = ledger.ledger_rows(small_manifest())
        self.assertEqual([r["finding_id"] for r in rows], ["F1", "F2"])  # sorted by number, not manifest order
        self.assertEqual(list(rows[0].keys()), ledger.COLUMNS)
        self.assertEqual(len(ledger.COLUMNS), 12)
        md = ledger.render_md(rows)
        lines = md.splitlines()
        self.assertEqual(lines[0], "| " + " | ".join(ledger.COLUMNS) + " |")
        self.assertEqual(lines[1].count("---"), 12)
        self.assertEqual(len(lines), 4)
        f2 = lines[3]
        self.assertIn("E1 evidence/E1.json", f2)
        self.assertIn("E2 inline [eth_call getMinDelay() at 0x1312d00]", f2)
        self.assertIn('"method":"eth_getStorageAt"', f2)
        self.assertIn("EIP-1967 admin slot; low 20 bytes as address; uint256 return word", f2)
        self.assertIn("| P1 |", f2)
        self.assertIn("| rpc_storage |", f2)
        self.assertIn("| proven |", f2)
        # pipes inside cells are escaped so the table keeps 12 columns
        self.assertIn("getMinDelay() = 0 \\| no delay", f2)
        self.assertEqual(f2.count(" | "), 11)

    def test_pin_or_tx_from_finding(self):
        rows = ledger.ledger_rows(small_manifest())
        self.assertEqual(rows[0]["pin_or_tx"], TX)
        self.assertEqual(rows[1]["pin_or_tx"], "P1")
        self.assertEqual(rows[0]["chain_id"], "31337")

    def test_csv_output(self):
        rows = ledger.ledger_rows(small_manifest())
        text = ledger.render_csv(rows)
        parsed = list(csv.DictReader(io.StringIO(text)))
        self.assertEqual(len(parsed), 2)
        self.assertEqual(list(parsed[0].keys()), ledger.COLUMNS)
        self.assertEqual(parsed[1]["finding_id"], "F2")
        self.assertIn("E1 evidence/E1.json", parsed[1]["artifact_or_query"])

    def test_dangling_evidence_rendered_visibly(self):
        m = small_manifest()
        m["findings"][0]["evidence_ids"].append("E99")
        rows = ledger.ledger_rows(m)
        self.assertIn("E99 (DANGLING)", rows[1]["artifact_or_query"])

    def test_null_address_and_empty_manifest(self):
        m = small_manifest()
        m["findings"][1]["address"] = None
        rows = ledger.ledger_rows(m)
        self.assertEqual(rows[0]["address"], "null")
        self.assertIn("(no findings in manifest)", ledger.render_md([]))

    def test_render_cli(self):
        d, p = write_tmp(small_manifest())
        with d:
            code, out, _ = run_cli(["render", "--manifest", p])
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith("| finding_id | proposition |"))
            code, out, _ = run_cli(["render", "--manifest", p, "--format", "csv"])
            self.assertEqual(code, 0)
            self.assertTrue(out.startswith("finding_id,proposition,chain_id,"))


class CheckTests(unittest.TestCase):
    def codes(self, res):
        return sorted({e["code"] for e in res["errors"]}), sorted({w["code"] for w in res["warnings"]})

    def test_clean_manifest_passes(self):
        res = ledger.check_manifest(small_manifest())
        self.assertEqual(res["result"], "PASS")
        self.assertEqual(res["exit_code"], 0)
        self.assertEqual(res["errors"], [])
        self.assertEqual(self.codes(res)[1], [])  # E3 is referenced via target.metadata.symbol.evidence_id

    def test_finding_without_evidence_fails(self):
        m = small_manifest()
        m["findings"][0]["evidence_ids"] = []
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("E-FINDING-NO-EVIDENCE", self.codes(res)[0])

    def test_unknown_confidence_may_lack_evidence(self):
        m = small_manifest()
        m["findings"][0]["evidence_ids"] = []
        m["findings"][0]["confidence"] = "unknown"
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 0)
        self.assertNotIn("E-FINDING-NO-EVIDENCE", self.codes(res)[0])

    def test_dangling_evidence_id_fails(self):
        m = small_manifest()
        m["findings"][1]["evidence_ids"] = ["E4", "E42"]
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("E-EVIDENCE-DANGLING", self.codes(res)[0])
        self.assertTrue(any("E42" in e["message"] for e in res["errors"]))

    def test_dangling_ids_in_checks_fail(self):
        m = small_manifest()
        m["checks"][0]["evidence_ids"] = ["E7"]
        m["checks"][0]["finding_ids"] = ["F9"]
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 1)
        msgs = " ".join(e["message"] for e in res["errors"])
        self.assertIn("E7", msgs)
        self.assertIn("F9", msgs)

    def test_duplicate_finding_id_fails(self):
        m = small_manifest()
        dup = copy.deepcopy(m["findings"][0])
        m["findings"].append(dup)
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 1)
        self.assertIn("E-SCHEMA", self.codes(res)[0])
        self.assertTrue(any("duplicate finding_id F2" in e["message"] for e in res["errors"]))

    def test_duplicate_evidence_id_fails(self):
        m = small_manifest()
        m["evidence"].append(copy.deepcopy(m["evidence"][0]))
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 1)
        self.assertTrue(any("duplicate evidence_id E1" in e["message"] for e in res["errors"]))

    def test_unreferenced_evidence_is_warning_only(self):
        m = small_manifest()
        m["evidence"].append({"evidence_id": "E5", "chain_id": 31337, "address": None, "pin_id": "P1", "tx_hash": None,
                              "block_number": None, "evidence_type": "manual_note", "artifact": "inline", "query": "n/a",
                              "decoding_basis": "raw", "summary": "unused"})
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("W-EVIDENCE-UNREFERENCED", self.codes(res)[1])
        self.assertTrue(any(w["where"] == "E5" for w in res["warnings"]))

    def test_empty_free_text_columns_warn(self):
        m = small_manifest()
        m["findings"][0]["alternatives"] = ""
        res = ledger.check_manifest(m)
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("W-LEDGER-FIELD-EMPTY", self.codes(res)[1])

    def test_check_cli_exit_codes_and_json(self):
        d, p = write_tmp(small_manifest())
        with d:
            code, out, _ = run_cli(["check", "--manifest", p])
            self.assertEqual(code, 0)
            self.assertIn("ledger check: PASS", out)
            self.assertIn("linkage only", out)
        m = small_manifest()
        m["findings"][0]["evidence_ids"] = []
        d, p = write_tmp(m)
        with d:
            code, out, _ = run_cli(["check", "--manifest", p, "--json"])
            self.assertEqual(code, 1)
            data = json.loads(out)
            self.assertEqual(data["result"], "FAIL")
            self.assertIn("E-FINDING-NO-EVIDENCE", {e["code"] for e in data["errors"]})

    def test_unreadable_manifest_exits_2(self):
        code, _, err = run_cli(["check", "--manifest", "/nonexistent/manifest.json"])
        self.assertEqual(code, 2)
        self.assertIn("cannot read manifest", err)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("[]")
            code, _, err = run_cli(["render", "--manifest", p])
            self.assertEqual(code, 2)
            self.assertIn("must be a JSON object", err)
        code, _, _ = run_cli([])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
