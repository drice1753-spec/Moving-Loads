# Workflow modes

Three modes share one packet, one evidence discipline and one manifest format; they differ in how much of
the surface map is opened and in when work stops. Choose the mode from the user's question, record it in
`mode`, and do not change it mid-run (a focused question that turns out to need everything becomes a new
broad run with the same frozen packet).

## Focused mode

Rule: investigate the asked question and its necessary dependencies; do not expand into a full audit.
Output: `verdict`, at least one check, ratings only for surfaces touched, and a report with `## Verdict`,
`## Coverage and limitations`, `## Evidence ledger` (other sections optional).

Procedure:
1. Build and freeze the packet (`references/target-packet.md`). Even a narrow question needs the pin.
2. Write the question as a proposition set: what would have to be true for "yes", what for "no".
3. List dependencies that can change the answer and nothing else. A dependency is in scope when the
   answer is conditional on it (who can withdraw from the vault is a dependency of "is this backing
   available", not of "did this balance come from fees").
4. Collect evidence for the propositions with preferred-class types; record limitations as they occur.
5. Answer in bounded language, state what was not examined, and stop.

Worked example: "Did this vault balance come from LP fees?"
1. Packet: token, vault (role `vault`), canonical pool(s) (role `pool`), position manager / position NFT
   (roles `position_manager`, `position_nft`), the fee asset(s). Pin P1.
2. Propositions: (a) the vault's balance of asset X at P1 is Y base units; (b) inflows to the vault over
   [deployment, P1] are traceable, receipt-level, to fee collection on the identified position(s); (c) any
   remainder is bounded and its origin named or marked unexplained.
3. Evidence:
   - `balanceOf(vault)` at the opening block and at P1 (`rpc_state`).
   - Vault inflows: `eth_getLogs` for `Transfer(address,address,uint256)` with topic2 = vault over the
     range, paginated; record the range and pagination in `discovery[]` (`log_decoded`).
   - For each inflow tx, the receipt (`receipt`) and the fee-collection log in the same tx or in the
     forwarding chain: for Uniswap v3, `Collect` on the position manager (tokenId, recipient, amount0,
     amount1) and, when the recipient is a forwarder, the forwarder's outgoing Transfer to the vault; for
     v4, the position manager / hook collection path per `references/platforms/uniswap-v4.md`; for v2 there
     is no discrete Collect - fees are realized only on burn, so "fee origin" must be computed from the
     burn's share of reserves versus principal, or reported as not separable.
   - Distinguish vault holdings (balance at P1), pool inventory (tokens still inside the pool), and
     unclaimed fees (`tokensOwed`/position state at P1): only the first is the vault's balance.
4. Reconcile with `scripts/reconcile.py`. Flows file (integer base units; reverted rows are excluded by
   the tool; transforms are paired):
   ```json
   {
     "asset": {"chain_id": "<observed via eth_chainId>", "address": "0x<fee asset>", "unit": "base units"},
     "opening": {"balance": "0", "block": 1000},
     "closing": {"balance": "1500000000000000000", "block": 2000},
     "rows": [
       {"tx": "0x<64 hex>", "status": "success", "direction": "in", "amount": "1000000000000000000", "block": 1500,
        "counterparty": "0x<position manager or forwarder>", "note": "Collect tokenId=<id> amount1 -> forwarded to vault (E7,E8)"},
       {"tx": "0x<64 hex>", "status": "success", "direction": "in", "amount": "500000000000000000", "block": 1800,
        "counterparty": "0x<other>", "note": "no Collect in tx or forwarding chain; origin unexplained"}
     ]
   }
   ```
   `python3 <skill-root>/scripts/reconcile.py --flows flows.json --tolerance 0 --json` reports the
   unexplained delta and exits 1 when it exceeds the tolerance.
5. Answer (bounded): "Of the vault's <Y> base units of <asset> at P1 (block N), <A> are traced
   receipt-level to `Collect` events on position <id> and forwarded to the vault (E7-E12); <B> arrived
   from <counterparty> with no fee-collection event in the transaction or its forwarding chain and remain
   unexplained; coverage: Transfer logs blocks [a, b], paginated in 5,000-block windows, no limitations."
   Do not add token-control findings, sellability, or launch history unless the user asked.

## Broad mode

Rule: screen every surface, deepen only on triggers, and produce a layered conclusion. All 22 core checks
must be present (any status) and all 11 ratings set.

Procedure (priority order; each step can change the verdict alone, so it runs before the next):
1. Packet and architecture pass.
2. Authority discovery first: surface A (`A-MINT`, `A-UPGRADE`, `A-SEIZE`, `A-RESTRICT`, `A-TAX`,
   `A-EXTCALL`, `A-ADMIN`) and surface B (`B-CANON`, `B-PRINCIPAL`, `B-SIDE`). No monetary threshold.
3. Then C (`C-HIST-SELL`, `C-QUOTE`) and D (`D-SUPPLY`, `D-CONC`).
4. Then E (`E-LAUNCH`), F (`F-FEES`, `F-TREASURY`), G (`G-REWARDS`, `G-RIGHTS`), H (`H-UTILITY`,
   `H-DEPS`, `H-DEV`).
5. Deepen only on the escalation triggers listed in each surface file (Step 4 table in `SKILL.md`).
   Time-box each deep track and record what remains unknown at the stop.
6. Layered conclusion: verdict under the requirement frame; per-surface ratings; key findings; strongest
   contrary evidence; unresolved questions; what would change the conclusion; recommendations tied to
   observed deficiencies.

Stopping rules per check: stop when the proposition is established (`pass`/`finding`) with preferred
evidence at the pin; or when it is blocked by a limitation (`unknown` citing `L<n>`); or when the surface
is shown not to exist (`not_applicable` with evidence of absence in `reason`). Never stop A or B early on
cost grounds.

## Formal mode

Rule: reconcile the manifest first, then generate the report and visuals from it. Never re-run research
to format it.

Procedure:
1. Take the completed broad manifest. Run `python3 <skill-root>/scripts/validate_report.py --manifest
   manifest.json --report report.md` (a draft report may exist) and `python3 <skill-root>/scripts/
   ledger.py check --manifest manifest.json`; fix linkage errors in the manifest, not in the prose.
2. Generate the report sections from manifest objects: `verdict` -> `## Verdict`; `target` + pins ->
   `## Target identity`; `ratings` -> `## Ratings`; `findings` -> `## Key findings`;
   `verdict.strongest_contrary_evidence` etc. -> their sections; `coverage` + `discovery` ->
   `## Coverage and limitations`; `declarations` -> `## Declarations`;
   `python3 <skill-root>/scripts/ledger.py render --manifest manifest.json --format md` -> `## Evidence ledger`.
3. Legitimate visuals (each labeled with pin id, denominator, and evidence ids):
   - concentration bars with explicit denominators and exclusions (total supply vs circulating float;
     pools, lockers, burn, treasury excluded or shown separately);
   - a quote-depth curve at the pin: input size vs expected output and per-unit degradation, route named,
     with failures marked as failures, not as zero;
   - a flow diagram from `reconcile.py` output with the unexplained delta drawn as its own bounded box.
   Illegitimate: any chart that implies a trend, prediction, or a "score".
4. Set `report.sha256` to the final file's hash and re-validate. A change to a number in the report is a
   change to the manifest first.

## Parallel lanes

Use when authorized parallel agents exist. Every lane receives the same frozen packet (checked by its
sha256) and a bounded scope; lanes return rows, not reports.

| Lane | Surfaces / checks | Deep track it may open on trigger |
|---|---|---|
| `token-controls` | A: `A-MINT` `A-UPGRADE` `A-SEIZE` `A-RESTRICT` `A-TAX` `A-EXTCALL` `A-ADMIN` | `proxy-bytecode` |
| `liquidity-custody` | B: `B-CANON` `B-PRINCIPAL` `B-SIDE` | `pool-position-history` |
| `sellability` | C: `C-HIST-SELL` `C-QUOTE` | (simulation only behind `fork_guard.py`) |
| `supply-concentration` | D: `D-SUPPLY` `D-CONC` | `transfer-replay` |
| `launch-history` | E: `E-LAUNCH` | `launch-cohort` |
| `fees-treasury` | F: `F-FEES` `F-TREASURY` | `proceeds-reconciliation` |
| `rewards-backing` | G: `G-REWARDS` `G-RIGHTS` | `reward-epoch` |
| `dependencies-dev` | H: `H-UTILITY` `H-DEPS` `H-DEV` | `dependency-redemption`, `operational-attribution` |

Lane input: the frozen packet (path + sha256), the assigned check ids, the mode, and the shared cache file
(read-only for lanes, or one cache per lane merged afterwards).

Lane output (manifest fragment, ids prefixed with the lane name; no prose report):
```json
{
  "lane": "liquidity-custody",
  "packet_sha256": "<hash of the frozen packet>",
  "pin_ids_used": ["P1"],
  "scope_addresses": [ ...manifest scope entries... ],
  "evidence":  [ {"evidence_id": "lc:E1", ...}, ... ],
  "checks":    [ {"check_id": "B-PRINCIPAL", "evidence_ids": ["lc:E1"], "finding_ids": ["lc:F1"], ...}, ... ],
  "findings":  [ {"finding_id": "lc:F1", "evidence_ids": ["lc:E1"], ...}, ... ],
  "limitations": [ {"limitation_id": "lc:L1", ...} ],
  "discovery":   [ {"discovery_id": "lc:S1", ...} ],
  "unresolved_questions": ["..."]
}
```
Lanes never set ratings or the verdict: a rating needs the merged basis.

Merge procedure:
1. Verify every fragment's `packet_sha256` equals the frozen hash; reject any that differ.
2. Concatenate scope addresses; deduplicate by (chain_id, address, role); on conflicting
   `runtime_status`/`code_hash` for the same address, keep the `rpc_derived` value and log the conflict.
3. Renumber ids globally in lane order (`token-controls` first): `lc:E1` -> `E14`, `lc:F1` -> `F6`,
   `lc:L1` -> `L3`, `lc:S1` -> `S2`. Keep the mapping table in `tooling.merge_map` and rewrite every
   reference (`evidence_ids`, `finding_ids`, `limitation_id`, `metadata.*.evidence_id`).
4. Deduplicate evidence by (chain_id, artifact_sha256, query); point all references at the survivor.
5. Resolve conflicting findings by evidence class: preferred onchain > `source_verified` >
   `simulation_counterfactual` > discovery-class. Within a class, current-state findings prefer the later
   pin, historical findings prefer the receipt. If still unresolved, keep both propositions with
   `alternatives` filled and rate on the worse one until resolved; never average.
6. Compute the 11 ratings from the merged checks, then the verdict. Validate.

## Sequential fallback

Same packet, one agent, this order: packet -> A -> B -> C -> D -> E -> F -> G -> H -> deep tracks
opened along the way -> ratings -> verdict -> validation. Stopping rules: in focused mode stop when the
propositions are answered; in broad mode apply the per-check stopping rules above; hard stop on chain
mismatch; time-box each deep track and write "what remains unknown" before moving on. Record the order
actually followed in `tooling.sequence` so a reviewer can see what was still open when a later surface
was rated.

## Batching independent reads

Group all pinned `eth_call`/`eth_getStorageAt`/`eth_getCode` reads for a surface and run them in one pass
before decoding; run `eth_getLogs` per topic with bounded ranges; run one selector scan per distinct code
hash. Do not interleave reads with narrative; the narrative is written from rows.

## Cache key

chain/address/block/query. Implementation: `ddcore.ResponseCache` keys sha256(host, method, params),
where params carry the address, the pinned block hex and the calldata or filter; the chain is bound by
reading `eth_chainId` live before the cache file is used and by never caching floating block tags. One
cache file per target packet; do not reuse a cache across chains.
