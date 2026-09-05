"""Tests for scripts/validate_report.py (validator contract v1.1; stdlib unittest, pytest also discovers these).

(a) every fixture directory through validate() against expected.json; the shipped template pair in place
(b) the CLI via subprocess (exit codes, closing note, --json shape incl. contract_version, missing manifest -> 2)
(c) in-memory mutations covering every E-code and every W-code at least once, every rule added or extended
    in contract v1.1, the false positives fixed in v1.1 (BOM, leading blank line, closing hashes, invalid UTF-8
    byte with a correct raw hash, report path escape), and --strict promotion of exactly STRICT_PROMOTED
(d) the valid fixture emits no warning at all
"""
import copy
import hashlib
import io
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
ALL_E_CODES = [  # validator contract v1.1 (35 codes)
    "E-SCHEMA", "E-ADDR-MALFORMED", "E-ADDR-CHECKSUM", "E-CHAIN-MISMATCH", "E-CHAIN-UNPINNED", "E-PIN-PLACEHOLDER",
    "E-PIN-HEADER-MISMATCH", "E-PIN-TIME", "E-PIN-PRIMARY", "E-META-STATUS", "E-META-REPORT", "E-SCOPE-TARGET",
    "E-SCOPE-RUNTIME", "E-SCOPE-ADDRESS", "E-REPORT-MISSING", "E-REPORT-HASH", "E-REPORT-IDENTITY",
    "E-REPORT-TARGET-ABSENT", "E-REPORT-SECTIONS", "E-REPORT-RATINGS", "E-REPORT-VERDICT", "E-CHECK-CORE-MISSING",
    "E-CHECK-STATUS", "E-CHECK-DISCOVERY-ONLY", "E-EVIDENCE-DANGLING", "E-FINDING-NO-EVIDENCE", "E-FINDING-UNLINKED",
    "E-FINDING-CHAIN", "E-FINDING-EVIDENCE-TYPE", "E-RATING-MISSING", "E-RATING-UNKNOWN-AS-LOW",
    "E-RATING-CRITICAL-AVERAGED", "E-DECL-SIGNING", "E-DECL-FORK", "E-MODE-MISMATCH",
]
ALL_W_CODES = [  # validator contract v1.1 (12 warnings)
    "W-REPORT-UNSCOPED-ADDRESS", "W-VERDICT-LANGUAGE", "W-EVIDENCE-DISCOVERY-ONLY", "W-CHECK-DISCOVERY-ONLY",
    "W-CHECK-SEVERITY-UNSUPPORTED", "W-RATING-BASIS-INCOMPLETE", "W-FINDING-CROSS-CHAIN", "W-FINDING-PIN-MISMATCH",
    "W-PIN-STALE", "W-PIN-UNUSED", "W-DECL-FORK-ATTESTATION", "W-LIMITATION-UNREFERENCED",
]
STRICT_PROMOTED = {"W-REPORT-UNSCOPED-ADDRESS", "W-EVIDENCE-DISCOVERY-ONLY", "W-VERDICT-LANGUAGE",
                   "W-CHECK-DISCOVERY-ONLY", "W-RATING-BASIS-INCOMPLETE", "W-DECL-FORK-ATTESTATION"}
COVERED_E_CODES: set = set()
COVERED_W_CODES: set = set()
ZERO32 = "0x" + "0" * 64


def load_valid():
    with open(os.path.join(VALID_DIR, "manifest.json"), "r", encoding="utf-8") as f:
        m = json.load(f)
    with open(os.path.join(VALID_DIR, "report.md"), "r", encoding="utf-8") as f:
        r = f.read()
    return m, r


def codes(res, kind="errors"):
    return [x["code"] for x in res[kind]]


def messages(res, code):
    return [x["message"] for x in res["errors"] + res["warnings"] if x["code"] == code]


def run(m, r, strict=False, rehash=True, report_missing=False):
    if rehash and r is not None:
        m["report"]["sha256"] = hashlib.sha256(r.encode("utf-8")).hexdigest()
    return vr.validate_objects(m, None if report_missing else r, VALID_DIR, strict=strict, report_path="report.md")


def run_files(m, report_bytes, strict=False, rehash=True, report_arg=None, extra_files=None):
    """Write manifest + report into a temporary directory and validate through the file entry point."""
    with tempfile.TemporaryDirectory() as tmp:
        rp = os.path.join(tmp, "report.md")
        with open(rp, "wb") as f:
            f.write(report_bytes)
        for name, content in (extra_files or {}).items():
            with open(os.path.join(tmp, name), "wb") as f:
                f.write(content)
        if rehash:
            m["report"]["sha256"] = ddcore.sha256_file(rp)
        mp = os.path.join(tmp, "manifest.json")
        with open(mp, "w", encoding="utf-8") as f:
            json.dump(m, f, indent=2)
        return vr.validate(mp, report_arg, strict=strict)


def check_by_id(m, cid):
    return next(c for c in m["checks"] if c["check_id"] == cid)


def ev_by_id(m, eid):
    return next(e for e in m["evidence"] if e["evidence_id"] == eid)


def finding_by_id(m, fid):
    return next(f for f in m["findings"] if f["finding_id"] == fid)


def new_addr(label):
    return ddcore.to_checksum_address("0x" + ddcore.keccak256(label.encode()).hex()[:40])


def pin_on(base, pin_id, chain, block=None, ts=None):
    p = copy.deepcopy(base)
    p["pin_id"] = pin_id
    p["chain_id"] = chain
    if block is not None:
        p["block_number"] = block
        p["captured_header"]["number"] = hex(block)
    if ts is not None:
        p["timestamp_unix"] = ts
        p["timestamp_utc"] = ddcore.iso_utc(ts)
        p["captured_header"]["timestamp"] = hex(ts)
    p["block_hash"] = ddcore.keccak256_hex(f"pin:{pin_id}:{chain}:{block}".encode())
    p["captured_header"]["hash"] = p["block_hash"]
    p["captured_header"]["parentHash"] = ddcore.keccak256_hex(f"parent:{pin_id}:{chain}".encode())
    return p


