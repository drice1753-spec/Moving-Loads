# Surface G - Rewards, vaults, backing, and redemption

Rating keys: `reward_accounting_liveness` (core check `G-REWARDS`) and `utility_redemption_rights`
(core check `G-RIGHTS`, shared with `H-UTILITY`). Sub-checks: `G-INVENTORY-VS-LIABILITY`,
`G-FEE-ORIGIN`, `G-CONSERVATION`, `G-LIVENESS`, `G-EXIT-ROUTE`. Read this file for any question about
"backed by", "rewards", "revenue share", "redeem", "vault", "staking", or "did these holdings come from
LP fees"; always in `broad` mode. Custody of the reward layer's admin keys is rated under
`admin_treasury_reward_custody` with the reads from `references/surfaces/A-token-controls.md`.

## Purpose

Keep three things apart that marketing merges: INVENTORY (what a vault or treasury holds at P1),
LIABILITIES (what holders are owed under the code's own accounting), and PROMISES (what documents and
posts say). Then test which holder rights are ENFORCEABLE - a code path any holder can call that
delivers a named asset - and what the holder actually receives, in what units, after which fees,
delays, caps, approvals and admin dependencies, and whether the received asset itself has an exit.

## What can change the verdict

- A vault balance that is encumbered, admin-withdrawable, denominated in the token itself, or made of
  synthetic claims: it is not available backing for holders, whatever the balance shows.
- A redemption path that exists in code but depends on an administrator (funding by allowance,
  keeper processing, unpausing, oracle updates) that has stopped or can stop.
- Conservation failure: paid out more than was funded plus minted plus carried in (duplicate or
  over-payment), or entitlements owed that no inventory covers (unpaid backlog).
- The asset actually delivered on exit being a receipt token, LP share or NFT with no proven exit of
  its own.
- A "from LP fees" claim where the receipt-level `Collect`/fee events do not account for the vault's
  inflows (donations, prefunding, purchases or minting make up the rest).
- Coverage: a pruned range for `Collect` or `Claimed` events, or a reward contract without
  corresponding source, leaves entitlement and conservation `unknown`.

## Procedure

Inventory reads cite P1. Liability reads cite P1 and the contract's own accounting functions.
Flow reconstructions cite tx hashes and a stated block range. Promises are recorded as `website`,
`dashboard`, `repository` or `token_metadata` evidence and never upgraded to rights.

1. Inventory the reward/vault/backing layer from the architecture pass: vault(s), reward distributor,
   staking contract, claim contract, the asset each holds and the asset each pays. Resolve each as in
   surface A (proxy status, admin, pausability, roles): an immutable token behind an upgradeable
   reward layer is rated on the reward layer's upgrade authority (behavioral example 3 in
   `references/examples/behavioral-examples.md`).
