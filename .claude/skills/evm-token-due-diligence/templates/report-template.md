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
     (E-REPORT-IDENTITY, E-META-REPORT, E-MODE-MISMATCH). manifest_path is relative to this file.
     mode is focused | broad | formal. Replace every <placeholder>. Keep the H2 headings verbatim and
     in this order. Every address mentioned in the body must be a manifest scope address. -->

# Token due diligence report

## Verdict

<!-- One direct, conditional sentence answering verdict.question under verdict.requirement_frame, in
     bounded language and naming the pin. Then conditions (if GO-WITH-CONDITIONS) and main reasons, each
     citing finding ids. Never "safe", "legit", "rug-proof", "risk-free" (W-VERDICT-LANGUAGE). -->

**Question:** <verdict.question>
**Requirement frame:** <verdict.requirement_frame>
**Answer:** <e.g. NO-GO under the stated requirement for rug resistance. | GO-WITH-CONDITIONS under <frame>: <conditions>.>

Main reasons:
- <reason> (F<n>)

## Target identity

<!-- The checksummed target address MUST appear in this section (E-REPORT-TARGET-ABSENT). -->

| Item | Value |
|---|---|
| Requested chain id | <target.requested.chain_id> |
| Observed chain id (eth_chainId) | <target.observed.chain_id> |
| Target address (EIP-55) | <target.address_checksum> |
| Name / symbol / decimals / total supply | <value (status)> / <value (status)> / <value (status)> / <value (status)> |
| Accounting model | <standard_erc20 | rebasing | fee_on_transfer | shares_based | wrapper | unknown> |
| Runtime code hash / size | <code_hash> / <code_size> bytes |
| Proxy status / implementation / admin / upgrade authority | <status> / <address or none> / <address or none> / <resolved authority or unresolved> |
| Primary pin | P1 = block <n>, hash <0x..>, <YYYY-MM-DDTHH:MM:SSZ> |
| Deployment | <resolved: tx 0x.. block n deployer 0x.. | unresolved: reason> |
| Target packet sha256 | <hex> |

## Ratings

<!-- All 11 rows in this order in broad/formal (E-RATING-MISSING). rating: critical|high|medium|low|unknown|not_applicable.
     likelihood: high|medium|low|unknown. confidence: high|medium|low. coverage: full|partial|none.
     A critical basis finding forces critical (E-RATING-CRITICAL-AVERAGED). low over unknown/skipped basis needs
     coverage_qualified + coverage_note (E-RATING-UNKNOWN-AS-LOW). -->

| Surface | Rating | Likelihood | Confidence | Coverage | Time basis | Basis checks | Summary |
|---|---|---|---|---|---|---|---|
| token_controls | | | | | P1 | A-MINT, A-UPGRADE, A-SEIZE, A-RESTRICT, A-TAX, A-EXTCALL, A-ADMIN | |
| canonical_lp_principal_custody | | | | | P1 | B-CANON, B-PRINCIPAL | |
| side_pool_removal_risk | | | | | P1 | B-SIDE | |
| sellability_exit_depth | | | | | P1 | C-HIST-SELL, C-QUOTE | |
| current_concentration | | | | | P1 | D-SUPPLY, D-CONC | |
| historical_launch_integrity | | | | | P1 | E-LAUNCH | |
| admin_treasury_reward_custody | | | | | P1 | F-FEES, F-TREASURY | |
| reward_accounting_liveness | | | | | P1 | G-REWARDS | |
| utility_redemption_rights | | | | | P1 | G-RIGHTS, H-UTILITY | |
| external_dependencies | | | | | P1 | H-DEPS | |
| development_disclosure | | | | | P1 | H-DEV | |

## Key findings

<!-- Ordered by severity. One entry per finding: id, exact proposition, severity, confidence
     (proven|strongly_supported|inference|unknown), evidence ids, pin or tx, historical flag. -->

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
     rule, exclusions, materiality threshold, coverage. What was not searched. -->

| Limitation | Kind | Description | Affected checks | Affected addresses | Retries |
|---|---|---|---|---|---|
| L<n> | <kind> | <description, endpoint redacted> | <check ids> | <addresses> | <n> |

| Discovery | Claim | Search universe | Block range / pagination | Inclusion rule | Exclusions | Materiality threshold | Coverage |
|---|---|---|---|---|---|---|---|
| S<n> | | | | | | | |

## Declarations

- No real signing: true. No broadcast: true. No private keys requested: true.
- External content (websites, repositories, token metadata, labels) treated as untrusted evidence: true.
- Simulation used: <false | true - fork_type <x>, verified disposable by fork_guard.py, attestation <path>, fork chain id <n>, fork block <n>, synthetic accounts only, results labeled counterfactual>.
- Validation: `validate_report.py` passed on <date>. Passing validates internal consistency of the manifest and report only; it does not establish RPC honesty, discovery completeness, or protocol safety.

## Evidence ledger

<!-- Render with: python3 <skill-root>/scripts/ledger.py render --manifest manifest.json --format md
     Column definitions: templates/ledger-columns.md -->

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| | | | | | | | | | | | |
