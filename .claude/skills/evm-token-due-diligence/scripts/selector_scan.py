#!/usr/bin/env python3
"""selector_scan.py — PUSH4 selector extraction and risky-selector/opcode scan of EVM runtime bytecode.

Why: the cheapest honest signal about what a deployed contract *might* expose is its runtime bytecode:
function dispatchers compare the calldata selector against PUSH4 immediates, privileged paths often
carry recognisable signatures, and DELEGATECALL/SELFDESTRUCT/CREATE2 opcodes change what an admin can
do later. This scan is a *lead generator* for the A-* and B-PRINCIPAL checks, never a verdict.

Usage:
  python3 <skill-root>/scripts/selector_scan.py --code 0x... [--json]
  python3 <skill-root>/scripts/selector_scan.py --code-file F [--json]
        F may hold a hex string (with or without 0x), a JSON-RPC eth_getCode response
        ({"result": "0x..."}), or raw binary bytes.

What it does:
  * walks opcodes correctly (PUSH1..PUSH32 = 0x60..0x7f skip their immediates, so data bytes are never
    misread as opcodes), after setting aside a trailing solc CBOR metadata blob when one is detected
  * collects distinct PUSH4 immediates as candidate selectors and PUSH20 immediates as candidate
    hardcoded addresses (checksummed; the all-zero address and the 0xff..ff address mask are excluded)
  * flags DELEGATECALL, SELFDESTRUCT, CALL, CALLCODE, STATICCALL, CREATE, CREATE2 opcodes
  * detects EIP-1167 minimal-proxy runtime (and two common variants) and extracts the implementation
  * computes keccak256 code hash and size (ddcore.keccak256_hex)
  * matches candidate selectors against a table of KNOWN RISKY SIGNATURES whose selectors are
    computed at runtime with ddcore.selector (never hardcoded), grouped by category with the surface
    check id each informs (A-MINT, A-RESTRICT, A-TAX, A-SEIZE, A-UPGRADE, A-EXTCALL, A-ADMIN, B-PRINCIPAL)

Exit codes: 0 scan completed, 2 usage / unreadable input. A match is a lead, not a finding, so the
exit code never encodes "risk".

Caveats are ALWAYS printed (human and JSON): presence of a selector is not proof it is reachable or
privileged; absence is not proof of safety (proxies, custom dispatchers, fallback routing); confirm by
reading the deployed implementation, storage and successful calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ddcore  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2

# --------------------------------------------------------------------------------------
# Opcode tables
# --------------------------------------------------------------------------------------
FLAGGED_OPCODES = {
    0xF4: "DELEGATECALL",
    0xFF: "SELFDESTRUCT",
    0xF1: "CALL",
    0xF2: "CALLCODE",
    0xFA: "STATICCALL",
    0xF0: "CREATE",
    0xF5: "CREATE2",
}
FLAG_ORDER = ["DELEGATECALL", "SELFDESTRUCT", "CALL", "CALLCODE", "STATICCALL", "CREATE", "CREATE2"]

# Small name table used only for human-readable context hints around PUSH4 immediates.
_OPCODE_NAMES = {
    0x00: "STOP", 0x01: "ADD", 0x03: "SUB", 0x10: "LT", 0x11: "GT", 0x14: "EQ", 0x15: "ISZERO",
    0x16: "AND", 0x1C: "SHR", 0x35: "CALLDATALOAD", 0x36: "CALLDATASIZE", 0x37: "CALLDATACOPY",
    0x3D: "RETURNDATASIZE", 0x3E: "RETURNDATACOPY", 0x50: "POP", 0x51: "MLOAD", 0x52: "MSTORE",
    0x54: "SLOAD", 0x55: "SSTORE", 0x56: "JUMP", 0x57: "JUMPI", 0x5A: "GAS", 0x5B: "JUMPDEST",
    0x5F: "PUSH0", 0x80: "DUP1", 0x81: "DUP2", 0x90: "SWAP1", 0x91: "SWAP2", 0xF3: "RETURN", 0xFD: "REVERT",
    0xFE: "INVALID",
}
_OPCODE_NAMES.update(FLAGGED_OPCODES)
for _i in range(1, 33):
    _OPCODE_NAMES[0x5F + _i] = f"PUSH{_i}"

ADDRESS_MASK = "0x" + "f" * 40
ZERO_ADDRESS = "0x" + "0" * 40

# --------------------------------------------------------------------------------------
# Known risky signatures (selectors computed at runtime; never hardcode hashes)
# --------------------------------------------------------------------------------------
RISKY_SIGNATURES: dict[str, tuple[str, list[str]]] = {
    "supply": ("A-MINT", [
        "mint(address,uint256)", "mint(uint256)", "mintTo(address,uint256)", "mint(address,uint256,bytes)",
        "mintBatch(address[],uint256[])", "mintFor(address,uint256)", "issue(uint256)", "issue(address,uint256)",
        "increaseSupply(uint256)", "rebase(uint256,uint256)", "rebase(uint256,int256)", "rebase(int256)",
        "rebase(uint256)", "setBalance(address,uint256)", "setTotalSupply(uint256)", "setSupply(uint256)",
        "airdrop(address[],uint256[])", "multiTransfer(address[],uint256[])", "reflect(uint256)",
    ]),
    "restriction": ("A-RESTRICT", [
        "pause()", "unpause()", "setPaused(bool)", "blacklist(address)", "blacklistAddress(address)",
        "setBlacklist(address,bool)", "setBlackList(address,bool)", "addBlackList(address)", "removeBlackList(address)",
        "addToBlacklist(address)", "removeFromBlacklist(address)", "blockAccount(address)", "unblockAccount(address)",
        "freeze(address)", "unfreeze(address)", "freezeAccount(address,bool)", "setWhitelist(address,bool)",
        "addWhitelist(address)", "removeWhitelist(address)", "setMaxTxAmount(uint256)", "setMaxTx(uint256)",
        "setMaxWalletAmount(uint256)", "setMaxWallet(uint256)", "setMaxTxPercent(uint256)", "setCooldownEnabled(bool)",
        "setCooldown(uint256)", "enableTrading()", "openTrading()", "setTradingEnabled(bool)", "setTradingOpen(bool)",
        "setTrading(bool)", "setLimitsInEffect(bool)", "removeLimits()", "setTransferDelayEnabled(bool)",
        "setBots(address[],bool)", "addBots(address[])", "delBot(address)", "setAntiWhale(bool)",
        "setSwapAndLiquifyEnabled(bool)", "setCanTransfer(address,bool)", "restrict(address)", "unrestrict(address)",
    ]),
    "tax": ("A-TAX", [
        "setFee(uint256)", "setFees(uint256,uint256)", "setFees(uint256,uint256,uint256)", "setTaxes(uint256,uint256)",
        "setTax(uint256)", "setBuyTax(uint256)", "setSellTax(uint256)", "setBuyFee(uint256)", "setSellFee(uint256)",
        "setTransferFee(uint256)", "updateBuyFees(uint256,uint256,uint256)", "updateSellFees(uint256,uint256,uint256)",
        "updateBuyFees(uint256,uint256)", "updateSellFees(uint256,uint256)", "updateFees(uint256,uint256)",
        "excludeFromFee(address,bool)", "excludeFromFee(address)", "includeInFee(address)", "excludeFromFees(address,bool)",
        "excludeFromReward(address)", "setFeeExempt(address,bool)", "setIsFeeExempt(address,bool)",
        "setIsTxLimitExempt(address,bool)", "setMarketingWallet(address)", "setTaxWallet(address)",
        "setDevWallet(address)", "setTreasuryWallet(address)", "setFeeReceiver(address)", "setFeeReceivers(address,address)",
        "updateMarketingWallet(address)", "setTaxPercent(uint256)", "setFeeOnTransfer(bool)", "setSwapTokensAtAmount(uint256)",
    ]),
    "seizure": ("A-SEIZE", [
        "burnFrom(address,uint256)", "burn(address,uint256)", "destroyBlackFunds(address)", "forceTransfer(address,address,uint256)",
        "adminTransfer(address,address,uint256)", "seize(address)", "seize(address,uint256)", "confiscate(address)",
        "confiscate(address,uint256)", "wipeFrozenAddress(address)", "rescueTokens(address)", "rescueTokens(address,uint256)",
        "rescueToken(address,uint256)", "rescueETH()", "rescueETH(uint256)", "withdrawToken(address,uint256)",
        "withdrawTokens(address)", "withdrawTokens(address,uint256)", "withdrawStuckTokens(address)", "withdrawStuckETH()",
        "clearStuckBalance()", "clearStuckBalance(uint256)", "recoverERC20(address,uint256)", "recoverToken(address,uint256)",
        "sweep(address)", "sweep(address,address)", "sweepToken(address,address)", "claimStuckTokens(address)",
        "manualSwap()", "manualSend()", "transferFromByAdmin(address,address,uint256)",
    ]),
    "upgrade": ("A-UPGRADE", [
        "upgradeTo(address)", "upgradeToAndCall(address,bytes)", "changeAdmin(address)", "upgrade(address,address)",
        "upgrade(address)", "upgradeAndCall(address,address,bytes)", "diamondCut((address,uint8,bytes4[])[],address,bytes)",
        "setImplementation(address)", "updateImplementation(address)", "setLogic(address)", "setBeacon(address)",
        "upgradeBeaconToAndCall(address,address,bytes)", "setCodeAddress(address)", "updateCodeAddress(address)",
        "setTarget(address)", "setController(address)",
    ]),
    "arbitrary_call": ("A-EXTCALL", [
        "execute(address,uint256,bytes)", "execute(address,bytes)", "execute(address,uint256,bytes,uint8)",
        "executeTransaction(address,uint256,string,bytes,uint256)", "execTransaction(address,uint256,bytes,uint8,uint256,uint256,uint256,address,address,bytes)",
        "call(address,bytes)", "call(address,uint256,bytes)", "callContract(address,bytes)", "multicall(bytes[])",
        "multicall(uint256,bytes[])", "aggregate((address,bytes)[])", "delegate(address)", "delegatecall(address,bytes)",
        "functionCall(address,bytes)", "batch(bytes[],bool)", "run(address,bytes)", "invoke(address,bytes)",
    ]),
    "admin": ("A-ADMIN", [
        "owner()", "transferOwnership(address)", "renounceOwnership()", "grantRole(bytes32,address)", "revokeRole(bytes32,address)",
        "renounceRole(bytes32,address)", "hasRole(bytes32,address)", "getRoleAdmin(bytes32)", "setOwner(address)",
        "acceptOwnership()", "pendingOwner()", "setAdmin(address)", "addAdmin(address)", "removeAdmin(address)",
        "setMinter(address)", "setMinter(address,bool)", "addMinter(address)", "removeMinter(address)", "setOperator(address)",
        "setOperator(address,bool)", "addOperator(address)", "setAuthority(address)", "authority()", "getOwner()",
        "admin()", "implementation()",
    ]),
    "lp_custody": ("B-PRINCIPAL", [
        "decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))", "collect((uint256,address,uint128,uint128))",
        "burn(uint256)", "removeLiquidity(address,address,uint256,uint256,uint256,address,uint256)",
        "removeLiquidityETH(address,uint256,uint256,uint256,address,uint256)",
        "removeLiquidityWithPermit(address,address,uint256,uint256,uint256,address,uint256,bool,uint8,bytes32,bytes32)",
        "removeLiquidityETHSupportingFeeOnTransferTokens(address,uint256,uint256,uint256,address,uint256)",
        "withdraw(uint256)", "withdraw()", "withdraw(address,uint256)", "withdrawAll()", "emergencyWithdraw()",
        "emergencyWithdraw(uint256)", "emergencyWithdraw(address)", "unlock(uint256)", "unlockLP(uint256)",
        "withdrawLP(uint256)", "extendLock(uint256,uint256)", "extendLockDuration(uint256,uint256)",
        "transferLockOwnership(uint256,address)", "transferLock(uint256,address)", "relock(uint256,uint256)",
        "modifyLiquidities(bytes,uint256)", "modifyLiquiditiesWithoutUnlock(bytes,bytes[])",
        "safeTransferFrom(address,address,uint256)", "safeTransferFrom(address,address,uint256,bytes)",
        "approve(address,uint256)", "setApprovalForAll(address,bool)",
        "permit(address,uint256,uint256,uint8,bytes32,bytes32)",
    ]),
}

# Signatures that are common in benign contracts too; the note is printed next to the match.
CONTEXT_NOTES = {
    "burn(uint256)": "context-dependent: NFPM position burn (custody) vs. a holder burning their own tokens",
    "withdraw(uint256)": "context-dependent: WETH-style unwrap vs. locker/vault withdrawal",
    "withdraw()": "context-dependent: generic withdraw; identify what is withdrawn and by whom",
    "approve(address,uint256)": "ERC-20 approve or ERC-721 position approval; matters for position custody only",
    "safeTransferFrom(address,address,uint256)": "ERC-721 transfer: a position NFT can leave a locker this way",
    "setApprovalForAll(address,bool)": "ERC-721 operator approval: an operator can decrease/collect/burn positions",
    "delegate(address)": "context-dependent: ERC20Votes delegation vs. an arbitrary delegate hook",
    "owner()": "informational: resolve the value at the pin, then who controls that owner",
    "implementation()": "informational: proxy admin-style getter; resolve EIP-1967 slots regardless",
    "admin()": "informational: proxy admin-style getter",
    "multicall(bytes[])": "self-multicall batches the contract's own functions; risky only with msg.value/delegatecall semantics",
    "permit(address,uint256,uint256,uint8,bytes32,bytes32)": "ERC-721 permit on position managers lets a signed message approve a spender",
    "hasRole(bytes32,address)": "informational: AccessControl present; enumerate role holders from RoleGranted logs",
    "getRoleAdmin(bytes32)": "informational: AccessControl present",
    "pendingOwner()": "informational: two-step ownership present",
    "authority()": "informational: auth-style access control present",
    "getOwner()": "informational",
}

CAVEATS = [
    "presence of a selector is not proof it is reachable or privileged; absence is not proof of safety "
    "(proxies, custom dispatchers, fallback routing); confirm by reading the deployed implementation, "
    "storage and successful calls",
    "PUSH4 immediates are candidates, not a function list: masks, constants and jump targets also appear as "
    "4-byte pushes, and dispatchers built with binary search or hand-written assembly may not push every selector",
    "a matched signature name is inferred from the selector only (4-byte collisions exist); confirm against verified "
    "source or a successful call before naming the function in a finding",
    "if the contract is a proxy, scan the implementation's runtime as well; this scan sees only the bytes given to it",
]


# --------------------------------------------------------------------------------------
# Input parsing
# --------------------------------------------------------------------------------------
def parse_code(text: str) -> bytes:
    """Parse a hex string (0x-prefixed or not, whitespace tolerated) or a JSON-RPC eth_getCode response."""
    s = text.strip()
    if s.startswith("{"):
        obj = json.loads(s)
        if isinstance(obj, dict):
            s = obj.get("result") or obj.get("code") or obj.get("bytecode") or ""
            if isinstance(s, dict):  # some tools nest {"object": "..."}
                s = s.get("object", "")
            s = str(s).strip()
    s = "".join(s.split())
    if s.startswith(("0x", "0X")):
        s = s[2:]
    if s == "":
        return b""
    if len(s) % 2:
        raise ValueError("hex string has odd length")
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise ValueError("input is not hexadecimal")


def read_code_file(path: str) -> bytes:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw  # raw binary runtime
    stripped = "".join(text.split())
    body = stripped[2:] if stripped.lower().startswith("0x") else stripped
    is_hexish = body == "" or all(c in "0123456789abcdefABCDEF" for c in body)
    if stripped.startswith("{") or is_hexish:
        return parse_code(text)
    return raw


# --------------------------------------------------------------------------------------
# Metadata + opcode walk
# --------------------------------------------------------------------------------------
def strip_cbor_metadata(code: bytes) -> tuple[bytes, Optional[dict]]:
    """Detect a trailing solc-style CBOR metadata blob (…<cbor map><2-byte length>). Returns (code_without_it, info|None).

    Why: metadata bytes are not code, but an unaligned walk over them yields spurious PUSH4 candidates and,
    more importantly, spurious DELEGATECALL/SELFDESTRUCT flags (a 0xf4 byte inside an IPFS hash is common)."""
    if len(code) < 4:
        return code, None
    length = int.from_bytes(code[-2:], "big")
    if length < 2 or length + 2 > len(code):
        return code, None
    seg = code[-(length + 2):-2]
    if seg[0] not in (0xA1, 0xA2, 0xA3, 0xA4):
        return code, None
    markers = [m for m in ("ipfs", "bzzr0", "bzzr1", "solc", "experimental") if m.encode() in seg]
    if not markers:
        return code, None
    return code[:-(length + 2)], {"detected": True, "length": length + 2, "markers": markers}


def walk(code: bytes) -> list[dict]:
    """Linear opcode walk. Each instruction: {offset, op, name, immediate (bytes|None)}. PUSHn skips n immediate bytes."""
    out: list[dict] = []
    i = 0
    n = len(code)
    while i < n:
        op = code[i]
        if 0x60 <= op <= 0x7F:
            size = op - 0x5F
            imm = code[i + 1:i + 1 + size]
            out.append({"offset": i, "op": op, "name": f"PUSH{size}", "immediate": bytes(imm),
                        "truncated": len(imm) < size})
            i += 1 + size
        else:
            out.append({"offset": i, "op": op, "name": _OPCODE_NAMES.get(op, f"0x{op:02x}"), "immediate": None})
            i += 1
    return out


# --------------------------------------------------------------------------------------
# EIP-1167 detection
# --------------------------------------------------------------------------------------
_MINIMAL_PROXY_PATTERNS = [
    # (variant name, prefix hex, suffix hex). Implementation address sits between prefix and suffix.
    ("eip1167", "363d3d373d3d3d363d73", "5af43d82803e903d91602b57fd5bf3"),
    ("eip1167-0age", "3d3d3d3d363d3d37363d73", "5af43d3d93803e603057fd5bf3"),
    ("vyper-forwarder", "366000600037611000600036600073", "5af41558576110006000f3"),
]


def detect_eip1167(code: bytes) -> dict:
    """Return {is_minimal_proxy, implementation, variant}. Exact-pattern match first, then a conservative
    'looks like a forwarder' fallback (tiny code with one PUSH20 and a DELEGATECALL and no CALL/SSTORE)."""
    result = {"is_minimal_proxy": False, "implementation": None, "variant": None}
    for name, pre, suf in _MINIMAL_PROXY_PATTERNS:
        p, s = bytes.fromhex(pre), bytes.fromhex(suf)
        if len(code) == len(p) + 20 + len(s) and code.startswith(p) and code.endswith(s):
            impl = code[len(p):len(p) + 20]
            result.update({"is_minimal_proxy": True, "implementation": ddcore.to_checksum_address("0x" + impl.hex()),
                           "variant": name})
            return result
    if 0 < len(code) <= 96:
        instrs = walk(code)
        push20 = [x for x in instrs if x["name"] == "PUSH20" and not x.get("truncated")]
        has_selector_push = any(x["name"] == "PUSH4" for x in instrs)
        ops = {x["op"] for x in instrs if x["immediate"] is None}
        if (len(push20) == 1 and 0xF4 in ops and 0xF1 not in ops and 0x55 not in ops and 0xF2 not in ops
                and not has_selector_push):
            result.update({"is_minimal_proxy": True,
                           "implementation": ddcore.to_checksum_address("0x" + push20[0]["immediate"].hex()),
                           "variant": "forwarder-like (non-canonical; confirm by reading the code)"})
    return result


# --------------------------------------------------------------------------------------
# Risky table
# --------------------------------------------------------------------------------------
def risky_table() -> list[dict]:
    """[{selector, signature, category, check_id, note}] computed at runtime from RISKY_SIGNATURES."""
    rows = []
    for category, (check_id, sigs) in RISKY_SIGNATURES.items():
        for sig in sigs:
            rows.append({"selector": ddcore.selector(sig), "signature": sig, "category": category,
                         "check_id": check_id, "note": CONTEXT_NOTES.get(sig)})
    return rows


def risky_index() -> dict[str, list[dict]]:
    idx: dict[str, list[dict]] = {}
    for row in risky_table():
        idx.setdefault(row["selector"], []).append(row)
    return idx


# --------------------------------------------------------------------------------------
# Scan
# --------------------------------------------------------------------------------------
def scan(code: bytes) -> dict:
    full_hash = ddcore.keccak256_hex(code)
    caveats = list(CAVEATS)
    body, meta = strip_cbor_metadata(code)
    if meta:
        caveats.append(f"a trailing CBOR metadata blob of {meta['length']} bytes (markers: {', '.join(meta['markers'])}) "
                       "was excluded from the opcode walk; if that suffix is not real metadata, those bytes could be code")
    if len(code) == 0:
        caveats.append("no runtime code: the address is an EOA, not yet deployed, or self-destructed at this block")

    instrs = walk(body)
    selectors: list[str] = []
    contexts: dict[str, str] = {}
    push20: list[str] = []
    push20_excluded = 0
    counts: dict[str, int] = {name: 0 for name in FLAG_ORDER}
    for k, ins in enumerate(instrs):
        if ins["immediate"] is None:
            name = FLAGGED_OPCODES.get(ins["op"])
            if name:
                counts[name] += 1
            continue
        if ins.get("truncated"):
            caveats.append(f"PUSH at offset {ins['offset']} runs past the end of code (truncated immediate)")
            continue
        if ins["name"] == "PUSH4":
            sel = "0x" + ins["immediate"].hex()
            if sel not in contexts:
                selectors.append(sel)
                nxt = instrs[k + 1]["name"] if k + 1 < len(instrs) else "<end>"
                contexts[sel] = nxt
        elif ins["name"] == "PUSH20":
            addr = "0x" + ins["immediate"].hex()
            if addr in (ZERO_ADDRESS, ADDRESS_MASK):
                push20_excluded += 1
                continue
            cs = ddcore.to_checksum_address(addr)
            if cs not in push20:
                push20.append(cs)

    idx = risky_index()
    matched: list[dict] = []
    matched_selectors = set()
    for sel in selectors:
        for row in idx.get(sel, []):
            matched.append(dict(row))
            matched_selectors.add(sel)
    unmatched_count = len([s for s in selectors if s not in matched_selectors])

    flags = {name: counts[name] > 0 for name in FLAG_ORDER}
    proxy = detect_eip1167(body if not meta else code)
    if not proxy["is_minimal_proxy"] and flags["DELEGATECALL"]:
        caveats.append("DELEGATECALL present but no EIP-1167 pattern: could be a custom proxy, a library call, or a "
                       "diamond; read EIP-1967/EIP-1822 slots (rpc_probe.py) and the implementation's runtime")
    if flags["SELFDESTRUCT"]:
        caveats.append("SELFDESTRUCT byte present: on post-Cancun chains it only transfers balance unless in the "
                       "creating transaction; still, confirm reachability before treating it as a kill switch")
    if flags["CREATE2"]:
        caveats.append("CREATE2 present: deterministic redeployment patterns (metamorphic contracts) are possible; "
                       "pair with SELFDESTRUCT reachability and factory analysis")

    return {
        "code_hash": full_hash,
        "code_size": len(code),
        "is_empty": len(code) == 0,
        "empty_code_hash_matches": full_hash == ddcore.EMPTY_CODE_HASH,
        "cbor_metadata": meta or {"detected": False},
        "selectors": selectors,
        "selector_next_opcode": contexts,
        "matched": matched,
        "matched_by_check": _group_by_check(matched),
        "unmatched_count": unmatched_count,
        "opcode_flags": flags,
        "opcode_counts": counts,
        "push20_candidates": push20,
        "push20_excluded_masks": push20_excluded,
        "eip1167": proxy,
        "caveats": caveats,
    }


def _group_by_check(matched: list[dict]) -> dict[str, list[str]]:
    g: dict[str, list[str]] = {}
    for m in matched:
        g.setdefault(m["check_id"], []).append(m["signature"])
    return g


# --------------------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------------------
def render_human(res: dict) -> str:
    lines = []
    lines.append("selector_scan.py — runtime bytecode scan (leads only; see caveats)")
    lines.append(f"code_hash : {res['code_hash']}")
    lines.append(f"code_size : {res['code_size']} bytes" + ("  (EMPTY code)" if res["is_empty"] else ""))
    meta = res["cbor_metadata"]
    if meta.get("detected"):
        lines.append(f"metadata  : trailing CBOR blob {meta['length']} bytes excluded from walk (markers: {', '.join(meta['markers'])})")
    lines.append(f"selectors : {len(res['selectors'])} distinct PUSH4 candidates, {len(res['matched'])} risky-table matches, "
                 f"{res['unmatched_count']} unmatched")
    if res["selectors"]:
        lines.append("  " + " ".join(res["selectors"]))
    lines.append("")
    lines.append("matched risky signatures (selector | signature | category | check_id):")
    if not res["matched"]:
        lines.append("  (none matched; this is NOT evidence of absence)")
    else:
        by_cat: dict[str, list[dict]] = {}
        for m in res["matched"]:
            by_cat.setdefault(m["category"], []).append(m)
        for cat in RISKY_SIGNATURES:
            rows = by_cat.get(cat)
            if not rows:
                continue
            lines.append(f"  [{cat} -> {rows[0]['check_id']}]")
            for m in rows:
                note = f"   # {m['note']}" if m.get("note") else ""
                lines.append(f"    {m['selector']}  {m['signature']}{note}")
    lines.append("")
    flags = res["opcode_flags"]
    lines.append("opcode flags: " + "  ".join(f"{k}={'YES' if v else 'no'}" for k, v in flags.items()))
    lines.append("")
    if res["push20_candidates"]:
        lines.append("PUSH20 candidate hardcoded addresses (checksummed; zero address and 0xff..ff mask excluded):")
        for a in res["push20_candidates"]:
            lines.append(f"  {a}")
    else:
        lines.append("PUSH20 candidate hardcoded addresses: none")
    p = res["eip1167"]
    if p["is_minimal_proxy"]:
        lines.append(f"EIP-1167 minimal proxy: YES ({p['variant']}) -> implementation {p['implementation']} "
                     "(scan that runtime next)")
    else:
        lines.append("EIP-1167 minimal proxy: no")
    lines.append("")
    lines.append("CAVEATS:")
    for c in res["caveats"]:
        lines.append(f"  - {c}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PUSH4 selector extraction and risky-selector/opcode scan of EVM runtime bytecode.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--code", help="runtime bytecode as hex (0x-prefixed)")
    g.add_argument("--code-file", help="file holding hex, an eth_getCode JSON response, or raw bytes")
    ap.add_argument("--json", action="store_true", help="print JSON instead of the human table")
    args = ap.parse_args(argv)

    if args.code is None and args.code_file is None:
        ap.print_usage(sys.stderr)
        print("error: one of --code or --code-file is required", file=sys.stderr)
        return EXIT_USAGE
    try:
        code = parse_code(args.code) if args.code is not None else read_code_file(args.code_file)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        print(f"error: cannot read bytecode: {e}", file=sys.stderr)
        return EXIT_USAGE

    res = scan(code)
    if args.json:
        print(json.dumps(res, indent=2))
    else:
        print(render_human(res))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
