# Platform: Uniswap v4 (PoolManager singleton, hooks, PoolId)

Read this when a pool lives inside a v4 PoolManager: the target's `Transfer` logs show a PoolManager as
counterparty, an `Initialize` log names the target as `currency0` or `currency1`, a v4 PositionManager
NFT holds the liquidity, or a launch platform graduated into a v4 pool. It extends
`references/surfaces/B-liquidity-custody.md` (`B-CANON`, `B-PRINCIPAL`, `B-HOOK-AUTH`,
`B-OPERATOR-APPROVALS`, `B-RANGE`, `B-DIRECT-MINT`), `references/surfaces/C-sellability.md` (`C-QUOTE`,
`C-ROUTE-DEPENDENCY`) and `references/surfaces/F-fees-treasury.md` (`F-FEES`, `F-BASIS-VS-BUCKET`).
Mechanics come from the v4-core and v4-periphery sources; the hook-flag layout, storage-slot constants and
action codes are VERSION-specific and every address is CHAIN-specific - both live in the verify-at-use
table with the procedure that confirms them against the deployed contracts.

## The rule that governs every v4 finding

Record in every evidence row and finding about a v4 pool: chain id, PoolManager address, `currency0`,
`currency1`, `fee` (and whether it is the dynamic-fee flag), `tickSpacing`, `hooks`, the derived PoolId,
and for positions the PositionManager address plus token id (or the direct owner plus salt). Why: a v4
pool has no address; the same pair can exist as many pools that differ only in fee, tick spacing or hook,
and a finding that omits one component cannot be reproduced or bound to the pool it is about. `B-CANON`
is `pass` only with all components and the PoolId matched to an `Initialize` log.

## When it applies (detection from the target packet)

1. A counterparty in the target's `Transfer` logs whose runtime carries PoolManager selectors:
   `extsload(bytes32)` `0x1e2eaeaf`, `extsload(bytes32,uint256)` `0x35fd631a`, `extsload(bytes32[])`
   `0xdbd035ff`, ERC-6909 `balanceOf(address,uint256)` `0x00fdd58e`, `isOperator(address,address)`
   `0xb6363cf2`, `protocolFeeController()` `0xf02de3b2`, `unlockCallback`-driven `unlock(bytes)`. Confirm
   with `python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E<n>-pm-code.hex --json`.
2. `Initialize` logs on that address with the target as topic2 (`currency0`) or topic3 (`currency1`):
   two `eth_getLogs` queries per PoolManager over the declared range.
3. Native-currency pools: `currency0 == address(0)`; the quote leg leaves no ERC-20 `Transfer` log, so
   sells and quotes are visible only through PoolManager events, traces and native balance deltas.
4. PositionManager: `poolManager()` `0xdc4c90d3` returns the PoolManager; `getPoolAndPositionInfo(uint256)`
   `0x7ba03aad`, `positionInfo(uint256)` `0x89097a6a`, `nextTokenId()` `0x75794a3c` present; ERC-721
   `Transfer` logs with the target's pool key in `getPoolAndPositionInfo`.
5. StateView: `poolManager()` returns the same PoolManager and `getSlot0(bytes32)` `0xc815641c` is present.
   A StateView bound to a different PoolManager reads a different deployment.
6. Version: hash the emitter's runtime (`eth_getCode` at P1, keccak256 = `runtime.code_hash`) and compare
   it with the hash you record at the pin from the address the Uniswap deployments page lists for this
   chain (verify-at-use row "PoolManager runtime code hash"; research captured no hash). Different bytes
   = a fork or another version, and every constant below then needs re-verification.

## PoolKey, currency ordering, native currency and PoolId

- `PoolKey` = `(address currency0, address currency1, uint24 fee, int24 tickSpacing, address hooks)`;
  ABI tuple `(address,address,uint24,int24,address)`, five 32-byte words (0xa0 bytes), `tickSpacing`
  sign-extended (bounds `MIN_TICK_SPACING = 1`, `MAX_TICK_SPACING = 32767`, so it is always positive).
- Ordering: `currency0 < currency1` as unsigned integers; `initialize` reverts otherwise
  (`CurrenciesOutOfOrderOrEqual`). The native currency is `address(0)`, so it is always `currency0`
  when paired with any ERC-20, and "native" means the CHAIN's gas token
  (`references/chains/chain-verification.md`), not necessarily ETH.
