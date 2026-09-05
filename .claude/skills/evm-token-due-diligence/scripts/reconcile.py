#!/usr/bin/env python3
"""reconcile.py — asset flow reconciliation in integer base units.

Why: "where did the fees / treasury / proceeds go" is only answerable as an identity that closes:
    opening balance + inflows + explained adjustments = outflows + closing balance + unexplained delta
Anything that does not close is an unexplained delta that must be bounded explicitly, not narrated away.
This tool computes that identity for ONE asset from a flows file and fails (exit 1) when the unexplained
delta exceeds the tolerance you declare.

Usage:
  python3 <skill-root>/scripts/reconcile.py --flows flows.json [--tolerance N] [--json]
  python3 <skill-root>/scripts/reconcile.py --flows weth.json native.json [--tolerance N] [--json]
      (two or more files: each asset is reconciled on its own and transform legs are PAIRED ACROSS the files)

Flows file (canonical shape; all amounts are INTEGER BASE UNITS as decimal strings; never floats, never
human units):
  { "asset": {"chain_id": N, "address": "0x…" | null, "is_native": false | true, "symbol": "<label>", "decimals": 18},
    "opening": {"balance": "<int>", "block": N}, "closing": {"balance": "<int>", "block": N},
    "rows": [ {"tx": "0x…", "status": "success|reverted", "direction": "in|out|transform_in|transform_out|gas|adjustment",
               "amount": "<int>", "block": N, "counterparty": "0x…"|null, "note": ""} ] }

  Native file: "address": null, "is_native": true; gas rows are accepted only there. One file per asset.
  Transforms pair by tx hash ACROSS asset files when the files are supplied together in one run
  (`--flows weth.json native.json`); a single-file run can only pair within that file and lists every other
  leg as unpaired for manual pairing.

Rules (each one exists to stop a known double-count or mislabel):
  * status "reverted": EXCLUDED from every sum (a reverted transfer moved nothing) but listed under
    excluded_reverted with its tx so the reader sees it was considered. Reverted VALUE rows are excluded; the
    gas of a reverted transaction is still spent, so record it as a separate `gas` row with status "success".
    A `gas` row marked reverted is excluded like any reverted row and raises W-GAS-ROW-REVERTED so the
    resulting unexplained delta is not chased elsewhere.
  * in: adds. out: subtracts.
  * transform_in / transform_out: a wrap, unwrap, bridge leg or burn-for-claim. They must be paired by identical
    tx hash (one leg in, the other leg out; normally in two different asset files). A leg whose counter-leg is
    not in any supplied file is still applied (so the delta stays visible) but reported under
    unpaired_transforms with a warning. Paired legs whose amounts differ are reported in transform_pairing
    (expected for bridge fees, unexpected for a wrap/unwrap).
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
      printed unexplained term is computed_closing - declared_closing (= -unexplained_delta) so both sides are equal;
      the sign convention of the JSON field `unexplained_delta` is stated on the line after the identity.

Warnings (never change the exit code): unpaired transform, duplicate row, row outside the opening..closing window,
non-hash tx id, W-GAS-ROW-REVERTED (a gas row marked reverted was excluded; gas is spent even on revert).

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
def collect_transform_legs(flows: dict) -> list[dict]:
    """Non-reverted transform legs of one flows file (light validation; reconcile() validates fully)."""
    legs = []
    rows = flows.get("rows")
    if not isinstance(rows, list):
        return legs
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or row.get("status") == "reverted":
            continue
        d = row.get("direction")
        tx = row.get("tx")
        if d in ("transform_in", "transform_out") and isinstance(tx, str) and tx:
            legs.append({"index": i, "tx": tx, "direction": d, "amount": str(row.get("amount"))})
    return legs


def pair_scope_from(files: list[dict]) -> dict[str, set[str]]:
    """tx hash -> set of transform directions seen across ALL supplied files."""
    scope: dict[str, set[str]] = {}
    for flows in files:
        for leg in collect_transform_legs(flows):
            scope.setdefault(leg["tx"], set()).add(leg["direction"])
    return scope


def reconcile(flows: dict, tolerance: int = 0, pair_scope: Optional[dict] = None) -> dict:
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
            if direction == "gas":
                warnings.append(
                    f"W-GAS-ROW-REVERTED rows[{i}]: gas row {amount} tx {tx} is marked reverted and was EXCLUDED like every reverted "
                    "row, but gas is spent even when a transaction reverts. If this is the gas of a reverted transaction, record it "
                    "as a separate gas row with status success (keep the reverted VALUE row as reverted); the unexplained delta "
                    "below may be exactly this amount")
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

    # transform pairing by tx hash (within this file, or across every file supplied in the same run)
    tx_dirs: dict[str, set[str]] = {}
    for e in applied:
        if e["direction"] in ("transform_in", "transform_out") and e["tx"]:
            tx_dirs.setdefault(e["tx"], set()).add(e["direction"])
    if pair_scope is not None:
        for tx, dirs in pair_scope.items():
            tx_dirs.setdefault(tx, set()).update(dirs)
    unpaired = [e for e in applied if e["direction"] in ("transform_in", "transform_out")
                and tx_dirs.get(e["tx"], set()) != {"transform_in", "transform_out"}]
    where = "in any supplied file" if pair_scope is not None else "in this file"
    hint = ("" if pair_scope is not None
            else " (supply the other asset's flows file in the same run, e.g. --flows weth.json native.json, to pair across files)")
    for e in unpaired:
        warnings.append(f"unpaired transform: rows[{e['index']}] {e['direction']} {e['amount']} tx {e['tx']} has no counter-leg "
                        f"{where}; applied anyway so the delta stays visible{hint}")

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
        "pairing_scope": "all supplied files" if pair_scope is not None else "this file only",
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
        lines.append(f"unpaired transforms (applied; no counter-leg found in {res.get('pairing_scope', 'this file only')}):")
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


def transform_pairing_summary(files: list[tuple[str, dict]]) -> dict:
    """Cross-file view of transform legs: paired txs (with amount comparison) and unpaired legs."""
    legs_by_tx: dict[str, list[dict]] = {}
    for path, flows in files:
        for leg in collect_transform_legs(flows):
            legs_by_tx.setdefault(leg["tx"], []).append({**leg, "file": path})
    paired, unpaired = [], []
    for tx, legs in legs_by_tx.items():
        dirs = {l["direction"] for l in legs}
        if dirs == {"transform_in", "transform_out"}:
            ins = [l for l in legs if l["direction"] == "transform_in"]
            outs = [l for l in legs if l["direction"] == "transform_out"]
            amounts_equal = {l["amount"] for l in ins} == {l["amount"] for l in outs}
            paired.append({"tx": tx, "legs": legs, "amounts_equal": amounts_equal,
                           "note": None if amounts_equal else
                           "amounts differ between legs: expected for bridge fees or burn-for-claim, unexpected for a wrap/unwrap"})
        else:
            unpaired.extend(legs)
    return {"paired": paired, "unpaired": unpaired}


def render_pairing_human(summary: dict) -> str:
    lines = ["", "cross-file transform pairing:"]
    if not summary["paired"] and not summary["unpaired"]:
        lines.append("  (no transform legs in the supplied files)")
    for p in summary["paired"]:
        legs = "; ".join(f"{l['direction']} {l['amount']} [{l['file']} rows[{l['index']}]]" for l in p["legs"])
        flag = "" if p["amounts_equal"] else f"  <- {p['note']}"
        lines.append(f"  paired   tx {p['tx']}: {legs}{flag}")
    for l in summary["unpaired"]:
        lines.append(f"  UNPAIRED tx {l['tx']}: {l['direction']} {l['amount']} [{l['file']} rows[{l['index']}]]")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Reconcile one asset's flows: opening + inflows + adjustments = outflows + closing + unexplained. "
                                             "Give several flows files (one per asset) in one run to pair transform legs across them.")
    ap.add_argument("--flows", required=True, nargs="+", metavar="FLOWS",
                    help="flows JSON file(s) (integer base units); one file per asset, several files pair transforms across assets")
    ap.add_argument("--tolerance", type=int, default=0, help="max |unexplained_delta| in base units that still exits 0 (default 0)")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the human summary")
    args = ap.parse_args(argv)
    try:
        loaded = [(path, load_flows(path)) for path in args.flows]
        if len(loaded) == 1:
            res = reconcile(loaded[0][1], args.tolerance)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(render_human(res))
            return res["exit_code"]
        scope = pair_scope_from([f for _, f in loaded])
        results = []
        for path, flows in loaded:
            try:
                results.append({"path": path, "result": reconcile(flows, args.tolerance, pair_scope=scope)})
            except FlowsError as e:
                raise FlowsError(f"{path}: {e}")
        summary = transform_pairing_summary(loaded)
    except FlowsError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    exit_code = max(r["result"]["exit_code"] for r in results)
    if args.json:
        print(json.dumps({"files": results, "transform_pairing": summary, "exit_code": exit_code}, indent=2))
    else:
        for r in results:
            print(f"=== {r['path']} ===")
            print(render_human(r["result"]))
            print()
        print(render_pairing_human(summary))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
