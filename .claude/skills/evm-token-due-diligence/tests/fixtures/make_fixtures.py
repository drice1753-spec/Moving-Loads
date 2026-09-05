#!/usr/bin/env python3
"""make_fixtures.py — deterministic generator for the validator fixtures.

Builds ONE complete, SYNTHETIC broad-mode manifest + report pair that passes
scripts/validate_report.py, then derives every reject-* fixture by a minimal mutation
(recomputing the report sha256 so only the intended defect triggers).

Everything here is synthetic: addresses are keccak-derived from labels, block hashes are
keccak256(label), chain ids are placeholders for the fixture only. Nothing describes a real token.

Run:  python3 tests/fixtures/make_fixtures.py [--out DIR] [--no-templates]
Writes <fixtures>/<case>/{manifest.json,report.md,expected.json} and, unless --no-templates,
<skill-root>/templates/manifest.example.json + report.example.md (identical to the valid fixture;
validate the template copy with --report templates/report.example.md because manifest.report.path
is 'report.md', relative to the manifest).
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent.parent
sys.path.insert(0, str(SKILL_ROOT / "scripts"))

import ddcore  # noqa: E402

ALL_E_CODES = [
    "E-SCHEMA", "E-ADDR-MALFORMED", "E-ADDR-CHECKSUM", "E-CHAIN-MISMATCH", "E-CHAIN-UNPINNED", "E-PIN-PLACEHOLDER",
    "E-PIN-HEADER-MISMATCH", "E-PIN-TIME", "E-PIN-PRIMARY", "E-META-STATUS", "E-META-REPORT", "E-SCOPE-TARGET",
    "E-SCOPE-RUNTIME", "E-REPORT-MISSING", "E-REPORT-HASH", "E-REPORT-IDENTITY", "E-REPORT-TARGET-ABSENT",
    "E-REPORT-SECTIONS", "E-CHECK-CORE-MISSING", "E-CHECK-STATUS", "E-EVIDENCE-DANGLING", "E-FINDING-NO-EVIDENCE",
    "E-FINDING-CHAIN", "E-RATING-MISSING", "E-RATING-UNKNOWN-AS-LOW", "E-RATING-CRITICAL-AVERAGED", "E-DECL-SIGNING",
    "E-DECL-FORK", "E-MODE-MISMATCH",
]

TARGET_CHAIN = 8453      # fixture value only; a real run verifies chain id from eth_chainId at use time
OTHER_CHAIN = 1          # used only by the same-symbol-other-chain reject case
UNPINNED_CHAIN = 42161   # used only by the scope-chain-without-pin reject case
SYMBOL = "SYNTH"
SYNTH_TAG = "SYNTHETIC FIXTURE - not a live finding"


def addr(label: str) -> str:
    return ddcore.to_checksum_address("0x" + ddcore.keccak256_hex(f"addr:{label}".encode())[2:42])


def h32(label: str) -> str:
    return ddcore.keccak256_hex(f"hash:{label}".encode())


def code_hash(label: str) -> str:
    return ddcore.keccak256_hex(f"runtime:{label}".encode())


A = {k: addr(k) for k in ("token", "owner", "pool", "position_manager", "locker", "router", "treasury",
                          "deployer", "quoter", "holder1", "other_token", "bridge")}
CH = {k: code_hash(k) for k in ("token", "pool", "position_manager", "locker", "router", "quoter", "bridge")}
TX = {k: h32(f"tx:{k}") for k in ("deploy", "launch_buy")}

P1_BLOCK, P1_TS = 20000000, 1735689600          # 2025-01-01T00:00:00Z
P2_BLOCK, P2_TS = 19000000, 1735603200          # 2024-12-31T00:00:00Z (launch-era, historical)
CAPTURED = "2025-01-01T00:10:00Z"


def pin(pid: str, chain: int, block: int, ts: int, purpose: str) -> dict:
    return {
        "pin_id": pid, "chain_id": chain, "block_number": block, "block_hash": h32(f"pin:{pid}"),
        "timestamp_unix": ts, "timestamp_utc": ddcore.iso_utc(ts), "captured_at_utc": CAPTURED,
        "rpc_method": "eth_getBlockByNumber", "rpc_endpoint_redacted": "https://rpc.example.invalid/<redacted>",
        "captured_header": {
            "number": hex(block), "hash": h32(f"pin:{pid}"), "parentHash": h32(f"pin:{pid}:parent"),
            "timestamp": hex(ts), "stateRoot": h32(f"pin:{pid}:state"),
        },
        "purpose": purpose,
    }


def scope(key: str, role: str, provenance: str, runtime_status: str, material: bool, label: str,
          chain: int = TARGET_CHAIN, detail: str | None = None) -> dict:
    ch = CH.get(key) if runtime_status == "contract" else (ddcore.EMPTY_CODE_HASH if key == "treasury" else None)
    return {"address": A[key], "chain_id": chain, "role": role, "label": label, "provenance": provenance,
            "provenance_detail": detail, "runtime_status": runtime_status, "code_hash": ch, "pin_id": "P1",
            "material": material, "limitation_id": None}


def evidence(eid: str, etype: str, address: str | None, query, basis: str, summary: str, pin_id: str | None = "P1",
             tx_hash: str | None = None, block: int | None = None, chain: int = TARGET_CHAIN) -> dict:
    return {"evidence_id": eid, "chain_id": chain, "address": address, "pin_id": pin_id, "tx_hash": tx_hash,
            "block_number": block, "evidence_type": etype, "artifact": f"evidence/{eid}.json",
            "artifact_sha256": hashlib.sha256(f"artifact:{eid}".encode()).hexdigest(), "query": query,
            "decoding_basis": basis, "summary": summary, "counterfactual": None}


def check(cid: str, name: str, status: str, evidence_ids: list, severity=None, finding_ids=None, reason=None,
          pin_id: str | None = "P1", limitation_id=None) -> dict:
    surface = {
        "A": "token_controls", "B": "canonical_lp_principal_custody", "C": "sellability_exit_depth",
        "D": "current_concentration", "E": "historical_launch_integrity", "F": "admin_treasury_reward_custody",
        "G": "reward_accounting_liveness", "H": "external_dependencies",
    }[cid[0]]
    if cid == "B-SIDE":
        surface = "side_pool_removal_risk"
    if cid in ("G-RIGHTS", "H-UTILITY"):
        surface = "utility_redemption_rights"
    if cid == "H-DEV":
        surface = "development_disclosure"
    return {"check_id": cid, "surface": surface, "name": name, "status": status, "severity": severity,
            "evidence_ids": evidence_ids, "finding_ids": finding_ids or [], "reason": reason, "pin_id": pin_id,
            "limitation_id": limitation_id}


def rating(value: str, likelihood: str, confidence: str, coverage: str, basis: list, summary: str,
           qualified: bool = False, note: str | None = None) -> dict:
    return {"rating": value, "likelihood": likelihood, "confidence": confidence, "coverage": coverage,
            "time_basis_pin_id": "P1", "basis_check_ids": basis, "summary": summary,
            "coverage_qualified": qualified, "coverage_note": note}


def build_manifest() -> dict:
    """The base, valid, broad-mode synthetic manifest (report.sha256 filled in later)."""
    meta = lambda value, eid="E1": {"value": value, "status": "resolved", "source": f"eth_call at P1; raw return preserved in {eid}",
                                     "reason": None, "evidence_id": eid}
    m = {
        "manifest_version": "1.0",
        "mode": "broad",
        "generated_at_utc": CAPTURED,
        "tooling": {"generator": "tests/fixtures/make_fixtures.py", "note": SYNTH_TAG},
        "target": {
            "requested": {"chain_id": TARGET_CHAIN, "chain_name": "fixture-chain (label only; chain_id authoritative)",
                          "address": A["token"], "source": "fixture generator"},
            "observed": {"chain_id": TARGET_CHAIN, "rpc_chain_id_hex": hex(TARGET_CHAIN),
                         "rpc_endpoint_redacted": "https://rpc.example.invalid/<redacted>", "client_version": "synthetic/0.0"},
            "address_checksum": ddcore.to_checksum_address(A["token"]),
            "metadata": {
                "name": meta("Synthetic Token"), "symbol": meta(SYMBOL), "decimals": meta(18),
                "total_supply": meta("1000000000000000000000000000"), "accounting_model": "standard_erc20",
            },
            "runtime": {
                "code_hash": CH["token"], "code_size": 4321, "pin_id": "P1",
                "proxy": {"status": "not_proxy", "implementation": None, "implementation_code_hash": None, "admin": None,
                          "beacon": None, "upgrade_authority": None,
                          "slots_read": {ddcore.EIP1967_IMPLEMENTATION_SLOT: "0x" + "00" * 32,
                                         ddcore.EIP1967_ADMIN_SLOT: "0x" + "00" * 32,
                                         ddcore.EIP1967_BEACON_SLOT: "0x" + "00" * 32},
                          "basis": "EIP-1967 slots zero at P1 and no DELEGATECALL opcode in runtime (E2, E4)"},
            },
            "deployment": {"status": "resolved", "tx_hash": TX["deploy"], "block_number": P2_BLOCK, "deployer": A["deployer"],
                           "factory": None, "deterministic": False, "reason": None},
            "decision_question": "Under a rug-resistance requirement for a 30-day hold, is this token acceptable?",
            "requirement_frame": "rug resistance for a 30-day hold",
            "scope_statement": "Token contract, its owner, the canonical v3 pool and position, the locker, treasury and launch-era transfers on the target chain.",
            "materiality_rules": "No monetary threshold applies to mint/upgrade/seizure/restriction/arbitrary-call/LP-removal authority. Holder balances below 0.5% of supply are not individually classified.",
            "known_limitations": ["Historical receipts before the pruning horizon of the queried RPC were unavailable (L1)."],
        },
        "pins": [pin("P1", TARGET_CHAIN, P1_BLOCK, P1_TS, "current_state"),
                 pin("P2", TARGET_CHAIN, P2_BLOCK, P2_TS, "historical")],
        "primary_pin_id": "P1",
        "scope_addresses": [
            scope("token", "token", "user_supplied", "contract", True, "target token"),
            scope("owner", "owner", "rpc_derived", "eoa", True, "owner() at P1", detail="eth_call owner() E3"),
            scope("pool", "pool", "event_derived", "contract", True, "canonical v3 pool", detail="PoolCreated log E9"),
            scope("position_manager", "position_manager", "rpc_derived", "contract", True, "v3 position manager"),
            scope("locker", "locker", "event_derived", "contract", True, "position NFT holder (locker)", detail="ownerOf E7"),
            scope("router", "router", "rpc_derived", "contract", False, "swap router used for quotes"),
            scope("quoter", "quoter", "rpc_derived", "contract", False, "quoter used for read-only quotes"),
            scope("treasury", "treasury", "rpc_derived", "eoa", True, "feeRecipient() at P1", detail="E13"),
            scope("deployer", "deployer", "rpc_derived", "eoa", False, "deployment tx sender"),
            scope("holder1", "holder", "rpc_derived", "eoa", False, "largest non-pool holder at P1"),
        ],
        "declarations": {
            "no_real_signing": True, "no_broadcast": True, "no_private_keys_requested": True,
            "external_content_untrusted": True,
            "simulation": {"used": False, "fork_type": None, "fork_verified_disposable": None, "fork_attestation_path": None,
                           "fork_chain_id": None, "fork_block": None, "synthetic_accounts_only": None,
                           "results_labeled_counterfactual": None},
        },
        "coverage": {
            "limitations": [{"limitation_id": "L1", "kind": "rpc_pruned",
                             "description": "eth_getTransactionReceipt for blocks before the RPC pruning horizon returned 'missing trie node'; historical sells could not be verified at receipt level.",
                             "affected_check_ids": ["C-HIST-SELL"], "affected_addresses": [], "retry_attempts": 3}],
            "notes": "This is a coverage limitation of the queried endpoint, not a token finding.",
        },
        "discovery": [{"discovery_id": "S1", "claim": "No side pool for the token was found besides the canonical pool.",
                       "search_universe": "PoolCreated logs of the v3 factory and PairCreated logs of the v2 factory recorded in E9",
                       "block_range": f"{P2_BLOCK}-{P1_BLOCK}", "pagination": "eth_getLogs in 50000-block windows",
                       "inclusion_rule": "either token of the pair equals the target address", "exclusions": None,
                       "materiality_threshold": "none for discovery", "coverage": "full for the two factories searched; other DEX factories not searched"}],
        "checks": [
            check("A-MINT", "Mint / supply-increase authority", "finding", ["E2", "E3", "E5"], "high", ["F1"]),
            check("A-UPGRADE", "Upgrade authority", "pass", ["E2", "E4"]),
            check("A-SEIZE", "Seizure / forced transfer / balance rewrite", "pass", ["E2", "E5"]),
            check("A-RESTRICT", "Pause / blacklist / limits / trading gates", "pass", ["E2", "E5"]),
            check("A-TAX", "Transfer taxes and exemptions", "pass", ["E2", "E5"]),
            check("A-EXTCALL", "External calls / delegatecall from token", "pass", ["E2"]),
            check("A-ADMIN", "Current owner / roles / timelock", "finding", ["E3"], "medium", ["F3"]),
            check("B-CANON", "Canonical pool identified by exact address", "pass", ["E6", "E9"]),
            check("B-PRINCIPAL", "Executable LP-principal removal path", "pass", ["E7", "E8"]),
            check("B-SIDE", "Side pools discovered and inventoried", "pass", ["E9"]),
            check("C-HIST-SELL", "Successful historical sell (receipt-level)", "unknown", [],
                  reason="Unknown because historical state was unavailable: receipts before the pruning horizon could not be fetched (L1).",
                  pin_id=None, limitation_id="L1"),
            check("C-QUOTE", "Pinned read-only quotes at small and holder-sized amounts", "pass", ["E10"]),
            check("D-SUPPLY", "Supply reconciliation", "pass", ["E11"]),
            check("D-CONC", "Classified concentration", "finding", ["E11"], "medium", ["F2"]),
            check("E-LAUNCH", "Launch parameters and early transfers decoded", "pass", ["E12"], pin_id="P2"),
            check("F-FEES", "Fee basis, splits, claim authority, recipients", "pass", ["E13"]),
            check("F-TREASURY", "Treasury / fee-wallet reconciliation", "pass", ["E13"]),
            check("G-REWARDS", "Rewards / vault / backing accounting", "not_applicable", [],
                  reason="No reward distributor, vault or backing contract is in scope; none was discovered in E9 or E14.", pin_id=None),
            check("G-RIGHTS", "Enforceable holder rights", "not_applicable", [],
                  reason="No redemption or claim right is advertised (E14) or present in the runtime (E2).", pin_id=None),
            check("H-UTILITY", "Advertised utility live, token-linked, enforceable", "pass", ["E14", "E2"]),
            check("H-DEPS", "Oracles, bridges, keepers, APIs, collateral dependencies", "pass", ["E2", "E14"]),
            check("H-DEV", "Source correspondence, builds, tests, disclosure", "pass", ["E5", "E15"]),
        ],
        "evidence": [
            evidence("E1", "rpc_state", A["token"], {"method": "eth_call", "calls": ["name()", "symbol()", "decimals()", "totalSupply()"], "block": hex(P1_BLOCK)},
                     "ABI string / uint8 / uint256 decode", "name, symbol, decimals, totalSupply resolved at P1"),
            evidence("E2", "bytecode", A["token"], {"method": "eth_getCode", "block": hex(P1_BLOCK)},
                     "PUSH4 selector walk + opcode scan (scripts/selector_scan.py)", "runtime selectors include mint(address,uint256); no DELEGATECALL/SELFDESTRUCT; no pause/blacklist selectors"),
            evidence("E3", "rpc_state", A["token"], {"method": "eth_call", "data": ddcore.selector("owner()"), "block": hex(P1_BLOCK)},
                     "address decode of owner() return", "owner() returns an externally owned account at P1"),
            evidence("E4", "rpc_storage", A["token"], {"method": "eth_getStorageAt", "slots": [ddcore.EIP1967_IMPLEMENTATION_SLOT, ddcore.EIP1967_ADMIN_SLOT, ddcore.EIP1967_BEACON_SLOT], "block": hex(P1_BLOCK)},
                     "raw", "EIP-1967 implementation/admin/beacon slots are zero at P1"),
            evidence("E5", "source_verified", A["token"], {"method": "compile-and-compare", "compiler": "solc (version recorded in artifact)"},
                     "compiled runtime bytes equal eth_getCode bytes except metadata hash", "verified source corresponds to deployed runtime; mint is onlyOwner with no cap"),
            evidence("E6", "rpc_state", A["pool"], {"method": "eth_call", "calls": ["slot0()", "liquidity()", "token0()", "token1()", "fee()"], "block": hex(P1_BLOCK)},
                     "v3 pool ABI", "canonical pool identified by exact address; token is token0; fee tier read at P1"),
            evidence("E7", "rpc_state", A["position_manager"], {"method": "eth_call", "calls": ["positions(tokenId)", "ownerOf(tokenId)", "getApproved(tokenId)"], "block": hex(P1_BLOCK)},
                     "v3 NonfungiblePositionManager ABI", "position NFT owned by the locker; no approvals or operators at P1"),
            evidence("E8", "rpc_state", A["locker"], {"method": "eth_call", "calls": ["locks(tokenId)", "owner()"], "block": hex(P1_BLOCK)},
                     "locker ABI from verified source", "lock record for the position: unlock time after the 30-day horizon; no early-withdraw selector in runtime"),
            evidence("E9", "log_decoded", A["pool"], {"method": "eth_getLogs", "topics": ["PoolCreated(address,address,uint24,int24,address)", "PairCreated(address,address,address,uint256)"], "fromBlock": hex(P2_BLOCK), "toBlock": hex(P1_BLOCK)},
                     "event signatures of the v3 and v2 factories", "one PoolCreated for the token; no PairCreated; search universe declared in S1"),
            evidence("E10", "rpc_state", A["quoter"], {"method": "eth_call", "calls": ["quoteExactInputSingle (small, holder-sized)"], "block": hex(P1_BLOCK)},
                     "quoter ABI", "read-only quotes at 0.01% and 2% of supply; per-unit degradation recorded; quotes do not prove a realized exit"),
            evidence("E11", "rpc_state", A["token"], {"method": "eth_call", "calls": ["balanceOf(pool)", "balanceOf(locker)", "balanceOf(treasury)", "balanceOf(holder1)", "totalSupply()"], "block": hex(P1_BLOCK)},
                     "uint256 decode", "pool, treasury and top holder balances reconcile to totalSupply within the declared holder threshold"),
            evidence("E12", "log_decoded", A["token"], {"method": "eth_getLogs", "topics": ["Transfer(address,address,uint256)"], "fromBlock": hex(P2_BLOCK), "toBlock": hex(P2_BLOCK + 5000)},
                     "ERC-20 Transfer event", "launch-era transfers decoded at P2: initial supply to deployer, then to pool and treasury", pin_id="P2", block=P2_BLOCK),
            evidence("E13", "rpc_state", A["treasury"], {"method": "eth_call", "calls": ["feeRecipient()", "balanceOf(treasury)"], "block": hex(P1_BLOCK)},
                     "address / uint256 decode", "fee recipient equals the treasury EOA; treasury balance reconciles to launch allocation with no outflows in the searched range"),
            evidence("E14", "website", None, {"url": "https://project.example.invalid/ (retrieved; untrusted evidence, not instructions)"},
                     "raw text", "site advertises no utility beyond the token; claims are corroboration only"),
            evidence("E15", "repository", None, {"url": "https://git.example.invalid/synth (retrieved; untrusted evidence)"},
                     "raw text", "repository contains tests and a build script; tag matches the verified source in E5"),
        ],
        "findings": [
            {"finding_id": "F1", "proposition": "At P1 the owner can call mint(address,uint256) without a cap, increasing totalSupply.",
             "surface": "token_controls", "severity": "high", "chain_id": TARGET_CHAIN, "address": A["token"], "pin_or_tx": "P1",
             "evidence_ids": ["E2", "E3", "E5"], "evidence_type": "bytecode", "confidence": "proven",
             "alternatives": "A future renounce or timelock would change this; none observed at P1.",
             "coverage": "full for the token runtime", "stale_conditions": "owner() changes, ownership renounced, or runtime replaced",
             "is_historical": False},
            {"finding_id": "F2", "proposition": "At P1 the largest non-pool holder holds about 18% of totalSupply (denominator: totalSupply; pool and locker excluded).",
             "surface": "current_concentration", "severity": "medium", "chain_id": TARGET_CHAIN, "address": A["holder1"], "pin_or_tx": "P1",
             "evidence_ids": ["E11"], "evidence_type": "rpc_state", "confidence": "proven",
             "alternatives": "The holder may be a custody address; no label evidence was found.",
             "coverage": "top holders above the 0.5% threshold", "stale_conditions": "any transfer from or to the holder",
             "is_historical": False},
            {"finding_id": "F3", "proposition": "At P1 the owner is an externally owned account with no timelock or multisig between it and the mint authority.",
             "surface": "token_controls", "severity": "medium", "chain_id": TARGET_CHAIN, "address": A["owner"], "pin_or_tx": "P1",
             "evidence_ids": ["E3"], "evidence_type": "rpc_state", "confidence": "proven",
             "alternatives": "The key could be held by a hardware or MPC custody arrangement; not observable onchain.",
             "coverage": "full", "stale_conditions": "ownership transferred to a contract", "is_historical": False},
        ],
        "ratings": {
            "token_controls": rating("high", "medium", "high", "full", ["A-MINT", "A-UPGRADE", "A-SEIZE", "A-RESTRICT", "A-TAX", "A-EXTCALL", "A-ADMIN"],
                                     "Uncapped owner mint at P1 (F1) from an EOA owner (F3); no upgrade, seizure, restriction or tax path found in the runtime."),
            "canonical_lp_principal_custody": rating("low", "low", "high", "full", ["B-CANON", "B-PRINCIPAL"],
                                                     "No current executable removal path found at the pinned block for the canonical position (held by the locker, unlock after horizon)."),
            "side_pool_removal_risk": rating("low", "low", "medium", "partial", ["B-SIDE"],
                                             "No side pool found in the searched factories (S1); other factories were not searched."),
            "sellability_exit_depth": rating("unknown", "unknown", "low", "partial", ["C-HIST-SELL", "C-QUOTE"],
                                             "Quotes at the tested sizes were obtained under the quoted state; no historical sell could be verified at receipt level (L1)."),
            "current_concentration": rating("medium", "medium", "high", "full", ["D-SUPPLY", "D-CONC"],
                                            "Supply reconciles; one holder near 18% of supply (F2)."),
            "historical_launch_integrity": rating("low", "low", "medium", "partial", ["E-LAUNCH"],
                                                  "Launch-era transfers decoded at P2 match the declared allocation; sales in the launch window were not traced beyond transfers."),
            "admin_treasury_reward_custody": rating("medium", "medium", "high", "full", ["F-FEES", "F-TREASURY", "A-ADMIN"],
                                                    "Fees route to an EOA treasury controlled by the same owner key."),
            "reward_accounting_liveness": rating("not_applicable", "unknown", "high", "full", ["G-REWARDS"], "No reward layer in scope."),
            "utility_redemption_rights": rating("low", "low", "medium", "full", ["G-RIGHTS", "H-UTILITY"],
                                                "No utility or redemption right is advertised or enforceable; nothing to lose beyond market value."),
            "external_dependencies": rating("low", "low", "medium", "partial", ["H-DEPS"],
                                            "No oracle, bridge or keeper dependency found in the runtime or on the site."),
            "development_disclosure": rating("low", "low", "medium", "partial", ["H-DEV"],
                                             "Verified source corresponds to the runtime; repository has tests; audit scope not disclosed."),
        },
        "verdict": {
            "question": "Under a rug-resistance requirement for a 30-day hold, is this token acceptable?",
            "requirement_frame": "rug resistance for a 30-day hold",
            "answer": "NO-GO under the stated requirement for rug resistance: at the pinned block the owner key can mint without a cap (F1), so supply and price can be changed unilaterally even though no current executable LP-removal path was found.",
            "conditional": True,
            "conditions": ["Would become GO-WITH-CONDITIONS if mint authority is renounced or placed behind a timelock and re-verified at a new pin."],
            "main_reasons": ["F1 uncapped mint by an EOA owner", "F3 no timelock or multisig"],
            "strongest_contrary_evidence": ["Canonical position locked past the horizon (E7, E8)", "No side pools in the searched universe (E9, S1)"],
            "unresolved_questions": ["Whether any historical sell succeeded at receipt level (L1)", "Whether the 18% holder is a custody address (F2)"],
            "evidence_that_would_change_it": ["A pinned owner() of the zero address or a timelock contract", "A receipt-level historical sell"],
        },
        "report": {"path": "report.md", "sha256": "0" * 64, "format": "markdown"},
    }
    return m


SURFACE_ORDER = ["token_controls", "canonical_lp_principal_custody", "side_pool_removal_risk", "sellability_exit_depth",
                 "current_concentration", "historical_launch_integrity", "admin_treasury_reward_custody",
                 "reward_accounting_liveness", "utility_redemption_rights", "external_dependencies", "development_disclosure"]
LEDGER_COLUMNS = ["finding_id", "proposition", "chain_id", "address", "pin_or_tx", "artifact_or_query", "decoding_basis",
                  "evidence_type", "confidence", "alternatives", "coverage", "stale_conditions"]


def report_symbol(m: dict) -> str:
    sym = m["target"]["metadata"]["symbol"]
    if sym["status"] == "resolved":
        return str(sym["value"])
    if sym["status"] == "unresolved":
        return "unresolved"
    return f"nonstandard:{sym['value']}"


def render_report(m: dict) -> str:
    """Render the report from the manifest. Body mentions ONLY scoped addresses."""
    ppin = next(p for p in m["pins"] if p["pin_id"] == m["primary_pin_id"])
    t = m["target"]
    ev = {e["evidence_id"]: e for e in m["evidence"]}
    checks = {c["check_id"]: c for c in m["checks"]}
    lines = [
        "---",
        "evm_dd_report: 1",
        f"mode: {m['mode']}",
        f"target_chain_id: {t['requested']['chain_id']}",
        f"target_address: {t['address_checksum']}",
        f"target_symbol: {report_symbol(m)}",
        f"primary_pin_id: {ppin['pin_id']}",
        f"primary_pin_block: {ppin['block_number']}",
        f"primary_pin_block_hash: {ppin['block_hash']}",
        f"manifest_path: {'manifest.json'}",
        "---",
        "",
        f"# Due-diligence report: {report_symbol(m)} on chain {t['requested']['chain_id']}",
        "",
        f"**{SYNTH_TAG}.** Every address, hash and number in this document is derived from labels by tests/fixtures/make_fixtures.py; nothing here describes a real token.",
        "",
        "## Verdict",
        "",
        f"**Question.** {m['verdict']['question']}",
        "",
        f"**Answer.** {m['verdict']['answer']}",
        "",
        "Conditions: " + " ".join(m["verdict"]["conditions"]),
        "",
        "## Target identity",
        "",
        f"- Requested chain_id {t['requested']['chain_id']}; observed eth_chainId {t['observed']['rpc_chain_id_hex']} (= {t['observed']['chain_id']}) from the queried RPC (endpoint redacted).",
        f"- Target address {t['address_checksum']} (EIP-55); runtime code hash {t['runtime']['code_hash']} at {ppin['pin_id']}; proxy status {t['runtime']['proxy']['status']}.",
        f"- Metadata at {ppin['pin_id']}: name `{t['metadata']['name']['value']}`, symbol `{t['metadata']['symbol']['value']}`, decimals {t['metadata']['decimals']['value']}, totalSupply {t['metadata']['total_supply']['value']} (all status resolved, E1).",
        f"- Primary pin {ppin['pin_id']}: block {ppin['block_number']}, hash {ppin['block_hash']}, {ppin['timestamp_utc']}; historical pin P2 at block {m['pins'][1]['block_number']} ({m['pins'][1]['timestamp_utc']}).",
        "",
        "## Ratings",
        "",
        "| surface | rating | likelihood | confidence | coverage | time basis | basis checks | summary |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for key in SURFACE_ORDER:
        r = m["ratings"][key]
        lines.append(f"| {key} | {r['rating']} | {r['likelihood']} | {r['confidence']} | {r['coverage']} | {r['time_basis_pin_id']} | {', '.join(r['basis_check_ids'])} | {r['summary']} |")
    lines += [
        "",
        "## Key findings",
        "",
    ]
    for f in m["findings"]:
        lines.append(f"- **{f['finding_id']}** ({f['severity']}, {f['confidence']}, {f['surface']}, {f['pin_or_tx']}, {f['address']}): {f['proposition']} Evidence: {', '.join(f['evidence_ids'])}.")
    lines += [
        "",
        f"Check statuses: {sum(1 for c in m['checks'] if c['status'] == 'pass')} pass, {sum(1 for c in m['checks'] if c['status'] == 'finding')} finding, "
        f"{sum(1 for c in m['checks'] if c['status'] == 'unknown')} unknown, {sum(1 for c in m['checks'] if c['status'] == 'not_applicable')} not_applicable. An unknown check is never a pass.",
        "",
        "## Strongest contrary evidence",
        "",
    ]
    for s in m["verdict"]["strongest_contrary_evidence"]:
        lines.append(f"- {s}")
    lines += ["", f"- The canonical position is held by the locker {A['locker']} via the position manager {A['position_manager']} (E7, E8); this does not establish that all liquidity is locked or that price is supported.", "",
              "## Unresolved questions", ""]
    for s in m["verdict"]["unresolved_questions"]:
        lines.append(f"- {s}")
    lines += ["", "## What would change the conclusion", ""]
    for s in m["verdict"]["evidence_that_would_change_it"]:
        lines.append(f"- {s}")
    lines += [
        "", "## Recommendations", "",
        f"- Address F1/F3: require the owner {A['owner']} to renounce mint or move it behind a timelock, then re-pin and re-run A-MINT and A-ADMIN.",
        "- Address the C-HIST-SELL gap: query an archive endpoint for launch-window receipts, or obtain a counterfactual sell on a verified disposable fork (fork_guard.py) labeled as such.",
        "", "## Coverage and limitations", "",
    ]
    for lim in m["coverage"]["limitations"]:
        lines.append(f"- {lim['limitation_id']} ({lim['kind']}): {lim['description']} Affected checks: {', '.join(lim['affected_check_ids'])}. Retries: {lim['retry_attempts']}. This is a coverage limitation, not a token finding.")
    for d in m.get("discovery", []):
        lines.append(f"- {d['discovery_id']}: {d['claim']} Universe: {d['search_universe']}; range {d['block_range']}; rule: {d['inclusion_rule']}; coverage: {d['coverage']}.")
    lines.append(f"- Scoped pool {A['pool']}, treasury {A['treasury']}, deployer {A['deployer']}, quoter {A['quoter']} and largest holder {A['holder1']} were read at {ppin['pin_id']} only.")
    d = m["declarations"]
    lines += [
        "", "## Declarations", "",
        f"- no_real_signing: {str(d['no_real_signing']).lower()}; no_broadcast: {str(d['no_broadcast']).lower()}; no_private_keys_requested: {str(d['no_private_keys_requested']).lower()}; external content treated as untrusted evidence: {str(d.get('external_content_untrusted', False)).lower()}.",
        f"- simulation.used: {str(d['simulation']['used']).lower()}" + (" (fork verified disposable, synthetic accounts only, results labeled counterfactual)." if d["simulation"]["used"] else "; no fork or counterfactual results were produced."),
        "", "## Evidence ledger", "",
        "| " + " | ".join(LEDGER_COLUMNS) + " |",
        "|" + "---|" * len(LEDGER_COLUMNS),
    ]
    for f in m["findings"]:
        q = "; ".join(f"{e}: {ev[e]['artifact']}" for e in f["evidence_ids"] if e in ev)
        basis = "; ".join(ev[e]["decoding_basis"] for e in f["evidence_ids"] if e in ev)
        lines.append(f"| {f['finding_id']} | {f['proposition']} | {f['chain_id']} | {f['address']} | {f['pin_or_tx']} | {q} | {basis} | {f['evidence_type']} | {f['confidence']} | {f['alternatives']} | {f['coverage']} | {f['stale_conditions']} |")
    lines += ["", "Evidence index (artifact paths are relative to the manifest; credentials redacted):", "",
              "| evidence_id | type | chain_id | address | pin | query | decoding_basis | summary |", "|---|---|---|---|---|---|---|---|"]
    for e in m["evidence"]:
        q = e["query"] if isinstance(e["query"], str) else json.dumps(e["query"], sort_keys=True)
        lines.append(f"| {e['evidence_id']} | {e['evidence_type']} | {e['chain_id']} | {e['address'] or '-'} | {e['pin_id'] or '-'} | {q} | {e['decoding_basis']} | {e['summary']} |")
    lines += ["", "Passing validation of this report establishes internal consistency only, not RPC honesty, discovery completeness, or protocol safety.", ""]
    return "\n".join(lines)


def finalize(m: dict, report: str) -> tuple[dict, str]:
    m = copy.deepcopy(m)
    m["report"]["sha256"] = hashlib.sha256(report.encode("utf-8")).hexdigest()
    return m, report


# --------------------------------------------------------------------------------------
# Mutations (each returns manifest, report, expected)
# --------------------------------------------------------------------------------------
def expected(expect: str, codes: list, side_effects: list | None = None, notes: str = "") -> dict:
    side = set(side_effects or [])
    return {"expect": expect, "expected_codes": codes,
            "forbidden_codes": [c for c in ALL_E_CODES if c not in codes and c not in side], "notes": notes}


def case_valid(m, r):
    return m, r, expected("pass", [], notes="Complete synthetic broad-mode pair; must pass with no E-code and no W-REPORT-UNSCOPED-ADDRESS.")


def case_same_symbol_other_chain(m, r):
    m["target"]["observed"]["chain_id"] = OTHER_CHAIN
    m["target"]["observed"]["rpc_chain_id_hex"] = hex(OTHER_CHAIN)
    m["scope_addresses"][0]["chain_id"] = OTHER_CHAIN
    return m, r, expected("fail", ["E-CHAIN-MISMATCH", "E-SCOPE-TARGET"], ["E-CHAIN-UNPINNED"],
                          "Same-symbol token substituted from chain 1: requested 8453 but RPC observed 0x1 and the scoped token entry is on chain 1. E-CHAIN-UNPINNED is an unavoidable side effect (chain 1 has no pin).")


def case_wrong_target_report(m, r):
    r = r.replace(A["token"], A["other_token"]).replace(A["token"].lower(), A["other_token"].lower())
    return m, r, expected("fail", ["E-REPORT-IDENTITY", "E-REPORT-TARGET-ABSENT"], [],
                          "Report frontmatter and body refer to a different address; the manifest target never appears in the body. W-REPORT-UNSCOPED-ADDRESS is expected as a warning.")


def case_fake_pin(m, r):
    p = m["pins"][0]
    zero = "0x" + "00" * 32
    r = r.replace(p["block_hash"], zero)
    p["block_hash"] = zero
    p["captured_header"]["hash"] = zero
    p["captured_header"]["number"] = hex(p["block_number"] + 1)
    return m, r, expected("fail", ["E-PIN-PLACEHOLDER", "E-PIN-HEADER-MISMATCH"], [],
                          "Primary pin has an all-zero block hash and a captured header whose number disagrees with block_number; the report frontmatter was updated to the same zero hash so only pin errors trigger.")


def case_unknown_as_pass(m, r):
    for c in m["checks"]:
        if c["check_id"] == "C-HIST-SELL":
            c["status"] = "pass"; c["evidence_ids"] = []; c["reason"] = "not run"; c["limitation_id"] = None
        if c["check_id"] == "C-QUOTE":
            c["status"] = "unknown"; c["reason"] = "quote call not run"
    rt = m["ratings"]["sellability_exit_depth"]
    rt["rating"] = "low"; rt["likelihood"] = "low"; rt["coverage_qualified"] = False; rt["coverage_note"] = None
    return m, r, expected("fail", ["E-CHECK-STATUS", "E-RATING-UNKNOWN-AS-LOW"], [],
                          "C-HIST-SELL presented as pass with no evidence and reason 'not run'; sellability rated low over an unknown C-QUOTE without coverage qualification. L1 still lists C-HIST-SELL in affected_check_ids, so no limitation warning is expected.")


def case_malformed_address(m, r):
    owner = m["scope_addresses"][1]["address"]
    flipped = None
    for i, ch in enumerate(owner[2:], start=2):
        if ch.isalpha():
            flipped = owner[:i] + ch.swapcase() + owner[i + 1:]
            break
    m["scope_addresses"][1]["address"] = flipped
    m["scope_addresses"][5]["address"] = m["scope_addresses"][5]["address"][:-1]  # router: 39 hex chars
    return m, r, expected("fail", ["E-ADDR-CHECKSUM", "E-ADDR-MALFORMED"], ["E-SCHEMA"],
                          "Owner scope address has one letter case-flipped (EIP-55 failure); router scope address has 39 hex characters. E-SCHEMA is an unavoidable side effect (schema address pattern).")


def case_missing_evidence(m, r):
    m["findings"][0]["evidence_ids"] = []
    for c in m["checks"]:
        if c["check_id"] == "A-UPGRADE":
            c["evidence_ids"] = ["E999"]
    return m, r, expected("fail", ["E-FINDING-NO-EVIDENCE", "E-EVIDENCE-DANGLING"], [],
                          "F1 keeps confidence proven with no evidence ids; A-UPGRADE cites a non-existent evidence id E999.")


def case_scope_chain_without_pin(m, r):
    m["scope_addresses"].append(scope("bridge", "bridge", "website_claim", "contract", True, "claimed bridge endpoint", chain=UNPINNED_CHAIN,
                                      detail="site claim; untrusted evidence"))
    return m, r, expected("fail", ["E-CHAIN-UNPINNED"], [],
                          "A material scope entry on chain 42161 with no pin for that chain (and pin_id P1 on another chain).")


def case_simulation_without_fork_guard(m, r):
    m["declarations"]["simulation"] = {"used": True, "fork_type": "anvil", "fork_verified_disposable": False, "fork_attestation_path": None,
                                       "fork_chain_id": TARGET_CHAIN, "fork_block": P1_BLOCK, "synthetic_accounts_only": True,
                                       "results_labeled_counterfactual": True}
    e = evidence("E16", "simulation_counterfactual", A["router"], {"method": "eth_call on fork", "note": "counterfactual sell from a synthetic account"},
                 "router ABI", "counterfactual sell succeeded on the fork; not a realized exit")
    e["counterfactual"] = True
    m["evidence"].append(e)
    for c in m["checks"]:
        if c["check_id"] == "C-QUOTE":
            c["evidence_ids"].append("E16")
    return m, r, expected("fail", ["E-DECL-FORK"], [],
                          "simulation.used is true and simulation_counterfactual evidence exists, but fork_verified_disposable is false (fork_guard.py attestation absent).")


CASES = [
    ("valid", case_valid),
    ("reject-same-symbol-other-chain", case_same_symbol_other_chain),
    ("reject-wrong-target-report", case_wrong_target_report),
    ("reject-fake-pin", case_fake_pin),
    ("reject-unknown-as-pass", case_unknown_as_pass),
    ("reject-malformed-address", case_malformed_address),
    ("reject-missing-evidence", case_missing_evidence),
    ("reject-scope-chain-without-pin", case_scope_chain_without_pin),
    ("reject-simulation-without-fork-guard", case_simulation_without_fork_guard),
]


def build_all() -> dict[str, tuple[dict, str, dict]]:
    base = build_manifest()
    base_report = render_report(base)
    out = {}
    for name, fn in CASES:
        m, r, exp = fn(copy.deepcopy(base), base_report)
        m, r = finalize(m, r)
        out[name] = (m, r, exp)
    return out


def write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(HERE), help="fixtures directory (default: this file's directory)")
    ap.add_argument("--no-templates", action="store_true", help="do not refresh templates/manifest.example.json + report.example.md")
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    cases = build_all()
    for name, (m, r, exp) in cases.items():
        d = out_dir / name
        d.mkdir(parents=True, exist_ok=True)
        write_json(d / "manifest.json", m)
        (d / "report.md").write_text(r, encoding="utf-8")
        write_json(d / "expected.json", exp)
        print(f"wrote {d}")
    if not args.no_templates:
        tdir = SKILL_ROOT / "templates"
        tdir.mkdir(parents=True, exist_ok=True)
        m, r, _ = cases["valid"]
        write_json(tdir / "manifest.example.json", m)
        (tdir / "report.example.md").write_text(r, encoding="utf-8")
        print(f"wrote {tdir / 'manifest.example.json'} and report.example.md (validate with --report templates/report.example.md)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
