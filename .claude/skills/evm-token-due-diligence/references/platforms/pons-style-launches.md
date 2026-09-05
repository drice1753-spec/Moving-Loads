# Platform: Pons-style bonding-curve launches

Read this when the target was created by a launch platform that mints the whole supply into a curve (or
a one-sided DEX position), sells it along a bonding curve with fees and taxes, and "graduates" into a DEX
pool whose position is then locked, burned, or held by someone. The named example is Pons, which the
research (2026-09, from the published repository `ponsdotdev/ponsfamily` and secondary coverage)
identifies as a launchpad deployed on Robinhood Chain in two generations: V1 (CREATE2 token, one-sided
Uniswap v3 position held by a locker, optional atomic router buy) and V2 (constant-product curve with a
phantom quote reserve, graduating into a permanently locked full-range Uniswap v4 pool with a singleton
afterSwap fee hook). Everything Pons-specific below is "per the published source at research time" and
must be re-verified against explorer-verified source and deployed bytecode: the research found that the
committed factory calls curve functions the committed curve does not define, so the repository cannot be
the verified source for at least one deployed contract. The pattern rules apply to any pump-style
launchpad on any chain.

Extends `references/surfaces/E-launch-integrity.md` (`E-LAUNCH`, `E-FACTORY-VERSION`, `E-EXEMPTIONS`,
`E-DIRECT-BUY-RECIPIENT`, `E-COHORT`, `E-EARLY-SALES`, `E-DETERMINISTIC`, `E-SEQUENCE`),
`references/surfaces/B-liquidity-custody.md` (`B-CANON`, `B-PRINCIPAL`, `B-LOCKER-AUTH`, `B-HOOK-AUTH`),
`references/surfaces/C-sellability.md`, `references/surfaces/F-fees-treasury.md` (`F-FEES`,
`F-BASIS-VS-BUCKET`, `F-CLAIM-AUTH`), `references/surfaces/D-supply-concentration.md` (`D-BUCKETS`) and
`references/deep-tracks/launch-cohort.md`. Pool mechanics: `references/platforms/uniswap-v3.md` (V1-style)
and `references/platforms/uniswap-v4.md` (V2-style).

## When it applies (detection from the target packet)

1. The deployment receipt's `to` is a contract (factory), not a plain CREATE from an EOA, and the target's
   first `Transfer` log mints 100% of supply to a CONTRACT (curve, factory, or position manager) - never to
   an EOA. Verify by receipt (`E-LAUNCH` step 1), then record the factory as `launch_platform`.
2. The launch receipt carries a launch event naming token, curve and deployer. Published Pons signatures
   (topic0 recomputed with `ddcore.keccak256_hex`; confirm against the receipt):
   V2 `TokenLaunched(address,address,address,address,uint256,uint256)`
   `0x8d4aad4953d0ca700d468f3753aa14432d1b35b43ec6409f051fb6aa43a89607` (indexed token, curve, deployer;
   data pairToken, launchConfigId, graduationThreshold);
   V1 `TokenLaunched(address,address,address,address,address,uint256,uint256,uint256,uint256,uint256)`
   `0xdb51ea9ad51ab453a65a4cb7e60c3cb378c9501bb002609f8f97778fb6c4235a` (token, deployer, dexFactory,
   pairToken, pool, dexId, launchConfigId, positionId, restrictionsEndBlock, initialBuyAmount) and
   `TokenDeployed(address,address,address,address,uint256,uint256)`
   `0x1461370115e1c2be79cb529f8cfcbd11316e789d9c6099fc83417b0b4c48c62a`.
3. A per-launch curve contract whose runtime has `buy(uint256,uint256,address)` `0x59a87bc1`,
   `sell(uint256,uint256,address)` `0xd04c6983` (note the RECIPIENT argument), `readyToGraduate()`
   `0xc68360a5`, `graduated()` `0xe7c2b772` (`selector_scan.py`).
4. A deterministic token address (CREATE2 through the factory or a deployer helper; V1 forces a vanity
   suffix) - `E-DETERMINISTIC`.
5. Phase at P1, which decides where the principal is: curve phase (curve holds tokens and quote,
   `graduated() == false`), swept phase (factory holds reserves after `graduate` but before pool
   creation), graduated (pool exists; position NFT in a locker or elsewhere).

## Lifecycle: where the evidence lives

