# Platform: Uniswap v3 (and v3-style forks)

Read this when a candidate or discovered pool exposes the v3 pool interface, when a position NFT on a
NonfungiblePositionManager (NFPM) holds the liquidity, when a locker holds such an NFT, or when a launch
platform seeded a one-sided v3 position. It extends `references/surfaces/B-liquidity-custody.md`
(`B-CANON`, `B-PRINCIPAL`, `B-SIDE`, `B-LOCKER-AUTH`, `B-OPERATOR-APPROVALS`, `B-RANGE`, `B-DIRECT-MINT`),
`references/surfaces/C-sellability.md` (`C-QUOTE`, `C-TAX-NET`, `C-ROUTE-DEPENDENCY`) and
`references/surfaces/F-fees-treasury.md` (`F-FEES`, `F-BASIS-VS-BUCKET`). Mechanics below come from the
Uniswap v3-core and v3-periphery sources and hold for the canonical contracts; every address, init code
hash, fee-tier list and chain list is chain-specific and lives in the verify-at-use table. A "v3-style"
fork shares the interface but not necessarily the constants, so nothing in this file is asserted for a
fork until the fork's own factory reproduces it.

## When it applies (detection from the target packet)

Detect from code and receipts, never from a label. All reads at P1 unless stated.

1. A `candidate_pools[]` address whose runtime contains the pool selectors: `slot0()` `0x3850c7bd`,
   `liquidity()` `0x1a686502`, `token0()` `0x0dfe1681`, `token1()` `0xd21220a7`, `fee()` `0xddca3f43`,
   `tickSpacing()` `0xd0c93a7c`, `factory()` `0xc45a0155`, `positions(bytes32)` `0x514ea4bf`,
   `ticks(int24)` `0xf30dba93`. Confirm with
   `python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E<n>-pool-code.hex --json`.
2. Read `factory()` on the pool, then on that factory either read `getPool(token0,token1,fee)`
   (`0x1698ee82`; must return the pool) or fetch the `PoolCreated` log whose `pool` field is the address.
   The `PoolCreated` receipt is the identity of the pool; the derivation in the next section is a
   cross-check.
3. Runtime hashes: a v3 pool embeds `factory`, `token0`, `token1`, `fee`, `tickSpacing` and
   `maxLiquidityPerTick` as immutables, so every pool of one factory has a DIFFERENT runtime code hash
   while sharing one creation code (that is why a single `POOL_INIT_CODE_HASH` serves all pools). Dedupe
   pools by masking the immutable offsets, or by the factory's `PoolCreated` set, not by raw code hash.
4. Fork or canonical: identical interface, different factory. Record the factory code hash, `owner()`
   of the factory, and `feeAmountTickSpacing(uint24)` (`0x22afcccb`) for each tier; treat the deployment
   as "v3-style" until its factory reproduces a `PoolCreated` address with a known init code hash.
5. NFPM: a contract with `positions(uint256)` `0x99fbab88` plus ERC-721 selectors (`ownerOf(uint256)`
   `0x6352211e`, `getApproved(uint256)` `0x081812fc`, `isApprovedForAll(address,address)` `0xe985e9c5`)
   whose `factory()` equals the pool's factory. NFT `name()`/`symbol()` are `token_metadata`, corroboration
   only.

## Objects to resolve

| Object | Scope role | How it is resolved (evidence type) |
|---|---|---|
| Factory | `factory` | `factory()` on the pool at P1 (`rpc_state`); `PoolCreated` receipt emitted by it (`log_decoded`) |
| Pool | `pool` | exact address from `PoolCreated`/`getPool`; `token0`, `token1`, `fee`, `tickSpacing` read at P1 |
| Which side is the target | - | `token0() == target` or `token1() == target`; drives the sell direction below |
| NFPM | `position_manager` | `IncreaseLiquidity(uint256,uint128,uint256,uint256)` emitters whose `positions(tokenId)` decode to this pool; `factory()` read |
| Position ids | `position_nft` (holder address) | NFPM `Transfer` and `IncreaseLiquidity` logs; pool `Mint` logs where `owner` = NFPM (tickLower/tickUpper match) |
| Direct positions | `holder`/`locker`/`launch_platform` | pool `Mint` logs where `owner` != NFPM; key `keccak256(abi.encodePacked(owner, tickLower, tickUpper))` |
| Owners, approvals, operators | `owner`/`locker`/`other` | `ownerOf`, `getApproved`, `isApprovedForAll` at P1; `Approval`/`ApprovalForAll` logs |
| Locker | `locker` | `ownerOf(tokenId)` at P1 is a contract; its admin surface per surface B step 7 |
| Quoter / router | `quoter`/`router` | `factory()` on the quoter equals the pool factory; router = `to` of proven historical sells |
| Factory owner | `owner` | `owner()` on the factory (protocol-fee authority) |

