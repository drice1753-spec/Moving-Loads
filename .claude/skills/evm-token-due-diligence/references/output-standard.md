# Output standard

The output exists to let a reader act on one question about one token without re-doing the work. Every
number is bound to a pin or a transaction, every claim to an evidence row, and every rating to the checks
that support it. Verdict first; evidence last but complete.

## Verdict-first structure

Open `## Verdict` with a direct, conditional answer to the user's actual question, judged against the
stated requirement frame (`verdict.requirement_frame`, for example "rug resistance for a 30-day hold" or
"exit of 0.5% of supply within 5% degradation"; when the user gave none, "rug resistance and exit at the
stated size over a 30-day hold", labeled an assumption). Form:

- one sentence with the answer in bounded language (below), naming the pin - this is
  `manifest.verdict.answer` and must appear verbatim (whitespace-normalized) in the section
  (E-REPORT-VERDICT);
- the conditions (`verdict.conditions`) when the answer is GO-WITH-CONDITIONS;
- the main reasons (`verdict.main_reasons`), each citing finding ids.

If the user asked "is this safe", restate it as the requirement frame it implies and answer that; never
answer the literal question with "yes". When a broad request contains a focused question ("is it a rug?
can I sell 2%?"), answer the focused question first, then the overall judgement.

## The 11 ratings (fixed order)

| # | Key | What the rating answers |
|---|---|---|
| 1 | `token_controls` | What current code and current authority holders can change or take |
| 2 | `canonical_lp_principal_custody` | Whether principal in the canonical pool(s) can be removed, by whom, when |
| 3 | `side_pool_removal_risk` | Whether other pools materially affect exit and who can remove them |
| 4 | `sellability_exit_depth` | Whether holders can sell and the realistic executable size at the pin |
| 5 | `current_concentration` | Classified concentration at the pin with denominators |
| 6 | `historical_launch_integrity` | How the launch allocated, exempted, and who sold early |
| 7 | `admin_treasury_reward_custody` | Who controls fees, treasury, reward inventory and the reward/vault layer's admin authority (`G-LAYER-ADMIN`), and where assets went |
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
  full, or partial with `coverage_qualified: true` and a `coverage_note` (at least 20 characters) naming
  what was not tested. Never `low` with `coverage: none` or an empty basis.
- `unknown`: the surface could not be tested to its minimum evidence; name the limitation or the reason.
- `not_applicable`: the surface does not exist for this system, shown by evidence of absence (no reward
  layer found in code, storage and events), not by failure to look; every basis check is
  `not_applicable`.

Columns per rating (required in broad and formal): `rating` (severity of what the surface implies),
`likelihood`, `confidence`, `coverage` (`full`/`partial`/`none`), `time_basis_pin_id` (the pin the rating
is valid at), `basis_check_ids` (every `finding`-status check on the surface must be listed;
W-RATING-BASIS-INCOMPLETE otherwise), `summary`.

Likelihood rubric (how likely the adverse outcome is to be exercised or occur under current evidence):
- `high`: the actor who can trigger the harm is a single key or a small unlocked group with an observed
  history of using similar authority, or the path needs no privilege at all.
- `medium`: a privileged path exists behind a multisig or timelock, or requires coordination of several
  parties.
- `low`: the path exists only behind a long timelock with public visibility, or no current executable
  path was found at the pin.
- `unknown`: a coverage gap - the holder or the constraint could not be resolved.

Confidence rubric (how well the evidence class supports the rating):
- `high`: preferred-class evidence at the pin (runtime, storage, receipt) for every load-bearing claim.
- `medium`: at least one load-bearing claim rests on decoded logs or verified source only.
- `low`: discovery-class evidence or an inference carries a load-bearing claim.

## No averaging

A rating is the worst supported outcome on its surface as a whole - every `finding`-status check whose
`surface` is the rating key (its severity and the severities of its linked findings) and every finding
whose `surface` is the rating key - not the mean of the declared basis. If any is `critical`, the rating
is `critical`; a `high` finding sets a floor of `high`, a `medium` finding a floor of `medium` (validator:
E-RATING-CRITICAL-AVERAGED for every floor violation; rating order critical > high > medium > low;
`unknown`/`not_applicable` are governed by E-RATING-UNKNOWN-AS-LOW instead). Omitting the check from
`basis_check_ids` does not help (the surface is evaluated as a whole, and W-RATING-BASIS-INCOMPLETE
names the omission), and every finding must hang off a check (E-FINDING-UNLINKED). Example:
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

Forbidden as conclusions: "safe", "legit", "rug-proof", "risk-free", "cannot be rugged", "guaranteed",
"audited therefore safe", "liquidity is locked" without position, locker authority and pin, "renounced"
as a conclusion about the system, "team wallet"/"insider"/"dev" as labels without evidence, "profit"
without cost basis, and any phrasing that implies a favorable review predicts returns. The validator's
W-VERDICT-LANGUAGE is a regex heuristic over the report `## Verdict` section and
`manifest.verdict.answer` + `main_reasons`: "is/are/it's/was/looks/seems/remains/will be/considered/
deemed/definitely/completely/totally/fully/100%/perfectly" + optional filler + "safe/secure/riskless/
risk-free/rug-proof/safe investment/no risk", with a negation lookbehind for "not ", "never ", "no ",
"isn't ", "aren't ", "cannot be ". It is not a semantic judgment (`--strict` makes it an error; read
every hit).

## Check object

Every entry of `checks[]` carries exactly these nine keys (plus optional `limitation_id`); `check_id` is
unique per manifest and `surface` names the one rating key the check feeds:

```json
{ "check_id": "G-LAYER-ADMIN", "surface": "admin_treasury_reward_custody",
  "name": "Upgrade/admin/pause/rescue authority over the reward, vault, distributor or backing layer",
  "status": "finding", "severity": "high", "evidence_ids": ["E21", "E22"], "finding_ids": ["F7"],
  "reason": null, "pin_id": "P1" }
```

Core ids (`A-MINT` ... `H-DEV`, 22 in broad/formal; E-CHECK-CORE-MISSING otherwise) feed the surface in
the SKILL.md mapping; additional ids are `<Surface letter>-<UPPERCASE-SLUG>` (`B-LOCKER-AUTH`,
`G-FEE-ORIGIN`, `G-LAYER-ADMIN`) and deep-track ids `T-<TRACK>-<SLUG>`; each surface file's checks table
states the `surface` of every id it defines. Check ids starting with `E-` are surface-E (launch
integrity) checks; validator codes are a separate namespace (`E-SCHEMA`, `E-PIN-*`, `E-REPORT-*`, ...)
and never appear in `checks[]`. Status rules: `pass` needs evidence and carries no findings and no
severity; `finding` needs a severity at least as high as its linked findings' (higher draws
W-CHECK-SEVERITY-UNSUPPORTED) and at least one finding id of the same surface; `unknown`/`skipped`/
`not_applicable` need a reason and carry no severity; a check named in a limitation's
`affected_check_ids` cannot be `pass` or `not_applicable` (all E-CHECK-STATUS).

## Required sections (H2, exact text, in this order)

| Section | Content |
|---|---|
| `## Verdict` | Conditional answer (`verdict.answer` verbatim), conditions, main reasons with finding ids |
| `## Target identity` | Requested vs observed chain id, EIP-55 address (must appear here), metadata with statuses, code hash and size, proxy status/implementation/admin/upgrade authority, primary pin (block, hash, UTC), deployment status, packet `_freeze.sha256` |
| `## Ratings` | Table of the 11 surfaces in fixed order with rating, likelihood, confidence, coverage, time basis, basis checks, summary; the first two cells of each row are `| <surface key> | <rating> |` and must equal the manifest |
| `## Key findings` | Findings ordered by severity, each with id, proposition, confidence, evidence ids, pin or tx |
| `## Strongest contrary evidence` | The best evidence against the verdict, and why it does not overturn it |
| `## Unresolved questions` | What remains unknown, with the limitation or reason, and what would resolve it |
| `## What would change the conclusion` | Specific observable evidence that would flip the verdict or a rating |
| `## Recommendations` | Actions addressing observed deficiencies (below) |
| `## Coverage and limitations` | Every `L<n>` with kind, description, affected checks/addresses, retries; every discovery record `S<n>`; what was not searched; every validator warning explained |
| `## Declarations` | no real signing, no broadcast, no private keys requested, external content untrusted, simulation used/not and fork attestation, and the validator note: passing validates internal consistency only |
| `## Evidence ledger` | The 12-column ledger (render with `ledger.py`) |

Focused mode requires `## Verdict`, `## Coverage and limitations`, `## Evidence ledger`; include the
others when they carry content (E-REPORT-SECTIONS for a missing required heading; headings are matched
case-exactly, optional closing hashes and trailing spaces tolerated). Before scanning the body for the
target address, the headings, the Ratings table and the Verdict text, the validator strips HTML comments
and fenced code blocks, so text inside them satisfies nothing. Every address in the body must be in
`scope_addresses` (W-REPORT-UNSCOPED-ADDRESS; error with `--strict`).

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
  independent signers, or renounce it after the final epoch; re-test G-LAYER-ADMIN and G-REWARDS."
  (addresses a G finding)
- "Publish the compiler settings and reproduce the deployed runtime hash from source; until then
  H-DEV stays `source_unverified`."
Not acceptable: "do your own research", "monitor the situation", "consider the risks".

## Manifest, report, validator

- The manifest (`schemas/manifest.schema.json`, `manifest_version: "1.0"`) is the source of truth; the
  report is a rendering of it, and the validator (contract v1.1) checks that rendering against it.
- `report.path` is relative to the manifest and must resolve inside the manifest's directory (absolute
  paths and `..` escapes are E-REPORT-MISSING "path escapes manifest directory"; an absolute `--report`
  CLI argument is allowed because the operator chose it). `report.sha256` is the sha256 of the raw report
  file bytes (`ddcore.sha256_file`); empty or malformed is E-REPORT-HASH. Frontmatter keys
  (`evm_dd_report`, `mode`, `target_chain_id`, `target_address`, `target_symbol`, `primary_pin_id`,
  `primary_pin_block`, `primary_pin_block_hash`, `manifest_path`) must match the manifest; a UTF-8 BOM
  and leading blank lines before the opening `---` are tolerated, duplicate keys are E-REPORT-IDENTITY.
