# Surface E - Launch integrity

Rating key: `historical_launch_integrity`. Core check: `E-LAUNCH`. Sub-checks: `E-FACTORY-VERSION`,
`E-EXEMPTIONS`, `E-DIRECT-BUY-RECIPIENT`, `E-COHORT`, `E-EARLY-SALES`. Read this file for any question
about how the token was launched, who got what at launch, whether early wallets sold, or whether a
launch platform's defaults were altered; always in `broad` mode. Attribution language comes from
`references/attribution.md`; Pons-style curve launches additionally use
`references/platforms/pons-style-launches.md`.

## Purpose

Decode what actually happened at launch from the deployment/launch transaction(s) and the first blocks
of trading: parameters chosen, allocations made, exemptions granted, who received the first buys, how
the launch signer was funded, in what order the pieces were deployed, and what the early cohort did
with its tokens. Launch evidence is HISTORICAL: it binds to transaction hashes and blocks, not to P1,
and it never proves what a wallet can do today (that is surfaces A-D at P1).

## What can change the verdict

- A launch parameter that differs from the factory's automatic behavior (a manually supplied fee
  exemption, an oversized initial buy, a non-default recipient, a skipped lock) - because it shows a
  choice, not a template.
- The direct buy at launch landing in an address other than the transaction sender, or executed
  without the tax that ordinary buyers paid.
- A deployment sequence with a window in which the token was tradeable before liquidity was locked or
  before exemptions were finalized.
- Early sales proven at receipt level for a material share of the launch allocation, and the
  redistribution route those sales took (market-mediated redistribution to new recipients).
- Coverage: a pruned or rate-limited log range over the launch window makes `E-EARLY-SALES` and
  `E-COHORT` `unknown`, which blocks a `low` rating without an explicit coverage note.
- Factory source that does not correspond to the deployed factory runtime: every decoded parameter
  then rests on `source_unverified` decoding and must be marked as such.

## Procedure

Historical reads use the block of the event (`eth_getTransactionReceipt`, `eth_getLogs`, traces);
current-state reads (an exemption flag as it stands now) use P1. Never mix the two in one row.

1. Resolve the launch transaction. Start from the packet `deployment` block (`status`, `tx_hash`,
   `deployer`, `factory`). If `unresolved`, discover candidates (explorer contract-creation view,
   `trace_filter`/`trace_block` around the first `Transfer` from the zero address, the first
   `OwnershipTransferred`, the pool's `PoolCreated`/`Initialize`) and then VERIFY by receipt: either
   `receipt.contractAddress == target`, or a `trace` whose CREATE/CREATE2 result equals the target.
   Record provenance (`rpc_derived` after verification; `explorer_label` before). A token created by a
   factory has the factory as `to` and the token address only in the trace/logs.
2. `E-FACTORY-VERSION` - identify the factory and the exact version deployed. Read the factory's
   runtime at the deployment block (fall back to P1 if pruned; note which) and keep its code hash.
   Then compare across siblings: from the factory's creation event (topic0 taken from the launch
   receipt, not assumed), sample other tokens created by the same factory code hash within a bounded
   range, read their runtimes, and compare code hashes with the target's (mask constructor-set
   immutables before comparing when the runtime embeds them; list the masked offsets). Identical
   runtime hash = same template; a different hash = a different factory version, a different template,
   or a non-factory deployment - each is a separate finding. Record the sample: range, count, hashes.
3. Decode launch calldata. Preferred decoding basis is the factory's source once correspondence is
   established (`references/surfaces/H-utility-dependencies-dev.md`, `H-SOURCE-CORRESPONDENCE`).
   Without correspondence, decode structurally: 4-byte selector (`ddcore.selector` over candidate
   signatures), then 32-byte words; address-shaped words (12 leading zero bytes), amounts, offsets for
   dynamic arrays. Capture every parameter as a `calldata` evidence row with its word index and the
   decoding basis. Parameters to look for: name/symbol/supply; allocation table (creator, vault,
   airdrop, team); fee-exemption list; initial buy amount (quote units); direct-buy RECIPIENT parameter;
   tax flag for the initial buy; pool configuration (fee tier, tick spacing, hook, initial price/tick);
   locker configuration (duration, beneficiary); CREATE2 salt.
4. Separate automatic from manual. A value is "automatic" only when it is a constant in the factory
   runtime or identical across the sibling sample from the same factory version; it is "manual" when it
   appears in the launcher's calldata or differs across siblings. Write one row per parameter:
   `parameter | observed value | automatic/manual | how determined (constant offset, sibling sample,
   calldata word)`. Never describe a platform default as a launcher choice or a launcher choice as a
   platform default.