## Reads and decodes (exact signatures)

Functions (selectors recomputable with `ddcore.selector(sig)`; verify the signature against the deployed
ABI or a receipt before relying on it):

| Call | Selector | Returns / layout |
|---|---|---|
| `slot0()` | `0x3850c7bd` | 7 words: `sqrtPriceX96` uint160, `tick` int24 (sign-extend the low 24 bits), `observationIndex` uint16, `observationCardinality` uint16, `observationCardinalityNext` uint16, `feeProtocol` uint8, `unlocked` bool |
| `liquidity()` | `0x1a686502` | uint128 = liquidity IN RANGE at the current tick only |
| `positions(bytes32 key)` (pool) | `0x514ea4bf` | 5 words: `liquidity` uint128, `feeGrowthInside0LastX128`, `feeGrowthInside1LastX128`, `tokensOwed0` uint128, `tokensOwed1` uint128 |
| `ticks(int24)` | `0xf30dba93` | tick data incl. `liquidityGross`, `liquidityNet` (needed to walk depth across ranges) |
| `protocolFees()` | `0x1ad8b03b` | (uint128 token0, uint128 token1) accrued protocol fees, uncollected |
| `feeGrowthGlobal0X128()` | `0xf3058399` | uint256 (fee accrual, token0) |
| `getPool(address,address,uint24)` (factory) | `0x1698ee82` | pool address, both token orders stored |
| `feeAmountTickSpacing(uint24)` (factory) | `0x22afcccb` | tick spacing for the tier; 0 = tier not enabled |
| `positions(uint256 tokenId)` (NFPM) | `0x99fbab88` | 12 words: `nonce` uint96, `operator` address, `token0`, `token1`, `fee` uint24, `tickLower` int24, `tickUpper` int24, `liquidity` uint128, `feeGrowthInside0LastX128`, `feeGrowthInside1LastX128`, `tokensOwed0` uint128, `tokensOwed1` uint128 |
| `ownerOf(uint256)` / `getApproved(uint256)` / `isApprovedForAll(address,address)` | `0x6352211e` / `0x081812fc` / `0xe985e9c5` | ERC-721; in the canonical NFPM `getApproved(tokenId)` returns `positions(tokenId).operator` |
| `decreaseLiquidity((uint256,uint128,uint256,uint256,uint256))` | `0x0c49ccbe` | (tokenId, liquidity, amount0Min, amount1Min, deadline) -> credits `tokensOwed`, transfers nothing |
| `collect((uint256,address,uint128,uint128))` | `0xfc6f7865` | (tokenId, recipient, amount0Max, amount1Max) -> transfers owed amounts to `recipient` |
| `burn(uint256)` (NFPM) | `0x42966c68` | requires liquidity == 0 and tokensOwed == 0 ("Not cleared") |
| `burn(int24,int24,uint128)` / `collect(address,int24,int24,uint128,uint128)` (pool) | `0xa34123a7` / `0x4f1eb3d8` | direct-position removal by the pool-level owner |
| `quoteExactInputSingle((address,address,uint256,uint24,uint160))` (QuoterV2) | `0xc6a5026a` | (amountOut, sqrtPriceX96After, initializedTicksCrossed, gasEstimate); note `amountIn` precedes `fee` |
| `quoteExactInputSingle(address,address,uint24,uint256,uint160)` (Quoter v1) | `0xf7729d43` | amountOut only; positional arguments, different order |
| `quoteExactInput(bytes,uint256)` | `0xcdca1753` | multi-hop; `path` = tokenIn(20) ++ fee(3) ++ tokenOut(20) [++ fee ++ token ...] |

Events (topic0 = `ddcore.keccak256_hex(b"<signature>")`; confirm against one real receipt on the target
chain before filtering with it):

