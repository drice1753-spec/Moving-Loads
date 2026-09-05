#!/usr/bin/env python3
"""validate_report.py — target-integrity validator for the evm-token-due-diligence skill (contract v1.1).

Checks a manifest (schemas/manifest.schema.json) and its report for INTERNAL CONSISTENCY:
requested/observed/reported chain+address identity, metadata status contradictions, block pins
against captured headers (and against each other), scope chain/role/provenance/runtime status,
scope binding of every evidence/finding/limitation/proxy address, report identity, hash, ratings
table and verdict text against the manifest, check/evidence/finding/rating linkage and severity
ordering, and the signing/broadcast/fork declarations.

Usage:
  python3 validate_report.py --manifest M.json [--report R.md] [--json] [--strict]
Exit codes: 0 pass, 1 fail, 2 usage (bad arguments or manifest file not found).

Error codes (E-*, always fatal):
  E-SCHEMA E-ADDR-MALFORMED E-ADDR-CHECKSUM E-CHAIN-MISMATCH E-CHAIN-UNPINNED E-PIN-PLACEHOLDER
  E-PIN-HEADER-MISMATCH E-PIN-TIME E-PIN-PRIMARY E-META-STATUS E-META-REPORT E-SCOPE-TARGET
  E-SCOPE-RUNTIME E-SCOPE-ADDRESS E-REPORT-MISSING E-REPORT-HASH E-REPORT-IDENTITY
  E-REPORT-TARGET-ABSENT E-REPORT-SECTIONS E-REPORT-RATINGS E-REPORT-VERDICT E-CHECK-CORE-MISSING
  E-CHECK-STATUS E-CHECK-DISCOVERY-ONLY E-EVIDENCE-DANGLING E-FINDING-NO-EVIDENCE E-FINDING-UNLINKED
  E-FINDING-CHAIN E-FINDING-EVIDENCE-TYPE E-RATING-MISSING E-RATING-UNKNOWN-AS-LOW
  E-RATING-CRITICAL-AVERAGED E-DECL-SIGNING E-DECL-FORK E-MODE-MISMATCH
Warning codes (W-*, fatal only when promoted by --strict; promoted set = STRICT_PROMOTED):
  W-REPORT-UNSCOPED-ADDRESS W-VERDICT-LANGUAGE W-EVIDENCE-DISCOVERY-ONLY W-CHECK-DISCOVERY-ONLY
  W-CHECK-SEVERITY-UNSUPPORTED W-RATING-BASIS-INCOMPLETE W-FINDING-CROSS-CHAIN W-FINDING-PIN-MISMATCH
  W-PIN-STALE W-PIN-UNUSED W-DECL-FORK-ATTESTATION W-LIMITATION-UNREFERENCED

Report parsing: a UTF-8 BOM and leading blank lines before the opening '---' are tolerated; the
report hash is computed over the raw file bytes; before the body is scanned for the target address,
required H2 headings, the Ratings table and the Verdict text, fenced code blocks and HTML comments are
stripped so invisible text cannot satisfy a requirement. W-VERDICT-LANGUAGE is a regex heuristic
(unconditional "is/are/looks/... safe|secure|risk-free|rug-proof" phrasing with simple negation
handling), not a semantic judgment.

Passing validates internal consistency of the manifest and report only. It does not establish
RPC honesty, discovery completeness, or protocol safety.

Python 3.10+ standard library only. Imports ddcore from the same directory.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ddcore  # noqa: E402

SCHEMA_PATH = HERE.parent / "schemas" / "manifest.schema.json"
CONTRACT_VERSION = "1.1"

CLOSING_NOTE = ("Passing validates internal consistency of the manifest and report only. "
                "It does not establish RPC honesty, discovery completeness, or protocol safety.")

# --------------------------------------------------------------------------------------
# Shared vocabulary (BRIEF section 2) — keep these strings exact
# --------------------------------------------------------------------------------------
SURFACES = [
    "token_controls", "canonical_lp_principal_custody", "side_pool_removal_risk",
    "sellability_exit_depth", "current_concentration", "historical_launch_integrity",
    "admin_treasury_reward_custody", "reward_accounting_liveness", "utility_redemption_rights",
    "external_dependencies", "development_disclosure",
]
CORE_CHECKS: dict[str, str] = {
    "A-MINT": "token_controls", "A-UPGRADE": "token_controls", "A-SEIZE": "token_controls",
    "A-RESTRICT": "token_controls", "A-TAX": "token_controls", "A-EXTCALL": "token_controls",
    "A-ADMIN": "token_controls",
    "B-CANON": "canonical_lp_principal_custody", "B-PRINCIPAL": "canonical_lp_principal_custody",
    "B-SIDE": "side_pool_removal_risk",
    "C-HIST-SELL": "sellability_exit_depth", "C-QUOTE": "sellability_exit_depth",
    "D-SUPPLY": "current_concentration", "D-CONC": "current_concentration",
    "E-LAUNCH": "historical_launch_integrity",
    "F-FEES": "admin_treasury_reward_custody", "F-TREASURY": "admin_treasury_reward_custody",
    "G-REWARDS": "reward_accounting_liveness", "G-RIGHTS": "utility_redemption_rights",
    "H-UTILITY": "utility_redemption_rights", "H-DEPS": "external_dependencies",
    "H-DEV": "development_disclosure",
}
# Checks to which no monetary materiality threshold applies: a 'pass' here must rest on
# non-discovery evidence (E-CHECK-DISCOVERY-ONLY is an error always).
NO_THRESHOLD_CHECKS = {"A-MINT", "A-UPGRADE", "A-SEIZE", "A-RESTRICT", "A-TAX", "A-EXTCALL", "A-ADMIN",
                       "B-CANON", "B-PRINCIPAL", "B-SIDE"}
EVIDENCE_TYPES = ["rpc_state", "rpc_storage", "bytecode", "calldata", "receipt", "log_decoded", "trace",
                  "source_verified", "source_unverified", "explorer", "dashboard", "website", "repository",
                  "token_metadata", "simulation_counterfactual", "manual_note"]
DISCOVERY_EVIDENCE_TYPES = {"explorer", "dashboard", "website", "repository", "token_metadata",
                            "source_unverified", "manual_note"}
# Evidence that asserts chain state or execution: it must be bound to a pin or a tx hash.
ONCHAIN_EVIDENCE_TYPES = {"rpc_state", "rpc_storage", "bytecode", "calldata", "receipt", "log_decoded", "trace",
                          "simulation_counterfactual"}
CHECK_STATUSES = ["pass", "finding", "unknown", "skipped", "not_applicable"]
SEVERITIES = ["critical", "high", "medium", "low", "info"]
SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1, "info": 0}
RATING_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}  # unknown / not_applicable are not numeric
CONFIDENCES = ["proven", "strongly_supported", "inference", "unknown"]
RATING_VALUES = ["critical", "high", "medium", "low", "unknown", "not_applicable"]
MODES = ["focused", "broad", "formal"]
META_STATUSES = ["resolved", "unresolved", "nonstandard"]
PROXY_STATUSES_WITH_IMPLEMENTATION = {"eip1967", "eip1822", "beacon", "minimal_1167", "custom"}

REQUIRED_SECTIONS = ["Verdict", "Target identity", "Ratings", "Key findings", "Strongest contrary evidence",
                     "Unresolved questions", "What would change the conclusion", "Recommendations",
                     "Coverage and limitations", "Declarations", "Evidence ledger"]
FOCUSED_SECTIONS = ["Verdict", "Coverage and limitations", "Evidence ledger"]

STRICT_PROMOTED = {"W-REPORT-UNSCOPED-ADDRESS", "W-EVIDENCE-DISCOVERY-ONLY", "W-VERDICT-LANGUAGE",
                   "W-CHECK-DISCOVERY-ONLY", "W-RATING-BASIS-INCOMPLETE", "W-DECL-FORK-ATTESTATION"}

FIRST_PUBLIC_EVM_TIMESTAMP = 1438269973  # earliest plausible block timestamp (no public EVM chain existed before)
PIN_FUTURE_TOLERANCE_S = 900             # block timestamp may exceed captured_at_utc by at most this (clock skew)
PIN_STALE_S = 7 * 24 * 3600              # W-PIN-STALE when capture is further than this from the block timestamp
COVERAGE_NOTE_MIN_CHARS = 20             # coverage_qualified needs a coverage_note at least this long

ADDRESS_KEYS = {"address", "address_checksum", "implementation", "admin", "beacon", "deployer", "factory",
                "affected_addresses"}
_ADDR_TOKEN_RE = re.compile(r"(?<![0-9a-zA-Z])0x[0-9a-fA-F]{40}(?![0-9a-zA-Z])")
_TX_RE = re.compile(r"^0x[0-9a-fA-F]{64}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# ATX H2: '## Title', optional closing hashes ('## Title ##') and trailing blanks; case must match exactly.
_H2_RE = re.compile(r"^##[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$", re.MULTILINE)
_FENCE_OPEN_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")
# Heuristic for unconditional safety phrasing. Documented as a heuristic: it catches
# "<is|are|it's|looks|seems|remains|will be|considered|deemed|definitely|completely|totally|fully|100%|perfectly>
#  [a|an|the|very|...] <safe|secure|riskless|risk-free|rug-proof|rugproof|safe investment|no risk>"
# and skips phrases preceded by "not ", "never ", "no ", "isn't ", "aren't ", "cannot be ".
_UNSAFE_RE = re.compile(
    r"(?<!\bnot )(?<!\bnever )(?<!\bno )(?<!\bisn't )(?<!\baren't )(?<!\bcannot be )"
    r"\b(?:is|are|it's|it is|was|were|looks|seems|remains|will be|considered|deemed|"
    r"definitely|completely|totally|fully|100%|perfectly)"
    r"(?:\s+(?:a|an|the|very|definitely|completely|totally|fully|100%|perfectly|entirely|absolutely|really|quite))*"
    r"\s+(?:safe investment|safe|secure|riskless|risk[- ]?free|rug[- ]?proof|no risk)\b",
    re.IGNORECASE)


# --------------------------------------------------------------------------------------
# Issue collection
# --------------------------------------------------------------------------------------
class Issues:
    def __init__(self, strict: bool = False):
        self.strict = strict
        self.errors: list[dict] = []
        self.warnings: list[dict] = []

    def error(self, code: str, message: str, path: Optional[str] = None) -> None:
        self.errors.append({"code": code, "message": message, "path": path})

    def warn(self, code: str, message: str, path: Optional[str] = None) -> None:
        if self.strict and code in STRICT_PROMOTED:
            self.errors.append({"code": code, "message": message + " (promoted by --strict)", "path": path})
        else:
            self.warnings.append({"code": code, "message": message, "path": path})

    def codes(self) -> list[str]:
        return [e["code"] for e in self.errors]


# --------------------------------------------------------------------------------------
# Tolerant accessors (semantic checks must never crash on malformed input)
# --------------------------------------------------------------------------------------
def _d(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


def _l(v: Any) -> list:
    return v if isinstance(v, list) else []


def _strs(v: Any) -> list[str]:
    return [x for x in _l(v) if isinstance(x, str)]


def _s(v: Any) -> str:
    return v if isinstance(v, str) else ""


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _lower(v: Any) -> str:
    return v.lower() if isinstance(v, str) else ""


def _norm_addr(v: Any) -> Optional[str]:
    return ddcore.normalize_address(v) if ddcore.is_hex_address(v) else None


def _ws(v: Any) -> str:
    """Whitespace-normalize: collapse runs of whitespace to one space and strip."""
    return " ".join(_s(v).split())


def _inside_dir(base: str, rel: str) -> bool:
    """True when base/rel resolves (symlinks followed) inside base."""
    try:
        base_real = os.path.realpath(base)
        target_real = os.path.realpath(os.path.join(base, rel))
        return os.path.commonpath([base_real, target_real]) == base_real
    except (ValueError, OSError):
        return False


# --------------------------------------------------------------------------------------
# Structural checks: a small draft-07 subset interpreter over schemas/manifest.schema.json
# (type, required, properties, additionalProperties, enum, const, pattern, minimum,
#  minItems, minLength, items, $ref into #/definitions). If the schema file is unavailable,
#  falls back to hand-coded top-level checks. jsonschema, if importable, is run as well.
# --------------------------------------------------------------------------------------
_TYPE_MAP = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: _is_int(v),
    "number": lambda v: (isinstance(v, (int, float)) and not isinstance(v, bool)),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _load_schema() -> Optional[dict]:
    try:
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            obj = json.load(f)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


class _MiniSchema:
    def __init__(self, schema: dict, issues: Issues, max_errors: int = 200):
        self.root = schema
        self.issues = issues
        self.count = 0
        self.max_errors = max_errors

    def _err(self, path: str, msg: str) -> None:
        self.count += 1
        if self.count <= self.max_errors:
            self.issues.error("E-SCHEMA", msg, path)

    def _resolve(self, node: dict) -> dict:
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            cur: Any = self.root
            for part in ref[2:].split("/"):
                cur = _d(cur).get(part, {})
            merged = dict(_d(cur))
            for k, v in node.items():
                if k != "$ref":
                    merged[k] = v
            return merged
        return node

    def check(self, node: dict, value: Any, path: str) -> None:
        node = self._resolve(node)
        if "const" in node and value != node["const"]:
            self._err(path, f"expected constant {node['const']!r}, got {value!r}")
        t = node.get("type")
        if t is not None:
            types = t if isinstance(t, list) else [t]
            if not any(_TYPE_MAP.get(x, lambda v: True)(value) for x in types):
                self._err(path, f"expected type {'/'.join(types)}, got {type(value).__name__}")
                return
        if "enum" in node and value not in node["enum"]:
            self._err(path, f"value {value!r} not in enum {node['enum']}")
        if isinstance(value, str):
            pat = node.get("pattern")
            if isinstance(pat, str) and not re.search(pat, value):
                self._err(path, f"value {value!r} does not match pattern {pat}")
            ml = node.get("minLength")
            if _is_int(ml) and len(value) < ml:
                self._err(path, f"string shorter than minLength {ml}")
        if _is_int(value) or (isinstance(value, float)):
            mn = node.get("minimum")
            if isinstance(mn, (int, float)) and value < mn:
                self._err(path, f"value {value} below minimum {mn}")
        if isinstance(value, dict):
            props = _d(node.get("properties"))
            for req in _l(node.get("required")):
                if req not in value:
                    self._err(f"{path}.{req}", "required key missing")
            ap = node.get("additionalProperties", True)
            for k, v in value.items():
                if k in props:
                    self.check(_d(props[k]), v, f"{path}.{k}")
                elif isinstance(ap, dict):
                    self.check(ap, v, f"{path}.{k}")
                elif ap is False:
                    self._err(f"{path}.{k}", "unexpected key (additionalProperties=false)")
        if isinstance(value, list):
            mi = node.get("minItems")
            if _is_int(mi) and len(value) < mi:
                self._err(path, f"array shorter than minItems {mi}")
            items = node.get("items")
            if isinstance(items, dict):
                for i, v in enumerate(value):
                    self.check(items, v, f"{path}[{i}]")


def _fallback_structure(manifest: dict, issues: Issues) -> None:
    required = ["manifest_version", "mode", "target", "pins", "primary_pin_id", "scope_addresses", "declarations",
                "coverage", "checks", "evidence", "findings", "verdict", "report"]
    for k in required:
        if k not in manifest:
            issues.error("E-SCHEMA", "required key missing", f"$.{k}")
    for k, typ in (("target", dict), ("pins", list), ("scope_addresses", list), ("declarations", dict),
                   ("coverage", dict), ("checks", list), ("evidence", list), ("findings", list),
                   ("verdict", dict), ("report", dict)):
        if k in manifest and not isinstance(manifest[k], typ):
            issues.error("E-SCHEMA", f"expected {typ.__name__}", f"$.{k}")
    if manifest.get("manifest_version") != "1.0":
        issues.error("E-SCHEMA", "manifest_version must be '1.0'", "$.manifest_version")
    if manifest.get("mode") not in MODES:
        issues.error("E-SCHEMA", f"mode must be one of {MODES}", "$.mode")


def check_structure(manifest: Any, issues: Issues) -> dict:
    """Returns {'schema_file': bool, 'schema_lib': bool}."""
    info = {"schema_file": False, "schema_lib": False}
    if not isinstance(manifest, dict):
        issues.error("E-SCHEMA", "manifest root must be a JSON object", "$")
        return info
    schema = _load_schema()
    if schema is not None:
        info["schema_file"] = True
        try:
            _MiniSchema(schema, issues).check(schema, manifest, "$")
        except Exception as e:  # never crash on malformed input
            issues.error("E-SCHEMA", f"structural check aborted: {e}", "$")
    else:
        _fallback_structure(manifest, issues)
    if schema is not None:
        try:
            import jsonschema  # type: ignore
        except Exception:
            jsonschema = None
        if jsonschema is not None:
            info["schema_lib"] = True
            try:
                seen = {e["path"] for e in issues.errors if e["code"] == "E-SCHEMA"}
                validator_cls = jsonschema.validators.validator_for(schema)
                for err in sorted(validator_cls(schema).iter_errors(manifest), key=lambda e: str(list(e.path))):
                    path = "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in err.path)
                    if path not in seen:
                        issues.error("E-SCHEMA", f"jsonschema: {err.message}", path)
            except Exception as e:
                issues.error("E-SCHEMA", f"jsonschema run failed: {e}", "$")
    return info


# --------------------------------------------------------------------------------------
# Context shared by the semantic checks
# --------------------------------------------------------------------------------------
class Ctx:
    def __init__(self, manifest: dict, manifest_dir: str, report_text: Optional[str], report_path: Optional[str],
                 report_bytes: Optional[bytes] = None, report_error: Optional[str] = None):
        self.m = manifest
        self.dir = manifest_dir
        self.report_text = report_text
        self.report_bytes = report_bytes
        self.report_path = report_path
        self.report_error = report_error
        self.mode = _s(manifest.get("mode"))
        target = _d(manifest.get("target"))
        self.target_chain = _d(target.get("requested")).get("chain_id")
        self.target_addr = _norm_addr(_d(target.get("requested")).get("address"))
        self.pins: dict[str, dict] = {}
        for p in _l(manifest.get("pins")):
            pid = _d(p).get("pin_id")
            if isinstance(pid, str) and pid not in self.pins:
                self.pins[pid] = _d(p)
        self.pinned_chains: set = {p.get("chain_id") for p in self.pins.values() if _is_int(p.get("chain_id"))}
        self.evidence: dict[str, dict] = {}
        for e in _l(manifest.get("evidence")):
            eid = _d(e).get("evidence_id")
            if isinstance(eid, str):
                self.evidence.setdefault(eid, _d(e))
        self.findings: dict[str, dict] = {}
        for f in _l(manifest.get("findings")):
            fid = _d(f).get("finding_id")
            if isinstance(fid, str):
                self.findings.setdefault(fid, _d(f))
        self.checks: dict[str, dict] = {}
        self.linked_findings: set = set()
        for c in _l(manifest.get("checks")):
            cid = _d(c).get("check_id")
            if isinstance(cid, str):
                self.checks.setdefault(cid, _d(c))
            self.linked_findings.update(_strs(_d(c).get("finding_ids")))
        self.limitations: dict[str, dict] = {}
        self.affected_check_ids: set = set()
        for lim in _l(_d(manifest.get("coverage")).get("limitations")):
            lid = _d(lim).get("limitation_id")
            if isinstance(lid, str):
                self.limitations.setdefault(lid, _d(lim))
            self.affected_check_ids.update(_strs(_d(lim).get("affected_check_ids")))
        self.scope_norm: set = set()           # normalized addresses (any chain)
        self.scope_pairs: set = set()          # (normalized address, chain_id)
        self.bridge_addrs: set = set()         # normalized addresses whose scope role is 'bridge'
        self.scope_entries: list[dict] = []
        for sc in _l(manifest.get("scope_addresses")):
            sc = _d(sc)
            na = _norm_addr(sc.get("address"))
            if na:
                self.scope_norm.add(na)
                self.scope_pairs.add((na, sc.get("chain_id")))
                if sc.get("role") == "bridge":
                    self.bridge_addrs.add(na)
                self.scope_entries.append(dict(sc, _norm=na))
        self.frontmatter: Optional[dict] = None
        self.body: str = ""

    def scoped(self, address: Any, chain_id: Any) -> Optional[bool]:
        """None when the address is not a parseable hex address (E-ADDR-* reports it), else membership."""
        na = _norm_addr(address)
        if na is None:
            return None
        return (na, chain_id) in self.scope_pairs


# --------------------------------------------------------------------------------------
# Identity and addresses
# --------------------------------------------------------------------------------------
def _walk_addresses(node: Any, path: str, out: list) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            sub = f"{path}.{k}"
            if k in ADDRESS_KEYS:
                if isinstance(v, str):
                    out.append((sub, v))
                elif isinstance(v, list):
                    for i, x in enumerate(v):
                        if isinstance(x, str):
                            out.append((f"{sub}[{i}]", x))
                        elif x is not None:
                            out.append((f"{sub}[{i}]", x))
                elif v is not None:
                    out.append((sub, v))
            else:
                _walk_addresses(v, sub, out)
    elif isinstance(node, list):
        for i, x in enumerate(node):
            _walk_addresses(x, f"{path}[{i}]", out)


def check_identity(ctx: Ctx, issues: Issues) -> None:
    m = ctx.m
    target = _d(m.get("target"))
    req = _d(target.get("requested"))
    obs = _d(target.get("observed"))
    # every address field anywhere
    found: list = []
    _walk_addresses(m, "$", found)
    for path, val in found:
        ok, reason = ddcore.validate_address(val)
        if not ok:
            code = "E-ADDR-CHECKSUM" if reason and "checksum" in reason else "E-ADDR-MALFORMED"
            issues.error(code, f"{reason}: {val!r}", path)
    # requested vs observed chain
    if _is_int(req.get("chain_id")) and _is_int(obs.get("chain_id")) and req["chain_id"] != obs["chain_id"]:
        issues.error("E-CHAIN-MISMATCH",
                     f"requested chain_id {req['chain_id']} != observed chain_id {obs['chain_id']}",
                     "$.target.observed.chain_id")
    hx = obs.get("rpc_chain_id_hex")
    if isinstance(hx, str) and re.match(r"^0x[0-9a-fA-F]+$", hx) and _is_int(obs.get("chain_id")):
        if int(hx, 16) != obs["chain_id"]:
            issues.error("E-CHAIN-MISMATCH",
                         f"observed.rpc_chain_id_hex {hx} decodes to {int(hx, 16)} != observed.chain_id {obs['chain_id']}",
                         "$.target.observed.rpc_chain_id_hex")
    # checksum form of the target
    if ddcore.is_hex_address(req.get("address")):
        want = ddcore.to_checksum_address(req["address"])
        if target.get("address_checksum") != want:
            issues.error("E-ADDR-CHECKSUM",
                         f"target.address_checksum must equal EIP-55 form of requested.address ({want})",
                         "$.target.address_checksum")
    # scope must contain the target as role token on the target chain (five conditions; unchanged in v1.1)
    if ctx.target_addr is not None:
        runtime_hash = _lower(_d(target.get("runtime")).get("code_hash"))
        matched = False
        for i, sc in enumerate(_l(m.get("scope_addresses"))):
            sc = _d(sc)
            if _norm_addr(sc.get("address")) != ctx.target_addr:
                continue
            problems = []
            if sc.get("chain_id") != ctx.target_chain:
                problems.append(f"chain_id {sc.get('chain_id')} != target chain {ctx.target_chain}")
            if sc.get("role") != "token":
                problems.append(f"role {sc.get('role')!r} != 'token'")
            if sc.get("provenance") != "user_supplied":
                problems.append(f"provenance {sc.get('provenance')!r} != 'user_supplied'")
            if sc.get("runtime_status") != "contract":
                problems.append(f"runtime_status {sc.get('runtime_status')!r} != 'contract'")
            if _lower(sc.get("code_hash")) != runtime_hash or not runtime_hash:
                problems.append("code_hash != target.runtime.code_hash")
            if not problems:
                matched = True
                break
        if not matched:
            issues.error("E-SCOPE-TARGET",
                         "scope_addresses has no entry for the target with chain_id == target chain, role 'token', "
                         "provenance 'user_supplied', runtime_status 'contract' and code_hash == target.runtime.code_hash",
                         "$.scope_addresses")


# --------------------------------------------------------------------------------------
# Pins
# --------------------------------------------------------------------------------------
def _check_one_pin(p: dict, path: str, issues: Issues) -> None:
    bh = p.get("block_hash")
    bn = p.get("block_number")
    ph, why = ddcore.looks_like_placeholder_hash(bh)
    if ph:
        issues.error("E-PIN-PLACEHOLDER", f"block_hash rejected: {why}", f"{path}.block_hash")
    if not _is_int(bn) or bn < 1:
        issues.error("E-PIN-PLACEHOLDER", f"block_number must be an integer >= 1 (got {bn!r})", f"{path}.block_number")
    h = _d(p.get("captured_header"))
    # header consistency
    try:
        hn = ddcore.hex_to_int(h.get("number"))
        if _is_int(bn) and hn != bn:
            issues.error("E-PIN-HEADER-MISMATCH", f"captured_header.number {hn} != block_number {bn}",
                         f"{path}.captured_header.number")
    except Exception:
        issues.error("E-PIN-HEADER-MISMATCH", "captured_header.number is not a hex quantity",
                     f"{path}.captured_header.number")
    hh = h.get("hash")
    if _lower(hh) != _lower(bh) or not isinstance(hh, str):
        issues.error("E-PIN-HEADER-MISMATCH", "captured_header.hash != block_hash", f"{path}.captured_header.hash")
    hph, _ = ddcore.looks_like_placeholder_hash(hh)
    if hph:
        issues.error("E-PIN-HEADER-MISMATCH", "captured_header.hash looks like a placeholder",
                     f"{path}.captured_header.hash")
    parent = h.get("parentHash")
    pph, pwhy = ddcore.looks_like_placeholder_hash(parent)
    if pph:
        issues.error("E-PIN-HEADER-MISMATCH", f"captured_header.parentHash rejected: {pwhy}",
                     f"{path}.captured_header.parentHash")
    elif isinstance(hh, str) and _lower(parent) == _lower(hh):
        issues.error("E-PIN-HEADER-MISMATCH", "captured_header.parentHash == captured_header.hash",
                     f"{path}.captured_header.parentHash")
    # time consistency
    ts = p.get("timestamp_unix")
    try:
        hts = ddcore.hex_to_int(h.get("timestamp"))
        if _is_int(ts) and hts != ts:
            issues.error("E-PIN-TIME", f"captured_header.timestamp {hts} != timestamp_unix {ts}",
                         f"{path}.captured_header.timestamp")
    except Exception:
        issues.error("E-PIN-TIME", "captured_header.timestamp is not a hex quantity",
                     f"{path}.captured_header.timestamp")
    if _is_int(ts):
        iso = None
        try:
            iso = ddcore.iso_utc(ts)
        except (OverflowError, ValueError, OSError):
            issues.error("E-PIN-TIME", f"timestamp_unix {ts} cannot be converted to a UTC time (out of range)",
                         f"{path}.timestamp_unix")
        if iso is not None and p.get("timestamp_utc") != iso:
            issues.error("E-PIN-TIME", f"timestamp_utc {p.get('timestamp_utc')!r} != iso_utc(timestamp_unix) {iso}",
                         f"{path}.timestamp_utc")
        if ts < FIRST_PUBLIC_EVM_TIMESTAMP:
            issues.error("E-PIN-TIME", f"timestamp_unix {ts} predates the first public EVM chain",
                         f"{path}.timestamp_unix")
        try:
            cap = ddcore.parse_iso_utc(p.get("captured_at_utc"))
            if ts > cap + PIN_FUTURE_TOLERANCE_S:
                issues.error("E-PIN-TIME", f"block timestamp {ts} is in the future relative to captured_at_utc "
                             f"({cap}); pins cannot be captured before their block exists", f"{path}.captured_at_utc")
            elif abs(cap - ts) > PIN_STALE_S:
                issues.warn("W-PIN-STALE", f"captured_at_utc is {abs(cap - ts) // 86400} days from the block "
                            "timestamp; state may be stale relative to capture", f"{path}.captured_at_utc")
        except Exception:
            issues.error("E-PIN-TIME", "captured_at_utc is not a canonical UTC timestamp", f"{path}.captured_at_utc")
    else:
        issues.error("E-PIN-TIME", "timestamp_unix must be an integer", f"{path}.timestamp_unix")


def check_pins(ctx: Ctx, issues: Issues) -> None:
    m = ctx.m
    seen: set = set()
    for i, p in enumerate(_l(m.get("pins"))):
        p = _d(p)
        path = f"$.pins[{i}]"
        pid = p.get("pin_id")
        if pid in seen:
            issues.error("E-SCHEMA", f"duplicate pin_id {pid!r}", f"{path}.pin_id")
        seen.add(pid)
        try:
            _check_one_pin(p, path, issues)
        except Exception as e:  # one malformed pin must not abort the others
            issues.error("E-SCHEMA", f"pin {pid!r} check aborted on malformed input: {type(e).__name__}: {e}", path)
    # primary pin
    ppid = m.get("primary_pin_id")
    if ppid not in ctx.pins:
        issues.error("E-PIN-PRIMARY", f"primary_pin_id {ppid!r} not found in pins", "$.primary_pin_id")
    elif ctx.pins[ppid].get("chain_id") != ctx.target_chain:
        issues.error("E-PIN-PRIMARY", f"primary pin {ppid} is on chain {ctx.pins[ppid].get('chain_id')}, "
                     f"target chain is {ctx.target_chain}", "$.primary_pin_id")
    # cross-pin consistency per chain: a higher block carries a strictly later timestamp; equal blocks agree
    by_chain: dict[int, list] = {}
    for i, p in enumerate(_l(m.get("pins"))):
        p = _d(p)
        if _is_int(p.get("chain_id")) and _is_int(p.get("block_number")) and _is_int(p.get("timestamp_unix")):
            by_chain.setdefault(p["chain_id"], []).append(
                (p["block_number"], p["timestamp_unix"], _lower(p.get("block_hash")), p.get("pin_id"), i))
    for chain, lst in by_chain.items():
        lst.sort(key=lambda t: (t[0], t[1]))
        for a, b in zip(lst, lst[1:]):
            if a[0] < b[0] and not a[1] < b[1]:
                issues.error("E-PIN-TIME", f"pins {a[3]} (block {a[0]}, timestamp {a[1]}) and {b[3]} (block {b[0]}, "
                             f"timestamp {b[1]}) on chain {chain} are not monotonic: a higher block must carry a "
                             "strictly later timestamp", f"$.pins[{b[4]}].timestamp_unix")
            elif a[0] == b[0]:
                if a[1] != b[1]:
                    issues.error("E-PIN-TIME", f"pins {a[3]} and {b[3]} are the same block {a[0]} on chain {chain} "
                                 "but carry different timestamps", f"$.pins[{b[4]}].timestamp_unix")
                if a[2] != b[2]:
                    issues.error("E-PIN-HEADER-MISMATCH", f"pins {a[3]} and {b[3]} are the same block {a[0]} on chain "
                                 f"{chain} but carry different block hashes", f"$.pins[{b[4]}].block_hash")
    # unused pins (chain with no scope/evidence/finding entry)
    used_chains: set = set()
    for key in ("scope_addresses", "evidence", "findings"):
        for e in _l(m.get(key)):
            used_chains.add(_d(e).get("chain_id"))
    for i, p in enumerate(_l(m.get("pins"))):
        p = _d(p)
        if p.get("chain_id") not in used_chains:
            issues.warn("W-PIN-UNUSED", f"pin {p.get('pin_id')} is on chain {p.get('chain_id')} which has no "
                        "scope, evidence or finding entry", f"$.pins[{i}]")


# --------------------------------------------------------------------------------------
# Chains referenced anywhere must be pinned; findings stay on one chain unless bridge legs
# --------------------------------------------------------------------------------------
def check_chains(ctx: Ctx, issues: Issues) -> None:
    m = ctx.m
    obs_chain = _d(_d(m.get("target")).get("observed")).get("chain_id")
    if _is_int(obs_chain) and obs_chain not in ctx.pinned_chains:
        issues.error("E-CHAIN-UNPINNED", f"observed chain {obs_chain} has no pin", "$.target.observed.chain_id")
    if _is_int(ctx.target_chain) and ctx.target_chain not in ctx.pinned_chains:
        issues.error("E-CHAIN-UNPINNED", f"requested chain {ctx.target_chain} has no pin", "$.target.requested.chain_id")
    for key in ("scope_addresses", "evidence", "findings"):
        for i, e in enumerate(_l(m.get(key))):
            cid = _d(e).get("chain_id")
            if _is_int(cid) and cid not in ctx.pinned_chains:
                issues.error("E-CHAIN-UNPINNED", f"chain {cid} referenced without any pin", f"$.{key}[{i}].chain_id")
    for i, f in enumerate(_l(m.get("findings"))):
        f = _d(f)
        fid = f.get("finding_id")
        cited = [(eid, ctx.evidence[eid]) for eid in _strs(f.get("evidence_ids")) if eid in ctx.evidence]
        bridge_leg = (f.get("surface") == "external_dependencies"
                      or _norm_addr(f.get("address")) in ctx.bridge_addrs
                      or any(_norm_addr(ev.get("address")) in ctx.bridge_addrs for _, ev in cited))
        for eid, ev in cited:
            if _is_int(ev.get("chain_id")) and ev["chain_id"] not in ctx.pinned_chains:
                issues.error("E-FINDING-CHAIN", f"finding {fid} references evidence {eid} on unpinned chain "
                             f"{ev['chain_id']}", f"$.findings[{i}].evidence_ids")
            elif _is_int(ev.get("chain_id")) and _is_int(f.get("chain_id")) and ev["chain_id"] != f["chain_id"]:
                if bridge_leg:
                    issues.warn("W-FINDING-CROSS-CHAIN", f"finding {fid} on chain {f['chain_id']} cites evidence {eid} "
                                f"on chain {ev['chain_id']} (permitted for a bridge leg; confirm intent)",
                                f"$.findings[{i}].evidence_ids")
                else:
                    issues.error("E-FINDING-CHAIN", f"finding {fid} on chain {f['chain_id']} cites evidence {eid} on "
                                 f"chain {ev['chain_id']}; only bridge-leg findings (surface external_dependencies, or "
                                 "an address scoped with role 'bridge') may cross chains - split the finding per chain",
                                 f"$.findings[{i}].evidence_ids")


# --------------------------------------------------------------------------------------
# Metadata
# --------------------------------------------------------------------------------------
def _is_digit_string(v: Any) -> bool:
    return isinstance(v, str) and v.isdigit()


def check_metadata(ctx: Ctx, issues: Issues) -> None:
    meta = _d(_d(ctx.m.get("target")).get("metadata"))
    for key in ("name", "symbol", "decimals", "total_supply"):
        f = _d(meta.get(key))
        path = f"$.target.metadata.{key}"
        status = f.get("status")
        val = f.get("value")
        if status == "resolved":
            if val is None:
                issues.error("E-META-STATUS", f"{key} status 'resolved' but value is null", path)
            elif key == "decimals":
                iv = val if _is_int(val) else (int(val) if _is_digit_string(val) else None)
                if iv is None or not (0 <= iv <= 255):
                    issues.error("E-META-STATUS", f"decimals resolved value must be an integer 0..255 (got {val!r})", path)
            elif key == "total_supply":
                if not ((_is_int(val) and val >= 0) or _is_digit_string(val)):
                    issues.error("E-META-STATUS", "total_supply resolved value must be a non-negative integer or "
                                 f"decimal digit string (got {val!r})", path)
        elif status == "unresolved":
            if val is not None:
                issues.error("E-META-STATUS", f"{key} status 'unresolved' but value is not null", path)
            if not _s(f.get("reason")).strip():
                issues.error("E-META-STATUS", f"{key} status 'unresolved' requires a non-empty reason", path)
        elif status == "nonstandard":
            if val is None:
                issues.error("E-META-STATUS", f"{key} status 'nonstandard' but value is null (keep the raw value)", path)
        eid = f.get("evidence_id")
        if isinstance(eid, str) and eid and eid not in ctx.evidence:
            issues.error("E-EVIDENCE-DANGLING", f"metadata.{key}.evidence_id {eid} does not exist", path)


def expected_report_symbol(manifest: dict) -> Optional[str]:
    sym = _d(_d(_d(manifest.get("target")).get("metadata")).get("symbol"))
    status = sym.get("status")
    if status == "resolved":
        return str(sym.get("value")) if sym.get("value") is not None else None
    if status == "unresolved":
        return "unresolved"
    if status == "nonstandard":
        return f"nonstandard:{sym.get('value')}"
    return None


# --------------------------------------------------------------------------------------
# Scope addresses
# --------------------------------------------------------------------------------------
def check_scope(ctx: Ctx, issues: Issues) -> None:
    for i, sc in enumerate(_l(ctx.m.get("scope_addresses"))):
        sc = _d(sc)
        path = f"$.scope_addresses[{i}]"
        rs = sc.get("runtime_status")
        ch = sc.get("code_hash")
        if ch is not None:
            ph, why = ddcore.looks_like_placeholder_hash(ch)
            if ph:
                issues.error("E-PIN-PLACEHOLDER", f"code_hash rejected: {why}", f"{path}.code_hash")
        if rs == "contract":
            ph, _ = ddcore.looks_like_placeholder_hash(ch)
            if ch is None or _lower(ch) == ddcore.EMPTY_CODE_HASH or ph:
                issues.error("E-SCOPE-RUNTIME", "runtime_status 'contract' requires a real non-empty code_hash "
                             f"(got {ch!r})", f"{path}.code_hash")
        elif rs == "eoa":
            if ch is not None and _lower(ch) != ddcore.EMPTY_CODE_HASH:
                issues.error("E-SCOPE-RUNTIME", "runtime_status 'eoa' requires code_hash null or the empty-code hash",
                             f"{path}.code_hash")
        elif rs == "unknown":
            lid = sc.get("limitation_id")
            if sc.get("material") is True and (not isinstance(lid, str) or lid not in ctx.limitations):
                issues.error("E-SCOPE-RUNTIME", "material address with runtime_status 'unknown' must cite an existing "
                             "coverage limitation_id", f"{path}.limitation_id")
        lid = sc.get("limitation_id")
        if isinstance(lid, str) and lid and lid not in ctx.limitations and not (rs == "unknown" and sc.get("material") is True):
            issues.error("E-EVIDENCE-DANGLING", f"limitation_id {lid} does not exist", f"{path}.limitation_id")
        pid = sc.get("pin_id")
        if pid not in ctx.pins:
            issues.error("E-CHAIN-UNPINNED", f"scope entry pin_id {pid!r} does not exist", f"{path}.pin_id")
        elif ctx.pins[pid].get("chain_id") != sc.get("chain_id"):
            issues.error("E-CHAIN-UNPINNED", f"pin on different chain: pin {pid} is chain "
                         f"{ctx.pins[pid].get('chain_id')}, entry is chain {sc.get('chain_id')}", f"{path}.pin_id")
    # limitation affected_addresses must be scope entries (any chain: limitations carry no chain_id)
    for i, lim in enumerate(_l(_d(ctx.m.get("coverage")).get("limitations"))):
        for j, a in enumerate(_l(_d(lim).get("affected_addresses"))):
            na = _norm_addr(a)
            if na is not None and na not in ctx.scope_norm:
                issues.error("E-SCOPE-ADDRESS", f"limitation affected address {a} is not a scope_addresses entry",
                             f"$.coverage.limitations[{i}].affected_addresses[{j}]")


def check_target(ctx: Ctx, issues: Issues) -> None:
    """target.runtime (pin, proxy resolution), target.deployment and the target's own scope entry."""
    target = _d(ctx.m.get("target"))
    rt = _d(target.get("runtime"))
    pid = rt.get("pin_id")
    if pid is not None and pid not in ctx.pins:
        issues.error("E-EVIDENCE-DANGLING", f"target.runtime.pin_id {pid!r} does not exist", "$.target.runtime.pin_id")
    elif pid is not None and ctx.pins[pid].get("chain_id") != ctx.target_chain:
        issues.error("E-CHAIN-UNPINNED", f"target.runtime.pin_id {pid} is on chain {ctx.pins[pid].get('chain_id')}, "
                     f"the target chain is {ctx.target_chain}", "$.target.runtime.pin_id")
    proxy = _d(rt.get("proxy"))
    status = proxy.get("status")
    on_target_chain = [sc for sc in ctx.scope_entries if sc.get("chain_id") == ctx.target_chain]
    for field, want_role in (("implementation", "implementation"), ("admin", "proxy_admin"), ("beacon", "beacon")):
        v = proxy.get(field)
        if v is None:
            continue
        na = _norm_addr(v)
        if na is None:
            continue  # E-ADDR-* reports the shape
        path = f"$.target.runtime.proxy.{field}"
        matches = [sc for sc in on_target_chain if sc["_norm"] == na]
        if not matches:
            issues.error("E-SCOPE-ADDRESS", f"target.runtime.proxy.{field} {v} is not a scope_addresses entry on the "
                         f"target chain {ctx.target_chain}", path)
        if field == "implementation":
            ok = any(sc.get("role") == "implementation" and sc.get("runtime_status") == "contract" for sc in matches)
            if not ok:
                issues.error("E-SCOPE-RUNTIME", f"proxy.implementation {v} must be in scope on the target chain with "
                             "role 'implementation' and runtime_status 'contract'", path)
        elif not any(sc.get("role") == want_role for sc in matches):
            issues.error("E-SCOPE-RUNTIME", f"proxy.{field} {v} must be in scope on the target chain with role "
                         f"'{want_role}'", path)
    if status in PROXY_STATUSES_WITH_IMPLEMENTATION and proxy.get("implementation") is None:
        issues.error("E-SCOPE-RUNTIME", f"proxy.status {status!r} requires a non-null implementation address (resolve "
                     "the implementation, or set status 'unknown' with a limitation)", "$.target.runtime.proxy.implementation")
    ich = proxy.get("implementation_code_hash")
    if ich is not None:
        ph, why = ddcore.looks_like_placeholder_hash(ich)
        if ph:
            issues.error("E-PIN-PLACEHOLDER", f"proxy.implementation_code_hash rejected: {why}",
                         "$.target.runtime.proxy.implementation_code_hash")
    dep = _d(target.get("deployment"))
    if dep.get("status") == "resolved" and dep.get("tx_hash") is not None:
        ph, why = ddcore.looks_like_placeholder_hash(dep.get("tx_hash"))
        if ph:
            issues.error("E-PIN-PLACEHOLDER", f"deployment.tx_hash rejected: {why}", "$.target.deployment.tx_hash")
    # the target's own scope entry is material by definition
    if ctx.target_addr is not None:
        for i, sc in enumerate(ctx.scope_entries):
            if sc["_norm"] == ctx.target_addr and sc.get("chain_id") == ctx.target_chain and sc.get("role") == "token":
                if sc.get("material") is not True:
                    issues.error("E-SCOPE-RUNTIME", "the target's own scope entry must have material: true",
                                 "$.scope_addresses")
                break