| Phase | What happens (pattern; Pons V2 per source) | Evidence | Surfaces |
|---|---|---|---|
| Creation tx | factory deploys token + curve (CREATE2), mints supply to the curve, records params, takes the launch fee; optional atomic first buy through a forwarder | receipt, calldata, `TokenLaunched`, first `CurveBuy` | E, D |
| Curve phase | `buy`/`sell` against a constant-product formula on `(phantomQuote + trackedQuote - pending fees, trackedTokens)`; fee and creator tax on the quote leg; snipe tax in a launch window; `reservedTokens` never sold | `CurveBuy`/`CurveSell` logs, curve reads | E, C, F |
| Threshold | `readyToGraduate()` when `sellableTokens() == 0` (real quote reserve = threshold); sells revert from here | curve reads at the crossing block | C |
| Graduation step 1 | `graduate(token)` (anyone; auto-attempted on the crossing buy, failures swallowed) sweeps fees, halts trading, moves reserves to the factory (`LaunchSwept`) | factory receipt | B, F |
| Graduation step 2 | `createGraduatedPool(token)` (anyone, retryable): seeds a v4 pool with the hook at the terminal price, mints a full-range position, sends the NFT to the locker, locks excess tokens permanently (`PoolGraduated`, `GraduationTokensPermanentlyLocked`) | factory receipt, `Initialize`, `ModifyLiquidity`, NFT `Transfer` | B, D |
| Post-graduation | hook takes `hookFeeBps + creatorTaxBps` per swap; `sweepPoolFees` converts and distributes; owner rescue windows if stuck | `Swap` by PoolId, `HookFeeCollected`, `FeesSwept` | C, F |

V1 pattern: full supply minted at launch into a one-sided v3 position from `initialTick` to the max
tick, NFT to a configurable locker with fee redirect to the creator wallet, same-block anti-snipe plus
`maxWalletBps`/`maxTxBps` for `restrictionBlocks`, graduation status derived from the locked principal.

## Objects to resolve

Read them from the factory and the launch record, never from a website. Getter names are per the
published source; a deployed factory may differ - fall back to a selector scan and structural decoding.

| Object | Scope role | Resolve by |
|---|---|---|
| Factory (exact version) | `launch_platform` | `to` of the launch tx; code hash at launch block and P1 |
| Token, curve | `token`, `curve` | `TokenLaunched` topics; `getLaunchedToken(address)` `0x3cf28b5a` |
| Locker, hook, buyback vault, fee escrow, forwarder, deployer helper, graduation executor | `locker`, `hook`, `vault`, `treasury`, `router`, `other` | factory getters `locker()` `0xd7b96d4e`, `memeHook()` `0x6651812c`, `buybackVault()` `0xf1f5c993`, `feeEscrow()` `0xc4b7de97`, `launchForwarder()` `0x9b924452`, `launchDeployer()` `0x858f5964`, `graduationExecutor()` `0xcc6d7a39` |
| DEX infrastructure | `pool_manager`, `position_manager` | `poolManager()` `0xdc4c90d3`, `positionManager()` `0x791b98bc`, `permit2()` `0x12261ee7`; then `Initialize`/`PoolGraduated` receipts |
| Quote (pair) asset | `other` | `TokenLaunched.pairToken` (zero = native); `pairToken()` `0x3de35b79` on the curve |
| Pool / position | `pool` (PoolId) / `position_nft` | `PoolGraduated.positionId`; v4 key components from `Initialize` |
| Fee parties | `fee_recipient`, `treasury` | `creatorFeeRecipient()` `0x9fa36cdc`, `originalDeployer()` `0x81cf58a9` (curve); `protocolFeeRecipient()` `0x64df049e`, `feeSweepOperator()` `0x8a36a6bb` (hook) |
| Platform owner | `owner` | `owner()`/`pendingOwner()` `0xe30c3978` on factory, hook, locker, vault |

## Reads and decodes

Launch calldata (dynamic tuples: decode offsets, then words; `calldata` evidence per parameter; selectors
below are recomputed from the published signatures and must match the launch transaction's first four
bytes - a different selector means a different deployed version):
- V2 `launchToken((string,string,string,string,(string,string,string,string,string),address,uint16,bool,bytes32,bytes32),uint256,address)`
  `0xf35abbcf`; with exemptions `launchToken(...,address[])` `0xa72101af`; forwarder-only
  `launchTokenFor(...,uint256,address,address,address[])` `0xd6a0eef5`. TokenParams fields after the
  strings: `creatorFeeRecipient` (zero = original deployer), `creatorTaxBps` (uint16), `buybackEnabled`,
  `expectedEconomics` (zero waives the economics pin), `salt`. Then `launchConfigId`, `pairToken`.