2. `G-INVENTORY-VS-LIABILITY` - build three ledgers at P1:
   - inventory: `balanceOf(vault)` for every asset held (ERC-20 via `eth_call`, native via
     `eth_getBalance`, positions via the position manager), preserved raw;
   - liabilities: what the contract says holders are owed - `earned(address)`, `claimable(address)`,
     `pendingRewards`, `totalClaimable()`, per-epoch entitlements, unclaimed epoch totals; when only
     per-holder reads exist, sum over the holder set from surface D and state the coverage;
   - promises: each quantitative claim in docs/website/posts, quoted, with retrieval date, recorded as
     untrusted evidence.
   Then classify each inventory asset as available or not: `encumbered` (owed to someone else,
   collateral, locked), `admin_withdrawable` (a `withdraw`/`rescue`/`sweep` path held by a key -
   name it), `self_denominated` (the project's own token or its own receipt token), `synthetic`
   (a claim on another contract, e.g. a receipt, an LP share, a bond NFT), `raw` (the asset holders
   are promised, unencumbered). Backing ratio = raw available inventory / liabilities, with both
   sides and the assets stated; an LP share or a synthetic is reported at face value only alongside
   its own exit test (step 5).
   ```
   python3 <skill-root>/scripts/rpc_probe.py --address 0x<vault> --chain-id N --block <P1 block> \
       --call "owner()" --call "paused()" --call "totalAssets()" --call "totalSupply()" \
       --cache rpc-cache.json --out packet-G-vault.json
   ```
3. `G-RIGHTS` - enforceable rights test. For each promised right, find the code path and fill one
   row: `right | function (selector) | who can call (any holder / whitelisted / admin only) | asset
   delivered (raw / synthetic claim / LP share / NFT) | conversion units (rate, formula, who sets the
   rate, oracle) | fees on exit | timing (vesting, cooldown, epoch, lock) | caps (per holder, global,
   cumulative) | approvals needed (which token, to which spender) | admin dependencies | exit route of
   the delivered asset`. A right is `enforceable` only when an ordinary holder can call the path at P1
   and the receipt would deliver the asset without a privileged action first. A right delivered by an
   offchain process (API, manual payout, "we will distribute") is a promise; rate it as such.
   Corroborate with behavior: find at least one historical successful claim receipt by a non-privileged
   caller whose balance delta in the delivered asset matches the contract's accounting.
4. `G-ADMIN-DEPENDENCY` - for each path, record: pausable (`paused()`, `whenNotPaused` guard);
   admin-funded (the contract pays from its own balance vs pulls via `transferFrom` from an admin
   wallet's allowance - read `allowance(admin, contract)` and the admin balance at P1: a right funded
   by allowance is a right the admin can revoke by one call); keeper-processed (claims require a prior
   `processEpoch`/`distribute`/`checkpoint` by a keeper role - name the keeper, its funding, its last
   call); oracle-dependent (rate read from an oracle - surface H); upgradeable (the path can be
   replaced). Each dependency is a stale condition for the right.
5. `G-EXIT-ROUTE` - for the asset actually delivered: if raw and traded, quote it under
   `references/surfaces/C-sellability.md` at holder-sized amounts; if a synthetic claim, LP share or
   NFT, repeat step 3 on that contract (what does redeeming IT deliver, who can, what fees, what
   liveness) until a raw asset with a quoted exit is reached or the chain ends in a promise. Report
   the depth of the chain and where it ends. A synthetic reward is not redeemable until this chain
   closes on a raw asset (behavioral example 5).
6. `G-FEE-ORIGIN` - answer "did these holdings come from LP fees?" by reconciliation, not by label:
   - fee collections at receipt level: v3 pool `Collect(owner, recipient, tickLower, tickUpper,
     amount0, amount1)` and position-manager `Collect(tokenId, recipient, amount0, amount1)`; v4 fee
     deltas from `ModifyLiquidity`/hook events; the curve/platform fee events for launch pools - over a
     stated range, paginated, with coverage;
   - forwarding transfers: `Transfer` logs from the collect recipient to the vault (or the vault as the
     direct recipient), matched by tx hash or by amount and block when forwarding is a separate tx;
   - vault inflows: every `Transfer` to the vault in the range, classified as `fee-origin` (matched to
     a collect), `donation/prefunding` (from a funder or treasury), `purchase` (from a swap), `mint`
     (from the zero address), `carryover` (opening balance), `unmatched`.
   Distinguish the three quantities the question conflates: vault holdings (`balanceOf(vault)` at P1),
   pool inventory (the position's liquidity - not the vault's, and only convertible by decreasing
   liquidity: surface B), and unclaimed fees (`tokensOwed0/1` from `positions(tokenId)` plus fee growth
   since the last poke; `tokensOwed` updates only when the position is touched, so read it at P1 and
   state that accrued-but-unpoked fees are excluded or computed from `feeGrowthInside`). Build one
   `reconcile.py` flows file per asset for the vault (format and command in
   `references/surfaces/F-fees-treasury.md`, step 7) with the `note` field carrying the origin class;
   the fee-origin share is `sum(fee-origin in) / sum(all in)` over the range, with the unmatched share
   stated.
7. `G-CONSERVATION` and the distribution tests, when a distribution is material:
   - conservation: `sum(paid) <= funded + minted + carry-in` per asset over the range, where paid is the
     sum of `Claimed`/`RewardPaid`/`Transfer`-out to holders, funded is inflows from the funder or fee
     forwarding, minted is `Transfer` from the zero address to the distributor, carry-in is the
     opening balance; a violation is an over-payment or a mis-decoded event - resolve which;
   - `G-ENTITLEMENT`: recompute a sample of claims from the code's formula with pinned inputs (balance
     or stake at the snapshot block, epoch total, share) and compare with the paid amount in the
     receipt; a mismatch is a finding about the code, the snapshot, or the decoding;
   - cumulative caps: total paid vs `maxRewards`/`cap` reads; per-holder caps vs per-holder sums;
   - `G-DUPLICATE`: the same (holder, epoch) paid twice, or the same claim id consumed twice;
   - unpaid amounts: entitlements recorded but never paid (`claimable` at P1 summed over holders vs
     inventory), and epochs funded but never processed;
   - retained inventory: funded + minted + carry-in - paid - liabilities at P1 = what the contract
     keeps, and who can withdraw it (step 4).
   Do NOT assume `distributed <= purchases` or `distributed <= fees`: prefunding, donations, carryover
   from a prior epoch, and minting all let distributions exceed the period's income legitimately; the
   test is conservation against ALL sources, and each source is named.
