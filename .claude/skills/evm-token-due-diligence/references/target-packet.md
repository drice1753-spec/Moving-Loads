# Target packet

The target packet is the single frozen object every lane and every later read cites. It exists so that
work done in parallel or over several sessions cannot drift to a different chain, address or block, and so
that "current state" means one block with one hash. Build it first, complete it with the user's framing,
hash it, and then do not edit it (make a new packet if the target or the pin must change).

Skeleton: `templates/target-packet.json`. Machine-filled fields come from `scripts/rpc_probe.py`.

## Fields

| Field | Filled by | Meaning |
|---|---|---|
| `packet_version`, `tool`, `generated_at_utc` | probe | Provenance of the packet itself |
| `requested.chain_id`, `requested.address`, `requested.address_checksum`, `requested.chain_name`, `requested.source` | probe (chain_name/source: you) | Exactly what the user asked for; `chain_id` is authoritative, `chain_name` is a label |
| `observed.chain_id`, `observed.rpc_chain_id_hex`, `observed.rpc_endpoint_redacted`, `observed.client_version` | probe | What the endpoint reported; chain id read live, endpoint redacted |
| `identity.status`, `identity.detail` | probe | `MATCH`, `CHAIN_MISMATCH` (stop), or `UNVERIFIED` (limitation) |
| `pin` (manifest pin object `P1` with `captured_header`), `pinned_block_hex` | probe | The block every later read uses; header is the raw `eth_getBlockByNumber` result |
| `runtime.code_hash`, `runtime.code_size`, `runtime.is_contract`, `runtime.pin_id`, `runtime.proxy.*` | probe | keccak256 of runtime code; EIP-1967/1822 slot values, EIP-1167 detection, implementation code hash, `upgrade_authority` (null until you resolve it under A-UPGRADE) |
| `metadata.name/symbol/decimals/total_supply` (`value`, `status`, `source`, `reason`, `selector`, `raw`) | probe | Resolved from the target with statuses `resolved` / `nonstandard` / `unresolved`; raw return data preserved |
| `probes[]` | probe | Standard probes `owner()`, `getOwner()`, `paused()` and each `--call`; `status` `ok` / `reverted` / `unavailable` (limitation recorded) / `empty`; a revert is a fact about the contract, not a limitation |
| `deployment` | you | `status`, `tx_hash`, `block_number`, `deployer`, `factory`, `deterministic`, `reason`; stays `unresolved` until verified by receipt (`contractAddress`) |
| `candidate_pools[]`, `related_contracts[]` | you (architecture pass) | Pools by exact address or full pool key; every contract/key from the architecture pass with role and provenance |
| `decision_question`, `requirement_frame`, `scope_statement`, `materiality_rules`, `known_limitations[]` | you | The user's question, the standard the verdict is judged against, what is in/out of scope, the materiality rule, what the user already knows is unavailable |
| `coverage_status`, `limitations[]`, `calls[]`, `failed_calls[]` | probe | `complete`/`partial`; manifest-shaped limitations; every RPC call for replay |
| `declarations`, `notes[]` | probe | Read-only, no signing/broadcast/keys; standing caveats |
| `_freeze` | you | `sha256` of the packet at freeze time, `frozen_at_utc`, who froze it |

Keys beginning with `_` are documentation. When copying packet objects into a manifest, drop them and
drop keys the schema does not allow (`runtime.is_contract`, `proxy.implementation_code_size`,
`metadata.*.selector`, `metadata.*.raw`); move raw metadata return data into evidence rows and cite them
via `metadata.*.evidence_id`.

## Filling the packet

1. Run the probe (env fallback `EVM_DD_RPC_URL` if `--rpc` is omitted):
   ```
   python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<target> --chain-id <requested> \
       --block latest --call "pendingOwner()" --cache rpc-cache.json --out target-packet.json
   ```
   Use `--block N` to pin a specific block (for example to match a pin from an earlier session). Add
   `--call "sig(type,..):arg,.."` for extra static-argument reads (`"balanceOf(address):0x.."`,
   `"hasRole(bytes32,address):0x..,0x.."`) or raw calldata hex.