5. Deterministic addresses. If the deployment used CREATE2, reconstruct
   `address = keccak256(0xff ++ deployer ++ salt ++ keccak256(init_code))[12:]` with
   `ddcore.keccak256_hex` and confirm it equals the target; record the salt and whether the salt
   encodes the launcher's address or a mined vanity pattern. A deterministic deployment shows a tool
   was used; it does not by itself show coordination or common ownership (`references/attribution.md`).
6. Deployment sequence. Order the steps by (block, tx index, log index): token created -> pool created
   (`PoolCreated` for v2/v3 factories, `Initialize` on the v4 PoolManager, curve created on a launch
   platform) -> liquidity added (`Mint`/`IncreaseLiquidity`/`ModifyLiquidity`) -> position transferred
   to a locker (`Transfer` of the position NFT, or lock event) -> exemptions set -> trading enabled.
   Record the tx hash and block of each step; measure any window in which trading was possible before
   the lock or before exemptions were final. Hand the pool and locker facts to
   `references/surfaces/B-liquidity-custody.md`.
7. `E-EXEMPTIONS` - list every address exempted from tax, limits or cooldown at launch (calldata or
   setter calls in the launch window), then read the SAME flags at P1 to show which exemptions still
   stand. Cross-reference `A-TAX` and `A-RESTRICT`. An exemption that covers the launch signer, the
   direct-buy recipient or a treasury changes how launch trades and later sales were taxed.
8. `E-DIRECT-BUY-RECIPIENT` - for the initial buy (in the launch tx or the first buy tx), decode the
   buy call's recipient parameter and compare it with `msg.sender` (tx `from`) and with the `to` of
   the token `Transfer` log; then establish the tax treatment by comparing the quote input, the token
   output, the curve/pool math at that state, and the exemption state at that block. Do not assume the
   sender received the benefit; the recipient parameter and the Transfer `to` decide. For Pons-style
   curves follow `references/platforms/pons-style-launches.md`.
9. Funding of the launch signer. Within a bounded pre-launch window (default: from the signer's first
   observed activity, or 50,000 blocks before the launch, whichever is shorter - state the choice),
   list native and ERC-20 inflows to the launch signer with tx hashes (traces for internal native
   transfers). Name the sources with neutral roles (`funder`, `exchange_deposit` when a label exists as
   corroboration only). Shared funding does not establish common ownership or coordination.
10. `E-COHORT` - define the cohort BEFORE measuring anything, and record the definition as a manifest
    `discovery[]` entry (`S1`...). Template:
    ```
    cohort_id: C1
    inclusion_rule: addresses that received the token by Transfer within [launch block, launch block + N]
                    from the launch signer, the factory, the direct-buy recipient, or the curve/pool
    time_bounds: block X (launch tx) to block X + N (N stated below), plus follow-up to P1 for outflows
    sources: eth_getLogs Transfer(address,address,uint256) on the token, paginated in ranges of R blocks;
             receipts of every in-range tx; traces where native value moved
    exclusions: pool/curve, locker, burn, router, factory, position manager (listed by address)
    coverage: full | partial (list ranges that failed with limitation ids)
    ```
    Default N = 1000 blocks or the first 24 hours by block timestamp, whichever ends first; widen when
    the platform's curve phase lasts longer, and say so. Coverage is the fraction of the range actually
    read; a paginated `eth_getLogs` that hit `rpc_rate_limit` in one page leaves the cohort `partial`.
11. `E-EARLY-SALES` - build the tracking table per cohort wallet:
    `wallet | initial allocation | transfers in | transfers out | sales (receipt-proven) | rebuys |
    downstream inventory | proceeds by asset | fees paid | retained assets | coverage`.
    A sale is proven ONLY by a successful receipt in which the wallet's tokens reach the pool/curve and
    a `Swap` (or curve sell event) with amounts consistent with the pool mechanics delivers quote asset
    to a recipient. A `Transfer` to a router, a pending approval, or a balance that reached zero proves
    nothing about a sale: the tokens may have moved to another wallet, into a locker, or into a
    position. Record the quote recipient of each sale; when a later buy from the same or a funded
    wallet delivers tokens to a NEW recipient, describe it as market-mediated redistribution, not as
    a wash or a hand-off, unless independently established. Escalate to
    `references/deep-tracks/launch-cohort.md` for full accounting.
