#!/usr/bin/env python3
"""pool_math.py — deterministic pool addressing and price helpers for Uniswap-style pools.

Why: a canonical pool must be identified by exact address (v2/v3) or full pool key (v4), never by symbol
or explorer label. These helpers let you compute what the address/PoolId SHOULD be from the inputs and
then confirm it against onchain evidence (a PairCreated/PoolCreated/Initialize event, or the pool's own
token0()/token1()/fee() reads). A computed address is a hypothesis until an event or read confirms it.

Subcommands (exact CLI contract):
  v2-pair                --factory 0x.. --token-a 0x.. --token-b 0x.. [--init-code-hash 0x..]
  v3-pool                --factory 0x.. --token-a 0x.. --token-b 0x.. --fee 500 [--init-code-hash 0x..]
  v4-pool-id             --currency0 0x.. --currency1 0x.. --fee N --tick-spacing N --hooks 0x..
  v3-tick-to-price       --tick N --decimals0 D0 --decimals1 D1
  v3-sqrtprice-to-price  --sqrt-price-x96 N --decimals0 D0 --decimals1 D1
Every subcommand accepts --json.

Formulas (standard protocol mechanics):
  ordering  token0/currency0 is the numerically smaller address; v4 native currency is the zero address and
            therefore always sorts first. v4-pool-id REJECTS an unsorted pair (exit 2) instead of fixing it,
            because a PoolKey is an exact onchain input and silently reordering would hide a caller error.
  v2 salt   keccak256(token0 ++ token1)                          (abi.encodePacked, 40 bytes)
  v3 salt   keccak256(abi.encode(token0, token1, fee))           (3 x 32-byte words)
  CREATE2   address = last 20 bytes of keccak256(0xff ++ factory ++ salt ++ init_code_hash)
  v4 PoolId keccak256(abi.encode(currency0, currency1, fee uint24, tickSpacing int24, hooks)) (5 x 32-byte words)
  price     raw token1-per-token0 = 1.0001**tick = (sqrtPriceX96 / 2**96)**2 ; human = raw * 10**(dec0 - dec1)

VERIFY AT USE TIME: the default init code hashes below are the widely published constants for the canonical
Uniswap v2 and v3 deployments. Forks (and some chains' canonical deployments, e.g. zkSync-type VMs) use
different bytecode and therefore different hashes. Procedure: fetch one PairCreated/PoolCreated log from
the target chain's factory, compute the address for that event's tokens with this tool, and require an
exact match before trusting any other computed address on that chain. Factory addresses are never defaulted
here: pass the one you verified (eth_getCode non-empty at the pin, and the event topic0 seen in its logs).

Exit codes: 0 ok, 2 usage / invalid input (malformed address, bad checksum, unsorted v4 currencies, a tick
outside [-887272, 887272], a sqrtPriceX96 outside [MIN_SQRT_RATIO, MAX_SQRT_RATIO], decimals outside 0..255).
v2-pair / v3-pool sort --token-a/--token-b into token0/token1 and SAY SO ("input order swapped: ...",
`inputs_reordered` in JSON) so a caller notices the order they gave is not the pool's.
"""
from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, getcontext, localcontext
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from ddcore import ZERO_ADDRESS, encode_static, keccak256, keccak256_hex, to_checksum_address, validate_address  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2

# Defaults: canonical Uniswap deployments. VERIFY AT USE TIME (see module docstring).
UNISWAP_V2_PAIR_INIT_CODE_HASH_DEFAULT = "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f"
UNISWAP_V3_POOL_INIT_CODE_HASH_DEFAULT = "0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54"

INIT_CODE_HASH_WARNING = ("(VERIFY against a PoolCreated/PairCreated event on the target chain; forks and some chains differ)")

# TickMath constants (v3 and v4 share them). Verify at use time against the deployed TickMath if a fork is suspected.
MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342
Q96 = 1 << 96
Q192 = 1 << 192