# --------------------------------------------------------------------------------------
# Report: frontmatter, identity, hash, sections, body scans
# --------------------------------------------------------------------------------------
def parse_frontmatter(text: str) -> tuple[Optional[dict], str, list[str]]:
    """Return (frontmatter dict or None, body, duplicate keys).

    Frontmatter = a leading '---' line (a UTF-8 BOM and blank lines before it are tolerated),
    'key: value' lines, closing '---'. On duplicate keys the first value is kept and the key is
    reported in the third element (the caller turns that into E-REPORT-IDENTITY)."""
    if text.startswith("\ufeff"):  # UTF-8 BOM decoded to U+FEFF
        text = text[1:]
    lines = text.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != "---":
        return None, text, []
    fm: dict = {}
    dups: list[str] = []
    for idx in range(start + 1, len(lines)):
        line = lines[idx]
        if line.strip() == "---":
            body = "\n".join(lines[idx + 1:])
            return fm, body, dups
        if not line.strip():
            continue
        if ":" not in line:
            return None, text, dups
        k, v = line.split(":", 1)
        k = k.strip()
        if k in fm:
            dups.append(k)
            continue
        fm[k] = v.strip()
    return None, text, dups  # no closing delimiter


def normalize_markdown(text: str) -> str:
    """Strip fenced code blocks (``` / ~~~) and HTML comments (single- or multi-line) so that
    invisible text cannot satisfy a body requirement. Line endings are normalized to '\\n'."""
    out: list[str] = []
    fence: Optional[tuple[str, int]] = None
    in_comment = False
    for line in text.splitlines():
        if fence is not None:
            m = _FENCE_CLOSE_RE.match(line)
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                fence = None
            continue
        if in_comment:
            j = line.find("-->")
            if j < 0:
                continue
            in_comment = False
            line = line[j + 3:]
        m = _FENCE_OPEN_RE.match(line)
        if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = (m.group(1)[0], len(m.group(1)))
            continue
        while True:
            i = line.find("<!--")
            if i < 0:
                break
            j = line.find("-->", i + 4)
            if j < 0:
                line = line[:i]
                in_comment = True
                break
            line = line[:i] + line[j + 3:]
        out.append(line)
    return "\n".join(out)


