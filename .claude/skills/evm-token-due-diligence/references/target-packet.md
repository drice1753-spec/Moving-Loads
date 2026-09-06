# Target packet

The target packet is the single frozen object every lane and every later read cites. It exists so that
work done in parallel or over several sessions cannot drift to a different chain, address or block, and so
that "current state" means one block with one hash. Build it first, complete it with the user's framing,
hash it, and then do not edit it (make a new packet if the target or the pin must change).

Skeleton: `templates/target-packet.json`. Machine-filled fields come from `scripts/rpc_probe.py`.

The target is always the TOKEN whose economic system is in question. If the user named a vault, pool,
locker or distributor, derive the token from it first (`positions(tokenId)` -> token0/token1, the pool
key, `token()`/`asset()`), confirm chain id + address with the user in one line, then probe the token;
the named contract goes into `related_contracts` with its own role. A chain NAME from the user is mapped
to the commonly cited id in `references/chains/chain-verification.md` section 6 as the REQUESTED id, with
the mapping recorded in `requested.source`; `eth_chainId` on the endpoint confirms it or stops the run.

## Fields

| Field | Filled by | Meaning |
|---|---|---|
| `packet_version`, `tool`, `generated_at_utc` | probe | Provenance of the packet itself |
| `requested.chain_id`, `requested.address`, `requested.address_checksum`, `requested.chain_name`, `requested.source` | probe (`chain_name`/`source`: you; the probe leaves them null) | Exactly what the user asked for; `chain_id` is authoritative, `chain_name` is a label; `source` records where the address came from and any chain-name mapping ("user said 'Base'; mapped to commonly cited id 8453, confirmed by eth_chainId") |
| `observed.chain_id`, `observed.rpc_chain_id_hex`, `observed.rpc_endpoint_redacted`, `observed.client_version` | probe | What the endpoint reported; chain id read live, endpoint redacted (`client_version_note` appears when `web3_clientVersion` was unavailable) |
| `identity.status`, `identity.requested`, `identity.observed`, `identity.detail` | probe | `MATCH` (continue); `OBSERVED_ONLY` (no `--chain-id` given, chain adopted from the endpoint: confirm with the user, re-run with `--chain-id`, never freeze in this state); `CHAIN_MISMATCH` (stop; recorded here, NOT as a limitation); `UNVERIFIED` (chain id unreadable; limitation, nothing can be pinned) |
| `cache.status`, `cache.chain_id`, `cache.note` | probe | Whether the `--cache` file was `used`, `ignored` (recorded for another chain) or `none`, and the chain id it is bound to |
| `pin` (manifest pin object `P1` with `captured_header`), `pinned_block_hex` | probe | The block every later read uses; header is the raw `eth_getBlockByNumber` result; `purpose` records the tag used (`finalized`, or `latest` with the reason) |
| `runtime.code_hash`, `runtime.code_size`, `runtime.runtime_status`, `runtime.is_contract`, `runtime.pin_id`, `runtime.proxy.*` | probe | keccak256 of runtime code; `runtime_status` `contract` / `eoa` / `unknown` (`unknown` whenever `eth_getCode` failed or returned a non-string - then `coverage_status` is `partial` with a limitation, never "not a contract"); EIP-1967/1822 slot values, EIP-1167 detection, implementation code hash; `proxy.status` is `unknown` whenever a slot read failed (limitation), never `not_proxy`; `upgrade_authority` stays null until you resolve it under A-UPGRADE |
| `metadata.name/symbol/decimals/total_supply` (`value`, `status`, `source`, `reason`, `selector`, `raw`, `qualifier`), `metadata.accounting_model` | probe (`accounting_model`: you) | Resolved from the target with statuses `resolved` / `nonstandard` / `unresolved`; raw return data preserved. For an `eoa` target every field is `unresolved` and carries the qualifier "no runtime code at this address at the pin; return data cannot come from this address" |
| `probes[]` | probe | Standard probes `owner()`, `getOwner()`, `paused()` and each `--call`; `status` `ok` / `reverted` / `unavailable` (limitation recorded) / `empty`; a revert is a fact about the contract, not a limitation; for an `eoa` target every entry carries the same qualifier and an `ok`/`reverted` result is marked INCONSISTENT |
| `deployment` | you | `status`, `tx_hash`, `block_number`, `deployer`, `factory`, `deterministic`, `reason`; stays `unresolved` until verified by receipt (`contractAddress`) |
| `candidate_pools[]`, `related_contracts[]` | you (architecture pass) | Pools by exact address or full pool key; every contract/key from the architecture pass with role and provenance |
| `decision_question`, `requirement_frame`, `scope_statement`, `materiality_rules`, `known_limitations[]` | you | The user's question, the standard the verdict is judged against, what is in/out of scope, the materiality rule, what the user already knows is unavailable |
| `coverage_status`, `limitations[]`, `calls[]`, `failed_calls[]` | probe | `complete`/`partial`; manifest-shaped limitations; every RPC call for replay (reverts at the pin are recorded in `failed_calls` with outcome `reverted`) |
| `declarations`, `notes[]` | probe | Read-only, no signing/broadcast/keys; standing caveats |
| `_freeze` | you | `sha256` of the packet at freeze time, `frozen_at_utc`, who froze it (see "Freezing the packet") |

