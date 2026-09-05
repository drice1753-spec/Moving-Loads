"""Tests for scripts/reconcile.py (stdlib unittest; synthetic flows written to temp files; no network)."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import reconcile as rc  # noqa: E402

TX1 = "0x" + "a1" * 32
TX2 = "0x" + "b2" * 32
TX3 = "0x" + "c3" * 32
TX4 = "0x" + "d4" * 32
TX5 = "0x" + "e5" * 32
CP = "0x2222222222222222222222222222222222222222"
TOKEN = "0x3333333333333333333333333333333333333333"


def base_flows(is_native=False) -> dict:
    """opening 1000; in 500 (TX1); out 200 (TX2); closing 1300 -> balanced."""
    return {
        "asset": {"symbol": "SYN", "address": None if is_native else TOKEN, "chain_id": 31337, "is_native": is_native, "decimals": 18},
        "opening": {"balance": "1000", "block": 100},
        "closing": {"balance": "1300", "block": 200},
        "rows": [
            {"tx": TX1, "status": "success", "direction": "in", "amount": "500", "block": 150, "counterparty": CP, "note": ""},
            {"tx": TX2, "status": "success", "direction": "out", "amount": "200", "block": 160, "counterparty": CP, "note": ""},
        ],
    }


def run_cli(flows: dict, extra: list[str] | None = None):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "flows.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(flows, f)
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = rc.main(["--flows", p] + (extra or []))
        return code, out.getvalue(), err.getvalue()


class BalancedTests(unittest.TestCase):
    def test_balanced_exit_0(self):
        code, out, _ = run_cli(base_flows())
        self.assertEqual(code, 0)
        self.assertIn("identity: opening + inflows + adjustments = outflows + closing + unexplained", out)
        self.assertIn("1000 + 500 + 0 = 200 + 1300 + (0)", out)
        self.assertIn("WITHIN tolerance", out)

    def test_json_output_fields(self):
        code, out, _ = run_cli(base_flows(), ["--json"])
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual(data["computed_closing"], "1300")
        self.assertEqual(data["unexplained_delta"], "0")
        self.assertTrue(data["within_tolerance"])
        self.assertEqual(data["identity"]["lhs"], data["identity"]["rhs"])
        self.assertEqual(data["sums"]["in"], "500")
        self.assertEqual(data["sums"]["out"], "200")
        self.assertEqual(data["excluded_reverted"], [])
        self.assertEqual(data["unpaired_transforms"], [])
        self.assertEqual(data["duplicates"], [])

    def test_identity_balances_when_unexplained(self):
        flows = base_flows()
        flows["closing"]["balance"] = "1350"  # holds 50 more than explained
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["unexplained_delta"], "50")
        self.assertEqual(res["identity"]["unexplained_term"], "-50")
        self.assertEqual(res["identity"]["lhs"], res["identity"]["rhs"])
        self.assertEqual(res["exit_code"], 1)


class RevertedRowTests(unittest.TestCase):
    def test_reverted_row_excluded_from_sums_but_listed(self):
        flows = base_flows()
        flows["rows"].append({"tx": TX3, "status": "reverted", "direction": "out", "amount": "999999", "block": 170,
                              "counterparty": CP, "note": "reverted transfer"})
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["computed_closing"], "1300")
        self.assertEqual(res["unexplained_delta"], "0")
        self.assertEqual(len(res["excluded_reverted"]), 1)
        self.assertEqual(res["excluded_reverted"][0]["tx"], TX3)
        self.assertEqual(res["row_counts"]["reverted_excluded"], 1)
        code, out, _ = run_cli(flows)
        self.assertEqual(code, 0)
        self.assertIn("excluded reverted rows", out)
        self.assertIn(TX3, out)


class TransformTests(unittest.TestCase):
    def test_paired_transform_applies_both_legs_without_warning(self):
        flows = base_flows()
        flows["rows"] += [
            {"tx": TX4, "status": "success", "direction": "transform_out", "amount": "100", "block": 180, "counterparty": None, "note": "wrap"},
            {"tx": TX4, "status": "success", "direction": "transform_in", "amount": "100", "block": 180, "counterparty": None, "note": "wrap"},
        ]
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["computed_closing"], "1300")
        self.assertEqual(res["unpaired_transforms"], [])
        self.assertFalse(any("unpaired transform" in w for w in res["warnings"]))

    def test_unpaired_transform_is_applied_and_warned(self):
        flows = base_flows()
        flows["rows"].append({"tx": TX4, "status": "success", "direction": "transform_out", "amount": "100", "block": 180,
                              "counterparty": None, "note": "bridge leg out"})
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["computed_closing"], "1200")       # still applied so the delta is visible
        self.assertEqual(res["unexplained_delta"], "100")
        self.assertEqual(len(res["unpaired_transforms"]), 1)
        self.assertEqual(res["unpaired_transforms"][0]["tx"], TX4)
        self.assertTrue(any("unpaired transform" in w for w in res["warnings"]))
        self.assertEqual(res["exit_code"], 1)
        flows["closing"]["balance"] = "1200"
        code, out, _ = run_cli(flows)
        self.assertEqual(code, 0)
        self.assertIn("unpaired transforms", out)

    def test_two_ins_same_tx_are_not_a_pair(self):
        flows = base_flows()
        flows["rows"] += [
            {"tx": TX4, "status": "success", "direction": "transform_in", "amount": "10", "block": 180, "counterparty": None, "note": ""},
            {"tx": TX4, "status": "success", "direction": "transform_in", "amount": "10", "block": 180, "counterparty": None, "note": ""},
        ]
        res = rc.reconcile(flows, 0)
        self.assertEqual(len(res["unpaired_transforms"]), 2)
        self.assertEqual(len(res["duplicates"]), 1)


class ToleranceTests(unittest.TestCase):
    def test_exactly_tolerance_passes_plus_one_fails(self):
        flows = base_flows()
        flows["closing"]["balance"] = "1307"   # unexplained +7
        code, _, _ = run_cli(flows, ["--tolerance", "7"])
        self.assertEqual(code, 0)
        code, _, _ = run_cli(flows, ["--tolerance", "6"])
        self.assertEqual(code, 1)
        flows["closing"]["balance"] = "1293"   # unexplained -7 (abs)
        code, _, _ = run_cli(flows, ["--tolerance", "7"])
        self.assertEqual(code, 0)
        code, _, _ = run_cli(flows, ["--tolerance", "6"])
        self.assertEqual(code, 1)

    def test_default_tolerance_zero(self):
        flows = base_flows()
        flows["closing"]["balance"] = "1301"
        code, out, _ = run_cli(flows)
        self.assertEqual(code, 1)
        self.assertIn("EXCEEDS tolerance", out)

    def test_negative_tolerance_rejected(self):
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(base_flows(), -1)


class GasTests(unittest.TestCase):
    def test_gas_on_non_native_asset_errors(self):
        flows = base_flows(is_native=False)
        flows["rows"].append({"tx": TX5, "status": "success", "direction": "gas", "amount": "21000", "block": 190, "counterparty": None, "note": ""})
        with self.assertRaises(rc.FlowsError) as ctx:
            rc.reconcile(flows, 0)
        self.assertIn("gas cannot be paid in a non-native asset", str(ctx.exception))
        code, out, err = run_cli(flows)
        self.assertEqual(code, 2)
        self.assertIn("gas cannot be paid", err)

    def test_gas_on_native_asset_subtracts(self):
        flows = base_flows(is_native=True)
        flows["rows"].append({"tx": TX5, "status": "success", "direction": "gas", "amount": "21000", "block": 190, "counterparty": None, "note": ""})
        flows["closing"]["balance"] = str(1300 - 21000)  # would be negative; use bigger opening instead
        flows["opening"]["balance"] = "100000"
        flows["closing"]["balance"] = str(100000 + 500 - 200 - 21000)
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["unexplained_delta"], "0")
        self.assertEqual(res["sums"]["gas"], "21000")
        self.assertEqual(res["outflows"], str(200 + 21000))


class AdjustmentTests(unittest.TestCase):
    def test_signed_adjustment_with_note(self):
        flows = base_flows()
        flows["rows"].append({"tx": None, "status": "success", "direction": "adjustment", "amount": "-30", "block": 195,
                              "counterparty": None, "note": "fee-on-transfer haircut observed in receipt logs"})
        flows["closing"]["balance"] = "1270"
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["unexplained_delta"], "0")
        self.assertEqual(res["adjustments"], "-30")

    def test_adjustment_without_note_errors(self):
        flows = base_flows()
        flows["rows"].append({"tx": None, "status": "success", "direction": "adjustment", "amount": "5", "block": 195,
                              "counterparty": None, "note": "  "})
        with self.assertRaises(rc.FlowsError) as ctx:
            rc.reconcile(flows, 0)
        self.assertIn("non-empty note", str(ctx.exception))


class DuplicateTests(unittest.TestCase):
    def test_duplicate_rows_warn(self):
        flows = base_flows()
        flows["rows"].append(copy.deepcopy(flows["rows"][0]))  # same (tx, direction, amount)
        flows["closing"]["balance"] = "1800"
        res = rc.reconcile(flows, 0)
        self.assertEqual(len(res["duplicates"]), 1)
        self.assertEqual(res["duplicates"][0]["tx"], TX1)
        self.assertTrue(any("possible double count" in w for w in res["warnings"]))
        code, out, _ = run_cli(flows)
        self.assertEqual(code, 0)  # duplicates warn; they do not fail the identity by themselves
        self.assertIn("possible double count", out)

    def test_reverted_duplicate_does_not_count(self):
        flows = base_flows()
        dup = copy.deepcopy(flows["rows"][0])
        dup["status"] = "reverted"
        flows["rows"].append(dup)
        res = rc.reconcile(flows, 0)
        self.assertEqual(res["duplicates"], [])


class ValidationTests(unittest.TestCase):
    def test_float_amount_rejected(self):
        flows = base_flows()
        flows["rows"][0]["amount"] = "500.5"
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(flows, 0)
        flows["rows"][0]["amount"] = 500.0
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(flows, 0)

    def test_negative_non_adjustment_rejected(self):
        flows = base_flows()
        flows["rows"][0]["amount"] = "-500"
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(flows, 0)

    def test_bad_status_direction_and_missing_tx(self):
        flows = base_flows()
        flows["rows"][0]["status"] = "pending"
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(flows, 0)
        flows = base_flows()
        flows["rows"][0]["direction"] = "sideways"
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(flows, 0)
        flows = base_flows()
        flows["rows"][0]["tx"] = ""
        with self.assertRaises(rc.FlowsError):
            rc.reconcile(flows, 0)

    def test_window_warnings(self):
        flows = base_flows()
        flows["rows"][0]["block"] = 50   # before opening block
        flows["rows"][1]["block"] = 250  # after closing block
        res = rc.reconcile(flows, 0)
        self.assertTrue(any("at or before the opening block" in w for w in res["warnings"]))
        self.assertTrue(any("after the closing block" in w for w in res["warnings"]))

    def test_malformed_file_exits_2(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "flows.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{not json")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                code = rc.main(["--flows", p])
            self.assertEqual(code, 2)
            self.assertIn("not valid JSON", err.getvalue())
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = rc.main(["--flows", "/nonexistent/flows.json"])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