def split_report(r):
    head, body = r.split("\n---\n", 1)
    return head + "\n---\n", body


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
                self.assertEqual(set(exp["expected_codes"]) & set(exp["forbidden_codes"]), set(), name)

    def test_expected_json_covers_the_v11_code_list(self):
        """forbidden + expected codes of every fixture span the full v1.1 error-code list (minus declared side effects)."""
        for name in sorted(os.listdir(FIXTURES)):
            exp_path = os.path.join(FIXTURES, name, "expected.json")
            if not os.path.isfile(exp_path):
                continue
            with open(exp_path, "r", encoding="utf-8") as f:
                exp = json.load(f)
            listed = set(exp["expected_codes"]) | set(exp["forbidden_codes"])
            self.assertTrue(listed <= set(ALL_E_CODES), f"{name}: unknown codes {listed - set(ALL_E_CODES)}")
            self.assertGreaterEqual(len(listed), len(ALL_E_CODES) - 2, f"{name}: expected.json is stale")

    def test_valid_has_no_warnings_and_no_unscoped_addresses(self):
        res = vr.validate(os.path.join(VALID_DIR, "manifest.json"))
        self.assertEqual(res["result"], "PASS", codes(res))
        self.assertNotIn("W-REPORT-UNSCOPED-ADDRESS", codes(res, "warnings"))
        self.assertEqual(res["warnings"], [], res["warnings"])
        self.assertEqual(res["note"], vr.CLOSING_NOTE)
        self.assertEqual(res["contract_version"], "1.1")
        res = vr.validate(os.path.join(VALID_DIR, "manifest.json"), strict=True)
        self.assertEqual(res["result"], "PASS", codes(res))

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
            self.assertIn(f"| {key} | {m['ratings'][key]['rating']} |", body)
        self.assertIn(m["verdict"]["answer"], body)
        self.assertEqual(len(m["ratings"]), 11)
        self.assertEqual(set(vr.CORE_CHECKS) - {c["check_id"] for c in m["checks"]}, set())
        self.assertEqual(m["mode"], "broad")
        self.assertEqual(check_by_id(m, "C-HIST-SELL")["status"], "unknown")
        self.assertEqual(m["ratings"]["sellability_exit_depth"]["rating"], "unknown")
        self.assertEqual(check_by_id(m, "A-MINT")["severity"], "high")
        self.assertEqual(m["ratings"]["token_controls"]["rating"], "high")
        # v1.1 shape: every finding linked from a check of its surface; onchain evidence pinned; addresses scoped
        linked = {fid for c in m["checks"] for fid in c["finding_ids"]}
        self.assertEqual(linked, {f["finding_id"] for f in m["findings"]})
        for e in m["evidence"]:
            if e["evidence_type"] in vr.ONCHAIN_EVIDENCE_TYPES:
                self.assertTrue(e["pin_id"] or e["tx_hash"], e["evidence_id"])
        scoped = {(s["address"].lower(), s["chain_id"]) for s in m["scope_addresses"]}
        for row in m["evidence"] + m["findings"]:
            if row["address"] is not None:
                self.assertIn((row["address"].lower(), row["chain_id"]), scoped)

    def test_templates_example_passes_in_place(self):
        tm = os.path.join(ROOT, "templates", "manifest.example.json")
        tr = os.path.join(ROOT, "templates", "report.example.md")
        self.assertTrue(os.path.isfile(tm) and os.path.isfile(tr), "templates not generated")
        res = vr.validate(tm)  # no --report override: report.path must resolve beside the manifest
        self.assertEqual(res["result"], "PASS", codes(res))
        self.assertEqual(res["warnings"], [], res["warnings"])
        self.assertEqual(os.path.basename(res["report"]), "report.example.md")
        with open(tm, "r", encoding="utf-8") as f:
            tmj = json.load(f)
        self.assertEqual(tmj["report"]["path"], "report.example.md")
        self.assertEqual(tmj["report"]["sha256"], ddcore.sha256_file(tr))
        with open(tr, "r", encoding="utf-8") as f:
            head = f.read().split("\n---\n", 1)[0]
        self.assertIn("manifest_path: manifest.example.json", head)
        # identical to the valid fixture apart from the renamed pair
        vm, vrep = load_valid()
        vm["report"]["path"] = "report.example.md"
        vm["report"]["sha256"] = tmj["report"]["sha256"]
        self.assertEqual(vm, tmj)
        with open(tr, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), vrep.replace("manifest_path: manifest.json", "manifest_path: manifest.example.json", 1))

    def test_generator_is_deterministic(self):
        gen = os.path.join(FIXTURES, "make_fixtures.py")
        with tempfile.TemporaryDirectory() as tmp:
            tdir = os.path.join(tmp, "templates")
            subprocess.run([sys.executable, gen, "--out", tmp, "--templates-dir", tdir], check=True, capture_output=True)
            for name in os.listdir(tmp):
                if name == "templates":
                    continue
                for fn in ("manifest.json", "report.md", "expected.json"):
                    with open(os.path.join(tmp, name, fn), "rb") as a, open(os.path.join(FIXTURES, name, fn), "rb") as b:
                        self.assertEqual(a.read(), b.read(), f"{name}/{fn} differs from committed fixture")
            for fn in ("manifest.example.json", "report.example.md"):
                with open(os.path.join(tdir, fn), "rb") as a, open(os.path.join(ROOT, "templates", fn), "rb") as b:
                    self.assertEqual(a.read(), b.read(), f"templates/{fn} differs from the generator output")


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
        for key in ("result", "errors", "warnings", "note", "counts", "schema_lib", "contract_version"):
            self.assertIn(key, obj)
        self.assertEqual(obj["contract_version"], "1.1")
        self.assertEqual(obj["result"], "FAIL")
        self.assertEqual(obj["counts"]["errors"], len(obj["errors"]))
        self.assertTrue(all(set(e) == {"code", "message", "path"} for e in obj["errors"]))

    def test_usage_exit_2(self):
        p = subprocess.run([sys.executable, self.SCRIPT], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        p = subprocess.run([sys.executable, self.SCRIPT, "--manifest", os.path.join(FIXTURES, "no-such-manifest.json")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("manifest file not found", p.stderr)

    def test_explicit_report_override(self):
        p = subprocess.run([sys.executable, self.SCRIPT, "--manifest", os.path.join(VALID_DIR, "manifest.json"),
                            "--report", os.path.join(FIXTURES, "reject-wrong-target-report", "report.md")],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("E-REPORT-HASH", p.stdout)

    def test_strict_help_lists_promoted_codes(self):
        p = subprocess.run([sys.executable, self.SCRIPT, "--help"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        flat = "".join(p.stdout.split())  # argparse wraps long help lines at hyphens
        for code in STRICT_PROMOTED:
            self.assertIn(code, flat)


class MutationTests(unittest.TestCase):
    """Every E-code and W-code at least once via validate_objects on the valid manifest."""

    def setUp(self):
        self.m, self.r = load_valid()

    def expect(self, code, res, present=True):
        got = codes(res)
        if present:
            COVERED_E_CODES.add(code)
            self.assertIn(code, got, got)
        else:
            self.assertNotIn(code, got, got)

    def expect_warn(self, code, res, present=True):
        got = codes(res, "warnings")
        if present:
            COVERED_W_CODES.add(code)
            self.assertIn(code, got, got + codes(res))
        else:
            self.assertNotIn(code, got, got)

    def assert_no_abort(self, res):
        self.assertFalse(any("aborted" in e["message"] for e in res["errors"]), codes(res))

    def test_baseline_passes(self):
        res = run(self.m, self.r)
        self.assertEqual(res["result"], "PASS", codes(res))
        self.assertEqual(res["warnings"], [])
        self.assertEqual(res["contract_version"], "1.1")

    def test_strict_promoted_set_is_exact(self):
        self.assertEqual(vr.STRICT_PROMOTED, STRICT_PROMOTED)
        self.assertTrue(STRICT_PROMOTED <= set(ALL_W_CODES))

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
        m = copy.deepcopy(self.m); m["checks"][0]["finding_ids"] = [1, None, {"x": 1}]; m["ratings"]["token_controls"]["basis_check_ids"] = [[1]]
        res = run(m, self.r); self.assertEqual(res["result"], "FAIL")

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
        m = copy.deepcopy(self.m); m["scope_addresses"][0]["runtime_status"] = "eoa"; m["scope_addresses"][0]["code_hash"] = None
        self.expect("E-SCOPE-TARGET", run(m, self.r))
        m = copy.deepcopy(self.m); del m["scope_addresses"][0]
        res = run(m, self.r); self.expect("E-SCOPE-TARGET", res)
        # lowercase scope address is matched by normalization (no false positive)
        m = copy.deepcopy(self.m); m["scope_addresses"][0]["address"] = m["scope_addresses"][0]["address"].lower()
        res = run(m, self.r); self.expect("E-SCOPE-TARGET", res, present=False); self.expect("E-ADDR-CHECKSUM", res, present=False)

    # --- pins ---
    def test_pin_placeholder(self):
        m = copy.deepcopy(self.m); m["pins"][0]["block_hash"] = "0x" + "ab" * 32
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][1]["block_number"] = 0; m["pins"][1]["captured_header"]["number"] = "0x0"
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))

    def test_pin_placeholder_covers_every_32_byte_hash(self):
        """v1.1: tx hashes and code hashes anywhere are checked for placeholders."""
        m = copy.deepcopy(self.m); f = finding_by_id(m, "F2"); f["pin_or_tx"] = ZERO32; f["is_historical"] = True
        res = run(m, self.r); self.expect("E-PIN-PLACEHOLDER", res)
        m = copy.deepcopy(self.m); ev_by_id(m, "E3")["tx_hash"] = ZERO32
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["deployment"]["tx_hash"] = ZERO32
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["runtime"]["proxy"]["implementation_code_hash"] = "0x" + "11" * 32
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        m = copy.deepcopy(self.m); ev_by_id(m, "E2")["artifact_sha256"] = "a" * 64
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][2]["code_hash"] = ZERO32
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r))
        # real hashes pass
        m = copy.deepcopy(self.m); ev_by_id(m, "E3")["tx_hash"] = ddcore.keccak256_hex(b"real tx")
        self.expect("E-PIN-PLACEHOLDER", run(m, self.r), present=False)

    def test_pin_header_mismatch(self):
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["hash"] = ddcore.keccak256_hex(b"other block")
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["parentHash"] = m["pins"][0]["captured_header"]["hash"]
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["number"] = "0x1"
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r))
        # header hash differing in case only is the same value (no false positive)
        m = copy.deepcopy(self.m); h = m["pins"][0]["captured_header"]["hash"]; m["pins"][0]["captured_header"]["hash"] = "0x" + h[2:].upper()
        self.expect("E-PIN-HEADER-MISMATCH", run(m, self.r), present=False)
        # v1.1: two pins at the same chain+block with different hashes contradict each other
        m = copy.deepcopy(self.m); p = pin_on(m["pins"][0], "P3", 8453, block=m["pins"][0]["block_number"], ts=m["pins"][0]["timestamp_unix"])
        m["pins"].append(p); ev_by_id(m, "E13")["pin_id"] = "P3"
        res = run(m, self.r); self.expect("E-PIN-HEADER-MISMATCH", res)

    def test_pin_time(self):
        m = copy.deepcopy(self.m); m["pins"][0]["captured_header"]["timestamp"] = hex(m["pins"][0]["timestamp_unix"] + 1)
        self.expect("E-PIN-TIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["timestamp_utc"] = "2020-01-01T00:00:00Z"
        self.expect("E-PIN-TIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_at_utc"] = "2024-12-01T00:00:00Z"  # before the block existed
        self.expect("E-PIN-TIME", run(m, self.r))
        m = copy.deepcopy(self.m); m["pins"][0]["captured_at_utc"] = "2024-12-31T23:46:00Z"  # 14 min skew: tolerated
        self.expect("E-PIN-TIME", run(m, self.r), present=False)
        m = copy.deepcopy(self.m); p = m["pins"][0]; p["timestamp_unix"] = 100; p["timestamp_utc"] = ddcore.iso_utc(100); p["captured_header"]["timestamp"] = "0x64"
        self.expect("E-PIN-TIME", run(m, self.r))

    def test_pin_time_overflow_does_not_abort_other_pins(self):
        """v1.1: an unconvertible timestamp is E-PIN-TIME for that pin; the other pins are still checked."""
        m = copy.deepcopy(self.m); p = m["pins"][0]; t = 2 ** 70
        p["timestamp_unix"] = t; p["captured_header"]["timestamp"] = hex(t)
        m["pins"][1]["captured_header"]["hash"] = ddcore.keccak256_hex(b"other")  # second pin defect must still surface
        res = run(m, self.r)
        self.expect("E-PIN-TIME", res); self.expect("E-PIN-HEADER-MISMATCH", res); self.assert_no_abort(res)
        self.assertTrue(any("out of range" in x for x in messages(res, "E-PIN-TIME")), messages(res, "E-PIN-TIME"))

    def test_pin_monotonic_per_chain(self):
        """v1.1: on one chain a higher block carries a strictly later timestamp."""
        m = copy.deepcopy(self.m); p = m["pins"][1]  # P2, block 19000000, earlier than P1
        later = m["pins"][0]["timestamp_unix"] + 3600
        p["timestamp_unix"] = later; p["timestamp_utc"] = ddcore.iso_utc(later); p["captured_header"]["timestamp"] = hex(later)
        p["captured_at_utc"] = ddcore.iso_utc(later + 600)
        res = run(m, self.r); self.expect("E-PIN-TIME", res)
        self.assertTrue(any("monotonic" in x for x in messages(res, "E-PIN-TIME")))
        m = copy.deepcopy(self.m); p = m["pins"][1]  # equal timestamps at different blocks
        same = m["pins"][0]["timestamp_unix"]
        p["timestamp_unix"] = same; p["timestamp_utc"] = ddcore.iso_utc(same); p["captured_header"]["timestamp"] = hex(same)
        self.expect("E-PIN-TIME", run(m, self.r))
        # a pin on another chain is not compared (no false positive)
        m = copy.deepcopy(self.m); q = pin_on(m["pins"][0], "P3", 10, block=1, ts=m["pins"][0]["timestamp_unix"])
        m["pins"].append(q)
        sc = copy.deepcopy(m["scope_addresses"][5]); sc.update({"chain_id": 10, "pin_id": "P3", "role": "bridge", "address": new_addr("b10")})
        m["scope_addresses"].append(sc)
        res = run(m, self.r); self.expect("E-PIN-TIME", res, present=False)

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
        self.expect_warn("W-PIN-STALE", run(m, self.r))

    def test_pin_unused_warning(self):
        m = copy.deepcopy(self.m)
        m["pins"].append(dict(copy.deepcopy(m["pins"][0]), pin_id="P3", chain_id=10))
        self.expect_warn("W-PIN-UNUSED", run(m, self.r))

    # --- chains ---
    def test_chain_unpinned(self):
        m = copy.deepcopy(self.m); m["evidence"][5]["chain_id"] = 10; m["evidence"][5]["pin_id"] = None
        self.expect("E-CHAIN-UNPINNED", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][2]["pin_id"] = "P7"
        self.expect("E-CHAIN-UNPINNED", run(m, self.r))

    def test_chain_unpinned_onchain_evidence_needs_pin_or_tx(self):
        """v1.1: rpc_state/bytecode/... rows must carry pin_id or tx_hash even when the chain has a pin."""
        m = copy.deepcopy(self.m); e = ev_by_id(m, "E3"); e["pin_id"] = None; e["block_number"] = None; e["tx_hash"] = None
        res = run(m, self.r); self.expect("E-CHAIN-UNPINNED", res)
        e["tx_hash"] = ddcore.keccak256_hex(b"a real receipt")
        res = run(copy.deepcopy(m), self.r); self.expect("E-CHAIN-UNPINNED", res, present=False)
        e["tx_hash"] = ZERO32  # placeholder does not count as a binding
        res = run(copy.deepcopy(m), self.r); self.expect("E-CHAIN-UNPINNED", res); self.expect("E-PIN-PLACEHOLDER", res)
        m = copy.deepcopy(self.m); e = ev_by_id(m, "E14"); e["pin_id"] = None  # website rows need no pin
        self.expect("E-CHAIN-UNPINNED", run(m, self.r), present=False)

    def test_chain_unpinned_target_runtime_pin_on_other_chain(self):
        m = copy.deepcopy(self.m)
        m["pins"].append(pin_on(m["pins"][0], "P3", 1, block=1, ts=m["pins"][0]["timestamp_unix"]))
        sc = copy.deepcopy(m["scope_addresses"][5]); sc.update({"chain_id": 1, "pin_id": "P3", "role": "bridge", "address": new_addr("b1")})
        m["scope_addresses"].append(sc)
        m["target"]["runtime"]["pin_id"] = "P3"
        res = run(m, self.r); self.expect("E-CHAIN-UNPINNED", res)
        self.assertTrue(any("target.runtime.pin_id" in x for x in messages(res, "E-CHAIN-UNPINNED")))

    def test_finding_chain(self):
        m = copy.deepcopy(self.m)
        e = ev_by_id(m, "E11"); e["chain_id"] = 10; e["pin_id"] = None
        res = run(m, self.r); self.expect("E-FINDING-CHAIN", res)
        # v1.1: evidence on another (pinned) chain cited by a non-bridge finding is an error, not a warning
        m = copy.deepcopy(self.m)
        m["pins"].append(pin_on(m["pins"][0], "P3", 10, block=5, ts=m["pins"][0]["timestamp_unix"]))
        sc = copy.deepcopy(m["scope_addresses"][0]); sc.update({"chain_id": 10, "pin_id": "P3", "role": "other", "provenance": "inferred"})
        m["scope_addresses"].append(sc)
        e = ev_by_id(m, "E11"); e["chain_id"] = 10; e["pin_id"] = "P3"
        res = run(m, self.r)
        self.expect("E-FINDING-CHAIN", res); self.expect_warn("W-FINDING-CROSS-CHAIN", res, present=False)

    def test_finding_cross_chain_permitted_for_bridge_legs(self):
        """v1.1: only bridge-leg findings may cross chains (role bridge on a cited address, or surface external_dependencies)."""
        m = copy.deepcopy(self.m)
        m["pins"].append(pin_on(m["pins"][0], "P3", 10, block=5, ts=m["pins"][0]["timestamp_unix"]))
        bridge = new_addr("bridge-dest")
        sc = copy.deepcopy(m["scope_addresses"][2]); sc.update({"chain_id": 10, "pin_id": "P3", "role": "bridge", "address": bridge, "code_hash": ddcore.keccak256_hex(b"bridge code")})
        m["scope_addresses"].append(sc)
        e = ev_by_id(m, "E11"); e["chain_id"] = 10; e["pin_id"] = "P3"; e["address"] = bridge
        res = run(copy.deepcopy(m), self.r)
        self.expect("E-FINDING-CHAIN", res, present=False); self.expect_warn("W-FINDING-CROSS-CHAIN", res)
        # surface external_dependencies is the other permitted form
        m2 = copy.deepcopy(m); m2["scope_addresses"][-1]["role"] = "other"
        f = finding_by_id(m2, "F2"); f["surface"] = "external_dependencies"
        check_by_id(m2, "D-CONC").update({"status": "pass", "severity": None, "finding_ids": []})
        check_by_id(m2, "H-DEPS").update({"status": "finding", "severity": "medium", "finding_ids": ["F2"], "evidence_ids": ["E2", "E14", "E11"]})
        m2["ratings"]["external_dependencies"].update({"rating": "medium", "basis_check_ids": ["H-DEPS"]})
        r2 = self.r.replace("| external_dependencies | low |", "| external_dependencies | medium |", 1)
        res = run(m2, r2)
        self.expect("E-FINDING-CHAIN", res, present=False); self.expect_warn("W-FINDING-CROSS-CHAIN", res)

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

    def test_scope_runtime_proxy_resolution(self):
        """v1.1: a proxy status needs a resolved implementation scoped with role implementation / contract; admin and beacon roles."""
        m = copy.deepcopy(self.m); m["target"]["runtime"]["proxy"]["status"] = "eip1967"
        res = run(m, self.r); self.expect("E-SCOPE-RUNTIME", res)
        impl = new_addr("impl")
        m = copy.deepcopy(self.m); m["target"]["runtime"]["proxy"].update({"status": "eip1967", "implementation": impl})
        res = run(copy.deepcopy(m), self.r); self.expect("E-SCOPE-RUNTIME", res); self.expect("E-SCOPE-ADDRESS", res)
        sc = copy.deepcopy(m["scope_addresses"][2]); sc.update({"address": impl, "role": "other", "code_hash": ddcore.keccak256_hex(b"impl code")})
        m["scope_addresses"].append(sc)
        res = run(copy.deepcopy(m), self.r); self.expect("E-SCOPE-RUNTIME", res); self.expect("E-SCOPE-ADDRESS", res, present=False)
        m["scope_addresses"][-1]["role"] = "implementation"
        res = run(copy.deepcopy(m), self.r); self.expect("E-SCOPE-RUNTIME", res, present=False)
        m["scope_addresses"][-1].update({"runtime_status": "eoa", "code_hash": None})
        res = run(copy.deepcopy(m), self.r); self.expect("E-SCOPE-RUNTIME", res)
        # admin must be scoped as proxy_admin, beacon as beacon
        m = copy.deepcopy(self.m); m["target"]["runtime"]["proxy"]["admin"] = m["scope_addresses"][1]["address"]  # owner entry, role owner
        res = run(m, self.r); self.expect("E-SCOPE-RUNTIME", res); self.expect("E-SCOPE-ADDRESS", res, present=False)
        m = copy.deepcopy(self.m); m["target"]["runtime"]["proxy"]["beacon"] = m["scope_addresses"][1]["address"]
        self.expect("E-SCOPE-RUNTIME", run(m, self.r))

    def test_scope_runtime_target_entry_material(self):
        m = copy.deepcopy(self.m); m["scope_addresses"][0]["material"] = False
        res = run(m, self.r); self.expect("E-SCOPE-RUNTIME", res); self.expect("E-SCOPE-TARGET", res, present=False)

    def test_scope_address_binding(self):
        """v1.1 E-SCOPE-ADDRESS: evidence, finding, limitation and proxy addresses must be scope entries (address + chain)."""
        other = new_addr("same-symbol-other-address")
        m = copy.deepcopy(self.m)
        for eid in ("E2", "E3", "E5"):
            ev_by_id(m, eid)["address"] = other
        res = run(m, self.r); self.expect("E-SCOPE-ADDRESS", res)
        m = copy.deepcopy(self.m); finding_by_id(m, "F1")["address"] = other
        self.expect("E-SCOPE-ADDRESS", run(m, self.r))
        m = copy.deepcopy(self.m); m["coverage"]["limitations"][0]["affected_addresses"] = [other]
        self.expect("E-SCOPE-ADDRESS", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["runtime"]["proxy"]["implementation"] = other
        self.expect("E-SCOPE-ADDRESS", run(m, self.r))
        # same address on a different chain is not scoped for that chain
        m = copy.deepcopy(self.m)
        m["pins"].append(pin_on(m["pins"][0], "P3", 10, block=5, ts=m["pins"][0]["timestamp_unix"]))
        sc = copy.deepcopy(m["scope_addresses"][5]); sc.update({"chain_id": 10, "pin_id": "P3", "role": "bridge", "address": new_addr("b10")})
        m["scope_addresses"].append(sc)
        e = ev_by_id(m, "E14"); e["chain_id"] = 10; e["address"] = m["scope_addresses"][0]["address"]
        res = run(m, self.r); self.expect("E-SCOPE-ADDRESS", res)
        # scoped addresses in any case form pass
        m = copy.deepcopy(self.m); ev_by_id(m, "E2")["address"] = ev_by_id(m, "E2")["address"].lower()
        res = run(m, self.r); self.expect("E-SCOPE-ADDRESS", res, present=False)

    # --- report ---
    def test_report_missing(self):
        self.expect("E-REPORT-MISSING", run(copy.deepcopy(self.m), self.r, report_missing=True))

    def test_report_missing_path_escape(self):
        """v1.1: report.path must be relative and resolve inside the manifest directory; --report may be absolute."""
        for bad in ("/etc/hostname", "../report.md", "sub/../../report.md"):
            m = copy.deepcopy(self.m); m["report"]["path"] = bad
            res = run_files(m, self.r.encode("utf-8"))
            self.expect("E-REPORT-MISSING", res)
            self.assertTrue(any("path escapes manifest directory" in x for x in messages(res, "E-REPORT-MISSING")), bad)
            self.assertIsNone(res["report"])
        m = copy.deepcopy(self.m); m["report"]["path"] = "./report.md"  # stays inside
        res = run_files(m, self.r.encode("utf-8")); self.assertEqual(res["result"], "PASS", codes(res))
        m = copy.deepcopy(self.m); m["report"]["path"] = "/etc/hostname"
        with tempfile.TemporaryDirectory() as tmp:
            rp = os.path.join(tmp, "elsewhere.md")
            with open(rp, "w", encoding="utf-8") as f:
                f.write(self.r)
            m["report"]["sha256"] = ddcore.sha256_file(rp)
            res = run_files(m, b"", rehash=False, report_arg=rp)  # explicit --report wins
            self.assertEqual(res["result"], "PASS", codes(res))
        m = copy.deepcopy(self.m)
        m["declarations"]["simulation"].update({"used": True, "fork_verified_disposable": True, "synthetic_accounts_only": True,
                                                "results_labeled_counterfactual": True, "fork_chain_id": 8453, "fork_block": 20000000,
                                                "fork_attestation_path": os.path.join(os.path.abspath(os.sep), "attestation.json")})
        res = run_files(m, self.r.encode("utf-8")); self.expect("E-REPORT-MISSING", res)

    def test_report_hash(self):
        self.expect("E-REPORT-HASH", run(copy.deepcopy(self.m), self.r + "\nedited\n", rehash=False))
        m = copy.deepcopy(self.m); m["report"]["sha256"] = ""
        res = run(m, self.r, rehash=False); self.expect("E-REPORT-HASH", res); self.expect("E-SCHEMA", res)
        m = copy.deepcopy(self.m); m["report"]["sha256"] = "not-a-hash"
        self.expect("E-REPORT-HASH", run(m, self.r, rehash=False))

    def test_report_hash_over_raw_bytes(self):
        """v1.1: the hash is computed over the file bytes, so an invalid UTF-8 byte with a correct raw hash passes."""
        raw = self.r.encode("utf-8").replace(b"Every address", b"Every\xff address", 1)
        res = run_files(copy.deepcopy(self.m), raw)
        self.expect("E-REPORT-HASH", res, present=False); self.assertEqual(res["result"], "PASS", codes(res))
        res = vr.validate_objects(copy.deepcopy(self.m), raw.decode("utf-8", errors="replace"), VALID_DIR, report_path="report.md",
                                  report_bytes=raw)
        self.assertEqual(res["result"], "FAIL"); self.expect("E-REPORT-HASH", res)  # stale sha256 for the mutated bytes
        m = copy.deepcopy(self.m); m["report"]["sha256"] = ddcore.sha256_bytes(raw)
        res = vr.validate_objects(m, raw.decode("utf-8", errors="replace"), VALID_DIR, report_path="report.md", report_bytes=raw)
        self.assertEqual(res["result"], "PASS", codes(res))

    def test_report_bom_and_leading_blank_lines_tolerated(self):
        res = run_files(copy.deepcopy(self.m), b"\xef\xbb\xbf" + self.r.encode("utf-8"))
        self.assertEqual(res["result"], "PASS", codes(res))
        res = run(copy.deepcopy(self.m), "\n\n" + self.r)
        self.assertEqual(res["result"], "PASS", codes(res))
        res = run(copy.deepcopy(self.m), self.r.replace("\n", "\r\n"))
        self.assertEqual(res["result"], "PASS", codes(res))

    def test_report_identity(self):
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("evm_dd_report: 1", "evm_dd_report: 2")))
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("target_chain_id: 8453", "target_chain_id: 1")))
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("primary_pin_block: 20000000", "primary_pin_block: 20000001")))
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.split("---", 2)[2]))  # no frontmatter
        self.expect("E-REPORT-IDENTITY", run(copy.deepcopy(self.m), self.r.replace("---\nevm_dd", "---\nbad line without colon\nevm_dd", 1)))
        # v1.1: duplicate frontmatter keys are rejected even when the second value is correct
        a = self.m["target"]["address_checksum"]
        r = self.r.replace("target_address: " + a, "target_address: 0x" + "1" * 40 + "\ntarget_address: " + a, 1)
        res = run(copy.deepcopy(self.m), r); self.expect("E-REPORT-IDENTITY", res)
        self.assertTrue(any("duplicate" in x for x in messages(res, "E-REPORT-IDENTITY")))
        # lowercase frontmatter address and uppercase block hash are the same values
        res = run(copy.deepcopy(self.m), self.r.replace("target_address: " + a, "target_address: " + a.lower(), 1))
        self.expect("E-REPORT-IDENTITY", res, present=False)

    def test_report_target_absent(self):
        a = self.m["target"]["address_checksum"]
        head, body = split_report(self.r)
        r = head + body.replace(a, "0x" + "0" * 40)
        res = run(copy.deepcopy(self.m), r)
        self.expect("E-REPORT-TARGET-ABSENT", res)
        self.expect_warn("W-REPORT-UNSCOPED-ADDRESS", res)

    def test_report_invisible_text_does_not_count(self):
        """v1.1: HTML comments and fenced code are stripped before body scans."""
        a = self.m["target"]["address_checksum"]
        head, body = split_report(self.r)
        body = body.replace(a, "0x" + "0" * 40).replace("## Verdict", "<!-- " + a + " -->\n\n## Verdict", 1)
        res = run(copy.deepcopy(self.m), head + body); self.expect("E-REPORT-TARGET-ABSENT", res)
        body2 = split_report(self.r)[1].replace(a, "0x" + "0" * 40).replace("## Verdict", "<!--\n" + a + "\n-->\n\n## Verdict", 1)
        res = run(copy.deepcopy(self.m), head + body2); self.expect("E-REPORT-TARGET-ABSENT", res)
        body3 = split_report(self.r)[1].replace(a, "0x" + "0" * 40).replace("## Verdict", "```\n" + a + "\n```\n\n## Verdict", 1)
        res = run(copy.deepcopy(self.m), head + body3); self.expect("E-REPORT-TARGET-ABSENT", res)
        r = self.r.replace("## Recommendations\n", "Recommendations\n", 1).replace("## Declarations\n", "```\n## Recommendations\n```\n\n## Declarations\n", 1)
        res = run(copy.deepcopy(self.m), r); self.expect("E-REPORT-SECTIONS", res)
        r = self.r.replace("## Recommendations\n", "<!-- ## Recommendations -->\n", 1)
        res = run(copy.deepcopy(self.m), r); self.expect("E-REPORT-SECTIONS", res)
        # a comment that mentions an unscoped address is not a visible mention (no warning); ~~~ fences are fences too
        r = self.r.replace("## Recommendations\n", "## Recommendations\n\n<!-- 0x" + "1" * 40 + " -->\n~~~\n0x" + "2" * 40 + "\n~~~\n", 1)
        res = run(copy.deepcopy(self.m), r); self.expect_warn("W-REPORT-UNSCOPED-ADDRESS", res, present=False)
        # a single-line inline code span with triple backticks is not a fence
        r = self.r.replace("## Recommendations\n", "## Recommendations\n\n```inline```\n", 1)
        res = run(copy.deepcopy(self.m), r); self.assertEqual(res["result"], "PASS", codes(res))

    def test_report_sections(self):
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(self.m), self.r.replace("## Recommendations", "## Advice")))
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(self.m), self.r.replace("## Recommendations", "### Recommendations")))
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(self.m), self.r.replace("## Recommendations\n", "## RECOMMENDATIONS\n")))
        # v1.1 false positives fixed: closing hashes and trailing spaces are valid ATX headings; order is free
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(self.m), self.r.replace("## Recommendations\n", "## Recommendations ##\n")), present=False)
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(self.m), self.r.replace("## Recommendations\n", "## Recommendations   \n")), present=False)
        m = copy.deepcopy(self.m); m["mode"] = "focused"
        r = self.r.replace("mode: broad", "mode: focused").replace("## Recommendations", "## Advice")
        self.assertNotIn("E-REPORT-SECTIONS", codes(run(m, r)))
        self.expect("E-REPORT-SECTIONS", run(copy.deepcopy(m), r.replace("## Evidence ledger", "## Ledger")))

    def test_report_ratings_table(self):
        """v1.1 E-REPORT-RATINGS: every manifest rating has a row whose rating cell equals the manifest."""
        res = run(copy.deepcopy(self.m), self.r.replace("| token_controls | high |", "| token_controls | low |", 1))
        self.expect("E-REPORT-RATINGS", res); self.expect("E-REPORT-IDENTITY", res, present=False)
        res = run(copy.deepcopy(self.m), self.r.replace("| development_disclosure | low |", "| dev_disclosure | low |", 1))
        self.expect("E-REPORT-RATINGS", res)
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["rating"] = "critical"
        check_by_id(m, "A-MINT")["severity"] = "critical"; finding_by_id(m, "F1")["severity"] = "critical"
        res = run(m, self.r); self.expect("E-REPORT-RATINGS", res); self.expect("E-RATING-CRITICAL-AVERAGED", res, present=False)
        res = run(copy.deepcopy(self.m), self.r.replace("| token_controls | high |", "|   token_controls   |  high  |", 1))
        self.expect("E-REPORT-RATINGS", res, present=False)
        res = run(copy.deepcopy(self.m), self.r.replace("## Ratings\n", "## Ratings2\n", 1))
        self.expect("E-REPORT-RATINGS", res); self.expect("E-REPORT-SECTIONS", res)
        m = copy.deepcopy(self.m); m["mode"] = "focused"; del m["ratings"]  # no ratings: nothing to render
        res = run(m, self.r.replace("mode: broad", "mode: focused").replace("| token_controls | high |", "| token_controls | low |", 1))
        self.expect("E-REPORT-RATINGS", res, present=False)

    def test_report_verdict_text(self):
        """v1.1 E-REPORT-VERDICT: manifest.verdict.answer must occur (whitespace-normalized) under ## Verdict."""
        res = run(copy.deepcopy(self.m), self.r.replace("**Answer.** NO-GO under the stated requirement", "**Answer.** GO under the stated requirement", 1))
        self.expect("E-REPORT-VERDICT", res)
        m = copy.deepcopy(self.m); m["verdict"]["answer"] = "GO-WITH-CONDITIONS under the stated requirement: none."
        self.expect("E-REPORT-VERDICT", run(m, self.r))
        ans = self.m["verdict"]["answer"]
        wrapped = ans.replace(" so supply", "\n  so   supply")
        res = run(copy.deepcopy(self.m), self.r.replace(ans, wrapped, 1))
        self.expect("E-REPORT-VERDICT", res, present=False)
        head, body = split_report(self.r)
        res = run(copy.deepcopy(self.m), head + body.replace("## Verdict\n", "## Verdict2\n", 1))
        self.expect("E-REPORT-VERDICT", res)

    def test_mode_mismatch(self):
        m = copy.deepcopy(self.m); m["mode"] = "formal"
        self.expect("E-MODE-MISMATCH", run(m, self.r))

    def test_verdict_language_warning_and_strict(self):
        r = self.r.replace("## Verdict\n", "## Verdict\n\nThis token is completely safe.\n")
        res = run(copy.deepcopy(self.m), r)
        self.expect_warn("W-VERDICT-LANGUAGE", res); self.assertEqual(res["result"], "PASS")
        res = run(copy.deepcopy(self.m), r, strict=True)
        self.assertIn("W-VERDICT-LANGUAGE", codes(res)); self.assertEqual(res["result"], "FAIL")
        r2 = self.r.replace("## Verdict\n", "## Verdict\n\nThe position is not safe from removal after unlock.\n")
        self.expect_warn("W-VERDICT-LANGUAGE", run(copy.deepcopy(self.m), r2), present=False)

    def test_verdict_language_variants_and_manifest(self):
        """v1.1: the heuristic covers the manifest answer/main_reasons and the broadened phrasing list."""
        for phrase in ("It's safe.", "This is a safe investment.", "The token is definitely safe.", "It looks safe.",
                       "This seems secure.", "The pool remains safe.", "It will be safe.", "It is considered safe.",
                       "This was deemed safe.", "The token is riskless.", "The launch is rug-proof.", "It is rugproof.",
                       "Holding is 100% safe.", "There is no risk.", "It is risk-free.", "Liquidity is perfectly safe."):
            with self.subTest(phrase=phrase):
                r = self.r.replace("## Verdict\n", "## Verdict\n\n" + phrase + "\n", 1)
                self.expect_warn("W-VERDICT-LANGUAGE", run(copy.deepcopy(self.m), r))
        for phrase in ("This token is not safe.", "It is never safe.", "It cannot be considered safe.", "It isn't safe.",
                       "They aren't safe.", "No current executable removal path found.", "It is not a safe investment.",
                       "The casino is secured by a timelock.", "The token is safeTransfer-compatible."):
            with self.subTest(phrase=phrase):
                r = self.r.replace("## Verdict\n", "## Verdict\n\n" + phrase + "\n", 1)
                self.expect_warn("W-VERDICT-LANGUAGE", run(copy.deepcopy(self.m), r), present=False)
        m = copy.deepcopy(self.m); m["verdict"]["answer"] = "This token is safe."
        r = self.r.replace(self.m["verdict"]["answer"], "This token is safe.", 1)
        res = run(m, r); self.expect_warn("W-VERDICT-LANGUAGE", res)
        self.assertTrue(any(x["path"] == "$.verdict.answer" for x in res["warnings"]))
        m = copy.deepcopy(self.m); m["verdict"]["main_reasons"].append("liquidity is fully safe")
        res = run(m, self.r); self.expect_warn("W-VERDICT-LANGUAGE", res)
        self.assertTrue(any(x["path"].startswith("$.verdict.main_reasons") for x in res["warnings"]))

    def test_unscoped_address_warning_and_strict(self):
        r = self.r.replace("## Recommendations\n", "## Recommendations\n\nAlso seen: 0x" + "1" * 40 + "\n")
        res = run(copy.deepcopy(self.m), r)
        self.expect_warn("W-REPORT-UNSCOPED-ADDRESS", res)
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

    def test_check_status_v11_contradictions(self):
        """v1.1: pass/not_applicable with findings or severity; finding severity below its findings; surface mismatch;
        a limited check presented as pass/not_applicable."""
        m = copy.deepcopy(self.m); c = check_by_id(m, "A-MINT"); c["status"] = "pass"; c["severity"] = None  # keeps finding_ids
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        m = copy.deepcopy(self.m); check_by_id(m, "A-UPGRADE")["severity"] = "critical"
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        m = copy.deepcopy(self.m); c = check_by_id(m, "G-REWARDS"); c["severity"] = "low"
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        m = copy.deepcopy(self.m); c = check_by_id(m, "G-REWARDS"); c["finding_ids"] = ["F2"]
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = "low"  # F1 is high
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        self.assertTrue(any("lower than its linked finding" in x for x in messages(res, "E-CHECK-STATUS")))
        m = copy.deepcopy(self.m); check_by_id(m, "D-CONC")["finding_ids"] = ["F2", "F3"]  # F3 is token_controls
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        m = copy.deepcopy(self.m); c = check_by_id(m, "C-HIST-SELL"); c.update({"status": "pass", "evidence_ids": ["E10"], "reason": None})
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)  # still listed in L1.affected_check_ids
        self.assertTrue(any("affected_check_ids" in x for x in messages(res, "E-CHECK-STATUS")))
        m = copy.deepcopy(self.m); c = check_by_id(m, "C-HIST-SELL"); c.update({"status": "not_applicable", "reason": "no market"})
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res)
        m = copy.deepcopy(self.m); c = check_by_id(m, "C-HIST-SELL"); c.update({"status": "skipped", "reason": "deferred"})
        res = run(m, self.r); self.expect("E-CHECK-STATUS", res, present=False)

    def test_check_severity_unsupported_warning(self):
        m = copy.deepcopy(self.m); check_by_id(m, "A-ADMIN")["severity"] = "high"  # F3 is medium
        res = run(m, self.r); self.expect_warn("W-CHECK-SEVERITY-UNSUPPORTED", res)
        self.assertEqual(res["result"], "PASS", codes(res))  # rating token_controls high is still at the floor
        res = run(copy.deepcopy(m), self.r, strict=True); self.assertEqual(res["result"], "PASS")  # not promoted

    def test_check_discovery_only(self):
        """v1.1: a pass on a no-threshold authority check supported only by discovery evidence is an error; elsewhere a warning."""
        for cid in ("A-UPGRADE", "B-PRINCIPAL", "B-SIDE"):
            m = copy.deepcopy(self.m); check_by_id(m, cid)["evidence_ids"] = ["E14"]
            res = run(m, self.r); self.expect("E-CHECK-DISCOVERY-ONLY", res)
            self.expect_warn("W-CHECK-DISCOVERY-ONLY", res, present=False)
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["evidence_ids"] = ["E14", "E15"]
        self.expect("E-CHECK-DISCOVERY-ONLY", run(m, self.r))
        m = copy.deepcopy(self.m); check_by_id(m, "H-UTILITY")["evidence_ids"] = ["E14"]
        res = run(m, self.r); self.expect_warn("W-CHECK-DISCOVERY-ONLY", res); self.expect("E-CHECK-DISCOVERY-ONLY", res, present=False)
        self.assertEqual(res["result"], "PASS")
        self.assertEqual(run(copy.deepcopy(m), self.r, strict=True)["result"], "FAIL")
        m = copy.deepcopy(self.m); check_by_id(m, "C-QUOTE")["evidence_ids"] = ["E15"]
        self.expect_warn("W-CHECK-DISCOVERY-ONLY", run(m, self.r))
        # a source_verified or onchain row alongside discovery rows is enough
        m = copy.deepcopy(self.m); check_by_id(m, "A-UPGRADE")["evidence_ids"] = ["E14", "E5"]
        res = run(m, self.r); self.expect("E-CHECK-DISCOVERY-ONLY", res, present=False); self.expect_warn("W-CHECK-DISCOVERY-ONLY", res, present=False)

    def test_evidence_dangling(self):
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["finding_ids"] = ["F77"]
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); check_by_id(m, "A-SEIZE")["pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["evidence"][0]["pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["findings"][0]["pin_or_tx"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); f = m["findings"][0]; f["pin_or_tx"] = ddcore.keccak256_hex(b"some tx"); f["is_historical"] = True
        self.assertNotIn("E-EVIDENCE-DANGLING", codes(run(m, self.r)))
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["basis_check_ids"].append("A-NOPE")
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["time_basis_pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["coverage"]["limitations"][0]["affected_check_ids"] = ["Z-NOPE"]
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["scope_addresses"][3]["limitation_id"] = "L9"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))
        m = copy.deepcopy(self.m); m["target"]["runtime"]["pin_id"] = "P8"
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r))

    def test_evidence_dangling_v11_pin_block_and_historical(self):
        """v1.1: evidence block_number must equal its pin's block; is_historical agrees with pin vs tx in pin_or_tx."""
        m = copy.deepcopy(self.m); ev_by_id(m, "E3")["block_number"] = 19000000  # pin P1 is 20000000
        res = run(m, self.r); self.expect("E-EVIDENCE-DANGLING", res)
        m = copy.deepcopy(self.m); ev_by_id(m, "E3")["block_number"] = 20000000
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r), present=False)
        m = copy.deepcopy(self.m); finding_by_id(m, "F2")["is_historical"] = True  # pin_or_tx P1
        res = run(m, self.r); self.expect("E-EVIDENCE-DANGLING", res)
        m = copy.deepcopy(self.m); finding_by_id(m, "F2")["pin_or_tx"] = ddcore.keccak256_hex(b"tx")  # is_historical False
        res = run(m, self.r); self.expect("E-EVIDENCE-DANGLING", res)
        m = copy.deepcopy(self.m); f = finding_by_id(m, "F2"); f["pin_or_tx"] = ddcore.keccak256_hex(b"tx"); f["is_historical"] = None
        self.expect("E-EVIDENCE-DANGLING", run(m, self.r), present=False)

    # --- findings ---
    def test_finding_no_evidence(self):
        m = copy.deepcopy(self.m); m["findings"][1]["evidence_ids"] = []
        self.expect("E-FINDING-NO-EVIDENCE", run(m, self.r))
        m["findings"][1]["confidence"] = "unknown"
        self.assertNotIn("E-FINDING-NO-EVIDENCE", codes(run(m, self.r)))

    def test_finding_unlinked(self):
        """v1.1: every finding hangs off a check; an orphan critical finding also forces the rating."""
        m = copy.deepcopy(self.m)
        f = copy.deepcopy(finding_by_id(m, "F1")); f["finding_id"] = "F4"; f["severity"] = "critical"
        m["findings"].append(f)
        c = check_by_id(m, "A-MINT"); c["status"] = "pass"; c["severity"] = None; c["finding_ids"] = []
        m["ratings"]["token_controls"]["rating"] = "low"
        res = run(m, self.r.replace("| token_controls | high |", "| token_controls | low |", 1))
        self.expect("E-FINDING-UNLINKED", res); self.expect("E-RATING-CRITICAL-AVERAGED", res)
        self.assertTrue(any("F4" in x for x in messages(res, "E-FINDING-UNLINKED")))
        m = copy.deepcopy(self.m); check_by_id(m, "A-ADMIN")["finding_ids"] = ["F1"]  # F3 now orphaned
        res = run(m, self.r); self.expect("E-FINDING-UNLINKED", res)

    def test_finding_evidence_type(self):
        m = copy.deepcopy(self.m); finding_by_id(m, "F1")["evidence_type"] = "website"
        res = run(m, self.r); self.expect("E-FINDING-EVIDENCE-TYPE", res)
        m = copy.deepcopy(self.m); finding_by_id(m, "F1")["evidence_type"] = "source_verified"  # E5 is cited
        self.expect("E-FINDING-EVIDENCE-TYPE", run(m, self.r), present=False)
        m = copy.deepcopy(self.m); f = finding_by_id(m, "F1"); f["evidence_ids"] = []; f["confidence"] = "unknown"; f["evidence_type"] = "manual_note"
        self.expect("E-FINDING-EVIDENCE-TYPE", run(m, self.r), present=False)  # nothing cited: nothing to compare

    def test_finding_pin_mismatch_warning(self):
        m = copy.deepcopy(self.m); finding_by_id(m, "F3")["pin_or_tx"] = "P2"  # E3 is pinned at P1
        res = run(m, self.r); self.expect_warn("W-FINDING-PIN-MISMATCH", res); self.assertEqual(res["result"], "PASS", codes(res))
        m = copy.deepcopy(self.m); ev_by_id(m, "E3")["pin_id"] = None; ev_by_id(m, "E3")["tx_hash"] = ddcore.keccak256_hex(b"tx")
        finding_by_id(m, "F3")["pin_or_tx"] = "P2"
        self.expect_warn("W-FINDING-PIN-MISMATCH", run(m, self.r), present=False)  # a tx-bound row is not 'another pin'

    def test_discovery_only_warning_and_strict(self):
        m = copy.deepcopy(self.m); m["findings"][0]["evidence_ids"] = ["E14", "E15"]; m["findings"][0]["evidence_type"] = "website"
        res = run(m, self.r)
        self.expect_warn("W-EVIDENCE-DISCOVERY-ONLY", res)
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
        r_low = self.r.replace("| sellability_exit_depth | unknown |", "| sellability_exit_depth | low |", 1)
        m = copy.deepcopy(self.m); m["ratings"]["sellability_exit_depth"]["rating"] = "low"
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, r_low))
        m["ratings"]["sellability_exit_depth"].update({"coverage_qualified": True, "coverage_note": "quotes only; historical sell unknown (L1)"})
        self.assertNotIn("E-RATING-UNKNOWN-AS-LOW", codes(run(m, r_low)))
        m = copy.deepcopy(self.m); m["ratings"]["external_dependencies"]["basis_check_ids"] = []
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, self.r))

    def test_rating_unknown_as_low_v11(self):
        """v1.1: not_applicable over a non-not_applicable or empty basis; low with coverage none; short coverage_note."""
        m = copy.deepcopy(self.m); c = check_by_id(m, "G-REWARDS"); c.update({"status": "unknown", "reason": "not run"})
        res = run(m, self.r); self.expect("E-RATING-UNKNOWN-AS-LOW", res)
        self.assertTrue(any("not_applicable" in x for x in messages(res, "E-RATING-UNKNOWN-AS-LOW")))
        m = copy.deepcopy(self.m); m["ratings"]["reward_accounting_liveness"]["basis_check_ids"] = []
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, self.r))
        m = copy.deepcopy(self.m); m["ratings"]["canonical_lp_principal_custody"]["coverage"] = "none"
        res = run(m, self.r); self.expect("E-RATING-UNKNOWN-AS-LOW", res)
        self.assertTrue(any("coverage 'none'" in x for x in messages(res, "E-RATING-UNKNOWN-AS-LOW")))
        r_low = self.r.replace("| sellability_exit_depth | unknown |", "| sellability_exit_depth | low |", 1)
        m = copy.deepcopy(self.m); m["ratings"]["sellability_exit_depth"].update({"rating": "low", "coverage_qualified": True, "coverage_note": "x"})
        res = run(m, r_low); self.expect("E-RATING-UNKNOWN-AS-LOW", res)
        self.assertTrue(any("shorter than" in x for x in messages(res, "E-RATING-UNKNOWN-AS-LOW")))
        m["ratings"]["sellability_exit_depth"]["coverage_note"] = "historical sells unverifiable (L1); quotes only"
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, r_low), present=False)
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"].update({"coverage_qualified": True, "coverage_note": "short"})
        self.expect("E-RATING-UNKNOWN-AS-LOW", run(m, self.r))  # any rating: a qualification must be substantive

    def test_rating_critical_averaged(self):
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = "critical"; m["findings"][0]["severity"] = "critical"
        self.expect("E-RATING-CRITICAL-AVERAGED", run(m, self.r))
        m["ratings"]["token_controls"]["rating"] = "critical"
        r = self.r.replace("| token_controls | high |", "| token_controls | critical |", 1)
        self.assertNotIn("E-RATING-CRITICAL-AVERAGED", codes(run(m, r)))

    def test_rating_critical_averaged_surface_wide(self):
        """v1.1: computed over the whole surface, not only basis_check_ids; through checks or findings alone."""
        r_low = self.r.replace("| token_controls | high |", "| token_controls | low |", 1)
        r_med = self.r.replace("| token_controls | high |", "| token_controls | medium |", 1)
        # (a) the critical check is omitted from basis_check_ids
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = "critical"; finding_by_id(m, "F1")["severity"] = "critical"
        m["ratings"]["token_controls"].update({"rating": "low", "basis_check_ids": ["A-UPGRADE"]})
        res = run(m, r_low); self.expect("E-RATING-CRITICAL-AVERAGED", res); self.expect_warn("W-RATING-BASIS-INCOMPLETE", res)
        # (b) the finding is critical while its check says high
        m = copy.deepcopy(self.m); finding_by_id(m, "F1")["severity"] = "critical"; m["ratings"]["token_controls"]["rating"] = "medium"
        res = run(m, r_med); self.expect("E-RATING-CRITICAL-AVERAGED", res); self.expect("E-CHECK-STATUS", res)
        # (c) a critical finding attached to a pass check
        m = copy.deepcopy(self.m); finding_by_id(m, "F1")["severity"] = "critical"
        c = check_by_id(m, "A-MINT"); c["status"] = "pass"; c["severity"] = None
        m["ratings"]["token_controls"]["rating"] = "low"
        res = run(m, r_low); self.expect("E-RATING-CRITICAL-AVERAGED", res)
        # (d) a critical check severity with no critical finding still binds the rating
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = "critical"
        res = run(m, self.r); self.expect("E-RATING-CRITICAL-AVERAGED", res); self.expect_warn("W-CHECK-SEVERITY-UNSUPPORTED", res)
        # (e) rating unknown over a critical finding is not an escape
        m = copy.deepcopy(self.m); check_by_id(m, "A-MINT")["severity"] = "critical"; finding_by_id(m, "F1")["severity"] = "critical"
        m["ratings"]["token_controls"]["rating"] = "unknown"
        res = run(m, self.r.replace("| token_controls | high |", "| token_controls | unknown |", 1)); self.expect("E-RATING-CRITICAL-AVERAGED", res)

    def test_rating_floor_high_and_medium(self):
        """v1.1: a high finding floors the rating at high; medium at medium; the message names the finding."""
        r_low = self.r.replace("| token_controls | high |", "| token_controls | low |", 1)
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["rating"] = "low"  # F1 is high
        res = run(m, r_low); self.expect("E-RATING-CRITICAL-AVERAGED", res)
        self.assertTrue(any("F1" in x or "A-MINT" in x for x in messages(res, "E-RATING-CRITICAL-AVERAGED")))
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["rating"] = "medium"
        self.expect("E-RATING-CRITICAL-AVERAGED", run(m, self.r.replace("| token_controls | high |", "| token_controls | medium |", 1)))
        m = copy.deepcopy(self.m); m["ratings"]["current_concentration"]["rating"] = "low"  # F2 is medium
        self.expect("E-RATING-CRITICAL-AVERAGED", run(m, self.r.replace("| current_concentration | medium |", "| current_concentration | low |", 1)))
        m = copy.deepcopy(self.m); m["ratings"]["current_concentration"]["rating"] = "high"  # above the floor is allowed
        self.expect("E-RATING-CRITICAL-AVERAGED", run(m, self.r.replace("| current_concentration | medium |", "| current_concentration | high |", 1)), present=False)
        m = copy.deepcopy(self.m); m["ratings"]["current_concentration"]["rating"] = "unknown"  # not numeric: no floor
        self.expect("E-RATING-CRITICAL-AVERAGED", run(m, self.r.replace("| current_concentration | medium |", "| current_concentration | unknown |", 1)), present=False)

    def test_rating_basis_incomplete_warning_and_strict(self):
        m = copy.deepcopy(self.m); m["ratings"]["token_controls"]["basis_check_ids"] = ["A-MINT", "A-UPGRADE"]  # A-ADMIN (finding) omitted
        res = run(m, self.r); self.expect_warn("W-RATING-BASIS-INCOMPLETE", res); self.assertEqual(res["result"], "PASS", codes(res))
        self.assertTrue(any("A-ADMIN" in x for x in messages(res, "W-RATING-BASIS-INCOMPLETE")))
        self.assertEqual(run(copy.deepcopy(m), self.r, strict=True)["result"], "FAIL")

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
                                                "results_labeled_counterfactual": True, "fork_chain_id": 8453, "fork_block": 20000000,
                                                "fork_attestation_path": "attestation.json"})
        res = run(m, self.r); self.assertNotIn("E-DECL-FORK", codes(res))
        m["evidence"][0]["counterfactual"] = False
        self.expect("E-DECL-FORK", run(m, self.r))
        m["evidence"][0]["counterfactual"] = True; m["declarations"]["simulation"]["fork_block"] = None
        self.expect("E-DECL-FORK", run(m, self.r))
        m["declarations"]["simulation"]["fork_block"] = 20000000; m["declarations"]["simulation"]["fork_attestation_path"] = "no-such-attestation.json"
        res = run(m, self.r)
        self.assertNotIn("E-DECL-FORK", codes(res)); self.expect_warn("W-DECL-FORK-ATTESTATION", res)

    def test_decl_fork_v11_chain_and_attestation(self):
        """v1.1: fork_chain_id must equal the target chain; a null attestation path warns (promoted under --strict)."""
        sim = {"used": True, "fork_verified_disposable": True, "synthetic_accounts_only": True, "results_labeled_counterfactual": True,
               "fork_type": "anvil", "fork_block": 20000000, "fork_chain_id": 1, "fork_attestation_path": "attestation.json"}
        m = copy.deepcopy(self.m); m["declarations"]["simulation"].update(sim)
        res = run(m, self.r); self.expect("E-DECL-FORK", res)
        self.assertTrue(any("fork_chain_id" in x for x in messages(res, "E-DECL-FORK")))
        m = copy.deepcopy(self.m); m["declarations"]["simulation"].update(dict(sim, fork_chain_id=8453, fork_attestation_path=None))
        res = run(m, self.r); self.expect("E-DECL-FORK", res, present=False); self.expect_warn("W-DECL-FORK-ATTESTATION", res)
        self.assertEqual(run(copy.deepcopy(m), self.r, strict=True)["result"], "FAIL")
        # an attestation file that exists beside the manifest satisfies the warning
        m = copy.deepcopy(self.m); m["declarations"]["simulation"].update(dict(sim, fork_chain_id=8453))
        res = run_files(m, self.r.encode("utf-8"), extra_files={"attestation.json": b"{}"})
        self.expect_warn("W-DECL-FORK-ATTESTATION", res, present=False); self.assertEqual(res["result"], "PASS", codes(res))

    # --- coverage ---
    def test_limitation_unreferenced_warning(self):
        m = copy.deepcopy(self.m)
        m["coverage"]["limitations"].append({"limitation_id": "L2", "kind": "api_unavailable", "description": "explorer API down",
                                             "affected_check_ids": [], "affected_addresses": [], "retry_attempts": 1})
        self.expect_warn("W-LIMITATION-UNREFERENCED", run(m, self.r))


class CoverageOfCodes(unittest.TestCase):
    def test_every_code_exercised(self):
        # MutationTests may run after this class alphabetically, so exercise the suite explicitly.
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(MutationTests)
        unittest.TextTestRunner(stream=io.StringIO()).run(suite)
        missing = sorted(set(ALL_E_CODES) - COVERED_E_CODES)
        self.assertEqual(missing, [], f"E-codes without a mutation test: {missing}")
        missing_w = sorted(set(ALL_W_CODES) - COVERED_W_CODES)
        self.assertEqual(missing_w, [], f"W-codes without a mutation test: {missing_w}")
        self.assertEqual(len(ALL_E_CODES), 35)
        self.assertEqual(len(ALL_W_CODES), 12)


if __name__ == "__main__":
    unittest.main()