def _sections(body: str) -> dict[str, str]:
    """Map H2 heading text -> section text (all sections with the same heading concatenated)."""
    secs: dict[str, str] = {}
    matches = list(_H2_RE.finditer(body))
    for k, m in enumerate(matches):
        name = m.group(1).strip()
        end = matches[k + 1].start() if k + 1 < len(matches) else len(body)
        secs[name] = secs.get(name, "") + "\n" + body[m.end():end]
    return secs


def _fm_int(v: Any) -> Optional[int]:
    try:
        return int(str(v).strip())
    except Exception:
        return None


def check_report(ctx: Ctx, issues: Issues) -> None:
    m = ctx.m
    rep = _d(m.get("report"))
    if ctx.report_text is None:
        issues.error("E-REPORT-MISSING", ctx.report_error or f"report file not found: {ctx.report_path!r}", "$.report.path")
        return
    want_raw = rep.get("sha256")
    want = _lower(want_raw)
    raw = ctx.report_bytes if ctx.report_bytes is not None else ctx.report_text.encode("utf-8")
    got = ddcore.sha256_bytes(raw)
    if not _SHA256_RE.match(want):
        issues.error("E-REPORT-HASH", f"manifest.report.sha256 is empty or malformed ({want_raw!r}); it must be the "
                     "sha256 of the report file bytes", "$.report.sha256")
    elif got != want:
        issues.error("E-REPORT-HASH", f"sha256 of report file bytes {got} != manifest.report.sha256 {want}", "$.report.sha256")
    fm, body, dups = parse_frontmatter(ctx.report_text)
    ctx.frontmatter = fm
    if dups:
        issues.error("E-REPORT-IDENTITY", f"duplicate frontmatter keys {sorted(set(dups))}; each identity key must "
                     "appear exactly once", "report:frontmatter")
    if fm is None:
        issues.error("E-REPORT-IDENTITY", "report frontmatter missing or unparseable (leading '---', 'key: value' "
                     "lines, closing '---')", "report:frontmatter")
        body = ctx.report_text
    else:
        if _fm_int(fm.get("evm_dd_report")) != 1:
            issues.error("E-REPORT-IDENTITY", f"frontmatter evm_dd_report must be 1 (got {fm.get('evm_dd_report')!r})",
                         "report:frontmatter.evm_dd_report")
        if _fm_int(fm.get("target_chain_id")) != ctx.target_chain:
            issues.error("E-REPORT-IDENTITY", f"frontmatter target_chain_id {fm.get('target_chain_id')!r} != manifest "
                         f"target chain {ctx.target_chain}", "report:frontmatter.target_chain_id")
        fa = _norm_addr(fm.get("target_address"))
        if fa is None or fa != ctx.target_addr:
            issues.error("E-REPORT-IDENTITY", f"frontmatter target_address {fm.get('target_address')!r} != manifest "
                         "target address", "report:frontmatter.target_address")
        ppid = m.get("primary_pin_id")
        pin = ctx.pins.get(ppid) if isinstance(ppid, str) else None
        if fm.get("primary_pin_id") != ppid:
            issues.error("E-REPORT-IDENTITY", f"frontmatter primary_pin_id {fm.get('primary_pin_id')!r} != manifest "
                         f"primary_pin_id {ppid!r}", "report:frontmatter.primary_pin_id")
        if pin is not None:
            if _fm_int(fm.get("primary_pin_block")) != pin.get("block_number"):
                issues.error("E-REPORT-IDENTITY", f"frontmatter primary_pin_block {fm.get('primary_pin_block')!r} != "
                             f"pin block_number {pin.get('block_number')}", "report:frontmatter.primary_pin_block")
            if _lower(fm.get("primary_pin_block_hash")) != _lower(pin.get("block_hash")) or not fm.get("primary_pin_block_hash"):
                issues.error("E-REPORT-IDENTITY", "frontmatter primary_pin_block_hash != primary pin block_hash",
                             "report:frontmatter.primary_pin_block_hash")
        if fm.get("mode") != ctx.mode:
            issues.error("E-MODE-MISMATCH", f"frontmatter mode {fm.get('mode')!r} != manifest mode {ctx.mode!r}",
                         "report:frontmatter.mode")
        exp_sym = expected_report_symbol(m)
        if exp_sym is not None and fm.get("target_symbol") != exp_sym:
            issues.error("E-META-REPORT", f"frontmatter target_symbol {fm.get('target_symbol')!r} != expected "
                         f"{exp_sym!r} from manifest metadata", "report:frontmatter.target_symbol")
    # body checks run on the NORMALIZED body: fenced code and HTML comments cannot satisfy a requirement
    body = normalize_markdown(body)
    ctx.body = body
    if ctx.target_addr is not None and ctx.target_addr not in body.lower():
        issues.error("E-REPORT-TARGET-ABSENT", "target address does not occur in the visible report body (outside "
                     "frontmatter, code fences and HTML comments)", "report:body")
    secs = _sections(body)
    required = FOCUSED_SECTIONS if ctx.mode == "focused" else REQUIRED_SECTIONS
    missing = [s for s in required if s not in secs]
    if missing:
        issues.error("E-REPORT-SECTIONS", f"missing required H2 sections: {missing}", "report:body")
    seen_unscoped: set = set()
    for tok in _ADDR_TOKEN_RE.findall(body):
        na = tok.lower()
        if na not in ctx.scope_norm and na not in seen_unscoped:
            seen_unscoped.add(na)
            issues.warn("W-REPORT-UNSCOPED-ADDRESS", f"address {tok} appears in the report but not in scope_addresses",
                        "report:body")
    # Ratings table must render manifest.ratings: rows '| <surface key> | <rating> | ...'
    ratings = ctx.m.get("ratings")
    if isinstance(ratings, dict) and ratings:
        rsec = secs.get("Ratings")
        if rsec is None:
            issues.error("E-REPORT-RATINGS", "manifest has ratings but the report has no '## Ratings' section to "
                         "render them", "report:body.Ratings")
        else:
            rows: dict[str, list[str]] = {}
            for line in rsec.splitlines():
                s = line.strip()
                if not s.startswith("|"):
                    continue
                cells = [_ws(c) for c in s.strip("|").split("|")]
                if len(cells) >= 2:
                    rows.setdefault(cells[0], []).append(cells[1])
            for key, r in ratings.items():
                want_r = _d(r).get("rating")
                got_rows = rows.get(str(key))
                if not got_rows:
                    issues.error("E-REPORT-RATINGS", f"no '| {key} | <rating> | ...' row under '## Ratings'",
                                 f"report:body.Ratings.{key}")
                elif isinstance(want_r, str):
                    for g in got_rows:
                        if g != want_r:
                            issues.error("E-REPORT-RATINGS", f"report Ratings row {key} says {g!r} but "
                                         f"manifest.ratings.{key}.rating is {want_r!r}", f"report:body.Ratings.{key}")
    # Verdict text must carry the manifest answer verbatim (whitespace-normalized)
    answer = _ws(_d(ctx.m.get("verdict")).get("answer"))
    vsec = secs.get("Verdict")
    if answer:
        if vsec is None:
            issues.error("E-REPORT-VERDICT", "no '## Verdict' section to carry manifest.verdict.answer", "report:body.Verdict")
        elif answer not in _ws(vsec):
            issues.error("E-REPORT-VERDICT", "manifest.verdict.answer does not occur (whitespace-normalized) in the "
                         "report '## Verdict' section; the report renders the manifest, it does not restate it",
                         "report:body.Verdict")
    # verdict language (heuristic)
    if vsec is not None:
        hit = _UNSAFE_RE.search(vsec)
        if hit:
            issues.warn("W-VERDICT-LANGUAGE", f"unconditional safety phrasing under '## Verdict': {hit.group(0)!r}",
                        "report:body.Verdict")