| Event | topic0 | Indexed |
|---|---|---|
| `PoolCreated(address,address,uint24,int24,address)` | `0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118` | token0, token1, fee; data: tickSpacing, pool |
| `Swap(address,address,int256,int256,uint160,uint128,int24)` | `0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67` | sender, recipient; amounts positive = into the pool, negative = out |
| `Mint(address,address,int24,int24,uint128,uint256,uint256)` | `0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde` | owner, tickLower, tickUpper; data: sender, amount, amount0, amount1 |
| `Burn(address,int24,int24,uint128,uint256,uint256)` | `0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c` | owner, tickLower, tickUpper |
| `Collect(address,address,int24,int24,uint128,uint128)` (pool) | `0x70935338e69775456a85ddef226c395fb668b63fa0115f5f20610b388e6ca9c0` | owner, tickLower, tickUpper; data: recipient, amounts |
| `IncreaseLiquidity(uint256,uint128,uint256,uint256)` (NFPM) | `0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f` | tokenId |
| `DecreaseLiquidity(uint256,uint128,uint256,uint256)` (NFPM) | `0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4` | tokenId |
| `Collect(uint256,address,uint256,uint256)` (NFPM) | `0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01` | tokenId; data: recipient |
| `Transfer(address,address,uint256)` / `Approval(address,address,uint256)` / `ApprovalForAll(address,address,bool)` | `0xddf252ad...b3ef` / `0x8c5be1e5...b925` / `0x17307eab...6c31` | ERC-721 custody and approval history on the NFPM |
| `SetFeeProtocol(uint8,uint8,uint8,uint8)` (pool) | `0x973d8d92bb299f4af6ce49b52a8adb85ae46b9f214c4c4fc06ac77401237b133` | protocol fee changes |

Price from state (`references/surfaces/C-sellability.md` spot definition): `price(token1 per token0, raw
units) = (sqrtPriceX96 / 2^96)^2 = 1.0001^tick`; human units multiply by `10^(decimals0 - decimals1)`.
Bounds: `MIN_TICK = -887272`, `MAX_TICK = 887272`; tick 0 <=> `sqrtPriceX96 = 2^96 =
79228162514264337593543950336`. Convert with:
```
python3 <skill-root>/scripts/pool_math.py v3-sqrtprice-to-price --sqrt-price-x96 <slot0.sqrtPriceX96> --decimals0 D0 --decimals1 D1
python3 <skill-root>/scripts/pool_math.py v3-tick-to-price --tick <tick> --decimals0 D0 --decimals1 D1
```
State which side the target is; a price quoted "per target token" when the target is token1 is the inverse.

## Pool address derivation and verification

Why: an address derived from published constants that reproduces the `PoolCreated` address proves the
factory, the token pair, the fee tier and the init code hash all at once; a mismatch means one of them is
wrong for this chain (a fork, a zk-style chain with different creation semantics, or a wrong token).

- Salt: `keccak256(abi.encode(token0, token1, fee))` - three padded 32-byte words, tokens sorted so
  `token0 < token1` numerically. v2-style pairs use `keccak256(abi.encodePacked(token0, token1))` (40
  bytes, no fee); mixing the two is the most common derivation error.
- Address: `keccak256(0xff ++ factory ++ salt ++ POOL_INIT_CODE_HASH)[12:]`.
```
python3 <skill-root>/scripts/pool_math.py v3-pool --factory 0x<factory> --token-a 0x<target> --token-b 0x<quote> --fee 3000 [--init-code-hash 0x<hash>]
```
The tool prints the init code hash it used and warns that it must be verified against a `PoolCreated`
event on the target chain. Procedure: (1) take the pool address from the `PoolCreated` log or `getPool`;
(2) run the derivation; (3) equal -> record `B-V3-POOL-DERIVATION` pass with both artifacts; unequal ->
the hash or factory is not the one for this deployment; keep the event-derived address as canonical and
record the mismatch as a finding about the deployment's constants, not about the token.

A deterministic self-check vector for the tool (not a finding about any token) is in the verify-at-use
table below; run it once per install to prove the derivation code before trusting a mismatch.

## Custody and removal paths

Principal leaves a v3 position only through `decreaseLiquidity` (NFPM) or `burn` (pool), followed by
`collect`. Resolve who can execute each today.