2. Check `identity.status`. `MATCH`: continue. `CHAIN_MISMATCH`: stop (below). `UNVERIFIED`: the chain id
   could not be read; fix the endpoint or record the limitation and stop - nothing can be pinned.
3. Read `runtime.proxy.status`. Anything but `not_proxy` means the implementation, admin/beacon and their
   owners go into `related_contracts` now, before any behavior claim.
4. Read `metadata.*.status`. Do not "fix" nonstandard or unresolved values; the report will carry them
   (`target_symbol: unresolved` or `nonstandard:<value>`).
5. Fill `decision_question` verbatim from the user, then `requirement_frame` (for example "rug resistance
   for a 30-day hold", "can a holder of 0.5% of supply exit within 5% degradation"), `scope_statement`,
   `known_limitations` (for example "no archive endpoint available", "explorer API not authorized").
   Keep `materiality_rules` as written: no monetary threshold applies to authority discovery.
6. Run the architecture pass (next section) and fill `candidate_pools` and `related_contracts`.
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
      timelock proposers/executors, module registries
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

Record each address as a `related_contracts[]` entry (`address`, `chain_id`, `role`, `provenance`,
`provenance_detail`, `runtime_status`, `code_hash`, `pin_id`, `material`) so it can be copied into
`scope_addresses` unchanged.

## Materiality rule

No monetary threshold applies to the discovery of mint, upgrade, seizure, transfer-restriction,
arbitrary-call or LP-removal authority: a key that can do any of these is material at any balance. Monetary
thresholds may bound other discovery (which holders to classify, which side pools to inventory, which fee
transfers to trace) and must then be declared in `discovery[].materiality_threshold`.

## Batching and caching

- Group independent reads at the same pinned block and run them in one pass with one `--cache` file
  before analysing anything; `ddcore.RpcClient` sends one request per call, so batching is about
  ordering and reuse, not JSON-RPC batch arrays.
- The cache key is chain/address/block/query, implemented as sha256(host, method, params) by
  `ddcore.ResponseCache`; floating tags (`latest`, `pending`, `safe`, `finalized`, `earliest`) are never
  cached. Verify `eth_chainId` live each session before trusting a cache file.
- Bound every `eth_getLogs` range and record it; paginate by block range on rate limits and record the
  pagination in `discovery[]`.
- Deduplicate analysis by code hash; never deduplicate storage reads across addresses.

## Freezing the packet

1. Confirm `identity.status == MATCH`, `pin` present, `decision_question` and `scope_statement` filled.
2. Compute the hash: `sha256sum target-packet.json` (or `python3 -c "import hashlib,sys;
   print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" target-packet.json`).
3. Write it into `_freeze.sha256` with `frozen_at_utc`; from then on the file is read-only. (Adding the
   hash changes the file; the frozen hash is of the content excluding `_freeze`, which is why `_freeze`
   sits last - recompute over the file with `_freeze` values set to null when checking.)
4. Every lane, every check `pin_id` and the manifest `primary_pin_id` cite `P1` from this packet. If a
   later pin is needed, add `P2` in the manifest; never replace `P1`.

## When the observed chain differs from the requested chain

Stop. Do not read anything else from that endpoint, do not "helpfully" continue on the observed chain, and
never substitute the same symbol's deployment there. Report to the user: requested chain id, observed
chain id (`eth_chainId`), redacted endpoint, and that the run cannot proceed until an endpoint for the
requested chain is supplied. `rpc_probe.py` exits 3 and writes the packet with
`identity.status = CHAIN_MISMATCH` so the attempt is on record; the validator rejects any manifest whose
requested and observed chains differ (E-CHAIN-MISMATCH). If the user actually meant the other chain, that
is a new target packet with a new decision question, not an edit.