## Packet-to-manifest mapping

The packet is a working object; the manifest (`schemas/manifest.schema.json`) is the deliverable. When
copying packet objects into a manifest:

- Drop every `_`-prefixed key and these schema-disallowed keys: `identity`, `cache`, `pinned_block_hex`,
  `probes`, `calls`, `failed_calls`, `coverage_status`, `observed.client_version_note`,
  `runtime.is_contract`, `runtime.runtime_status`, `runtime.proxy.implementation_code_size`,
  `metadata.*.selector`, `metadata.*.raw`, `metadata.*.qualifier`, `declarations.read_only_methods_only`,
  `notes`, `tool`, `packet_version`, `generated_at_utc` (the manifest has its own `generated_at_utc` and
  `tooling`).
- Move: `requested.address_checksum` -> `target.address_checksum`; `runtime.runtime_status` -> the
  target's `scope_addresses[0].runtime_status`; `pin` -> `pins[0]` (and `primary_pin_id: "P1"`);
  `limitations[]` -> `coverage.limitations[]`; `related_contracts[]` -> `scope_addresses[1..]` unchanged
  (same shape); `candidate_pools[]` -> scope entries with role `pool` plus a `discovery[]` record; the raw
  metadata return data and every probe result -> evidence rows (`rpc_state`, `artifact` = the packet
  path or an extracted file), cited via `metadata.*.evidence_id` and from the checks; an `eoa` qualifier
  -> the metadata field's `reason`; `decision_question`, `requirement_frame`, `scope_statement`,
  `materiality_rules`, `known_limitations` -> the same keys under `target`.
- Copy unchanged: `requested.{chain_id, chain_name, address, source}` -> `target.requested`;
  `observed.{chain_id, rpc_chain_id_hex, rpc_endpoint_redacted, client_version}` -> `target.observed`;
  `runtime.{code_hash, code_size, pin_id, proxy}` -> `target.runtime`; `metadata.*.{value, status,
  source, reason}` -> `target.metadata.*`; `deployment` -> `target.deployment`;
  `declarations.{no_real_signing, no_broadcast, no_private_keys_requested}` -> `declarations`.
- Add what the manifest requires and the packet does not carry: `manifest_version: "1.0"`, `mode`,
  `declarations.simulation` (`{"used": false}` plus nulls unless a verified fork was used - then the
  full object with `fork_attestation_path` relative to the manifest), `declarations.external_content_untrusted:
  true`, `verdict` (`question`, `answer`, `conditional` - true for GO-WITH-CONDITIONS / NO-GO-under-frame
  answers - and `conditions`), `report` (`path` relative to the manifest, `sha256` of the raw file
  bytes), and the target itself as `scope_addresses[0]`: `{address: address_checksum, chain_id, role:
  "token", provenance: "user_supplied", runtime_status: "contract", code_hash: runtime.code_hash, pin_id:
  "P1", material: true}` (E-SCOPE-TARGET for the five identity conditions; E-SCOPE-RUNTIME if `material`
  is not true).

## Filling the packet

1. Run the probe (env fallback `EVM_DD_RPC_URL` if `--rpc` is omitted):
   ```
   python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<target> --chain-id <requested> \
       --block finalized --call "pendingOwner()" --cache rpc-cache.json --out target-packet.json
   ```
   `--block` accepts `latest|safe|finalized|N`. Use `finalized` where the endpoint supports the tag; fall
   back to `latest` if the endpoint rejects it and record which tag was used (pin `purpose`, coverage).
   Use `--block N` to pin a specific block (for example to match a pin from an earlier session). Add
   `--call "sig(type,..):arg,.."` for extra static-argument reads (`"balanceOf(address):0x.."`,
   `"hasRole(bytes32,address):0x..,0x.."`) or raw calldata hex. Exit codes: 3 chain mismatch - STOP; 1
   identity `UNVERIFIED` or pin failed (packet written with the limitation, nothing pinned) - STOP and
   report the limitation, then fix or replace the endpoint and re-run; 0 continue; 2 usage.