# v4 constants (verify at use time against the deployed PoolManager / LPFeeLibrary / Hooks library).
V4_MIN_TICK_SPACING = 1
V4_MAX_TICK_SPACING = 32767
V4_DYNAMIC_FEE_FLAG = 0x800000
V4_MAX_LP_FEE = 1_000_000
V4_HOOK_FLAGS = [
    ("BEFORE_INITIALIZE", 1 << 13), ("AFTER_INITIALIZE", 1 << 12),
    ("BEFORE_ADD_LIQUIDITY", 1 << 11), ("AFTER_ADD_LIQUIDITY", 1 << 10),
    ("BEFORE_REMOVE_LIQUIDITY", 1 << 9), ("AFTER_REMOVE_LIQUIDITY", 1 << 8),
    ("BEFORE_SWAP", 1 << 7), ("AFTER_SWAP", 1 << 6),
    ("BEFORE_DONATE", 1 << 5), ("AFTER_DONATE", 1 << 4),
    ("BEFORE_SWAP_RETURNS_DELTA", 1 << 3), ("AFTER_SWAP_RETURNS_DELTA", 1 << 2),
    ("AFTER_ADD_LIQUIDITY_RETURNS_DELTA", 1 << 1), ("AFTER_REMOVE_LIQUIDITY_RETURNS_DELTA", 1 << 0),
]
V4_ALL_HOOK_MASK = (1 << 14) - 1

# v3 fee tier -> tick spacing hints (verify at use time via factory.feeAmountTickSpacing(fee); the 100 tier
# was governance-enabled and may be absent on a given chain).
V3_FEE_TIER_HINTS = {100: 1, 500: 10, 3000: 60, 10000: 200}

DECIMAL_PRECISION = 50
getcontext().prec = DECIMAL_PRECISION


class PoolMathError(ValueError):
    """Invalid input for a pool-math computation (reported as exit 2)."""


# --------------------------------------------------------------------------------------
# Address helpers
# --------------------------------------------------------------------------------------
def checked_address(value: str, label: str) -> str:
    ok, reason = validate_address(value)
    if not ok:
        raise PoolMathError(f"{label}: {reason} ({value!r})")
    return to_checksum_address(value)


def address_int(addr: str) -> int:
    return int(addr, 16)


def sort_tokens(token_a: str, token_b: str) -> tuple[str, str]:
    """Sort by numeric address value (the ordering every Uniswap factory uses). Rejects identical tokens."""
    t0, t1, _ = sort_tokens_noted(token_a, token_b)
    return t0, t1


def sort_tokens_noted(token_a: str, token_b: str) -> tuple[str, str, bool]:
    """(token0, token1, inputs_reordered): the third value is True when token_a sorts AFTER token_b."""
    a, b = checked_address(token_a, "token_a"), checked_address(token_b, "token_b")
    if address_int(a) == address_int(b):
        raise PoolMathError("token_a and token_b are identical addresses")
    if address_int(a) < address_int(b):
        return a, b, False
    return b, a, True


def checked_decimals(value: Any, label: str) -> int:
    d = int(value)
    if not (0 <= d <= 255):
        raise PoolMathError(f"{label} must be a uint8 (0..255), got {d}")
    return d


def checked_hash32(value: str, label: str) -> bytes:
    s = value[2:] if value.startswith(("0x", "0X")) else value
    if len(s) != 64:
        raise PoolMathError(f"{label} must be 32 bytes (0x + 64 hex), got {value!r}")
    try:
        return bytes.fromhex(s)
    except ValueError:
        raise PoolMathError(f"{label} is not hexadecimal: {value!r}")


def create2_address(factory: str, salt: bytes, init_code_hash: bytes) -> str:
    if len(salt) != 32 or len(init_code_hash) != 32:
        raise PoolMathError("salt and init_code_hash must be 32 bytes")
    digest = keccak256(b"\xff" + bytes.fromhex(factory[2:]) + salt + init_code_hash)
    return to_checksum_address("0x" + digest[12:].hex())