- Finalization loop: write the report -> set `report.sha256` -> validate -> if you edit the report
  (including the Declarations "validated on <date>" line), re-hash and re-validate; the last run must be
  on the final bytes.
- Validate:
  ```
  python3 <skill-root>/scripts/validate_report.py --manifest manifest.json --report report.md --strict
  python3 <skill-root>/scripts/ledger.py check --manifest manifest.json
  ```
  Exit 0 pass, 1 fail, 2 usage. `--json` prints machine-readable issues plus `"contract_version": "1.1"`.
  `--strict` promotes W-REPORT-UNSCOPED-ADDRESS, W-EVIDENCE-DISCOVERY-ONLY, W-VERDICT-LANGUAGE,
  W-CHECK-DISCOVERY-ONLY, W-RATING-BASIS-INCOMPLETE and W-DECL-FORK-ATTESTATION to errors.
- The closing note printed by the validator belongs in `## Declarations`: passing validates internal
  consistency of the manifest and report only; it does not establish RPC honesty, discovery completeness,
  or protocol safety. Read every warning; a warning you cannot explain in `## Coverage and limitations`
  is a gap in the report, not noise.

### Validator codes (contract v1.1; rule-by-rule notes in `references/evidence-rules.md`)

Errors (always fatal):

| Code | Fires when |
|---|---|
| E-SCHEMA | Structure or type violates the schema; duplicate pin/evidence/finding ids; a check aborted on malformed input |
| E-ADDR-MALFORMED | An address field is not 0x + 40 hex |
| E-ADDR-CHECKSUM | Mixed-case address fails EIP-55, or `target.address_checksum` is not the EIP-55 form of `requested.address` |
| E-CHAIN-MISMATCH | `requested.chain_id` != `observed.chain_id`, or `observed.rpc_chain_id_hex` decodes differently |
| E-CHAIN-UNPINNED | A chain referenced by scope, evidence, findings or the target has no pin; onchain-type evidence (`rpc_state`, `rpc_storage`, `bytecode`, `calldata`, `receipt`, `log_decoded`, `trace`, `simulation_counterfactual`) has neither a `pin_id` nor a non-placeholder `tx_hash`; a `pin_id` is on another chain than its row; `target.runtime.pin_id` is not on the target chain |
| E-PIN-PLACEHOLDER | Block hash all-zero / same-nibble / non-hex / wrong length, block 0, parentHash == hash; placeholder tx hashes or 32-byte hashes in `findings[].pin_or_tx`, `evidence[].tx_hash`, `target.deployment.tx_hash` (resolved), `proxy.implementation_code_hash`, `scope_addresses[].code_hash`; all-same-char `evidence[].artifact_sha256` |
| E-PIN-HEADER-MISMATCH | Pin number/hash/parentHash disagree with `captured_header`; two pins for one block with different hashes |
| E-PIN-TIME | Timestamp disagrees with the header; block timestamp more than 900 s after `captured_at_utc`; pins on one chain not monotonic (higher block, not strictly later timestamp) or same block with different timestamps; a timestamp that cannot be converted (that pin only) |
| E-PIN-PRIMARY | `primary_pin_id` missing or not on the target chain |
| E-META-STATUS | Metadata `value`/`status` contradiction (resolved with null, unresolved with a value, bad decimals or supply) |
| E-META-REPORT | Report `target_symbol` contradicts the manifest metadata status/value |
| E-SCOPE-TARGET | No scope entry for the target with chain_id == requested chain, role `token`, provenance `user_supplied`, runtime_status `contract`, code_hash == `target.runtime.code_hash` |
| E-SCOPE-RUNTIME | Runtime status vs code hash contradiction; material `unknown` without a limitation; proxy status with implementation missing/not scoped as `implementation`+`contract`; non-null admin/beacon without a `proxy_admin`/`beacon` scope role; the target's own scope entry not `material: true` |
| E-SCOPE-ADDRESS | An evidence, finding, limitation-affected or proxy address is not a scope entry on that chain |
| E-REPORT-MISSING | Report file absent; `report.path` or `fork_attestation_path` absolute or escaping the manifest directory |
| E-REPORT-HASH | sha256 of the raw report bytes != `report.sha256`, or `report.sha256` empty/malformed |
| E-REPORT-IDENTITY | Frontmatter missing/unparseable, duplicate keys, or chain/address/pin/symbol keys != manifest |
| E-REPORT-TARGET-ABSENT | The checksummed target address does not appear in the visible body |
| E-REPORT-SECTIONS | A required H2 heading is absent (all 11 in broad/formal; the 3 focused ones otherwise) |
| E-REPORT-RATINGS | `## Ratings` lacks a `| <surface key> | <rating> |` row for a manifest rating, or the rating cell differs; the first two cells are plain text (no backticks, bold or links) and are compared after collapsing whitespace |
| E-REPORT-VERDICT | `manifest.verdict.answer` (whitespace-normalized) does not occur in `## Verdict` |
| E-CHECK-CORE-MISSING | A core check id is absent in broad/formal mode |
| E-CHECK-STATUS | Duplicate check id; unknown surface; `pass`/`finding` without evidence; `unknown`/`skipped`/`not_applicable` without reason; `finding` without severity/finding id or with severity below its findings'; `pass`/`not_applicable` with finding ids or severity; `unknown`/`skipped` with severity or over a proven finding; linked finding on another surface; limited check marked `pass`/`not_applicable` |
| E-CHECK-DISCOVERY-ONLY | A no-threshold authority check (A-MINT, A-UPGRADE, A-SEIZE, A-RESTRICT, A-TAX, A-EXTCALL, A-ADMIN, B-CANON, B-PRINCIPAL, B-SIDE) is `pass` on discovery-class evidence only |
| E-EVIDENCE-DANGLING | A referenced evidence/finding/pin/limitation/basis-check id does not exist; `evidence[].block_number` != its pin's block; `pin_or_tx` neither a pin id nor a 0x+64hex tx; `is_historical` true with a pin id or false with a tx hash |
| E-FINDING-NO-EVIDENCE | A finding cites no evidence and its confidence is not `unknown` |
| E-FINDING-UNLINKED | A finding is referenced by no check's `finding_ids` |
| E-FINDING-CHAIN | A finding cites evidence on an unpinned chain, or on a different chain without being a bridge leg (surface `external_dependencies`, or an address scoped with role `bridge`) |
| E-FINDING-EVIDENCE-TYPE | `findings[].evidence_type` is not the type of any cited evidence row |
| E-RATING-MISSING | A rating key is absent in broad/formal mode |
| E-RATING-UNKNOWN-AS-LOW | `low` over unknown/skipped basis without qualification, with empty basis, or with `coverage: none`; `not_applicable` with empty basis or a non-`not_applicable` basis check; `coverage_qualified` with a `coverage_note` under 20 characters |
| E-RATING-CRITICAL-AVERAGED | A critical check/finding on the surface without rating `critical`; a `high`/`medium` finding above the rating (floor) |
| E-DECL-SIGNING | `no_real_signing`, `no_broadcast` or `no_private_keys_requested` is not exactly true |
| E-DECL-FORK | Simulation used without the fork/synthetic/counterfactual flags or block; `fork_chain_id` != target chain; simulation evidence while `used` is false; simulation evidence not flagged counterfactual |
| E-MODE-MISMATCH | Report frontmatter `mode` != manifest `mode` |