- `fee`: LP fee in pips of the input (`1_000_000` = 100%; `MAX_LP_FEE = 1_000_000`), OR exactly
  `0x800000` (8388608) = `DYNAMIC_FEE_FLAG` (equality test, not a bit test) meaning the hook sets the fee;
  a dynamic-fee key requires a non-zero hook. `OVERRIDE_FEE_FLAG = 0x400000` appears only on fees a hook
  returns per swap, never in a key.
- `PoolId = keccak256(abi.encode(PoolKey))` over the 160 bytes:
  ```
  python3 <skill-root>/scripts/pool_math.py v4-pool-id --currency0 0x<lower> --currency1 0x<higher> --fee 3000 --tick-spacing 60 --hooks 0x<hook or zero address>
  ```
  The output must equal topic1 of the pool's `Initialize` log; a mismatch means a wrong component (most
  often tick spacing, or a hook assumed to be zero).
- A deterministic PoolId self-check vector for the tool is in the verify-at-use table; run it once per
  install so that a later mismatch is attributed to the key, not to the derivation code.

## Why the PoolManager's balance is never one pool's reserves

All pools share one contract (`mapping(PoolId => Pool.State)`), and flash accounting settles token
transfers only when an `unlock` ends. `balanceOf(PoolManager)` for a currency (or its native balance) is
therefore the sum over every pool that uses that currency, plus tokens backing outstanding ERC-6909
claims, plus anything unsettled in flight. Never read it as depth, reserves or "locked liquidity". Per-pool
figures come only from PoolId-scoped state (`getSlot0`, `getLiquidity`, tick data, positions), and those
are liquidity units, not token amounts: convert per position with the range formulas in
`references/platforms/uniswap-v3.md` ("In-range vs out-of-range"), which hold unchanged in v4.

## Reading pool state: StateView or extsload (record which)

StateView (periphery view contract bound to one PoolManager; recommended for RPC reads):

| Call | Selector | Returns |
|---|---|---|
| `getSlot0(bytes32 poolId)` | `0xc815641c` | `sqrtPriceX96` uint160, `tick` int24, `protocolFee` uint24, `lpFee` uint24 (four words; a docs page says uint8 for the fee fields - the source says uint24, trust the ABI) |
| `getLiquidity(bytes32)` | `0xfa6793d5` | uint128 in-range liquidity |
| `getPositionInfo(bytes32,address,int24,int24,bytes32)` | `0xdacf1d2f` | `liquidity` uint128, `feeGrowthInside0LastX128`, `feeGrowthInside1LastX128` |
| `getPositionInfo(bytes32,bytes32 positionId)` | `0x97fd7b42` | same, by position key |
| `getPositionLiquidity(bytes32,bytes32)` | `0xf0928f29` | uint128 |
| `getTickInfo(bytes32,int24)` | `0x7c40f1fe` | `liquidityGross` uint128, `liquidityNet` int128, fee growth outside (for walking depth) |
| `getFeeGrowthGlobals(bytes32)` | `0x9ec538c8` | (uint256, uint256) |