# --------------------------------------------------------------------------------------
# v2 / v3 / v4 computations (pure; return dicts used by both the CLI and tests)
# --------------------------------------------------------------------------------------
def v2_pair(factory: str, token_a: str, token_b: str, init_code_hash: Optional[str] = None) -> dict:
    f = checked_address(factory, "factory")
    t0, t1, reordered = sort_tokens_noted(token_a, token_b)
    if t0.lower() == ZERO_ADDRESS:
        raise PoolMathError("token0 is the zero address; v2 pairs require non-zero tokens (wrap native first)")
    ich_hex = init_code_hash or UNISWAP_V2_PAIR_INIT_CODE_HASH_DEFAULT
    ich = checked_hash32(ich_hex, "init_code_hash")
    salt = keccak256(bytes.fromhex(t0[2:]) + bytes.fromhex(t1[2:]))
    pair = create2_address(f, salt, ich)
    return {
        "kind": "uniswap_v2_pair",
        "factory": f, "token0": t0, "token1": t1,
        "inputs_reordered": reordered,
        "salt": "0x" + salt.hex(),
        "salt_preimage": "abi.encodePacked(token0, token1) (40 bytes)",
        "init_code_hash": "0x" + ich.hex(),
        "init_code_hash_is_default": init_code_hash is None,
        "pair": pair,
        "verify": "match against a PairCreated(address indexed token0, address indexed token1, address pair, uint256) "
                  "log from this factory on the target chain, then read pair.token0()/token1()/factory() at the pin",
    }


def v3_pool(factory: str, token_a: str, token_b: str, fee: int, init_code_hash: Optional[str] = None) -> dict:
    f = checked_address(factory, "factory")
    t0, t1, reordered = sort_tokens_noted(token_a, token_b)
    fee = int(fee)
    if not (0 <= fee < (1 << 24)):
        raise PoolMathError("fee must fit uint24 (0 <= fee < 16777216)")
    ich_hex = init_code_hash or UNISWAP_V3_POOL_INIT_CODE_HASH_DEFAULT
    ich = checked_hash32(ich_hex, "init_code_hash")
    encoded = encode_static([("address", t0), ("address", t1), ("uint", fee)])
    salt = keccak256(encoded)
    pool = create2_address(f, salt, ich)
    return {
        "kind": "uniswap_v3_pool",
        "factory": f, "token0": t0, "token1": t1, "fee": fee,
        "inputs_reordered": reordered,
        "tick_spacing_hint": V3_FEE_TIER_HINTS.get(fee),
        "tick_spacing_hint_note": "verify at use time via factory.feeAmountTickSpacing(fee); 0 means the tier is not enabled",
        "salt": "0x" + salt.hex(),
        "salt_preimage": "abi.encode(token0, token1, fee) (3 x 32-byte words) = 0x" + encoded.hex(),
        "init_code_hash": "0x" + ich.hex(),
        "init_code_hash_is_default": init_code_hash is None,
        "pool": pool,
        "verify": "match against a PoolCreated(address indexed token0, address indexed token1, uint24 indexed fee, "
                  "int24 tickSpacing, address pool) log from this factory on the target chain, then read "
                  "pool.token0()/token1()/fee()/factory() at the pin",
    }


def encode_int24(value: int) -> bytes:
    if not (-(1 << 23) <= value < (1 << 23)):
        raise PoolMathError("tick_spacing must fit int24 (-8388608 <= x <= 8388607)")
    return (value & ((1 << 256) - 1)).to_bytes(32, "big")


def hook_permissions(hooks: str) -> list[str]:
    bits = address_int(hooks) & V4_ALL_HOOK_MASK
    return [name for name, flag in V4_HOOK_FLAGS if bits & flag]