Warnings (fatal only when promoted by `--strict`, marked *):

| Code | Fires when |
|---|---|
| W-REPORT-UNSCOPED-ADDRESS * | An address in the visible report body is not a scope entry |
| W-VERDICT-LANGUAGE * | Unconditional safety phrasing (heuristic above) in `## Verdict` or in `verdict.answer`/`main_reasons` |
| W-EVIDENCE-DISCOVERY-ONLY * | A critical/high finding at proven/strongly_supported confidence rests only on discovery-class evidence |
| W-CHECK-DISCOVERY-ONLY * | Any other `pass` check rests only on discovery-class evidence |
| W-CHECK-SEVERITY-UNSUPPORTED | A `finding` check's severity is higher than every linked finding's |
| W-RATING-BASIS-INCOMPLETE * | A `finding`-status check on the surface is missing from `basis_check_ids` |
| W-FINDING-CROSS-CHAIN | A permitted bridge-leg finding cites evidence on another chain (confirm intent) |
| W-FINDING-PIN-MISMATCH | A finding is pinned at one pin while every cited row is pinned at another pin on the same chain |
| W-PIN-STALE | `captured_at_utc` is more than 7 days from the block timestamp |
| W-PIN-UNUSED | A pin's chain has no scope, evidence or finding entry |
| W-DECL-FORK-ATTESTATION * | `fork_attestation_path` set but the file is absent, or simulation used with a null path |
| W-LIMITATION-UNREFERENCED | A limitation is referenced by no check, scope entry or `affected_check_ids` |

