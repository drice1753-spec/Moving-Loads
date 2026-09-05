"""Tests for scripts/validate_report.py (stdlib unittest; pytest also discovers these).

(a) every fixture directory through validate() against expected.json
(b) the CLI via subprocess (exit codes, closing note, --json shape)
(c) in-memory mutations covering every E-code at least once, the W-codes, and --strict promotion
(d) the valid fixture emits no W-REPORT-UNSCOPED-ADDRESS (its report mentions scoped addresses only)
"""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPTS = os.path.join(ROOT, "scripts")
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, SCRIPTS)

import ddcore  # noqa: E402
import validate_report as vr  # noqa: E402

VALID_DIR = os.path.join(FIXTURES, "valid")
ALL_E_CODES = [
    "E-SCHEMA", "E-ADDR-MALFORMED", "E-ADDR-CHECKSUM", "E-CHAIN-MISMATCH", "E-CHAIN-UNPINNED", "E-PIN-PLACEHOLDER",
    "E-PIN-HEADER-MISMATCH", "E-PIN-TIME", "E-PIN-PRIMARY", "E-META-STATUS", "E-META-REPORT", "E-SCOPE-TARGET",
    "E-SCOPE-RUNTIME", "E-REPORT-MISSING", "E-REPORT-HASH", "E-REPORT-IDENTITY", "E-REPORT-TARGET-ABSENT",
    "E-REPORT-SECTIONS", "E-CHECK-CORE-MISSING", "E-CHECK-STATUS", "E-EVIDENCE-DANGLING", "E-FINDING-NO-EVIDENCE",
    "E-FINDING-CHAIN", "E-RATING-MISSING", "E-RATING-UNKNOWN-AS-LOW", "E-RATING-CRITICAL-AVERAGED", "E-DECL-SIGNING",
    "E-DECL-FORK", "E-MODE-MISMATCH",
]
COVERED_E_CODES: set = set()


def load_valid():
    with open(os.path.join(VALID_DIR, "manifest.json"), "r", encoding="utf-8") as f:
        m = json.load(f)
    with open(os.path.join(VALID_DIR, "report.md"), "r", encoding="utf-8") as f:
        r = f.read()
    return m, r


def codes(res, kind="errors"):
    return [x["code"] for x in res[kind]]


def run(m, r, strict=False, rehash=True, report_missing=False):
    if rehash and r is not None:
        m["report"]["sha256"] = hashlib.sha256(r.encode("utf-8")).hexdigest()
    return vr.validate_objects(m, None if report_missing else r, VALID_DIR, strict=strict, report_path="report.md")


def check_by_id(m, cid):
    return next(c for c in m["checks"] if c["check_id"] == cid)