- V1 `launchToken((string,string,string,string,(string,string,string,string,string),address),uint256,uint256,bytes32)`
  `0x686399cb`: `feeWallet`, `launchConfigId`, `dexId`, `salt`; `msg.value - launchFee` = initial buy.
- Launch record `getLaunchedToken(token)` and config `getLaunchConfig(uint256)` `0x1cad862d` (supply,
  curveFeeBps, phantomQuote, graduationThreshold, poolFee, tickSpacing): the record is the launch's frozen
  snapshot, the config is owner-mutable; decode both with the deployed ABI.
- Curve reads at P1 and at historical blocks: `feeBps()` `0x24a9d853`, `creatorTaxBps()` `0xc1bb8901`,
  `phantomQuote()` `0xc57eadfc`, `reservedTokens()` `0x15a55347`, `sellableTokens()` `0x808bcddc`,
  `realQuoteReserve()` `0x4f1f58fd`, `trackedQuote()` `0xca52b0b7`, `trackedTokens()` `0x4c37ef23`,
  `graduationThreshold()` `0x8b0bc501`, `protocolFeeShareBps()` `0x9040f866`, `buybackBurnBps()`
  `0x49127e2a`, `readyToGraduate()`, `graduated()`.
- Hook policy: `hookFeeBps()` `0xea26abcf`, `protocolFeeShareBps()`, `buybackBurnBps()`,
  `maxInternalPriceImpactBps()` `0x90addc1e`, `currentFeePolicy()` `0x89a69bd8`, `owner()`.
- Factory parameters: `launchFee()` `0xcf3cf573`, `maxCreatorTaxBps()` `0xf325a5fb`, `snipeTaxStartBps()`
  `0x50e25ac2`, `snipeTaxSeconds()` `0x6783774b`, `launchEnabled()` `0x236a4afb`.
- Events (topic0 from the published signatures; confirm each against a deployed receipt):
  `CurveBuy(address,address,uint256,uint256,uint256,uint256)`
  `0xec36bf571f136799e8dc0b0b8bea4b04d8bd3d43de838aab0d5fc21d4cbfc455` (indexed buyer = msg.sender,
  recipient; data quoteIn, tokensOut, fee, tax); `CurveSell(...)` same layout
  `0x8113d738abdcb6b38357e9d53a54a7157861a09031b453651f0fe7fe151f59df` (seller, recipient of the quote);
  `CurveBuyRefunded(address,uint256)` `0xa69e8258ccc7b9bbb70ab953fc2d1062b4ee28b8ca827534097e1732e87b0262`;
  `FeesSwept(uint256,uint256,uint256)` `0x9f4cd7c4ed99d08a797804560c9c5d71d2cf7e101f2e3b5e7d1ca8a24c370e4f`
  (protocol, buyback, creator); `LaunchSwept(address,uint256,uint256)`
  `0xcdb72f157fd3666758a6ce201387ffb52038c7562e4fff352828da1096c4b6b4`;
  `PoolGraduated(address,uint256,uint256,uint256)`
  `0x0a44ef75df69c534f43cd6c1aa3ef8983065fe5fe79ef9e79f6494e6f258c259`;
  `GraduationTokensPermanentlyLocked(address,uint256)`
  `0xa0a18f5bf205becee8b268d7cf69addab8548ae8ef361791464cf0e0e17c1361`;
  `LaunchGraduationRescued(address,address,uint256,uint256)`
  `0x7017304fdd491394686dce984eac721f0be1a22228346210f16694772bde44ca`;
  `AutoGraduationFailed(address,uint256)` `0xe2cd2f31ebc05ec28640102987f4c8fc5f20e269e1b3aa82577f3f2f0e35c7c6`;
  `HookFeeCollected(bytes32,address,uint256,uint256)`
  `0xc532c43b3423e14ef72748f1c8291238829ca0af8ba9b67975ad1483485a4b4d`.

## The direct-curve-buy-recipient and tax-treatment rule (E-DIRECT-BUY-RECIPIENT)

