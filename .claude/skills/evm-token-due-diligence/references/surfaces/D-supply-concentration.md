# Surface D - Supply and concentration

Rating key: `current_concentration`. Core checks: `D-SUPPLY`, `D-CONC`. Read this file for whale or
holder concentration, "how much does the team hold", supply reconciliation, or any size input needed by
surface C; always in `broad` mode. Historical launch questions belong to surface E
(`references/surfaces/E-launch-integrity.md`); this file compares launch-time and current concentration
only after both are measured on the same definitions.

## Purpose

Reconcile the token's supply and material balances with a method that matches its accounting model,
then classify every material balance into a named bucket and report concentration against explicit
denominators with explicit exclusions, at P1. Keep units that are not the same thing (raw asset,
rebasing units, shares, synthetic claims, LP shares, custody balances) in separate rows so that no
figure is ever the sum of unlike quantities.

## What can change the verdict

- `totalSupply()` at P1 does not equal the sum of balances derived from the Transfer replay, or
  balances change without Transfer events (rebasing, rewrites, shares): the holder set and every size
  input downstream are then unreliable until explained.
- A few non-custody addresses hold a large share of the circulating float; or the largest holders
  are creator/launch wallets; or an "investor-like" balance turns out to be a bridge escrow or an
  exchange deposit address (or the reverse).
- The denominator: the same balance is 5% of total supply and 40% of a float that excludes pool,
  locker and treasury custody; only the stated denominator makes the number meaningful.
- Burn addresses that are contracts with a withdraw path, or "burned" supply that is actually a
  locker or a vault.
- Launch-time concentration that persists, or that changed only through market-mediated
  redistribution (surface E / `references/attribution.md`).
- Coverage: a Transfer replay with a block-range gap cannot support any holder statement beyond the
  covered range.

## Procedure

Every current-state read cites P1; the replay range is deployment block .. P1, declared as a
`discovery` row with pagination and gaps.

1. Fix the accounting model (`target.metadata.accounting_model`) from surface A's code findings and
   from behavior, not from the name: `standard_erc20`, `rebasing`, `fee_on_transfer`, `shares_based`
   (vault-style share token), `wrapper`, or `unknown`. If A found rebase/rewrite/shares paths, the model
   is not standard even if no such call has happened yet.
2. `D-SUPPLY` - reconcile with the method for the model:
   - standard ERC-20: replay `Transfer(address,address,uint256)` from deployment to P1 (`eth_getLogs`,
     topic0 = `ddcore.keccak256_hex(b"Transfer(address,address,uint256)")`, windowed, paginated);
     mints are transfers from the zero address, burns are transfers to the zero address (a token may
     also emit no Transfer on burn-to-dead: treat `0x..dEaD` as a holder, not as a burn, in the replay);
     check (a) mints - burns = `totalSupply()` at P1, (b) sum of derived non-negative balances =
     `totalSupply()`, (c) `balanceOf` at P1 equals the derived balance for every material address and a
     random sample of small ones; record each residual;
   - rebasing: reconcile SHARES (`sharesOf`/`totalShares` or the share-transfer event the token emits)
     rather than units; record the units-per-share rate at P1 and state balances in both units and
     shares; the Transfer event stream in units will not reconcile and that is expected, not a finding;
   - fee-on-transfer: if the token emits a Transfer for the tax leg, replay reconciles; if it deducts
     silently, per-address residuals equal cumulative tax paid - confirm the tax recipient's balance
     absorbs the sum;
   - wrapper: reconcile the wrapper's supply against `balanceOf(wrapper)` on the underlying at P1
     (1:1 or the stated rate); the wrapper's holders are holders of a claim on the wrapper's custody;
   - shares-based vault token (ERC-4626-style): shares reconcile like standard ERC-20; assets do not
     (`totalAssets()`, `convertToAssets(shares)` at P1 are a separate unit row); the share-to-asset rate
     is admin- or market-dependent (surface G);
   - non-Transfer balance changes (`D-NONTRANSFER-CHANGES`): when (b) or (c) fails and the model does
     not explain it, the balance mapping was written by a path that emitted no Transfer. Require storage
     diffs (`eth_getStorageAt` of the balance slot for affected addresses at two pinned blocks; find the
     slot by keccak256(pad(address) . pad(slotIndex)) against a known balance) or traces of the
     suspected calls; escalate to `references/deep-tracks/transfer-replay.md`. Do not "fix" the holder
     table by trusting `balanceOf` alone; state which figure is used and why.
