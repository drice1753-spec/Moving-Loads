---
evm_dd_report: 1
mode: broad
target_chain_id: <integer chain id as observed via eth_chainId at the pin>
target_address: <EIP-55 checksummed address>
target_symbol: <symbol | unresolved | nonstandard:<value>>
primary_pin_id: P1
primary_pin_block: <integer block number of P1>
primary_pin_block_hash: <0x + 64 hex, equal to pins[P1].block_hash>
manifest_path: manifest.json
---

<!-- Frontmatter is parsed as simple `key: value` lines and must match the manifest exactly
     (E-REPORT-IDENTITY, E-META-REPORT, E-MODE-MISMATCH); a UTF-8 BOM and leading blank lines are
     tolerated, a duplicated key is rejected. manifest_path is relative to this file, and the manifest's
     report.path must point back here from inside the manifest's directory (E-REPORT-MISSING).
     mode is focused | broad | formal. Replace every <placeholder>. Keep the H2 headings verbatim and
     in this order (E-REPORT-SECTIONS). HTML comments like this one and fenced code blocks are
     stripped before the validator scans the body, so the target address, the Ratings rows and the
     verdict answer must sit in visible text. Every address mentioned in the body must be a manifest
     scope address (W-REPORT-UNSCOPED-ADDRESS, an error under --strict). -->

# Token due diligence report

## Verdict

<!-- One direct, conditional sentence answering verdict.question under verdict.requirement_frame, in
     bounded language and naming the pin. The Answer line must contain manifest.verdict.answer verbatim
     (whitespace-normalized; E-REPORT-VERDICT). Then conditions (if GO-WITH-CONDITIONS) and main reasons,
     each citing finding ids. Never "safe", "legit", "rug-proof", "risk-free" (W-VERDICT-LANGUAGE, a
     regex heuristic; error under --strict). If the user gave no horizon or size, the requirement frame
     is the default "rug resistance and exit at the stated size over a 30-day hold", labeled an
     assumption here. -->

**Question:** <verdict.question>
**Requirement frame:** <verdict.requirement_frame> <(assumed default | as stated by the user)>
**Answer:** <verdict.answer, e.g. NO-GO under the stated requirement for rug resistance. | GO-WITH-CONDITIONS under <frame>: <conditions>.>

Main reasons:
- <reason> (F<n>)

## Target identity

<!-- The checksummed target address MUST appear in this section as visible text (E-REPORT-TARGET-ABSENT). -->

| Item | Value |
|---|---|
| Requested chain id | <target.requested.chain_id> (<requested.source, incl. any chain-name mapping>) |
| Observed chain id (eth_chainId) | <target.observed.chain_id> |
| Target address (EIP-55) | <target.address_checksum> |
| Name / symbol / decimals / total supply | <value (status)> / <value (status)> / <value (status)> / <value (status)> |
| Accounting model | <standard_erc20 | rebasing | fee_on_transfer | shares_based | wrapper | unknown> |
| Runtime code hash / size / status | <code_hash> / <code_size> bytes / <contract | eoa | unknown> |
| Proxy status / implementation / admin / upgrade authority | <status> / <address or none> / <address or none> / <resolved authority or unresolved> |
| Primary pin | P1 = block <n>, hash <0x..>, <YYYY-MM-DDTHH:MM:SSZ> (<finalized | latest, reason>) |
| Deployment | <resolved: tx 0x.. block n deployer 0x.. | unresolved: reason> |
| Target packet `_freeze.sha256` | <hex> |

## Ratings

<!-- All 11 rows in this order in broad/formal (E-RATING-MISSING). The first two cells of each row,
     `| <surface key> | <rating> |`, must equal manifest.ratings[key].rating (E-REPORT-RATINGS); write them
     as plain text (no backticks, bold or links) - cells are compared after collapsing whitespace.
     rating: critical|high|medium|low|unknown|not_applicable. likelihood: high|medium|low|unknown.
     confidence: high|medium|low. coverage: full|partial|none (rubrics: references/output-standard.md).
     A critical basis finding forces critical and a high/medium finding sets the floor
     (E-RATING-CRITICAL-AVERAGED). low over unknown/skipped basis needs coverage_qualified +
     coverage_note >= 20 chars (E-RATING-UNKNOWN-AS-LOW). Basis checks list every finding-status check on
     the surface (W-RATING-BASIS-INCOMPLETE); the ids below are defaults - keep only the checks you ran
     (the 22 core ids are mandatory in broad/formal; G-LAYER-ADMIN when a reward/vault layer exists). -->