def v4_pool_id(currency0: str, currency1: str, fee: int, tick_spacing: int, hooks: str) -> dict:
    c0 = checked_address(currency0, "currency0")
    c1 = checked_address(currency1, "currency1")
    if address_int(c0) >= address_int(c1):
        raise PoolMathError(
            "currency0 must sort strictly before currency1 by numeric address value "
            "(the native currency is the zero address and always sorts first); "
            f"got currency0={c0} currency1={c1}. Swap them and re-run; this tool does not reorder silently."
        )
    fee = int(fee)
    if not (0 <= fee < (1 << 24)):
        raise PoolMathError("fee must fit uint24 (0 <= fee < 16777216)")
    tick_spacing = int(tick_spacing)
    h = checked_address(hooks, "hooks")
    words = [
        ("address", c0), ("address", c1), ("uint", fee), ("int", None), ("address", h),
    ]
    encoded = (encode_static(words[:3]) + encode_int24(tick_spacing) + encode_static(words[4:]))
    assert len(encoded) == 160
    pool_id = keccak256_hex(encoded)
    warnings: list[str] = []
    if not (V4_MIN_TICK_SPACING <= tick_spacing <= V4_MAX_TICK_SPACING):
        warnings.append(f"tick_spacing {tick_spacing} is outside the v4 valid range [{V4_MIN_TICK_SPACING}, {V4_MAX_TICK_SPACING}]; "
                        "PoolManager.initialize would reject it (computed anyway for structural checks)")
    dynamic = fee == V4_DYNAMIC_FEE_FLAG
    if dynamic and h.lower() == ZERO_ADDRESS:
        warnings.append("fee == 0x800000 (dynamic-fee flag) requires a non-zero hooks address; this key would be rejected by initialize")
    if not dynamic and fee > V4_MAX_LP_FEE:
        warnings.append(f"fee {fee} exceeds MAX_LP_FEE {V4_MAX_LP_FEE} and is not the dynamic flag; initialize would reject it")
    perms = hook_permissions(h) if h.lower() != ZERO_ADDRESS else []
    if h.lower() != ZERO_ADDRESS and not perms and not dynamic:
        warnings.append("hooks address has no permission bits set in its low 14 bits and the fee is not dynamic; "
                        "initialize would reject it (isValidHookAddress)")
    return {
        "kind": "uniswap_v4_pool_id",
        "currency0": c0, "currency1": c1, "fee": fee, "tick_spacing": tick_spacing, "hooks": h,
        "currency0_is_native": c0.lower() == ZERO_ADDRESS,
        "fee_is_dynamic_flag": dynamic,
        "hook_permissions_from_address_bits": perms,
        "encoding_hex": "0x" + encoded.hex(),
        "encoding_words": ["0x" + encoded[i:i + 32].hex() for i in range(0, 160, 32)],
        "pool_id": pool_id,
        "pool_id_bytes25": "0x" + encoded_pool_id_bytes25(pool_id),
        "warnings": warnings,
        "verify": "match pool_id against topic[1] of an Initialize(bytes32 indexed id, address indexed currency0, "
                  "address indexed currency1, uint24 fee, int24 tickSpacing, address hooks, uint160 sqrtPriceX96, "
                  "int24 tick) log on the target chain's PoolManager; per-pool state must be read from PoolId-scoped "
                  "storage (StateView.getSlot0/getLiquidity or extsload), never from the PoolManager's token balance",
    }


def encoded_pool_id_bytes25(pool_id_hex: str) -> str:
    """Upper 25 bytes of the PoolId: the key PositionManager.poolKeys uses (PositionInfo packs only 200 bits)."""
    return pool_id_hex[2:2 + 50]


# --------------------------------------------------------------------------------------
# Tick / sqrtPrice helpers (decimal.Decimal, 50 digits)
# --------------------------------------------------------------------------------------
def _dec_pow10(exp: int) -> Decimal:
    return Decimal(10) ** exp