1. NFPM positions. For each token id: `ownerOf`, `getApproved`, and `isApprovedForAll(owner, X)` for every
   `X` seen as operator in `ApprovalForAll` logs with this owner (from NFPM deployment to P1; declare the
   range). The canonical NFPM gates `decreaseLiquidity`, `collect` and `burn` with
   `_isApprovedOrOwner(msg.sender, tokenId)`: owner, single-token approved address, or operator. Any of
   the three is an executable removal path (decrease -> collect to any `recipient`).
2. Custody transfer paths: `transferFrom`/`safeTransferFrom` (`0x23b872dd`/`0x42842e0e`) move the NFT;
   `approve(address,uint256)` `0x095ea7b3` and `setApprovalForAll(address,bool)` `0xa22cb465` create
   paths; `permit(address,uint256,uint256,uint8,bytes32,bytes32)` `0x7ac2ff7b` lets the OWNER KEY approve a
   spender with an offline signature (contract owners via EIP-1271 in the canonical periphery - verify in
   the deployed version). An EOA owner is therefore always a current path; a contract owner is a path
   exactly when its code can call these functions (surface B step 7 selector scan).
3. Direct positions (no NFT): pool `Mint` with `owner` != NFPM. The pool-level owner removes by
   `burn(tickLower,tickUpper,amount)` then `collect(recipient,...)`; only that contract's own code decides
   who can trigger it. Read `positions(key)` on the pool at P1 with
   `key = keccak256(abi.encodePacked(owner, tickLower(int24), tickUpper(int24)))` (20 + 3 + 3 bytes).
   `B-DIRECT-MINT` covers enumeration; the owner contract's admin surface decides `B-PRINCIPAL`.
4. Lockers. `ownerOf(tokenId)` at P1 is a contract: read its lock record (id, beneficiary, unlock time vs
   P1 `timestamp_unix`), scan selectors for `decreaseLiquidity`/`collect` forwarding, `withdraw`,
   `unlock`, `rescue`/`recover`, `execute`/arbitrary call, `migrate`, `transferLock`, and proxy slots
   (`B-LOCKER-AUTH`). Two v3-specific traps: (a) a beneficiary `collect` right moves fees, not principal,
   BUT a locker that also forwards `decreaseLiquidity` to the beneficiary is a full removal path (decrease
   credits `tokensOwed`, collect drains them); (b) `getApproved(tokenId)` may be non-zero or the locker may
   have an operator set BEFORE it received the NFT - approvals are cleared on transfer in ERC-721, but
   operators (`isApprovedForAll(locker, X)`) persist and are set by the locker's own code.
5. Write the path statement per position: `holder/approved/operator -> function -> effect -> earliest
   time`. "No current executable removal path found at the pinned block" requires every id resolved,
   every approval and operator read, the locker admin surface read, and no unlock before P1.

## In-range vs out-of-range liquidity and exit depth

Why: `liquidity()` is the liquidity active at the current tick only, and a position whose range does not
contain the current tick holds one asset entirely; summing `liquidity` across positions with different
ranges says nothing about how much quote asset a seller can actually receive.

- Position composition, with `sP = sqrtPriceX96/2^96`, `sA = sqrt(1.0001^tickLower)`,
  `sB = sqrt(1.0001^tickUpper)`, `L = liquidity`:
  in range (`sA <= sP < sB`): `amount0 = L*(sB - sP)/(sP*sB)`, `amount1 = L*(sP - sA)`;
  below range (`sP <= sA`): all token0, `amount0 = L*(sB - sA)/(sA*sB)`;
  above range (`sP >= sB`): all token1, `amount1 = L*(sB - sA)`.
- Sell direction: selling the target when it is `token0` is `zeroForOne` (price and tick fall) and
  consumes `token1` held by positions at or below the current tick; when the target is `token1` the sale
  is `oneForZero` (tick rises) and consumes `token0` held by positions at or above the current tick.
  Exit depth = quote asset available in that direction, position by position, in tick order.
- A one-sided launch position (target only, from an initial tick up to `MAX_TICK`) holds NO quote asset
  until buyers have pushed the price into the range; before that, sells have no depth from it.
- `B-RANGE` records, per canonical position: tickLower, tickUpper, current tick, in/out of range, and the
  direction in which it provides depth. Hand the per-position composition to surface C as the basis for
  the size ladder; note that any tick crossing changes it (stale condition).

