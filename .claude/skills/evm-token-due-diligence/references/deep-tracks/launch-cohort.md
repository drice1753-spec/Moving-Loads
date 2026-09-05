# Deep track: launch-cohort accounting

Track code `COHORT`. Check ids `T-COHORT-<SLUG>`. Feeds surface E (`E-LAUNCH`, `E-COHORT`, `E-EARLY-SALES`,
`E-DIRECT-BUY-RECIPIENT`) and surface D (`D-CONC` historical comparison). Run it because "insiders dumped" and
"the team still holds" are both cohort claims, and a cohort claim is meaningless until the cohort is defined
and every wallet in it is accounted from allocation to the pin. Language rules for what the numbers may be
called are in `references/attribution.md`; this track produces the numbers.

## Trigger

- Early receivers (launch tx, first N blocks, curve buyers, airdrop or allocation recipients) hold or held a
  material share, and the user asks what they did with it.
- `E-LAUNCH` decoded allocations or exemptions that went to specific wallets.
- Surface C found historical sells concentrated in early wallets.
- A sale-and-rebuy pattern is suspected (same pool, short interval, new recipients).
- A proceeds question ("how much did the launch wallets take out") is asked; this track supplies sales,
  `references/deep-tracks/proceeds-reconciliation.md` supplies the asset-level reconciliation.

## Minimum evidence

| Item | Source | Why |
|---|---|---|
| Frozen packet with `P1`, and a historical pin `P2` at or just after the launch window (`purpose: historical`) | `scripts/rpc_probe.py --block <launch_block>` | cohort membership and allocation are read at a block, not "at launch" |
| Launch transaction(s), factory/curve/pool addresses and their creation logs | packet `deployment`; surface E | the inclusion rule references exact contracts |
| Per-address inbound/outbound token ledger over the cohort window | `references/deep-tracks/transfer-replay.md` (full) or a bounded `eth_getLogs` on the token filtered by `to`/`from` topics with declared windows | transfers are the spine of every row |
| Pool/curve swap logs and the platform's buy/sell events for the same window | `references/platforms/uniswap-v3.md`, `uniswap-v4.md`, `pons-style-launches.md` | a sale is proven by pool or curve mechanics, never by a router transfer |
| Receipts for every transaction counted as a sale, rebuy, or proceeds movement | `eth_getTransactionReceipt` (status `0x1`) | reverted transactions are not sales |

## Procedure

1. Define the cohort before measuring anything, and write the definition as a `discovery` row. Five fields
   are mandatory; without any one of them the cohort is an anecdote:
   - **inclusion rule**: for example "addresses that received the token from `<curve|pool|launch_platform>`
     or from the launch transaction between block L and L+N", or "recipients named in the allocation
     calldata of tx `<hash>`". Name the contracts by address.
   - **time bounds**: block range, with the reason for the end bound (first canonical pool swap, end of the
     curve phase, a fixed number of blocks).
   - **sources**: which logs, calls and platform events produced the membership list.
   - **exclusions**: pools, routers, the platform/curve, lockers, burn addresses, the token itself, known
     bridges; list each excluded address with its role.
   - **coverage**: the windows actually fetched and any gaps (`limitation_id`).
   Membership derived from an explorer label or a website list is `explorer`/`website` provenance and must be
   re-derived from logs before it counts.

2. Read allocation. For each member, record the first inbound amount, its transaction, the sender role
   (`curve`, `pool`, `launch_platform`, `deployer`, `treasury`, `other`) and whether the transfer was
   automatic platform behavior or a manually supplied exception (an explicit recipient argument, an exemption
   list entry). For Pons-style launches decode the direct curve buy: the **recipient argument** of the buy
   call and the tax treatment applied to that recipient decide who received the benefit; the transaction
   sender is a separate role (`launch_signer`) unless it is also the recipient. See
   `references/platforms/pons-style-launches.md`.

3. Build the per-wallet accounting table. One row per member; every non-zero cell cites at least one
   receipt or decoded log. All amounts in integer base units per asset.

   | Column | Definition | Proof standard |
   |---|---|---|
   | allocation | first inbound amount(s) inside the window | `log_decoded` + `calldata` for the recipient |
   | transfers_in / transfers_out | token moves to/from other addresses (not pool/curve) | `log_decoded`; recipient role classified |
   | sales | token in to pool/curve **and** quote asset out to a recipient in the same receipt, with the pool `Swap`/curve sell event amounts matching | `receipt` + `log_decoded`; native output needs a `trace` or the router's `Withdrawal`+value transfer |
   | rebuys | quote in to pool/curve and token out to a recipient, by this wallet or by a wallet it funded | same as sales |
   | downstream_inventory | addresses that received token from this wallet, with their balance at `P1` | `log_decoded` + `rpc_state` |
   | proceeds_by_asset | quote assets received from sales, per asset, per recipient | `log_decoded`/`trace` |
   | fees | transfer tax, platform fee, LP fee share, gas (gasUsed × effectiveGasPrice) | `receipt` |
   | retained | `balanceOf` at `P1` plus downstream inventory still held by addresses this wallet funded, stated separately | `rpc_state` |

