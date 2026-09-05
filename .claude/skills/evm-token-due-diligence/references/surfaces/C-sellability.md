# Surface C - Sellability and executable depth

Rating key: `sellability_exit_depth`. Core checks: `C-HIST-SELL`, `C-QUOTE`. Read this file for "can I
sell / exit", honeypot suspicion, exit sizing, or any question about realistic size; always in `broad`
mode. Inputs from surface B: pool addresses or keys, range facts; from surface A: taxes, gates, limits;
from surface D: holder sizes.

## Purpose

Show, with receipts and pinned read-only quotes, whether an ordinary holder can convert the token into
a quote asset, at what sizes, through which route, at what cost, and where the path fails. Keep three
classes of evidence apart because they prove different things: a historical sell proves execution at
that historical state; a quote or preview at P1 proves what the pool math returns, not a realized exit;
a counterfactual simulation on a verified fork proves what a synthetic account could do at the forked
state.

## What can change the verdict

- No successful historical sell by a non-privileged address exists, or the only sells are by exempt or
  launch wallets.
- Quotes at holder-sized amounts degrade severely per unit versus the small quote, or revert.
- A transfer-path revert for ordinary senders (trading gate closed, blacklist, cooldown, max-tx, tax
  above a limit, hook revert) while privileged or exempt wallets can still sell.
- Exit depends on a single route (one pool, one hook, one router) that surface B rated removable.
- Fee-on-transfer taxes that make the net quote materially lower than the gross quote, or sell taxes
  configurable up to a prohibitive ceiling (surface A).
- Quote asset is itself illiquid, synthetic or bridged, so "sellable" ends in an asset that cannot be
  exited (route to surface G/H).

## Procedure

All reads at P1; a quote is meaningful only at the block it was read. Sizes are stated in base units
with decimals and as a share of the pool's token-side depth.

1. Define the sizes before quoting, and record each definition:
   - small: an amount well below any limit and below 0.1% of the pool's token-side reserves or
     in-range token liquidity (state the number);
   - median holder: the median non-custody balance from surface D's classified holder set at P1;
   - top-10 holder: the tenth-largest non-custody balance (and, separately, the largest if the user
     asks "can the whale exit");
   - user-specified position: the user's stated holding, if any;
   - for fee-on-transfer tokens, each size is quoted GROSS (what leaves the seller) and NET (what
     reaches the pool after tax; surface A supplies the rate); quoters do not model the tax.
2. `C-HIST-SELL` - find a successful historical sell and prove it at receipt level:
   - scan pool `Swap` logs in the searched range (declare the range and pagination): v2-style
     `Swap(address,uint256,uint256,uint256,uint256,address)` where the token-side `amountIn` > 0 and the
     quote-side `amountOut` > 0; v3-style `Swap(address,address,int256,int256,uint160,uint128,int24)`
     where the token-side amount is positive (into the pool) and the quote-side negative; v4
     `Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)` by PoolId (verify each signature
     against the deployed ABI before hashing);
   - prefer a sender that is not exempt, not a launch wallet, not a router-only intermediary; state why
     the chosen sender is "ordinary" (no exemption flag at that block, not in the launch cohort);
   - fetch the receipt: `eth_getTransactionReceipt` status `0x1`; decode the token `Transfer` from the
     seller to the pool, the quote-asset `Transfer` (or WETH-style `Withdrawal` plus a native-value leg
     visible only in a `trace`) from the pool to a NON-POOL recipient, and compute gas paid as
     `gasUsed x effectiveGasPrice`;
   - record: tx hash, block, seller, recipient, token in, quote out, fees observed, gas; finding
     `is_historical: true`, `pin_or_tx` = the tx hash. A router `Transfer` alone, or an event without the
     receipt, is insufficient.
