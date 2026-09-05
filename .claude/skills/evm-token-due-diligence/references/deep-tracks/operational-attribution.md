# Deep track: narrow operational attribution

Track code `ATTRIB`. Check ids `T-ATTRIB-<SLUG>`. Feeds every surface that names a role holder (A admin
chain, B custodians, E launch actors, F recipients, G distributors) and the language rules in
`references/attribution.md`. Run it because diligence needs to say *which address holds which operational
role* — who can upgrade, who removed principal, who receives fees — and that is a question about keys and
receipts, not about people. This track is narrow by design: it links an address to a **project-control
role** using authenticated evidence, and it stops there. It never becomes an identity investigation.

## Trigger

- A finding needs a role label stronger than the neutral defaults (`launch_signer`, `fee_recipient`,
  `funder`, observed controller) — for example "the address that performed the launch also holds the admin
  role".
- Two or more scope addresses must be related for a conclusion to hold ("the fee recipient and the upgrade
  admin are the same operator").
- A project publicly claims that a specific address is its deployer, treasury, multisig or admin, and the
  claim must be matched to deployed behavior.
- A user asks "is this the team's wallet"; the answer is bounded to what authenticated control evidence
  shows.
Do not run it to find out who a person is; that is outside this skill by default.

## Minimum evidence

| Item | Source | Why |
|---|---|---|
| Receipts of the control actions being attributed (deployment, initializer, upgrade, role grant, pause, fee-recipient change, principal removal) with `tx.from` and, for contract callers, the trace to the originating signer | `eth_getTransactionReceipt`, `eth_getTransactionByHash`, `trace`/`debug_traceTransaction` | an address is an operator because it *did* an operator thing, proven at receipt level |
| Current role state at `P1` (owner, roles, multisig owners and threshold, proxy admin) | `scripts/rpc_probe.py --rpc URL --address <contract> --chain-id N --block <P1 block> --call "owner()"` and role/owner reads; proxy track rung 1 | roles are a pinned state |
| For any claim from a channel: the original artifact preserved (page, post, metadata field) with retrieval time, recorded as `website`/`repository`/`token_metadata` evidence | fetch and store; hash it | claims are untrusted evidence; they are matched, not believed |
| For a signed message: the message text, signature, claimed signer and the recovery result from a tool that can perform secp256k1 recovery (none is bundled in this skill's stdlib helpers) | external tool, recorded as `manual_note` with the exact inputs; otherwise `unknown` | an unrecovered signature is a string |
| ENS or name-service evidence: the resolver/reverse-record reads at `P1` on the chain where the registry lives (own pin) | `eth_call` on the registry/resolver | a name is a claim by whoever set the record; the read shows who controls it |

## Procedure

1. Start every address at its neutral role. Assign from observed action only: `deployer` (sent the creation
   tx or was the factory caller), `launch_signer` (signed the launch/first-buy transaction), `fee_recipient`
   (receives fees per configuration reads), `funder` (first native inflow source), `owner`/`role_holder`/
   `multisig`/`timelock` (from state reads), `holder`. "Observed controller" is a phrase, not a role enum:
   use it in prose for an address that performed admin actions, with the role enum that fits the action.

2. Collect authenticated project-control evidence. Only these classes can raise an attribution above
   "observed":
   - **control actions**: the address (or the signer behind a multisig/contract call, from traces or the
     multisig's execution event) performed launch or admin actions on scope contracts — receipts required;
   - **onchain-signed statements**: a message whose recovered signer is a control-holding key, published
     through a channel the project itself controls (its verified contract metadata, an ENS text record set by
     that key, a contract event emitted by that key) — recovery required, else the statement stays a claim;
   - **contract-verified metadata / name records**: ENS reverse records, resolver text records, or values a
     control key wrote onchain — read at a pin; the writer of the record is the evidence, the text is not.
   Everything else (website text, social posts, explorer name tags, token metadata `name`/`symbol`, README
   files) is discovery-class and cannot by itself support a `proven` or `strongly_supported` attribution.

3. Record what the following do **not** prove, and do not let them carry an attribution alone:
   - shared funding source (an exchange withdrawal or a bridge serves many users);
   - shared routers, aggregators, launch platforms or settlement destinations (shared infrastructure);
   - timing coincidence (blocks, minutes, "right after");
   - deterministic deployment (`CREATE2`/factory addresses are predictable by anyone with the salt and init
     code; a matching address proves the inputs, not the operator);
   - same exchange deposit address family or the same custodial label;
   - code similarity or copied templates (forks are common);
   - polished or awkward writing, and any inference about tooling or AI authorship.
   These are recorded as observations with neutral wording ("addresses A and B were both funded by C").

4. Grade the link with the confidence classes and use the wording that matches:
   - `proven`: the address performed the control action itself (receipt, `tx.from`) — "the address that
     deployed the token also executed the upgrade at tx X";
   - `strongly_supported`: two independent authenticated links (for example a multisig execution whose signer
     set includes the address, plus an ENS record set by the same key naming the project) — "the operator of
     the admin multisig is also the fee recipient";
   - `inference`: one authenticated link plus circumstantial observations — must be labeled "inference" in
     the proposition and listed with its alternatives;
   - `unknown`: only discovery-class material — the role stays neutral and the claim is recorded as a claim.
   Never write "owned by", "the team", "the developer", "insider", "the same person" at any confidence class;
   write roles and keys: "the launch signer", "the address holding `DEFAULT_ADMIN_ROLE` at P1".

5. Stop at commingling. When a link runs through an exchange, a custodial service, a mixer, a shared router
   balance, or any address with material unrelated inflows, the attribution ends there. Record the delivered
   amount and the stop reason as in `references/deep-tracks/proceeds-reconciliation.md` step 7; do not
   continue on the far side.

6. Respect the privacy boundary. By default do not perform broad personal-identity searches, do not link
   an address to a legal name, employer, location or social account, and do not compile profiles. The only
   identity-adjacent statements permitted are those the project itself published through an authenticated
   control channel (step 2) and that are needed to answer the decision question — quote them as claims with
   their provenance. If the user explicitly asks for identity work, treat it as a separate task outside this
   skill's procedures and say so; the manifest for this run records `out_of_scope` as the limitation kind.

7. Write the attribution finding so it can be checked: which role, which address, which action(s), which
   receipts, which pinned reads, which alternatives were considered, and what would falsify it (a signer-set
   change, a role revocation, a contrary signed statement).

## Stopping condition

- **Closed**: every role label used in the report is either a neutral default or backed by authenticated
  project-control evidence at the stated confidence class, with alternatives listed.
- **Bounded**: the question concerned one relation ("is the fee recipient the upgrade admin"); stop when it
  is answered at receipt level.
- **Blocked**: signatures cannot be recovered, traces are unavailable for contract-mediated actions, or
  the only material is discovery-class; the role stays neutral, the check is `unknown`, and the report says
  "no authenticated control link found", never "unrelated".

## Output rows

SYNTHETIC EXAMPLE — placeholders; not a live finding.

Evidence rows:
```json
{ "evidence_id": "E111", "chain_id": <chain_id>, "address": "<proxy_admin>", "pin_id": null,
  "tx_hash": "<upgrade tx>", "block_number": <block>, "evidence_type": "receipt",
  "artifact": "artifacts/receipt-<tx>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getTransactionReceipt", "tx": "<upgrade tx>", "also": "eth_getTransactionByHash for tx.from"},
  "decoding_basis": "status 0x1; Upgraded(address) on <proxy>; tx.from = <eoa>",
  "summary": "<eoa> executed upgradeTo on <proxy> via <proxy_admin>" }

{ "evidence_id": "E112", "chain_id": <chain_id>, "address": "<token>", "pin_id": null,
  "tx_hash": "<creation tx>", "block_number": <block>, "evidence_type": "receipt",
  "artifact": "artifacts/receipt-<creation tx>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getTransactionReceipt", "tx": "<creation tx>"},
  "decoding_basis": "contractAddress == <token>; tx.from = <eoa>",
  "summary": "<eoa> deployed <token>" }

{ "evidence_id": "E113", "chain_id": <chain_id>, "address": null, "pin_id": null, "tx_hash": null,
  "block_number": null, "evidence_type": "website",
  "artifact": "artifacts/site-team-page-<date>.html", "artifact_sha256": "<sha256>",
  "query": {"url_redacted": "<redacted>", "retrieved_at_utc": "<ts>"},
  "decoding_basis": "raw page; untrusted claim",
  "summary": "page claims <addr> is the project treasury; no signed statement; recorded as a claim only" }
```

Check rows:
```json
{ "check_id": "T-ATTRIB-CONTROL-LINK", "surface": "token_controls",
  "name": "Role attribution rests on receipts of control actions and pinned role reads",
  "status": "finding", "severity": "info", "evidence_ids": ["E111", "E112"], "finding_ids": ["F81"], "reason": null, "pin_id": "P1" }

{ "check_id": "T-ATTRIB-CLAIM-MATCH", "surface": "development_disclosure",
  "name": "Public control claims matched to deployed behavior or recorded as unmatched claims",
  "status": "unknown", "severity": null, "evidence_ids": ["E113"], "finding_ids": [],
  "reason": "no signed statement or onchain record from a control key supports the website's treasury claim; claim recorded, not matched", "pin_id": "P1" }

{ "check_id": "T-ATTRIB-NEGATIVE", "surface": "historical_launch_integrity",
  "name": "Non-probative relations (shared funding, routers, timing, CREATE2, settlement) recorded as observations only",
  "status": "pass", "severity": null, "evidence_ids": ["E112"], "finding_ids": [], "reason": null, "pin_id": null }

{ "check_id": "T-ATTRIB-BOUNDARY", "surface": "development_disclosure",
  "name": "No personal-identity search performed; attribution limited to roles and keys",
  "status": "pass", "severity": null, "evidence_ids": ["E113"], "finding_ids": [], "reason": null, "pin_id": null }
```

Finding row:
```json
{ "finding_id": "F81", "surface": "token_controls", "severity": "info",
  "proposition": "The EOA <eoa> deployed <token> (tx <creation tx>) and executed the implementation upgrade at tx <upgrade tx>; at P1 it is the owner() of <proxy_admin>. Role: deployer and upgrade authority holder (proven). No stronger label is supported.",
  "chain_id": <chain_id>, "address": "<eoa>", "pin_or_tx": "P1",
  "evidence_ids": ["E111", "E112"], "evidence_type": "receipt", "confidence": "proven",
  "alternatives": "The key may be operated by a service or shared among several operators; the receipts prove the key acted, not who holds it.",
  "coverage": "control actions on <proxy> and <token> full; multisig signer sets not applicable",
  "stale_conditions": "OwnershipTransferred or AdminChanged after P1", "is_historical": false }
```

Unresolved questions:
- "Whether the website's treasury address <addr> is controlled by a project key; no authenticated link
  found."
- "Whether the launch signer <addr> and the fee recipient <addr2> are operated together; only a shared
  funder was observed, which is non-probative."

## What remains unknown if it cannot be completed

- Whether the operator of one role also holds another (for example whether the fee recipient can also
  upgrade), so cross-surface conclusions that depend on "the same operator" must be split into per-address
  statements.
- Whether public claims about official addresses are true; they stay claims with provenance.
- Nothing about identity — by design; the report states that identity was out of scope.

## Common mistakes

- Promoting a shared funding source, a shared router, `CREATE2` predictability, or timing into "same owner".
- Writing "the team", "the dev", "insider", "the same person" instead of a role and an address.
- Accepting an explorer name tag, a website team page, or token metadata as attribution.
- Treating an unrecovered signature as a signed statement.
- Attributing a multisig action to one signer without the execution event or trace that names signers.
- Continuing an attribution chain through an exchange or custodian.
- Performing identity searches because "it would help"; the boundary holds until the user explicitly opens
  a separate task, and this skill's manifest then records it as out of scope.
- Describing sale/rebuy routing between cohort wallets as coordination; it is market-mediated
  redistribution unless an authenticated link says otherwise (`references/deep-tracks/launch-cohort.md`).