4. Prove each sale from mechanics, not from direction. A row is a sale only when the receipt shows: status
   `0x1`; a token `Transfer` from the wallet (or its router hop) into the pool or curve; the pool `Swap`
   (v2/v3/v4 signature per `references/deep-tracks/pool-position-history.md` table) or curve sell event whose
   input equals that transfer (net of tax); and the quote asset leaving the pool to a recipient. Record the
   recipient of the quote asset — it is frequently not the seller. A token transfer to a router, an aggregator,
   or a launch platform with no swap event in the receipt is a **transfer**, not a sale (it may be an add-
   liquidity, a failed swap wrapped in a multicall, or custody).

5. Detect and describe market-mediated redistribution. When a member's sale and another address's buy occur
   in the same pool within a short interval, list both receipts, state that no direct transfer between the
   two addresses was observed (or cite it if one was), and describe the pair as "market-mediated
   redistribution: wallet A sold N into pool P at tx X; wallet B bought M from pool P at tx Y". Do not write
   "A transferred to B via the pool", "wash trade", or "the team moved tokens to fresh wallets" unless a
   direct transfer, a shared signer action, or an admin action independently establishes it
   (`references/deep-tracks/operational-attribution.md`).

6. Trace funding sources with neutral roles. For each member find the first inbound native-asset transfer
   (gas funding) and its sender; classify the sender as `funder` (EOA), `exchange_deposit`/exchange
   withdrawal address (only with `explorer_label` provenance stated), `bridge`, `router`, or `other`. A shared
   funder across members is a fact to record, not a conclusion about common ownership.

7. Treat zero balance as a state, not an exit. A wallet at zero may have transferred to another wallet,
   added liquidity, staked, bridged, or burned; each is a different row. Classify every outbound leg; only
   proven sales enter `sales`.

8. Aggregate with denominators. Cohort totals for allocation, sales, rebuys, proceeds and retained are stated
   as absolute base units and as a share of the cohort's own allocation and of `totalSupply()` at `P2` and
   `P1`. State exclusions used for each denominator (surface D rules).

9. Hand off. Proceeds rows per wallet per asset go to `references/deep-tracks/proceeds-reconciliation.md`
   with the opening block = first inbound block and closing block = `P1`. Any role attribution stronger than
   the neutral roles goes to `references/deep-tracks/operational-attribution.md`.

## Stopping condition

- **Complete**: every member row is filled from receipts for the declared window through `P1`, cohort totals
  reconcile (allocation + transfers_in + rebuys = transfers_out + sales + fees + retained, per wallet, with
  any unexplained remainder bounded and stated), and every sale cites the pool/curve mechanics.
- **Bounded**: the question concerned a subset (top-k allocations, a single wallet); stop at that subset and
  say what was excluded and why.
- **Blocked**: logs, traces or historical reads are unavailable for part of the window; the affected rows are
  `unknown`, totals are stated as lower bounds on the covered portion only, and no inference fills the gap.

## Output rows

SYNTHETIC EXAMPLE — placeholders; not a live finding.

Discovery row (the cohort definition):
```json
{ "discovery_id": "S7",
  "claim": "Cohort C1 = addresses that received <token> from <curve> or in launch tx <hash> in blocks L..L+<N>",
  "search_universe": "eth_getLogs address=<token> topic0=Transfer topic1=<curve|launch sender> on chain <chain_id>; curve Buy events on <curve>",
  "block_range": "<L>-<L+N>",
  "pagination": "windows in artifacts/cohort-ranges.json",
  "inclusion_rule": "first inbound Transfer from <curve> or from launch tx; recipient argument of direct buys, not tx sender",
  "exclusions": "<pool> (pool), <router> (router), <curve> (curve), <locker> (locker), 0x000…dEaD (burn)",
  "materiality_threshold": "none for membership; per-wallet rows limited to allocations >= <int> base units (stated)",
  "coverage": "full for L..L+N; downstream inventory read at P1" }
```

Evidence rows:
```json
{ "evidence_id": "E61", "chain_id": <chain_id>, "address": "<pool>", "pin_id": null,
  "tx_hash": "<tx hash>", "block_number": <block>, "evidence_type": "receipt",
  "artifact": "artifacts/receipt-<tx>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getTransactionReceipt", "tx": "<tx hash>"},
  "decoding_basis": "status 0x1; token Transfer wallet->pool <int>; pool Swap amount0In/amount1Out (v2 signature); quote Transfer pool-><recipient>",
  "summary": "sale of <int> base units for <int> <quote> delivered to <recipient>" }

{ "evidence_id": "E62", "chain_id": <chain_id>, "address": "<curve>", "pin_id": null,
  "tx_hash": "<launch tx>", "block_number": <block>, "evidence_type": "calldata",
  "artifact": "artifacts/tx-<hash>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getTransactionByHash", "tx": "<launch tx>"},
  "decoding_basis": "buy selector <0x…> per curve proven source; recipient = arg 2; sender = tx.from",
  "summary": "direct buy recipient <addr> differs from signer <addr2>; tax exemption applied per <event>" }
```