# --------------------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------------------
def check_checks(ctx: Ctx, issues: Issues) -> None:
    m = ctx.m
    checks = _l(m.get("checks"))
    present = {_d(c).get("check_id") for c in checks}
    if ctx.mode in ("broad", "formal"):
        for cid in CORE_CHECKS:
            if cid not in present:
                issues.error("E-CHECK-CORE-MISSING", f"core check {cid} ({CORE_CHECKS[cid]}) absent from checks "
                             "(must be present with any status)", "$.checks")
    seen: set = set()
    for i, c in enumerate(checks):
        c = _d(c)
        path = f"$.checks[{i}]"
        cid = c.get("check_id")
        if cid in seen:
            issues.error("E-CHECK-STATUS", f"duplicate check_id {cid!r}", f"{path}.check_id")
        seen.add(cid)
        surface = c.get("surface")
        if surface not in SURFACES:
            issues.error("E-CHECK-STATUS", f"unknown surface key {surface!r}", f"{path}.surface")
        status = c.get("status")
        ev = _strs(c.get("evidence_ids"))
        fids = _strs(c.get("finding_ids"))
        reason = _s(c.get("reason")).strip()
        sev = c.get("severity")
        linked = [(fid, ctx.findings[fid]) for fid in fids if fid in ctx.findings]
        if status in ("pass", "finding") and not ev:
            issues.error("E-CHECK-STATUS", f"{cid}: status '{status}' requires at least one evidence id", f"{path}.evidence_ids")
        if status in ("unknown", "skipped", "not_applicable") and not reason:
            issues.error("E-CHECK-STATUS", f"{cid}: status '{status}' requires a non-empty reason", f"{path}.reason")
        if status == "finding":
            if sev not in SEVERITIES:
                issues.error("E-CHECK-STATUS", f"{cid}: status 'finding' requires a severity", f"{path}.severity")
            if not fids:
                issues.error("E-CHECK-STATUS", f"{cid}: status 'finding' requires at least one finding id", f"{path}.finding_ids")
            else:
                sevs = [f.get("severity") for _, f in linked if f.get("severity") in SEV_RANK]
                if sev in SEV_RANK and sevs:
                    mx = max(sevs, key=lambda x: SEV_RANK[x])
                    if SEV_RANK[sev] < SEV_RANK[mx]:
                        issues.error("E-CHECK-STATUS", f"{cid}: severity '{sev}' is lower than its linked finding "
                                     f"severity '{mx}'; a check carries at least the severity of its findings",
                                     f"{path}.severity")
                    elif SEV_RANK[sev] > SEV_RANK[mx]:
                        issues.warn("W-CHECK-SEVERITY-UNSUPPORTED", f"{cid}: severity '{sev}' is higher than every "
                                    f"linked finding (max '{mx}'); add a finding that supports it or lower the severity",
                                    f"{path}.severity")
        if status in ("pass", "not_applicable"):
            if fids:
                issues.error("E-CHECK-STATUS", f"{cid}: status '{status}' cannot carry finding ids {fids}; a check with "
                             "findings has status 'finding'", f"{path}.finding_ids")
            if sev is not None:
                issues.error("E-CHECK-STATUS", f"{cid}: status '{status}' cannot carry a severity (got {sev!r})",
                             f"{path}.severity")
        if status in ("unknown", "skipped"):
            if sev is not None:
                issues.error("E-CHECK-STATUS", f"{cid}: status '{status}' cannot carry a severity (unknown/skipped is "
                             "never a pass nor a finding)", f"{path}.severity")
            proven = [f for f in fids if _d(ctx.findings.get(f)).get("confidence") == "proven"]
            if proven:
                issues.error("E-CHECK-STATUS", f"{cid}: status '{status}' contradicts proven findings {proven}",
                             f"{path}.finding_ids")
        for fid, f in linked:
            if f.get("surface") != surface:
                issues.error("E-CHECK-STATUS", f"{cid}: linked finding {fid} is on surface {f.get('surface')!r} but the "
                             f"check is on {surface!r}; a finding hangs off a check of its own surface",
                             f"{path}.finding_ids")
        if isinstance(cid, str) and cid in ctx.affected_check_ids and status in ("pass", "not_applicable"):
            issues.error("E-CHECK-STATUS", f"{cid}: listed in a coverage limitation's affected_check_ids but status is "
                         f"'{status}'; a limited check is unknown, skipped or a partial finding", f"{path}.status")
        if status == "pass":
            types = [ctx.evidence[e].get("evidence_type") for e in ev if e in ctx.evidence]
            if types and all(t in DISCOVERY_EVIDENCE_TYPES for t in types):
                msg = (f"{cid}: status 'pass' is supported only by discovery-class evidence ({sorted(set(types))}); "
                       "a pass needs rpc_state/rpc_storage/bytecode/calldata/receipt/log_decoded/trace, "
                       "source_verified or simulation_counterfactual evidence")
                if cid in NO_THRESHOLD_CHECKS:
                    issues.error("E-CHECK-DISCOVERY-ONLY", msg + " (no-threshold authority check)", f"{path}.evidence_ids")
                else:
                    issues.warn("W-CHECK-DISCOVERY-ONLY", msg, f"{path}.evidence_ids")
        for eid in _l(c.get("evidence_ids")):
            if eid not in ctx.evidence:
                issues.error("E-EVIDENCE-DANGLING", f"{cid}: evidence id {eid!r} does not exist", f"{path}.evidence_ids")
        for fid in _l(c.get("finding_ids")):
            if fid not in ctx.findings:
                issues.error("E-EVIDENCE-DANGLING", f"{cid}: finding id {fid!r} does not exist", f"{path}.finding_ids")
        pid = c.get("pin_id")
        if pid is not None and pid not in ctx.pins:
            issues.error("E-EVIDENCE-DANGLING", f"{cid}: pin_id {pid!r} does not exist", f"{path}.pin_id")
        lid = c.get("limitation_id")
        if lid is not None and lid not in ctx.limitations:
            issues.error("E-EVIDENCE-DANGLING", f"{cid}: limitation_id {lid!r} does not exist", f"{path}.limitation_id")


