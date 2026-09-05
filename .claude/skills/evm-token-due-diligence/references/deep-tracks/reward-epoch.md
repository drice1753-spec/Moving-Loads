# Deep track: reward-epoch accounting and backlog modeling

Track code `EPOCH`. Check ids `T-EPOCH-<SLUG>`. Feeds surface G (`G-REWARDS`, `G-RIGHTS`) and the
`reward_accounting_liveness` rating; asset-level closure comes from
`references/deep-tracks/proceeds-reconciliation.md`, and any reward paid in a synthetic asset continues in
`references/deep-tracks/dependency-redemption.md`. Run it because a distributor's balance and a dashboard's
"total distributed" cannot show whether holders are owed more than exists, whether anyone still processes
epochs, or whether the same entitlement was paid twice; only an epoch-by-epoch model tested against logs can.

## Trigger

- Rewards, reflections, revenue share, staking yield or "backing" are advertised or observed in code
  (`G-REWARDS` found a distributor, vault, or accrual logic).
- Payments are batched, epoch-based, keeper-driven, or claim-based with a queue.
- Distributed amounts look larger or smaller than the observed funding, or a dashboard figure disagrees with
  onchain balances.
- Processing appears stalled (no distribution event for a period that matters to the requirement frame).
- A holder-rights question ("what am I owed, can I get it") depends on the accounting being correct.

## Minimum evidence

| Item | Source | Why |
|---|---|---|
| Distributor/vault/reward-token addresses with `code_hash`, proxy status and admin chain at `P1` | `scripts/rpc_probe.py`; `references/deep-tracks/proxy-bytecode.md` rung 1 | an upgradeable distributor's rules can change; say so |
| The entitlement rule as executed (proven source or decoded runtime) | `source_verified` after `proxy-bytecode.md` rung 2; else `selector_scan.py` plus traced calls (`source_unverified`) | the model must come from code, not from documentation |
| Complete distribution/claim/funding event logs for the modeled range | paged `eth_getLogs` on the distributor and on the reward asset (`Transfer` to/from the distributor) | conservation and duplicates are log questions |
| Reward-asset balances of the distributor at the opening block and at `P1` | `eth_call balanceOf` / `eth_getBalance` at those blocks | retained inventory |
| Receipts for processing transactions (caller, gas, status) | `eth_getTransactionReceipt` | liveness is about who calls, not who could |
| Requirement frame (what "live enough" means for the user) | target packet `requirement_frame` | backlog is rated against a stated need, not an absolute |

## Procedure

1. Write the epoch model in three parts, each cited to code or logs, before computing anything.
   - **Funding**: what enters the reward pot per epoch and from where — fee forwarding from a pool or
     position manager (match to `Collect` rows from `references/deep-tracks/pool-position-history.md`),
     treasury transfers, minting by the reward token, donations, carryover of an unpaid remainder. Record the
     funding asset and whether it is the underlying asset or a synthetic (a token minted by the system).
   - **Entitlement rule**: per-share of a snapshot balance, time-weighted, tiered, whitelist-only, minimum
     holding, exclusions (pools, contracts, blacklisted addresses), rounding direction, cumulative caps, and
     whether entitlement is computed at claim time or at processing time.
   - **Processing**: who may trigger (anyone, a keeper address, an admin), per-epoch or per-claim, batch
     limits, gas ceilings, pause/disable switches, and what state advances (`lastProcessedEpoch`,
     `cursor`, `currentIndex` or equivalents — read them at `P1` through the layout or a view).