Check rows:
```json
{ "check_id": "T-COHORT-DEFINITION", "surface": "historical_launch_integrity",
  "name": "Cohort defined with inclusion rule, time bounds, sources, exclusions and coverage before measurement",
  "status": "pass", "severity": null, "evidence_ids": ["E62"], "finding_ids": [], "reason": null, "pin_id": "P2" }

{ "check_id": "T-COHORT-SALES", "surface": "historical_launch_integrity",
  "name": "Each counted sale is proven by receipt and pool/curve mechanics with the quote recipient identified",
  "status": "finding", "severity": "medium", "evidence_ids": ["E61"], "finding_ids": ["F31"], "reason": null, "pin_id": null }

{ "check_id": "T-COHORT-REBUYS", "surface": "historical_launch_integrity",
  "name": "Rebuys and new-recipient buys following cohort sales are enumerated and described as market-mediated redistribution",
  "status": "pass", "severity": null, "evidence_ids": ["E61"], "finding_ids": [], "reason": null, "pin_id": null }

{ "check_id": "T-COHORT-FUNDING", "surface": "historical_launch_integrity",
  "name": "Funding source of each member classified with neutral roles",
  "status": "unknown", "severity": null, "evidence_ids": [], "finding_ids": [],
  "reason": "native-value funding needs trace_filter/internal transfers; provider lacks trace methods (L8)", "pin_id": null, "limitation_id": "L8" }

{ "check_id": "T-COHORT-RETAINED", "surface": "current_concentration",
  "name": "Retained cohort inventory at P1 (direct plus downstream) stated with denominators",
  "status": "pass", "severity": null, "evidence_ids": ["E61"], "finding_ids": [], "reason": null, "pin_id": "P1" }
```

Finding row:
```json
{ "finding_id": "F31", "surface": "historical_launch_integrity", "severity": "medium",
  "proposition": "Cohort C1 (<n> addresses, <pct>% of supply at P2) sold <int> base units (<pct>% of its allocation) into <pool> in <k> receipt-proven transactions between blocks <a> and <b>; <int> <quote> was delivered to <m> recipient addresses; <pct>% of the allocation is retained at P1 directly or by funded downstream addresses.",
  "chain_id": <chain_id>, "address": "<pool>", "pin_or_tx": "<first sale tx hash>",
  "evidence_ids": ["E61", "E62"], "evidence_type": "receipt", "confidence": "strongly_supported",
  "alternatives": "Buys by new addresses after the sales are market-mediated redistribution; common control of sellers and buyers is not established by timing or shared funding.",
  "coverage": "cohort window L..L+N full; <k> sale receipts in E61, pin_or_tx cites the first; retained share read at P1; funding sources partial (L8)",
  "stale_conditions": "retained share changes with any transfer after P1; historical rows do not go stale",
  "is_historical": true }
```

Unresolved questions:
- "Whether members funded by the same `funder` address are under common control (no signer or admin link found)."
- "Whether the quote asset delivered to <recipient> was subsequently sold, bridged or deposited (handed to
  proceeds reconciliation; commingling stop may apply)."

## What remains unknown if it cannot be completed

- The share of the launch allocation that was sold versus moved, so `historical_launch_integrity` stays
  `unknown` or `partial`, and no percentage may be quoted as if it were complete.
- Whether current top holders are cohort members' downstream addresses (surface D concentration keeps
  `unclassified` buckets).
- Whether observed sale/rebuy pairs involve the same operator; the only supportable description remains market-
  mediated redistribution.
- Proceeds totals, since sales could not be proven; the proceeds track cannot start on router transfers.

## Common mistakes

- Measuring before defining: choosing members after seeing who sold.
- Counting a transfer to a router, aggregator, or the launch platform as a sale.
- Attributing a Pons-style direct buy to the transaction sender instead of the recipient argument, and
  applying the sender's tax status to it.
- Calling a wallet at zero balance "cashed out".
- Writing "transferred via the pool" or "washed" for a sale followed by a stranger's buy.
- Netting cohort sales against later rebuys and reporting only the net, hiding that the intermediate buyers
  were different addresses.
- Quoting proceeds as profit: cost basis, platform fees, transfer taxes, gas and retained inventory belong
  in the row before any such word (and the word stays out unless all four are defensible).
- Using explorer "sniper"/"insider" labels as membership; they are discovery, re-derive from logs.
- Forgetting the historical pin `P2`: allocation shares need `totalSupply()` at the launch block, not at `P1`.