3. Build the holder set from the replay (`event_derived`), then confirm every material balance with
   `balanceOf(address)` at P1:
   ```
   python3 <skill-root>/scripts/rpc_probe.py --address 0x<token> --chain-id N --block <P1 block> \
       --call "totalSupply()" --call "balanceOf(address):0x<holder1>" --call "balanceOf(address):0x<holder2>" \
       --cache rpc-cache.json --out packet-D-balances.json
   ```
   Explorer holder pages are `explorer` evidence (discovery only); never take a balance from them.
   Resolve runtime status for each material holder (`eth_getCode` at P1) - contract vs EOA decides the
   bucket candidates below.
4. `D-BUCKETS` - classify each material balance into exactly one bucket, with the evidence for the
   classification:
   - pool custody: pool addresses from surface B (`B-CANON`, `B-SIDE`), including v4 PoolManager
     balances attributed to pool ids only via pool-scoped reads;
   - protocol custody: staking, vesting, distributor and other protocol contracts holding supply on
     behalf of others (surface G identifies liabilities);
   - lockers: token lockers/vesting lockers (not LP lockers, which hold LP shares);
   - treasuries: fee recipients and treasury contracts/wallets from surface F;
   - burn addresses: the zero address and conventional dead addresses with empty code; a contract at
     a "burn" address is not a burn until its withdraw paths are excluded;
   - creator allocations: deployer, launch signer, and wallets funded by the launch allocation
     (surface E defines the cohort; use its rows, do not invent one);
   - investor-like balances: non-custody addresses not otherwise classified (a neutral label; it does
     not assert intent or identity);
   - bridge escrow: canonical bridge contracts holding supply for other chains (verify via bridge
     `Deposit`/`Lock` receipts and the destination-chain supply; if unverified, keep the balance in
     `investor-like` with a note);
   - exchange deposit addresses: explorer-labeled only; provenance `explorer_label`, listed as a
     separate discovery-only bucket, never used to lower concentration without on-chain corroboration;
   - unclassified: everything else material, with the reason.
   Each bucket entry becomes a scope address with the matching role (`pool`, `vault`, `locker`,
   `treasury`, `burn`, `deployer`/`launch_signer`, `holder`, `bridge`, `exchange_deposit`, `other`).
5. Declare the denominators and exclusions, then compute every metric on at least two of them:
   - total supply (`totalSupply()` at P1, model-adjusted as in step 2);
   - circulating float, DEFINED in the report: total supply minus the buckets you exclude (state each:
     burn, pool custody, protocol custody, lockers, treasuries, bridge escrow); different exclusions
     give different floats; publish the one used and the alternatives;
   - optionally, "tradable float": circulating float minus creator allocations still held.
6. `D-UNITS` - produce the units table (one row per unit; never summed across rows):

   | Unit | What it counts | Read at P1 from | Convertible to raw asset by |
   |---|---|---|---|
   | raw asset | the token itself in base units | `balanceOf`, `totalSupply` | identity |
   | rebasing units | units shown to holders under the current rate | `balanceOf` | rate x shares (rate is admin/market dependent) |
   | shares | share-based claim on a pool of assets | `sharesOf`, `totalShares`, ERC-4626 `balanceOf` | `convertToAssets` at P1; depends on redemption liveness (surface G) |
   | synthetic claims | IOUs, receipt tokens, points, reward balances | the issuing contract | only via the issuer's redemption path (surface G) |
   | bond/NFT shares | positions represented by NFTs or bonds | position/bond contract reads | contract-specific; often time-locked |
   | LP shares | v2 LP tokens, v3/v4 position liquidity | pair `balanceOf`, `positions(tokenId)` | removal (surface B); value depends on range and price |
   | custody balances | raw asset held by pools, lockers, vaults, bridges, treasuries | `balanceOf(custodian)` | depends on the custodian's rules |
   | total supply | all raw units in existence | `totalSupply()` | denominator only |
   | circulating float | total supply minus declared exclusions | computed | denominator only |