2. Declare discovery: contracts, topics (from the proven ABI; there is no standard reward event signature),
   block range from distributor deployment (or the question's start) to `P1`, windows recorded.

3. Reconstruct the funding ledger: every inflow to the distributor per asset with its source class (fee
   collection, treasury, mint, donation, unknown) and receipt; every outflow with its class (payment, admin
   withdrawal, fee, transfer to another system). Run `scripts/reconcile.py` per asset with opening balance at
   the opening block and closing at `P1`; the identity `opening + inflows + adjustments = payments + other
   outflows + retained + unexplained` is the conservation test.

4. Reconstruct entitlements per epoch: for each processed epoch, the snapshot or accrual inputs (holder
   balances at the epoch block via `eth_call` at that block, or the contract's recorded snapshot), the rule
   applied, and the resulting entitlement per account. Where the contract stores per-account accrual
   (`earned(address)`-style views), read them at `P1` for a deterministic sample and for every scope address.

5. Test conservation: Σ entitlements for processed epochs must equal Σ payments + carried unpaid remainder
   (within the rounding the rule specifies). Payments exceeding funding are not a contradiction if prefunding,
   donations, carryover or minting exist — state which and cite the inflow rows; "distributed more than was
   bought" is not by itself a finding, and "distributed less than funded" is not by itself fraud.

6. Test cumulative caps and duplicates: compare cumulative payments per account and in total against any
   cap in the rule; group payment logs by `(epoch, account)` (or the contract's claim key) and list any key
   paid twice with both receipts; check that a re-entrant or re-processable epoch cannot pay again (a
   processed-flag read at `P1`, or a counterfactual on a verified fork if the question is material).

7. Model the backlog and liveness:
   - **unpaid amounts**: Σ entitlements accrued − Σ paid, per asset, at `P1`, and the distributor's retained
     balance against it (shortfall = unpaid − retained, if positive);
   - **last processed epoch** vs the epoch implied by `P1`'s timestamp or block; epochs pending;
   - **keeper dependence**: the set of addresses that triggered processing historically, their share of
     triggers, whether processing is permissionless in code, the native balance of the keeper at `P1`, and
     the interval since its last call;
   - **admin gating**: pause flags, `setDistributor`/`setRewardToken`-style setters, withdraw/rescue paths
     for the pot, and who holds them (link to `A-ADMIN` and the proxy track).
   Rate liveness against the requirement frame: "processing observed every N blocks over the range; last
   trigger at block b by <keeper>; permissionless: yes/no".

8. Distinguish the asset delivered. If payments are in a synthetic (a token minted by the reward system, a
   receipt token, an internal credit) rather than the underlying asset, record the synthetic's address and
   controls, and hand the "can it be redeemed" question to `references/deep-tracks/dependency-redemption.md`;
   do not describe a synthetic payment as a payment of the underlying.

9. Hand off: asset files to proceeds reconciliation; per-holder entitlement reads to surface G rights;
   admin gating to surface A.

## Stopping condition

- **Closed**: the model is cited to code, funding reconciles per asset within stated tolerance,
  entitlements conserve for the covered epochs, caps and duplicates are tested, and backlog/liveness are
  stated with numbers and the keeper set at `P1`.
- **Bounded**: the question concerned one epoch range or one asset; stop there and declare the exclusion.
- **Blocked**: the rule cannot be established (unverified source, opaque runtime), historical balances are
  pruned, or logs are incomplete; the affected checks are `unknown` with limitations, and no figure from a
  dashboard or website substitutes for the missing computation.

## Output rows

SYNTHETIC EXAMPLE — placeholders; not a live finding.

Evidence rows:
```json
{ "evidence_id": "E91", "chain_id": <chain_id>, "address": "<reward_distributor>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "rpc_state",
  "artifact": "artifacts/epoch-state-P1.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_call", "to": "<reward_distributor>", "calls": ["lastProcessedEpoch()", "currentEpoch()", "paused()", "rewardToken()"], "block": "<P1 hex>"},
  "decoding_basis": "selectors from proven source (T-PROXY-SOURCE-CORRESPONDENCE pass); uint256/bool/address returns",
  "summary": "lastProcessedEpoch <n>, currentEpoch <n+k>, paused false, rewardToken <addr>" }

{ "evidence_id": "E92", "chain_id": <chain_id>, "address": "<reward_distributor>", "pin_id": null, "tx_hash": null,
  "block_number": null, "evidence_type": "log_decoded",
  "artifact": "artifacts/epoch-payments.jsonl", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getLogs", "address": "<reward_distributor>", "topics": ["<Paid/Claimed topic0 from ABI>"], "windows": "artifacts/epoch-ranges.json"},
  "decoding_basis": "event <Name>(uint256 indexed epoch, address indexed account, uint256 amount) from proven source",
  "summary": "<n> payments across <e> epochs; <d> duplicate (epoch, account) keys" }

{ "evidence_id": "E93", "chain_id": <chain_id>, "address": "<reward_distributor>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "log_decoded",
  "artifact": "artifacts/flows-<chain>-<distributor>-<asset>.json", "artifact_sha256": "<sha256>",
  "query": {"tool": "scripts/reconcile.py", "args": "--flows flows-<chain>-<distributor>-<asset>.json --tolerance 0 --json"},
  "decoding_basis": "Transfer logs to/from distributor; inflow classes by counterparty role",
  "summary": "funding <int> (fees <int>, treasury <int>, mint <int>); payments <int>; retained <int>; unexplained 0" }
```

Check rows:
```json
{ "check_id": "T-EPOCH-MODEL", "surface": "reward_accounting_liveness",
  "name": "Funding, entitlement rule and processing are established from executed code and logs",
  "status": "pass", "severity": null, "evidence_ids": ["E91", "E92"], "finding_ids": [], "reason": null, "pin_id": "P1" }

{ "check_id": "T-EPOCH-CONSERVATION", "surface": "reward_accounting_liveness",
  "name": "Entitlements for processed epochs equal payments plus carried remainder; funding reconciles per asset",
  "status": "pass", "severity": null, "evidence_ids": ["E92", "E93"], "finding_ids": [], "reason": null, "pin_id": "P1" }

{ "check_id": "T-EPOCH-DUPLICATES", "surface": "reward_accounting_liveness",
  "name": "No (epoch, account) key paid more than once; cumulative caps respected",
  "status": "finding", "severity": "medium", "evidence_ids": ["E92"], "finding_ids": ["F61"], "reason": null, "pin_id": null }

{ "check_id": "T-EPOCH-BACKLOG", "surface": "reward_accounting_liveness",
  "name": "Unpaid entitlements, pending epochs and retained inventory quantified at P1",
  "status": "finding", "severity": "high", "evidence_ids": ["E91", "E93"], "finding_ids": ["F62"], "reason": null, "pin_id": "P1" }

{ "check_id": "T-EPOCH-LIVENESS", "surface": "reward_accounting_liveness",
  "name": "Processing cadence, trigger set, permissionlessness and admin gating established over the range",
  "status": "unknown", "severity": null, "evidence_ids": [], "finding_ids": [],
  "reason": "processing calls emit no event and trace_filter is unavailable; caller set unknown (L11)", "pin_id": "P1", "limitation_id": "L11" }

{ "check_id": "T-EPOCH-ASSET-DELIVERED", "surface": "utility_redemption_rights",
  "name": "Asset actually delivered to holders identified as underlying or synthetic",
  "status": "pass", "severity": null, "evidence_ids": ["E92"], "finding_ids": [], "reason": null, "pin_id": "P1" }
```

Finding rows:
```json
{ "finding_id": "F61", "surface": "reward_accounting_liveness", "severity": "medium",
  "proposition": "Epoch <n> paid account <addr> twice (txs <a>, <b>) for <int> base units each; the processed flag for epoch <n> read at P1 is false, so the epoch remains re-processable.",
  "chain_id": <chain_id>, "address": "<reward_distributor>", "pin_or_tx": "<tx b>",
  "evidence_ids": ["E92", "E91"], "evidence_type": "log_decoded", "confidence": "proven",
  "alternatives": "The rule may intend split payments per epoch; the proven source shows a single-payment path with no split logic.",
  "coverage": "all payment logs deployment..P1 full", "stale_conditions": "an upgrade changing the processing path", "is_historical": true }

{ "finding_id": "F62", "surface": "reward_accounting_liveness", "severity": "high",
  "proposition": "At P1 accrued entitlements exceed payments by <int> base units of <asset>; the distributor holds <int>, a shortfall of <int>; <k> epochs are unprocessed and the only historical trigger address last called at block <b>.",
  "chain_id": <chain_id>, "address": "<reward_distributor>", "pin_or_tx": "P1",
  "evidence_ids": ["E91", "E93"], "evidence_type": "rpc_state", "confidence": "strongly_supported",
  "alternatives": "Prefunding by a later treasury transfer or minting could cover the shortfall; no such inflow observed to P1. The shortfall is an accounting state, not proof of intent.",
  "coverage": "funding and payments full; caller set partial (L11)",
  "stale_conditions": "any funding inflow, processing call, or rule change after P1", "is_historical": false }
```

Unresolved questions:
- "Who triggers processing and whether they are funded; calls emit no event (L11)."
- "Whether the synthetic reward token <addr> is redeemable for <underlying> (handed to dependency-redemption)."

## What remains unknown if it cannot be completed

- Whether holders are owed more than exists (`reward_accounting_liveness` cannot be `low`; `G-REWARDS` stays
  `unknown`).
- Whether payments have been duplicated or capped incorrectly, and whether processing will continue without
  a specific keeper or admin.
- Whether "total distributed" figures published anywhere are true; they remain `dashboard`/`website` claims.
- Whether the amount delivered to holders was the underlying asset at all.

## Common mistakes

- Taking the entitlement rule from documentation or a website instead of executed code.
- Calling "distributed > purchased" a red flag without checking prefunding, donations, carryover and minting.
- Treating the distributor's balance as "backing" for accrued entitlements without comparing it to unpaid
  amounts and checking who can withdraw it.
- Counting a payment in a synthetic token as a payment of the underlying.
- Concluding liveness from a permissionless `process()` function that nobody has called.
- Measuring cadence over a range that omits the most recent period before `P1`.
- Reading `earned()`-style views at `latest` alongside logs cut at `P1`.
- Ignoring rounding direction, then reporting dust remainders as conservation failures (or real shortfalls
  as rounding).
- Rating liveness without a requirement frame; "stalled" means nothing without "for whom, for how long".
