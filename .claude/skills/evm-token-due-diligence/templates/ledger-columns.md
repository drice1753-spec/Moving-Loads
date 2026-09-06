# Finding-to-evidence ledger: column definitions

The ledger is the part of a report a skeptical reader checks first: one row per finding, each row
re-derivable from a preserved artifact. It is rendered from the manifest
(`python3 <skill-root>/scripts/ledger.py render --manifest manifest.json --format md`) and checked
(`python3 <skill-root>/scripts/ledger.py check --manifest manifest.json`, exit 1 on findings without
evidence or dangling ids; warnings W-EVIDENCE-UNREFERENCED, W-LEDGER-FIELD-EMPTY for an empty free-text
column, W-LEDGER-UNKNOWN-NO-EVIDENCE for an `unknown`-confidence finding with no evidence).
`validate_report.py` applies the stricter rules named per column. Columns, in order: `finding_id`,
`proposition`, `chain_id`, `address`, `pin_or_tx`, `artifact_or_query`, `decoding_basis`, `evidence_type`,
`confidence`, `alternatives`, `coverage`, `stale_conditions`:

| # | Column | Allowed values | Meaning |
|---|---|---|---|
| 1 | `finding_id` | `F<n>` (`^F[0-9]+$`), unique | Matches `findings[].finding_id`; every finding is referenced from the `finding_ids` of at least one check of the same surface (E-FINDING-UNLINKED, E-CHECK-STATUS) |
| 2 | `proposition` | Free text, one testable statement | Exactly what is claimed, bound to chain + address + pin or tx. No adjectives, no conclusions about the whole system ("owner can call `mint(address,uint256)` without cap at P1", not "token is mintable and dangerous") |
| 3 | `chain_id` | Integer as observed via `eth_chainId`; must have a pin | The chain the proposition is about; a chain without a pin is rejected (E-CHAIN-UNPINNED); cited evidence on another chain is rejected unless the finding is a bridge leg (E-FINDING-CHAIN) |
| 4 | `address` | EIP-55 address that is a `scope_addresses` entry on this chain, or `null` | The contract or key the proposition is about (E-SCOPE-ADDRESS when unscoped); `null` only for cohort or discovery propositions, which then name the `S<n>` record in `coverage` |
| 5 | `pin_or_tx` | `P<n>` (existing pin) or `0x` + 64 hex (non-placeholder) | `P<n>` for current-state propositions (`is_historical: false`); a tx hash for historical execution (`is_historical: true`); the pairing is enforced (E-EVIDENCE-DANGLING, E-PIN-PLACEHOLDER); a finding pinned at a different pin than all its evidence draws W-FINDING-PIN-MISMATCH |
| 6 | `artifact_or_query` | Evidence ids `E<n>` plus artifact path (relative to the manifest) or `inline`, and the reproducible query (method, params, block) | Enough to re-run the read; endpoint redacted |
| 7 | `decoding_basis` | ABI fragment / event signature / selector / `source_verified:<method>` / `source_unverified` / `raw` | How raw bytes became the claim; `raw` when no decoding was applied |
| 8 | `evidence_type` | `rpc_state` `rpc_storage` `bytecode` `calldata` `receipt` `log_decoded` `trace` `source_verified` `source_unverified` `explorer` `dashboard` `website` `repository` `token_metadata` `simulation_counterfactual` `manual_note` | The strongest type supporting the row; it must be the type of one of the cited evidence rows (E-FINDING-EVIDENCE-TYPE); discovery-class types alone cannot carry a material finding (W-EVIDENCE-DISCOVERY-ONLY) |
| 9 | `confidence` | `proven` `strongly_supported` `inference` `unknown` | `proven`: preferred evidence directly establishes it; `strongly_supported`: preferred evidence plus a small stated inference; `inference`: consistent but alternatives remain; `unknown`: not testable (then `evidence_ids` may be empty, and `coverage` says why) |
| 10 | `alternatives` | Free text, non-empty | Competing explanations considered and why they were rejected or remain; "none identified after considering X, Y" - never a bare "none" |
| 11 | `coverage` | Free text, non-empty | What was searched and what was not: block ranges, pagination, endpoints, limitation ids (`L<n>`), discovery ids (`S<n>`) |
| 12 | `stale_conditions` | Free text, non-empty | The observable events that would invalidate the row: upgrade, ownership or role change, position transfer or liquidity change, a new block for quotes, a reorg of the pin |

Rows for the same finding on two chains are two rows (one per chain and pin). Rows never cite a pin on a
different chain than `chain_id`. A finding's `severity` feeds its surface's rating floor: a critical row
forces `critical`, a high or medium row sets the floor (E-RATING-CRITICAL-AVERAGED), whether or not the
check is listed in the rating's basis.

## Example row (SYNTHETIC EXAMPLE, not a live finding)

All values below are invented for illustration: the addresses are repeated-digit placeholders, the hash
is fabricated, and the chain id is a stand-in that must be replaced by the value read from `eth_chainId`
at use time.

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F3 | At P1 the EIP-1967 admin slot of 0x1111111111111111111111111111111111111111 resolves to 0x2222222222222222222222222222222222222222, which has code and returns `getMinDelay() = 0`, so the implementation can be replaced without delay by that admin's owner. | 31337 (synthetic; verify at use time) | 0x1111111111111111111111111111111111111111 | P1 | E9 `evidence/E9.json` eth_getStorageAt [0x1111..1111, EIP1967_ADMIN_SLOT, 0x<pinned block hex>]; E10 `evidence/E10.json` eth_getCode [0x2222..2222, 0x<pinned block hex>]; E11 `evidence/E11.json` eth_call {to: 0x2222..2222, data: selector("getMinDelay()")} at 0x<pinned block hex> | EIP-1967 admin slot = keccak256("eip1967.proxy.admin") - 1; `getMinDelay()` selector via ddcore.selector; uint decode of the 32-byte return | rpc_storage | proven | The admin contract could enforce a delay by a path other than `getMinDelay()`; its selectors were scanned (E12) and no alternative delay read was found. Its owner is resolved separately under A-ADMIN (F4). | Slots read at P1 only; admin owner resolution in F4; no historical `AdminChanged` replay (out of scope for this row). | `AdminChanged` or `Upgraded` event after P1; a change to the admin's `getMinDelay()`; a reorg of block P1. |

The same row in the manifest is `findings[]` entry `F3` with `evidence_ids: ["E9","E10","E11"]`,
`surface: token_controls`, `severity: high`, `pin_or_tx: "P1"`, `is_historical: false`, and is cited by
check `A-UPGRADE` (`surface: token_controls`, `status: finding`, `severity: high`); the address
0x1111111111111111111111111111111111111111 is a scope entry on chain 31337, and `token_controls` is then
rated at least `high`.
