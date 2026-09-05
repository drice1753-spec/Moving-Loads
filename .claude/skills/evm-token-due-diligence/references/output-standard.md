# Output standard

The output exists to let a reader act on one question about one token without re-doing the work. Every
number is bound to a pin or a transaction, every claim to an evidence row, and every rating to the checks
that support it. Verdict first; evidence last but complete.

## Verdict-first structure

Open `## Verdict` with a direct, conditional answer to the user's actual question, judged against the
stated requirement frame (`verdict.requirement_frame`, for example "rug resistance for a 30-day hold" or
"exit of 0.5% of supply within 5% degradation"). Form:

- one sentence with the answer in bounded language (below), naming the pin;
- the conditions (`verdict.conditions`) when the answer is GO-WITH-CONDITIONS;
- the main reasons (`verdict.main_reasons`), each citing finding ids.

If the user asked "is this safe", restate it as the requirement frame it implies and answer that; never
answer the literal question with "yes".

## The 11 ratings (fixed order)

| # | Key | What the rating answers |
|---|---|---|
| 1 | `token_controls` | What current code and current authority holders can change or take |
| 2 | `canonical_lp_principal_custody` | Whether principal in the canonical pool(s) can be removed, by whom, when |
| 3 | `side_pool_removal_risk` | Whether other pools materially affect exit and who can remove them |
| 4 | `sellability_exit_depth` | Whether holders can sell and the realistic executable size at the pin |
| 5 | `current_concentration` | Classified concentration at the pin with denominators |
| 6 | `historical_launch_integrity` | How the launch allocated, exempted, and who sold early |
| 7 | `admin_treasury_reward_custody` | Who controls fees, treasury and reward inventory and where they went |
| 8 | `reward_accounting_liveness` | Whether reward/vault accounting conserves, pays entitlements, and keeps processing |
| 9 | `utility_redemption_rights` | Which rights holders can enforce versus promises, and what they receive on exit |
| 10 | `external_dependencies` | Oracles, bridges, keepers, APIs, collateral and custody that could change the verdict |
| 11 | `development_disclosure` | Source correspondence, builds, tests, audit scope, release controls, disclosure accuracy |

Rating levels:
- `critical`: an executable path or an observed event that on its own decides the question adversely
  (unrestricted mint by one key, executable LP-principal removal by a hot key, holders cannot sell).
- `high`: a material adverse capability or observation exists, conditioned by a step that is not a real
  barrier (short timelock, low-threshold multisig, partial exemption) or by partial coverage on a decisive
  proposition.
- `medium`: adverse capability bounded by real constraints (caps, long timelock, independent parties), or
  material unknowns beside otherwise favorable evidence.
- `low`: propositions tested with preferred evidence at the pin; no adverse capability found; coverage
  full, or partial with `coverage_qualified: true` and a `coverage_note` naming what was not tested.
- `unknown`: the surface could not be tested to its minimum evidence; name the limitation or the reason.
- `not_applicable`: the surface does not exist for this system, shown by evidence of absence (no reward
  layer found in code, storage and events), not by failure to look.

Columns per rating (required in broad and formal): `rating` (severity of what the surface implies),
`likelihood` (`high`/`medium`/`low`/`unknown`: how likely the adverse outcome is to be exercised or occur
under current evidence), `confidence` (`high`/`medium`/`low`: how well the evidence class supports the
rating), `coverage` (`full`/`partial`/`none`), `time_basis_pin_id` (the pin the rating is valid at),
`basis_check_ids`, `summary`.

## No averaging

A rating is the worst supported outcome among its basis checks, not their mean. If any basis check carries
a `critical` finding, the rating is `critical` (validator: E-RATING-CRITICAL-AVERAGED). Example:
`token_controls` with `A-MINT` finding critical (one EOA can mint without cap) and `A-UPGRADE`, `A-SEIZE`,
`A-RESTRICT`, `A-TAX`, `A-EXTCALL`, `A-ADMIN` all `pass` is `critical`, not `medium`; the six passes go in
the summary as context. Likewise `low` over an `unknown` basis check is rejected unless coverage-qualified
(E-RATING-UNKNOWN-AS-LOW).

## Bounded language

Use these forms (and forms built the same way: proposition + scope + pin/tx):
- "No current executable removal path found at the pinned block."
- "Sellable at the tested sizes under the quoted state."
- "Unknown because historical state was unavailable."
- "NO-GO under the stated requirement for rug resistance."
- "GO-WITH-CONDITIONS under <requirement frame>: <conditions>."
- "Proven at tx <hash>: <what executed>." / "At P1, <address> holds <role> with <constraint>."

Forbidden as conclusions (the validator warns on some of these under `## Verdict`, W-VERDICT-LANGUAGE;
`--strict` makes it an error): "safe", "legit", "rug-proof", "risk-free", "cannot be rugged",
"guaranteed", "audited therefore safe", "liquidity is locked" without position, locker authority and pin,
"renounced" as a conclusion about the system, "team wallet"/"insider"/"dev" as labels without evidence,
"profit" without cost basis, and any phrasing that implies a favorable review predicts returns.

## Required sections (H2, exact text, in this order)

