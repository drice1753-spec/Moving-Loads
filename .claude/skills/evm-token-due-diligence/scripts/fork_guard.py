#!/usr/bin/env python3
"""fork_guard.py — verify that an RPC endpoint is a DISPOSABLE LOCAL FORK before any simulation write.

Why: simulation writes are permitted only on a verified disposable fork with synthetic accounts, and
every result from such a fork is counterfactual. This guard produces the attestation the manifest's
declarations.simulation block points at. It is read-only and never handles keys: it refuses to run
if any argument looks like a private key, a mnemonic, or a key-carrying flag.

Checks (exit 0 only when every check is ok):
  no_key_material_in_argv           argv contains no private key / mnemonic / --private-key style flags
  local_endpoint                    URL host is loopback (localhost, 127.0.0.0/8, ::1) or RFC1918
                                    private (10/8, 172.16/12, 192.168/16); DNS names are NOT resolved
  fork_self_identification          web3_clientVersion mentions anvil/hardhat/ganache/foundry, OR
                                    anvil_nodeInfo returns an object, OR hardhat_metadata returns an object
  chain_id_matches                  eth_chainId == --expect-chain-id
  block_at_or_above_expected_fork_block   eth_blockNumber >= --expect-fork-block (only when given)
  fork_config_reported              informational: fork block + REDACTED fork url from anvil_nodeInfo.forkConfig
                                    or hardhat_metadata.forkedNetwork when the node reports them

Usage:
  python3 <skill-root>/scripts/fork_guard.py --rpc http://127.0.0.1:8545 --expect-chain-id N
        [--expect-fork-block B] [--out attestation.json] [--timeout S] [--json]

Exit codes: 0 all checks ok (fork_verified_disposable = true), 1 a check failed, 2 usage / refused.

Verify at use time: node-introspection field names (anvil_nodeInfo.forkConfig.forkUrl/forkBlockNumber,
hardhat_metadata.forkedNetwork.chainId/forkBlockNumber) follow the node versions current when this was
written; check them against your node's documentation or its raw response if extraction returns null.
Note that a Hardhat fork keeps its own default chain id unless configured, and anvil adopts the forked
chain's id by default: --expect-chain-id must match what YOUR fork actually reports.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from ddcore import RpcClient, RpcCoverageError, RpcPolicyError, hex_to_int, iso_utc, redact_url  # noqa: E402

ATTESTATION_VERSION = "1.0"
TOOL = {"name": "fork_guard.py", "version": "1.0", "library": "ddcore"}
DEFAULT_OUT = "fork-attestation.json"
NOTES = "Results produced on this fork are COUNTERFACTUAL and must be labeled as such"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_USAGE = 2

LOCAL_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)
FORK_MARKERS = ("anvil", "hardhat", "ganache", "foundry")

_PK_RE = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_LOWER_WORD_RE = re.compile(r"^[a-z]+$")
_FORBIDDEN_FLAGS = ("--private-key", "--privatekey", "--mnemonic", "--seed", "--keystore", "--secret")


# ------------------------------------------------------------------------------------
# argv hygiene
# ------------------------------------------------------------------------------------
def find_key_material(argv: list[str]) -> Optional[str]:
    """Reason string if any argument looks like key material or a key-carrying flag; else None."""
    run = 0
    for arg in argv:
        low = arg.lower()
        for flag in _FORBIDDEN_FLAGS:
            if low == flag or low.startswith(flag + "=") or low.startswith(flag + "-"):
                return f"flag {arg.split('=')[0]} is never accepted (the guard does not handle keys)"
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
# host classification (no DNS resolution: a name may resolve anywhere)
# ------------------------------------------------------------------------------------
def classify_host(url: str) -> tuple[bool, str]:
    try:
        p = urllib.parse.urlsplit(url)
    except Exception:
        return False, "URL could not be parsed"
    if p.scheme.lower() not in ("http", "https"):
        return False, f"scheme {p.scheme!r} is not http(s)"
    host = p.hostname
    if not host:
        return False, "URL has no host"
    if host == "localhost" or host.endswith(".localhost"):
        return True, f"host {host!r} is the loopback name"
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False, f"host {host!r} is a DNS name, not localhost or a literal loopback/RFC1918 address; names are not resolved because they may point anywhere"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    for net in LOCAL_NETWORKS:
        if ip in net:
            return True, f"host {ip} is within {net}"
    return False, f"host {ip} is not loopback or RFC1918 private"


def _as_int(v: Any) -> Optional[int]:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        try:
            return hex_to_int(v) if v.startswith("0x") else int(v)
        except ValueError:
            return None
    return None


# ------------------------------------------------------------------------------------
# the guard
# ------------------------------------------------------------------------------------
def run_guard(rpc: str, expect_chain_id: int, expect_fork_block: Optional[int] = None,
              timeout: float = 10.0, argv_checked: bool = True) -> dict:
    checks: list[dict] = []
    att: dict[str, Any] = {
        "attestation_version": ATTESTATION_VERSION,
        "tool": dict(TOOL),
        "fork_verified_disposable": False,
        "fork_type": "unknown_local",
        "fork_chain_id": None,
        "fork_block": None,
        "current_block": None,
        "client_version": None,
        "fork_source_redacted": None,
        "rpc_endpoint_redacted": redact_url(rpc),
        "expected": {"chain_id": expect_chain_id, "fork_block": expect_fork_block},
        "checked_at_utc": iso_utc(int(time.time())),
        "checks": checks,
        "notes": NOTES,
        "declarations": {"no_real_signing": True, "no_broadcast": True, "no_private_keys_requested": True},
    }

    def check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    check("no_key_material_in_argv", argv_checked, "no private key, mnemonic, or key flag present in the arguments")

    ok, detail = classify_host(rpc)
    check("local_endpoint", ok, detail)
    if not ok:
        check("fork_self_identification", False, "not attempted: endpoint is not local (no network request was made)")
        check("chain_id_matches", False, "not attempted: endpoint is not local")
        att["fork_verified_disposable"] = False
        return att

    try:
        client = RpcClient(rpc, timeout=timeout)
    except ValueError as e:
        check("fork_self_identification", False, f"client could not be created: {e}")
        check("chain_id_matches", False, "not attempted")
        return att

    # --- self identification -------------------------------------------------------
    client_version: Optional[str] = None
    cv_detail = ""
    try:
        cv = client.call("web3_clientVersion", [])
        client_version = cv if isinstance(cv, str) else None
        cv_detail = f"web3_clientVersion={client_version!r}"
    except RpcCoverageError as e:
        cv_detail = f"web3_clientVersion unavailable ({e.kind}: {e.message})"
    att["client_version"] = client_version

    anvil_info: Optional[dict] = None
    try:
        r = client.call("anvil_nodeInfo", [])
        anvil_info = r if isinstance(r, dict) else None
    except RpcCoverageError:
        anvil_info = None
    hardhat_meta: Optional[dict] = None
    try:
        r = client.call("hardhat_metadata", [])
        hardhat_meta = r if isinstance(r, dict) else None
    except RpcCoverageError:
        hardhat_meta = None

    low_cv = (client_version or "").lower()
    identified = any(m in low_cv for m in FORK_MARKERS) or anvil_info is not None or hardhat_meta is not None
    if anvil_info is not None or "anvil" in low_cv or "foundry" in low_cv:
        att["fork_type"] = "anvil"
    elif hardhat_meta is not None or "hardhat" in low_cv:
        att["fork_type"] = "hardhat"
    elif "ganache" in low_cv:
        att["fork_type"] = "ganache"
    else:
        att["fork_type"] = "unknown_local"
    id_bits = [cv_detail,
               "anvil_nodeInfo=" + ("object" if anvil_info is not None else "absent"),
               "hardhat_metadata=" + ("object" if hardhat_meta is not None else "absent")]
    check("fork_self_identification", identified,
          ("; ".join(id_bits)) if identified else
          "node does not self-identify as a disposable fork: " + "; ".join(id_bits))

    # --- chain id --------------------------------------------------------------------
    observed_chain: Optional[int] = None
    try:
        observed_chain = hex_to_int(client.call("eth_chainId", []))
    except (RpcCoverageError, ValueError, TypeError) as e:
        check("chain_id_matches", False, f"eth_chainId unavailable: {e}")
    att["fork_chain_id"] = observed_chain

    # fork config (needed for the chain-id hint and for the informational check)
    fork_block: Optional[int] = None
    fork_source: Optional[str] = None
    forked_chain: Optional[int] = None
    if anvil_info is not None:
        fc = anvil_info.get("forkConfig")
        if isinstance(fc, dict):
            fork_block = _as_int(fc.get("forkBlockNumber"))
            url = fc.get("forkUrl")
            fork_source = redact_url(url) if isinstance(url, str) and url else None
    if hardhat_meta is not None and fork_block is None:
        fn = hardhat_meta.get("forkedNetwork")
        if isinstance(fn, dict):
            fork_block = _as_int(fn.get("forkBlockNumber"))
            forked_chain = _as_int(fn.get("chainId"))
            fork_source = "<hardhat forkedNetwork; url not reported by hardhat_metadata>"
    att["fork_block"] = fork_block
    att["fork_source_redacted"] = fork_source

    if observed_chain is not None:
        if observed_chain == expect_chain_id:
            check("chain_id_matches", True, f"eth_chainId={observed_chain} equals expected {expect_chain_id}")
        else:
            hint = ""
            if forked_chain is not None and forked_chain == expect_chain_id:
                hint = (f"; the node reports forkedNetwork.chainId={forked_chain}: a Hardhat fork keeps its own default chain id "
                        f"unless configured, so either configure the fork's chain id or pass the id it actually reports")
            check("chain_id_matches", False, f"eth_chainId={observed_chain} differs from expected {expect_chain_id}{hint}")

    # --- block number ----------------------------------------------------------------
    current_block: Optional[int] = None
    try:
        current_block = hex_to_int(client.call("eth_blockNumber", []))
    except (RpcCoverageError, ValueError, TypeError) as e:
        if expect_fork_block is not None:
            check("block_at_or_above_expected_fork_block", False, f"eth_blockNumber unavailable: {e}")
    att["current_block"] = current_block
    if expect_fork_block is not None and current_block is not None:
        ok = current_block >= expect_fork_block
        check("block_at_or_above_expected_fork_block", ok,
              f"eth_blockNumber={current_block} {'>=' if ok else '<'} expected fork block {expect_fork_block}")

    if fork_block is not None or fork_source is not None:
        check("fork_config_reported", True, f"fork_block={fork_block} fork_source={fork_source}")
    else:
        check("fork_config_reported", True,
              "node reported no fork configuration (fresh local chain, non-forking mode, or a node API this guard does not parse): results are still counterfactual")

    att["fork_verified_disposable"] = all(c["ok"] for c in checks)
    return att


# ------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------
def render_table(att: dict) -> str:
    rows = ["fork_guard: checks"]
    width = max(len(c["name"]) for c in att["checks"]) if att["checks"] else 20
    for c in att["checks"]:
        rows.append(f"  {c['name'].ljust(width)}  {'ok  ' if c['ok'] else 'FAIL'}  {c['detail']}")
    rows.append(f"  {'fork_verified_disposable'.ljust(width)}  {att['fork_verified_disposable']}  "
                f"type={att['fork_type']} chain={att['fork_chain_id']} fork_block={att['fork_block']} "
                f"current_block={att['current_block']} endpoint={att['rpc_endpoint_redacted']}")
    rows.append(f"  note: {att['notes']}")
    return "\n".join(rows)


def write_attestation(att: dict, path: str) -> str:
    out = os.path.abspath(path)
    parent = os.path.dirname(out)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = out + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(att, f, indent=2)
        f.write("\n")
    os.replace(tmp, out)
    return out


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(prog="fork_guard.py", description="Verify that a local RPC endpoint is a disposable fork before any simulation write. Read-only; never accepts keys.")
    ap.add_argument("--rpc", required=True, help="local fork endpoint, e.g. http://127.0.0.1:8545")
    ap.add_argument("--expect-chain-id", type=int, required=True, help="chain id the fork must report")
    ap.add_argument("--expect-fork-block", type=int, default=None, help="minimum block number the fork must be at")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"attestation output path (default ./{DEFAULT_OUT})")
    ap.add_argument("--timeout", type=float, default=10.0, help="per-request timeout in seconds")
    ap.add_argument("--json", action="store_true", help="print the attestation JSON to stdout (table goes to stderr)")
    return ap


def main(argv: Optional[list[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    reason = find_key_material(argv)
    if reason:
        print(f"fork_guard: refusing to run: {reason}. The guard never handles keys; simulation accounts must be "
              f"synthetic accounts created by the fork itself. Remove the argument and re-run.", file=sys.stderr)
        return EXIT_USAGE
    ap = build_parser()
    try:
        args = ap.parse_args(argv)
    except _UsageError as e:
        print(f"fork_guard: usage error: {e}\n{ap.format_usage()}", file=sys.stderr)
        return EXIT_USAGE
    if args.expect_chain_id < 1:
        print("fork_guard: --expect-chain-id must be a positive integer", file=sys.stderr)
        return EXIT_USAGE
    if args.expect_fork_block is not None and args.expect_fork_block < 0:
        print("fork_guard: --expect-fork-block must be >= 0", file=sys.stderr)
        return EXIT_USAGE
    try:
        att = run_guard(args.rpc, args.expect_chain_id, args.expect_fork_block, timeout=args.timeout, argv_checked=True)
    except RpcPolicyError as e:  # fixed method set; fail closed anyway
        print(f"fork_guard: policy violation: {e}", file=sys.stderr)
        return EXIT_FAIL
    out_path = write_attestation(att, args.out)
    table = render_table(att) + f"\n  written: {out_path}"
    if args.json:
        print(json.dumps(att, indent=2))
        print(table, file=sys.stderr)
    else:
        print(table)
    if att["fork_verified_disposable"]:
        return EXIT_OK
    print("fork_guard: FAIL - do not perform simulation writes against this endpoint.", file=sys.stderr)
    return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
