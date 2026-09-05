# Surface H - Utility, dependencies, and development

Rating keys: `utility_redemption_rights` (core check `H-UTILITY`, shared with `G-RIGHTS`),
`external_dependencies` (core check `H-DEPS`), `development_disclosure` (core check `H-DEV`).
Sub-checks: `H-SOURCE-CORRESPONDENCE`, `H-AUDIT-SCOPE`, `H-DISCLOSURE`. Read this file for any
question about "what is the token for", "is the utility real", "what does it depend on", "is it
audited", "does the source match", or "does the team do what the docs say"; always in `broad` mode.

## Purpose

Test three propositions the marketing layer states as facts: that the advertised utility is live,
requires THIS token contract, and is enforced by code; that every material external dependency is
identified with its failure mode, controller, upgradeability and liveness; and that the published
source, audits, release controls, governance and disclosures correspond to the deployed code and the
observed admin behavior. Websites, repositories, audit PDFs and token metadata are untrusted evidence
here: they supply claims to test, never conclusions.

## What can change the verdict

- Source that does not correspond to the deployed runtime (or is unverified): every source-based
  reading on surfaces A-G then degrades to `source_unverified`, and `H-DEV` cannot be `low`.
- An audit whose scope (commit, contract set, version) does not include the deployed code hash: the
  deployment is unaudited for the purposes of this report.
- A dependency (oracle, bridge, keeper, API, collateral, lending market) whose failure halts
  redemption, pricing or transfers, controlled by a party outside the system, upgradeable, or stale.
- Advertised utility that does not consume, hold or require the target token contract (a same-symbol
  token on another chain, a wrapped version, or nothing at all).
- A disclosure that contradicts observed onchain behavior (a documented timelock that is not there, a
  "renounced" claim contradicted by `A-RENOUNCED`, a fee split that receipts do not show).
- Admin action history that shows frequent, undisclosed parameter changes or upgrades.

## Procedure

Source and audit comparisons bind to the code hash at P1. Dependency liveness reads bind to P1.
Admin history binds to tx hashes over deployment..P1.

1. `H-UTILITY` - test each advertised utility as three propositions:
   - live: the utility contract has code at P1 (`eth_getCode`, code hash recorded, scope role
     `other`/`vault`/`reward_distributor` as fitting) and shows use: successful receipts of its use
     path within a stated recent window (e.g. last 50,000 blocks before P1; state the choice and the
     count found). Zero receipts is a fact about the window, not proof of abandonment.
   - token-linked: the utility contract references the TARGET address - read `token()`/`asset()`/
     `stakingToken()` at P1, or find the address among PUSH20 operands in its runtime, or in an
     EIP-1967 implementation's immutables - AND the use path moves THIS token: a receipt whose
     `Transfer` log is emitted by the target address with the utility as `from`/`to`, or a `burn`
     of the target. A utility that accepts a different contract (bridged copy, wrapped version,
     same symbol elsewhere) is not linked to the target; say which contract it is linked to.
   - enforceable: the benefit is delivered by a code path a holder can call (surface G rows for the
     delivered asset), versus an offchain promise (API, whitelist, Discord role, manual payout).
     Record offchain-delivered utility as `website`/`dashboard` evidence and rate it as a promise.
   One row per utility: `utility | live (window, receipts) | token-linked (read + receipt) |
   enforceable (path | promise) | evidence ids`.
