#!/usr/bin/env python3
"""selftest.py — run every unit test under tests/ and validate every fixture pair.

Usage: python3 <skill-root>/scripts/selftest.py [--quiet]
Exit 0 only when all discovered tests pass AND every tests/fixtures/*/ pair matches its expected.json
(valid must pass; reject-* must fail with the expected E-codes and none of the forbidden ones).
Missing test files are simply not discovered; they do not fail the run.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import validate_report as vr  # noqa: E402


def run_unit_tests(quiet: bool) -> tuple[int, int, int, int]:
    """Returns (tests_run, failures, errors, skipped)."""
    if not TESTS.is_dir():
        print(f"no tests directory at {TESTS}")
        return 0, 0, 0, 0
    loader = unittest.defaultTestLoader
    suite = loader.discover(str(TESTS), pattern="test_*.py", top_level_dir=str(TESTS))
    stream = io.StringIO() if quiet else sys.stdout
    result = unittest.TextTestRunner(stream=stream, verbosity=1 if quiet else 2).run(suite)
    if quiet and not result.wasSuccessful():
        print(stream.getvalue())
    return result.testsRun, len(result.failures), len(result.errors), len(result.skipped)


def run_fixtures() -> tuple[list[dict], bool]:
    rows = []
    ok_all = True
    if not FIXTURES.is_dir():
        return rows, True
    for d in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        exp_path = d / "expected.json"
        man_path = d / "manifest.json"
        if not exp_path.is_file() or not man_path.is_file():
            continue
        with open(exp_path, "r", encoding="utf-8") as f:
            exp = json.load(f)
        res = vr.validate(str(man_path))
        got = sorted({e["code"] for e in res["errors"]})
        want_result = "PASS" if exp.get("expect") == "pass" else "FAIL"
        missing = [c for c in exp.get("expected_codes", []) if c not in got]
        forbidden = [c for c in exp.get("forbidden_codes", []) if c in got]
        ok = res["result"] == want_result and not missing and not forbidden
        ok_all = ok_all and ok
        rows.append({"fixture": d.name, "expected": want_result, "got": res["result"], "codes": got,
                     "missing": missing, "forbidden_hit": forbidden, "ok": ok,
                     "warnings": sorted({w["code"] for w in res["warnings"]})})
    return rows, ok_all


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("no fixtures found")
        return
    w = max(len(r["fixture"]) for r in rows)
    print(f"{'fixture'.ljust(w)}  expected  got   ok    codes")
    for r in rows:
        extra = ""
        if r["missing"]:
            extra += f"  MISSING={r['missing']}"
        if r["forbidden_hit"]:
            extra += f"  FORBIDDEN={r['forbidden_hit']}"
        print(f"{r['fixture'].ljust(w)}  {r['expected'].ljust(8)}  {r['got'].ljust(4)}  {'yes' if r['ok'] else 'NO '}   "
              f"{', '.join(r['codes']) or '-'}{extra}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run unit tests and validate fixtures for evm-token-due-diligence.")
    ap.add_argument("--quiet", action="store_true", help="suppress per-test output unless something fails")
    args = ap.parse_args(argv)
    print("== unit tests ==")
    n, failures, errors, skipped = run_unit_tests(args.quiet)
    print(f"tests run: {n}, failures: {failures}, errors: {errors}, skipped: {skipped}")
    print("\n== fixtures ==")
    rows, fixtures_ok = run_fixtures()
    print_table(rows)
    print()
    print(vr.CLOSING_NOTE)
    ok = failures == 0 and errors == 0 and fixtures_ok
    print(f"\nSELFTEST: {'OK' if ok else 'FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