| Section | Content |
|---|---|
| `## Verdict` | Conditional answer, conditions, main reasons with finding ids |
| `## Target identity` | Requested vs observed chain id, EIP-55 address (must appear here), metadata with statuses, code hash and size, proxy status/implementation/admin/upgrade authority, primary pin (block, hash, UTC), deployment status, packet sha256 |
| `## Ratings` | Table of the 11 surfaces in fixed order with rating, likelihood, confidence, coverage, time basis, basis checks, summary |
| `## Key findings` | Findings ordered by severity, each with id, proposition, confidence, evidence ids, pin or tx |
| `## Strongest contrary evidence` | The best evidence against the verdict, and why it does not overturn it |
| `## Unresolved questions` | What remains unknown, with the limitation or reason, and what would resolve it |
| `## What would change the conclusion` | Specific observable evidence that would flip the verdict or a rating |
| `## Recommendations` | Actions addressing observed deficiencies (below) |
| `## Coverage and limitations` | Every `L<n>` with kind, description, affected checks/addresses, retries; every discovery record `S<n>`; what was not searched |
| `## Declarations` | no real signing, no broadcast, no private keys requested, external content untrusted, simulation used/not and fork attestation, and the validator note: passing validates internal consistency only |
| `## Evidence ledger` | The 12-column ledger (render with `ledger.py`) |

Focused mode requires `## Verdict`, `## Coverage and limitations`, `## Evidence ledger`; include the
others when they carry content. Every address in the body must be in `scope_addresses`
(W-REPORT-UNSCOPED-ADDRESS).

## Ledger columns and discovery records

Ledger columns (definitions and an example: `templates/ledger-columns.md`):
`finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions`

Discovery-claim record (manifest `discovery[]`, one per claim like "all pools", "top holders", "launch
cohort"): `discovery_id` (`S<n>`), `claim`, `search_universe` (which logs/contracts/APIs), `block_range`,
`pagination`, `inclusion_rule`, `exclusions`, `materiality_threshold`, `coverage`. A discovery claim
without a record is an inference, not a finding.

## Recommendations must address observed deficiencies

Each recommendation names the finding it addresses and the observable state that would close it:
- "Move the canonical v3 position NFT (id <n>) from the deployer EOA to a timelocked locker with no
  rescue or arbitrary-call function; re-test B-PRINCIPAL at a new pin." (addresses a B-PRINCIPAL finding)
- "Transfer `DEFAULT_ADMIN_ROLE` on the reward distributor to a multisig with threshold >= 2 of
  independent signers, or renounce it after the final epoch; re-test G-REWARDS." (addresses a G finding)
- "Publish the compiler settings and reproduce the deployed runtime hash from source; until then
  H-DEV stays `source_unverified`."
Not acceptable: "do your own research", "monitor the situation", "consider the risks".

## Manifest, report, validator

- The manifest (`schemas/manifest.schema.json`) is the source of truth; the report is a rendering of it.
- `report.path` is relative to the manifest; `report.sha256` is the sha256 of the report file
  (`ddcore.sha256_file`). Frontmatter keys (`evm_dd_report`, `mode`, `target_chain_id`, `target_address`,
  `target_symbol`, `primary_pin_id`, `primary_pin_block`, `primary_pin_block_hash`, `manifest_path`) must
  match the manifest.
- Validate:
  ```
  python3 <skill-root>/scripts/validate_report.py --manifest manifest.json --report report.md --strict
  python3 <skill-root>/scripts/ledger.py check --manifest manifest.json
  ```
  Exit 0 pass, 1 fail, 2 usage. `--json` prints machine-readable issues. `--strict` promotes
  W-REPORT-UNSCOPED-ADDRESS, W-EVIDENCE-DISCOVERY-ONLY and W-VERDICT-LANGUAGE to errors.
- Warning codes the validator can emit (never fatal unless promoted): W-REPORT-UNSCOPED-ADDRESS,
  W-VERDICT-LANGUAGE, W-EVIDENCE-DISCOVERY-ONLY, W-PIN-STALE, W-PIN-UNUSED (a pin whose chain nothing
  references), W-FINDING-CROSS-CHAIN (evidence on a different chain than the finding; legitimate for bridge
  findings), W-DECL-FORK-ATTESTATION (attestation path set but file absent), W-LIMITATION-UNREFERENCED.
  `ledger.py check` adds W-EVIDENCE-UNREFERENCED and W-LEDGER-FIELD-EMPTY. Read every warning; a warning
  you cannot explain in `## Coverage and limitations` is a gap in the report, not noise.
- The closing note printed by the validator belongs in `## Declarations`: passing validates internal
  consistency of the manifest and report only; it does not establish RPC honesty, discovery completeness,
  or protocol safety.

## Check status -> allowed rating outcomes

| Basis check status (with severity) | Effect on the surface rating |
|---|---|
| `pass` (>= 1 evidence id) | Supports `low` only if every other basis check is `pass`, `not_applicable`, or the rating is coverage-qualified; otherwise neutral |
| `finding`, severity `critical` | Rating MUST be `critical` |
| `finding`, severity `high` | Rating at least `high` |
| `finding`, severity `medium` | Rating at least `medium` |
| `finding`, severity `low` or `info` | Rating at least `low`; `info` alone does not raise it |
| `unknown` (reason required) | Rating `unknown`, or the level implied by other findings; `low` only with `coverage_qualified: true` + `coverage_note` |
| `skipped` (reason required) | Same as `unknown`; never a pass |
| `not_applicable` (reason with evidence of absence) | `not_applicable` when all basis checks are `not_applicable`; otherwise ignored for the level |

The likelihood column is independent of the level: a `critical` capability held by a 5-of-9 timelocked
multisig may carry `likelihood: low`; the rating stays `critical` because the capability exists.