## Sellability and quote method (C-QUOTE at P1)

1. Use QuoterV2 when deployed on the chain; encode the struct as five static words
   (`tokenIn`, `tokenOut`, `amountIn`, `fee`, `sqrtPriceLimitX96 = 0`) after selector `0xc6a5026a`; call
   `eth_call` with `{"to": quoter, "data": ...}` at the P1 block hex (never `latest`, never a
   transaction: the quoter is non-view and computes by reverting internally). Decode four words:
   `amountOut`, `sqrtPriceX96After`, `initializedTicksCrossed`, `gasEstimate`. Quoter v1 takes
   positional arguments in a different order (`0xf7729d43`) and returns `amountOut` only.
   `rpc_probe.py --call` accepts raw calldata, so build it once and reuse it per size:
   ```
   python3 - <<'EOF'
   import sys; sys.path.append("<skill-root>/scripts")
   from ddcore import encode_static
   data = "0xc6a5026a" + encode_static([("address","0x<target>"),("address","0x<quote>"),("uint",10**18),("uint",3000),("uint",0)]).hex()
   print(data)
   EOF
   python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<quoterV2> --chain-id N --block <P1 block> --call "<data>" --cache rpc-cache.json --out packet-C-quote-small.json
   ```
   Save the call object and raw return as the `C-QUOTE` artifact for each size.
2. Revert handling: a revert of the `eth_call` itself carries a reason string (`SPL` = price limit hit,
   `Unexpected error` = the pool reverted with non-string data, custom pool errors on forks); a zero
   `amountOut` with zero liquidity in the sell direction is a depth result, not an error. Classify per
   surface C step 7.
3. Cross-checks: (a) spot from `slot0` via `v3-sqrtprice-to-price` must bracket the small-size per-unit
   output; (b) `initializedTicksCrossed` should agree with the position map from `B-RANGE`; (c) the
   quoter's `factory()` (`0xc45a0155`) must equal the pool's factory, otherwise it quotes a different
   deployment; record the read as `C-V3-QUOTER-BASIS`.
4. Fee-on-transfer targets: the quoter assumes the full `amountIn` reaches the pool; quote the NET input
   (`C-TAX-NET`) and label the gross-to-quote rate.
5. Multi-hop: `quoteExactInput(bytes path, uint256 amountIn)` with the packed path; every hop's pool is
   a `C-ROUTE-DEPENDENCY` row with its `B-PRINCIPAL` status.
6. Historical sells (`C-HIST-SELL`): a pool `Swap` where the target-side amount is positive (into the
   pool) and the quote-side amount is negative, plus the token `Transfer` into the pool and the quote
   leaving to a non-pool recipient (a WETH-style `Withdrawal(address,uint256)`
   `0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65` followed by a native value
   transfer needs a `trace`).

## Fee mechanics (F-FEES, F-BASIS-VS-BUCKET)

- LP fee: `fee / 1e6` of the input amount per swap (an immutable of the pool; 500 = 0.05%). Tier list and
  tick spacing are FACTORY state: read `feeAmountTickSpacing(fee)` at P1 for each tier you rely on; the
  canonical constructor enables 500/10, 3000/60, 10000/200 and the 100/1 tier was added later by
  governance on some chains - never assume it exists (verify-at-use table).
- Protocol fee: `slot0.feeProtocol` packs two 4-bit values (token0 in the low nibble, token1 in the high
  nibble); each is 0 (off) or 4..10 meaning `1/n` OF THE SWAP FEE for that token is diverted, so the
  protocol take on gross volume is `(fee/1e6)/n`. State both bases. The factory owner sets it
  (`setFeeProtocol`, event `SetFeeProtocol`) and collects with `collectProtocol` (factory owner only);
  `protocolFees()` shows what is accrued and uncollected.
- LP fee accrual: fees accumulate in `feeGrowthGlobal*`; a position's `tokensOwed` is updated only when it
  is touched (`burn` with zero amount, "poke", or a decrease). NFPM `collect` moves owed amounts to
  `recipient`, emitting NFPM `Collect(tokenId, recipient, amount0, amount1)` and pool
  `Collect(owner=NFPM, recipient, ...)`. Fee inflows to a wallet reconcile from these receipts
  (`F-TREASURY`, `T-POOLHIST-FEE-COLLECTIONS`); collecting fees is not principal removal (keep the two
  propositions apart) but the fee recipient is a `fee_recipient` scope address.
