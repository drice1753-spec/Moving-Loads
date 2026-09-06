# Behavioral examples (six synthetic scenarios)

Every scenario below is a SYNTHETIC EXAMPLE, not a live finding. Nothing here describes a real token,
chain, address, transaction or person. The examples exist to show the shape of a correct answer, the
ratings it produces, the ledger rows that carry it, and the wrong conclusion the same evidence invites.

Conventions used in all six:
- Placeholders such as `0xSYNTH-TOKEN`, `0xSYNTH-TX-1` are not valid addresses or hashes; a real
  manifest carries EIP-55 addresses and 32-byte hashes (the validator rejects placeholders with
  E-ADDR-MALFORMED / E-PIN-PLACEHOLDER).
- Chain id `31337` is a synthetic stand-in; at use time the chain id is whatever `eth_chainId` returns
  from the endpoint actually queried (verify at use time; see `references/chains/chain-verification.md`).
- `P1` is the primary pin (block number, hash, UTC timestamp, captured header). Evidence bullets are
  tagged with the evidence type (BRIEF 2.4 vocabulary) and whether they are current-state at P1 or
  historical (bound to a tx or block before P1).
- Ledger rows use the 12 columns of `templates/ledger-columns.md`; `artifact_or_query` is abbreviated.
  Row shape follows validator contract v1.1: `pin_or_tx` is exactly one pin id (current state) or one tx
  hash (`is_historical: true`); `artifact_or_query` names evidence ids only (findings and discovery
  records are never evidence; `S<n>` ids go in `coverage`); every finding hangs off a check of its own
  surface (E-FINDING-UNLINKED, E-CHECK-STATUS), and a favourable proposition recorded as a row ("no
  removal path found") hangs off a `finding`-status check with severity `info`, because `pass` checks
  carry evidence ids only. Only the surfaces a scenario turns on are shown; broad mode rates all 11.
- Quantities (percentages, units, block counts) are invented to make the arithmetic visible.

## Scenario 1 - Locked canonical liquidity with removable side liquidity

SYNTHETIC EXAMPLE, not a live finding.

**Situation.** Broad mode. Token `0xSYNTH-TOKEN` on chain 31337. Requirement frame: "rug resistance for
the canonical pool over a 30-day hold". The canonical v3 pool `0xSYNTH-POOL-V3` (fee 3000) is served by
position NFT 4471 on position manager `0xSYNTH-NPM`, owned by locker `0xSYNTH-LOCKER`. The project
website states "100% of liquidity locked for 12 months". A second, v2-style pair exists.

**What the evidence showed.**
- [rpc_state, current-state at P1] `ownerOf(4471)` on `0xSYNTH-NPM` returns `0xSYNTH-LOCKER`;
  `positions(4471)` returns liquidity L1, an in-range tick span, `tokensOwed0/1` small.
- [bytecode, current-state at P1] `selector_scan.py --code-file locker.bin`: no `decreaseLiquidity`,
  `execute(address,bytes)`, `rescue`, `sweep`; no DELEGATECALL/CALLCODE; not EIP-1167.
- [rpc_storage, current-state at P1] the locker's `unlockTime` slot decodes to a timestamp 63 days after
  P1's `timestamp_utc`; the lock owner slot resolves to `0xSYNTH-SIGNER`.
- [source_verified, current-state at P1] the locker source reproduces the runtime code hash; its only
  withdrawal function requires `block.timestamp >= unlockTime` and `msg.sender == lockOwner`.
- [log_decoded, historical] `PairCreated` from factory `0xSYNTH-FACTORY-V2` at block 1,180,400 created
  `0xSYNTH-PAIR-V2` (token / wrapped native), found by discovery record S1 below.
- [rpc_state, current-state at P1] `getReserves()` on `0xSYNTH-PAIR-V2` holds 18% of all pooled token
  inventory (v3 in-range amount computed with `pool_math.py v3-sqrtprice-to-price` + v2 reserves);
  the pair's LP token `balanceOf(0xSYNTH-SIGNER)` is 100% of LP supply less MINIMUM_LIQUIDITY.
- [rpc_state, current-state at P1] `eth_getCode(0xSYNTH-SIGNER)` is empty (`runtime_status: eoa`); the
  LP tokens are not in any locker.
- [website, discovery only] the "100% locked" claim; tested, contradicted for the side pool.

Discovery record (manifest `discovery[]`):
```json
{"discovery_id": "S1", "claim": "All pools for 0xSYNTH-TOKEN in the declared universe were found.",
 "search_universe": "PoolCreated logs of 0xSYNTH-FACTORY-V3 and PairCreated logs of 0xSYNTH-FACTORY-V2 (E6, E7)",
 "block_range": "1180000-1250000", "pagination": "eth_getLogs in 10000-block windows, no failed windows",
 "inclusion_rule": "either token of the pair/pool equals 0xSYNTH-TOKEN", "exclusions": null,
 "materiality_threshold": "none for discovery; inventory reported for every pool found",
 "coverage": "full for the two factories; other DEX factories not searched"}
```

**Correct ratings.**

| Surface | Rating | Likelihood | Confidence | Coverage | Note |
|---|---|---|---|---|---|
| canonical_lp_principal_custody | low | low | high | full | B-CANON pass; B-PRINCIPAL finding (F1, info: no removal path found at P1); valid until `unlockTime` or a new position |
| side_pool_removal_risk | high | medium | high | partial | B-SIDE finding (F2, high); S1 covers two factories only |
| sellability_exit_depth | medium | medium | high | full | C-ROUTE-DEPENDENCY finding (F4, medium; row omitted): 18% of tested depth is removable in one transaction |
| development_disclosure | medium | medium | high | partial | H-DISCLOSURE finding (F3, medium): the "100% locked" claim is contradicted for the side pool; other H checks not shown |

**Correct bounded verdict text.** "No current executable removal path found at the pinned block for the
canonical position (NFT 4471 in `0xSYNTH-LOCKER`, unlock 63 days after P1; F1). A side pool
`0xSYNTH-PAIR-V2` holding 18% of pooled token inventory has its LP tokens held by an EOA that can remove
them in one transaction (F2, S1). GO-WITH-CONDITIONS under rug resistance for the canonical pool over 30
days: re-pin and re-test B-PRINCIPAL before the unlock; NO-GO under 'all liquidity locked'."

**Sample ledger rows.**

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | At P1, position 4471 on 0xSYNTH-NPM is owned by 0xSYNTH-LOCKER, whose runtime has no principal-removal path before its `unlockTime` (P1 + 63 days) and none callable by anyone but 0xSYNTH-SIGNER after it. | 31337 (synthetic; verify at use time) | 0xSYNTH-LOCKER | P1 | E1 eth_call ownerOf(4471); E2 eth_call positions(4471); E3 eth_getCode locker; E4 eth_getStorageAt unlockTime slot; all at 0x<P1 block hex> | NPM ABI; selector walk (selector_scan.py); storage layout from source_verified locker | rpc_state | proven | A hidden path via fallback: excluded by source correspondence (E5). NFT approval/operator to a third party: `getApproved(4471)` and `isApprovedForAll` read zero (E1). | Current state at P1; `Transfer`/`Approval` history of NFT 4471 replayed from mint to P1 (no limitation). | NFT transfer, approval, `IncreaseLiquidity`/`DecreaseLiquidity`, reaching `unlockTime`, reorg of P1. |
| F2 | At P1, 0xSYNTH-PAIR-V2 holds 18% of all pooled 0xSYNTH-TOKEN inventory and 100% of its LP tokens (less MINIMUM_LIQUIDITY) are held by the EOA 0xSYNTH-SIGNER, which can call `burn` via the router in one transaction. | 31337 (synthetic; verify at use time) | 0xSYNTH-PAIR-V2 | P1 | E6 eth_getLogs PairCreated; E8 eth_call getReserves(); E9 eth_call balanceOf(0xSYNTH-SIGNER) on the pair; E10 eth_getCode 0xSYNTH-SIGNER | v2 pair ABI; `PairCreated(address,address,address,uint256)` | rpc_state | proven | LP tokens could be approved to a locker without transfer: `allowance` reads to known lockers are zero (E9). | S1 universe (two factories, full range); other DEXes not searched. | LP transfer to a locker; liquidity removal; new pool creation. |
| F3 | The website statement "100% of liquidity locked" does not match deployed state at P1: one of two pools is locked. | 31337 (synthetic; verify at use time) | null | P1 | E11 `evidence/E11.html` (website capture, sha256 recorded); E1-E4, E8-E10 (the onchain reads that contradict it) | raw (E11); NPM and v2 pair ABI (E1-E4, E8-E10) | rpc_state | proven | The statement may refer to the v3 position only; as written it is contradicted by the pair's LP holder read (E9). | Website captured once at `captured_at_utc`; onchain reads at P1; S1 universe. | Website edit; LP lock of the pair. |

**The WRONG conclusion to avoid.** "Liquidity is locked for 12 months (per the website), so LP removal
risk is low." Wrong because: the lock covers one position, bound to one locker and one unlock time;
`side_pool_removal_risk` is a separate surface and S1 shows a second pool whose LP tokens sit in an
EOA; the website is untrusted evidence and was contradicted; and "locked" is never a system-wide
conclusion, only a proposition about position 4471 until `unlockTime`.

## Scenario 2 - Fixed supply with severe holder-sized exit degradation

SYNTHETIC EXAMPLE, not a live finding.

**Situation.** Broad mode. `0xSYNTH-TOKEN` has no mint path and a constant `totalSupply` since
deployment; one v3 pool `0xSYNTH-POOL-V3` against wrapped native, quoter `0xSYNTH-QUOTER`. The user
holds 1.5% of supply. Requirement frame: "exit a 1.5%-of-supply position within 10% per-unit
degradation versus the small-size quote".

**What the evidence showed.**
- [bytecode, current-state at P1] no mint/rebase/balance-rewrite selectors; no CALL-family opcodes;
  `owner()` selector absent. [rpc_state, current-state at P1] `totalSupply()` equals the sum of the
  earliest reachable `Transfer` mint events (D-SUPPLY pass).
- [receipt + log_decoded, historical] tx `0xSYNTH-TX-SELL-1`: a holder sold 0.02% of supply; receipt
  status success; `Swap` amounts match the pool's mechanics within rounding (C-HIST-SELL pass).
- [rpc_state, current-state at P1] `quoteExactInputSingle` via `0xSYNTH-QUOTER` at P1's block for four
  sizes (table below); cross-checked by a tick walk with `pool_math.py v3-tick-to-price`.
- [rpc_state, current-state at P1] active liquidity is concentrated in a narrow range around the
  current tick; liquidity beyond it is thin. Top-10 holders excluding pool and burn hold 34% of supply.

Quote table (all at P1, route token -> wrapped native, fee 3000):

| Size (fraction of supply) | Input (tokens) | Quoted output (wrapped native) | Per-unit output | Degradation vs small | Result |
|---|---|---|---|---|---|
| small, 0.01% | 100,000 | 1.000 | 1.000e-5 | 0% (reference) | success |
| holder-sized, 0.5% | 5,000,000 | 39.5 | 7.90e-6 | -21.0% | success |
| holder-sized, 1.5% (user) | 15,000,000 | 61.2 | 4.08e-6 | -59.2% | success, exits the concentrated range |
| holder-sized, 4% (top holder) | 40,000,000 | - | - | - | revert (price limit / insufficient liquidity) |

**Correct ratings.**

| Surface | Rating | Likelihood | Confidence | Coverage | Note |
|---|---|---|---|---|---|
| token_controls | low | low | high | full | A-MINT..A-ADMIN pass at P1 |
| sellability_exit_depth | high | high | high | full | C-QUOTE finding (F3, high; F2, info for the reference size); C-HIST-SELL finding (F1, info) proves execution at that historical state only |
| current_concentration | high | medium | high | full | D-CONC finding (F4, high; row omitted): 34% in top-10 excluding pool and burn, denominators stated |

**Correct bounded verdict text.** "Sellable at the tested sizes under the quoted state" applies to the
small size only: at P1 a 0.01%-of-supply sell quotes at the reference per-unit output (F2), and one
historical sell of 0.02% executed at tx `0xSYNTH-TX-SELL-1` (F1). At 0.5% the per-unit output degrades
21%, at 1.5% it degrades 59%, and at 4% the quote reverts (F3). NO-GO under the stated requirement
(exit of a 1.5% position within 10% degradation); the fixed supply does not change this."

**Sample ledger rows.**

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | At tx 0xSYNTH-TX-SELL-1 a sell of 0.02% of supply into 0xSYNTH-POOL-V3 executed successfully and delivered wrapped native consistent with the pool's reserves at that block. | 31337 (synthetic; verify at use time) | 0xSYNTH-POOL-V3 | 0xSYNTH-TX-SELL-1 | E12 eth_getTransactionReceipt; E13 `Swap` log decode | `Swap(address,address,int256,int256,uint160,uint128,int24)` | receipt | proven | A router "success" without delivery: excluded by the `Transfer` of wrapped native to the seller in the same receipt (E12). | One historical sell; proves execution at that state only. | Never for this tx; irrelevant to current depth. |
| F3 | At P1, quoted per-unit output for a 1.5%-of-supply sell is 59.2% below the 0.01% reference and a 4% sell reverts on 0xSYNTH-QUOTER. | 31337 (synthetic; verify at use time) | 0xSYNTH-POOL-V3 | P1 | E14-E17 eth_call quoteExactInputSingle at 0x<P1 block hex>, one artifact per size; E18 tick-walk cross-check | QuoterV2 ABI; `pool_math.py v3-tick-to-price` | rpc_state | proven | Quoter misconfiguration: excluded by the independent tick walk agreeing within rounding (E18). Aggregator routes through other pools: none exist (S1). | Four sizes at P1; no multi-hop routes tested because S1 found one pool. | Any block after P1 (re-pin for "now"); liquidity added or removed; range changes. |

**The WRONG conclusion to avoid.** "Fixed supply, no owner, one historical sell succeeded: holders can
sell." Wrong because: a successful historical sell proves execution at that historical state and size
only; a quote at 0.01% says nothing about 1.5%; executable depth is a curve, and the curve here
reverts before the top holder's size. Fixed supply is a `token_controls` fact and does not bear on
`sellability_exit_depth`. Also wrong: generalising "Sellable at the tested sizes under the quoted
state" to sizes that were not tested.

## Scenario 3 - An upgradeable reward layer surrounding an immutable token

SYNTHETIC EXAMPLE, not a live finding.

**Situation.** Broad mode. `0xSYNTH-TOKEN` is a non-proxy ERC-20 with no owner. Holders stake into
`0xSYNTH-VAULT`, an EIP-1967 proxy whose admin `0xSYNTH-PROXYADMIN` is owned by the EOA
`0xSYNTH-ADMIN-EOA`; the vault holds 41% of supply (38% staked deposits, 3% reward inventory). The user
asks "is the token immutable, and can staked deposits be taken?" under the requirement frame "staked
deposits cannot be redirected by a single key".

**What the evidence showed.**
- [bytecode, current-state at P1] token runtime: no mint, burn-from-by-admin, pause, blacklist, tax,
  upgrade or role selectors; no DELEGATECALL/CALL/CALLCODE/CREATE/CREATE2 opcodes; not EIP-1167.
- [rpc_storage, current-state at P1] token EIP-1967 implementation, admin and beacon slots and the
  EIP-1822 slot are zero. [rpc_state, current-state at P1] `owner()` reverts (selector absent).
- [source_verified, current-state at P1] token source reproduces the runtime code hash by byte comparison.
- [manual_note + coverage limitation L1 `rpc_pruned`, historical] the token's deployment receipt is
  beyond the endpoint's pruning horizon; constructor arguments were not read. Added check
  `A-DEPLOY-HISTORY` is `unknown` (reason cites L1); the initial-supply recipient was taken from the
  earliest reachable `Transfer` log instead.
- [rpc_storage, current-state at P1] vault implementation slot -> `0xSYNTH-VAULT-IMPL`; admin slot ->
  `0xSYNTH-PROXYADMIN`. [rpc_state, current-state at P1] `owner()` of the proxy admin =
  `0xSYNTH-ADMIN-EOA`, code empty (EOA); no `getMinDelay()` selector on the admin (not a timelock).
- [rpc_state, current-state at P1] `balanceOf(0xSYNTH-VAULT)` = 41% of `totalSupply()`; `totalStaked()`
  = 38% of supply.
- [log_decoded, historical] two `Upgraded(address)` events on the vault in the searched range: the
  upgrade path has been exercised.

**Correct ratings.**

| Surface | Rating | Likelihood | Confidence | Coverage | Note |
|---|---|---|---|---|---|
| token_controls | low | low | high | partial | A-MINT..A-ADMIN pass; A-RENOUNCED finding (F1, info); `coverage_qualified: true`; `coverage_note`: "A-DEPLOY-HISTORY unknown (L1, rpc_pruned): deployment receipt and constructor args not readable. Current-state controls fully tested at P1; the runtime has no owner, role or upgrade selectors, so history cannot re-introduce them." |
| admin_treasury_reward_custody | high | high | high | full | G-LAYER-ADMIN finding (F2, high; F3, medium: the path was exercised twice): one EOA can replace the code custodying 41% of supply, without delay; likelihood high because that key has used the authority |
| reward_accounting_liveness | medium | medium | high | full | G-REWARDS pass: conservation and entitlement verified at P1; rated medium, not low, because the accounting code is replaceable (G-LAYER-ADMIN, F2) - the authority itself is rated once, under admin_treasury_reward_custody |
| current_concentration | medium | medium | high | full | 41% classified as protocol custody, stated separately from holder balances |

**Correct bounded verdict text.** "No mint, upgrade, seizure, restriction, tax or external-call path
found in the token's own runtime at the pinned block (F1; coverage note L1): no address can change the
token's code. The vault that custodies 41% of supply is an EIP-1967 proxy whose implementation can be
replaced without delay by one EOA (F2) and has been upgraded twice (F3). NO-GO under the stated
requirement that staked deposits cannot be redirected by a single key; GO-WITH-CONDITIONS for unstaked
holding under 'token code immutability': do not stake; re-test A-checks at a new pin before acting."

**Sample ledger rows.**

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | At P1, 0xSYNTH-TOKEN is not a proxy (EIP-1967/1822 slots zero), has no owner or role selectors, no CALL-family opcodes, and its runtime matches verified source; no path to change supply, balances, transfer rules or code exists in the deployed runtime. | 31337 (synthetic; verify at use time) | 0xSYNTH-TOKEN | P1 | E1 eth_getCode; E2 selector_scan.py --json; E3-E6 eth_getStorageAt (four slots); E7 source_verified compile + byte comparison | selector walk; EIP-1967/1822 slot constants (ddcore); compiler settings from verified source | bytecode | proven | Hidden path in a fallback without a selector: excluded by source correspondence (E7). Constructor-time grants: not readable (L1), but no role storage is referenced by the runtime. | Current state at P1; deployment receipt unavailable (L1). | Never for this runtime; a reorg of P1 only. |
| F2 | At P1, the implementation of 0xSYNTH-VAULT (holding 41% of supply) can be replaced by 0xSYNTH-PROXYADMIN, whose `owner()` is the EOA 0xSYNTH-ADMIN-EOA, with no timelock. | 31337 (synthetic; verify at use time) | 0xSYNTH-VAULT | P1 | E8 eth_getStorageAt EIP1967_ADMIN_SLOT; E9 eth_call owner() on the admin; E10 eth_getCode admin EOA; E11 eth_call balanceOf(vault) at 0x<P1 block hex> | EIP-1967 admin slot; `owner()` selector; uint decode | rpc_storage | proven | A delay enforced elsewhere: the admin's selectors contain no delay or governance read (E12). | Slots and reads at P1; `Upgraded` replay over the full range (F3). | `AdminChanged`, `OwnershipTransferred` on the admin, `Upgraded`, reorg of P1. |

**The WRONG conclusion to avoid.** "The token is immutable and renounced, so the system cannot be
rugged." Wrong because ratings are per surface: `token_controls` describes the token's code, while
the layer that holds 41% of supply is replaceable by one key and is rated once, under
`admin_treasury_reward_custody` (`G-LAYER-ADMIN`); `reward_accounting_liveness` only reflects that its
accounting code is replaceable. Three further wrong moves: rating `token_controls` `low` while
`A-DEPLOY-HISTORY` is `unknown` without `coverage_qualified: true` and a `coverage_note` (validator
E-RATING-UNKNOWN-AS-LOW); rating `token_controls` `high` because the vault is upgradeable, which moves a
finding onto the wrong surface; and repeating the same authority finding on two surfaces.

## Scenario 4 - Launch wallets selling and rebuying for new recipients (market-mediated redistribution)

SYNTHETIC EXAMPLE, not a live finding.

**Situation.** Focused mode on the question "did insiders dump on buyers and rotate into fresh
wallets?", requirement frame "no material early selling by allocation recipients". Launch on a
Pons-style platform (`references/platforms/pons-style-launches.md`). Six wallets W1..W6 received tokens
through the platform's direct-buy path in the first 15 blocks; within 400 blocks they sold 62% of their
allocation into `0xSYNTH-POOL`; over the next 2,000 blocks nine previously unseen addresses R1..R9
bought a similar quantity. All fifteen wallets were funded from `0xSYNTH-EXCH-HOT`.

**What the evidence showed.**
- [manual_note, definition recorded before measurement] cohort C1 in discovery record S1: inclusion
  rule "recipient of a direct-buy in blocks L..L+15", sources "curve/pool `Buy` logs + calldata",
  exclusions "the pool, the platform, the burn address", coverage "full; no failed log windows".
- [calldata + receipt, historical] six direct-buy txs decode a `recipient` parameter = W1..W6; for two
  of them the tx sender was `0xSYNTH-RELAYER`, not the recipient.
- [receipt + log_decoded, historical] 14 sale txs `0xSYNTH-TX-S1..S14` by W1..W6: `Swap` events with
  token in and quote asset out, receipts successful, amounts consistent with pool mechanics; total sold
  = 62% of allocation; gross proceeds 41.3 quote units after pool fee.
- [log_decoded, historical] purchases by R1..R9: `Swap` events with recipient R_i; total bought = 58%
  of what C1 sold. [calldata, historical] no C1 transaction carries any R_i as `recipient` or `to`;
  each R_i call has `msg.sender == R_i`.
- [trace, historical] native funding of W1..W6 and R1..R9 from `0xSYNTH-EXCH-HOT`, 12 to 90 minutes
  before each wallet's first action; [explorer, corroboration] label "exchange hot wallet".
- [rpc_state, current-state at P1] W1..W6 retain 38% of their allocation; R1..R9 hold 9.1% of supply.

**Correct ratings.**

| Surface | Rating | Likelihood | Confidence | Coverage | Note |
|---|---|---|---|---|---|
| historical_launch_integrity | high | high | high | full | E-EARLY-SALES finding (F1, high; F2, info); the event already occurred, so likelihood describes recurrence for retained inventory |
| current_concentration | medium | medium | high | full | D-CONC: R1..R9 9.1% and W1..W6 retained balance classified as `holder`, provenance `event_derived` |

**Correct bounded verdict text.** "Proven at tx `0xSYNTH-TX-S1..S14`: the six direct-buy allocation
recipients (cohort C1, S1) sold 62% of their allocation into `0xSYNTH-POOL` within 400 blocks of launch
for gross proceeds of 41.3 quote units (F1); 38% is retained, so no profit figure is stated. The later
purchases by nine new recipients are market-mediated redistribution: no trace, calldata recipient,
controlling contract or reconciled flow links any of them to a C1 wallet, and shared funding from one
exchange hot wallet does not establish common control (F2). NO-GO under the stated requirement of no
material early selling by allocation recipients; the 'insider rotation' reading is not established."

**Sample ledger rows.**

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | Cohort C1 (S1: six direct-buy recipients, blocks L..L+15) sold 62% of its allocation into 0xSYNTH-POOL in 14 successful txs within 400 blocks of launch, receiving 41.3 quote units after pool fee. | 31337 (synthetic; verify at use time) | null | 0xSYNTH-TX-S1 | E20 receipts + `Swap` decodes for 14 txs; E19 direct-buy calldata (recipient param) | `Swap` event ABI; direct-buy function ABI from source_verified platform | receipt | proven | Router transfers without execution: excluded by receipts and pool mechanics. Sales by a non-cohort wallet holding cohort tokens: excluded by `Transfer` replay of cohort balances (E21). | Blocks L..L+400 fully read; cohort defined before measurement (S1); 14 txs S1..S14 (receipts in E20), pin_or_tx cites the first. | Never for the window; later sales belong to a follow-up range. |
| F2 | Purchases by R1..R9 (58% of C1's sold quantity, blocks L+400..L+2400) are market-mediated redistribution through 0xSYNTH-POOL; no evidence links any R wallet to a C1 wallet. | 31337 (synthetic; verify at use time) | null | 0xSYNTH-TX-R1 | E22 `Swap` decodes for R1..R9; E23 calldata of all C1 txs (no R recipient); E24 traces of funding from 0xSYNTH-EXCH-HOT; E25 explorer label capture | `Swap` ABI; router ABI; trace `CALL` value transfers | log_decoded | inference | Common control of W and R wallets: consistent with timing and shared exchange funding, but exchanges fund unrelated users from one hot wallet by design; no trace, batch, controlling contract or recipient parameter found. Unrelated buyers: equally consistent. | Blocks L+400..L+2400 (S1 follow-up range); nine purchase receipts in E22, pin_or_tx cites the first; commingling stop at 0xSYNTH-EXCH-HOT (F-COMMINGLING). | A trace, calldata recipient or controlling contract linking an R wallet to C1. |

**The WRONG conclusion to avoid.** "Insiders dumped 62% and re-accumulated under fresh wallets funded
from the same exchange." Wrong because: "insider" needs a non-market allocation path and a definition in
the requirement frame, and the direct-buy function is public with a recipient parameter anyone can set;
the sales are proven, but "rotation" rests on timing plus a shared exchange funding source, neither of
which proves common ownership (`references/attribution.md`); and "profit" is not established (cost
basis is the direct-buy price plus platform fee, and 38% of inventory is retained). The correct
description is market-mediated redistribution with the alternatives stated.

## Scenario 5 - A vault holding synthetic claims without a proven underlying exit

SYNTHETIC EXAMPLE, not a live finding.

**Situation.** Focused mode on "is the token backed, and can I redeem?", requirement frame "redeemable
underlying backing". `0xSYNTH-VAULT` is advertised as "every token redeemable 1:1 for ASSET". At P1 the
vault holds 100,000 units of `0xSYNTH-WASSET`, a bridged representation of ASSET issued by bridge
`0xSYNTH-BRIDGE`, and owns v3 position NFT 902 in the token/WASSET pool.

**What the evidence showed.**
- [rpc_state, current-state at P1] vault holdings: `balanceOf(0xSYNTH-VAULT)` on `0xSYNTH-WASSET` =
  100,000e18.
- [rpc_state, current-state at P1] pool inventory: `positions(902)` gives liquidity and range; the
  WASSET-equivalent amount (about 40,000, computed with `pool_math.py`) is not in the vault's balance
  and is convertible only by `decreaseLiquidity`, callable by the vault owner.
- [rpc_state, current-state at P1] unclaimed fees: `positions(902).tokensOwed0/1` = 1,200 WASSET, not
  yet collected; `collect` authority is the vault owner.
- [source_verified + rpc_state, current-state at P1] the holder-callable `redeem(uint256)` transfers
  WASSET (a synthetic claim), not ASSET, at a `rate()` set by the owner; `owner()` = `0xSYNTH-ADMIN-EOA`;
  a `sweep(address,uint256)` function is reachable by the owner.
- [rpc_state, current-state at P1] `0xSYNTH-BRIDGE.paused()` = true; `eth_call` of `burnAndRelease`
  reverts with "paused"; only `RELAYER_ROLE` (one holder) can unpause.
- [log_decoded, historical] search S1 for bridge `Released`/`Withdraw` events naming the vault or any
  holder over blocks 1,100,000-1,250,000: none found.
- [receipt, historical] tx `0xSYNTH-TX-REDEEM`: a holder redeemed and received WASSET; no delivery of
  ASSET to that holder found in the searched range.
- [website, discovery only] the "1:1 redeemable" claim.

**Correct ratings.**

| Surface | Rating | Likelihood | Confidence | Coverage | Note |
|---|---|---|---|---|---|
| utility_redemption_rights | high | high | high | partial | G-RIGHTS finding (F1, high); G-EXIT-ROUTE finding (F3, medium); the issuer's off-chain release process is not observable |
| external_dependencies | high | medium | high | partial | H-DEPS finding (F2, high): exit depends on a paused bridge controlled by one role holder |
| admin_treasury_reward_custody | high | medium | high | full | G-LAYER-ADMIN finding (F4, high; row omitted): owner can `sweep`, `decreaseLiquidity`, `collect` and set `rate()` |
| reward_accounting_liveness | medium | medium | medium | partial | G-REWARDS pass (redemptions process), rated medium because they deliver only the synthetic claim |

**Correct bounded verdict text.** "Holders can redeem for `0xSYNTH-WASSET`, a synthetic claim, at an
owner-set rate (F1). No executable exit from WASSET to the underlying asset was found at the pinned
block (bridge paused, F2), and no historical exit was found in the searched range (F3, S1). 'Redeemable
1:1 for ASSET' is a website claim not matched by an enforceable right. NO-GO under the stated requirement
of redeemable underlying backing."

**Sample ledger rows.**

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | At P1 the assets attributable to 0xSYNTH-VAULT are: vault holdings 100,000 WASSET (a synthetic claim on ASSET); pool inventory of about 40,000 WASSET-equivalent in position 902, not in the vault balance and convertible only by the owner; unclaimed fees of 1,200 WASSET. None is ASSET; holder `redeem` delivers WASSET at an owner-set rate. | 31337 (synthetic; verify at use time) | 0xSYNTH-VAULT | P1 | E1 eth_call balanceOf(vault) on WASSET; E2 eth_call positions(902); E3 pool_math amounts; E4 eth_call rate(); E5 source_verified vault `redeem` | ERC-20 ABI; NPM `positions` ABI; v3 amount formulas; verified source | rpc_state | proven | A second exit path delivering ASSET: none in the vault's selectors (E6). | Reads at P1; position history not replayed (not needed for the question). | `Collect`, `DecreaseLiquidity`, `rate()` change, `sweep`, reorg of P1. |
| F2 | At P1, 0xSYNTH-BRIDGE is paused and `burnAndRelease` reverts; the only unpause authority is one `RELAYER_ROLE` holder. | 31337 (synthetic; verify at use time) | 0xSYNTH-BRIDGE | P1 | E7 eth_call paused(); E8 eth_call burnAndRelease (revert data); E9 eth_call getRoleMemberCount(RELAYER_ROLE) | AccessControl ABI; revert string decode | rpc_state | proven | A release path bypassing `burnAndRelease`: none among the bridge's selectors (E10). | Bridge state at P1; issuer's off-chain process not observable. | `Unpaused`, role grant, bridge upgrade, reorg of P1. |
| F3 | No successful WASSET -> ASSET release naming the vault or any token holder occurred in blocks 1,100,000-1,250,000. | 31337 (synthetic; verify at use time) | 0xSYNTH-BRIDGE | P1 | E11 eth_getLogs `Released`/`Withdraw` in 10,000-block windows (S1) | bridge event ABI from verified source | log_decoded | strongly_supported | Releases before the range or on another chain: not searched (stated in S1). | S1 range full; no failed windows. | Range advances; a release event appears. |

**The WRONG conclusion to avoid.** "The vault holds 141,200 ASSET-equivalent against 100,000 tokens, so
the token is 141% backed." Wrong because: the 100,000 is a synthetic claim whose own exit is paused;
the 40,000 is pool inventory that is not in the vault's balance, is exposed to price, and is convertible
only by the owner; the 1,200 is unclaimed fees not yet collected; and none of the three is ASSET. A
vault balance is inventory, not available backing, until an executable and observed exit to the
underlying asset exists at the pin.

## Scenario 6 - An RPC failure that must remain a coverage limitation

SYNTHETIC EXAMPLE, not a live finding.

**Situation.** Focused mode on "who can remove the canonical LP principal?". P1 was captured from
endpoint A (`eth_chainId` verified, header preserved). While reading the position and the locker at
P1's block, endpoint A returned "missing trie node" (its state window had moved past P1) on three
retries; the fallback endpoint B failed DNS resolution. No archive endpoint was available in the session.

**What the evidence showed.**
- [rpc_state, current-state at P1] E1: the pin header and chain id (captured before the failure).
- [log_decoded, historical] E2: `Transfer` of position NFT 4471 to `0xSYNTH-LOCKER` at block 1,201,000,
  tx `0xSYNTH-TX-LOCK`, read from logs before the failure.
- [manual_note] E3: the failed queries preserved verbatim (redacted endpoint, method, params, block,
  error text, three retry timestamps, DNS error of endpoint B).
- Not obtained: `ownerOf(4471)`, `positions(4471)`, the locker's code and storage at P1.

Limitation entries (manifest `coverage.limitations[]`):
```json
{"limitation_id": "L1", "kind": "rpc_pruned",
 "description": "eth_call ownerOf(4471)/positions(4471) on 0xSYNTH-NPM and eth_getCode/eth_getStorageAt on 0xSYNTH-LOCKER at block 0x<P1> returned 'missing trie node' on endpoint A (state pruned past the pin); fallback endpoint B: see L2.",
 "affected_check_ids": ["B-PRINCIPAL"], "affected_addresses": ["0xSYNTH-NPM", "0xSYNTH-LOCKER"], "retry_attempts": 3}
{"limitation_id": "L2", "kind": "dns_failure",
 "description": "Endpoint B failed DNS resolution on 3 attempts; no read completed.",
 "affected_check_ids": ["B-PRINCIPAL"], "affected_addresses": [], "retry_attempts": 3}
```

The check as it must appear (manifest `checks[]`):
```json
{"check_id": "B-PRINCIPAL", "surface": "canonical_lp_principal_custody",
 "name": "Executable LP-principal removal path for position 4471",
 "status": "unknown", "severity": null, "evidence_ids": ["E2", "E3"], "finding_ids": ["F2"],
 "reason": "Position owner, liquidity and locker code at P1 unreadable: L1 (rpc_pruned, 3 retries); fallback L2 (dns_failure). The historical NFT transfer to 0xSYNTH-LOCKER at block 1201000 (E2) is not current state.",
 "pin_id": "P1", "limitation_id": "L1"}
```
F2 (confidence `unknown`) may hang off the `unknown` check; F1 (proven, historical) may not (E-CHECK-STATUS:
an unknown check contradicts a proven finding), so it hangs off an added check `B-NFT-CUSTODY-HISTORY`
(`status: finding`, `severity: info`, same surface) that is listed in the rating's basis.

**Correct ratings.**

| Surface | Rating | Likelihood | Confidence | Coverage | Note |
|---|---|---|---|---|---|
| canonical_lp_principal_custody | unknown | unknown | low | partial | B-CANON pass (pool identified by address, E1); B-NFT-CUSTODY-HISTORY finding (F1, info); B-PRINCIPAL unknown (L1, L2); `time_basis_pin_id: P1` |

**Correct bounded verdict text.** "Unknown because historical state was unavailable: the owner and
liquidity of position 4471 and the code of `0xSYNTH-LOCKER` at the pinned block could not be read (L1
rpc_pruned after 3 retries; L2 dns_failure on the fallback). The historical transfer of NFT 4471 to
`0xSYNTH-LOCKER` at block 1,201,000 (F1) is not evidence of its custody at P1. No rating other than
`unknown` is issued; re-run B-PRINCIPAL against an archive endpoint at P1, or re-pin and re-read."

**Sample ledger rows.**

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | At tx 0xSYNTH-TX-LOCK (block 1,201,000) position NFT 4471 on 0xSYNTH-NPM was transferred to 0xSYNTH-LOCKER. | 31337 (synthetic; verify at use time) | 0xSYNTH-NPM | 0xSYNTH-TX-LOCK | E2 eth_getLogs `Transfer(address,address,uint256)` topic filter tokenId 4471, blocks 1,190,000-1,201,000 | ERC-721 `Transfer` ABI | log_decoded | proven | None identified after checking the receipt status and the emitting address (E2). | Logs before the failure; nothing after block 1,201,000 was read. | Irrelevant to current state; historical fact only. |
| F2 | The owner, liquidity and removal path of position 4471 at P1 are unknown: reads at the pinned block failed (L1, L2). | 31337 (synthetic; verify at use time) | 0xSYNTH-LOCKER | P1 | E3 `evidence/E3.json` (failed queries: method, params, block, error, retries; endpoint redacted) | raw | manual_note | unknown | The position may still be in the locker, may have been transferred after block 1,201,000, or the locker may expose a removal path; none is tested. | No state read at P1; L1, L2. | A successful read at P1 from an archive endpoint; a re-pin. |

**The WRONG conclusion to avoid.** Two wrong moves. (1) Marking `B-PRINCIPAL` `pass` because the last
known transfer went to a locker, or because "nothing adverse was seen": an `unknown` check is never a
pass (E-CHECK-STATUS), a `low` rating over it is rejected (E-RATING-UNKNOWN-AS-LOW), and the historical
transfer says nothing about approvals, later transfers, or the locker's code at P1. (2) Reporting the
failure as a token risk ("the locker's state could not be read, which suggests obfuscation" or "the
project's contracts are unreadable"): coverage limitation kinds (`rpc_pruned`, `dns_failure`,
`rpc_timeout`, `rpc_rate_limit`, ...) describe the endpoint, never the token, and are recorded only as
`L<n>` entries referenced by the affected checks (W-LIMITATION-UNREFERENCED otherwise).