3. `C-QUOTE` - obtain read-only quotes at P1 for every defined size via `eth_call` with the pinned
   block, saving the exact call object as the artifact:
   - v2-style: router `getAmountsOut(uint256,address[])` (dynamic array: encode offset, length, then
     address words by hand; `ddcore.encode_word` covers the static words), cross-checked with direct pair
     math from `getReserves()` and the pair's fee;
   - v3-style: QuoterV2 `quoteExactInputSingle((address,address,uint256,uint24,uint160))` returning
     (amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate); the older Quoter (v1) forces
     the pool swap to revert internally, parses the amount out of that revert, and then returns `amountOut`
     normally to `eth_call` - a revert that reaches you is therefore the pool's own reason (or the quoter's
     "Unexpected error"), which is a failure class to record, not a decoding step; cross-check the spot with
     `python3 <skill-root>/scripts/pool_math.py v3-sqrtprice-to-price --sqrt-price-x96 <slot0.sqrtPriceX96> --decimals0 D0 --decimals1 D1`;
   - v4: V4Quoter `quoteExactInputSingle` with the pool key struct, `zeroForOne`, exact amount and
     hook data; a hook can change the result or revert; record hook data used (usually empty);
   - direct pool math (constant product, or tick-walk for concentrated liquidity across the in-range
     positions from surface B) as a cross-check on every quoter result; disagreement beyond rounding is
     a finding to explain (fee-on-transfer, hook, quoter unsupported).
   Quoter, router and quote-asset addresses are "verify at use time" (table below).
4. Report per size, in one table: route (pool address or PoolId, fee tier, hops), input (gross and
   net of tax), quote asset, expected output, fees (LP fee, protocol fee if switched on, transfer tax),
   per-unit output, degradation versus the small quote's per-unit output (as a percentage), and, when
   the quoter returns it, ticks crossed. Define executable depth as the largest tested size whose
   degradation stays within the user's stated tolerance; if the user gave none, report the curve and do
   not pick a threshold for them.
5. Keep five things distinct in every statement: price impact (output shortfall caused by depth at
   the tested size, measured), slippage tolerance (a user-set parameter on a real trade, never
   measured here), gas (paid in native currency, from receipts or estimates), spot price (from
   `slot0`/`getSlot0`/reserves at P1, an instantaneous marginal figure), and executable depth (the size
   at which the measured impact stays within a tolerance).
6. `C-TAX-NET` - for fee-on-transfer tokens, compute the net quote: net input = gross x (1 - sell tax
   at P1), then quote the net input; report gross-to-quote-asset effective rate; when the tax is routed
   through a swap-back (surface A `A-EXTCALL`), note that the swap-back itself consumes depth and that
   v2 routers require the `SupportingFeeOnTransferTokens` variants.
7. Failure taxonomy - classify every revert or shortfall with the evidence that supports the class:
   trading gate (`tradingActive == false`, `launch` not called), blacklist/whitelist (mapping read for the
   sender), cooldown (last-trade timestamp vs block timestamp), max-tx (size vs `maxTxAmount`),
   max-wallet (buy side; recipient balance vs `maxWallet`), tax above limit (`require(fee <= ..)`
   revert), insufficient liquidity (v2 `INSUFFICIENT_LIQUIDITY`/output amount errors; v3 price-limit or
   zero-liquidity; v4 hook or manager errors), hook revert (v4), quoter unsupported (fee-on-transfer or
   rebasing tokens on quoters that assume conservation; non-standard pools), transfer returned `false`
   without revert. To separate "the quoter cannot model it" (a coverage note) from "the transfer path
   reverts for ordinary holders" (a finding), run a read-only `eth_call` whose call object is
   `{"from": <ordinary holder>, "to": <token contract>, "data": transfer(<pool address>, <amount>)}` at
   the P1 block hex: the TOKEN is the callee and the pool is only the recipient argument (a call sent
   to the pool itself reverts for unrelated reasons and manufactures a false honeypot finding).
   `rpc_probe.py` cannot set `from`; use the client directly and preserve the call object as the artifact:
   ```python
   import sys; sys.path.insert(0, "<skill-root>/scripts")
   from ddcore import RpcClient, RpcCoverageError, encode_static, selector
   data = selector("transfer(address,uint256)") + encode_static([("address", pool), ("uint", amount)]).hex()
   call = [{"from": holder, "to": token, "data": data}, p1_block_hex]
   try: ret = RpcClient(url).call("eth_call", call)                       # 32-byte bool on success
   except RpcCoverageError as e: ret = ("REVERT" if e.execution_failure else "LIMITATION", e.message)
   ```
   No key is used, nothing is signed. A revert (`execution_failure`) is the finding's evidence; any other
   error is a coverage limitation. Repeat with an exempt wallet as `from` to show differential treatment.