# --------------------------------------------------------------------------------------
# Evidence and findings
# --------------------------------------------------------------------------------------
def check_evidence(ctx: Ctx, issues: Issues) -> None:
    seen: set = set()
    for i, e in enumerate(_l(ctx.m.get("evidence"))):
        e = _d(e)
        path = f"$.evidence[{i}]"
        eid = e.get("evidence_id")
        if eid in seen:
            issues.error("E-SCHEMA", f"duplicate evidence_id {eid!r}", f"{path}.evidence_id")
        seen.add(eid)
        pid = e.get("pin_id")
        if pid is not None and pid not in ctx.pins:
            issues.error("E-EVIDENCE-DANGLING", f"evidence {eid}: pin_id {pid!r} does not exist", f"{path}.pin_id")
        elif pid is not None:
            if ctx.pins[pid].get("chain_id") != e.get("chain_id"):
                issues.error("E-CHAIN-UNPINNED", f"pin on different chain: evidence {eid} is chain {e.get('chain_id')}, "
                             f"pin {pid} is chain {ctx.pins[pid].get('chain_id')}", f"{path}.pin_id")
            bn = e.get("block_number")
            if _is_int(bn) and _is_int(ctx.pins[pid].get("block_number")) and bn != ctx.pins[pid]["block_number"]:
                issues.error("E-EVIDENCE-DANGLING", f"evidence {eid}: block_number {bn} != block {ctx.pins[pid]['block_number']} "
                             f"of pin {pid}; a row is bound to one block", f"{path}.block_number")
        tx = e.get("tx_hash")
        tx_ok = False
        if tx is not None:
            ph, why = ddcore.looks_like_placeholder_hash(tx)
            if ph:
                issues.error("E-PIN-PLACEHOLDER", f"evidence {eid}: tx_hash rejected: {why}", f"{path}.tx_hash")
            else:
                tx_ok = True
        if e.get("evidence_type") in ONCHAIN_EVIDENCE_TYPES and pid is None and not tx_ok:
            issues.error("E-CHAIN-UNPINNED", f"evidence {eid} ({e.get('evidence_type')}) asserts chain state but carries "
                         "neither a pin_id nor a tx_hash; bind every onchain row to a pin or a transaction",
                         f"{path}.pin_id")
        sha = e.get("artifact_sha256")
        if isinstance(sha, str) and len(sha) == 64 and len(set(sha)) == 1:
            issues.error("E-PIN-PLACEHOLDER", f"evidence {eid}: artifact_sha256 is a repeated character (placeholder)",
                         f"{path}.artifact_sha256")
        if e.get("address") is not None and ctx.scoped(e.get("address"), e.get("chain_id")) is False:
            issues.error("E-SCOPE-ADDRESS", f"evidence {eid}: address {e.get('address')} on chain {e.get('chain_id')} is "
                         "not a scope_addresses entry (address + chain must be scoped)", f"{path}.address")