Why: `buy(quoteIn, minTokensOut, recipient)` delivers tokens to `recipient`, refunds any unfilled quote to
`msg.sender`, and logs `buyer = msg.sender`; in V2 the creator's atomic launch-and-buy runs through a
separate forwarder that calls `launchTokenFor(..., originalDeployer = its caller, exemptions)` and then
`buy(..., recipient)` with an explicit recipient that is auto-exempted from the snipe tax; in V1 the initial
buy (`msg.value - launchFee`) is routed through the DEX router to `feeWallet` if set, else to `msg.sender`,
with a temporary whitelist so the same-block rule does not block it. The transaction sender therefore
proves nothing about who received the tokens or what tax applied. Never assume the sender got the benefit.

1. Find the first buys: `CurveBuy` in the launch receipt (forwarder path) or the first `CurveBuy` logs on
   the curve; V1: the `Swap` and token `Transfer` in the launch receipt.
2. Decode the recipient three ways and require agreement: `CurveBuy` topic2, the buy calldata's third word
   (or the forwarder call's `recipient` word), and the token `Transfer.to`. Compare with the tx `from`,
   `TokenLaunched.deployer`, `originalDeployer()`, `creatorFeeRecipient()`. Record each address with a
   neutral role: `launch_signer` (tx from), `deployer` (launch record), `fee_recipient`, `holder`
   (recipient). Different addresses are a fact to record, not a conclusion about identity.
3. Tax treatment: from the `CurveBuy` fields compute `fee/quoteIn` and `tax/quoteIn`; compare with
   `feeBps`/`creatorTaxBps` at that block. Snipe tax (V2): for buys inside the launch window the
   deliverable is reduced for non-exempt buyers (the committed source defaults to a 99% start decaying
   over a window measured in seconds; deployed values verify-at-use). Show the differential: compute the
   untaxed expected output from curve state before the buy (`tokensOut = trackedTokens * netQuote /
   (phantomQuote + trackedQuote - pendingFees + netQuote)`, per the published source - verify with one
   ordinary receipt) and compare with `tokensOut` for the exempt recipient and for the next non-exempt
   buy; the part not explained by curve movement is tax. Record exempt/non-exempt status from the
   exemption list at that block.
4. A creator tax paid on the creator's own buy is later claimable by the creator: the net cost differs
   from the gross; write it as an `F-FEES` row, not as "the creator paid tax".
5. Exemptions (`E-EXEMPTIONS`): manual = the `snipeTaxExemptions` array in the launch calldata (up to a
   source cap of 32); automatic = deployer, `creatorFeeRecipient`, and the forwarder buy's recipient.
   Mark origin per address and read the same status at P1 where a getter exists.
6. Refunds (`CurveBuyRefunded`) go to `msg.sender`, which may not be the recipient; account for them in
   the launch signer's flows.
7. V1: recipient = `feeWallet` or `msg.sender`; `feeWallet` also becomes the LP-fee redirect on the locker,
   so the same address is `holder` and `fee_recipient`; the initial buy is executed with
   `amountOutMinimum = 0` against the just-minted one-sided position.

## Taxes on curve trades, platform fees and splits (F-FEES, F-BASIS-VS-BUCKET)

State every percentage with its base. Per the published V2 source (caps are source constants; live values
verify-at-use):

| Flow | Base | Where configured | Recipient / route |
|---|---|---|---|
| Launch fee | fixed amount; `msg.value` must EQUAL `launchFee()` | factory (owner) | forwarded to the hook's `protocolFeeRecipient` |
| Curve base fee `feeBps` | % of the GROSS quote leg of each buy and sell (cap 10%) | launch config, frozen per launch | split below |
| Creator tax `creatorTaxBps` | % of the GROSS quote leg (cap 10%; base + tax cap 20%) | launcher's calldata, capped by `maxCreatorTaxBps` | 100% to the creator, bypasses the split |
| Protocol share `protocolFeeShareBps` | % of the BASE-FEE BUCKET (cap 50%) | hook policy, frozen per launch | `protocolFeeRecipient` |
| Creator bucket | remainder of the base-fee bucket | - | creator (claim-based via the fee escrow) |
| Buyback slice `buybackBurnBps` | % of the CREATOR BUCKET, only if `buybackEnabled` | hook policy / launcher flag | buys the launched token on its own curve or pool and LOCKS it in the buyback vault with a multi-year linear vest - not burned, per source |
| Post-graduation hook fee `hookFeeBps` | % of the unspecified currency of each swap (cap 10%; plus `creatorTaxBps`, combined cap 20%) | hook policy | pending in the hook until `sweepPoolFees(bytes32,uint256,uint256)` `0x3d61055e` by the creator or the `feeSweepOperator` converts memecoin-denominated fees against the pool's own liquidity (bounded by `maxInternalPriceImpactBps`) and distributes as above |

Rules: payouts are claim-based (`F-CLAIM-AUTH`: who can claim, to which address, whether the destination
is fixed); the internal fee conversion is a platform-initiated sell that consumes the pool's depth (a
`C-ROUTE-DEPENDENCY` note); secondary sources report a 1% fee and a 70/30 creator/protocol split (legacy
90/10) and a "not immutable" off-chain buyback of the platform token from the protocol's share - read the
live values and the launch's frozen snapshot, and separate current configuration, launch snapshot and
realized rates (`F-REALIZED-RATE`). Hook policy setters are owner-only and, per source, affect future
launches only: verify by reading the launch's stored policy at P1 against the hook's current values.

