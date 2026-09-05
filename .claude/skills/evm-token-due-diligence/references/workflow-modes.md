# Workflow modes

Three modes share one packet, one evidence discipline and one manifest format; they differ in how much of
the surface map is opened and in when work stops. Choose the mode from the user's question, record it in
`mode`, and do not change it mid-run (a focused question that turns out to need everything becomes a new
broad run with the same frozen packet).

Selection rule: a request for an overall judgement ("is it a rug", "is it safe", "should I buy") ->
`broad`; a request about one mechanism or one flow ("did X come from Y", "can the owner mint", "can I
sell 2%") -> `focused`; when the request mixes both, run `broad` and answer the focused question first
in the verdict; a finished broad manifest plus "write the report/deck" -> `formal`. Requirement frame:
take the user's horizon and size verbatim; when none was given, use "rug resistance and exit at the
stated size over a 30-day hold", write it into `verdict.requirement_frame` and label it an assumption in
the verdict.

## Focused mode

Rule: investigate the asked question and its necessary dependencies; do not expand into a full audit.
Output: `verdict`, at least one check, ratings only for surfaces touched, and a report with `## Verdict`,
`## Coverage and limitations`, `## Evidence ledger` (other sections optional).

Procedure:
1. Build and freeze the packet (`references/target-packet.md`). Even a narrow question needs the pin.
2. Write the question as a proposition set: what would have to be true for "yes", what for "no".
3. List dependencies that can change the answer and nothing else. A dependency is in scope when the
   answer is conditional on it (who can withdraw from the vault is a dependency of "is this backing
   available", not of "did this balance come from fees"). The architecture pass is limited to these
   dependencies; the rest is recorded as an `out_of_scope` known limitation.
4. Collect evidence for the propositions with preferred-class types; record limitations as they occur.
5. Answer in bounded language, state what was not examined, and stop.

Worked example: "Did this vault balance come from LP fees?"
1. Packet: the target is the TOKEN. If only the vault was supplied, derive the token from the vault's
   position or pool (`positions(tokenId)` -> token0/token1, or the pool key), confirm chain id + address
   with the user, and probe the token. Scope: token (`token`), vault (role `vault`), canonical pool(s)
   (role `pool`), position manager / position NFT (roles `position_manager`, `position_nft`), the fee
   asset (WETH: role `other`, label "quote asset (wrapped native)", provenance `rpc_derived` from
   `token0()/token1()`), and every inflow counterparty named in the answer (`funder`, `treasury`,
   `holder`, or `other` with a neutral label) - `--strict` rejects unscoped addresses in the report body.
   Pin P1.
2. Propositions: (a) the vault's balance of asset X at P1 is Y base units; (b) inflows to the vault over
   [deployment, P1] are traceable, receipt-level, to fee collection on the identified position(s); (c) any
   remainder is bounded and its origin named or marked unexplained.
3. Evidence:
   - `balanceOf(vault)` at the opening block and at P1 (`rpc_state`).
   - Vault inflows: `eth_getLogs` for `Transfer(address,address,uint256)` with topic2 = vault over the
     range, paginated; record the range and pagination in `discovery[]` (`log_decoded`).
   - Wrap inflows: the fee asset may be a wrapped native, and a wrapped-native `deposit()` emits
     `Deposit(address,uint256)` (topic0 `0xe1fffcc4...9109c`, `references/chains/chain-verification.md`
     section 5) and NO `Transfer`. Also query `Deposit` with topic1 = vault (and `Withdrawal` for the
     reverse); each `Deposit` is a `transform_in` row in the WETH file paired with a `transform_out` row
     of the same tx in the native file, whose origin is then traced by `debug_traceTransaction` /
     `trace_transaction` to a `Collect`/hook fee payout in native (v4 native pools, unwrapping routers,
     curve fee payouts). Without a trace API, wrap-derived WETH is `unmatched` with a limitation `L<n>`
     (`api_unavailable`), never `fee-origin`.
   - For each inflow tx, the receipt (`receipt`) and the fee-collection log in the same tx or in the
     forwarding chain: for Uniswap v3, `Collect` on the position manager (tokenId, recipient, amount0,
     amount1) and, when the recipient is a forwarder, the forwarder's outgoing Transfer to the vault; for
     each `Collect`, read `positions(tokenId)` at P1 (or at the collect block if the id was later burned)
     and keep the row as `fee-origin` only when token0/token1 include the target and the pool matches
     B-CANON/B-SIDE - collections from other pools are classed `fee-origin-other-pool` and reported
     separately; for v4, the position manager / hook collection path per
     `references/platforms/uniswap-v4.md`; for v2 there is no discrete Collect - fees are realized only
     on burn, so "fee origin" must be computed from the burn's share of reserves versus principal, or
     reported as not separable.
   - Distinguish vault holdings (balance at P1), pool inventory (tokens still inside the pool), and
     unclaimed fees (`tokensOwed`/position state at P1): only the first is the vault's balance.
4. Reconcile with `scripts/reconcile.py`, one flows file per asset (WETH file and native file). Canonical
   flows shape (integer base units as decimal strings; reverted value rows are excluded by the tool; the
   gas of a reverted tx is a separate `gas` row with `status: "success"` - a `gas` row marked reverted is
   excluded and draws W-GAS-ROW-REVERTED; supply both files in one run so transforms pair by tx hash
   ACROSS the asset files; a leg with no counter-leg in any supplied file is a warning):
   ```json
   { "asset": {"chain_id": 8453, "address": "0x<fee asset>", "is_native": false, "symbol": "WETH", "decimals": 18},
     "opening": {"balance": "0", "block": 1000}, "closing": {"balance": "1500000000000000000", "block": 2000},
     "rows": [ {"tx": "0x<64 hex>", "status": "success", "direction": "in", "amount": "1000000000000000000", "block": 1500,
                "counterparty": "0x<position manager or forwarder>", "note": "Collect tokenId=<id> amount1 -> forwarded to vault (E7,E8)"},
               {"tx": "0x<64 hex>", "status": "success", "direction": "transform_in", "amount": "300000000000000000", "block": 1700,
                "counterparty": null, "note": "WETH Deposit by the vault; native leg in flows-native.json (E13)"},
               {"tx": "0x<64 hex>", "status": "success", "direction": "in", "amount": "200000000000000000", "block": 1800,
                "counterparty": "0x<other>", "note": "no Collect in tx or forwarding chain; origin unexplained"} ] }
   ```
   Native file: `"asset": {"chain_id": 8453, "address": null, "is_native": true, "symbol": "ETH",
   "decimals": 18}`; `gas` rows are accepted only there and a native file must not carry an address (the
   tool exits 2 on any other shape). Directions: `in|out|transform_in|transform_out|gas|adjustment`.
   The chain id shown is a placeholder; use the value observed via `eth_chainId`.
   `python3 <skill-root>/scripts/reconcile.py --flows flows-weth.json flows-native.json --tolerance 0 --json`
   reconciles each file, pairs the transform legs across them, and prints per file the
   identity `opening + inflows + adjustments = outflows + closing + unexplained`, where the JSON field
   `unexplained_delta = declared_closing - computed_closing` (sign stated on the next line), and exits 1
   when it exceeds the tolerance.
5. Answer (bounded): "Of the vault's <Y> base units of <asset> at P1 (block N), <A> are traced
   receipt-level to `Collect` events on position <id> (pool <address>, containing the target) and
   forwarded to the vault (E7-E12); <C> were wrapped by the vault from native ETH whose source is traced
   to <collect/hook receipts> (E13-E15) / not traceable without a trace API (L2); <B> arrived from
   <counterparty> with no fee-collection event in the transaction or its forwarding chain and remain
   unexplained; searched Transfer and Deposit logs over blocks [a, b], paginated in 5,000-block windows."
   Do not add token-control findings, sellability, or launch history unless the user asked.

## Broad mode

Rule: screen every surface, deepen only on triggers, and produce a layered conclusion. All 22 core checks
must be present (any status) and all 11 ratings set.

Procedure (priority order; each step can change the verdict alone, so it runs before the next):
1. Packet and architecture pass.
2. Authority discovery first: surface A (`A-MINT`, `A-UPGRADE`, `A-SEIZE`, `A-RESTRICT`, `A-TAX`,
   `A-EXTCALL`, `A-ADMIN`) and surface B (`B-CANON`, `B-PRINCIPAL`, `B-SIDE`). No monetary threshold.
3. Then C (`C-HIST-SELL`, `C-QUOTE`) and D (`D-SUPPLY`, `D-CONC`).
4. Then E (`E-LAUNCH`), F (`F-FEES`, `F-TREASURY`), G (`G-REWARDS`, `G-RIGHTS`, and `G-LAYER-ADMIN` -
   upgrade/admin/pause/rescue authority over the reward, vault, distributor or backing layer, rated under
   `admin_treasury_reward_custody`), H (`H-UTILITY`, `H-DEPS`, `H-DEV`).
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
2. Generate the report sections from manifest objects: `verdict` -> `## Verdict` (the `answer` text
   verbatim; E-REPORT-VERDICT otherwise); `target` + pins -> `## Target identity`; `ratings` ->
   `## Ratings` (one `| <surface key> | <rating> | ...` row per key; E-REPORT-RATINGS otherwise);
   `findings` -> `## Key findings`; `verdict.strongest_contrary_evidence` etc. -> their sections;
   `coverage` + `discovery` -> `## Coverage and limitations`; `declarations` -> `## Declarations`;
   `python3 <skill-root>/scripts/ledger.py render --manifest manifest.json --format md` -> `## Evidence ledger`.
3. Legitimate visuals (each labeled with pin id, denominator, and evidence ids):
   - concentration bars with explicit denominators and exclusions (total supply vs circulating float;
     pools, lockers, burn, treasury excluded or shown separately);
   - a quote-depth curve at the pin: input size vs expected output and per-unit degradation, route named,
     with failures marked as failures, not as zero;
   - a flow diagram from `reconcile.py` output with the unexplained delta drawn as its own bounded box.
   Illegitimate: any chart that implies a trend, prediction, or a "score".
4. Finalization loop: set `report.sha256` to the sha256 of the final file bytes and validate; if you then
   edit the report (including the Declarations "validated on <date>" line), re-hash and re-validate. The
   last validation run must be on the final bytes. A change to a number in the report is a change to the
   manifest first.

## Parallel lanes

Use when authorized parallel agents exist. Every lane receives the same frozen packet, verifies
`_freeze.sha256` before reading anything (same command as SKILL.md Step 1 and `references/target-packet.md`):

```
python3 -c "import hashlib,json,sys;p=json.load(open(sys.argv[1]));p['_freeze']={k:None for k in p.get('_freeze',{})};print(hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':')).encode()).hexdigest())" target-packet.json
```

and gets a bounded scope; lanes return rows, not reports.

| Lane | Prefix | Surfaces / checks | Deep tracks typically triggered |
|---|---|---|---|
| `token-controls` | `tc:` | A: `A-MINT` `A-UPGRADE` `A-SEIZE` `A-RESTRICT` `A-TAX` `A-EXTCALL` `A-ADMIN` | `proxy-bytecode` |
| `liquidity-custody` | `lc:` | B: `B-CANON` `B-PRINCIPAL` `B-SIDE` | `pool-position-history`, `proxy-bytecode` (locker/hook proxies) |
| `sellability` | `sl:` | C: `C-HIST-SELL` `C-QUOTE` | simulation only behind `fork_guard.py`; `pool-position-history` |
| `supply-concentration` | `sc:` | D: `D-SUPPLY` `D-CONC` | `transfer-replay` |
| `launch-history` | `lh:` | E: `E-LAUNCH` | `launch-cohort` |
| `fees-treasury` | `ft:` | F: `F-FEES` `F-TREASURY` | `proceeds-reconciliation` |
| `rewards-backing` | `rb:` | G: `G-REWARDS` `G-RIGHTS` `G-LAYER-ADMIN` | `reward-epoch`, `dependency-redemption` |
| `dependencies-dev` | `dd:` | H: `H-UTILITY` `H-DEPS` `H-DEV` | `dependency-redemption`, `operational-attribution` |

Id rule: a lane prefixes EVERY id it creates with its fixed prefix - `E`, `F`, `L`, `S`, ad-hoc check ids
(`lc:B-LOCKER-AUTH`), deep-track check ids (`sc:T-REPLAY-CONSERVATION`), and any pin other than the
frozen `P1` (`lh:P2`, carrying its full captured header). Lanes reuse `P1` verbatim and never create a
second pin on the target chain at a different block without a stated purpose (`historical`). Two lanes
can never collide because the prefixes are fixed. A lane may open ANY deep track whose trigger fires (not
only the ones listed for it) but must declare each in its fragment under `deep_tracks_opened` with the
trigger and the evidence ids; the coordinator merges identical tracks opened by two lanes into one. A
lane never marks a check `pass` on a proposition it deferred; it leaves the check `unknown` with reason
"deferred to track <name>".

Lane input: the frozen packet (path + `_freeze.sha256`), the assigned check ids, the mode, and the shared
cache file (read-only for lanes, or one cache per lane merged afterwards; all bound to the same chain id).

Lane output (manifest fragment; no prose report):
```json
{
  "lane": "liquidity-custody",
  "packet_sha256": "<_freeze.sha256 of the frozen packet, recomputed by the lane>",
  "pins": [ {"pin_id": "lc:P2", "chain_id": 8453, "purpose": "historical", "...": "full pin object"} ],
  "pin_ids_used": ["P1", "lc:P2"],
  "scope_addresses": [ "...manifest scope entries..." ],
  "evidence":  [ {"evidence_id": "lc:E1", "pin_id": "P1", "...": "..."} ],
  "checks":    [ {"check_id": "B-PRINCIPAL", "evidence_ids": ["lc:E1"], "finding_ids": ["lc:F1"], "pin_id": "P1", "...": "..."},
                 {"check_id": "lc:B-LOCKER-AUTH", "...": "..."} ],
  "findings":  [ {"finding_id": "lc:F1", "evidence_ids": ["lc:E1"], "pin_or_tx": "P1", "...": "..."} ],
  "limitations": [ {"limitation_id": "lc:L1", "...": "..."} ],
  "discovery":   [ {"discovery_id": "lc:S1", "...": "..."} ],
  "deep_tracks_opened": [ {"track": "pool-position-history", "trigger": "position NFT transferred after launch", "evidence_ids": ["lc:E4"]} ],
  "unresolved_questions": ["..."]
}
```
Lanes never set ratings or the verdict: a rating needs the merged basis.

Merge procedure:
1. Verify every fragment's `packet_sha256` equals the frozen `_freeze.sha256`; reject any that differ.
2. Concatenate scope addresses; deduplicate by (chain_id, address, role); on conflicting
   `runtime_status`/`code_hash` for the same address, keep the `rpc_derived` value and log the conflict.
3. Renumber ids globally in lane order (`token-controls` first): PINS FIRST (`lh:P2` -> `P2`, `ft:P2` ->
   `P3`; two prefixed pins with identical `chain_id` + `block_hash` merge into one), then `E`, `F`, `L`,
   `S` and prefixed check ids (`lc:E1` -> `E14`, `lc:F1` -> `F6`, `lc:L1` -> `L3`, `lc:S1` -> `S2`,
   `lc:B-LOCKER-AUTH` -> `B-LOCKER-AUTH`). Keep the mapping table in `tooling.merge_map` and rewrite every
   structured reference (`pins`, `checks[].pin_id`, `checks[].limitation_id`, `checks[].evidence_ids`,
   `checks[].finding_ids`, `evidence[].pin_id`, `findings[].evidence_ids`, `findings[].pin_or_tx`,
   `ratings[].time_basis_pin_id`, `ratings[].basis_check_ids`, `scope_addresses[].pin_id`,
   `scope_addresses[].limitation_id`, `metadata.*.evidence_id`, `coverage.limitations[].affected_check_ids`)
   AND every free-text field (`reason`, `summary`, `coverage`, `alternatives`, `stale_conditions`,
   `description`, `proposition`, `note`) by regex on the prefixed ids (`\b(tc|lc|sl|sc|lh|ft|rb|dd):
   (P|E|F|L|S)\d+\b` and the prefixed check ids). Before validating, assert pin ids are unique and every
   `P<n>` referenced anywhere resolves to exactly one entry.
4. Deduplicate evidence by (chain_id, artifact_sha256, query); point all references at the survivor.
5. Resolve conflicting findings by evidence class: preferred onchain > `source_verified` >
   `simulation_counterfactual` > discovery-class. Within a class, current-state findings prefer the later
   pin, historical findings prefer the receipt. If still unresolved, keep both propositions with
   `alternatives` filled and rate on the worse one until resolved; never average.
6. Open the deep tracks declared in `deep_tracks_opened` that no lane completed, one instance per
   trigger, sequentially.
7. Compute the 11 ratings from the merged checks (every `finding`-status check on a surface belongs in
   that rating's `basis_check_ids`), then the verdict. Validate.

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

chain/address/block/query. Implementation: `ddcore.ResponseCache` keys each entry on
`[chain_id, scheme, host, port, redacted path, method, params]`, where params carry the address, the
pinned block hex and the calldata or filter; the chain id is bound live (`RpcClient.set_chain_id` right
after `eth_chainId`), stored per entry and in the file header, and a file recorded for another chain is
refused (`RpcCoverageError`, kind `rpc_error`, "cache file belongs to chain X"). Floating block tags are
never cached. One cache file per target packet; do not reuse a cache across chains or across path-routed
multi-chain endpoints.
