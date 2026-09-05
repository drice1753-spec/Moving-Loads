"""Tests for scripts/pool_math.py (stdlib unittest; no network).

The v2/v3/v4 vectors are the widely published canonical Ethereum-mainnet values (verify at use time; they are
used here only as arithmetic test vectors for the CREATE2 / abi.encode / keccak pipeline). If a vector fails,
investigate the encoding — do not edit the expected value.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import os
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

import ddcore  # noqa: E402
import pool_math as pm  # noqa: E402

V3_FACTORY = "0x1F98431c8aD98523631AE4a59f267346ea31F984"
V2_FACTORY = "0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f"
USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
V3_INIT = "0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54"
V2_INIT = "0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f"
V3_POOL_500 = "0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640"
V3_SALT_500 = "0x08374668a423750b443f65d645c5693995d43722b42cd84f7eeba28b008a40a2"
V2_PAIR = "0xB4e16d0168e52d35CaCD2c6185b44281Ec28C9Dc"
V2_SALT = "0x85053f65cd1ece2bb37b70c13d66eadebf2779df5ddd68cf12f3ccfdc6bfe760"
V4_ETH_USDC_500_ID = "0x21c67e77068de97969ba93d4aab21826d33ca12bb9f565d8496e8fda8a82ca27"
ZERO = ddcore.ZERO_ADDRESS
HOOK_C0 = "0x00000000000000000000000000000000000000C0"


class V3PoolTests(unittest.TestCase):
    def test_canonical_vector(self):
        res = pm.v3_pool(V3_FACTORY, USDC, WETH, 500)
        self.assertEqual(res["token0"], USDC)
        self.assertEqual(res["token1"], WETH)
        self.assertEqual(res["salt"], V3_SALT_500)
        self.assertEqual(res["init_code_hash"], V3_INIT)
        self.assertTrue(res["init_code_hash_is_default"])
        self.assertEqual(res["pool"], V3_POOL_500)
        self.assertEqual(res["tick_spacing_hint"], 10)

    def test_argument_order_does_not_matter(self):
        a = pm.v3_pool(V3_FACTORY, USDC, WETH, 500)
        b = pm.v3_pool(V3_FACTORY, WETH, USDC, 500)
        self.assertEqual(a["pool"], b["pool"])
        self.assertEqual(a["token0"], b["token0"])

    def test_lowercase_inputs_accepted_and_checksummed(self):
        res = pm.v3_pool(V3_FACTORY.lower(), USDC.lower(), WETH.lower(), 500)
        self.assertEqual(res["pool"], V3_POOL_500)
        self.assertEqual(res["factory"], V3_FACTORY)

    def test_salt_is_abi_encode_not_packed(self):
        expected = ddcore.keccak256_hex(ddcore.encode_static([("address", USDC), ("address", WETH), ("uint", 500)]))
        self.assertEqual(pm.v3_pool(V3_FACTORY, USDC, WETH, 500)["salt"], expected)
        self.assertIn("abi.encode(token0, token1, fee)", pm.v3_pool(V3_FACTORY, USDC, WETH, 500)["salt_preimage"])

    def test_override_init_code_hash_changes_address(self):
        other = "0x" + "11" * 32
        res = pm.v3_pool(V3_FACTORY, USDC, WETH, 500, other)
        self.assertNotEqual(res["pool"], V3_POOL_500)
        self.assertFalse(res["init_code_hash_is_default"])

    def test_rejections(self):
        with self.assertRaises(pm.PoolMathError):
            pm.v3_pool(V3_FACTORY, USDC, USDC, 500)
        with self.assertRaises(pm.PoolMathError):
            pm.v3_pool(V3_FACTORY, USDC, WETH, 1 << 24)
        with self.assertRaises(pm.PoolMathError):
            pm.v3_pool("0x1F98431C8AD98523631AE4A59F267346EA31F98x", USDC, WETH, 500)
        with self.assertRaises(pm.PoolMathError):  # bad EIP-55 mixed case
            pm.v3_pool(V3_FACTORY, "0xa0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", WETH, 500)
        with self.assertRaises(pm.PoolMathError):
            pm.v3_pool(V3_FACTORY, USDC, WETH, 500, "0x1234")


class V2PairTests(unittest.TestCase):
    def test_canonical_vector(self):
        res = pm.v2_pair(V2_FACTORY, WETH, USDC)
        self.assertEqual(res["token0"], USDC)
        self.assertEqual(res["token1"], WETH)
        self.assertEqual(res["salt"], V2_SALT)
        self.assertEqual(res["init_code_hash"], V2_INIT)
        self.assertEqual(res["pair"], V2_PAIR)

    def test_salt_is_packed_40_bytes(self):
        expected = ddcore.keccak256_hex(bytes.fromhex(USDC[2:]) + bytes.fromhex(WETH[2:]))
        self.assertEqual(pm.v2_pair(V2_FACTORY, USDC, WETH)["salt"], expected)

    def test_zero_token_rejected(self):
        with self.assertRaises(pm.PoolMathError):
            pm.v2_pair(V2_FACTORY, ZERO, WETH)
        with self.assertRaises(pm.PoolMathError):
            pm.v2_pair(V2_FACTORY, WETH, WETH)


class V4PoolIdTests(unittest.TestCase):
    def test_native_usdc_vector(self):
        res = pm.v4_pool_id(ZERO, USDC, 500, 10, ZERO)
        self.assertEqual(res["pool_id"], V4_ETH_USDC_500_ID)
        self.assertTrue(res["currency0_is_native"])
        self.assertEqual(len(bytes.fromhex(res["encoding_hex"][2:])), 160)
        self.assertEqual(res["pool_id_bytes25"], "0x" + V4_ETH_USDC_500_ID[2:52])
        self.assertEqual(res["warnings"], [])

    def test_encoding_layout(self):
        res = pm.v4_pool_id(ZERO, USDC, 500, 60, ZERO)
        words = res["encoding_words"]
        self.assertEqual(len(words), 5)
        self.assertEqual(words[0], "0x" + "00" * 32)
        self.assertEqual(words[1], "0x" + "00" * 12 + USDC[2:].lower())
        self.assertEqual(words[2], "0x" + "00" * 30 + "01f4")            # fee 500 left-padded
        self.assertEqual(words[3], "0x" + "00" * 31 + "3c")              # tickSpacing 60
        self.assertEqual(words[4], "0x" + "00" * 32)
        self.assertEqual(res["pool_id"], ddcore.keccak256_hex(bytes.fromhex(res["encoding_hex"][2:])))

    def test_negative_tick_spacing_is_twos_complement(self):
        res = pm.v4_pool_id(ZERO, USDC, 500, -1, ZERO)
        self.assertEqual(res["encoding_words"][3], "0x" + "ff" * 32)
        self.assertTrue(any("outside the v4 valid range" in w for w in res["warnings"]))
        self.assertEqual(res["pool_id"], ddcore.keccak256_hex(bytes.fromhex(res["encoding_hex"][2:])))

    def test_unsorted_currencies_rejected(self):
        with self.assertRaises(pm.PoolMathError) as ctx:
            pm.v4_pool_id(USDC, ZERO, 500, 10, ZERO)
        self.assertIn("currency0 must sort strictly before currency1", str(ctx.exception))
        with self.assertRaises(pm.PoolMathError):
            pm.v4_pool_id(WETH, USDC, 500, 10, ZERO)
        with self.assertRaises(pm.PoolMathError):
            pm.v4_pool_id(USDC, USDC, 500, 10, ZERO)

    def test_native_currency_sorts_first(self):
        ok = pm.v4_pool_id(ZERO, WETH, 3000, 60, ZERO)
        self.assertTrue(ok["currency0_is_native"])
        with self.assertRaises(pm.PoolMathError):
            pm.v4_pool_id(WETH, ZERO, 3000, 60, ZERO)

    def test_hook_permission_bits(self):
        res = pm.v4_pool_id(ZERO, USDC, 3000, 60, HOOK_C0)
        self.assertEqual(res["hook_permissions_from_address_bits"], ["BEFORE_SWAP", "AFTER_SWAP"])
        self.assertEqual(pm.hook_permissions(HOOK_C0), ["BEFORE_SWAP", "AFTER_SWAP"])
        self.assertNotEqual(res["pool_id"], pm.v4_pool_id(ZERO, USDC, 3000, 60, ZERO)["pool_id"])

    def test_dynamic_fee_flag_requires_hook(self):
        res = pm.v4_pool_id(ZERO, USDC, 0x800000, 60, ZERO)
        self.assertTrue(res["fee_is_dynamic_flag"])
        self.assertTrue(any("dynamic-fee flag" in w for w in res["warnings"]))
        res2 = pm.v4_pool_id(ZERO, USDC, 0x800000, 60, HOOK_C0)
        self.assertEqual(res2["warnings"], [])

    def test_hook_without_permission_bits_warns(self):
        hook = "0x" + "11" * 18 + "c000"  # low 14 bits zero (0xc000 = bits 15 and 14 only, outside ALL_HOOK_MASK)
        res = pm.v4_pool_id(ZERO, USDC, 3000, 60, hook)
        self.assertEqual(res["hook_permissions_from_address_bits"], [])
        self.assertTrue(any("no permission bits" in w for w in res["warnings"]))


class TickPriceTests(unittest.TestCase):
    def test_tick_zero_is_one(self):
        res = pm.tick_to_price(0, 18, 18)
        self.assertEqual(res["price_raw_token1_per_token0"], "1")
        self.assertEqual(res["price_human_token1_per_token0"], "1")
        self.assertEqual(res["price_human_token0_per_token1"], "1")
        self.assertEqual(res["sqrt_price_x96_approx"], str(1 << 96))

    def test_tick_zero_decimal_adjustment(self):
        res = pm.tick_to_price(0, 6, 18)
        self.assertEqual(Decimal(res["price_human_token1_per_token0"]), Decimal("1e-12"))
        self.assertEqual(Decimal(res["price_human_token0_per_token1"]), Decimal("1e12"))

    def test_positive_tick_structure_with_6_18_decimals(self):
        tick = 202919
        res = pm.tick_to_price(tick, 6, 18)
        raw = Decimal(res["price_raw_token1_per_token0"])
        human = Decimal(res["price_human_token1_per_token0"])
        inverse = Decimal(res["price_human_token0_per_token1"])
        self.assertGreater(raw, 1)
        # human = raw * 10**(6-18); relative structure, not a specific price
        self.assertAlmostEqual(float(human / raw), 1e-12, places=20)
        self.assertLess(abs(human * inverse - 1), Decimal("1e-30"))
        # magnitude sanity: 1.0001**202919 is between 1e8 and 1e9, so human (6/18 decimals) is between 1e-4 and 1e-3
        self.assertTrue(Decimal("1e8") < raw < Decimal("1e9"))
        self.assertTrue(Decimal("1e-4") < human < Decimal("1e-3"))
        self.assertTrue(Decimal("1e3") < inverse < Decimal("1e4"))

    def test_negative_tick_is_inverse_of_positive(self):
        up = Decimal(pm.tick_to_price(1000, 18, 18)["price_raw_token1_per_token0"])
        down = Decimal(pm.tick_to_price(-1000, 18, 18)["price_raw_token1_per_token0"])
        self.assertLess(abs(up * down - 1), Decimal("1e-40"))

    def test_out_of_range_tick_warns(self):
        res = pm.tick_to_price(pm.MAX_TICK + 1, 18, 18)
        self.assertTrue(any("outside TickMath range" in w for w in res["warnings"]))


class SqrtPriceTests(unittest.TestCase):
    def test_q96_is_price_one(self):
        res = pm.sqrtprice_to_price(1 << 96, 18, 18)
        self.assertEqual(res["price_raw_token1_per_token0"], "1")
        self.assertEqual(res["price_human_token1_per_token0"], "1")
        self.assertEqual(res["tick_approx"], 0)

    def test_decimal_adjustment_and_inverse(self):
        res = pm.sqrtprice_to_price(2 << 96, 6, 18)  # raw price 4
        self.assertEqual(res["price_raw_token1_per_token0"], "4")
        self.assertEqual(Decimal(res["price_human_token1_per_token0"]), Decimal("4e-12"))
        self.assertEqual(Decimal(res["price_human_token0_per_token1"]), Decimal("2.5e11"))

    def test_bounds_and_rejections(self):
        self.assertEqual(pm.sqrtprice_to_price(pm.MIN_SQRT_RATIO, 18, 18)["warnings"], [])
        self.assertTrue(pm.sqrtprice_to_price(pm.MAX_SQRT_RATIO + 1, 18, 18)["warnings"])
        with self.assertRaises(pm.PoolMathError):
            pm.sqrtprice_to_price(0, 18, 18)

    def test_tick_roundtrip_approx(self):
        tick = 12345
        sp = int(pm.tick_to_price(tick, 18, 18)["sqrt_price_x96_approx"])
        back = pm.sqrtprice_to_price(sp, 18, 18)["tick_approx"]
        self.assertIn(back, (tick - 1, tick))


class CliTests(unittest.TestCase):
    def _run(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = pm.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_v3_pool_human(self):
        rc, out, _ = self._run(["v3-pool", "--factory", V3_FACTORY, "--token-a", USDC, "--token-b", WETH, "--fee", "500"])
        self.assertEqual(rc, 0)
        self.assertIn(V3_POOL_500, out)
        self.assertIn(f"init_code_hash used: {V3_INIT} (VERIFY against a PoolCreated/PairCreated event on the target chain; "
                      "forks and some chains differ)", out)

    def test_v3_pool_json(self):
        rc, out, _ = self._run(["v3-pool", "--factory", V3_FACTORY, "--token-a", USDC, "--token-b", WETH, "--fee", "500", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["pool"], V3_POOL_500)
        self.assertIn("init_code_hash used:", data["init_code_hash_notice"])

    def test_v2_pair_human(self):
        rc, out, _ = self._run(["v2-pair", "--factory", V2_FACTORY, "--token-a", USDC, "--token-b", WETH,
                                "--init-code-hash", V2_INIT])
        self.assertEqual(rc, 0)
        self.assertIn(V2_PAIR, out)
        self.assertIn("init_code_hash used:", out)
        self.assertIn("VERIFY against a PoolCreated/PairCreated event", out)

    def test_v4_pool_id_cli(self):
        rc, out, _ = self._run(["v4-pool-id", "--currency0", ZERO, "--currency1", USDC, "--fee", "500",
                                "--tick-spacing", "10", "--hooks", ZERO])
        self.assertEqual(rc, 0)
        self.assertIn(V4_ETH_USDC_500_ID, out)
        self.assertIn("init_code_hash used: n/a", out)
        self.assertIn("word0:", out)

    def test_v4_unsorted_exits_2(self):
        rc, out, err = self._run(["v4-pool-id", "--currency0", USDC, "--currency1", ZERO, "--fee", "500",
                                  "--tick-spacing", "10", "--hooks", ZERO])
        self.assertEqual(rc, 2)
        self.assertIn("currency0 must sort strictly before currency1", err)
        self.assertEqual(out, "")

    def test_tick_and_sqrtprice_cli(self):
        rc, out, _ = self._run(["v3-tick-to-price", "--tick", "0", "--decimals0", "6", "--decimals1", "18", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["price_raw_token1_per_token0"], "1")
        rc, out, _ = self._run(["v3-sqrtprice-to-price", "--sqrt-price-x96", str(1 << 96), "--decimals0", "18", "--decimals1", "18"])
        self.assertEqual(rc, 0)
        self.assertIn("raw token1/token0   : 1", out)
        self.assertIn("init_code_hash used: n/a", out)
        rc, out, _ = self._run(["v3-sqrtprice-to-price", "--sqrt-price-x96", hex(1 << 96), "--decimals0", "18", "--decimals1", "18", "--json"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["price_raw_token1_per_token0"], "1")

    def test_no_command_is_usage(self):
        rc, _, _ = self._run([])
        self.assertEqual(rc, 2)

    def test_bad_checksum_exits_2(self):
        rc, _, err = self._run(["v3-pool", "--factory", V3_FACTORY, "--token-a", "0xa0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
                                "--token-b", WETH, "--fee", "500"])
        self.assertEqual(rc, 2)
        self.assertIn("EIP-55", err)


if __name__ == "__main__":
    unittest.main()