7. `D-CONC` - concentration metrics, each tagged with denominator, exclusions and pin:
   - top-1, top-5, top-10, top-20 shares of total supply AND of circulating float;
   - the same metrics with pool custody included and excluded (a pool-heavy token looks concentrated
     on one basis and dispersed on the other);
   - Gini coefficient optional; if reported, state the formula, the address universe (all non-zero
     balances from the replay, or holders above a dust threshold - say which) and the exclusions;
   - count distinct addresses, not owners: shared funding, timing or routers do not prove common
     ownership (`references/attribution.md`); if a cohort is proposed, it comes from surface E with its
     inclusion rule.
8. `D-HIST-VS-CURRENT` - compare launch concentration with current concentration on identical
   definitions: take a historical pin (`P2`, `purpose: historical`) at launch block + a stated offset,
   rebuild buckets and denominators at P2 (surface E supplies launch allocations and the cohort), then
   report the same metrics at P2 and P1 side by side. Describe changes as "market-mediated
   redistribution" unless a stronger claim is independently established; a wallet reaching zero is
   not a cash-out (surface E / `references/deep-tracks/launch-cohort.md`).
9. Write the rows: replay coverage as a `discovery` row (range, windows, gaps -> limitation ids);
   reconciliation residuals as evidence (`log_decoded` + `rpc_state`) and, where unexplained, a finding;
   the bucket table and the concentration table as evidence with every input address, denominator and
   pin; the rating from the float-based metrics with the creator-allocation share called out.

### Verify-at-use table (never assert from memory)

| Item | Why it matters | How to verify at use time |
|---|---|---|
| Burn addresses (zero address, conventional dead addresses) | exclusion from float | `eth_getCode` empty at P1; the zero address is special-cased by the token's `Transfer` semantics (check whether burns emit to it); a dead address is conventional, not provable |
| Bridge escrow contracts | bucket, cross-chain supply | a bridge `Deposit`/`Lock` receipt on this chain plus the destination-chain mint/receipt with its own pin; without both, do not classify as escrow |
| Exchange deposit labels | discovery only | never verified on-chain; keep as `explorer_label` provenance and outside the concentration denominator adjustments |
| Balance mapping slot index | storage diffs for non-Transfer changes | derive by hashing `pad(address) . pad(slotIndex)` and matching `eth_getStorageAt` to a known `balanceOf` at P1 |
| Chain id | every read | `eth_chainId` live (rpc_probe.py); a same-symbol token on another chain is never the target |

## Checks

| check_id | surface | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|---|
| D-SUPPLY | current_concentration | Supply reconciles under the model-appropriate method: mints - burns = totalSupply; derived balances sum to totalSupply; `balanceOf` confirms material derived balances; residuals explained | replay coverage row + the three checks with residuals + `totalSupply()` at P1 | `log_decoded`, `rpc_state` | any Transfer/mint/burn/rebase after P1 |
| D-CONC | current_concentration | Concentration stated with denominator, exclusions and pin, on total supply and on the defined float, pool-inclusive and pool-exclusive | bucket table + metric table + definitions | `rpc_state` | any material balance change after P1 |
| D-UNITS | current_concentration | Raw asset, rebasing units, shares, synthetic claims, bond/NFT shares, LP shares, custody balances, total supply and float are reported as distinct rows and never summed across | units table with a read per row | `rpc_state` | rate change, redemption rule change |
| D-NONTRANSFER-CHANGES | current_concentration | Balances do not change without Transfer events; where they do, the mechanism is identified from storage diffs or traces | residual analysis + storage diffs/traces for affected addresses | `rpc_storage`, `trace`, `log_decoded` | any rebase/rewrite call after P1 |
| D-BUCKETS | current_concentration | Every material balance is in exactly one bucket with on-chain basis (labels are discovery only) | per-address runtime status + classification evidence | `rpc_state`, `log_decoded`, `explorer` (discovery) | balance movement between buckets |
| D-HOLDER-CONFIRM | current_concentration | Every material derived balance equals `balanceOf` at P1 | `balanceOf` reads for all material addresses + sample of small ones | `rpc_state` | any transfer after P1 |
| D-HIST-VS-CURRENT | current_concentration | Launch-time concentration and current concentration are compared on identical bucket and denominator definitions | P2 (historical) pin + bucket/metric tables at P2 and P1 | `rpc_state`, `log_decoded` | new historical evidence changing the cohort |
| D-REPLAY-COVERAGE | current_concentration | The replay covered deployment..P1 without gaps (or gaps are declared with limitation ids) | discovery row with windows, pagination, gap list | `log_decoded`, `manual_note` | block range advances |