2. `H-DEPS` - build the dependency inventory from the architecture pass and from surfaces B, C, F, G.
   For each: `dependency | type (external asset / oracle / bridge / API / keeper / lending / collateral /
   redemption / custody) | address or endpoint (chain, pin) | what breaks if it fails (pricing halts,
   redemption halts, transfers halt, rewards stop, liquidation) | who controls it (owner/roles/threshold,
   surface A reads on that contract) | upgradeable (proxy status, `Upgraded` history) | liveness
   evidence at P1 | stale condition`. Type-specific reads:
   - oracle: feed address read from the consumer at P1; `latestRoundData()`/equivalent `updatedAt`
     vs P1 timestamp; the consumer's staleness guard (present? threshold?); who can change the feed
     address; what the consumer does on a revert or zero answer;
   - bridge: canonical vs third-party (from the destination chain's verified deployment, never a
     list); message validators/relayers and their pause authority; a token whose supply on this
     chain is minted by a bridge has the bridge as a minter (`A-MINT`); the destination chain gets
     its own pin;
   - keeper: the address that calls processing functions (`from` of the last processing receipt),
     its native balance at P1, its last successful call block, what stops when it stops (`G-LIVENESS`);
   - API/offchain: cannot be verified onchain; record the claim, mark the dependency as
     `unverifiable onchain`, and rate rights that depend on it as promises;
   - lending/collateral: whether the token is collateral somewhere material (a listing is a claim by
     that market's contracts, read at P1), collateral factors, liquidation path, oracle used there;
   - external asset / custody: an asset held for holders by a third party (surface G availability
     classes) - who holds it, under which contract, with which withdrawal authority;
   - redemption dependency: any contract on the exit chain from `G-EXIT-ROUTE`.
   `H-DEP-LIVENESS` summarizes: for each dependency, the freshest evidence that it functioned
   (block, tx) and its age at P1.
3. `H-SOURCE-CORRESPONDENCE` - establish, per runtime (proxy, implementation, admin contracts, utility
   contracts), whether published source corresponds to deployed code:
   - read the metadata trailer of the runtime: the last two bytes are the big-endian length of a
     CBOR-encoded map appended by the Solidity compiler; decode the map (keys such as `ipfs`,
     `bzzr1`, `solc`) to obtain the metadata hash and the compiler version the deployed code was built
     with; a runtime without a trailer is a non-Solidity or stripped build (state which);
   - obtain the published source and its recorded compiler settings (explorer verification record or
     repository build file); the explorer's "verified" badge is `explorer` evidence, not
     correspondence;
   - compile-and-compare when a matching compiler is available (the package does not ship one; if it
     is not available, record the limitation as `source_unavailable`/`other` and stop at the metadata
     comparison): compare the compiled runtime with `eth_getCode` at P1 byte-for-byte, excluding the
     metadata trailer and the declared immutable references (list the offsets); any remaining
     difference means no correspondence;
   - outcomes: `source_verified` only when the byte comparison closes or the compiled runtime's
     keccak256 equals the code hash at P1; otherwise `source_unverified` (mismatch, unverified,
     decompiled, or "verified" for a different address or implementation). Decompiler output is never
     verified source. Deduplicate by code hash: identical runtimes share one correspondence result.
   Store the compared hashes in evidence: `ddcore.keccak256_hex(runtime_bytes)`.
4. `H-BUILD-TESTS` - reproducible build and tests, from the repository (untrusted; discovery only):
   a pinned compiler version and settings in the build file that match the metadata trailer; a lock
   file; tests present for the deployed contracts; a CI record. Running an untrusted repository's
   tests executes its code: do it only in an isolated environment, and never treat "tests pass" as a
   statement about the deployed code unless correspondence (step 3) holds for the tested commit.
   Absent correspondence, the repository is a claim about some code, not this code.
5. `H-AUDIT-SCOPE` - for each audit report: the commit/tag or contract hashes it names; the contract
   list in scope; the compiler version; the date. Compare with the deployed runtime(s): the audited
   commit must compile to the code hash at P1 (via step 3), or the report must name the deployed code
   hash, or the diff between the audited commit and the corresponding deployed source must be read
   and its findings assessed. An audit of a different commit, of the implementation but not the proxy
   admin, of the token but not the reward layer, or of a predecessor version is not an audit of this
   deployment - state precisely what it covers. Record the report as `website`/`repository` evidence
   with retrieval date; its conclusions are claims. Unresolved audit findings are a disclosure item
   (step 8), not a token finding by themselves.
6. `H-RELEASE-CONTROLS` - who can deploy, upgrade, or change parameters (from `A-UPGRADE`/`A-ADMIN`):
   the key types, thresholds, timelock delays, and whether the deployer key still holds any
   authority or funds; whether upgrades have been announced before execution (compare `Upgraded`
   events with dated disclosures); whether implementation addresses were verified before the upgrade
   tx executed.
7. `H-GOVERNANCE` - governance reality: is there a governor contract, and is it the holder of the
   authorities that matter (the timelock it queues into is `owner()`/proxy admin), or does a multisig
   hold them while votes are advisory? Read: governor's `timelock()`, the timelock's proposer and
   executor roles (surface A role replay), guardian/cancel/veto roles and their holders, quorum and
   proposal thresholds at P1, and the execution history (proposals executed vs multisig direct
   actions over the range). "Community-governed" is a claim; the authority reads decide.
8. `H-DISCLOSURE` - disclosure accuracy table. For each material claim in docs, website, metadata or
   posts: `claim (quoted) | source (URL/record, retrieval date; untrusted) | onchain observation
   (evidence ids) | result: matches / contradicts / unverifiable`. Claims to test first: renounced or
   no owner (`A-RENOUNCED`), locked liquidity and duration (`B-PRINCIPAL`), tax rates and recipients
   (`A-TAX`, `F-FEES`), supply and allocations (`D-SUPPLY`, `E-LAUNCH`), backing and rewards
   (`G-INVENTORY-VS-LIABILITY`), audits (step 5), timelocks and multisig thresholds (`A-ADMIN`).
   A contradiction is a finding on this surface with the contradicting evidence; a `matches` row
   does not upgrade the claim's source to preferred evidence.
9. `H-ADMIN-HISTORY` - actual operating behavior: replay admin-relevant events from deployment to P1
   (`OwnershipTransferred`, `Upgraded`, `AdminChanged`, `BeaconUpgraded`, `RoleGranted/Revoked`,
   `Paused/Unpaused`, fee and limit setter events, or setter calls found by scanning the admin
   address's transactions to the system contracts when no event exists - trace/explorer discovery,
   receipts as proof). Report counts, timing, what changed, and whether each change was disclosed
   before or after execution. Frequency alone is neither good nor bad; undisclosed changes to
   holder-facing parameters are the finding.
10. Apply the anti-bias rules before writing any conclusion on this surface:
    - polished marketing, a professional site, or a known launch platform do not establish safety;
    - a copied or templated contract does not establish fraud; templates are the norm, and the
      question is what the deployed instance permits (surface A) and what was changed from the
      template (`E-FACTORY-VERSION`);
    - awkward, verbose or unusual code does not establish AI authorship, incompetence, or malice;
      never assert authorship or intent from style;
    - a missing audit is a coverage fact about assurance, not a finding of a defect; a present audit
      is not a finding of correctness;
    - absence of a repository or of tests lowers confidence in claims about the code; it does not
      lower or raise the ratings on surfaces A-G, which rest on deployed code and state.
11. Write the rows. Rate `utility_redemption_rights` jointly with surface G from the enforceable
    rights and the token-linkage results; rate `external_dependencies` from the worst dependency
    whose failure halts a material function, weighted by its controller and liveness; rate
    `development_disclosure` from correspondence, audit scope, release controls and the disclosure
    table - never from marketing quality.

### Verify-at-use table

| Item | Verify at use time by |
|---|---|
| Oracle feed addresses and decimals | read from the consumer contract at P1 (`eth_call`), then `eth_getCode` and a `latestRoundData()`-style read on the feed; never from a docs list |
| Bridge contract addresses (source and destination) | `to`/emitter in a preserved bridge receipt on each chain; destination chain id from `eth_chainId` on its RPC, pinned (`references/chains/chain-verification.md`) |
| Chain ids of dependency chains | `eth_chainId` per RPC, pinned as P2..Pn; never from a table |
| Compiler version for compile-and-compare | CBOR metadata trailer of the deployed runtime, not the repository's claim |
| Explorer verification records | retrieved at use time, recorded as `explorer` evidence with retrieval date; the comparison in step 3 decides correspondence |
| Lending-market listings and collateral factors | reads on that market's contracts at P1 |

## Checks

| check_id | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|
| H-UTILITY | Each advertised utility is live, linked to the target contract, and enforceable by code (or is a promise) | code at P1 + one use receipt moving the target token + path guard | `rpc_state`, `receipt`, `bytecode` | utility contract upgrade or token pointer change; window expiry for "live" |
| H-DEPS | Every material dependency is inventoried with failure mode, controller, upgradeability and liveness | one row per dependency with reads at P1 | `rpc_state`, `rpc_storage`, `receipt` | dependency admin action, feed change, keeper stop |
| H-DEV | Source correspondence, build/tests, audit scope, release controls, governance and disclosure accuracy are assessed against the deployed code and observed behavior | steps 3-9 rows | `bytecode`, `rpc_state`, `log_decoded`, `source_verified` | upgrade (new code hash), new admin actions |
| H-SOURCE-CORRESPONDENCE | Published source compiles to the runtime at P1 (per runtime, deduplicated by code hash) | metadata trailer decode + hash/byte comparison with listed exclusions | `bytecode`, `source_verified` | upgrade |
| H-AUDIT-SCOPE | Each audit's scope covers the deployed code hash and the contracts that matter, or the gap is stated | audit-named commit/hashes vs P1 code hashes | `bytecode`, `repository` (report, untrusted) | upgrade; new audit |
| H-DISCLOSURE | Material claims are tested against onchain observation with a per-claim result | disclosure table with evidence ids per row | `rpc_state`, `receipt`, `website` (claims) | any admin action or disclosure change |
| H-LIVE | The utility contract shows successful use within the stated window | receipts in window (count, range) | `receipt` | window expiry |
| H-TOKEN-LINKED | The utility reads and moves the target contract, not a same-symbol or wrapped substitute | pointer read at P1 + `Transfer` emitted by the target in a use receipt | `rpc_state`, `log_decoded` | pointer setter call, upgrade |
| H-DEP-LIVENESS | Each dependency's freshest functioning evidence and its age at P1 | last update/processing tx per dependency | `receipt`, `rpc_state` | staleness threshold elapsed |
| H-RELEASE-CONTROLS | Deploy/upgrade/parameter authorities, thresholds and delays are resolved; upgrades were verified and disclosed | surface A reads + `Upgraded` history vs disclosures | `rpc_state`, `log_decoded` | ownership/role/delay change |
| H-GOVERNANCE | Token votes bind the authorities that matter, or a multisig/guardian can act or veto without them | governor/timelock/role reads + execution history | `rpc_state`, `log_decoded` | role change, governor upgrade |
| H-ADMIN-HISTORY | Admin actions from deployment to P1 are replayed, classified and matched to disclosures | event replay with coverage + receipts for setter calls | `log_decoded`, `receipt` | new admin action |

Statuses follow the manifest rules: `pass` and `finding` need evidence ids; `unknown`/`skipped` need a
reason with the limitation id (`source_unavailable` when no compiler or record could be obtained).
`unknown` is never a pass.

## Common false positives and negatives

False positives (a development or dependency problem that is not one):
- A code-hash mismatch caused only by the metadata trailer or immutable values; exclude them
  explicitly and compare the rest before declaring non-correspondence.
- "Unaudited" reported as a defect; it is an assurance gap (lower confidence), and the ratings on
  A-G already rest on deployed code.
- A template contract flagged as "copied"; the finding, if any, is a changed line that adds an
  authority, never the template itself.
- A stale oracle that the consumer does not read on any path that matters (dead code or a disabled
  feature); tie each dependency to a live path.
- Zero usage receipts in a short window reported as "abandoned"; widen the window or state the bound.
- An unusual coding style reported as evidence of anything about the author.

False negatives (a development or dependency fact missed):
- Verifying the proxy's source and not the implementation's (or the reverse); the ProxyAdmin, the
  beacon, the reward layer and the utility contract each need their own correspondence result.
- An audit that names the right repository but a commit that predates the deployed changes.
- A utility linked to a bridged or wrapped copy of the token, or to a same-symbol token on another
  chain, reported as token-linked; the emitter address of the `Transfer` decides.
- A keeper or relayer dependency invisible in the token's own code (it lives in the reward or bridge
  layer).