def check_findings(ctx: Ctx, issues: Issues) -> None:
    seen: set = set()
    for i, f in enumerate(_l(ctx.m.get("findings"))):
        f = _d(f)
        path = f"$.findings[{i}]"
        fid = f.get("finding_id")
        if fid in seen:
            issues.error("E-SCHEMA", f"duplicate finding_id {fid!r}", f"{path}.finding_id")
        seen.add(fid)
        ev = _strs(f.get("evidence_ids"))
        if not ev and f.get("confidence") != "unknown":
            issues.error("E-FINDING-NO-EVIDENCE", f"finding {fid}: no evidence ids but confidence "
                         f"{f.get('confidence')!r} (only 'unknown' may stand without evidence)", f"{path}.evidence_ids")
        for eid in _l(f.get("evidence_ids")):
            if eid not in ctx.evidence:
                issues.error("E-EVIDENCE-DANGLING", f"finding {fid}: evidence id {eid!r} does not exist", f"{path}.evidence_ids")
        if isinstance(fid, str) and fid not in ctx.linked_findings:
            issues.error("E-FINDING-UNLINKED", f"finding {fid} is not referenced by any check's finding_ids; every "
                         "finding hangs off a check", f"{path}.finding_id")
        pot = f.get("pin_or_tx")
        is_pin = isinstance(pot, str) and pot in ctx.pins
        is_tx = isinstance(pot, str) and bool(_TX_RE.match(pot))
        if not (is_pin or is_tx):
            issues.error("E-EVIDENCE-DANGLING", f"finding {fid}: pin_or_tx {pot!r} is neither an existing pin_id nor a "
                         "0x+64hex tx hash", f"{path}.pin_or_tx")
        elif is_tx:
            ph, why = ddcore.looks_like_placeholder_hash(pot)
            if ph:
                issues.error("E-PIN-PLACEHOLDER", f"finding {fid}: pin_or_tx tx hash rejected: {why}", f"{path}.pin_or_tx")
        hist = f.get("is_historical")
        if hist is True and is_pin:
            issues.error("E-EVIDENCE-DANGLING", f"finding {fid}: is_historical is true but pin_or_tx is a pin id; "
                         "historical rows cite the transaction hash", f"{path}.pin_or_tx")
        if hist is False and is_tx:
            issues.error("E-EVIDENCE-DANGLING", f"finding {fid}: is_historical is false but pin_or_tx is a tx hash; "
                         "current-state rows cite a pin", f"{path}.pin_or_tx")
        if f.get("address") is not None and ctx.scoped(f.get("address"), f.get("chain_id")) is False:
            issues.error("E-SCOPE-ADDRESS", f"finding {fid}: address {f.get('address')} on chain {f.get('chain_id')} is "
                         "not a scope_addresses entry (address + chain must be scoped)", f"{path}.address")
        cited = [ctx.evidence[e] for e in ev if e in ctx.evidence]
        if cited:
            types = {c.get("evidence_type") for c in cited}
            if f.get("evidence_type") not in types:
                issues.error("E-FINDING-EVIDENCE-TYPE", f"finding {fid}: evidence_type {f.get('evidence_type')!r} is not "
                             f"the type of any cited evidence row ({sorted(t for t in types if isinstance(t, str))})",
                             f"{path}.evidence_type")
            if is_pin:
                pins_of = [c.get("pin_id") for c in cited]
                chain = ctx.pins[pot].get("chain_id")
                if all(isinstance(p, str) and p in ctx.pins and p != pot and ctx.pins[p].get("chain_id") == chain
                       for p in pins_of):
                    issues.warn("W-FINDING-PIN-MISMATCH", f"finding {fid} is pinned at {pot} but every cited evidence "
                                f"row is pinned at {sorted(set(pins_of))} on the same chain; re-pin the finding or the "
                                "evidence", f"{path}.pin_or_tx")
        if f.get("severity") in ("critical", "high") and f.get("confidence") in ("proven", "strongly_supported"):
            types_l = [c.get("evidence_type") for c in cited]
            if types_l and all(t in DISCOVERY_EVIDENCE_TYPES for t in types_l):
                issues.warn("W-EVIDENCE-DISCOVERY-ONLY", f"finding {fid} is {f.get('severity')}/{f.get('confidence')} "
                            f"but every cited evidence is discovery-class ({sorted(set(types_l))}); material onchain "
                            "claims need rpc_state/rpc_storage/bytecode/calldata/receipt/log_decoded/trace",
                            f"{path}.evidence_ids")