| Surface | Rating | Likelihood | Confidence | Coverage | Time basis | Basis checks | Summary |
|---|---|---|---|---|---|---|---|
| token_controls | | | | | P1 | A-MINT, A-UPGRADE, A-SEIZE, A-RESTRICT, A-TAX, A-EXTCALL, A-ADMIN | |
| canonical_lp_principal_custody | | | | | P1 | B-CANON, B-PRINCIPAL | |
| side_pool_removal_risk | | | | | P1 | B-SIDE | |
| sellability_exit_depth | | | | | P1 | C-HIST-SELL, C-QUOTE | |
| current_concentration | | | | | P1 | D-SUPPLY, D-CONC | |
| historical_launch_integrity | | | | | P1 | E-LAUNCH | |
| admin_treasury_reward_custody | | | | | P1 | F-FEES, F-TREASURY, G-LAYER-ADMIN | |
| reward_accounting_liveness | | | | | P1 | G-REWARDS | |
| utility_redemption_rights | | | | | P1 | G-RIGHTS, H-UTILITY | |
| external_dependencies | | | | | P1 | H-DEPS | |
| development_disclosure | | | | | P1 | H-DEV | |

## Key findings

<!-- Ordered by severity. One entry per finding: id, exact proposition, severity, confidence
     (proven|strongly_supported|inference|unknown), evidence ids, pin or tx, historical flag. Every
     finding is linked from a check of the same surface (E-FINDING-UNLINKED, E-CHECK-STATUS). -->

- **F<n>** (<severity>, <confidence>) - <proposition>. Evidence: E<n>, E<n>. Basis: <P1 | tx 0x..>.

## Strongest contrary evidence

<!-- The best evidence against the verdict and why it does not overturn it. Cite evidence ids. -->

- <contrary evidence> (E<n>) - <why it does not change the verdict>

## Unresolved questions

<!-- Each with the limitation id or reason, and what would resolve it. -->

- <question> - <L<n> | reason>; resolvable by <archive endpoint / later pin / source publication>.

## What would change the conclusion

<!-- Specific observable evidence (a state at a new pin, a transaction, a published artifact) that would
     flip the verdict or a rating. -->

- <observable evidence> would move <rating key> from <x> to <y> / would change the verdict to <z>.

## Recommendations

<!-- Each addresses an observed deficiency and names the finding and the state that would close it.
     Not acceptable: "do your own research", "monitor the situation". -->

- <action> (addresses F<n>); verify by re-testing <check id> at a new pin.

## Coverage and limitations

<!-- Every coverage.limitations[] entry: id, kind, description, affected checks, affected addresses,
     retries. Every discovery[] record: id, claim, search universe, block range, pagination, inclusion
     rule, exclusions, materiality threshold, coverage. What was not searched. Every validator warning
     that remains, with its explanation. -->

| Limitation | Kind | Description | Affected checks | Affected addresses | Retries |
|---|---|---|---|---|---|
| L<n> | <kind> | <description, endpoint redacted> | <check ids> | <addresses> | <n> |

| Discovery | Claim | Search universe | Block range / pagination | Inclusion rule | Exclusions | Materiality threshold | Coverage |
|---|---|---|---|---|---|---|---|
| S<n> | | | | | | | |

## Declarations

<!-- The validation line records a run that must have happened on the FINAL bytes of this file: after
     writing it, re-hash (manifest.report.sha256 = ddcore.sha256_file(report)) and re-run the validator;
     repeat until the last run is on the final file. -->

- No real signing: true. No broadcast: true. No private keys requested: true.
- External content (websites, repositories, token metadata, labels) treated as untrusted evidence: true.
- Simulation used: <false | true - fork_type <x>, verified disposable by fork_guard.py, attestation <path relative to the manifest>, fork chain id <n> (= target chain), fork block <n>, synthetic accounts only, results labeled counterfactual>.
- Validation: `validate_report.py` (contract v1.1) passed with `--strict` on <date>; report sha256 re-computed after this line was written. Passing validates internal consistency of the manifest and report only; it does not establish RPC honesty, discovery completeness, or protocol safety.

## Evidence ledger

<!-- Render with: python3 <skill-root>/scripts/ledger.py render --manifest manifest.json --format md
     Column definitions: templates/ledger-columns.md -->

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | |
