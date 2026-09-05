#!/usr/bin/env python3
"""ledger.py — render or check the finding-to-evidence ledger from a target-integrity manifest.

Why: the ledger is the part of a report a skeptical reader checks first. Every finding must be
re-derivable from preserved evidence, so the ledger is generated FROM the manifest (one row per finding,
joined to its evidence rows) rather than typed by hand, and a check pass refuses findings that float free
of evidence or point at evidence ids that do not exist.

Usage:
  python3 <skill-root>/scripts/ledger.py render --manifest M.json [--format md|csv]
  python3 <skill-root>/scripts/ledger.py check  --manifest M.json [--json]

Columns (12, in this order; see templates/ledger-columns.md):
  finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis |
  evidence_type | confidence | alternatives | coverage | stale_conditions

  artifact_or_query concatenates, for each evidence id the finding cites, "E<n> <artifact path>" plus the
  reproducible query when it is a string or a small object (credentials must already be redacted in the
  manifest). pin_or_tx, chain_id, address, evidence_type, confidence and the free-text columns come from the
  finding itself. A dangling evidence id renders as "E<n> (DANGLING)".

check exits 1 on:
  E-FINDING-NO-EVIDENCE   a finding cites no evidence (allowed only when confidence is "unknown")
  E-EVIDENCE-DANGLING     a finding or check cites an evidence id that does not exist; a check cites a finding id
                          that does not exist
  E-SCHEMA                duplicate finding_id / evidence_id, or findings/evidence entries that are not objects
Warnings (exit unaffected):
  W-EVIDENCE-UNREFERENCED an evidence row is never referenced by any finding or check
  W-LEDGER-FIELD-EMPTY    a free-text ledger column (alternatives / coverage / stale_conditions) is empty

Exit codes: render 0 (2 on unreadable manifest); check 0 clean, 1 errors, 2 unreadable manifest.
This check is narrower than validate_report.py; run both. Passing here validates internal linkage only,
not RPC honesty, discovery completeness, or protocol safety.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

COLUMNS = ["finding_id", "proposition", "chain_id", "address", "pin_or_tx", "artifact_or_query",
           "decoding_basis", "evidence_type", "confidence", "alternatives", "coverage", "stale_conditions"]

_ID_NUM = re.compile(r"^[A-Z]+([0-9]+)$")


class ManifestError(ValueError):
    """Unreadable manifest (exit 2)."""


# --------------------------------------------------------------------------------------
# Loading / indexing
# --------------------------------------------------------------------------------------
def load_manifest(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise ManifestError(f"cannot read manifest: {e}")
    except json.JSONDecodeError as e:
        raise ManifestError(f"manifest is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise ManifestError("manifest must be a JSON object")
    return data


def _list(manifest: dict, key: str) -> list:
    v = manifest.get(key)
    return v if isinstance(v, list) else []


def _id_sort_key(id_str: Any) -> tuple:
    m = _ID_NUM.match(str(id_str))
    return (0, int(m.group(1)), str(id_str)) if m else (1, 0, str(id_str))


def evidence_index(manifest: dict) -> dict[str, dict]:
    idx: dict[str, dict] = {}
    for e in _list(manifest, "evidence"):
        if isinstance(e, dict) and isinstance(e.get("evidence_id"), str) and e["evidence_id"] not in idx:
            idx[e["evidence_id"]] = e
    return idx


# --------------------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------------------
def _query_text(q: Any) -> str:
    if q is None or q == "":
        return ""
    if isinstance(q, str):
        return q
    try:
        return json.dumps(q, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError):
        return str(q)


def artifact_or_query(finding: dict, evidence_by_id: dict[str, dict]) -> str:
    parts = []
    for eid in finding.get("evidence_ids") or []:
        ev = evidence_by_id.get(eid)
        if ev is None:
            parts.append(f"{eid} (DANGLING)")
            continue
        piece = f"{eid} {ev.get('artifact') or 'inline'}"
        qt = _query_text(ev.get("query"))
        if qt:
            piece += f" [{qt}]"
        parts.append(piece)
    return "; ".join(parts)


def decoding_basis(finding: dict, evidence_by_id: dict[str, dict]) -> str:
    bases: list[str] = []
    for eid in finding.get("evidence_ids") or []:
        ev = evidence_by_id.get(eid)
        if ev and isinstance(ev.get("decoding_basis"), str) and ev["decoding_basis"] and ev["decoding_basis"] not in bases:
            bases.append(ev["decoding_basis"])
    return "; ".join(bases)


def _cell(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def ledger_rows(manifest: dict) -> list[dict]:
    """One row per finding (manifest order sorted by finding number), values as strings."""
    ev_idx = evidence_index(manifest)
    rows = []
    findings = [f for f in _list(manifest, "findings") if isinstance(f, dict)]
    for f in sorted(findings, key=lambda x: _id_sort_key(x.get("finding_id"))):
        rows.append({
            "finding_id": _cell(f.get("finding_id")),
            "proposition": _cell(f.get("proposition")),
            "chain_id": _cell(f.get("chain_id")),
            "address": _cell(f.get("address")),
            "pin_or_tx": _cell(f.get("pin_or_tx")),
            "artifact_or_query": artifact_or_query(f, ev_idx),
            "decoding_basis": decoding_basis(f, ev_idx),
            "evidence_type": _cell(f.get("evidence_type")),
            "confidence": _cell(f.get("confidence")),
            "alternatives": _cell(f.get("alternatives")),
            "coverage": _cell(f.get("coverage")),
            "stale_conditions": _cell(f.get("stale_conditions")),
        })
    return rows


def _md_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def render_md(rows: list[dict]) -> str:
    out = ["| " + " | ".join(COLUMNS) + " |", "|" + "---|" * len(COLUMNS)]
    for r in rows:
        out.append("| " + " | ".join(_md_escape(r[c]) for c in COLUMNS) + " |")
    if not rows:
        out.append("| (no findings in manifest) |" + " |" * (len(COLUMNS) - 1))
    return "\n".join(out)


def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({c: r[c] for c in COLUMNS})
    return buf.getvalue().rstrip("\n")


# --------------------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------------------
def check_manifest(manifest: dict) -> dict:
    errors: list[dict] = []
    warnings: list[dict] = []

    def err(code: str, where: str, msg: str) -> None:
        errors.append({"code": code, "where": where, "message": msg})

    def warn(code: str, where: str, msg: str) -> None:
        warnings.append({"code": code, "where": where, "message": msg})

    evidence_ids: list[str] = []
    for i, e in enumerate(_list(manifest, "evidence")):
        if not isinstance(e, dict) or not isinstance(e.get("evidence_id"), str):
            err("E-SCHEMA", f"evidence[{i}]", "evidence entry is not an object with a string evidence_id")
            continue
        if e["evidence_id"] in evidence_ids:
            err("E-SCHEMA", e["evidence_id"], f"duplicate evidence_id {e['evidence_id']}")
        evidence_ids.append(e["evidence_id"])
    evidence_set = set(evidence_ids)

    finding_ids: list[str] = []
    referenced_evidence: set[str] = set()
    for i, f in enumerate(_list(manifest, "findings")):
        if not isinstance(f, dict) or not isinstance(f.get("finding_id"), str):
            err("E-SCHEMA", f"findings[{i}]", "finding entry is not an object with a string finding_id")
            continue
        fid = f["finding_id"]
        if fid in finding_ids:
            err("E-SCHEMA", fid, f"duplicate finding_id {fid}")
        finding_ids.append(fid)
        eids = f.get("evidence_ids")
        if not isinstance(eids, list):
            err("E-SCHEMA", fid, "evidence_ids must be a list")
            eids = []
        for eid in eids:
            if eid in evidence_set:
                referenced_evidence.add(eid)
            else:
                err("E-EVIDENCE-DANGLING", fid, f"finding {fid} cites evidence {eid} which does not exist")
        if not eids:
            if f.get("confidence") == "unknown":
                warn("W-LEDGER-FIELD-EMPTY", fid, f"finding {fid} has no evidence; allowed only because confidence is unknown")
            else:
                err("E-FINDING-NO-EVIDENCE", fid, f"finding {fid} (confidence {f.get('confidence')!r}) cites no evidence")
        for col in ("alternatives", "coverage", "stale_conditions"):
            v = f.get(col)
            if not isinstance(v, str) or not v.strip():
                warn("W-LEDGER-FIELD-EMPTY", fid, f"finding {fid}: ledger column {col} is empty")
    finding_set = set(finding_ids)

    for i, c in enumerate(_list(manifest, "checks")):
        if not isinstance(c, dict):
            err("E-SCHEMA", f"checks[{i}]", "check entry is not an object")
            continue
        cid = str(c.get("check_id") or f"checks[{i}]")
        for eid in c.get("evidence_ids") or []:
            if eid in evidence_set:
                referenced_evidence.add(eid)
            else:
                err("E-EVIDENCE-DANGLING", cid, f"check {cid} cites evidence {eid} which does not exist")
        for fid in c.get("finding_ids") or []:
            if fid not in finding_set:
                err("E-EVIDENCE-DANGLING", cid, f"check {cid} cites finding {fid} which does not exist")

    # metadata evidence pointers count as references too
    meta = (manifest.get("target") or {}).get("metadata") if isinstance(manifest.get("target"), dict) else None
    if isinstance(meta, dict):
        for field in meta.values():
            if isinstance(field, dict) and isinstance(field.get("evidence_id"), str) and field["evidence_id"] in evidence_set:
                referenced_evidence.add(field["evidence_id"])

    for eid in evidence_ids:
        if eid not in referenced_evidence:
            warn("W-EVIDENCE-UNREFERENCED", eid, f"evidence {eid} is never referenced by any finding or check")

    return {
        "result": "PASS" if not errors else "FAIL",
        "counts": {"findings": len(finding_ids), "evidence": len(evidence_ids), "checks": len(_list(manifest, "checks")),
                   "errors": len(errors), "warnings": len(warnings)},
        "errors": errors,
        "warnings": warnings,
        "exit_code": EXIT_OK if not errors else EXIT_FAIL,
        "note": "ledger check validates finding<->evidence linkage only; run validate_report.py for the full "
                "target-integrity validation. Neither proves RPC honesty, discovery completeness, or protocol safety.",
    }


def render_check(res: dict) -> str:
    lines = [f"ledger check: {res['result']} (findings={res['counts']['findings']} evidence={res['counts']['evidence']} "
             f"checks={res['counts']['checks']} errors={res['counts']['errors']} warnings={res['counts']['warnings']})"]
    for e in res["errors"]:
        lines.append(f"  ERROR {e['code']} [{e['where']}] {e['message']}")
    for w in res["warnings"]:
        lines.append(f"  WARN  {w['code']} [{w['where']}] {w['message']}")
    lines.append(res["note"])
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Render or check the finding-to-evidence ledger from a manifest.")
    sub = ap.add_subparsers(dest="command")
    pr = sub.add_parser("render", help="print the 12-column ledger")
    pr.add_argument("--manifest", required=True)
    pr.add_argument("--format", choices=["md", "csv"], default="md")
    pc = sub.add_parser("check", help="exit 1 on findings without evidence or dangling ids")
    pc.add_argument("--manifest", required=True)
    pc.add_argument("--json", action="store_true")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        manifest = load_manifest(args.manifest)
    except ManifestError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "render":
        rows = ledger_rows(manifest)
        print(render_md(rows) if args.format == "md" else render_csv(rows))
        return EXIT_OK
    res = check_manifest(manifest)
    print(json.dumps(res, indent=2) if args.json else render_check(res))
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