def tick_to_price(tick: int, decimals0: int, decimals1: int) -> dict:
    tick = int(tick)
    warnings: list[str] = []
    if not (MIN_TICK <= tick <= MAX_TICK):
        # checked before any arithmetic: a tick outside TickMath cannot exist in a pool, and a huge one would overflow Decimal
        raise PoolMathError(f"tick {tick} is outside TickMath range [{MIN_TICK}, {MAX_TICK}]; no pool can hold this tick")
    decimals0 = checked_decimals(decimals0, "decimals0")
    decimals1 = checked_decimals(decimals1, "decimals1")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        raw = Decimal("1.0001") ** tick
        human = raw * _dec_pow10(decimals0 - decimals1)
        inverse = Decimal(1) / human if human != 0 else None
        sqrt_approx = (raw.sqrt() * Decimal(Q96)).to_integral_value()
    return {
        "kind": "v3_tick_to_price",
        "tick": tick, "decimals0": decimals0, "decimals1": decimals1,
        "price_raw_token1_per_token0": _fmt(raw),
        "price_human_token1_per_token0": _fmt(human),
        "price_human_token0_per_token1": _fmt(inverse) if inverse is not None else None,
        "sqrt_price_x96_approx": str(int(sqrt_approx)),
        "sqrt_price_x96_approx_note": "Decimal approximation of sqrt(1.0001^tick)*2^96; TickMath's fixed-point result may differ in the last digits",
        "formula": "raw = 1.0001**tick (token1 per token0 in base units); human = raw * 10**(decimals0 - decimals1)",
        "warnings": warnings,
        "verify": "decimals0/decimals1 via eth_call decimals() on token0/token1 at the pin; token order from pool.token0()/token1()",
    }


def sqrtprice_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> dict:
    sp = int(sqrt_price_x96)
    warnings: list[str] = []
    if sp <= 0:
        raise PoolMathError("sqrt_price_x96 must be a positive integer")
    if not (MIN_SQRT_RATIO <= sp <= MAX_SQRT_RATIO):
        raise PoolMathError(f"sqrt_price_x96 {sp} is outside TickMath range [{MIN_SQRT_RATIO}, {MAX_SQRT_RATIO}]; no pool can hold this price")
    decimals0 = checked_decimals(decimals0, "decimals0")
    decimals1 = checked_decimals(decimals1, "decimals1")
    with localcontext() as ctx:
        ctx.prec = DECIMAL_PRECISION
        raw = Decimal(sp * sp) / Decimal(Q192)  # square as an exact int first; Decimal(sp)*Decimal(sp) would round at 50 digits
        human = raw * _dec_pow10(decimals0 - decimals1)
        inverse = Decimal(1) / human if human != 0 else None
        tick_approx = int((raw.ln() / Decimal("1.0001").ln()).to_integral_value(rounding="ROUND_FLOOR")) if raw > 0 else None
    return {
        "kind": "v3_sqrtprice_to_price",
        "sqrt_price_x96": str(sp), "decimals0": decimals0, "decimals1": decimals1,
        "price_raw_token1_per_token0": _fmt(raw),
        "price_human_token1_per_token0": _fmt(human),
        "price_human_token0_per_token1": _fmt(inverse) if inverse is not None else None,
        "tick_approx": tick_approx,
        "tick_approx_note": "floor(log_1.0001(raw)); slot0.tick can differ by one at a tick boundary",
        "formula": "raw = (sqrtPriceX96 / 2**96)**2 = sqrtPriceX96**2 / 2**192; human = raw * 10**(decimals0 - decimals1)",
        "warnings": warnings,
        "verify": "sqrtPriceX96 from slot0() (v3) or StateView.getSlot0(poolId) (v4) at the pin; decimals via decimals() on each token",
    }


def _fmt(d: Decimal) -> str:
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------
def _init_code_line(res: dict) -> str:
    return f"init_code_hash used: {res['init_code_hash']} {INIT_CODE_HASH_WARNING}"


def _order_note(res: dict) -> str:
    return f"input order swapped: token0={res['token0']} token1={res['token1']}"


def _order_lines(res: dict) -> list[str]:
    if res.get("inputs_reordered"):
        return [f"note    : {_order_note(res)} (token_a/token_b sorted by numeric address value; use token0/token1 from here on)"]
    return []