2. Check `identity.status`. `MATCH`: continue. `OBSERVED_ONLY`: the chain was adopted from the endpoint
   because no `--chain-id` was given; confirm the intended chain with the user, re-run with
   `--chain-id N`, and require `MATCH` before Step 2 - never freeze an `OBSERVED_ONLY` packet.
   `CHAIN_MISMATCH`: stop (below). `UNVERIFIED`: the chain id could not be read; fix the endpoint or
   record the limitation and stop - nothing can be pinned.
3. Read `runtime.runtime_status` and `runtime.proxy.status`. `eoa` means the address has no code at the
   pin: the metadata and probe results are qualified, and the "token" is not a contract - re-check the
   address with the user. `unknown` means a limitation blocked the read: nothing about code or proxy
   status is known yet. Any proxy status but `not_proxy` means the implementation, admin/beacon and their
   owners go into `related_contracts` now, before any behavior claim.
4. Read `metadata.*.status`. Do not "fix" nonstandard or unresolved values; the report will carry them
   (`target_symbol: unresolved` or `nonstandard:<value>`).
5. Fill `decision_question` verbatim from the user, then `requirement_frame` (for example "rug resistance
   for a 30-day hold", "can a holder of 0.5% of supply exit within 5% degradation"; when the user gave
   none, "rug resistance and exit at the stated size over a 30-day hold", labeled an assumption),
   `scope_statement`, `known_limitations` (for example "no archive endpoint available", "explorer API not
   authorized"). Keep `materiality_rules` as written: no monetary threshold applies to authority
   discovery.
6. Run the architecture pass (next section) and fill `candidate_pools` and `related_contracts`. In
   `focused` mode the pass is limited to the question's dependencies (`references/workflow-modes.md`,
   focused step 3); record the rest as a `known_limitations` entry of kind `out_of_scope` ("authority
   surfaces not enumerated: focused run") so the reader sees what was not looked at.
7. Freeze.

## Cheap architecture pass (checklist)

Purpose: find every contract or key that can change the answer before spending effort on any one surface.
Cheap means pinned reads, slot reads, event queries with bounded ranges and one selector scan - no
replay, no traces yet.

Find every contract or key that can:
- [ ] change balances: mint, burn-from, rebase, balance rewrite, `transferFrom` without allowance, hidden
      admin transfer
- [ ] restrict transfers: pause, blacklist, whitelist, cooldown, tx/wallet limits, trading-enabled gates,
      anti-bot modules
- [ ] remove principal: LP withdraw, `decreaseLiquidity`, burn-position, locker withdraw/rescue,
      arbitrary call from a custodian, approval/operator paths on position NFTs
- [ ] upgrade behavior: proxy admin, beacon owner, implementation setter, `DEFAULT_ADMIN_ROLE`,
      timelock proposers/executors, module registries - on the token AND on every reward, vault,
      distributor or backing contract (`G-LAYER-ADMIN`)
- [ ] collect fees: fee switches, tax rates and routes, fee recipients, protocol fee controllers, hook fees
- [ ] allocate rewards: distributors, vaults, epoch processors, merkle roots and who sets them
- [ ] enforce claimed utility: redemption contracts, oracles, bridges, keepers, collateral custodians

How to find them:
- Proxy slots: `rpc_probe.py` reads EIP-1967 implementation/admin/beacon and EIP-1822 logic slots and
  detects EIP-1167; for beacons call `implementation()` on the beacon.
- Owner and roles: `owner()`, `getOwner()`, `pendingOwner()`, `hasRole`/`getRoleMemberCount` reads,
  `OwnershipTransferred` and `RoleGranted` logs from deployment to P1; for each holder read code (EOA vs
  contract), and for multisigs read owners/threshold; for timelocks read `getMinDelay()` and proposers.
- Pools: `PoolCreated` (v3) / `PairCreated` (v2) / `Initialize` (v4 PoolManager) logs filtered by the token
  address, plus `scripts/pool_math.py` to compute expected addresses or PoolIds and confirm them against
  the events (the init code hash is "verify at use time").
- Lockers and custodians: holders of the v2 LP token (Transfer logs of the pair), owners of v3 position
  NFTs (`ownerOf`, `Transfer` logs on the position manager), v4 position owner via the position manager;
  then read the custodian's code hash and selectors.
- Fee recipients: config reads (`feeTo`, `feeRecipient`, `treasury`, `marketingWallet`, hook fee
  config) and the counterparties of observed fee transfers.
- Reward contracts: token approvals/allowances from vaults, `Approval` logs, and the emitters of
  reward/claim events that mention the token.
- Runtime dedupe: group scope addresses by `code_hash`; scan each distinct runtime once with
  `python3 <skill-root>/scripts/selector_scan.py --code-file <hex file> --json`, then bind findings per address.
- Secondary probes: every `rpc_probe.py` run against another contract (admin, pool, locker, vault)
  passes `--chain-id N --block <P1 block number>` from the frozen packet's `pin.block_number`. A
  secondary packet produced without it pins to `latest`, names that pin `P1` too, and its probes may
  NOT be cited as P1; before copying probes into evidence assert `packet.pin.block_hash ==
  frozen.pin.block_hash`.

Record each address as a `related_contracts[]` entry (`address`, `chain_id`, `role`, `provenance`,
`provenance_detail`, `runtime_status`, `code_hash`, `pin_id`, `material`) so it can be copied into
`scope_addresses` unchanged. `scope_addresses[0]` is always the target itself (role `token`, provenance
`user_supplied`, `runtime_status` `contract`, `code_hash` = `runtime.code_hash` - E-SCOPE-TARGET otherwise -
and `material: true` - E-SCOPE-RUNTIME otherwise). Every other address you will name in the report -
quote asset (role `other`,
label "quote asset"), forwarders, funders, holders, exchange deposits - needs its own entry with a
neutral role, because `--strict` turns an unscoped address in the report body into an error
(W-REPORT-UNSCOPED-ADDRESS), and every evidence/finding/limitation address must be scoped
(E-SCOPE-ADDRESS).

## Materiality rule

No monetary threshold applies to the discovery of mint, upgrade, seizure, transfer-restriction,
arbitrary-call or LP-removal authority: a key that can do any of these is material at any balance. Monetary
thresholds may bound other discovery (which holders to classify, which side pools to inventory, which fee
transfers to trace) and must then be declared in `discovery[].materiality_threshold`.

## Batching and caching

- Group independent reads at the same pinned block and run them in one pass with one `--cache` file
  before analysing anything; `ddcore.RpcClient` sends one request per call, so batching is about
  ordering and reuse, not JSON-RPC batch arrays.
- The cache key is chain/address/block/query: `ddcore.ResponseCache` keys each entry on
  `[chain_id, scheme, host, port, redacted path, method, params]`, stores the chain id per entry and in
  the file header, and refuses reads and writes until `rpc_probe.py` has bound the live `eth_chainId`
  (`RpcClient.set_chain_id`). A file recorded for another chain is refused (`RpcCoverageError`, kind
  `rpc_error`, "cache file belongs to chain X"); floating tags (`latest`, `pending`, `safe`, `finalized`,
  `earliest`) are never cached; reverts at pinned blocks are cached as reverts. One cache file per target
  packet.
- Bound every `eth_getLogs` range and record it; paginate by block range on rate limits and record the
  pagination in `discovery[]`.
- Deduplicate analysis by code hash; never deduplicate storage reads across addresses.

## Freezing the packet

1. Confirm `identity.status == MATCH`, `pin` present, `decision_question` and `scope_statement` filled.
2. Compute the frozen hash: the sha256 of the packet JSON serialized with `sort_keys=True,
   separators=(",", ":")` after setting every value in `_freeze` to null:
   ```
   python3 -c "import hashlib,json,sys;p=json.load(open(sys.argv[1]));p['_freeze']={k:None for k in p.get('_freeze',{})};print(hashlib.sha256(json.dumps(p,sort_keys=True,separators=(',',':')).encode()).hexdigest())" target-packet.json
   ```
   This is independent of whitespace and of the `_freeze` values, so the hash can live inside the file.
3. Write it into `_freeze.sha256` with `frozen_at_utc` and `frozen_by`; from then on the file is
   read-only. Lanes and reviewers verify by re-running the same command and comparing with
   `_freeze.sha256` (`packet_sha256` in every lane fragment).
4. Every lane, every check `pin_id` and the manifest `primary_pin_id` cite `P1` from this packet. If a
   later pin is needed, add `P2` in the manifest; never replace `P1`.

## When the observed chain differs from the requested chain

Stop. Do not read anything else from that endpoint, do not "helpfully" continue on the observed chain, and
never substitute the same symbol's deployment there. Report to the user: requested chain id, observed
chain id (`eth_chainId`), redacted endpoint, and that the run cannot proceed until an endpoint for the
requested chain is supplied (if the chain id came from a chain-name mapping, ask which of the two chains
was meant). `rpc_probe.py` exits 3 and writes the packet with `identity.status = CHAIN_MISMATCH`
(`identity.requested`, `identity.observed`, `identity.detail`) so the attempt is on record; the mismatch
is an identity failure and is not added to `limitations`. The validator rejects any manifest whose
requested and observed chains differ (E-CHAIN-MISMATCH). If the user actually meant the other chain, that
is a new target packet with a new decision question, not an edit.