8. `G-LIVENESS` - processing liveness: last epoch processed (`currentEpoch()`, `lastProcessed`,
   `lastUpdateTime`) vs P1's timestamp; the epoch length; the keeper address, its native balance for
   gas, and the block of its last successful call (`eth_getLogs` on the processing event, or the
   keeper's recent receipts); what happens when the keeper stops (claims halt? accrue? expire?). A
   distributor whose last processing is older than one epoch at P1 is a liveness finding even when the
   balances look full. Add the keeper as a scope address with role `role_holder` or `other`.
9. Write the rows. Inventory, liability and rights findings bind to P1; flow and conservation findings
   bind to tx hashes and the range (`is_historical: true`). Rate `reward_accounting_liveness` from
   conservation and liveness; rate `utility_redemption_rights` from the enforceable-rights and
   exit-route rows; keep the reward layer's admin custody in `admin_treasury_reward_custody`.

### Verify-at-use table

| Item | Verify at use time by |
|---|---|
| Position manager / pool addresses for `Collect` events | `Collect` topic0 and emitter in a preserved receipt; `references/platforms/uniswap-v3.md`, `references/platforms/uniswap-v4.md` |
| Event signatures (`Claimed`, `RewardPaid`, `Distributed`, epoch events) | signature from corresponding source only; otherwise decode by observed topic0 and word layout and mark the basis `raw` |
| Oracle or rate source used for conversion units | `eth_call` of the rate function at P1 plus the oracle address read from the contract (`references/surfaces/H-utility-dependencies-dev.md`) |
| Keeper address | `from` of the last processing receipt, not a docs page |
| Epoch length and snapshot rule | contract reads at P1 or corresponding source; docs are corroboration |

## Checks

| check_id | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|
| G-REWARDS | Reward/vault/backing/redemption accounting: entitlement rules, conservation, and processing liveness hold over the stated range and at P1 | inventory and liability reads at P1 + event sums over the range + last-processed read | `rpc_state`, `log_decoded`, `receipt` | any claim/process/fund tx after P1; upgrade of the reward layer |
| G-RIGHTS | Each promised right is either enforceable by a holder-callable code path delivering a named asset, or is a promise; the delivered asset is identified | function guard + one successful non-privileged claim receipt with a matching balance delta | `bytecode`, `receipt`, `rpc_state` | pause, upgrade, allowance revocation, oracle change |
| G-INVENTORY-VS-LIABILITY | Inventory (by availability class), liabilities and promises are separated and the backing ratio names both sides | `balanceOf`/`eth_getBalance` per asset + liability reads at P1 | `rpc_state`, `website` (promises, untrusted) | any vault inflow/outflow or claim after P1 |
| G-FEE-ORIGIN | The share of vault inflows traceable to receipt-level fee collections over the range, with unmatched share stated | `Collect`/fee events -> forwarding transfers -> vault inflows, reconciled | `log_decoded`, `receipt` | new inflows after the closing block |
| G-CONSERVATION | `sum(paid) <= funded + minted + carry-in` per asset, with each source named | event sums + opening balance | `log_decoded`, `rpc_state` | any distribution after the range |
| G-LIVENESS | Last processed epoch/checkpoint is current at P1 and the keeper is funded and active | last-processed read + keeper's last receipt + keeper balance | `rpc_state`, `receipt` | one epoch elapsing without processing |
| G-EXIT-ROUTE | The asset delivered on exit has a proven route to a raw asset with a quoted exit, or the chain ends in a promise | per-hop rights rows + a `C-QUOTE` on the terminal asset | `rpc_state`, `receipt`, `bytecode` | any change on any hop |
| G-ADMIN-DEPENDENCY | Pause, admin funding by allowance, keeper processing, oracle and upgrade dependencies of each right are listed with holders | guard reads + `allowance` + keeper reads at P1 | `rpc_state`, `bytecode` | any admin action |
| G-ENTITLEMENT | A sample of paid claims recomputes from the code's formula and pinned inputs | sample receipts + snapshot reads | `receipt`, `rpc_state`, `source_verified` | formula change (upgrade) |
| G-DUPLICATE | No (holder, epoch) or claim id was paid twice in the range | claim event replay keyed by (holder, epoch/id) | `log_decoded` | none for the range |

Statuses follow the manifest rules: `pass` and `finding` need evidence ids; `unknown`/`skipped` need a
reason with the limitation id. `unknown` is never a pass; a vault with an unread liability side cannot
support a `low` rating.

## Common false positives and negatives

False positives (a backing or reward problem that is not one):
- Distributions exceeding the period's fee income when prefunding, donations, carryover or minting
  explain the difference (name the source; conservation holds).
- A large `claimable` backlog on a distributor that is pull-based by design and fully funded; the
  finding is unpaid only if inventory does not cover it or processing has stalled.
- A vault balance in an LP share reported as "not backing" without testing the share's exit; report
  it as synthetic with the exit test result, not as worthless.
- A paused claim path during a disclosed migration window, when the unpause authority and timing are
  read; still a dependency, rated by the authority holder, not automatically critical.
- `tokensOwed` at zero read as "no fees": the position may simply not have been poked since the last
  collect; compute from fee growth or state the exclusion.

False negatives (a backing or reward fact missed):
- Backing denominated in the project's own token or its receipt token counted at face value.
- A right that passes the selector check but whose funding is an admin wallet's allowance, revocable
  in one call.
- A keeper that stopped: balances full, `claimable` current, but no epoch processed since well before
  P1 - always read the last processing block.
- Vault holdings taken as "from LP fees" because the vault is the `Collect` recipient, when most
  inflows came from a treasury transfer; only the receipt-level match counts.
- Fees that were collected to an intermediary and forwarded in a later tx, missed by matching on tx
  hash only; match by amount and block window, and state the matching rule.
- Promises quoted from a dashboard as liabilities: the dashboard is untrusted; the liability is what
  the contract's accounting returns.
- A synthetic reward whose issuer is a proxy the same admin can upgrade; the exit chain has an
  upgrade dependency at that hop (surface A rules apply to every hop).
- Conservation tested on one asset while payouts occur in another (rewards paid in quote from a
  vault funded in the token and swapped); reconcile each asset and the swap legs as transforms.

## Escalation triggers to deep tracks

- Epoch-based distribution, any conservation or entitlement mismatch, a backlog, or a keeper more
  than one epoch behind: `references/deep-tracks/reward-epoch.md` (epoch accounting and backlog
  modeling with a stopping condition).
- Any redemption or backing chain longer than one hop, any external asset, oracle, bridge or lending
  system in the exit route, or a synthetic claim as inventory:
  `references/deep-tracks/dependency-redemption.md`.
- "Did these holdings come from LP fees?" when forwarding is indirect or the position changed hands:
  `references/deep-tracks/pool-position-history.md` and `references/deep-tracks/proceeds-reconciliation.md`.
- Reward contracts that mint, rebase or rewrite balances: `references/deep-tracks/transfer-replay.md`.
- Reward layer that is a proxy, has unverified source, or dispatches through a fallback:
  `references/deep-tracks/proxy-bytecode.md`.
- A decisive redemption test that needs a write: only on a fork verified by
  `python3 <skill-root>/scripts/fork_guard.py --rpc http://127.0.0.1:8545 --expect-chain-id N --out attestation.json`,
  with a synthetic account, and the result requires a successful receipt PLUS the intended asset's
  balance delta - a success flag or an event alone is insufficient; label the result counterfactual.

## How to state results

Name the ledger each number belongs to (inventory, liability, promise), the availability class, the
asset delivered, and the point where an exit chain ends.

- "At P1 vault 0x.. holds 812.5 WETH (E3) and 4.2M of the token (E4). Liabilities: `totalClaimable()`
  = 640.0 WETH (E5). Availability: the WETH is raw and unencumbered but `withdraw(address,uint256)` is
  callable by `owner()` = 0x.. (EOA, E6), so it is admin-withdrawable; the token balance is
  self-denominated and excluded from backing. Backing ratio on raw available inventory: 812.5 / 640.0
  (WETH), subject to the admin withdrawal path."
- "Holders can call `claim()` (any address with a non-zero `earned`) and receive rWETH, a receipt
  token issued by 0x.. (E8). Redeeming rWETH for WETH requires `redeem(uint256)` on 0x.., which is
  `paused()` = true at P1 (E9) and unpausable only by 0x... The right to WETH is not currently
  enforceable; the right to rWETH is."
- "Fee-origin reconciliation, WETH, blocks N..M (coverage full): `Collect` events on position #123
  total 41.2 (E12); forwarding transfers to the vault matched 41.2 (E13); vault inflows in range 96.0
  (E14). Fee-origin share 42.9%; 53.1 (55.3%) came from treasury 0x.. in 2 transfers (E15); unmatched
  1.7 (1.8%)."
- "Conservation over blocks N..M: paid 300.0 <= funded 250.0 + minted 0 + carry-in 80.0 (E16-E18).
  Paid exceeds the period's fee income of 120.0 because 130.0 was prefunded by 0x..; this is a
  named source, not an anomaly. Last epoch processed: 41 at block M-5,000; epoch length 7 days; P1 is
  19 days later, so two epochs are unprocessed (G-LIVENESS `finding`, medium): the keeper 0x.. has a
  native balance of 0 at P1 (E19)."
- "Unknown because historical state was unavailable: `Collect` logs for blocks N..N+30,000 returned
  `rpc_pruned` (limitation L2); G-FEE-ORIGIN is `unknown` for that range and the fee-origin share is a
  lower bound."
- Never: "fully backed"; "rewards are guaranteed"; "holders own the vault"; "revenue share" without
  the delivered asset; "the vault came from fees" from a recipient label.