def render_human(res: dict) -> str:
    k = res["kind"]
    lines: list[str] = []
    if k == "uniswap_v2_pair":
        lines += [
            "Uniswap v2-style pair (CREATE2)",
            f"factory : {res['factory']}",
            f"token0  : {res['token0']}",
            f"token1  : {res['token1']}",
            *_order_lines(res),
            f"salt    : {res['salt']}   [{res['salt_preimage']}]",
            f"pair    : {res['pair']}",
            _init_code_line(res),
            f"verify  : {res['verify']}",
        ]
    elif k == "uniswap_v3_pool":
        hint = res["tick_spacing_hint"]
        lines += [
            "Uniswap v3-style pool (CREATE2)",
            f"factory : {res['factory']}",
            f"token0  : {res['token0']}",
            f"token1  : {res['token1']}",
            *_order_lines(res),
            f"fee     : {res['fee']} (tick spacing hint: {hint if hint is not None else 'unknown tier'}; {res['tick_spacing_hint_note']})",
            f"salt    : {res['salt']}   [{res['salt_preimage']}]",
            f"pool    : {res['pool']}",
            _init_code_line(res),
            f"verify  : {res['verify']}",
        ]
    elif k == "uniswap_v4_pool_id":
        lines += [
            "Uniswap v4 PoolKey -> PoolId",
            f"currency0   : {res['currency0']}" + ("  (native currency)" if res["currency0_is_native"] else ""),
            f"currency1   : {res['currency1']}",
            f"fee         : {res['fee']}" + ("  (DYNAMIC_FEE_FLAG 0x800000)" if res["fee_is_dynamic_flag"] else ""),
            f"tickSpacing : {res['tick_spacing']}",
            f"hooks       : {res['hooks']}" + (f"  permissions from address bits: {', '.join(res['hook_permissions_from_address_bits'])}"
                                                if res["hook_permissions_from_address_bits"] else "  (no hook permission bits)"),
            "abi.encode(PoolKey) (160 bytes, 5 words):",
        ]
        for i, w in enumerate(res["encoding_words"]):
            lines.append(f"  word{i}: {w}")
        lines += [
            f"PoolId      : {res['pool_id']}",
            f"PoolId[:25] : {res['pool_id_bytes25']}  (bytes25 key used by PositionManager.poolKeys)",
            "init_code_hash used: n/a (v4 pools are not contracts; VERIFY the PoolId against the Initialize event topic[1] "
            "on the target chain's PoolManager; PoolManager address must itself be verified at use time)",
            f"verify      : {res['verify']}",
        ]
    elif k == "v3_tick_to_price":
        lines += [
            "v3/v4 tick -> price",
            f"tick        : {res['tick']}  (decimals0={res['decimals0']}, decimals1={res['decimals1']})",
            f"raw token1/token0   : {res['price_raw_token1_per_token0']}",
            f"human token1/token0 : {res['price_human_token1_per_token0']}",
            f"human token0/token1 : {res['price_human_token0_per_token1']}  (inverse)",
            f"sqrtPriceX96 approx : {res['sqrt_price_x96_approx']}  ({res['sqrt_price_x96_approx_note']})",
            f"formula     : {res['formula']}",
            "init_code_hash used: n/a (pure arithmetic; VERIFY decimals0/decimals1 via decimals() on each token and "
            "token ordering from the pool's token0()/token1() at the pin)",
            f"verify      : {res['verify']}",
        ]
    elif k == "v3_sqrtprice_to_price":
        lines += [
            "v3/v4 sqrtPriceX96 -> price",
            f"sqrtPriceX96: {res['sqrt_price_x96']}  (decimals0={res['decimals0']}, decimals1={res['decimals1']})",
            f"raw token1/token0   : {res['price_raw_token1_per_token0']}",
            f"human token1/token0 : {res['price_human_token1_per_token0']}",
            f"human token0/token1 : {res['price_human_token0_per_token1']}  (inverse)",
            f"tick approx : {res['tick_approx']}  ({res['tick_approx_note']})",
            f"formula     : {res['formula']}",
            "init_code_hash used: n/a (pure arithmetic; VERIFY decimals0/decimals1 via decimals() on each token and "
            "token ordering from the pool's token0()/token1() at the pin)",
            f"verify      : {res['verify']}",
        ]
    for w in res.get("warnings", []) or []:
        lines.append(f"WARNING: {w}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Uniswap v2/v3 CREATE2 pool addresses, v4 PoolId, tick/sqrtPrice to price.")
    sub = ap.add_subparsers(dest="command")

    p2 = sub.add_parser("v2-pair", help="CREATE2 address of a Uniswap v2-style pair")
    p2.add_argument("--factory", required=True)
    p2.add_argument("--token-a", required=True)
    p2.add_argument("--token-b", required=True)
    p2.add_argument("--init-code-hash", default=None, help="override the default (verify at use time)")
    p2.add_argument("--json", action="store_true")

    p3 = sub.add_parser("v3-pool", help="CREATE2 address of a Uniswap v3-style pool")
    p3.add_argument("--factory", required=True)
    p3.add_argument("--token-a", required=True)
    p3.add_argument("--token-b", required=True)
    p3.add_argument("--fee", required=True, type=int, help="uint24 fee, e.g. 500")
    p3.add_argument("--init-code-hash", default=None, help="override the default (verify at use time)")
    p3.add_argument("--json", action="store_true")

    p4 = sub.add_parser("v4-pool-id", help="Uniswap v4 PoolKey -> PoolId (currencies must already be sorted)")
    p4.add_argument("--currency0", required=True)
    p4.add_argument("--currency1", required=True)
    p4.add_argument("--fee", required=True, type=int)
    p4.add_argument("--tick-spacing", required=True, type=int)
    p4.add_argument("--hooks", required=True)
    p4.add_argument("--json", action="store_true")

    pt = sub.add_parser("v3-tick-to-price", help="tick -> price (raw and decimal-adjusted, plus inverse)")
    pt.add_argument("--tick", required=True, type=int)
    pt.add_argument("--decimals0", required=True, type=int)
    pt.add_argument("--decimals1", required=True, type=int)
    pt.add_argument("--json", action="store_true")

    ps = sub.add_parser("v3-sqrtprice-to-price", help="sqrtPriceX96 -> price (raw and decimal-adjusted, plus inverse)")
    ps.add_argument("--sqrt-price-x96", required=True, help="uint160 as decimal or 0x-hex")
    ps.add_argument("--decimals0", required=True, type=int)
    ps.add_argument("--decimals1", required=True, type=int)
    ps.add_argument("--json", action="store_true")
    return ap