- A governor that queues into a timelock that holds no authority, while a multisig holds the real
  `owner()`; read what the timelock owns.
- An offchain API dependency (price, eligibility, KYC) that gates redemption; nothing onchain shows
  it, so the docs claim and the consumer's admin-set flags are the only trail - record it as
  unverifiable and rate the dependent rights as promises.
- Disclosures tested only against the current state at P1 when the claim concerns history ("liquidity
  has always been locked"): test against the replay (surfaces B and E).

## Escalation triggers to deep tracks

- Any dependency in a redemption or backing chain, any oracle/bridge/lending/collateral dependency
  whose failure halts a material function, or an exit chain longer than one hop:
  `references/deep-tracks/dependency-redemption.md`.
- Source without correspondence on any runtime that holds an authority, a custom proxy, fallback
  dispatch, DELEGATECALL to a storage-loaded target, or an audit whose gap to the deployed code must be
  read at bytecode level: `references/deep-tracks/proxy-bytecode.md` (escalate gradually: runtime and
  selectors -> verified predecessors and compiler metadata -> storage and historical calls ->
  reconstruction or simulation; equivalence is claimed only when compilation and byte comparison
  close the meaningful differences).
- Admin history that must be described as the behavior of an operator or controller (never an
  identity): `references/deep-tracks/operational-attribution.md`, with language from
  `references/attribution.md`; no broad personal-identity searches.
- Keeper or epoch liveness in the reward layer: `references/deep-tracks/reward-epoch.md`.
- A utility contract that mints, burns or rewrites balances of the target:
  `references/deep-tracks/transfer-replay.md`.
- Protocol-wide invariant work or a substantial exploit campaign: route to a separate audit workflow
  when one is available; this skill records the boundary as `out_of_scope`.

## How to state results

Bind each statement to the code hash, the pin, the dependency address and the retrieval date of any
claim; keep "claim" and "observation" in separate clauses.

- "The implementation runtime at P1 (code hash 0x..) carries a metadata trailer naming solc 0.8.x
  and IPFS hash ...; the published source at the explorer record (retrieved 2026-..-..) compiles
  with the recorded settings to a runtime that matches byte-for-byte outside the trailer and two
  immutable slots at offsets [..] (E21). H-SOURCE-CORRESPONDENCE `pass` for this runtime; the proxy
  and ProxyAdmin runtimes were compared separately (E22, E23)."
- "The audit report (retrieved 2026-..-..) covers commit abc123 of the token repository. That commit
  compiles to code hash 0x.., which differs from the deployed implementation hash 0x.. at P1; the
  diff adds `setFeeRecipient(address)` (E24). The deployed implementation is not covered by this
  audit for that function; H-AUDIT-SCOPE `finding`, medium."
- "Staking contract 0x.. reads `stakingToken()` = the target at P1 (E25) and receipt 0x.. shows a
  `Transfer` emitted by the target into it (E26): token-linked. Rewards are paid in the target itself
  from a balance funded by `owner()` transfers (E27): enforceable for the target token, dependent on
  admin funding. 38 successful `stake` receipts in the last 50,000 blocks (E28)."
- "Redemption depends on price feed 0x.. (read from `oracle()` at P1, E29); `updatedAt` is 31 hours
  before P1 and the consumer has no staleness guard (E30); the feed address setter is held by the
  EOA 0x.. (E31). Failure of this feed halts redemption; H-DEPS `finding`, high."
- "Disclosure: the docs state 'contract renounced' (retrieved 2026-..-..). Observation at P1:
  `owner()` = zero address, but `DEFAULT_ADMIN_ROLE` has one member and the ProxyAdmin owner is 0x..
  (E32-E34). Result: contradicts."
- "Unknown because the source record was unavailable: the explorer verification API returned errors
  on 3 attempts (limitation L5, `explorer_unavailable`); H-SOURCE-CORRESPONDENCE is `unknown` and all
  source-based readings on surfaces A-G are recorded as `source_unverified`."
- Never: "audited, therefore safe"; "AI-written"; "copy-paste scam"; "the utility is real" from a
  website; "decentralized governance" without the timelock's authority reads; "the team is
  transparent" from disclosure volume.