- Realized rate: sum swap inputs x `fee/1e6` over the range and compare with collected fees plus growth
  (`F-REALIZED-RATE`); differences come from range (out-of-range positions earn nothing) and protocol take.

## Common mistakes

- Deriving with the v2 salt (packed, no fee) or an init code hash from another chain, then "correcting"
  the event-derived address to match; the event wins.
- Reporting `liquidity()` or a position's `liquidity` as depth; depth is the quote-side amount in the sell
  direction (formulas above).
- Deduplicating pools by runtime code hash without masking immutables (every pool differs), or treating
  a different pool hash as "a modified fork".
- Reading `positions(tokenId)` and skipping `getApproved`/`isApprovedForAll`; `operator` in the struct is
  the single-token approval, operators-for-all live in a separate mapping.
- Calling a locker "no fee, no withdrawal" from its name; only the selector scan and the lock record say
  what it can do.
- Quoting at `latest` or via `eth_estimateGas`/a transaction; quoting gross input for a taxed token.
- Using QuoterV2's struct order (`amountIn` before `fee`) with the v1 selector or vice versa.
- Counting protocol fee as a share of gross volume, or LP fee as a share of the fee bucket.
- Treating "position NFT sent to 0x..dEaD" as provable destruction: conventional burn, no known key.

## Verify-at-use table

Every value here is chain-specific and can be superseded; research values are from the Uniswap docs
repository and `sdk-core` (Ethereum mainnet, read 2026-09), confidence high, flagged verify-at-use.

| Item | Research value (Ethereum mainnet) | Verify at use time by |
|---|---|---|
| UniswapV3Factory | `0x1F98431c8aD98523631AE4a59f267346ea31F984` | `factory()` on a pool found via the target's `Transfer`/`Swap` logs; a `PoolCreated` receipt emitted by that address; `eth_chainId` first |
| NonfungiblePositionManager | `0xC36442b4a4522E871399CD717aBDD847Ab11FE88` | `factory()` on the NFPM equals the factory; an `IncreaseLiquidity` receipt; pool `Mint.owner` |
| QuoterV2 / Quoter (v1) | `0x61fFE014bA17989E743c5F6cB21bF9697530B21e` / `0xb27308f9F90D607463bb33eA1BeBb41C27CE5AB6` | `factory()` on the quoter equals the factory; a small-size quote agrees with `slot0` spot |
| SwapRouter / SwapRouter02 / UniversalRouter | `0xE592427A0AEce92De3Edee1F18E0157C05861564` / `0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45` / `0x66a9893cc07d91d95644aedd05d03f95e1dba8af` | `to` of proven historical sells; routers are route dependencies, never identity proofs |
| POOL_INIT_CODE_HASH | `0xe34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54` | `pool_math.py v3-pool` reproduces a `PoolCreated` address on THIS chain; forks and zk-style chains differ |
| Fee tiers / tick spacing | 100/1 (governance-added), 500/10, 3000/60, 10000/200 | `feeAmountTickSpacing(fee)` at P1 on the factory; `FeeAmountEnabled(uint24,int24)` logs (`0xc66a3fdf07232cdd185febcc6579d408c241b47ae2f9907d84be655141eeaecc`) |
| Protocol fee "about 1/6 at launch" (docs statement) | not an on-chain value | `slot0.feeProtocol` per pool at P1; `SetFeeProtocol` logs |
| Chains with official v3 deployments | docs list per-chain pages (Ethereum, Arbitrum, Optimism, Polygon, Base, BNB, Avalanche, Celo, zkSync, Zora, World Chain, Unichain, X Layer, Monad, MegaETH, Tempo, Robinhood Chain) | the deployments page at use time, then the on-chain checks above; a listing is discovery, not evidence |
| v3 derivation self-check vector | factory `0x1F98431c8aD98523631AE4a59f267346ea31F984`, token0 `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, token1 `0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2`, fee 500, hash above -> salt `0x08374668a423750b443f65d645c5693995d43722b42cd84f7eeba28b008a40a2`, pool `0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640` (research: high) | `pool_math.py v3-pool` reproduces it; confirm the pool against a `PoolCreated` receipt on Ethereum mainnet at use time; a tool that fails this vector is broken, not the chain |
| v2-style pair derivation self-check | factory `0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f`, init code hash `0x96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f` | `pool_math.py v2-pair` reproduces a `PairCreated` (`0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9`) address on this chain |

## Checks to add (in addition to the surface B/C/F checks)

| check_id | surface | Proposition | Minimum evidence | Stale condition |
|---|---|---|---|---|
| B-V3-POOL-DERIVATION | canonical_lp_principal_custody | CREATE2 derivation from factory + init code hash reproduces the `PoolCreated` address, or the mismatch is explained | `PoolCreated` log + `pool_math.py` output (`log_decoded`, `manual_note`) | never (deterministic); re-run if the factory changes |
| B-V3-NFT-AUTHORITY | canonical_lp_principal_custody | For each canonical token id, owner, single approval and every operator are read at P1 and each is classified | `ownerOf`/`getApproved`/`isApprovedForAll` reads + `ApprovalForAll` log range (`rpc_state`, `log_decoded`) | any `Transfer`/`Approval`/`ApprovalForAll` on the id or owner |
| B-V3-EXIT-DIRECTION | sellability_exit_depth | Quote-side amounts per position in the target's sell direction are computed from `slot0` and position ranges | `slot0` + `positions` + `ticks` reads at P1 (`rpc_state`) | tick crossing; liquidity change |
| B-V3-LOCKER-FORWARDING | canonical_lp_principal_custody | The locker does not forward `decreaseLiquidity` (only `collect`) to any beneficiary, or the forwarding path and its caller are stated | locker selector scan + lock record (`bytecode`, `rpc_state`) | locker upgrade or lock record change |
| C-V3-QUOTER-BASIS | sellability_exit_depth | The quoter used references the pool's factory and was called at P1 with a preserved call object | `factory()` on the quoter + call artifacts (`rpc_state`) | quoter replaced; re-pin |
| F-V3-PROTOCOL-FEE | admin_treasury_reward_custody | `feeProtocol` per canonical pool is decoded and the factory owner (collector) is resolved | `slot0` decode + factory `owner()` (`rpc_state`) | `SetFeeProtocol`; factory ownership change |

## Helper commands

```
# derive and cross-check the pool address (prints the init code hash used + verification warning)
python3 <skill-root>/scripts/pool_math.py v3-pool --factory 0x<factory> --token-a 0x<target> --token-b 0x<quote> --fee 3000
python3 <skill-root>/scripts/pool_math.py v3-pool --factory 0x<factory> --token-a 0x<target> --token-b 0x<quote> --fee 3000 --init-code-hash 0x<hash from a verified PoolCreated>