8. `C-ROUTE-DEPENDENCY` - state what the exit depends on: which pool(s) (and their `B-PRINCIPAL`
   status), which router or quoter contract, which hook, and whether the quote asset is a raw asset, a
   wrapped native, a bridged representation or a synthetic claim; a removable pool or an admin-settable
   hook makes the quote conditional on that authority.
9. `C-SIM-EXIT` (optional, decisive only under these conditions) - simulate the sale ONLY on a local
   fork verified first:
   ```
   python3 <skill-root>/scripts/fork_guard.py --rpc http://127.0.0.1:8545 --expect-chain-id N --expect-fork-block <P1 block> --out attestation.json
   ```
   Proceed only on exit 0, and only against the exact URL the guard attested (`FORK` below; never
   `EVM_DD_RPC_URL`). `ddcore.RpcClient` refuses non-read methods by design, so the writes go through a
   separate "fork writer" that speaks JSON-RPC directly to the attested fork. No key exists anywhere: the
   fork impersonates a synthetic address, funds it, and seeds its token balance by writing the balance
   slot (slot derivation as in surface D step 2, confirmed with `balanceOf` before use). Fill the
   placeholders, then run it once per tested size:
   ```python
   # fork_writer.py - COUNTERFACTUAL writes, only to the disposable fork that fork_guard.py just attested.
   # Direct JSON-RPC via urllib; never ddcore.RpcClient (read-only by design), never a real endpoint, never a key.
   import json, sys, urllib.request
   sys.path.insert(0, "<skill-root>/scripts"); from ddcore import encode_static, keccak256_hex, selector
   FORK = "http://127.0.0.1:8545"                       # the exact --rpc value fork_guard.py accepted
   CHAIN_ID, TOKEN, QUOTE, ROUTER = N, "0x<token>", "0x<quote asset>", "0x<router>"
   BAL_SLOT, AMOUNT, SWAP_CALLDATA = <balances slot index>, <size in base units>, "0x<calldata of the C-QUOTE route>"
   att = json.load(open("attestation.json"))
   assert att["fork_verified_disposable"] is True and att["fork_chain_id"] == CHAIN_ID, "run fork_guard.py first"
   def rpc(method, params):
       body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
       r = json.load(urllib.request.urlopen(urllib.request.Request(FORK, body, {"content-type": "application/json"}), timeout=60))
       if "error" in r: raise SystemExit(f"{method}: {r['error']}")
       return r["result"]
   assert int(rpc("eth_chainId", []), 16) == CHAIN_ID
   SYN = "0x" + "11" * 20                               # synthetic account: no private key exists; the fork impersonates it
   bal = lambda tok, who: int(rpc("eth_call", [{"to": tok, "data": selector("balanceOf(address)") + encode_static([("address", who)]).hex()}, "latest"]), 16)
   rpc("anvil_setBalance", [SYN, hex(10**18)]); rpc("anvil_impersonateAccount", [SYN])
   rpc("anvil_setStorageAt", [TOKEN, keccak256_hex(encode_static([("address", SYN), ("uint", BAL_SLOT)])), "0x" + hex(AMOUNT)[2:].rjust(64, "0")])
   assert bal(TOKEN, SYN) == AMOUNT, "wrong balances slot index: derive it as in surface D step 2"
   before = (bal(TOKEN, SYN), bal(QUOTE, SYN))
   send = lambda to, data: rpc("eth_getTransactionReceipt", [rpc("eth_sendTransaction", [{"from": SYN, "to": to, "data": data, "gas": hex(1_500_000)}])])
   send(TOKEN, selector("approve(address,uint256)") + encode_static([("address", ROUTER), ("uint", AMOUNT)]).hex())
   rcpt = send(ROUTER, SWAP_CALLDATA)
   after = (bal(TOKEN, SYN), bal(QUOTE, SYN))
   print(json.dumps({"counterfactual": True, "fork_block": att["fork_block"], "tx": rcpt["transactionHash"], "status": rcpt["status"],
                     "token_delta": after[0] - before[0], "quote_delta": after[1] - before[1], "gas_used": rcpt["gasUsed"]}))
   ```
   Hardhat forks expose the same calls as `hardhat_setBalance` / `hardhat_impersonateAccount` /
   `hardhat_setStorageAt`; a native quote asset is read with `eth_getBalance` and its delta corrected for
   gas. Foundry `cast` is an optional alternative for the same writes (`cast rpc --rpc-url $FORK
   anvil_setBalance ...`, `cast send --rpc-url $FORK --unlocked --from $SYN ...`, `cast receipt
   --rpc-url $FORK <tx>`, `cast call --rpc-url $FORK <token> 'balanceOf(address)' $SYN`), always against
   the attested URL. The printed JSON line is the `simulation_counterfactual` artifact. A decisive result
   requires ALL of: a successful receipt (status `0x1`); the intended underlying-asset
   balance delta at the synthetic account (quote asset actually received, not a wrapped or synthetic
   substitute unless that is the stated target), reconciled against the quote and fees; the route and
   costs explained (pool, fee, tax, gas). A success flag, a return value, or an emitted `Swap` event alone
   is insufficient. Record `declarations.simulation` (`used`, `fork_type`, `fork_verified_disposable`,
   `fork_attestation_path`, `fork_chain_id`, `fork_block`, `synthetic_accounts_only`,
   `results_labeled_counterfactual`) and evidence type `simulation_counterfactual` with
   `counterfactual: true`; the validator rejects simulation evidence without these or with
    `fork_chain_id` != the target chain id (E-DECL-FORK), and warns when `fork_attestation_path` is null
    (W-DECL-FORK-ATTESTATION, an error under `--strict`).