`ledger.py check` errors: E-FINDING-NO-EVIDENCE, E-EVIDENCE-DANGLING, E-SCHEMA (duplicate ids,
malformed entries); warnings: W-EVIDENCE-UNREFERENCED (an evidence row no finding or check cites),
W-LEDGER-FIELD-EMPTY (a free-text ledger column - `alternatives`, `coverage`, `stale_conditions` - is
empty), W-LEDGER-UNKNOWN-NO-EVIDENCE (a finding with confidence `unknown` cites no evidence; the
`coverage` column must say why).

## Check status -> allowed rating outcomes

| Basis check status (with severity) | Effect on the surface rating |
|---|---|
| `pass` (>= 1 evidence id, none discovery-only) | Supports `low` only if every other basis check is `pass`, `not_applicable`, or the rating is coverage-qualified; otherwise neutral (judgment) |
| `finding`, severity `critical` | Rating MUST be `critical` (validator-enforced: E-RATING-CRITICAL-AVERAGED) |
| `finding`, severity `high` | Rating at least `high` (validator-enforced floor: E-RATING-CRITICAL-AVERAGED) |
| `finding`, severity `medium` | Rating at least `medium` (validator-enforced floor: E-RATING-CRITICAL-AVERAGED) |
| `finding`, severity `low` or `info` | Rating at least `low`; `info` alone does not raise it (judgment; not validator-enforced) |
| `unknown` (reason required) | Rating `unknown`, or the level implied by other findings; `low` only with `coverage_qualified: true` + `coverage_note` (validator-enforced: E-RATING-UNKNOWN-AS-LOW) |
| `skipped` (reason required) | Same as `unknown`; never a pass |
| `not_applicable` (reason with evidence of absence) | `not_applicable` only when ALL basis checks are `not_applicable` (validator-enforced); otherwise ignored for the level |

The likelihood column is independent of the level: a `critical` capability held by a 5-of-9 timelocked
multisig may carry `likelihood: low`; the rating stays `critical` because the capability exists.