# pinned pool reads (packet per contract; probes are recorded with raw return data)
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<pool> --chain-id N --block <P1 block> \
    --call "slot0()" --call "liquidity()" --call "token0()" --call "token1()" --call "fee()" --call "tickSpacing()" --call "factory()" --call "protocolFees()" \
    --cache rpc-cache.json --out packet-B-pool.json

# position, owner and approvals on the NFPM
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<nfpm> --chain-id N --block <P1 block> \
    --call "positions(uint256):<tokenId>" --call "ownerOf(uint256):<tokenId>" --call "getApproved(uint256):<tokenId>" \
    --call "isApprovedForAll(address,address):0x<owner>,0x<operator>" --out packet-B-nfpm.json

# factory tier and owner
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<factory> --chain-id N --block <P1 block> \
    --call "feeAmountTickSpacing(uint24):3000" --call "owner()" --call "getPool(address,address,uint24):0x<target>,0x<quote>,3000" --out packet-B-factory.json

# price conversions
python3 <skill-root>/scripts/pool_math.py v3-sqrtprice-to-price --sqrt-price-x96 <sqrtPriceX96> --decimals0 18 --decimals1 6
python3 <skill-root>/scripts/pool_math.py v3-tick-to-price --tick <tick> --decimals0 18 --decimals1 6

# locker and pool runtime scans (presence != reachability; absence != safety)
python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E<n>-locker-code.hex --json
```
Direct pool positions are readable with `--call "positions(bytes32):0x<key>"` on the pool. Struct arguments
(the QuoterV2 call) are not expressible with `--call`'s static argument syntax; pass raw calldata
(`0x<selector><words>`) built with `ddcore.encode_static` as shown above.
