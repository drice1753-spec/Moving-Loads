#!/usr/bin/env python3
"""reconcile.py — asset flow reconciliation in integer base units.

Why: "where did the fees / treasury / proceeds go" is only answerable as an identity that closes:
    opening balance + inflows + explained adjustments = outflows + closing balance + unexplained delta
Anything that does not close is an unexplained delta that must be bounded explicitly, not narrated away.
This tool computes that identity for ONE asset from a flows file and fails (exit 1) when the unexplained
delta exceeds the tolerance you declare.

Usage:
  python3 <skill-root>/scripts/reconcile.py --flows flows.json [--tolerance N] [--json]

Flows file (all amounts are INTEGER BASE UNITS as decimal strings; never floats, never human units):
  {
    "asset":   {"symbol": "…", "address": "0x…" | null, "chain_id": N, "is_native": false, "decimals": 18},
    "opening": {"balance": "int", "block": N},
    "closing": {"balance": "int", "block": N},
    "rows": [
      {"tx": "0x…", "status": "success|reverted", "direction": "in|out|transform_in|transform_out|gas|adjustment",
       "amount": "int", "block": N, "counterparty": "0x…", "note": ""}
    ]
  }

Rules (each one exists to stop a known double-count or mislabel):
  * status "reverted": EXCLUDED from every sum (a reverted transfer moved nothing) but listed under
    excluded_reverted with its tx so the reader sees it was considered.
  * in: adds. out: subtracts.
  * transform_in / transform_out: a wrap, unwrap, bridge leg or burn-for-claim. They must be paired by identical
    tx hash (one leg in, the other leg out). A transform row whose pair is not in the same file is still applied
    (so the delta stays visible) but reported under unpaired_transforms with a warning; the counter-leg is then
    expected in the OTHER asset's flows file — reconcile that file too.
  * gas: subtracts only when asset.is_native is true; on any other asset it is an error, because gas cannot be
    paid in a non-native asset.
  * adjustment: a signed amount (e.g. "-5" or "5") with a mandatory non-empty note — an "explained adjustment"
    such as a rebase, a fee-on-transfer haircut, or a rounding correction. Unexplained adjustments are not allowed;
    that is what unexplained_delta is for.
  * duplicate (tx, direction, amount) rows among applied rows are reported as a warning (possible double count).

Computation:
  computed_closing = opening + sum(in) + sum(transform_in) + sum(adjustment)
                              - sum(out) - sum(transform_out) - sum(gas)
  unexplained_delta = declared_closing - computed_closing
      (positive: the account holds MORE than the explained flows account for; negative: less)
  identity line: opening + inflows + adjustments = outflows + closing + unexplained
      where inflows = in + transform_in, outflows = out + transform_out + gas, closing = declared closing and the
      printed unexplained term is computed_closing - declared_closing (= -unexplained_delta) so both sides are equal.

Exit codes: 0 |unexplained_delta| <= tolerance, 1 exceeds tolerance, 2 usage / malformed flows file.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from ddcore import is_hex32, validate_address  # noqa: E402

EXIT_OK = 0
EXIT_UNEXPLAINED = 1
EXIT_USAGE = 2

DIRECTIONS = ("in", "out", "transform_in", "transform_out", "gas", "adjustment")
STATUSES = ("success", "reverted")
_INT_RE = re.compile(r"^[+-]?[0-9]+$")


class FlowsError(ValueError):
    """Malformed flows input (exit 2)."""


# --------------------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------------------
def parse_amount(value: Any, label: str, allow_negative: bool) -> int:
    """Integer base units only. Accepts int or a decimal-integer string; rejects floats and exponents."""
    if isinstance(value, bool):
        raise FlowsError(f"{label}: amount must be an integer string, got boolean")
    if isinstance(value, int):
        n = value
    elif isinstance(value, str) and _INT_RE.match(value.strip()):
        n = int(value.strip())
    else:
        raise FlowsError(f"{label}: amount must be an integer in base units as a decimal string, got {value!r}")
    if n < 0 and not allow_negative:
        raise FlowsError(f"{label}: amount must be non-negative for this direction (got {n}); use direction to express sign")
    return n


def load_flows(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except OSError as e:
        raise FlowsError(f"cannot read flows file: {e}")
    except json.JSONDecodeError as e:
        raise FlowsError(f"flows file is not valid JSON: {e}")
    if not isinstance(data, dict):
        raise FlowsError("flows file must be a JSON object")
    return data


def _balance_block(section: Any, label: str) -> tuple[int, Optional[int]]:
    if not isinstance(section, dict) or "balance" not in section:
        raise FlowsError(f"{label} must be an object with a 'balance' (integer string) and optional 'block'")
    bal = parse_amount(section["balance"], f"{label}.balance", allow_negative=False)
    blk = section.get("block")
    if blk is not None and (isinstance(blk, bool) or not isinstance(blk, int) or blk < 0):
        raise FlowsError(f"{label}.block must be a non-negative integer or null")
    return bal, blk


# --------------------------------------------------------------------------------------
# Core
# --------------------------------------------------------------------------------------
def reconcile(flows: dict, tolerance: int = 0) -> dict:
    if tolerance < 0:
        raise FlowsError("tolerance must be >= 0")
    asset = flows.get("asset")
    if not isinstance(asset, dict):
        raise FlowsError("'asset' must be an object (symbol, address, chain_id, is_native, decimals)")
    is_native = bool(asset.get("is_native", False))
    addr = asset.get("address")
    if addr is not None:
        ok, reason = validate_address(addr)
        if not ok:
            raise FlowsError(f"asset.address: {reason}")
    if is_native and addr not in (None, ""):
        raise FlowsError("asset.is_native is true but asset.address is set; a native asset has no contract address")
    opening, opening_block = _balance_block(flows.get("opening"), "opening")
    closing, closing_block = _balance_block(flows.get("closing"), "closing")
    rows = flows.get("rows")
    if not isinstance(rows, list):
        raise FlowsError("'rows' must be a list")

    sums = {d: 0 for d in DIRECTIONS}
    counts = {d: 0 for d in DIRECTIONS}
    applied: list[dict] = []
    excluded_reverted: list[dict] = []
    warnings: list[str] = []

    for i, row in enumerate(rows):
        label = f"rows[{i}]"
        if not isinstance(row, dict):
            raise FlowsError(f"{label}: must be an object")
        status = row.get("status")
        if status not in STATUSES:
            raise FlowsError(f"{label}: status must be one of {STATUSES}, got {status!r}")
        direction = row.get("direction")
        if direction not in DIRECTIONS:
            raise FlowsError(f"{label}: direction must be one of {DIRECTIONS}, got {direction!r}")
        tx = row.get("tx")
        if direction != "adjustment":
            if not isinstance(tx, str) or not tx:
                raise FlowsError(f"{label}: tx is required for direction {direction}")
            if not is_hex32(tx):
                warnings.append(f"{label}: tx {tx!r} is not a 0x+64-hex transaction hash; keep it only if it is a documented synthetic id")
        elif tx is not None and tx != "" and not is_hex32(tx):
            warnings.append(f"{label}: adjustment tx {tx!r} is not a 0x+64-hex transaction hash")
        amount = parse_amount(row.get("amount"), f"{label}.amount", allow_negative=(direction == "adjustment"))
        note = row.get("note")
        if direction == "adjustment" and (not isinstance(note, str) or not note.strip()):
            raise FlowsError(f"{label}: adjustment rows require a non-empty note (explained adjustment)")
        if direction == "gas" and not is_native:
            raise FlowsError(f"{label}: gas cannot be paid in a non-native asset (asset.is_native is false)")
        cp = row.get("counterparty")
        if cp not in (None, ""):
            ok, reason = validate_address(cp)
            if not ok:
                raise FlowsError(f"{label}.counterparty: {reason}")
        blk = row.get("block")
        if blk is not None and (isinstance(blk, bool) or not isinstance(blk, int)):
            raise FlowsError(f"{label}.block must be an integer or null")

        entry = {"index": i, "tx": tx, "direction": direction, "amount": str(amount), "block": blk,
                 "counterparty": cp, "note": note if isinstance(note, str) else None}
        if status == "reverted":
            excluded_reverted.append(entry)
            continue
        if blk is not None:
            if opening_block is not None and blk <= opening_block:
                warnings.append(f"{label}: block {blk} is at or before the opening block {opening_block}; the opening balance already includes it?")
            if closing_block is not None and blk > closing_block:
                warnings.append(f"{label}: block {blk} is after the closing block {closing_block}; outside the reconciliation window")
        sums[direction] += amount
        counts[direction] += 1
        applied.append(entry)

    # transform pairing by tx hash
    tx_dirs: dict[str, set[str]] = {}
    for e in applied:
        if e["direction"] in ("transform_in", "transform_out") and e["tx"]:
            tx_dirs.setdefault(e["tx"], set()).add(e["direction"])
    unpaired = [e for e in applied if e["direction"] in ("transform_in", "transform_out")
                and tx_dirs.get(e["tx"], set()) != {"transform_in", "transform_out"}]
    for e in unpaired:
        warnings.append(f"unpaired transform: rows[{e['index']}] {e['direction']} {e['amount']} tx {e['tx']} has no counter-leg "
                        "in this file; applied anyway so the delta stays visible (reconcile the other asset's file)")

    # duplicate detection (possible double count)
    seen: dict[tuple, int] = {}
    duplicates: list[dict] = []
    for e in applied:
        if e["direction"] == "adjustment":
            continue
        key = (e["tx"], e["direction"], e["amount"])
        if key in seen:
            duplicates.append({"tx": e["tx"], "direction": e["direction"], "amount": e["amount"],
                               "first_index": seen[key], "duplicate_index": e["index"]})
        else:
            seen[key] = e["index"]
    for d in duplicates:
        warnings.append(f"duplicate row: rows[{d['duplicate_index']}] repeats rows[{d['first_index']}] "
                        f"({d['direction']} {d['amount']} tx {d['tx']}); possible double count")

    inflows = sums["in"] + sums["transform_in"]
    outflows = sums["out"] + sums["transform_out"] + sums["gas"]
    adjustments = sums["adjustment"]
    computed_closing = opening + inflows + adjustments - outflows
    unexplained_delta = closing - computed_closing
    identity_term = computed_closing - closing  # so that opening + inflows + adjustments == outflows + closing + term
    within = abs(unexplained_delta) <= tolerance

    return {
        "asset": asset,
        "opening": {"balance": str(opening), "block": opening_block},
        "declared_closing": {"balance": str(closing), "block": closing_block},
        "computed_closing": str(computed_closing),
        "sums": {k: str(v) for k, v in sums.items()},
        "row_counts": {"total": len(rows), "applied": len(applied), "reverted_excluded": len(excluded_reverted), **counts},
        "inflows": str(inflows),
        "outflows": str(outflows),
        "adjustments": str(adjustments),
        "unexplained_delta": str(unexplained_delta),
        "unexplained_delta_sign_note": "declared_closing - computed_closing; positive = holds more than explained flows account for",
        "tolerance": str(tolerance),
        "within_tolerance": within,
        "identity": {
            "text": "opening + inflows + adjustments = outflows + closing + unexplained",
            "lhs": str(opening + inflows + adjustments),
            "rhs": str(outflows + closing + identity_term),
            "unexplained_term": str(identity_term),
            "unexplained_term_note": "computed_closing - declared_closing (= -unexplained_delta) so both sides balance",
        },
        "excluded_reverted": excluded_reverted,
        "unpaired_transforms": unpaired,
        "duplicates": duplicates,
        "warnings": warnings,
        "exit_code": EXIT_OK if within else EXIT_UNEXPLAINED,
    }


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
def render_human(res: dict) -> str:
    a = res["asset"]
    ident = res["identity"]
    lines = [
        "reconcile.py — asset flow reconciliation (integer base units)",
        f"asset     : {a.get('symbol') or '?'} {a.get('address') or '(native)'} chain_id={a.get('chain_id')} "
        f"is_native={bool(a.get('is_native', False))} decimals={a.get('decimals')}",
        f"opening   : {res['opening']['balance']} @ block {res['opening']['block']}",
        f"closing   : {res['declared_closing']['balance']} @ block {res['declared_closing']['block']} (declared)",
        f"rows      : {res['row_counts']['total']} total, {res['row_counts']['applied']} applied, "
        f"{res['row_counts']['reverted_excluded']} reverted (excluded)",
        f"sums      : in={res['sums']['in']} transform_in={res['sums']['transform_in']} adjustment={res['sums']['adjustment']} "
        f"out={res['sums']['out']} transform_out={res['sums']['transform_out']} gas={res['sums']['gas']}",
        f"computed closing = {res['computed_closing']}",
        "",
        "identity: opening + inflows + adjustments = outflows + closing + unexplained",
        f"          {res['opening']['balance']} + {res['inflows']} + {res['adjustments']} = {res['outflows']} + "
        f"{res['declared_closing']['balance']} + ({ident['unexplained_term']})    [lhs {ident['lhs']} == rhs {ident['rhs']}]",
        f"unexplained_delta (declared_closing - computed_closing) = {res['unexplained_delta']}  "
        f"tolerance = {res['tolerance']}  -> {'WITHIN tolerance' if res['within_tolerance'] else 'EXCEEDS tolerance (exit 1)'}",
    ]
    if res["excluded_reverted"]:
        lines.append("")
        lines.append("excluded reverted rows (moved nothing):")
        for e in res["excluded_reverted"]:
            lines.append(f"  rows[{e['index']}] {e['direction']} {e['amount']} tx {e['tx']}")
    if res["unpaired_transforms"]:
        lines.append("")
        lines.append("unpaired transforms (applied; counter-leg expected in the other asset's file):")
        for e in res["unpaired_transforms"]:
            lines.append(f"  rows[{e['index']}] {e['direction']} {e['amount']} tx {e['tx']}")
    if res["duplicates"]:
        lines.append("")
        lines.append("duplicate (tx, direction, amount) rows — possible double count:")
        for d in res["duplicates"]:
            lines.append(f"  rows[{d['duplicate_index']}] repeats rows[{d['first_index']}]: {d['direction']} {d['amount']} tx {d['tx']}")
    if res["warnings"]:
        lines.append("")
        lines.append("warnings:")
        for w in res["warnings"]:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append("note: a closing identity proves the flows in this file account for the balance change; it does not prove "
                 "the rows are complete, that a counterparty is a sale, or who benefited. Stop exact attribution at commingling.")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile one asset's flows: opening + inflows + adjustments = outflows + closing + unexplained.")
    ap.add_argument("--flows", required=True, help="flows JSON file (integer base units)")
    ap.add_argument("--tolerance", type=int, default=0, help="max |unexplained_delta| in base units that still exits 0 (default 0)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the human summary")
    args = ap.parse_args(argv)
    try:
        flows = load_flows(args.flows)
        res = reconcile(flows, args.tolerance)
    except FlowsError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(render_human(res))
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