# --------------------------------------------------------------------------------------
# Ratings
# --------------------------------------------------------------------------------------
def check_ratings(ctx: Ctx, issues: Issues) -> None:
    ratings = ctx.m.get("ratings")
    if ratings is None:
        ratings = {}
    ratings = _d(ratings)
    if ctx.mode in ("broad", "formal"):
        for key in SURFACES:
            if key not in ratings:
                issues.error("E-RATING-MISSING", f"rating {key} absent (required in {ctx.mode} mode)", f"$.ratings.{key}")
    for key, r in ratings.items():
        r = _d(r)
        path = f"$.ratings.{key}"
        basis = _strs(r.get("basis_check_ids"))
        rating = r.get("rating")
        note = _s(r.get("coverage_note")).strip()
        cq = r.get("coverage_qualified") is True
        if cq and len(note) < COVERAGE_NOTE_MIN_CHARS:
            issues.error("E-RATING-UNKNOWN-AS-LOW", f"rating {key}: coverage_qualified is true but coverage_note is "
                         f"shorter than {COVERAGE_NOTE_MIN_CHARS} characters; a qualification states what was not covered",
                         f"{path}.coverage_note")
        qualified = cq and len(note) >= COVERAGE_NOTE_MIN_CHARS
        unresolved_basis = []
        non_na_basis = []
        for cid in basis:
            c = ctx.checks.get(cid)
            if c is None:
                issues.error("E-EVIDENCE-DANGLING", f"rating {key}: basis check {cid!r} does not exist", f"{path}.basis_check_ids")
                continue
            st = c.get("status")
            if st in ("unknown", "skipped"):
                unresolved_basis.append(cid)
            if st != "not_applicable":
                non_na_basis.append(cid)
        if rating == "low":
            if not basis:
                issues.error("E-RATING-UNKNOWN-AS-LOW", f"rating {key} is 'low' with no basis_check_ids (nothing supports it)",
                             f"{path}.basis_check_ids")
            elif unresolved_basis and not qualified:
                issues.error("E-RATING-UNKNOWN-AS-LOW", f"rating {key} is 'low' over unknown/skipped basis checks "
                             f"{unresolved_basis} without coverage_qualified=true and a coverage_note", f"{path}.rating")
            if r.get("coverage") == "none":
                issues.error("E-RATING-UNKNOWN-AS-LOW", f"rating {key} is 'low' with coverage 'none'; nothing examined "
                             "cannot support a low rating", f"{path}.coverage")
        if rating == "not_applicable":
            if not basis:
                issues.error("E-RATING-UNKNOWN-AS-LOW", f"rating {key} is 'not_applicable' with no basis_check_ids; "
                             "not_applicable rests on not_applicable checks with evidence of absence", f"{path}.basis_check_ids")
            elif non_na_basis:
                issues.error("E-RATING-UNKNOWN-AS-LOW", f"rating {key} is 'not_applicable' but basis checks "
                             f"{non_na_basis} are not not_applicable (an unknown or skipped check is never 'does not apply')",
                             f"{path}.rating")
        # severity over the surface as a whole (not just the declared basis): checks on the surface with
        # status finding (their severity and their linked findings') and every finding on the surface
        worst: Optional[str] = None
        worst_src = ""

        def consider(sev: Any, src: str) -> None:
            nonlocal worst, worst_src
            if sev in SEV_RANK and (worst is None or SEV_RANK[sev] > SEV_RANK[worst]):
                worst, worst_src = sev, src

        finding_checks = []
        if key in SURFACES:
            for cid, c in ctx.checks.items():
                if c.get("surface") == key and c.get("status") == "finding":
                    finding_checks.append(cid)
                    consider(c.get("severity"), f"check {cid}")
                    for fid in _strs(c.get("finding_ids")):
                        fdoc = ctx.findings.get(fid)
                        if fdoc is not None:
                            consider(fdoc.get("severity"), f"finding {fid} (linked from {cid})")
            for fid, fdoc in ctx.findings.items():
                if fdoc.get("surface") == key:
                    consider(fdoc.get("severity"), f"finding {fid}")
            if worst == "critical" and rating != "critical":
                issues.error("E-RATING-CRITICAL-AVERAGED", f"rating {key} is {rating!r} but {worst_src} on this surface "
                             "is critical; a critical finding is never averaged away", f"{path}.rating")
            elif worst in ("high", "medium") and rating in RATING_RANK and RATING_RANK[rating] < SEV_RANK[worst]:
                issues.error("E-RATING-CRITICAL-AVERAGED", f"rating {key} is {rating!r} but {worst_src} on this surface "
                             f"is {worst}; the rating floor for this surface is {worst}", f"{path}.rating")
            missing_basis = [cid for cid in finding_checks if cid not in basis]
            if missing_basis:
                issues.warn("W-RATING-BASIS-INCOMPLETE", f"rating {key}: checks with status 'finding' on this surface "
                            f"{missing_basis} are missing from basis_check_ids", f"{path}.basis_check_ids")
        tb = r.get("time_basis_pin_id")
        if tb is not None and tb not in ctx.pins:
            issues.error("E-EVIDENCE-DANGLING", f"rating {key}: time_basis_pin_id {tb!r} does not exist", f"{path}.time_basis_pin_id")