## Creator allocations (E-LAUNCH, D-BUCKETS)

Per source neither Pons version pre-allocates tokens to the creator: V2 mints 100% to the curve, V1
mints 100% into the one-sided position. Do not assume it: read every `Transfer` in the launch receipt; any
transfer from curve/factory/token to a non-platform address is an allocation. Buckets: curve inventory
(sellable), curve reserve (`reservedTokens = supply * phantomQuote / (phantomQuote + graduationThreshold)`,
never sold, destined for the pool and the permanent lock - protocol custody, not creator), first-buy
recipient (holder), permanently locked excess at graduation (`locker`), buyback vault holdings (read the
vault's beneficiary and vesting; `vault`), burn. Creator economics are fee share plus creator tax plus the
optional first buy at curve price.

## Graduation and what happens to liquidity

Condition (V2): `readyToGraduate()` <=> `sellableTokens() == 0` <=> real quote reserve reached the launch's
`graduationThreshold`. Between the crossing and pool creation, `sell` reverts (`C-CURVE-SELL-GATE`): a
holder cannot exit until someone calls `createGraduatedPool`. Seed math per source:
`poolTokenAmount = sweptTokens * sweptQuote / (sweptQuote + phantomQuote)`; the remainder of the reserve is
locked permanently. Worked shape (illustrative numbers only): supply 1e9, phantom 1.8, threshold 4.2 ->
reserve 3e8, sellable 7e8, pool seed 2.1e8 tokens against 4.2 quote, 9e7 tokens locked.

Who controls the liquidity afterwards - test each, do not read the name:

| Outcome | What to read | Check |
|---|---|---|
| NFT in a locker with no removal path (Pons V2 per source: no collect, no withdrawal, no arbitrary call; owner can only `setFactory` once, cannot renounce) | `ownerOf(positionId)` on the PositionManager at P1 = locker; locker runtime scan for `modifyLiquidities` `0xdd46508f`/`0x4afe393c`, transfer/approve/permit selectors, DELEGATECALL; locker `owner()`; proxy slots | `B-GRADUATION-LOCK`, `B-LOCKER-AUTH` |
| NFT burned (sent to a dead address) | `ownerOf` reverts or returns the dead address; conventional, not provable destruction | `B-PRINCIPAL` |
| NFT held by the platform (factory, executor, EOA) | `ownerOf`; the holder's admin surface | `B-PRINCIPAL` (removable) |
| Swept phase (reserves in the factory before pool creation) | launch record phase; factory owner powers `rescueSweptGraduation(address,address)` `0xdbcb9c76` after a source delay of 7 days, `forceSweptGraduation(address)` `0x7aed273e`, `rescueCurveFees(address)` `0x189eb0f5` | `B-CURVE-PHASE-CUSTODY` (principal under platform-owner custody while stuck) |
| Hook authority on the pool | hook address bits (must be afterSwap-only per source; any removal bit set is a finding), hook `owner()` and setters | `B-V4-HOOK-PERMISSIONS`, `B-V4-HOOK-ADMIN` |
| V1: NFT in a configurable locker with fee redirect | v3 reference: `ownerOf`, approvals, locker selectors, `collect` vs `decreaseLiquidity` forwarding | `B-V3-LOCKER-FORWARDING` |

## The exact-factory-version rule (E-FACTORY-VERSION)

Why: several factories coexist (V1, V2, and a deleted repository note named a different "V2 deployment"
address), owner-mutable configs change economics between launches, and the deployed curve differs from
the committed one. A finding decoded with the wrong version's ABI is wrong silently.

1. Factory = `to` of the verified launch tx. Read its runtime at the launch block and at P1; record both
   code hashes and `owner()`. Research addresses are labels to compare against, never identity.
2. Token and curve are per-launch deployments with immutables, so their runtime hashes differ per launch:
   sample N sibling launches from the same factory (`TokenLaunched` logs in a declared range), read their
   token and curve runtimes, mask immutable offsets, and compare. Identical masked hashes = same template
   and version; a different hash = a different version or a non-template deployment (separate finding).
   Do the same for the hook, locker, vault, escrow and forwarder the launch actually used (getters at the
   launch block when archive state allows; otherwise at P1 with the difference noted).
3. Source: fetch explorer-verified source for EACH deployed address (Blockscout on Robinhood Chain per
   research) and establish correspondence (`H-SOURCE-CORRESPONDENCE`) before decoding by name; the GitHub
   repository is `repository` evidence (discovery). Where correspondence fails, decode structurally
   (selector + words) and label the basis `source_unverified`.
4. "Deployed behavior verified" means a receipt exercising the function you rely on (a taxed `CurveBuy`, a
   `LaunchSwept`, a `PoolGraduated`), not the source text. Record one per relied-on behavior
   (`E-PLATFORM-DEPLOYED-BEHAVIOR`).

## Automatic vs manual (E-EXEMPTIONS, E-LAUNCH rows)

| Parameter | Origin | How determined |
|---|---|---|
| supply, curve fee, phantom quote, graduation threshold, pool fee, tick spacing | platform config (owner-set per `launchConfigId`; automatic for the launcher) | `getLaunchConfig(id)` at the launch block vs the launch record; constant across siblings using the same id |
| creator fee recipient, creator tax, buyback flag, economics pin, salt, pair token, config id | launcher (manual) | launch calldata words |
| snipe-tax exemption list | launcher (manual; capped) | calldata `address[]` |
| deployer, creator fee recipient, forwarder recipient exemptions | automatic | factory/forwarder code; identical across siblings |
| first buy amount and recipient | launcher (manual) through the forwarder (V2) or `msg.value`/`feeWallet` (V1) | forwarder calldata; `CurveBuy`; V1 `initialBuyAmount` in `TokenLaunched` |
| snipe-tax start and window, launch fee, max creator tax, launch gating/whitelist | platform owner (mutable) | factory reads at the launch block and P1; setter events |
| V1 `maxWalletBps`, `maxTxBps`, `restrictionBlocks`, `initialTick`, vanity suffix | platform config / code | config reads; siblings |

Never describe a platform default as a launcher choice or vice versa; one row per parameter.

## Cohort definition for launch wallets (E-COHORT, then the deep track)

Write the definition before measuring (manifest `discovery[]` row), specialized for a curve:
```
cohort_id: C1
inclusion_rule: recipients (CurveBuy.recipient) of buys on curve 0x<curve> from the launch block L until
                min(L + N, graduation block G), plus sub-cohort "exempt at launch" = forwarder buy recipient
                + calldata exemption list + automatic exemptions
time_bounds: L .. min(L+N, G); N chosen to cover the snipe-tax window (seconds -> blocks at this chain's
             block time) plus the first K blocks; follow-up to P1 for outflows
sources: CurveBuy/CurveSell logs on the curve; token Transfer logs; receipts; traces for native quote legs
exclusions: curve, factory, locker, hook, buyback vault, fee escrow, forwarder, PoolManager, PositionManager,
            burn addresses (each by address and role)
coverage: windows fetched; limitation ids for gaps
```
Sales during the curve phase are proven by a `CurveSell` receipt (seller, recipient of the quote) with the
token `Transfer` into the curve; a native quote leaving the curve needs a `trace` (or a per-block balance
delta when the recipient has no other activity in that block, stated). Post-graduation sales are v4 `Swap`
logs by PoolId (`references/platforms/uniswap-v4.md`). Sale-then-buy to a new recipient is "market-mediated
redistribution" (`references/attribution.md`).

## Common mistakes

- Attributing the first buy to the launch signer because it signed the transaction; the recipient word and
  the `Transfer.to` decide.
- Calling the creator tax "1%" without saying 1% of gross quote, and the protocol share "30%" without
  saying 30% of the fee bucket.
- Treating `reservedTokens` or the permanently locked excess as creator or insider holdings.
- Reporting the curve-phase price as depth; the curve is one-directional after the threshold (sells revert)
  and its constant-product uses a phantom reserve that is never withdrawable.
- Reading the GitHub repository as the deployed source; decoding parameters by struct names that the
  deployed bytecode may not implement.
- Missing the swept phase: principal sits in the factory under the owner's rescue powers until someone
  creates the pool.
- Assuming "no dev allocation" from the docs; the first-buy recipient and the exemption list are the
  allocation mechanism.
- Counting the buyback vault as burned supply; per source it vests, and the beneficiary must be read.
- Calling the locker immutable because it has no owner function besides `setFactory`, without reading
  whether `setFactory` was already used and whether the hook can still block or take on removals.

## Verify-at-use table

Research values (2026-09-05) from `ponsdotdev/ponsfamily` (README, `contract-meta.json`, sources, git
history) and secondary coverage; the official docs and explorer were not directly fetchable. Nothing here
is asserted; confidence is the research's own.

| Item | Research value | Confidence | Verify at use time by |
|---|---|---|---|
| Chain | Robinhood Chain only, chain id 4663 | high | `eth_chainId`; `references/chains/robinhood-chain.md` |
| V1 factory | `0xA5aAb3F0c6EeadF30Ef1D3Eb997108E976351feB` | high | `to` of the launch tx; code hash; `TokenLaunched` (10-field) receipts emitted by it |
| V2 factory | `0x7eD598BcEf8bd9Edd8C97A195C6d13f40801EC7e` | high | same; `TokenLaunched` (6-field) receipts; getters above |
| Other "V2 deployment" and "Genesis Buyback Wallet" addresses in a deleted repo doc | `0xb6d1bf07...16aba3`, `0x379e850c...9296` | low | treat as unrelated unless a receipt ties them to the target |
| Platform token (PONS) | `0x39dBED3a2bd333467115dE45665cC57F813C4571` | medium | a separate target; never conflate with a launched token; resolve from verified contracts |
| Legacy (V1) locker | `0x31ca5E101941A93A7DD6d0497928700625CF54B5` | medium | `ownerOf(positionId)` on the v3 NFPM at P1 |
| Forwarder, hook, locker, vault, escrow, deployer helper, executor (V2) | not in the repository | - | factory getters at the launch block/P1; receipts |
| Live fee values (1% curve/hook fee; 70/30 creator/protocol, legacy 90/10; 80/20 use of protocol share) | secondary reports | medium | `feeBps`, `hookFeeBps`, `protocolFeeShareBps`, `buybackBurnBps` reads; launch snapshot; `FeesSwept` receipts |
| Launch fee | 0.0005 ETH reported | medium | `launchFee()`; the launch receipt's value |
| Graduation threshold / supply | 4.2 ETH real reserve / 1e9 tokens reported | medium | `TokenLaunched.graduationThreshold`, `getLaunchConfig(id)`, `totalSupply()` |
| Snipe tax | start 99% (source cap 9900 bps); window: committed source default 15 s, cap 60 s, secondary reports ~5 s decay | medium/low | `snipeTaxStartBps()`, `snipeTaxSeconds()` at the launch block; differential from receipts |
| Locker has no withdrawal path; buyback vested 5 years; rescue delay 7 days; creator-recipient override timelock 3 days | committed source | high for the source, unknown for deployed bytecode | selector scan + explorer-verified source correspondence for the deployed addresses |
| Audits | none published found; reviews "in progress" per one secondary source | low | ask for the report and match its scope to deployed code hashes (`H-AUDIT-SCOPE`) |
| Operator | "Pons Labs, LLC"; pseudonymous developer | low | outside scope unless project-control evidence ties it to an address (`references/attribution.md`) |
| Conflation list | PONSCORE, ponspad, PonsVault, hood.fun, Pools.trade (Uniswap Labs), TrustSwap, `ponfamily.com` (not the official docs host) | medium | the factory address in the launch receipt is the only identity of the platform |

## Checks to add

| check_id | surface | Proposition | Minimum evidence | Stale condition |
|---|---|---|---|---|
| E-CURVE-TAX-TREATMENT | historical_launch_integrity | The first buy's fee, tax and snipe-tax status are computed from the receipt and curve state and compared with a contemporaneous ordinary buy | two `CurveBuy` receipts + curve reads at those blocks (`receipt`, `log_decoded`, `rpc_state`) | never (historical) |
| E-PLATFORM-DEPLOYED-BEHAVIOR | historical_launch_integrity | Each platform behavior relied on is shown by a receipt on the deployed contracts, not by repository text | one receipt per behavior (`receipt`) | platform upgrade / new factory |
| E-LAUNCH-CONFIG-SNAPSHOT | historical_launch_integrity | The launch's frozen economics equal the config at the launch block, and differ or not from the live config at P1 | launch record + config reads at both blocks (`rpc_state`) | config update |
| B-CURVE-PHASE-CUSTODY | canonical_lp_principal_custody | Who holds the principal at P1 (curve, factory in swept phase, locker) and which owner powers apply in that phase | phase read + holder balances + owner power selectors (`rpc_state`, `bytecode`) | phase change |
| B-GRADUATION-LOCK | canonical_lp_principal_custody | The graduated position's holder at P1 has no removal path, including hook removal bits and locker forwarding | `ownerOf` + locker scan + hook bits (`rpc_state`, `bytecode`) | NFT transfer; locker or hook change |
| C-CURVE-SELL-GATE | sellability_exit_depth | Whether sells revert at P1 (threshold reached, pool not yet created) or the curve is live | `readyToGraduate`/`graduated` reads + a read-only `sell` `eth_call` (`rpc_state`) | graduation step |
| F-CURVE-FEE-SPLIT | admin_treasury_reward_custody | Every fee percentage is stated with its base (gross leg, fee bucket, creator bucket) and recipient, from the launch snapshot | curve and hook reads + one `FeesSwept` receipt (`rpc_state`, `receipt`) | policy change for new launches |
| F-HOOK-SWEEP-AUTH | admin_treasury_reward_custody | Who can sweep post-graduation fees and how the internal conversion is bounded | hook reads + `sweepPoolFees` receipt (`rpc_state`, `receipt`) | operator change |
| D-CURVE-RESERVE | current_concentration | Curve reserve, locked excess and vault holdings are bucketed as protocol custody with the formula shown | `reservedTokens`, locker and vault balances at P1 (`rpc_state`) | graduation; vesting |
| H-PLATFORM-SOURCE | development_disclosure | Explorer-verified source corresponds to the deployed factory, curve, hook and locker runtimes | compile-and-compare per address (`bytecode`, `source_verified`/`source_unverified`) | redeploy |

## Helper commands

```
# launch record, config and platform parameters at P1 (repeat with --block <launch block> when archive state exists)
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<factory> --chain-id N --block <P1 block> \
    --call "owner()" --call "pendingOwner()" --call "launchFee()" --call "maxCreatorTaxBps()" --call "snipeTaxStartBps()" --call "snipeTaxSeconds()" \
    --call "getLaunchConfig(uint256):<configId>" --call "getLaunchedToken(address):0x<target>" \
    --call "locker()" --call "memeHook()" --call "launchForwarder()" --call "buybackVault()" --call "feeEscrow()" --call "poolManager()" --call "positionManager()" \
    --cache rpc-cache.json --out packet-E-factory.json

# curve state
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<curve> --chain-id N --block <P1 block> \
    --call "feeBps()" --call "creatorTaxBps()" --call "phantomQuote()" --call "reservedTokens()" --call "sellableTokens()" --call "realQuoteReserve()" \
    --call "readyToGraduate()" --call "graduated()" --call "creatorFeeRecipient()" --call "originalDeployer()" --call "pairToken()" --out packet-E-curve.json

# hook policy and post-graduation position custody
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<hook> --chain-id N --block <P1 block> \
    --call "owner()" --call "hookFeeBps()" --call "protocolFeeShareBps()" --call "buybackBurnBps()" --call "protocolFeeRecipient()" --call "feeSweepOperator()" --out packet-F-hook.json
python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<positionManager> --chain-id N --block <P1 block> \
    --call "ownerOf(uint256):<positionId>" --call "getApproved(uint256):<positionId>" --call "getPoolAndPositionInfo(uint256):<positionId>" --out packet-B-position.json

# runtime scans (presence != reachability; absence != safety)
python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E<n>-locker-code.hex --json
python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E<n>-curve-code.hex --json

# PoolId of the graduated pool from the Initialize log components
python3 <skill-root>/scripts/pool_math.py v4-pool-id --currency0 0x<lower> --currency1 0x<higher> --fee <poolFee> --tick-spacing <tickSpacing> --hooks 0x<hook>
```