class FixtureTests(unittest.TestCase):
    def test_fixture_dirs_exist(self):
        names = sorted(d for d in os.listdir(FIXTURES) if os.path.isdir(os.path.join(FIXTURES, d)))
        for want in ("valid", "reject-same-symbol-other-chain", "reject-wrong-target-report", "reject-fake-pin",
                     "reject-unknown-as-pass", "reject-malformed-address", "reject-missing-evidence",
                     "reject-scope-chain-without-pin", "reject-simulation-without-fork-guard"):
            self.assertIn(want, names)

    def test_every_fixture_matches_expected(self):
        for name in sorted(os.listdir(FIXTURES)):
            d = os.path.join(FIXTURES, name)
            exp_path = os.path.join(d, "expected.json")
            if not os.path.isfile(exp_path):
                continue
            with self.subTest(fixture=name):
                with open(exp_path, "r", encoding="utf-8") as f:
                    exp = json.load(f)
                res = vr.validate(os.path.join(d, "manifest.json"))
                got = codes(res)
                self.assertEqual(res["result"], "PASS" if exp["expect"] == "pass" else "FAIL", got)
                for c in exp["expected_codes"]:
                    self.assertIn(c, got, f"{name}: expected {c} in {got}")
                for c in exp["forbidden_codes"]:
                    self.assertNotIn(c, got, f"{name}: forbidden {c} in {got}")

    def test_valid_has_no_warnings_and_no_unscoped_addresses(self):
        res = vr.validate(os.path.join(VALID_DIR, "manifest.json"))
        self.assertEqual(res["result"], "PASS")
        self.assertNotIn("W-REPORT-UNSCOPED-ADDRESS", codes(res, "warnings"))
        self.assertEqual(res["warnings"], [], res["warnings"])
        self.assertEqual(res["note"], vr.CLOSING_NOTE)

    def test_valid_report_content(self):
        m, r = load_valid()
        self.assertIn("SYNTHETIC FIXTURE - not a live finding", r)
        body = r.split("---", 2)[2]
        for sec in vr.REQUIRED_SECTIONS:
            self.assertIn(f"## {sec}", body)
        header = "| " + " | ".join(["finding_id", "proposition", "chain_id", "address", "pin_or_tx", "artifact_or_query",
                                    "decoding_basis", "evidence_type", "confidence", "alternatives", "coverage",
                                    "stale_conditions"]) + " |"
        self.assertIn(header, body)
        for key in vr.SURFACES:
            self.assertIn(f"| {key} |", body)
        self.assertEqual(len(m["ratings"]), 11)
        self.assertEqual(set(vr.CORE_CHECKS) - {c["check_id"] for c in m["checks"]}, set())
        self.assertEqual(m["mode"], "broad")
        self.assertEqual(check_by_id(m, "C-HIST-SELL")["status"], "unknown")
        self.assertEqual(m["ratings"]["sellability_exit_depth"]["rating"], "unknown")
        self.assertEqual(check_by_id(m, "A-MINT")["severity"], "high")
        self.assertEqual(m["ratings"]["token_controls"]["rating"], "high")

    def test_templates_example_passes(self):
        tm = os.path.join(ROOT, "templates", "manifest.example.json")
        tr = os.path.join(ROOT, "templates", "report.example.md")
        if not (os.path.isfile(tm) and os.path.isfile(tr)):
            self.skipTest("templates not generated")
        res = vr.validate(tm, tr)
        self.assertEqual(res["result"], "PASS", codes(res))
        with open(tm, "rb") as a, open(os.path.join(VALID_DIR, "manifest.json"), "rb") as b:
            self.assertEqual(a.read(), b.read())
        with open(tr, "rb") as a, open(os.path.join(VALID_DIR, "report.md"), "rb") as b:
            self.assertEqual(a.read(), b.read())

    def test_generator_is_deterministic(self):
        gen = os.path.join(FIXTURES, "make_fixtures.py")
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run([sys.executable, gen, "--out", tmp, "--no-templates"], check=True, capture_output=True)
            for name in os.listdir(tmp):
                for fn in ("manifest.json", "report.md", "expected.json"):
                    with open(os.path.join(tmp, name, fn), "rb") as a, open(os.path.join(FIXTURES, name, fn), "rb") as b:
                        self.assertEqual(a.read(), b.read(), f"{name}/{fn} differs from committed fixture")