10. Write the rows: the quote table as evidence (`rpc_state`, one row per size with the exact call
    object), the historical sell as `receipt` + `log_decoded` (+ `trace` where a native leg is involved),
    failures as findings with the taxonomy class, and the rating from the holder-sized results.

### Verify-at-use table (never assert from memory)

| Item | Why it matters | How to verify at use time |
|---|---|---|
| Router address (v2/v3 style) | `getAmountsOut`, fee-on-transfer variants | the `to` of a historical successful swap receipt that emitted the pool's `Swap`; `factory()` and `WETH()`-style reads on it at P1 match the pool's factory and the quote asset |
| Quoter / QuoterV2 / V4Quoter address | pinned quotes | `factory()` / `poolManager()` reads on the quoter match the pool's factory or manager; a test quote at P1 for a small size agrees with direct pool math within rounding |
| Wrapped native (WETH-style) address | quote asset identity, unwrap legs | `token0()`/`token1()` of the canonical pool; `Deposit`/`Withdrawal` events in a historical receipt |
| Chain id | every call | `eth_chainId` live (rpc_probe.py step 1); mismatch stops the run |
| Fork endpoint | simulation only | `fork_guard.py` exit 0 with attestation JSON |

## Checks

| check_id | surface | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|---|
| C-HIST-SELL | sellability_exit_depth | A non-privileged address sold the token for a quote asset that reached a non-pool recipient, proven at receipt level | receipt status 0x1 + decoded token-in and quote-out legs + gas + sender's non-exempt status at that block | `receipt`, `log_decoded`, `trace` | none for the historical fact; relevance decays with any A/B change after that block |
| C-QUOTE | sellability_exit_depth | Read-only quotes at P1 at the small size and each holder-sized amount, with route, fees, per-unit degradation | one `eth_call` artifact per size at the pinned block + cross-check with direct pool math | `rpc_state` | any block after P1 (re-pin for "now") |
| C-SIM-EXIT | sellability_exit_depth | A synthetic account's sale on a verified fork produced a successful receipt AND the intended underlying-asset delta, with route and costs explained (counterfactual) | fork attestation + receipt + balance deltas before/after + reconciliation to the quote | `simulation_counterfactual` | fork block differs from P1; any A/B change |
| C-TAX-NET | sellability_exit_depth | Net-of-tax quote at each size and the effective gross-to-quote rate | tax rate at P1 (surface A) + net quotes | `rpc_state` | tax setter call after P1 |
| C-ROUTE-DEPENDENCY | sellability_exit_depth | The exit depends on named pools, router/quoter, hook and quote asset, each with its custody or authority status | route table with B-PRINCIPAL/B-HOOK-AUTH cross-references and quote-asset classification | `rpc_state`, `manual_note` (cross-reference) | any B stale condition; quote-asset depeg or bridge halt |
| C-TRANSFER-PATH | sellability_exit_depth | An ordinary holder's `transfer` to the pool at P1 does not revert, and the treatment equals an exempt wallet's | `eth_call` of `{from: holder, to: token, data: transfer(pool, amount)}` at P1 for an ordinary holder and for an exempt wallet, with the call objects and any revert data | `rpc_state` | any A-RESTRICT/A-TAX setter call |
| C-FAILURE-CLASS | sellability_exit_depth | Each observed failure is classified by the taxonomy with supporting reads | the relevant flag/limit/mapping read at P1 per failure | `rpc_state`, `bytecode` | setter call after P1 |

