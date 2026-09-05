#!/usr/bin/env python3
"""rpc_probe.py — build the frozen target packet for ONE exact (chain, address) from raw JSON-RPC.

Read-only: only ddcore.READ_ONLY_METHODS are ever sent (ddcore enforces; this tool does not
bypass it). It never signs, never broadcasts, and refuses to run if any argument looks like key
material. The RPC URL is redacted in every output field.

Flow (each step's failure becomes a coverage limitation, never a token finding):
  1. eth_chainId (always live, never from cache) -> observed chain; compared with --chain-id.
     identity.status: MATCH | OBSERVED_ONLY (no --chain-id given; confirm with the user before
     relying on the packet) | CHAIN_MISMATCH | UNVERIFIED. A mismatch aborts the reads: a
     same-symbol token on another chain is not the target. It is recorded under `identity`
     (status, requested, observed, detail), NOT as a limitation: it is an identity failure, not
     a coverage problem. The client and its response cache are bound to the observed chain here
     (ddcore.RpcClient.set_chain_id); a --cache file recorded for another chain is ignored for
     the run (packet `cache.status` = "ignored", warning on stderr).
  2. eth_getBlockByNumber(--block) -> pin P1 in manifest pin format with the FULL captured header.
     Every later read uses the pinned block number hex, never "latest".
  3. eth_getCode -> code hash (keccak256), size, runtime_status contract|eoa|unknown, EIP-1167
     minimal-proxy detection. A non-string or non-hex result is a limitation (runtime_status
     "unknown"), never "not a contract".
  4. eth_getStorageAt for the EIP-1967 implementation/admin/beacon slots and the EIP-1822 logic
     slot -> proxy status; implementation code hash. Any failed or malformed slot read leaves
     proxy.status "unknown" with a limitation, never "not_proxy".
  5. eth_call name()/symbol()/decimals()/totalSupply() -> metadata with resolved/nonstandard/
     unresolved statuses and raw return data preserved.
  6. eth_call owner()/getOwner()/paused() plus each --call -> probes with decoded candidates.
     A revert is a FACT about the contract (status "reverted"), not a limitation; reverts at the
     pin are cached like results.
  For an `eoa` target (no code at the pin) steps 5-6 still run, but every metadata entry is
  `unresolved` and every probe carries the qualifier "no runtime code at this address at the
  pin; return data cannot come from this address" so a lying or misrouted endpoint is visible.

Usage:
  python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x.. [--chain-id N]
        [--block latest|safe|finalized|N] [--call "owner()"]... [--cache FILE] [--out packet.json]
        [--timeout S] [--retries N] [--json]

  --rpc falls back to the EVM_DD_RPC_URL environment variable.
  --block: a floating tag (latest|safe|finalized) is resolved to a number by the pin and never
  cached; N is decimal or 0x hex. Prefer `finalized` where the chain supports it.
  --call accepts "sig()" (no arguments), "sig(type,..):arg,.." with static argument types only
  (address, bool, bytes32, uintN, intN; integer arguments must fit the declared width), or raw
  calldata hex.

Exit codes: 0 packet written (coverage complete or partial), 1 fatal coverage failure (chain id
or pin could not be obtained; the packet is still written with limitations), 2 usage error,
3 chain mismatch (packet written with identity.status = CHAIN_MISMATCH; do not proceed).

Chain ids, hosts and any other chain-specific numbers are never assumed here: the observed chain
id is whatever the endpoint reports and must be verified at use time against the requested chain.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ddcore  # noqa: E402
from ddcore import (  # noqa: E402
    EIP1822_LOGIC_SLOT,
    EIP1967_ADMIN_SLOT,
    EIP1967_BEACON_SLOT,
    EIP1967_IMPLEMENTATION_SLOT,
    ResponseCache,
    RpcClient,
    RpcCoverageError,
    RpcPolicyError,
    decode_abi_string,
    decode_abi_string_detail,
    decode_uint,
    encode_static,
    hex_to_int,
    int_to_hex,
    iso_utc,
    keccak256_hex,
    normalize_address,
    selector,
    to_checksum_address,
    validate_address,
)

PACKET_VERSION = "1.0"
TOOL = {"name": "rpc_probe.py", "version": "1.0", "library": "ddcore"}
PIN_ID = "P1"
ENV_RPC = "EVM_DD_RPC_URL"
DEFAULT_OUT = "target-packet.json"

EXIT_OK = 0
EXIT_FATAL = 1
EXIT_USAGE = 2
EXIT_CHAIN_MISMATCH = 3

OWNER_SELECTOR = "0x8da5cb5b"  # owner()
BEACON_IMPLEMENTATION_SIG = "implementation()"

# (label, affected core check ids when the read fails)
STANDARD_PROBES: tuple[tuple[str, list[str]], ...] = (
    ("owner()", ["A-ADMIN"]),
    ("getOwner()", ["A-ADMIN"]),
    ("paused()", ["A-RESTRICT"]),
)
METADATA_FIELDS: tuple[tuple[str, str, str], ...] = (
    # packet key, signature, kind
    ("name", "name()", "string"),
    ("symbol", "symbol()", "string"),
    ("decimals", "decimals()", "uint8"),
    ("total_supply", "totalSupply()", "uint256"),
)
PROXY_SLOTS: tuple[tuple[str, str], ...] = (
    ("eip1967_implementation", EIP1967_IMPLEMENTATION_SLOT),
    ("eip1967_admin", EIP1967_ADMIN_SLOT),
    ("eip1967_beacon", EIP1967_BEACON_SLOT),
    ("eip1822_logic", EIP1822_LOGIC_SLOT),
)

CORE_CHECK_IDS = [
    "A-MINT", "A-UPGRADE", "A-SEIZE", "A-RESTRICT", "A-TAX", "A-EXTCALL", "A-ADMIN",
    "B-CANON", "B-PRINCIPAL", "B-SIDE", "C-HIST-SELL", "C-QUOTE", "D-SUPPLY", "D-CONC",
    "E-LAUNCH", "F-FEES", "F-TREASURY", "G-REWARDS", "G-RIGHTS", "H-UTILITY", "H-DEPS", "H-DEV",
]
TOKEN_CONTROL_CHECKS = ["A-MINT", "A-UPGRADE", "A-SEIZE", "A-RESTRICT", "A-TAX", "A-EXTCALL", "A-ADMIN"]
LIMITATION_KINDS = {
    "rpc_timeout", "rpc_rate_limit", "rpc_pruned", "rpc_error", "dns_failure", "api_unavailable",
    "explorer_unavailable", "source_unavailable", "out_of_scope", "other",
}
_NOT_RETRIED_KINDS = ddcore.NON_RETRIED_KINDS
IDENTITY_STATUSES = ("MATCH", "OBSERVED_ONLY", "CHAIN_MISMATCH", "UNVERIFIED")
EOA_QUALIFIER = "no runtime code at this address at the pin; return data cannot come from this address"

# EIP-1167 minimal proxy runtime: 363d3d373d3d3d363d73 <20-byte impl> 5af43d82803e903d91602b57fd5bf3
MINIMAL_PROXY_PREFIX = bytes.fromhex("363d3d373d3d3d363d73")
MINIMAL_PROXY_SUFFIX = bytes.fromhex("5af43d82803e903d91602b57fd5bf3")

_BLOCK_TAGS_ACCEPTED = ("latest", "safe", "finalized")
_PK_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_LOWER_WORD_RE = re.compile(r"^[a-z]+$")
_FORBIDDEN_FLAGS = ("--private-key", "--privatekey", "--mnemonic", "--seed", "--keystore", "--secret")
_RAW_CALLDATA_RE = re.compile(r"^0x[0-9a-fA-F]{8}(?:[0-9a-fA-F]{64})*$")
_SIG_RE = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\(([^()]*)\)$")
_INT_TYPE_RE = re.compile(r"^(u?int)([0-9]+)?$")
_REVERT_MARKERS = ddcore.EXECUTION_FAILURE_MARKERS


# ------------------------------------------------------------------------------------
# argv hygiene: this tool never accepts or reads key material
# ------------------------------------------------------------------------------------
def find_key_material(argv: list[str]) -> Optional[str]:
    """Return a reason string if any argument looks like a private key, a mnemonic, or a
    key-carrying flag; None otherwise. Checked before parsing and before any network use."""
    run = 0
    for arg in argv:
        low = arg.lower()
        for flag in _FORBIDDEN_FLAGS:
            if low == flag or low.startswith(flag + "=") or low.startswith(flag + "-"):
                return f"flag {arg.split('=')[0]} is never accepted (this tool does not handle keys)"
        if _PK_RE.match(arg.strip()):
            return "an argument looks like a 32-byte private key (0x + 64 hex)"
        words = arg.split()
        if len(words) >= 12 and all(_LOWER_WORD_RE.match(w) for w in words):
            return "an argument looks like a mnemonic phrase (12+ lowercase words)"
        if _LOWER_WORD_RE.match(arg):
            run += 1
            if run >= 12:
                return "the arguments look like a mnemonic phrase (12+ consecutive lowercase words)"
        else:
            run = 0
    return None


# ------------------------------------------------------------------------------------
# small helpers
# ------------------------------------------------------------------------------------
def hex_to_bytes(h: Any) -> bytes:
    if not isinstance(h, str):
        raise ValueError("expected a hex string")
    s = h[2:] if h[:2] in ("0x", "0X") else h
    if len(s) % 2:
        s = "0" + s
    return bytes.fromhex(s) if s else b""


def word_to_address(value: Optional[str]) -> Optional[str]:
    """Low 20 bytes of a 32-byte storage word as a checksummed address; None when the word is zero."""
    if not value:
        return None
    try:
        w = hex_to_bytes(value).rjust(32, b"\x00")[-32:]
    except ValueError:
        return None
    if not any(w):
        return None
    return to_checksum_address("0x" + w[12:].hex())


def detect_minimal_proxy(code: bytes) -> Optional[str]:
    """EIP-1167 canonical runtime (45 bytes) -> checksummed implementation, else None.
    Non-canonical push-optimised variants are not matched; inspect selectors/bytecode for those."""
    if len(code) == 45 and code.startswith(MINIMAL_PROXY_PREFIX) and code.endswith(MINIMAL_PROXY_SUFFIX):
        return to_checksum_address("0x" + code[10:30].hex())
    return None


def classify_call_failure(err: RpcCoverageError) -> Optional[str]:
    """'reverted' when the node reports an EVM execution failure for eth_call (a fact about the
    contract at the pin); None when the failure is an infrastructure/coverage problem."""
    if err.kind != "rpc_error":
        return None
    if getattr(err, "execution_failure", False):
        return "reverted"
    low = (err.message or "").lower()
    if low.startswith("rpc error 3:"):
        return "reverted"
    if low.startswith("rpc error") and any(m in low for m in _REVERT_MARKERS):
        return "reverted"
    return None


def normalize_block_arg(text: str) -> str:
    t = text.strip().lower()
    if t in _BLOCK_TAGS_ACCEPTED:
        return t
    if re.match(r"^0x[0-9a-f]+$", t):
        return int_to_hex(int(t, 16))
    if re.match(r"^[0-9]+$", t):
        return int_to_hex(int(t))
    raise ValueError(f"--block must be one of {'/'.join(_BLOCK_TAGS_ACCEPTED)} or a block number, got {text!r}")


def _abi_kind(type_name: str) -> tuple[str, int]:
    """(ddcore encode kind, bit width). Widths are validated so calldata never carries an impossible value."""
    t = type_name.strip()
    if t == "address":
        return "address", 160
    if t == "bool":
        return "bool", 8
    if t == "bytes32":
        return "bytes32", 256
    m = _INT_TYPE_RE.match(t)
    if m:
        bits = int(m.group(2)) if m.group(2) else 256
        if bits % 8 or not 8 <= bits <= 256:
            raise ValueError(f"unsupported integer width in --call type {t!r}: N must be a multiple of 8 in 8..256")
        return ("uint" if m.group(1) == "uint" else "int"), bits
    raise ValueError(f"unsupported argument type {t!r} in --call (static types only: address, bool, bytes32, uintN, intN)")


def _coerce_arg(kind: str, bits: int, text: str) -> Any:
    t = text.strip()
    if kind == "address":
        ok, why = validate_address(t)
        if not ok:
            raise ValueError(f"--call address argument {t!r}: {why}")
        return t
    if kind == "bool":
        if t.lower() in ("true", "1"):
            return True
        if t.lower() in ("false", "0"):
            return False
        raise ValueError(f"--call bool argument must be true/false, got {t!r}")
    if kind == "bytes32":
        b = hex_to_bytes(t)
        if len(b) != 32:
            raise ValueError("--call bytes32 argument must be 32 bytes of hex")
        return b
    try:
        v = int(t, 16) if t.lower().startswith(("0x", "-0x")) else int(t, 10)
    except ValueError:
        raise ValueError(f"--call {kind}{bits} argument {t!r} is not an integer (decimal or 0x hex)")
    if kind == "uint":
        if not 0 <= v < (1 << bits):
            raise ValueError(f"--call uint{bits} argument {v} is out of range [0, 2**{bits} - 1]")
    else:
        if not -(1 << (bits - 1)) <= v < (1 << (bits - 1)):
            raise ValueError(f"--call int{bits} argument {v} is out of range [-2**{bits - 1}, 2**{bits - 1} - 1]")
    return v


def parse_call_spec(spec: str) -> tuple[str, str]:
    """'sig()' | 'sig(types):args' | raw calldata -> (label, calldata hex)."""
    s = spec.strip()
    if _RAW_CALLDATA_RE.match(s):
        return s, s.lower()
    sig, _, argtext = s.partition(":")
    sig = sig.replace(" ", "")
    m = _SIG_RE.match(sig)
    if not m:
        raise ValueError(f"--call {spec!r} is not a function signature like owner() or balanceOf(address):0x..")
    types = [t for t in m.group(2).split(",") if t.strip()]
    args = [a for a in argtext.split(",")] if argtext.strip() else []
    if len(types) != len(args):
        raise ValueError(f"--call {sig} expects {len(types)} argument(s), got {len(args)}; give them after a colon, e.g. balanceOf(address):0x...")
    items = []
    for t, a in zip(types, args):
        kind, bits = _abi_kind(t)
        items.append((kind, _coerce_arg(kind, bits, a)))
    data = selector(sig) + encode_static(items).hex()
    label = sig if not args else f"{sig}:{','.join(a.strip() for a in args)}"
    return label, data


def decode_candidates(raw: str) -> dict[str, Any]:
    """Interpret return data under several static readings; the caller decides which applies."""
    cands: dict[str, Any] = {"uint": None, "address": None, "bool": None, "string": None}
    try:
        b = hex_to_bytes(raw)
    except ValueError:
        return cands
    if len(b) >= 32:
        word = b[:32]
        u = int.from_bytes(word, "big")
        cands["uint"] = str(u)
        if not any(word[:12]):
            cands["address"] = to_checksum_address("0x" + word[12:].hex())
        if u in (0, 1):
            cands["bool"] = bool(u)
    text, status = decode_abi_string(b)
    if status in ("resolved", "nonstandard"):
        cands["string"] = text
    return cands


def live_call(client: RpcClient, method: str, params: Optional[list] = None) -> Any:
    """Call bypassing the response cache (identity facts such as chain id must be fresh)."""
    saved = client.cache
    client.cache = None
    try:
        return client.call(method, params or [])
    finally:
        client.cache = saved


def _safe(text: Any, limit: int = 64) -> str:
    """Render untrusted strings (token metadata) without control characters."""
    if text is None:
        return "null"
    s = "".join(ch if ch.isprintable() else "\\x%02x" % ord(ch) for ch in str(text))
    return s if len(s) <= limit else s[:limit] + "..."


# ------------------------------------------------------------------------------------
# the probe
# ------------------------------------------------------------------------------------
class Probe:
    def __init__(self, client: RpcClient, address: str, requested_chain_id: Optional[int],
                 block: str, extra_calls: list[tuple[str, str]], now: Callable[[], int]):
        self.client = client
        self.address = address
        self.address_checksum = to_checksum_address(address)
        self.requested_chain_id = requested_chain_id
        self.block = block
        self.extra_calls = extra_calls
        self.now = now
        self.limitations: list[dict] = []
        self.failed_calls: list[dict] = []
        self.pinned_hex: Optional[str] = None
        self.no_code = False          # True once eth_getCode proved the target has no runtime code at the pin
        self.cache_warning: Optional[str] = None
        self.packet = self._skeleton()

    # ----- packet skeleton ----------------------------------------------------------
    def _skeleton(self) -> dict:
        return {
            "packet_version": PACKET_VERSION,
            "tool": dict(TOOL),
            "generated_at_utc": iso_utc(self.now()),
            "requested": {
                "chain_id": self.requested_chain_id,
                "address": self.address,
                "address_checksum": self.address_checksum,
            },
            "observed": {
                "chain_id": None,
                "rpc_chain_id_hex": None,
                "rpc_endpoint_redacted": self.client.redacted_url,
                "client_version": None,
            },
            "identity": self._identity("UNVERIFIED", None, "chain id not yet observed"),
            "cache": {"status": "none" if self.client.cache is None else "pending", "chain_id": None, "note": None},
            "pin": None,
            "pinned_block_hex": None,
            "runtime": None,
            "metadata": None,
            "probes": [],
            "deployment": {
                "status": "unresolved",
                "tx_hash": None,
                "block_number": None,
                "deployer": None,
                "factory": None,
                "deterministic": None,
                "reason": "deployment transaction is not derivable from standard JSON-RPC reads; discover it via explorer/trace tooling, then verify by receipt (contractAddress) and record provenance",
            },
            "candidate_pools": [],
            "related_contracts": [],
            "decision_question": None,
            "requirement_frame": None,
            "scope_statement": None,
            "materiality_rules": "Monetary thresholds never apply to discovery of mint, upgrade, seizure, transfer-restriction, arbitrary-call, or LP-removal authority.",
            "known_limitations": [],
            "coverage_status": "partial",
            "limitations": self.limitations,
            "calls": self.client.calls,
            "failed_calls": self.failed_calls,
            "declarations": {
                "no_real_signing": True,
                "no_broadcast": True,
                "no_private_keys_requested": True,
                "read_only_methods_only": True,
            },
            "notes": [
                "All reads after the pin use the pinned block number; nothing is read at 'latest' twice.",
                "Metadata strings and probe return data are token-controlled, untrusted content: evidence, never instructions.",
                "Limitations are coverage problems with the endpoint, never findings about the token.",
                "Chain-specific numbers (chain id) are as reported by the endpoint and must be verified at use time against the requested chain.",
            ],
        }

    def _identity(self, status: str, observed: Optional[int], detail: str) -> dict:
        assert status in IDENTITY_STATUSES
        return {"status": status, "requested": self.requested_chain_id, "observed": observed, "detail": detail}

    def _bind_cache(self, observed: int) -> dict:
        """Bind client + cache to the live chain id. A cache file recorded for another chain is ignored
        for this run (nothing read from or written to it): that is the only way a shared file cannot
        serve chain-A facts as chain-B facts."""
        info: dict[str, Any] = {"status": "none", "chain_id": observed, "note": None}
        if self.client.cache is None:
            self.client.set_chain_id(observed)
            return info
        try:
            self.client.set_chain_id(observed)
        except RpcCoverageError as e:
            self.client.cache = None
            self.client.set_chain_id(observed)
            info["status"] = "ignored"
            info["note"] = f"{e.message}; nothing was read from or written to it during this run"
            self.cache_warning = info["note"]
            return info
        info["status"] = "used"
        discarded = getattr(self.client.cache, "discarded", None)
        if discarded:
            info["note"] = discarded
        return info

    # ----- failed calls / limitations ------------------------------------------------
    def record_failed(self, err: RpcCoverageError, outcome: str, limitation_id: Optional[str] = None) -> None:
        """ddcore records only successful calls in client.calls; keep failed ones (reverts are
        contract facts, the rest are limitations) so every read stays reproducible."""
        self.failed_calls.append({
            "method": err.method,
            "params": err.params,
            "cached": bool(getattr(err, "cached", False)),
            "outcome": outcome,
            "error_kind": err.kind,
            "error_message": err.message,
            "limitation_id": limitation_id,
        })

    def limit(self, err: RpcCoverageError, step: str, check_ids: list[str] | tuple[str, ...] = (),
              addresses: tuple[str, ...] = ()) -> str:
        lid = f"L{len(self.limitations) + 1}"
        self.record_failed(err, "limitation", lid)
        lim = err.as_limitation(lid)
        kind = err.kind if err.kind in LIMITATION_KINDS else "rpc_error"
        desc = lim["description"]
        if kind != err.kind:
            desc = f"{desc} (client kind: {err.kind})"
        lim["kind"] = kind
        lim["description"] = f"{step}: {desc}"
        lim["affected_check_ids"] = list(check_ids)
        lim["affected_addresses"] = [to_checksum_address(a) for a in addresses]
        lim["retry_attempts"] = 0 if err.kind in _NOT_RETRIED_KINDS else int(self.client.retries)
        self.limitations.append(lim)
        return lid

    def _finish(self, code: int, coverage: Optional[str] = None) -> tuple[dict, int]:
        self.packet["coverage_status"] = coverage or ("complete" if not self.limitations else "partial")
        return self.packet, code

    # ----- steps --------------------------------------------------------------------
    def run(self) -> tuple[dict, int]:
        p = self.packet
        # (1) chain id, always live
        try:
            chain_hex = live_call(self.client, "eth_chainId", [])
            observed = hex_to_int(chain_hex)
            if observed < 1:
                raise ValueError(f"chain id {observed} is not a positive integer")
        except RpcCoverageError as e:
            self.limit(e, "step 1 (eth_chainId)", CORE_CHECK_IDS, (self.address,))
            p["identity"] = self._identity("UNVERIFIED", None, "chain id could not be read from the endpoint; nothing else was attempted")
            return self._finish(EXIT_FATAL)
        except (ValueError, TypeError) as e:
            err = RpcCoverageError("rpc_error", f"eth_chainId returned a non-quantity: {e}", "eth_chainId", [])
            self.limit(err, "step 1 (eth_chainId)", CORE_CHECK_IDS, (self.address,))
            p["identity"] = self._identity("UNVERIFIED", None, "chain id response was malformed; nothing else was attempted")
            return self._finish(EXIT_FATAL)
        p["observed"]["chain_id"] = observed
        p["observed"]["rpc_chain_id_hex"] = chain_hex if isinstance(chain_hex, str) else int_to_hex(observed)
        # bind the client and its cache to the LIVE chain id before any cacheable read
        p["cache"] = self._bind_cache(observed)

        try:  # optional, non-material
            cv = live_call(self.client, "web3_clientVersion", [])
            p["observed"]["client_version"] = cv if isinstance(cv, str) else None
        except RpcCoverageError as e:
            p["observed"]["client_version"] = None
            p["observed"]["client_version_note"] = f"web3_clientVersion unavailable ({e.kind}); non-material, not recorded as a limitation"

        if self.requested_chain_id is None:
            p["identity"] = self._identity(
                "OBSERVED_ONLY", observed,
                f"no --chain-id supplied; observed chain {observed} adopted as the target chain. Confirm it is the chain the user meant before relying on this packet.")
        elif self.requested_chain_id == observed:
            p["identity"] = self._identity("MATCH", observed, f"requested chain {self.requested_chain_id} equals RPC-observed chain {observed}")
        else:
            # identity failure, not a coverage limitation: nothing is added to limitations
            p["identity"] = self._identity(
                "CHAIN_MISMATCH", observed,
                f"requested chain {self.requested_chain_id} but RPC reports {observed}: do not proceed; "
                f"a same-symbol token on another chain is not the target. No reads were made at the address; "
                f"this is an identity failure, not a coverage limitation.")
            return self._finish(EXIT_CHAIN_MISMATCH, coverage="partial")

        # (2) pin
        try:
            header = self.client.block(self.block)
        except RpcCoverageError as e:
            self.limit(e, f"step 2 (eth_getBlockByNumber {self.block})", CORE_CHECK_IDS, (self.address,))
            return self._finish(EXIT_FATAL)
        missing = [k for k in ("number", "hash", "parentHash", "timestamp") if not isinstance(header.get(k), str)]
        if missing:
            err = RpcCoverageError("rpc_error", f"block header lacks {missing}", "eth_getBlockByNumber", [self.block, False])
            self.limit(err, "step 2 (pin)", CORE_CHECK_IDS, (self.address,))
            return self._finish(EXIT_FATAL)
        try:
            block_number = hex_to_int(header["number"])
            ts = hex_to_int(header["timestamp"])
        except ValueError as e:
            err = RpcCoverageError("rpc_error", f"block header quantities malformed: {e}", "eth_getBlockByNumber", [self.block, False])
            self.limit(err, "step 2 (pin)", CORE_CHECK_IDS, (self.address,))
            return self._finish(EXIT_FATAL)
        captured_at = self.now()
        try:
            ts_utc = iso_utc(ts)
            captured_utc = iso_utc(captured_at)
        except (ValueError, OverflowError, OSError) as e:  # ddcore.TimestampRangeError is both
            err = RpcCoverageError("rpc_error", f"block header timestamp out of range: {e}", "eth_getBlockByNumber", [self.block, False])
            self.limit(err, "step 2 (pin)", CORE_CHECK_IDS, (self.address,))
            return self._finish(EXIT_FATAL)
        if self.block not in _BLOCK_TAGS_ACCEPTED and int_to_hex(block_number) != self.block:
            err = RpcCoverageError("rpc_error", f"requested block {self.block} but header number is {int_to_hex(block_number)}", "eth_getBlockByNumber", [self.block, False])
            self.limit(err, "step 2 (pin)", CORE_CHECK_IDS, (self.address,))
        self.pinned_hex = int_to_hex(block_number)
        p["pin"] = {
            "pin_id": PIN_ID,
            "chain_id": observed,
            "block_number": block_number,
            "block_hash": header["hash"],
            "timestamp_unix": ts,
            "timestamp_utc": ts_utc,
            "captured_at_utc": captured_utc,
            "rpc_method": "eth_getBlockByNumber",
            "rpc_endpoint_redacted": self.client.redacted_url,
            "captured_header": header,
            "purpose": "current_state",
        }
        p["pinned_block_hex"] = self.pinned_hex
        placeholder, why = ddcore.looks_like_placeholder_hash(header["hash"])
        if placeholder:
            p["identity"]["pin_warning"] = f"block hash looks like a placeholder ({why}); the validator will reject this pin"

        # (3) code
        self._step_code()
        # (5) metadata
        self._step_metadata()
        # (6) probes
        self._step_probes()
        return self._finish(EXIT_OK)

    def _step_code(self) -> None:
        p = self.packet
        assert self.pinned_hex is not None
        runtime: dict[str, Any] = {
            "code_hash": None,
            "code_size": None,
            "is_contract": None,
            "runtime_status": "unknown",  # manifest vocabulary: contract | eoa | unknown
            "pin_id": PIN_ID,
            "proxy": {
                "status": "unknown",
                "implementation": None,
                "implementation_code_hash": None,
                "admin": None,
                "beacon": None,
                "upgrade_authority": None,
                "slots_read": {},
                "basis": None,
            },
        }
        p["runtime"] = runtime
        code_params = [normalize_address(self.address), self.pinned_hex]
        try:
            code_hex = self.client.code(self.address, self.pinned_hex)
        except RpcCoverageError as e:
            lid = self.limit(e, "step 3 (eth_getCode)", TOKEN_CONTROL_CHECKS, (self.address,))
            runtime["proxy"]["basis"] = f"runtime code unavailable (limitation {lid}); runtime_status and proxy status unknown"
            return
        if not isinstance(code_hex, str):
            # a null or non-string result is a malformed response, never "no code"
            err = RpcCoverageError("rpc_error", f"eth_getCode returned a non-string result ({type(code_hex).__name__})", "eth_getCode", code_params)
            lid = self.limit(err, "step 3 (eth_getCode)", TOKEN_CONTROL_CHECKS, (self.address,))
            runtime["proxy"]["basis"] = f"runtime code unavailable (limitation {lid}: non-string result); runtime_status and proxy status unknown"
            return
        try:
            code = hex_to_bytes(code_hex)
        except ValueError as e:
            err = RpcCoverageError("rpc_error", f"eth_getCode returned non-hex data: {e}", "eth_getCode", code_params)
            lid = self.limit(err, "step 3 (eth_getCode)", TOKEN_CONTROL_CHECKS, (self.address,))
            runtime["proxy"]["basis"] = f"runtime code unavailable (limitation {lid}); runtime_status and proxy status unknown"
            return
        runtime["code_hash"] = keccak256_hex(code)
        runtime["code_size"] = len(code)
        runtime["is_contract"] = len(code) > 0
        runtime["runtime_status"] = "contract" if code else "eoa"
        proxy = runtime["proxy"]
        if not code:
            self.no_code = True
            proxy["status"] = "not_proxy"
            proxy["basis"] = ("no runtime code at the pinned block (EOA, self-destructed, or not yet deployed); storage slots not read; "
                              "metadata and probe calls below are qualified: their return data cannot come from this address")
            return

        minimal_impl = detect_minimal_proxy(code)

        # (4) proxy slots at the pin
        slot_values: dict[str, Optional[str]] = {}
        failed: list[str] = []
        for name, slot in PROXY_SLOTS:
            try:
                v = self.client.storage(self.address, slot, self.pinned_hex)
            except RpcCoverageError as e:
                self.limit(e, f"step 4 (eth_getStorageAt {name})", ["A-UPGRADE"], (self.address,))
                failed.append(name)
                continue
            slot_params = [normalize_address(self.address), slot, self.pinned_hex]
            if not isinstance(v, str):
                err = RpcCoverageError("rpc_error", f"eth_getStorageAt returned a non-string result ({type(v).__name__})", "eth_getStorageAt", slot_params)
                self.limit(err, f"step 4 (eth_getStorageAt {name})", ["A-UPGRADE"], (self.address,))
                failed.append(name)
                continue
            try:
                hex_to_bytes(v)
            except ValueError:
                err = RpcCoverageError("rpc_error", "eth_getStorageAt returned non-hex data", "eth_getStorageAt", slot_params)
                self.limit(err, f"step 4 (eth_getStorageAt {name})", ["A-UPGRADE"], (self.address,))
                failed.append(name)
                continue
            proxy["slots_read"][slot] = v
            slot_values[name] = v
        impl = word_to_address(slot_values.get("eip1967_implementation"))
        admin = word_to_address(slot_values.get("eip1967_admin"))
        beacon = word_to_address(slot_values.get("eip1967_beacon"))
        logic = word_to_address(slot_values.get("eip1822_logic"))
        proxy["admin"] = admin
        proxy["beacon"] = beacon

        basis_parts: list[str] = []
        if minimal_impl is not None:
            proxy["status"] = "minimal_1167"
            proxy["implementation"] = minimal_impl
            basis_parts.append("runtime matches the canonical EIP-1167 minimal-proxy pattern; implementation taken from bytecode")
            if impl or beacon or logic:
                basis_parts.append("proxy storage slots are ALSO set: inspect, this is unusual")
        elif impl is not None:
            proxy["status"] = "eip1967"
            proxy["implementation"] = impl
            basis_parts.append("EIP-1967 implementation slot is non-zero at the pin")
        elif beacon is not None:
            proxy["status"] = "beacon"
            basis_parts.append("EIP-1967 beacon slot is non-zero at the pin")
            beacon_impl = self._beacon_implementation(beacon)
            proxy["implementation"] = beacon_impl
            if beacon_impl is None:
                basis_parts.append("beacon.implementation() did not yield an address; resolve manually")
        elif logic is not None:
            proxy["status"] = "eip1822"
            proxy["implementation"] = logic
            basis_parts.append("EIP-1822 (UUPS PROXIABLE) logic slot is non-zero at the pin")
        elif failed:
            proxy["status"] = "unknown"
            basis_parts.append(f"proxy slots could not all be read ({', '.join(failed)}); see limitations; never treat this as not_proxy")
        else:
            proxy["status"] = "not_proxy"
            basis_parts.append("no known proxy slots set; custom proxies possible: inspect selectors")
        if failed and proxy["status"] not in ("unknown", "not_proxy"):
            basis_parts.append(f"some proxy slots could not be read ({', '.join(failed)}); see limitations")
        if admin is not None:
            basis_parts.append("EIP-1967 admin slot is non-zero (transparent-proxy style admin)")
        if proxy["status"] != "not_proxy":
            basis_parts.append("upgrade authority is NOT resolved by this probe: resolve admin/owner/roles/timelock and record under A-UPGRADE")
        proxy["basis"] = "; ".join(basis_parts)

        target_impl = proxy.get("implementation")
        if target_impl:
            impl_params = [normalize_address(target_impl), self.pinned_hex]
            try:
                impl_hex = self.client.code(target_impl, self.pinned_hex)
                if not isinstance(impl_hex, str):
                    raise ValueError(f"non-string result ({type(impl_hex).__name__})")
                impl_code = hex_to_bytes(impl_hex)
                proxy["implementation_code_hash"] = keccak256_hex(impl_code)
                proxy["implementation_code_size"] = len(impl_code)
                if not impl_code:
                    proxy["basis"] += "; implementation has NO code at the pin"
            except RpcCoverageError as e:
                lid = self.limit(e, "step 4 (eth_getCode implementation)", ["A-UPGRADE"], (target_impl,))
                proxy["basis"] += f"; implementation code hash unavailable (limitation {lid})"
            except ValueError as e:
                err = RpcCoverageError("rpc_error", f"eth_getCode(implementation) returned malformed data: {e}", "eth_getCode", impl_params)
                lid = self.limit(err, "step 4 (eth_getCode implementation)", ["A-UPGRADE"], (target_impl,))
                proxy["basis"] += f"; implementation code hash unavailable (limitation {lid})"

    def _beacon_implementation(self, beacon: str) -> Optional[str]:
        assert self.pinned_hex is not None
        try:
            raw = self.client.eth_call(beacon, selector(BEACON_IMPLEMENTATION_SIG), self.pinned_hex)
        except RpcCoverageError as e:
            if classify_call_failure(e) is None:
                self.limit(e, "step 4 (beacon implementation())", ["A-UPGRADE"], (beacon,))
            else:
                self.record_failed(e, "reverted")
            return None
        try:
            return ddcore.decode_address(raw)
        except Exception:
            return None

    def _step_metadata(self) -> None:
        p = self.packet
        assert self.pinned_hex is not None
        meta: dict[str, dict] = {}
        p["metadata"] = meta
        for key, sig, kind in METADATA_FIELDS:
            entry = self._metadata_entry(sig, kind)
            if self.no_code:
                # an EOA cannot answer: keep the raw bytes as evidence of what the endpoint said, but the
                # value is unresolved and the contradiction is spelled out
                decoded = entry["value"]
                entry["qualifier"] = EOA_QUALIFIER
                entry["value"] = None
                entry["status"] = "unresolved"
                entry["reason"] = EOA_QUALIFIER + (f" (endpoint returned data that decodes to {decoded!r}: inconsistent with an address without code)"
                                                   if decoded is not None else "")
            meta[key] = entry

    def _metadata_entry(self, sig: str, kind: str) -> dict:
        assert self.pinned_hex is not None
        sel = selector(sig)
        entry: dict[str, Any] = {
            "value": None,
            "status": "unresolved",
            "source": f"eth_call {sig} ({sel}) at {PIN_ID}",
            "reason": None,
            "selector": sel,
            "raw": None,
            "qualifier": None,
        }
        call_params = [{"to": normalize_address(self.address), "data": sel}, self.pinned_hex]
        try:
            raw = self.client.eth_call(self.address, sel, self.pinned_hex)
        except RpcCoverageError as e:
            if classify_call_failure(e) == "reverted":
                self.record_failed(e, "reverted")
                entry["reason"] = f"call reverted: {e.message}"
            else:
                lid = self.limit(e, f"step 5 (eth_call {sig})", [], (self.address,))
                entry["reason"] = f"rpc failure, see limitation {lid}"
            return entry
        if not isinstance(raw, str):
            err = RpcCoverageError("rpc_error", f"eth_call returned a non-string result ({type(raw).__name__})", "eth_call", call_params)
            lid = self.limit(err, f"step 5 (eth_call {sig})", [], (self.address,))
            entry["reason"] = f"malformed response (non-string result), see limitation {lid}"
            return entry
        entry["raw"] = raw
        try:
            b = hex_to_bytes(raw)
        except ValueError:
            err = RpcCoverageError("rpc_error", "eth_call returned non-hex data", "eth_call", call_params)
            lid = self.limit(err, f"step 5 (eth_call {sig})", [], (self.address,))
            entry["reason"] = f"malformed response (non-hex return data), see limitation {lid}"
            return entry
        if not b:
            entry["reason"] = "empty return (no code at address, fallback returned nothing, or function absent)"
            return entry
        if kind == "string":
            value, status, reason = decode_abi_string_detail(b)
            entry["value"] = value
            entry["status"] = status
            entry["reason"] = reason
            return entry
        # uint kinds
        if len(b) < 32:
            entry["reason"] = f"return data is {len(b)} bytes, shorter than one ABI word"
            return entry
        v = decode_uint(b)
        if kind == "uint8":
            entry["value"] = v
            if len(b) != 32 or v > 255:
                entry["status"] = "nonstandard"
                entry["reason"] = "decimals() did not return a single uint8-range word"
            else:
                entry["status"] = "resolved"
        else:
            entry["value"] = str(v)  # decimal string: base units may exceed 2**53
            if len(b) != 32:
                entry["status"] = "nonstandard"
                entry["reason"] = "totalSupply() returned more than one word; first word kept"
            else:
                entry["status"] = "resolved"
        return entry

    def _step_probes(self) -> None:
        p = self.packet
        assert self.pinned_hex is not None
        specs: list[tuple[str, str, list[str]]] = []
        for label, checks in STANDARD_PROBES:
            specs.append((label, selector(label), checks))
        seen = {data for _, data, _ in specs}
        for label, data in self.extra_calls:
            if data in seen:  # --call repeating a standard probe (or itself) is read once
                continue
            seen.add(data)
            specs.append((label, data, []))
        for label, data, checks in specs:
            entry = self._probe_entry(label, data, checks)
            if self.no_code:
                entry["qualifier"] = EOA_QUALIFIER
                if entry["status"] in ("ok", "reverted"):
                    entry["detail"] = f"INCONSISTENT: {EOA_QUALIFIER}; {entry['detail']}"
            p["probes"].append(entry)

    def _probe_entry(self, label: str, data: str, checks: list[str]) -> dict:
        assert self.pinned_hex is not None
        entry: dict[str, Any] = {
            "name": label,
            "selector": data[:10],
            "calldata": data,
            "raw": None,
            "decoded_candidates": {"uint": None, "address": None, "bool": None, "string": None},
            "status": None,
            "detail": None,
            "qualifier": None,
        }
        call_params = [{"to": normalize_address(self.address), "data": data}, self.pinned_hex]
        try:
            raw = self.client.eth_call(self.address, data, self.pinned_hex)
        except RpcCoverageError as e:
            if classify_call_failure(e) == "reverted":
                self.record_failed(e, "reverted")
                entry["status"] = "reverted"
                entry["detail"] = f"node reported execution failure: {e.message} (a fact about the contract at {PIN_ID}, not a limitation)"
            else:
                lid = self.limit(e, f"step 6 (eth_call {label})", checks, (self.address,))
                entry["status"] = "unavailable"
                entry["detail"] = f"rpc failure, see limitation {lid}"
            return entry
        if not isinstance(raw, str):
            err = RpcCoverageError("rpc_error", f"eth_call returned a non-string result ({type(raw).__name__})", "eth_call", call_params)
            lid = self.limit(err, f"step 6 (eth_call {label})", checks, (self.address,))
            entry["status"] = "unavailable"
            entry["detail"] = f"malformed response (non-string result), see limitation {lid}"
            return entry
        entry["raw"] = raw
        try:
            b = hex_to_bytes(raw)
        except ValueError:
            err = RpcCoverageError("rpc_error", "eth_call returned non-hex data", "eth_call", call_params)
            lid = self.limit(err, f"step 6 (eth_call {label})", checks, (self.address,))
            entry["status"] = "unavailable"
            entry["detail"] = f"malformed response (non-hex return data), see limitation {lid}"
            return entry
        if not b:
            entry["status"] = "empty"
            entry["detail"] = "empty return: selector not implemented, fallback returned nothing, or no code"
            return entry
        entry["status"] = "ok"
        entry["decoded_candidates"] = decode_candidates(raw)
        entry["detail"] = "decoded_candidates are alternative static readings; choose by ABI, do not trust the value as a claim"
        return entry


def build_packet(client: RpcClient, address: str, requested_chain_id: Optional[int] = None,
                 block: str = "latest", extra_calls: Optional[list[tuple[str, str]]] = None,
                 now: Optional[Callable[[], int]] = None) -> tuple[dict, int]:
    """Run the probe. Returns (packet, exit_code)."""
    probe = Probe(client, address, requested_chain_id, block, list(extra_calls or []),
                  now or (lambda: int(time.time())))
    return probe.run()


# ------------------------------------------------------------------------------------
# output
# ------------------------------------------------------------------------------------
def write_packet(packet: dict, path: str) -> str:
    out = os.path.abspath(path)
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(packet, f, indent=2)
        f.write("\n")
    os.replace(tmp, out)
    return out


def summarize(packet: dict, out_path: Optional[str]) -> str:
    req, obs, ident = packet["requested"], packet["observed"], packet["identity"]
    lines = ["rpc_probe: target packet"]
    lines.append(f"  requested : chain {req['chain_id'] if req['chain_id'] is not None else '(none given)'}  address {req['address_checksum']}")
    lines.append(f"  observed  : chain {obs['chain_id']} ({obs['rpc_chain_id_hex']})  endpoint {obs['rpc_endpoint_redacted']}  client {_safe(obs.get('client_version'))}")
    lines.append(f"  identity  : {ident['status']} - {ident['detail']}")
    cache = packet.get("cache") or {}
    if cache.get("status") not in (None, "none"):
        lines.append(f"  cache     : {cache.get('status')}" + (f" - {cache.get('note')}" if cache.get("note") else ""))
    pin = packet.get("pin")
    if pin:
        lines.append(f"  pin {pin['pin_id']}    : block {pin['block_number']}  hash {pin['block_hash']}  time {pin['timestamp_utc']}  captured {pin['captured_at_utc']}")
    rt = packet.get("runtime")
    if rt:
        lines.append(f"  runtime   : runtime_status={rt.get('runtime_status')}  code_hash {rt['code_hash']}  size {rt['code_size']}  contract={rt['is_contract']}")
        px = rt["proxy"]
        lines.append(f"  proxy     : {px['status']}  implementation={px['implementation']}  impl_code_hash={px['implementation_code_hash']}  admin={px['admin']}  beacon={px['beacon']}")
        if px.get("basis"):
            lines.append(f"              basis: {px['basis']}")
    meta = packet.get("metadata")
    if meta:
        parts = []
        for k in ("name", "symbol", "decimals", "total_supply"):
            m = meta[k]
            shown = "" if m["value"] is None else (f" {_safe(m['value'])!r}" if isinstance(m["value"], str) else f" {m['value']}")
            parts.append(f"{k}={m['status']}" + shown + (f" ({_safe(m['reason'], 48)})" if m["reason"] and m["status"] != "resolved" else ""))
        lines.append("  metadata  : " + " | ".join(parts))
    if packet.get("probes"):
        parts = []
        for pr in packet["probes"]:
            s = f"{pr['name']} {pr['status']}"
            if pr["status"] == "ok":
                c = pr["decoded_candidates"]
                text = None if c["string"] is None else repr(_safe(c["string"], 24))
                s += f" -> uint={_safe(c['uint'], 24)} address={c['address']} bool={c['bool']} string={text}"
            parts.append(s)
        lines.append("  probes    : " + "; ".join(parts))
    lims = packet.get("limitations", [])
    lines.append(f"  coverage  : {packet['coverage_status']} ({len(lims)} limitation(s))")
    for lim in lims:
        lines.append(f"              {lim['limitation_id']} [{lim['kind']}] {lim['description']}")
    lines.append(f"  calls     : {len(packet.get('calls', []))} JSON-RPC call(s) recorded; endpoint redacted; read-only methods only")
    if out_path:
        lines.append(f"  written   : {out_path}")
    return "\n".join(lines)


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="rpc_probe.py", description="Build the frozen target packet for one exact (chain, address) from raw JSON-RPC. Read-only.")
    ap.add_argument("--rpc", help=f"JSON-RPC HTTP(S) endpoint (fallback: env {ENV_RPC}); redacted in all output")
    ap.add_argument("--address", help="target contract address (strict; mixed case must satisfy EIP-55)")
    ap.add_argument("--chain-id", type=int, default=None, help="the REQUESTED chain id; compared with eth_chainId")
    ap.add_argument("--block", default="latest",
                    help="block to pin: latest|safe|finalized|N (decimal or 0x hex); default latest. A floating tag is resolved "
                         "to a number by the pin and never cached; prefer finalized where the chain supports it")
    ap.add_argument("--call", action="append", default=[], help='extra read-only eth_call probe, e.g. "owner()" or "balanceOf(address):0x.."; repeatable')
    ap.add_argument("--cache", default=None,
                    help="response cache file (pinned reads only; keyed by the LIVE chain id plus endpoint; a file recorded for "
                         "another chain is ignored with a warning; chain id itself is always live)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"packet output path (default ./{DEFAULT_OUT})")
    ap.add_argument("--timeout", type=float, default=20.0, help="per-request timeout in seconds")
    ap.add_argument("--retries", type=int, default=2,
                    help="retries for transient failures (timeouts, rate limits, DNS); rpc errors, reverts and pruned state are never retried; default 2")
    ap.add_argument("--json", action="store_true", help="print the packet JSON to stdout (summary goes to stderr)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    reason = find_key_material(argv)
    if reason:
        print(f"rpc_probe: refusing to run: {reason}. This tool is read-only and never handles keys; "
              f"remove the argument and re-run.", file=sys.stderr)
        return EXIT_USAGE
    ap = build_parser()
    try:
        args = ap.parse_args(argv)
    except _UsageError as e:
        print(f"rpc_probe: usage error: {e}\n{ap.format_usage()}", file=sys.stderr)
        return EXIT_USAGE

    rpc = args.rpc or os.environ.get(ENV_RPC)
    if not rpc:
        print(f"rpc_probe: no RPC endpoint. Pass --rpc URL or set {ENV_RPC}=URL for the chain you intend to query "
              f"(the observed eth_chainId is compared with --chain-id; a same-symbol token on another chain is not the target).",
              file=sys.stderr)
        return EXIT_USAGE
    if not args.address:
        print("rpc_probe: --address is required (0x + 40 hex; mixed case must be a valid EIP-55 checksum).", file=sys.stderr)
        return EXIT_USAGE
    ok, why = validate_address(args.address)
    if not ok:
        print(f"rpc_probe: malformed --address {args.address!r}: {why}. Fix the address rather than guessing; do not substitute a same-symbol token.", file=sys.stderr)
        return EXIT_USAGE
    if args.chain_id is not None and args.chain_id < 1:
        print("rpc_probe: --chain-id must be a positive integer", file=sys.stderr)
        return EXIT_USAGE
    try:
        block = normalize_block_arg(args.block)
    except ValueError as e:
        print(f"rpc_probe: {e}", file=sys.stderr)
        return EXIT_USAGE
    extra_calls: list[tuple[str, str]] = []
    for spec in args.call:
        try:
            extra_calls.append(parse_call_spec(spec))
        except ValueError as e:
            print(f"rpc_probe: {e}", file=sys.stderr)
            return EXIT_USAGE
    if args.retries < 0:
        print("rpc_probe: --retries must be >= 0", file=sys.stderr)
        return EXIT_USAGE
    cache = ResponseCache(args.cache) if args.cache else None
    try:
        client = RpcClient(rpc, timeout=args.timeout, cache=cache, retries=args.retries)
    except ValueError as e:
        print(f"rpc_probe: bad --rpc: {e}", file=sys.stderr)
        return EXIT_USAGE

    try:
        packet, code = build_packet(client, args.address, args.chain_id, block, extra_calls)
    except RpcPolicyError as e:  # cannot happen with the fixed method set; fail closed anyway
        print(f"rpc_probe: policy violation: {e}", file=sys.stderr)
        return EXIT_FATAL

    out_path = write_packet(packet, args.out)
    summary = summarize(packet, out_path)
    if args.json:
        print(json.dumps(packet, indent=2))
        print(summary, file=sys.stderr)
    else:
        print(summary)
    if (packet.get("cache") or {}).get("status") == "ignored":
        print(f"rpc_probe: WARNING: --cache file ignored: {packet['cache']['note']}", file=sys.stderr)
    if packet["identity"]["status"] == "CHAIN_MISMATCH":
        print("\n" + "!" * 78 + "\nWARNING: " + packet["identity"]["detail"] + "\n" + "!" * 78, file=sys.stderr)
    elif code == EXIT_FATAL:
        print("rpc_probe: FATAL coverage failure: chain id or pin could not be established; packet written with limitations only.", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