# --------------------------------------------------------------------------------------
# Declarations, coverage, verdict
# --------------------------------------------------------------------------------------
def check_declarations(ctx: Ctx, issues: Issues) -> None:
    decl = _d(ctx.m.get("declarations"))
    for key in ("no_real_signing", "no_broadcast", "no_private_keys_requested"):
        if decl.get(key) is not True:
            issues.error("E-DECL-SIGNING", f"declarations.{key} must be exactly true", f"$.declarations.{key}")
    sim = _d(decl.get("simulation"))
    used = sim.get("used")
    sim_evidence = [(i, _d(e)) for i, e in enumerate(_l(ctx.m.get("evidence")))
                    if _d(e).get("evidence_type") == "simulation_counterfactual"]
    if used is True:
        for key in ("fork_verified_disposable", "synthetic_accounts_only", "results_labeled_counterfactual"):
            if sim.get(key) is not True:
                issues.error("E-DECL-FORK", f"simulation.used is true but simulation.{key} is not true",
                             f"$.declarations.simulation.{key}")
        for key in ("fork_chain_id", "fork_block"):
            if not _is_int(sim.get(key)):
                issues.error("E-DECL-FORK", f"simulation.used is true but simulation.{key} is missing",
                             f"$.declarations.simulation.{key}")
        fc = sim.get("fork_chain_id")
        if _is_int(fc) and _is_int(ctx.target_chain) and fc != ctx.target_chain:
            issues.error("E-DECL-FORK", f"simulation.fork_chain_id {fc} != target chain {ctx.target_chain}; a fork of "
                         "another chain cannot produce counterfactuals for this target", "$.declarations.simulation.fork_chain_id")
        ap = sim.get("fork_attestation_path")
        if isinstance(ap, str) and ap:
            if os.path.isabs(ap) or not _inside_dir(ctx.dir, ap):
                issues.error("E-REPORT-MISSING", f"fork_attestation_path {ap!r}: path escapes manifest directory",
                             "$.declarations.simulation.fork_attestation_path")
            elif not os.path.exists(os.path.join(ctx.dir, ap)):
                issues.warn("W-DECL-FORK-ATTESTATION", f"fork_attestation_path {ap!r} not found relative to the manifest",
                            "$.declarations.simulation.fork_attestation_path")
        else:
            issues.warn("W-DECL-FORK-ATTESTATION", "simulation.used is true but fork_attestation_path is null; record "
                        "the fork_guard.py attestation file", "$.declarations.simulation.fork_attestation_path")
    else:
        for i, e in sim_evidence:
            issues.error("E-DECL-FORK", f"evidence {e.get('evidence_id')} is simulation_counterfactual but "
                         "declarations.simulation.used is not true", f"$.evidence[{i}].evidence_type")
    for i, e in sim_evidence:
        if e.get("counterfactual") is not True:
            issues.error("E-DECL-FORK", f"evidence {e.get('evidence_id')} is simulation_counterfactual but "
                         "counterfactual is not true (results must be labeled counterfactual)", f"$.evidence[{i}].counterfactual")


def check_coverage(ctx: Ctx, issues: Issues) -> None:
    referenced: set = set()
    for c in ctx.checks.values():
        if isinstance(c.get("limitation_id"), str):
            referenced.add(c["limitation_id"])
    for sc in _l(ctx.m.get("scope_addresses")):
        if isinstance(_d(sc).get("limitation_id"), str):
            referenced.add(_d(sc)["limitation_id"])
    for i, lim in enumerate(_l(_d(ctx.m.get("coverage")).get("limitations"))):
        lim = _d(lim)
        lid = lim.get("limitation_id")
        affected = _l(lim.get("affected_check_ids"))
        for cid in affected:
            if cid not in ctx.checks:
                issues.error("E-EVIDENCE-DANGLING", f"limitation {lid}: affected_check_id {cid!r} does not exist",
                             f"$.coverage.limitations[{i}].affected_check_ids")
        if lid not in referenced and not affected:
            issues.warn("W-LIMITATION-UNREFERENCED", f"limitation {lid} is not referenced by any check, scope entry or "
                        "affected_check_ids", f"$.coverage.limitations[{i}]")


def check_verdict(ctx: Ctx, issues: Issues) -> None:
    v = _d(ctx.m.get("verdict"))
    for key in ("question", "answer"):
        if not _s(v.get(key)).strip():
            issues.error("E-SCHEMA", f"verdict.{key} must be a non-empty string", f"$.verdict.{key}")
    texts = [("$.verdict.answer", v.get("answer"))]
    texts += [(f"$.verdict.main_reasons[{i}]", x) for i, x in enumerate(_l(v.get("main_reasons")))]
    for path, text in texts:
        if isinstance(text, str):
            hit = _UNSAFE_RE.search(text)
            if hit:
                issues.warn("W-VERDICT-LANGUAGE", f"unconditional safety phrasing in the manifest verdict: {hit.group(0)!r}", path)


# --------------------------------------------------------------------------------------
# Entry points
# --------------------------------------------------------------------------------------
_SEMANTIC_CHECKS = [
    ("identity", check_identity), ("pins", check_pins), ("chains", check_chains), ("metadata", check_metadata),
    ("scope", check_scope), ("target", check_target), ("report", check_report), ("checks", check_checks),
    ("evidence", check_evidence), ("findings", check_findings), ("ratings", check_ratings),
    ("declarations", check_declarations), ("coverage", check_coverage), ("verdict", check_verdict),
]


def _result(issues: Issues, extra: dict) -> dict:
    out = {
        "result": "PASS" if not issues.errors else "FAIL",
        "contract_version": CONTRACT_VERSION,
        "errors": issues.errors,
        "warnings": issues.warnings,
        "note": CLOSING_NOTE,
        "counts": {"errors": len(issues.errors), "warnings": len(issues.warnings)},
    }
    out.update(extra)
    return out


def validate_objects(manifest: Any, report_text: Optional[str], manifest_dir: str = ".", strict: bool = False,
                     report_path: Optional[str] = None, report_bytes: Optional[bytes] = None,
                     report_error: Optional[str] = None) -> dict:
    """Validate an in-memory manifest (dict) and report text (None = report file missing).

    report_bytes, when given, are the raw file bytes the report hash is computed over (otherwise the
    UTF-8 encoding of report_text is hashed). report_error, when given, is the E-REPORT-MISSING message
    to use when report_text is None (e.g. a rejected report.path)."""
    issues = Issues(strict=strict)
    info = check_structure(manifest, issues)
    if isinstance(manifest, dict):
        ctx = Ctx(manifest, manifest_dir, report_text, report_path, report_bytes, report_error)
        for name, fn in _SEMANTIC_CHECKS:
            try:
                fn(ctx, issues)
            except Exception as e:  # malformed input must never crash the validator
                issues.error("E-SCHEMA", f"{name} check aborted on malformed input: {type(e).__name__}: {e}", f"$.<{name}>")
    return _result(issues, {"schema_lib": info["schema_lib"], "schema_file": info["schema_file"], "strict": strict})


def validate(manifest_path: str, report_path: Optional[str] = None, strict: bool = False) -> dict:
    """Validate manifest file + report file.

    report_path defaults to manifest.report.path, which must be relative and resolve inside the manifest's
    directory (E-REPORT-MISSING 'path escapes manifest directory' otherwise). An explicit report_path is the
    operator's choice and may live anywhere."""
    manifest_path = os.path.abspath(manifest_path)
    manifest_dir = os.path.dirname(manifest_path)
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except FileNotFoundError:
        issues = Issues(strict)
        issues.error("E-SCHEMA", f"manifest file not found: {manifest_path}", "$")
        return _result(issues, {"schema_lib": False, "schema_file": False, "strict": strict, "manifest": manifest_path})
    except Exception as e:
        issues = Issues(strict)
        issues.error("E-SCHEMA", f"manifest is not valid JSON: {e}", "$")
        return _result(issues, {"schema_lib": False, "schema_file": False, "strict": strict, "manifest": manifest_path})
    report_error: Optional[str] = None
    if report_path is None:
        rp = _d(_d(manifest).get("report")).get("path")
        if isinstance(rp, str) and rp:
            if os.path.isabs(rp) or not _inside_dir(manifest_dir, rp):
                report_error = (f"report.path {rp!r}: path escapes manifest directory (it must be relative and resolve "
                                "inside the manifest's directory; pass --report explicitly to validate a file elsewhere)")
            else:
                report_path = os.path.normpath(os.path.join(manifest_dir, rp))
    else:
        report_path = os.path.abspath(report_path)
    report_bytes: Optional[bytes] = None
    report_text: Optional[str] = None
    if report_path and os.path.isfile(report_path):
        with open(report_path, "rb") as f:
            report_bytes = f.read()
        report_text = report_bytes.decode("utf-8", errors="replace")
    res = validate_objects(manifest, report_text, manifest_dir, strict, report_path,
                           report_bytes=report_bytes, report_error=report_error)
    res["manifest"] = manifest_path
    res["report"] = report_path
    return res


def format_human(res: dict) -> str:
    lines = []
    for e in res["errors"]:
        lines.append(f"ERROR {e['code']}: {e['message']} [{e['path']}]")
    for w in res["warnings"]:
        lines.append(f"WARN {w['code']}: {w['message']} [{w['path']}]")
    if res["result"] == "PASS":
        lines.append("RESULT: PASS" + (f" ({len(res['warnings'])} warnings)" if res["warnings"] else ""))
    else:
        lines.append(f"RESULT: FAIL ({len(res['errors'])} errors, {len(res['warnings'])} warnings)")
    lines.append(CLOSING_NOTE)
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Target-integrity validator for evm-token-due-diligence manifests/reports "
                                 f"(contract v{CONTRACT_VERSION}).", epilog=CLOSING_NOTE)
    ap.add_argument("--manifest", required=True, help="path to manifest.json")
    ap.add_argument("--report", help="path to the report (default: manifest.report.path, which must resolve inside "
                    "the manifest's directory)")
    ap.add_argument("--json", action="store_true", help="print a JSON result instead of human-readable lines")
    ap.add_argument("--strict", action="store_true",
                    help="promote " + ", ".join(sorted(STRICT_PROMOTED)) + " to errors")
    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        return 2 if e.code not in (0,) else 0
    if not os.path.isfile(args.manifest):
        print(f"usage error: manifest file not found: {args.manifest}", file=sys.stderr)
        return 2
    try:
        res = validate(args.manifest, args.report, strict=args.strict)
    except Exception as e:
        print(f"usage error: {e}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(format_human(res))
    return 0 if res["result"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