12. Write the rows. Historical findings carry `pin_or_tx` = the tx hash and `is_historical: true`;
    current-state corollaries (an exemption still active at P1) are separate findings on P1. Rate the
    surface from decoded facts, never from the absence of decoding.

### Verify-at-use table

Nothing in this file asserts a chain-specific value. Each item below is chain-specific and must be
verified at use time via RPC/receipt before it is used in a decoding step.

| Item | Verify at use time by |
|---|---|
| Launch platform factory address (per chain) | `to` of the verified deployment tx; `eth_getCode` at the deployment block; code hash recorded |
| Factory creation event topic0 | topic0 observed in the launch receipt; recompute with `ddcore.keccak256_hex(b"<Signature>")` only after reading the signature from corresponding source |
| Pool factory init code hash (v2/v3 CREATE2) | `PoolCreated` event on the target chain; then `python3 <skill-root>/scripts/pool_math.py v3-pool --factory 0x.. --token-a 0x.. --token-b 0x.. --fee 500 --init-code-hash 0x..` must reproduce the event's pool address |
| v4 PoolManager address and PoolId | `Initialize` log on the manager; `python3 <skill-root>/scripts/pool_math.py v4-pool-id --currency0 .. --currency1 .. --fee .. --tick-spacing .. --hooks ..` must reproduce the logged id |
| Curve/router addresses used by early sales | `to` of the proven sale receipts, not a website list |
| Default N (blocks) and pagination size R | stated in the cohort definition; not a platform constant |

## Checks

| check_id | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|
| E-LAUNCH | Launch parameters, allocations, exemptions, direct-buy recipient, funding, sequence and early transfers/sales are decoded from the launch tx(s) | verified deployment receipt + decoded calldata + ordered launch logs | `receipt`, `calldata`, `log_decoded`, `trace` | never (historical); the DECODING is stale if factory source correspondence is later refuted |
| E-FACTORY-VERSION | The exact factory version and template are identified; target runtime matches the template's | factory code hash at deployment block + sibling runtime hash sample | `bytecode`, `receipt` | new factory version does not change this; target upgrade changes the current runtime only |
| E-EXEMPTIONS | Every launch-time exemption is listed with its origin (automatic/manual) and its state at P1 | calldata or setter receipts + P1 flag reads | `calldata`, `rpc_state` | exemption setter call after P1 |
| E-DIRECT-BUY-RECIPIENT | The initial buy's recipient and tax treatment are established from the call and its logs | buy calldata + Transfer log `to` + amount check against curve/pool math | `calldata`, `log_decoded`, `receipt` | never (historical) |
| E-COHORT | The cohort is defined before measurement, with inclusion rule, bounds, sources, exclusions, coverage | `discovery[]` entry + paginated log ranges with coverage | `log_decoded`, `manual_note` (definition) | new cohort members cannot appear; coverage improves if failed ranges are re-read |
| E-EARLY-SALES | Sales, rebuys and redistribution in the first N blocks are receipt-proven with pool mechanics | per-sale receipt + Swap/curve event + quote recipient | `receipt`, `log_decoded`, `trace` | never for the window; later sales belong to the follow-up range |
| E-DETERMINISTIC | CREATE2 address reconstruction reproduces the target and the salt is characterized | deployer, salt, init code hash, recomputed address | `trace`, `calldata`, `bytecode` | never |
| E-SEQUENCE | The token->pool->lock->exemptions order and any tradeable-before-lock window are measured | ordered (block, tx index, log index) rows | `receipt`, `log_decoded` | never |
| E-FUNDING | The launch signer's pre-launch funding sources are listed with neutral roles | inflow txs (native via trace, ERC-20 via logs) within the stated window | `trace`, `log_decoded` | never |

Statuses follow the manifest rules: `pass` and `finding` need evidence ids; `unknown`/`skipped` need a
reason (cite the limitation id when a pruned or rate-limited range caused it). `unknown` is never a
pass, and a `low` rating over an `unknown` cohort requires `coverage_qualified: true` with a note.

## Common false positives and negatives

False positives (a launch irregularity that is not one):
- A large "creator allocation" that is the factory's constant for every token from that version;
  report it as automatic behavior, with the sibling sample as evidence.
- A direct buy whose recipient differs from `msg.sender` because the platform's router forwards to a
  `recipient` argument equal to the caller's declared wallet - only a finding if the recipient is a
  third party or the tax treatment differs from ordinary buyers.