`extsload` on the PoolManager (slot layout from the v4-core `StateLibrary` at research time; verify by
comparing with `getSlot0` for the same PoolId at the same block - disagreement means the deployed version
differs, and then StateView's decoding is the basis to record):
- `stateSlot = keccak256(poolId ++ uint256(POOLS_SLOT))` with `POOLS_SLOT = 6` (64 bytes hashed);
- `Slot0` at `stateSlot`: bits 0..159 `sqrtPriceX96`, 160..183 `tick` (int24), 184..207 `protocolFee`
  (uint24), 208..231 `lpFee` (uint24);
- offsets from `stateSlot`: +1 `feeGrowthGlobal0X128`, +2 `feeGrowthGlobal1X128`, +3 `liquidity`
  (uint128), +4 ticks mapping, +5 tick bitmap, +6 positions mapping.
- A slot self-check vector (derived from the formulas, not confirmed on-chain) is in the verify-at-use
  table.
- Spot price and tick conversion are identical to v3 (`pool_math.py v3-sqrtprice-to-price`,
  `v3-tick-to-price`), price = currency1 per currency0.

`protocolFee` is a uint24 packing two direction-specific values; decode with the `ProtocolFeeLibrary` of
the deployed v4-core version (verify), and read `protocolFeesAccrued(address currency)` `0x97e8cd4e` on the
PoolManager for what is collectable by the protocol fee controller.

## Positions via the v4 PositionManager

- Token ids are ERC-721 (solmate-style plus `ERC721Permit_v4`): `ownerOf(uint256)` `0x6352211e`,
  `getApproved(uint256)` `0x081812fc` (public mapping), `isApprovedForAll(address,address)` `0xe985e9c5`,
  `approve(address,uint256)` `0x095ea7b3`, `setApprovalForAll(address,bool)` `0xa22cb465`,
  `permit(address,uint256,uint256,uint256,bytes)` `0x0f5730f1` and
  `permitForAll(address,address,bool,uint256,uint256,bytes)` `0x3aea60f0` (signature-based approvals: an
  EOA owner is always a current path).
- `getPoolAndPositionInfo(uint256)` `0x7ba03aad` -> six words: the `PoolKey` (5) then `PositionInfo` (1).
- `positionInfo(uint256)` `0x89097a6a` -> packed uint256: bits 0..7 `hasSubscriber`, bits 8..31
  `tickLower` (int24, sign-extend), bits 32..55 `tickUpper` (int24), bits 56..255 the UPPER 25 bytes of
  the PoolId (`bytes25`). Recover the full key with `poolKeys(bytes25)` `0x86b6be7d`; the argument is
  left-aligned: calldata = selector ++ PoolId[0:25] ++ 7 zero bytes (raw `--call`).
- `getPositionLiquidity(uint256)` `0x1efeed33` -> uint128, read from the PoolManager with owner =
  PositionManager and `salt = bytes32(tokenId)`. Equivalent StateView read:
  `getPositionInfo(poolId, positionManager, tickLower, tickUpper, bytes32(tokenId))`.
- Position key on the PoolManager: `keccak256(abi.encodePacked(owner, tickLower(int24), tickUpper(int24),
  salt))` over 58 bytes.
- Enumerate ids from PositionManager ERC-721 `Transfer` logs (mint = from zero) and from PoolManager
  `ModifyLiquidity` logs filtered by PoolId whose `sender` is the PositionManager (its `salt` field is
  `bytes32(tokenId)`).
- Direct positions (no NFT): any contract that calls `modifyLiquidity` inside its own `unlock` owns a
  position keyed by its address and an arbitrary salt (hooks, lockers, launch executors do this). Enumerate
  `ModifyLiquidity` logs with `sender != PositionManager` (`B-DIRECT-MINT`); the owning contract's code
  alone decides who can remove.

## Custody and removal paths

1. Authority on an NFT position: owner, `getApproved[tokenId]`, or an operator of the owner
   (`onlyIfApproved` -> `_isApprovedOrOwner`). Read all three at P1 and every `ApprovalForAll` log for the
   owner over the declared range (`B-OPERATOR-APPROVALS`).
2. The removal call is `modifyLiquidities(bytes unlockData, uint256 deadline)` `0xdd46508f` with
   `unlockData = abi.encode(bytes actions, bytes[] params)`; `actions` is a packed byte string of action
   codes and `params[i]` is the ABI encoding for `actions[i]`. Codes at research time (verify against the
   deployed `Actions` library; a periphery upgrade can renumber them): `INCREASE_LIQUIDITY 0x00`,
   `DECREASE_LIQUIDITY 0x01` (params `(uint256 tokenId, uint256 liquidity, uint128 amount0Min, uint128
   amount1Min, bytes hookData)`), `MINT_POSITION 0x02`, `BURN_POSITION 0x03` (params `(uint256 tokenId,
   uint128 amount0Min, uint128 amount1Min, bytes hookData)`; removes all liquidity, clears the info and
   burns the NFT), `SETTLE_PAIR 0x0d`, `TAKE 0x0e`, `TAKE_ALL 0x0f`, `TAKE_PORTION 0x10`, `TAKE_PAIR 0x11`
   (params `(address currency0, address currency1, address recipient)`), `CLOSE_CURRENCY 0x12`,
   `SWEEP 0x14`, `MINT_6909 0x17`, `BURN_6909 0x18`. A decrease or burn followed by a take with an
   arbitrary `recipient` is the principal-removal path; decode historical removals from calldata
   (`calldata` evidence, action bytes named). `modifyLiquiditiesWithoutUnlock(bytes,bytes[])` `0x4afe393c`
   is the same path for callers already inside an unlock.
3. ERC-6909 claims are a removal terminus: liquidity can be removed into claim balances (`MINT_6909`, or
   `PoolManager.mint`) so the tokens stay inside the PoolManager while being owed to the claim holder.
   Read `balanceOf(holder, uint256(uint160(currency)))` `0x00fdd58e` for every custodian and launch wallet
   and include claims when reconciling where liquidity went (`T-POOLHIST-*`). Claim events (verify the
   signatures against the deployed ABI): `Transfer(address,address,address,uint256,uint256)` topic0
   `0x1b3d7edb2e9c0b0e7c525b20aaaef0f5940d2ed71663c7d39266ecafac728859` (caller, from, to, id, amount),
   `OperatorSet(address,address,bool)` `0xceb576d9f15e4e200fdb5096d64d5dfd667e16def20c1eefd14256d8e3faa267`.
4. Lockers holding the NFT: surface B step 7 (`B-LOCKER-AUTH`) plus a scan for `modifyLiquidities`
   (`0xdd46508f`, `0x4afe393c`), ERC-721 transfer/approve selectors, DELEGATECALL and arbitrary call in
   the locker runtime; read `isApprovedForAll(locker, X)` for every operator seen.
5. Hooks are custody authorities when removal-related bits are set (next section).
6. Write the path statement per position exactly as in the v3 reference; "no current executable removal
   path found at the pinned block" additionally requires the hook's removal bits and owner resolved.

## Hook authority (B-HOOK-AUTH, A-style findings about the hook)

Permission bits live in the hook ADDRESS: `hasPermission(flag) = uint160(hooks) & flag != 0`;
`permissions = int(hooks, 16) & 0x3fff`. Layout from v4-core `Hooks.sol` at research time (verify against
the v4-core version the PoolManager was built from and record the mapping used):

| Bit | Mask | Permission | Diligence meaning |
|---|---|---|---|
| 13 | `0x2000` | beforeInitialize | can gate pool creation |
| 12 | `0x1000` | afterInitialize | - |
| 11 | `0x800` | beforeAddLiquidity | can gate who adds liquidity |
| 10 | `0x400` | afterAddLiquidity | - |
| 9 | `0x200` | beforeRemoveLiquidity | CAN REVERT REMOVALS (blocks LPs and lockers) |
| 8 | `0x100` | afterRemoveLiquidity | can act on removals |
| 7 | `0x80` | beforeSwap | CAN REVERT OR REROUTE SWAPS (selective sell blocking) |
| 6 | `0x40` | afterSwap | can act after swaps (hook fees) |
| 5 | `0x20` | beforeDonate | - |
| 4 | `0x10` | afterDonate | - |
| 3 | `0x8` | beforeSwapReturnsDelta | can take amounts from the swap input |
| 2 | `0x4` | afterSwapReturnsDelta | can take amounts from the swap output |
| 1 | `0x2` | afterAddLiquidityReturnsDelta | can take amounts on add |
| 0 | `0x1` | afterRemoveLiquidityReturnsDelta | CAN TAKE AMOUNTS ON REMOVAL |

Example from the official deployment guide: an address ending `...C0` (low bits `1100 0000`) has bits 7
and 6 = beforeSwap and afterSwap. `hooks == address(0)` means no hook and is valid only for a non-dynamic
fee. `getHookPermissions()` `0xc4e833ce` (BaseHook) is corroboration; the PoolManager enforces the address
bits.

Procedure:
1. Decode the bits and list the swap-affecting and removal-affecting ones (`B-V4-HOOK-PERMISSIONS`).
2. Treat the hook as an authority: apply the surface A procedure to the hook contract (`owner()`, roles,
   EIP-1967 slots, setters, allow/deny lists, pause, fee setters, DELEGATECALL) - `B-V4-HOOK-ADMIN`. The
   hook owner is a custody authority when bits 9/8/0 are set and a sellability authority when bits 7/3/2
   are set (`C-ROUTE-DEPENDENCY`); an upgradeable hook is whatever its admin makes it.
3. Dynamic fees: key `fee == 0x800000` -> read `lpFee` from `getSlot0` at P1 (what the hook last set) and
   find the hook's fee logic and ceiling; the protocol cap is `MAX_LP_FEE` (100%), so "the hook can set
   the fee" is a finding until the hook's own bound is read from its runtime (`F-FEES`).
4. Hook addresses are mined for CREATE2 (deterministic deployer); the address encodes permissions, never
   identity or provenance.

## Events filtered by PoolId

`eth_getLogs` with `address = PoolManager`, `topics = [topic0, poolId]`, bounded block windows
(`references/chains/chain-verification.md` for window limits). topic0 values recomputed from the source
signatures - confirm each with one receipt on the target chain:

| Event | topic0 | Notes |
|---|---|---|
| `Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)` | `0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438` | topics: id, currency0, currency1; data: fee, tickSpacing, hooks, sqrtPriceX96, tick |
| `ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)` | `0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec` | topics: id, sender; negative `liquidityDelta` = removal; `salt` = `bytes32(tokenId)` for PositionManager positions |
| `Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)` | `0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f` | topics: id, sender; data: amount0, amount1, sqrtPriceX96, liquidity, tick, fee (the fee actually charged; differs from the key for dynamic pools) |
| `Donate(bytes32,address,uint256,uint256)` | `0x29ef05caaff9404b7cb6d1c0e9bbae9eaa7ab2541feba1a9c4248594c08156cb` | fee injection into in-range positions |

`sender` is the contract that called the PoolManager (PositionManager, a router, a hook), not the end
user; recover the user from the transaction `from` and the router's own calldata. The sign convention of
`amount0`/`amount1` in `Swap` is NOT v3's: decode one known receipt (token `Transfer`/settle legs and the
trader's balance delta) before classifying any swap as a sell, and record the convention used. Discovery
of all pools for the target: `Initialize` with the target as topic2, then as topic3.

## Quoting with the v4 quoter (C-QUOTE)

`V4Quoter.quoteExactInputSingle((PoolKey,bool zeroForOne,uint128 exactAmount,bytes hookData))`
`0xaa9d21cb` -> `(uint256 amountOut, uint256 gasEstimate)`. It is non-view and works by reverting inside
`unlock` (`QuoteSwap(uint256)` `0xecbd9804` caught internally), so call it ONLY with `eth_call` at the P1
block hex. `zeroForOne = true` when the target is `currency0`. Encoding (single dynamic tuple argument):
```
python3 - <<'EOF'
import sys; sys.path.append("<skill-root>/scripts")
from ddcore import encode_static
words = encode_static([("uint", 0x20),                      # offset to the tuple
    ("address","0x<currency0>"), ("address","0x<currency1>"), ("uint", 3000), ("int", 60), ("address","0x<hooks>"),
    ("bool", True), ("uint", 10**18), ("uint", 0x100),      # zeroForOne, exactAmount, offset of hookData within the tuple
    ("uint", 0)])                                           # hookData length 0 (append padded bytes if a hook needs data)
print("0xaa9d21cb" + words.hex())
EOF
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<v4quoter> --chain-id N --block <P1 block> --call "<data>" --out packet-C-quote-v4-small.json
```
Rules: record the `hookData` used (usually empty; some hooks require data and revert otherwise);
`UnexpectedRevertBytes(bytes)` wraps a pool or hook revert - decode the inner reason and classify (hook
revert is a finding class in surface C step 7); native output is in the chain's native units; cross-check
against `getSlot0` spot and `getLiquidity`; a hook with bits 7/3/2 makes every quote conditional on the
hook's state and owner (`C-ROUTE-DEPENDENCY`). Historical sells: `Swap` by PoolId plus receipt-level legs
(`C-HIST-SELL`); for native quote a `trace` or balance delta is required.

## Fee mechanics (F-FEES, F-BASIS-VS-BUCKET)

- LP fee: `fee` pips of the input for static pools; `lpFee` from `getSlot0` for dynamic pools (hook-set,
  and hooks may override per swap with `OVERRIDE_FEE_FLAG`). The `Swap` event's `fee` field records what
  was charged.
- Protocol fee: `protocolFee` in `getSlot0`, set through the protocol fee controller (an owner-governed
  path on the PoolManager); accrued per currency in `protocolFeesAccrued`. The order in which protocol and
  LP fees are applied is version-specific: measure it from one swap receipt or derive from the deployed
  v4-core version before stating a basis.
- Hook takes: returns-delta hooks (bits 3/2/1/0) remove amounts from swaps or liquidity actions; the
  hook's accounting (pending fees, recipients, sweep functions) is a fee route with its own claim
  authority (`F-CLAIM-AUTH`); its recipients are `fee_recipient` scope addresses. Distinguish a hook take
  on gross swap value from a share of an LP-fee bucket.
- Donations (`Donate`) credit in-range positions directly; a launch platform "buyback" that donates is a
  fee flow to LPs, not a burn.

## Common mistakes

- Using `balanceOf(PoolManager)` or its native balance as reserves, depth or "locked liquidity".
- Identifying a pool by pair only; omitting tick spacing or assuming `hooks = 0`, then failing to
  reproduce the PoolId.
- Ordering currencies by "token then quote" instead of numerically; forgetting that native is `currency0`.
- Reading `sender` as the trader or LP.
- Carrying v3's `Swap` sign convention into v4 without checking a receipt.
- Ignoring ERC-6909 claims when a custodian's token balance "disappeared".
- Trusting `getHookPermissions()` over the address bits, or reading a hook's owner but not its proxy slots.
- Applying `StateLibrary` slot constants or `Actions` codes from a different periphery/core version.
- Quoting with empty `hookData` against a hook that requires data and reporting the revert as a honeypot.
- Treating a `PositionManager` NFT in a locker as locked without reading the hook's removal bits and the
  locker's `modifyLiquidities` reachability.

## Verify-at-use table

Research values are from the Uniswap docs repository v4 deployments page and `sdk-core` (Ethereum mainnet,
read 2026-09-05; the page itself warns that addresses differ across chains); confidence high unless noted;
all verify-at-use.

| Item | Research value (Ethereum mainnet) | Verify at use time by |
|---|---|---|
| PoolManager | `0x000000000004444c5dc75cB358380D2e3dE08A90` | emitter of an `Initialize` receipt that names the target; `poolManager()` on the PositionManager and StateView; `eth_chainId` first |
| PoolManager runtime code hash | not captured by research | `eth_getCode` at P1 on the `Initialize` emitter and on the address the deployments page lists for this chain; record both keccak256 hashes as `bytecode` evidence; equal = same build, unequal = fork or other version (constants below re-verified) |
| PositionManager | `0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e` | `poolManager()` read; `ModifyLiquidity.sender` of an NFT position's mint receipt |
| StateView | `0x7ffe42c4a5deea5b0fec41c94c136cf115597227` | `poolManager()` read; `getSlot0` agrees with `extsload` at the same block |
| V4Quoter | `0x52f0e24d1c21c8a0cb1e5a5dd6198556bd9e1203` | `poolManager()` read; a small quote agrees with `getSlot0` spot |
| UniversalRouter / Permit2 | `0x66a9893cc07d91d95644aedd05d03f95e1dba8af` / `0x000000000022D473030F116dDEE9F6B43aC78BA3` | `to` of proven historical sells; route dependencies only |
| PoolId derivation self-check vector | `currency0 = 0x0000000000000000000000000000000000000000`, `currency1 = 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48`, fee 500, tickSpacing 10, hooks zero -> PoolId `0x21c67e77068de97969ba93d4aab21826d33ca12bb9f565d8496e8fda8a82ca27` (research: high, community-confirmed rather than Uniswap-published) | `pool_math.py v4-pool-id` reproduces it; confirm against an `Initialize` receipt on Ethereum mainnet at use time |
| State-slot self-check vector | for that PoolId with `POOLS_SLOT = 6`: `stateSlot = 0xda8cac368d67cd2f2d8aaa5cc531768e0fa3b1d205c5c5de60da078e1f59bdfc`, liquidity slot `0xda8cac368d67cd2f2d8aaa5cc531768e0fa3b1d205c5c5de60da078e1f59bdff` (medium; formula-derived, not confirmed on-chain) | `extsload(stateSlot)` decodes to the same values as StateView `getSlot0` at the same block |
| Hook permission bit layout | table above (v4-core `Hooks.sol`) | compare with the `Hooks` library of the v4-core version the PoolManager was built from; record the mapping |
| `StateLibrary` slot constants | `POOLS_SLOT = 6`, offsets +1/+2/+3/+4/+5/+6 | `extsload` result equals StateView `getSlot0`/`getLiquidity` at the same block |
| `Actions` codes | table above (v4-periphery `Actions.sol`) | decode a known `modifyLiquidities` receipt and match its effects |
| PoolManager deployment block (Ethereum) | 21688329 (community indexer config; low) | `eth_getCode` at block-1 empty and at block non-empty, or the creation receipt |
| Chains with official v4 deployments | Ethereum, Unichain, Optimism, Base, Arbitrum One, Polygon, Zora, Worldchain, X Layer, Ink, Soneium, Avalanche, BNB Smart Chain, Celo, Monad, MegaETH, Tempo, Robinhood Chain, plus testnets (as of 2026-09-05) | the deployments page at use time, then the on-chain checks above; a listing is discovery only |

## Checks to add (in addition to surface B/C/F checks)

| check_id | surface | Proposition | Minimum evidence | Stale condition |
|---|---|---|---|---|
| B-V4-POOLKEY | canonical_lp_principal_custody | The full pool key and derived PoolId are recorded and reproduce the `Initialize` topic | `Initialize` log + `pool_math.py v4-pool-id` output (`log_decoded`, `manual_note`) | never for the key; re-run on PoolManager change |
| B-V4-STATE-BASIS | canonical_lp_principal_custody | Pool state was read PoolId-scoped (StateView or `extsload`) with the basis recorded, never from PoolManager balances | `getSlot0`/`getLiquidity` artifacts at P1 (`rpc_state`, `rpc_storage`) | re-pin |
| B-V4-HOOK-PERMISSIONS | canonical_lp_principal_custody | The hook's permission bits are decoded from its address and removal/swap-affecting bits listed | address decode + mapping version (`manual_note`, `bytecode`) | hook address change (new pool) |
| B-V4-HOOK-ADMIN | canonical_lp_principal_custody | The hook's owner, upgradeability and setters are resolved as authorities | `owner()`, proxy slots, selector scan (`rpc_state`, `rpc_storage`, `bytecode`) | hook ownership change or upgrade |
| B-V4-CLAIMS | canonical_lp_principal_custody | ERC-6909 claim balances of custodians and launch wallets are read for both currencies | `balanceOf(address,uint256)` reads at P1 (`rpc_state`) | claim transfer/burn |
| B-V4-DIRECT-POSITIONS | canonical_lp_principal_custody | `ModifyLiquidity` senders other than the PositionManager are enumerated with their owning contracts | log range + owner contract reads (`log_decoded`, `rpc_state`) | new `ModifyLiquidity` |
| C-V4-QUOTE-HOOKDATA | sellability_exit_depth | Quotes at P1 record `hookData`, `zeroForOne`, the key, and any hook revert is classified | call artifacts per size (`rpc_state`) | re-pin; hook state change |
| F-V4-FEE-BASIS | admin_treasury_reward_custody | LP fee (static or dynamic), protocol fee and hook takes are stated with their bases and recipients | `getSlot0` + hook policy reads (`rpc_state`) | fee update; hook policy change |

## Helper commands

```
# PoolId from the key (must equal Initialize topic1)
python3 <skill-root>/scripts/pool_math.py v4-pool-id --currency0 0x0000000000000000000000000000000000000000 --currency1 0x<target> --fee 3000 --tick-spacing 60 --hooks 0x<hook>

# pool state through StateView at P1
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<stateview> --chain-id N --block <P1 block> \
    --call "poolManager()" --call "getSlot0(bytes32):0x<poolId>" --call "getLiquidity(bytes32):0x<poolId>" \
    --call "getPositionInfo(bytes32,address,int24,int24,bytes32):0x<poolId>,0x<positionManager>,<tickLower>,<tickUpper>,0x<bytes32 tokenId>" \
    --cache rpc-cache.json --out packet-B-v4-state.json

# the same Slot0 word straight from the PoolManager (basis cross-check)
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<poolmanager> --chain-id N --block <P1 block> \
    --call "extsload(bytes32):0x<stateSlot>" --call "balanceOf(address,uint256):0x<custodian>,<uint160 currency as integer>" --out packet-B-v4-pm.json

# NFT position: key, packed info, liquidity, custody
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<positionManager> --chain-id N --block <P1 block> \
    --call "getPoolAndPositionInfo(uint256):<tokenId>" --call "getPositionLiquidity(uint256):<tokenId>" \
    --call "ownerOf(uint256):<tokenId>" --call "getApproved(uint256):<tokenId>" --call "isApprovedForAll(address,address):0x<owner>,0x<operator>" \
    --call "0x86b6be7d<PoolId first 25 bytes><14 zero hex chars>" --out packet-B-v4-position.json

# hook and locker runtime scans
python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E<n>-hook-code.hex --json
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<hook> --chain-id N --block <P1 block> --call "owner()" --call "getHookPermissions()" --out packet-B-v4-hook.json
```