## Common false positives and negatives

False positives (concentration overstated or a discrepancy invented):
- Counting pool custody, a locker or a bridge escrow as a whale; counting the PoolManager singleton's
  balance as one pool's or one holder's.
- Calling a shares/rebasing unit mismatch a "supply discrepancy" when the model explains it.
- A replay residual caused by an `eth_getLogs` window gap (a coverage limitation) reported as hidden
  minting.
- Treating several addresses funded from one source as one owner.

False negatives (concentration understated or a discrepancy missed):
- Excluding "team", "treasury" or "burn" balances on the basis of labels or a website; a contract at
  a dead-looking address with a withdraw path; "burned" supply that sits in a vault.
- Using explorer holder pages or `balanceOf` alone without the replay (rewrites and shares hide
  there).
- Reporting only the total-supply denominator when most supply is in pool custody or lockers.
- Ignoring supply held on other chains via bridges (state it as out of scope or pin the other chain).
- Comparing launch and current concentration on different bucket definitions.

## Escalation triggers to deep tracks

- Any unexplained reconciliation residual, balances changing without Transfer events, a rebasing or
  shares model that must be replayed in shares, or a historical holder question ("who held X at block
  B?"): `references/deep-tracks/transfer-replay.md`.
- Creator/launch cohort definition, early transfers and sales, "did they cash out":
  `references/deep-tracks/launch-cohort.md` (surface E owns the cohort definition).
- Supply minted by a reward distributor with epochs, or claims backed by a vault:
  `references/deep-tracks/reward-epoch.md` and `references/deep-tracks/dependency-redemption.md`
  (surface G).
- Treasury or fee-wallet balances that must reconcile to receipts:
  `references/deep-tracks/proceeds-reconciliation.md` (surface F).
- A holder that must be described as an operator or controller (never an identity):
  `references/deep-tracks/operational-attribution.md`.

## How to state results

- "At P1, `totalSupply()` is S (E40). Transfer replay over blocks A..B (S2; 1 window gap, L5) derives
  mints - burns = S and a balance sum equal to S; `balanceOf` confirms the 25 material addresses
  (E41-E43). D-SUPPLY: pass; D-REPLAY-COVERAGE: unknown for the gap."
- "Concentration at P1: top-10 non-custody addresses hold 31% of total supply and 58% of circulating
  float (float = total supply minus burn, pool custody, LP-locker and treasury buckets; definitions and
  inputs in E44). Pool custody is 42% of total supply. Creator-allocation wallets (cohort defined in
  E-LAUNCH, E30) hold 12% of float."
- "Balances changed without Transfer events for 3 addresses between P2 and P1 (residuals in E45);
  storage diffs of the balance slots show writes in transactions 0x.., 0x.. calling `setBalance` (A-SEIZE,
  E46). D-NONTRANSFER-CHANGES: finding, severity high."
- "The address 0x.. holding 9% of float is explorer-labeled as an exchange deposit (E47,
  `explorer_label`). This is discovery-only; it is reported as investor-like in the metrics and the
  label is noted as an alternative."
- "Unknown because historical state was unavailable: the replay could not cover blocks X..Y (L5,
  `rpc_pruned`); balances derived for that range are unconfirmed and `current_concentration` cannot be
  rated `low`."
- Never: "well distributed" without denominator and exclusions; "team holds nothing" from a label;
  "supply is fixed" when A found a mint or rebase path.