- A cohort wallet balance reaching zero: tokens moved, were locked, or were staked; not a cash-out.
- A `Transfer` to a router address without a matching `Swap`: an approval, a failed route, or a
  multicall that reverted after the transfer was reverted with it (check receipt status).
- Deterministic deployment or shared funding presented as coordination or identity.
- Sales by wallets funded from the same source described as a single actor; use "wallets funded by
  the same funder" and stop there.

False negatives (a launch fact missed):
- Decoding the launch tx only, when exemptions and locks were set in later txs in the same block or
  the next few blocks.
- Reading exemption flags at P1 only, missing exemptions that were active during the launch window
  and removed later (the tax treatment of early sales depends on the historical flag).
- Trusting an explorer's "creator" label instead of the receipt's `from` and the trace's CREATE.
- Treating the factory's verified source as the deployed factory without comparing the code hash
  (a different factory version can change the recipient and tax logic).
- A direct buy routed through a contract whose `Transfer` `to` is an intermediary that forwards in the
  same tx: follow the internal transfers to the final recipient within the receipt.
- Sales through a side pool or aggregator that the `Swap` topic filter on the canonical pool did not
  see; filter on the token's `Transfer` logs from the wallet first, then classify the counterparty.
- Cohort defined after seeing the results (selection bias); the definition must precede measurement
  and stand in `discovery[]` unchanged; if it must change, add a second cohort id and keep both.

## Escalation triggers to deep tracks

- Any receipt-proven early sale by a cohort wallet above the stated materiality threshold, any rebuy
  delivering tokens to a new recipient, or a cohort with more than a handful of wallets:
  `references/deep-tracks/launch-cohort.md` (full allocation -> transfers -> sales -> rebuys ->
  inventory -> proceeds -> fees -> retained accounting with a stopping condition).
- Proceeds leaving the cohort into treasuries, bridges or exchanges:
  `references/deep-tracks/proceeds-reconciliation.md` (and `references/surfaces/F-fees-treasury.md`).
- Allocations that do not reconcile with `Transfer` logs (mint at construction without events,
  balance rewrites): `references/deep-tracks/transfer-replay.md`.
- The launch position's later history (lock, unlock, decrease, transfer):
  `references/deep-tracks/pool-position-history.md`.
- A need to describe the launch signer as an operator of the project (never an identity):
  `references/deep-tracks/operational-attribution.md`.
- Factory runtime without corresponding source, or a recipient/tax path that cannot be read from
  calldata alone: `references/deep-tracks/proxy-bytecode.md` for the factory or curve contract.

## How to state results

Bind each statement to the transaction, block and decoding basis; keep automatic and manual apart;
use neutral roles.

- "The deployment tx 0x.. (block N) created the target via factory 0x.. (code hash 0x.. at block N,
  verify-at-use). Calldata word 5 sets the fee-exemption list to [0x.., 0x..]; the sibling sample
  (12 tokens, blocks N-20000..N) shows an empty list in every other case, so the exemption is a
  manually supplied parameter. Confidence: proven for the calldata; strongly_supported for 'manual'
  (sample of 12)."
- "The initial buy in tx 0x.. names recipient 0x.. (calldata word 2) while the sender was 0x..; the
  token Transfer `to` is 0x... The output matched the curve's untaxed quote at that state (evidence E9),
  so the buy was not taxed at the ordinary 3% rate. Recipient 0x.. is recorded with role `holder`;
  no ownership relation to the sender is asserted."
- "Cohort C1 (definition S1: recipients within blocks X..X+1000, 14 wallets, coverage full): 9 wallets
  sold a combined 41% of the cohort allocation in receipt-proven swaps on pool 0x.. (evidence E12-E20);
  proceeds of 3.1 quote units reached 4 recipients, one of which bought back into 2 new wallets. This
  is market-mediated redistribution; no common control is established."
- "Unknown because historical state was unavailable: `eth_getLogs` for blocks X+400..X+1000 failed
  with `rpc_rate_limit` after 3 retries (limitation L3); E-EARLY-SALES is `unknown` for that range and
  the `historical_launch_integrity` rating cannot be `low` without a coverage note."
- "No manually supplied exception was found in the decoded launch parameters of tx 0x..; every value
  matches the factory version's constants (E-FACTORY-VERSION, sample of 8). This binds only to the
  decoded transaction and the sample, not to later admin actions (surface A)."
- Never: "fair launch"; "the dev dumped"; "the same person controls these wallets"; "the launch was
  clean" when a range was unread; "they cashed out" from a zero balance.