class CliTests(unittest.TestCase):
    SCRIPT = os.path.join(SCRIPTS, "validate_report.py")

    def test_valid_exit_0_and_note(self):
        p = subprocess.run([sys.executable, self.SCRIPT, "--manifest", os.path.join(VALID_DIR, "manifest.json")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("RESULT: PASS", p.stdout)
        self.assertIn(vr.CLOSING_NOTE, p.stdout)

    def test_reject_exit_1_and_note(self):
        p = subprocess.run([sys.executable, self.SCRIPT, "--manifest",
                            os.path.join(FIXTURES, "reject-fake-pin", "manifest.json")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("ERROR E-PIN-PLACEHOLDER", p.stdout)
        self.assertIn("RESULT: FAIL", p.stdout)
        self.assertIn(vr.CLOSING_NOTE, p.stdout)

    def test_json_output(self):
        p = subprocess.run([sys.executable, self.SCRIPT, "--manifest",
                            os.path.join(FIXTURES, "reject-missing-evidence", "manifest.json"), "--json"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        obj = json.loads(p.stdout)
        for key in ("result", "errors", "warnings", "note", "counts", "schema_lib"):
            self.assertIn(key, obj)
        self.assertEqual(obj["result"], "FAIL")
        self.assertEqual(obj["counts"]["errors"], len(obj["errors"]))
        self.assertTrue(all(set(e) == {"code", "message", "path"} for e in obj["errors"]))

    def test_usage_exit_2(self):
        p = subprocess.run([sys.executable, self.SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)

    def test_explicit_report_override(self):
        p = subprocess.run([sys.executable, self.SCRIPT, "--manifest", os.path.join(VALID_DIR, "manifest.json"),
                            "--report", os.path.join(FIXTURES, "reject-wrong-target-report", "report.md")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("E-REPORT-HASH", p.stdout)


class MutationTests(unittest.TestCase):
    """Every E-code at least once via validate_objects on the valid manifest."""

    def setUp(self):
        self.m, self.r = load_valid()

    def expect(self, code, res, present=True):
        got = codes(res)
        if present:
            COVERED_E_CODES.add(code)
            self.assertIn(code, got, got)
        else:
            self.assertNotIn(code, got, got)

    def test_baseline_passes(self):
        res = run(self.m, self.r)
        self.assertEqual(res["result"], "PASS", codes(res))

    # --- structure ---
    def test_schema_missing_key_and_bad_enum(self):
        m = copy.deepcopy(self.m); del m["pins"]
        self.expect("E-SCHEMA", run(m, self.r))
        m = copy.deepcopy(self.m); m["mode"] = "casual"
        self.expect("E-SCHEMA", run(m, self.r))
        m = copy.deepcopy(self.m); m["extra_key"] = 1
        self.expect("E-SCHEMA", run(m, self.r))
        m = copy.deepcopy(self.m); m["verdict"]["answer"] = "  "
        self.expect("E-SCHEMA", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"].append(copy.deepcopy(m["pins"][0]))
        self.expect("E-SCHEMA", run(m, self.r))  # duplicate pin_id

    def test_never_crashes_on_garbage(self):
        res = vr.validate_objects("not a dict", None, VALID_DIR)
        self.assertEqual(res["result"], "FAIL"); self.assertIn("E-SCHEMA", codes(res))
        res = vr.validate_objects({"manifest_version": 1, "pins": "x", "target": [], "checks": {}, "ratings": 5}, "---\n", VALID_DIR)
        self.assertEqual(res["result"], "FAIL")
        res = vr.validate_objects({}, None, VALID_DIR)
        self.assertEqual(res["result"], "FAIL")

    # --- identity / addresses ---
    def test_addr_malformed(self):
        m = copy.deepcopy(self.m); m["scope_addresses"][3]["address"] = "0x1234"
        self.expect("E-ADDR-MALFORMED", run(m, self.r))
        m = copy.deepcopy(self.m); m["evidence"][0]["address"] = "0x" + "zz" * 20
        self.expect("E-ADDR-MALFORMED", run(m, self.r))

    def test_addr_checksum(self):
        m = copy.deepcopy(self.m)
        a = m["target"]["requested"]["address"]
        m["target"]["address_checksum"] = a.lower()
        self.expect("E-ADDR-CHECKSUM", run(m, self.r))
        m = copy.deepcopy(self.m)
        bad = m["findings"][1]["address"]
        idx = next(i for i, ch in enumerate(bad) if i > 1 and ch.isalpha())
        m["findings"][1]["address"] = bad[:idx] + bad[idx].swapcase() + bad[idx + 1:]
        self.expect("E-ADDR-CHECKSUM", run(m, self.r))

    def test_chain_mismatch(self):
        m = copy.deepcopy(self.m); m["target"]["observed"]["rpc_chain_id_hex"] = "0x1"
        self.expect("E-CHAIN-MISMATCH", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["observed"]["chain_id"] = 1; m["target"]["observed"]["rpc_chain_id_hex"] = "0x1"
        self.expect("E-CHAIN-MISMATCH", run(m, self.r))

    def test_scope_target(self):
        m = copy.deepcopy(self.m); m["scope_addresses"][0]["role"] = "other"
        self.expect("E-SCOPE-TARGET", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][0]["provenance"] = "inferred"
        self.expect("E-SCOPE-TARGET", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][0]["code_hash"] = ddcore.keccak256_hex(b"different runtime")
        self.expect("E-SCOPE-TARGET", run(m, self.r))
        m = copy.deepcopy(self.m); del m["scope_addresses"][0]
        res = run(m, self.r); self.expect("E-SCOPE-TARGET", res)

    # --- pins ---
    def test_pin_placeholder(self):
        m = copy.deepcopy(self.m); m["pins"][0]["block_hash"] = "0x" + "ab" * 32
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][1]["block_number"] = 0; m["pins"][1]["captured_header"]["number"] = "0x0"
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))

    def test_pin_header_mismatch(self):
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["hash"] = ddcore.keccak256_hex(b"other block")
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["parentHash"] = m["pins"][0]["captured_header"]["hash"]
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["number"] = "0x1"
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r))

    def test_pin_time(self):
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["timestamp"] = hex(m["pins"][0]["timestamp_unix"] + 1)
        self.expect("E-PIN-TIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["timestamp_utc"] = "2020-01-01T00:00:00Z"
        self.expect("E-PIN-TIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_at_utc"] = "2024-12-01T00:00:00Z"  # before the block existed
        self.expect("E-PIN-TIME", run(m, self.r))
        m = copy.deepcopy(self.m); p = m["pins"][0]; p["timestamp_unix"] = 100; p["timestamp_utc"] = ddcore.iso_utc(100); p["captured_header"]["timestamp"] = "0x64"
        self.expect("E-PIN-TIME", run(m, self.r))

    def test_pin_primary(self):
        m = copy.deepcopy(self.m); m["primary_pin_id"] = "P9"
        res = run(m, self.r); self.expect("E-PIN-PRIMARY", res)
        m = copy.deepcopy(self.m)
        m["pins"].append(dict(m["pins"][0], pin_id="P3", chain_id=10))
        m["primary_pin_id"] = "P3"
        r = self.r.replace("primary_pin_id: P1", "primary_pin_id: P3")
        self.expect("E-PIN-PRIMARY", run(m, r))

    def test_pin_stale_warning(self):
        m = copy.deepcopy(self.m); m["pins"][0]["captured_at_utc"] = "2025-03-01T00:00:00Z"
        self.assertIn("W-PIN-STALE", codes(run(m, self.r), "warnings"))

    def test_pin_unused_warning(self):
        m = copy.deepcopy(self.m)
        m["pins"].append(dict(copy.deepcopy(m["pins"][0]), pin_id="P3", chain_id=10))
        self.assertIn("W-PIN-UNUSED", codes(run(m, self.r), "warnings"))

    # --- chains ---
    def test_chain_unpinned(self):
        m = copy.deepcopy(self.m); m["evidence"][5]["chain_id"] = 10; m["evidence"][5]["pin_id"] = None
        self.expect("E-CHAIN-UNPINNED", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][2]["pin_id"] = "P7"
        self.expect("E-CHAIN-UNPINNED", run(m, self.r))

    def test_finding_chain(self):
        m = copy.deepcopy(self.m)
        e = next(e for e in m["evidence"] if e["evidence_id"] == "E11"); e["chain_id"] = 10; e["pin_id"] = None
        res = run(m, self.r); self.expect("E-FINDING-CHAIN", res)
        m = copy.deepcopy(self.m)
        m["pins"].append(dict(copy.deepcopy(m["pins"][0]), pin_id="P3", chain_id=10))
        e = next(e for e in m["evidence"] if e["evidence_id"] == "E11"); e["chain_id"] = 10; e["pin_id"] = "P3"
        res = run(m, self.r)
        self.assertNotIn("E-FINDING-CHAIN", codes(res)); self.assertIn("W-FINDING-CROSS-CHAIN", codes(res, "warnings"))

    # --- metadata ---
    def test_meta_status(self):
        m = copy.deepcopy(self.m); m["target"]["metadata"]["name"]["value"] = None
        self.expect("E-META-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["metadata"]["name"].update({"status": "unresolved", "reason": None})
        self.expect("E-META-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["metadata"]["decimals"]["value"] = 300
        self.expect("E-META-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["metadata"]["total_supply"]["value"] = "12.5"
        self.expect("E-META-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["metadata"]["symbol"].update({"status": "nonstandard", "value": None})
        self.expect("E-META-STATUS", run(m, self.r))

    def test_meta_report(self):
        m = copy.deepcopy(self.m)
        m["target"]["metadata"]["symbol"].update({"status": "unresolved", "value": None, "reason": "symbol() reverted at P1"})
        self.expect("E-META-REPORT", run(m, self.r))
        r = self.r.replace("target_symbol: SYNTH", "target_symbol: unresolved")
        res = run(copy.deepcopy(m), r); self.assertNotIn("E-META-REPORT", codes(res))
        m2 = copy.deepcopy(self.m)
        m2["target"]["metadata"]["symbol"].update({"status": "nonstandard", "value": "SYNTH"})
        self.expect("E-META-REPORT", run(m2, self.r))
        res = run(copy.deepcopy(m2), self.r.replace("target_symbol: SYNTH", "target_symbol: nonstandard:SYNTH"))
        self.assertNotIn("E-META-REPORT", codes(res))

    # --- scope ---
    def test_scope_runtime(self):
        m = copy.deepcopy(self.m); m["scope_addresses"][2]["code_hash"] = ddcore.EMPTY_CODE_HASH
        self.expect("E-SCOPE-RUNTIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][1]["code_hash"] = ddcore.keccak256_hex(b"code")
        self.expect("E-SCOPE-RUNTIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][2].update({"runtime_status": "unknown", "code_hash": None, "limitation_id": None})
        self.expect("E-SCOPE-RUNTIME", run(m, self.r))
        m["scope_addresses"][2]["limitation_id"] = "L1"
        self.assertNotIn("E-SCOPE-RUNTIME", codes(run(m, self.r)))

    # --- report ---
    def test_report_missing(self):
        self.expect("E-REPORT-MISSING", run(copy.deepcopy(self.m), self.r, report_missing=True))

    def test_report_hash(self):
        self.expect("E-REPORT-HASH", run(copy.deepcopy(self.m), self.r + "\nedited\n", rehash=False))

    def test_report_identity(self):
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("evm_dd_report: 1", "evm_dd_report: 2")))
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("target_chain_id: 8453", "target_chain_id: 1")))
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("primary_pin_block: 20000000", "primary_pin_block: 20000001")))
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.split("---", 2)[2]))  # no frontmatter
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("---\nevm_dd", "---\nbad line without colon\nevm_dd", 1)))

    def test_report_target_absent(self):
        a = self.m["target"]["address_checksum"]
        head, body = self.r.split("\n---\n", 1)
        r = head + "\n---\n" + body.replace(a, "0x" + "0" * 40)
        res = run(copy.deepcopy(self.m), r)
        self.expect("E-REPORT-TARGET-ABSENT", res)
        self.assertIn("W-REPORT-UNSCOPED-ADDRESS", codes(res, "warnings"))

    def test_report_sections(self):
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(self.m), self.r.replace("## Recommendations", "## Advice")))
        m = copy.deepcopy(self.m); m["mode"] = "focused"
        r = self.r.replace("mode: broad", "mode: focused").replace("## Recommendations", "## Advice")
        self.assertNotIn("E-REPORT-SECTIONS", codes(run(m, r)))
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(m), r.replace("## Evidence ledger", "## Ledger")))

    def test_mode_mismatch(self):
        m = copy.deepcopy(self.m); m["mode"] = "formal"
        self.expect("E-MODE-MISMATCH", run(m, self.r))

    def test_verdict_language_warning_and_strict(self):
        r = self.r.replace("## Verdict\n", "## Verdict\n\nThis token is completely safe.\n")
        res = run(copy.deepcopy(self.m), r)
        self.assertIn("W-VERDICT-LANGUAGE", codes(res, "warnings")); self.assertEqual(res["result"], "PASS")
        res = run(copy.deepcopy(self.m), r, strict=True)
        self.assertIn("W-VERDICT-LANGUAGE", codes(res)); self.assertEqual(res["result"], "FAIL")
        r2 = self.r.replace("## Verdict\n", "## Verdict\n\nThe position is not safe from removal after unlock.\n")
        self.assertNotIn("W-VERDICT-LANGUAGE", codes(run(copy.deepcopy(self.m), r2), "warnings"))

    def test_unscoped_address_warning_and_strict(self):
        r = self.r.replace("## Recommendations\n", "## Recommendations\n\nAlso seen: 0x" + "1" * 40 + "\n")
        res = run(copy.deepcopy(self.m), r)
        self.assertIn("W-REPORT-UNSCOPED-ADDRESS", codes(res, "warnings"))
        self.assertEqual(run(copy.deepcopy(self.m), r, strict=True)["result"], "FAIL")

    # --- checks ---
    def test_check_core_missing(self):
        m = copy.deepcopy(self.m); m["checks"] = [c for c in m["checks"] if c["check_id"] != "A-TAX"]
        self.expect("E-CHECK-CORE-MISSING", run(m, self.r))
        m["mode"] = "focused"
        self.assertNotIn("E-CHECK-CORE-MISSING", codes(run(m, self.r.replace("mode: broad", "mode: focused"))))

    def test_check_status(self):
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["evidence_ids"] = []
        self.expect("E-CHECK-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); check_by_id(m, "C-HIST-SELL")["reason"] = ""
        self.expect("E-CHECK-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = None
        self.expect("E-CHECK-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["finding_ids"] = []
        self.expect("E-CHECK-STATUS", run(m, self.r))
        m = copy.deepcopy(self.m); c = check_by_id(m, "A-MINT"); c["status"] = "skipped"; c["reason"] = "skipped"
        self.expect("E-CHECK-STATUS", run(m, self.r))  # skipped with severity + proven finding
        m = copy.deepcopy(self.m); m["checks"].append(copy.deepcopy(m["checks"][1]))
        self.expect("E-CHECK-STATUS", run(m, self.r))  # duplicate
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["surface"] = "not_a_surface"
        self.expect("E-CHECK-STATUS", run(m, self.r))

    def test_evidence_dangling(self):
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["finding_ids"] = ["F77"]
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["evidence"][0]["pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["findings"][0]["pin_or_tx"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["findings"][0]["pin_or_tx"] = ddcore.keccak256_hex(b"some tx")
        self.assertNotIn("E-EVIDENCE-DANGLING", codes(run(m, self.r)))
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["basis_check_ids"].append("A-NOPE")
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["time_basis_pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["coverage"]["limitations"][0]["affected_check_ids"] = ["Z-NOPE"]
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][3]["limitation_id"] = "L9"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))

    # --- findings ---
    def test_finding_no_evidence(self):
        m = copy.deepcopy(self.m); m["findings"][1]["evidence_ids"] = []
        self.expect("E-FINDING-NO-EVIDENCE", run(m, self.r))
        m["findings"][1]["confidence"] = "unknown"
        self.assertNotIn("E-FINDING-NO-EVIDENCE", codes(run(m, self.r)))

    def test_discovery_only_warning_and_strict(self):
        m = copy.deepcopy(self.m); m["findings"][0]["evidence_ids"] = ["E14", "E15"]
        res = run(m, self.r)
        self.assertIn("W-EVIDENCE-DISCOVERY-ONLY", codes(res, "warnings"))
        self.assertEqual(run(copy.deepcopy(m), self.r, strict=True)["result"], "FAIL")

    # --- ratings ---
    def test_rating_missing(self):
        m = copy.deepcopy(self.m); del m["ratings"]["external_dependencies"]
        self.expect("E-RATING-MISSING", run(m, self.r))
        m = copy.deepcopy(self.m); del m["ratings"]
        self.expect("E-RATING-MISSING", run(m, self.r))
        m["mode"] = "focused"
        self.assertNotIn("E-RATING-MISSING", codes(run(m, self.r.replace("mode: broad", "mode: focused"))))

    def test_rating_unknown_as_low(self):
        m = copy.deepcopy(self.m); m["ratings"]["sellability_exit_depth"]["rating"] = "low"
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, self.r))
        m["ratings"]["sellability_exit_depth"].update({"coverage_qualified": True, "coverage_note": "quotes only; historical sell unknown (L1)"})
        self.assertNotIn("E-RATING-UNKNOWN-AS-LOW", codes(run(m, self.r)))
        m = copy.deepcopy(self.m); m["ratings"]["external_dependencies"]["basis_check_ids"] = []
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, self.r))

    def test_rating_critical_averaged(self):
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = "critical"; m["findings"][0]["severity"] = "critical"
        self.expect("E-RATING-CRITICAL-AVERAGED", run(m, self.r))
        m["ratings"]["token_controls"]["rating"] = "critical"
        self.assertNotIn("E-RATING-CRITICAL-AVERAGED", codes(run(m, self.r)))

    # --- declarations ---
    def test_decl_signing(self):
        for key in ("no_real_signing", "no_broadcast", "no_private_keys_requested"):
            m = copy.deepcopy(self.m); m["declarations"][key] = False
            self.expect("E-DECL-SIGNING", run(m, self.r))
            m["declarations"][key] = "true"
            self.expect("E-DECL-SIGNING", run(m, self.r))

    def test_decl_fork(self):
        m = copy.deepcopy(self.m); m["evidence"][0]["evidence_type"] = "simulation_counterfactual"; m["evidence"][0]["counterfactual"] = True
        self.expect("E-DECL-FORK", run(m, self.r))  # used=false but simulation evidence present
        m["declarations"]["simulation"].update({"used": True, "fork_verified_disposable": True, "synthetic_accounts_only": True,
                                                "results_labeled_counterfactual": True, "fork_chain_id": 8453, "fork_block": 20000000})
        self.assertNotIn("E-DECL-FORK", codes(run(m, self.r)))
        m["evidence"][0]["counterfactual"] = False
        self.expect("E-DECL-FORK", run(m, self.r))
        m["evidence"][0]["counterfactual"] = True; m["declarations"]["simulation"]["fork_block"] = None
        self.expect("E-DECL-FORK", run(m, self.r))
        m["declarations"]["simulation"]["fork_block"] = 20000000; m["declarations"]["simulation"]["fork_attestation_path"] = "no-such-attestation.json"
        res = run(m, self.r)
        self.assertNotIn("E-DECL-FORK", codes(res)); self.assertIn("W-DECL-FORK-ATTESTATION", codes(res, "warnings"))

    # --- coverage ---
    def test_limitation_unreferenced_warning(self):
        m = copy.deepcopy(self.m)
        m["coverage"]["limitations"].append({"limitation_id": "L2", "kind": "api_unavailable", "description": "explorer API down",
                                             "affected_check_ids": [], "affected_addresses": [], "retry_attempts": 1})
        self.assertIn("W-LIMITATION-UNREFERENCED", codes(run(m, self.r), "warnings"))


class CoverageOfCodes(unittest.TestCase):
    def test_every_e_code_exercised(self):
        # runs after MutationTests in alphabetical order (CoverageOfCodes < FixtureTests < MutationTests is NOT
        # guaranteed), so exercise the suite explicitly.
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(MutationTests)
        unittest.TextTestRunner(stream=open(os.devnull, "w")).run(suite)
        missing = sorted(set(ALL_E_CODES) - COVERED_E_CODES)
        self.assertEqual(missing, [], f"E-codes without a mutation test: {missing}")


if __name__ == "__main__":
    unittest.main()