## Common false positives and negatives

False positives (sellability overstated):
- A historical sell by an exempt wallet, the deployer, or a launch-platform contract presented as
  proof that ordinary holders can sell.
- A small-size quote presented as depth; a spot price presented as an executable price.
- A quoter result for a fee-on-transfer token taken gross (the quoter assumed no tax).
- A successful simulation judged by a `Swap` event or a router return value without the quote-asset
  balance delta at the synthetic account.
- Summing quotes across pools without checking that they can be executed independently (the same
  in-range liquidity is not available twice).

False negatives (a false honeypot call):
- A quoter that reverts because it cannot model the token (fee-on-transfer, rebasing) reported as "no
  exit"; classify as `quoter unsupported`, then quote by direct pool math and test the transfer path.
- A revert caused by the quoter's own assumptions (price-limit, sqrtPriceLimitX96 = 0 handling on some
  versions) reported as a token restriction.
- An RPC timeout on the quote reported as a failure of the token (it is `coverage.limitations`).
- Testing only sizes above `maxTxAmount` and concluding "cannot sell"; report the limit and quote
  below it as well.

## Escalation triggers to deep tracks

- Depth history is needed ("was it ever possible to exit at size X?", liquidity moved after launch,
  several positions): `references/deep-tracks/pool-position-history.md`.
- A hook, router or quoter is a proxy or has no corresponding source and its behavior decides the
  quote: `references/deep-tracks/proxy-bytecode.md`.
- Holder sizes are in doubt (rebasing units, shares, non-Transfer changes):
  `references/deep-tracks/transfer-replay.md` (surface D supplies the sizes).
- The exit is a redemption (vault, curve, bond) rather than a swap, or the quote asset is a synthetic
  claim: `references/deep-tracks/dependency-redemption.md` and surface G.
- Historical sells are concentrated in a launch cohort: `references/deep-tracks/launch-cohort.md`.

## How to state results

- "Sellable at the tested sizes under the quoted state: at P1, quotes via QuoterV2 0x.. on pool 0x..
  (fee 3000) returned per-unit outputs within 0.4% (small), 2.1% (median holder), 9.8% (top-10 holder)
  of the small-size per-unit output (E14-E16); direct tick-walk agrees within rounding (E17). Sell tax
  at P1 is 300 bps (A-TAX, E6); figures are net of tax. Quotes are read-only and do not prove a realized
  exit."
- "A successful ordinary-holder sell is proven at tx 0x.. (block N): receipt status 0x1, token in
  X to pool 0x.., quote asset out Y to the seller's address, gas Z (E18-E20). This proves execution at
  that historical state only."
- "At P1 the transfer path reverts for the ordinary holder 0x.. with reason data decoding to a
  trading-gate check (E21), while the exempt wallet 0x.. transfers successfully in the same read-only
  call (E22). Classified as `trading gate`; the setter is held by the owner (A-RESTRICT)."
- "Executable depth under the user's 5% tolerance: up to about N tokens (between the median-holder and
  top-10 sizes; the curve is in E23). The top-10 holder size exceeds the tolerance at P1."
- "Counterfactual (fork of chain N at block B, attestation attestation.json): a synthetic account's sale
  of size S produced receipt status 0x1 and a quote-asset delta of Q at the account, reconciled to the
  quote minus LP fee and tax within rounding (E30-E32). This is not evidence of a realized exit by any
  real holder."
- "Unknown because historical state was unavailable: `Swap` logs before block X could not be read (L4);
  no ordinary-holder sell was found in the covered range; C-HIST-SELL is `unknown`."
- Never: "not a honeypot" as an unconditional statement; "you can exit" without the size, route and
  pin; "deep liquidity" from a single small quote.