def _parse_int(s: str) -> int:
    s = str(s).strip()
    return int(s, 16) if s.lower().startswith("0x") else int(s)


def run(args: argparse.Namespace) -> dict:
    if args.command == "v2-pair":
        return v2_pair(args.factory, args.token_a, args.token_b, args.init_code_hash)
    if args.command == "v3-pool":
        return v3_pool(args.factory, args.token_a, args.token_b, args.fee, args.init_code_hash)
    if args.command == "v4-pool-id":
        return v4_pool_id(args.currency0, args.currency1, args.fee, args.tick_spacing, args.hooks)
    if args.command == "v3-tick-to-price":
        return tick_to_price(args.tick, args.decimals0, args.decimals1)
    if args.command == "v3-sqrtprice-to-price":
        return sqrtprice_to_price(_parse_int(args.sqrt_price_x96), args.decimals0, args.decimals1)
    raise PoolMathError("unknown command")


def main(argv: Optional[list[str]] = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.command:
        ap.print_help(sys.stderr)
        return EXIT_USAGE
    try:
        res = run(args)
    except (PoolMathError, ValueError, ArithmeticError) as e:  # ArithmeticError: decimal overflow on absurd input
        print(f"error: {e}", file=sys.stderr)
        return EXIT_USAGE
    if args.json:
        if res["kind"] in ("uniswap_v2_pair", "uniswap_v3_pool"):
            res["init_code_hash_notice"] = _init_code_line(res)
            res["order_notice"] = _order_note(res) if res.get("inputs_reordered") else None
        print(json.dumps(res, indent=2))
    else:
        print(render_human(res))
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
