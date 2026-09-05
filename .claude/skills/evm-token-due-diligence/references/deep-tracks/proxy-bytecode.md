# Deep track: proxy authority and critical bytecode reconstruction

Track code `PROXY`. Check ids `T-PROXY-<SLUG>`. Feeds surface A (`A-UPGRADE`, `A-ADMIN`, `A-EXTCALL`,
`A-MINT`, `A-SEIZE`), surface H (`H-DEV` source correspondence) and any surface whose contract is unverified.
Run it because "verified" on an explorer is a claim about a source file, not a proof about the bytes that
execute, and because a proxy's behavior is whatever its current implementation is plus whatever its admin
can make it become. Escalate one rung at a time; each rung answers a narrower question at a higher cost, and
most questions close at rung 1 or 2.

## Trigger

- `rpc_probe.py` reports a non-empty EIP-1967/EIP-1822/beacon slot, an EIP-1167 minimal proxy, or
  `DELEGATECALL` in a contract that is not a recognized proxy (`custom`).
- Source is unverified, or verified source does not match the runtime code hash, for any material contract
  (token, implementation, admin, locker, curve, distributor, vault).
- `scripts/selector_scan.py` flags risky selectors or opcodes without a source explanation.
- An admin action (upgrade, admin change, initializer, role grant) is asked about historically.
- A surface needs to know whether a path is reachable (not just present) and static reading cannot say.

## Minimum evidence

| Item | Source | Why |
|---|---|---|
| Runtime bytecode and `code_hash` at `P1` for the proxy and every resolved implementation/beacon/admin | `eth_getCode` at the pin; `scripts/rpc_probe.py --address … --block <P1>` | every statement below is about specific bytes at a specific block |
| Proxy slot reads at `P1` | `rpc_probe.py` (EIP-1967 implementation/admin/beacon, EIP-1822 logic) | who executes, who can change it |
| Selector and opcode inventory | `python3 <skill-root>/scripts/selector_scan.py --code-file runtime.hex --json` | the cheapest map of what can be called |
| For rung 2: candidate source (explorer-verified or repository) with declared compiler version and settings | explorer / repository (`explorer`, `repository` evidence — untrusted until byte-compared) | correspondence must be established, not assumed |
| For rung 3: event logs of admin actions and receipts of the calls that emitted them | paged `eth_getLogs` on proxy, admin, beacon | history of authority |
| For rung 4: a disposable fork attestation | `python3 <skill-root>/scripts/fork_guard.py --rpc http://127.0.0.1:8545 --expect-chain-id <id> --expect-fork-block <P1> --out attestation.json` (exit 0) | no simulation write without it |

Verify-at-use table (protocol-standard signatures recomputed with ddcore; confirm on an observed log):

| Event / selector | Value (recompute) | Meaning |
|---|---|---|
| `Upgraded(address)` | `0xbc7cd75a20ee27fd9adebab32041f755214dbc6bffa90cc0225b39da2e5c2d3b` | implementation changed (EIP-1967 proxies) |
| `AdminChanged(address,address)` | `0x7e644d79422f17c01e4894b5f4f588d331ebfa28653d42ae832dc59e38c9798f` | proxy admin changed |
| `BeaconUpgraded(address)` | `0x1cf3b03a6cf19fa2baba4df148e9dcabedea7f8a5c07840e207e5c089be95d3e` | beacon pointer changed |
| `OwnershipTransferred(address,address)` | `0x8be0079c531659141344cd1fd0a4f28419497f9722a3daafe3b4186f6b6457e0` | owner changed |
| `Initialized(uint8)` / `Initialized(uint64)` | `0x7f26b83f…2498` / `0xc7f505b2…81d2` | initializer ran (two library generations) |
| `RoleGranted` / `RoleRevoked (bytes32,address,address)` | `0x2f878811…6f0d` / `0xf6391f5c…171b` | role membership changed |
| `upgradeTo(address)` / `upgradeToAndCall(address,bytes)` | `0x3659cfe6` / `0x4f1ef286` | upgrade selectors (proxy or implementation side) |
| `changeAdmin(address)` / `upgrade(address,address)` / `upgradeAndCall(address,address,bytes)` | `0x8f283970` / `0x99a88ec4` / `0x9623609d` | admin-contract selectors |
| `implementation()` / `admin()` | `0x5c60da1b` / `0xf851a440` | admin-only view on transparent proxies; calling them from another address is routed to the implementation |

## Procedure

Rung 1 — runtime, selectors, slots (always).
1. Read the proxy's code and the four standard slots at `P1`; resolve implementation, admin, beacon (and the
   beacon's `implementation()`), and record each with its own `code_hash`. If none is set but the code contains
   `DELEGATECALL`, mark `proxy.status: custom` and search the runtime for `PUSH32` constants that look like
   storage slots; read each candidate with `eth_getStorageAt` at `P1` and treat a value that decodes to a
   contract address as a candidate implementation.
2. Run `selector_scan.py` on the proxy code **and** on each implementation. Classify hits: upgrade, admin change,
   initializer, mint, burn-from, pause, blacklist, arbitrary call, `SELFDESTRUCT`, `CREATE2`. The tool's caveat
   applies literally: presence is not reachability, absence is not safety (a fallback-routed or diamond-style
   contract shows few selectors).
3. Resolve the admin chain: proxy admin → its owner → multisig threshold/timelock (`owner()`, `getOwners()`,
   `getThreshold()`, `getMinDelay()` as applicable, each at `P1`), until an EOA, a multisig with listed
   signers, or an unresolvable contract. Each hop is a scope row with role and provenance `rpc_derived`.
4. Close here if the question was "who can upgrade, and is the path present": record findings under
   `A-UPGRADE`/`A-ADMIN` and stop.

Rung 2 — verified predecessors and compiler metadata (when source correspondence matters).
5. Extract the CBOR metadata tail from the runtime (last two bytes are the big-endian length of the tail):
   ```python
   import re
   def metadata_tail(code_hex):
       b = bytes.fromhex(code_hex[2:]); n = int.from_bytes(b[-2:], "big")
       if n + 2 > len(b): return None, 0
       t = b[-(n + 2):-2]; f = lambda p: re.search(p, t, re.S)
       ipfs = f(rb"\x64ipfs\x58\x22(.{34})"); bz = f(rb"\x65bzzr[01]\x58\x20(.{32})"); sv = f(rb"\x64solc\x43(.{3})")
       return {"ipfs": ipfs.group(1).hex() if ipfs else None, "bzzr": bz.group(1).hex() if bz else None,
               "solc": ".".join(str(x) for x in sv.group(1)) if sv else None}, n + 2
   ```
   Older compilers encode `solc` as a text string rather than three bytes; a missing tail means metadata was
   stripped or the code was not produced by solc. Record the values as `bytecode` evidence.
6. Compare by code hash before comparing by source: list other deployments (same chain or others, each with
   its own pin) whose runtime `code_hash` equals this one; the implementation, decoder and findings about the
   *code* transfer between identical runtimes, findings about *state* never do. Deduplicate work this way.
7. Establish correspondence. Compile the candidate source with the recorded compiler version and settings
   when a compiler is available in the environment (none is bundled with this skill; document what was used),
   then compare the produced runtime with the deployed runtime after removing the metadata tail from both and
   after accounting for immutables (32-byte words at the offsets the compiler reports as immutable
   references; without compiler output, accept only differences that are whole 32-byte words at identical
   offsets and confirm each against a constructor argument or an `immutable` in the source). Only when every
   remaining difference is closed this way is the source `source_verified`. An explorer's "verified" badge
   alone stays `explorer`; a byte-identical match to another deployment that you did verify inherits
   `source_verified` with the reference noted.
8. Decompiler output is discovery. It may guide which selectors to trace and which slots to read; it is
   recorded as `manual_note` or `source_unverified` and never as the deployed implementation's source.

Rung 3 — storage layout reads and historical calls (when authority history or hidden state matters).
9. Read state through the layout: with verified source, use its storage layout (including EIP-1967
   unstructured slots and ERC-7201 namespaced slots, both keccak-derived — recompute, do not recall); without
   it, probe by calling the view (`owner()`, `paused()`) and finding the slot whose `eth_getStorageAt` value at
   `P1` matches, then read that slot at historical pins.
10. Fetch the authority history: `Upgraded`, `AdminChanged`, `BeaconUpgraded`, `OwnershipTransferred`,
    `Initialized`, `RoleGranted`/`RoleRevoked` on the proxy, admin and beacon, paged with declared windows.
    For each event take the receipt, the calldata selector, `tx.from`, and `eth_getCode` of the new
    implementation at that block (its `code_hash`). Build the implementation timeline: block, tx, caller,
    old hash, new hash, whether an initializer ran in the same receipt (`Initialized`) and with what calldata.
11. Cover calls that emit nothing (custom proxies, `SSTORE` of a new implementation without an event):
    enumerate transactions to the proxy/admin from the admin chain (via `trace_filter` when available, or an
    explorer transaction list as discovery, confirmed per receipt) and diff storage with
    `debug_traceTransaction` prestate diff mode for each candidate.

Rung 4 — deeper reconstruction or simulation (only when a reachability or effect question remains).
12. Verify the fork first: `fork_guard.py` must exit 0 and the attestation path must be recorded in
    `declarations.simulation.fork_attestation_path` with `fork_verified_disposable: true`,
    `synthetic_accounts_only: true`, `results_labeled_counterfactual: true`. No real key is ever used; the
    admin is impersonated by the fork client's own tooling or replaced through state overrides on
    `eth_call`/`debug_traceCall` (both read-only in `ddcore.RpcClient`).
13. Run the counterfactual: "does `upgradeTo(<synthetic impl>)` from the resolved admin succeed", "does the
    mint path change `totalSupply`", "does the arbitrary-call path move the token balance". Each result is
    `simulation_counterfactual` evidence with `counterfactual: true`, the fork block, the exact calldata, and
    the balance/storage deltas read after the call — a success flag or an emitted event alone is not a
    result.
14. Route out: protocol-wide invariant work, multi-contract exploit construction, or economic attack modeling
    belongs to a separate audit workflow when one is available; record the hand-off as an unresolved
    question, not as a finding.

## Stopping condition

- **Closed at rung 1**: upgrade authority and admin chain resolved at `P1` with slot reads; the question was
  about presence and authority.
- **Closed at rung 2**: correspondence established (or refuted) by byte comparison; `H-DEV` and the affected
  A checks can cite `source_verified` (or must keep `source_unverified`).
- **Closed at rung 3**: the implementation timeline and admin actions are complete for the declared range;
  hidden state changes are enumerated or excluded by storage diffs.
- **Closed at rung 4**: the reachability/effect question has a counterfactual answer on a verified fork.
- **Blocked**: no compiler, tracing or fork is available for the rung the question needs; stop, record the
  limitation, leave the check `unknown`, and do not substitute decompiler text or an explorer badge.

## Output rows

SYNTHETIC EXAMPLE — placeholders; not a live finding.

Evidence rows:
```json
{ "evidence_id": "E81", "chain_id": <chain_id>, "address": "<proxy>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "rpc_storage",
  "artifact": "artifacts/probe-<proxy>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getStorageAt", "address": "<proxy>", "slots": ["<EIP1967 impl>", "<EIP1967 admin>", "<EIP1967 beacon>"], "block": "<P1 hex>"},
  "decoding_basis": "EIP-1967 slots from ddcore constants; low 20 bytes as address",
  "summary": "implementation <impl>, admin <admin>, beacon empty" }

{ "evidence_id": "E82", "chain_id": <chain_id>, "address": "<impl>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "bytecode",
  "artifact": "artifacts/selectors-<impl>.json", "artifact_sha256": "<sha256>",
  "query": {"tool": "scripts/selector_scan.py", "args": "--code-file impl.hex --json"},
  "decoding_basis": "PUSH4 walk; risky-signature table; opcode flags; metadata tail parsed (ipfs, solc)",
  "summary": "selectors: upgradeToAndCall, mint(address,uint256); DELEGATECALL present; solc <x.y.z>; ipfs <hash>" }

{ "evidence_id": "E83", "chain_id": <chain_id>, "address": "<proxy>", "pin_id": null,
  "tx_hash": "<tx hash>", "block_number": <block>, "evidence_type": "log_decoded",
  "artifact": "artifacts/upgrade-history.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getLogs", "address": "<proxy>", "topics": ["<Upgraded topic0>"], "windows": "artifacts/proxy-ranges.json"},
  "decoding_basis": "Upgraded(address) topic1 = implementation; eth_getCode at each block for code_hash",
  "summary": "<n> upgrades; latest at block <b> by <caller>; initializer ran in <k> of them" }

{ "evidence_id": "E84", "chain_id": <chain_id>, "address": "<proxy>", "pin_id": "P1", "tx_hash": null,
  "block_number": <fork_block>, "evidence_type": "simulation_counterfactual", "counterfactual": true,
  "artifact": "artifacts/sim-upgrade.json", "artifact_sha256": "<sha256>",
  "query": {"fork_attestation": "attestation.json", "call": {"from": "<resolved admin, impersonated>", "to": "<proxy>", "data": "0x3659cfe6<synthetic impl>"}, "reads_after": ["EIP1967 impl slot"]},
  "decoding_basis": "state override on eth_call; slot read after call",
  "summary": "COUNTERFACTUAL: upgrade from <admin> succeeds on the fork at block <fork_block>; slot changed" }
```

Check rows:
```json
{ "check_id": "T-PROXY-AUTHORITY", "surface": "token_controls",
  "name": "Implementation, admin and beacon resolved at P1 with the full admin chain",
  "status": "finding", "severity": "high", "evidence_ids": ["E81", "E82"], "finding_ids": ["F51"], "reason": null, "pin_id": "P1" }

{ "check_id": "T-PROXY-SOURCE-CORRESPONDENCE", "surface": "development_disclosure",
  "name": "Published source reproduces the deployed runtime after metadata and immutables are accounted for",
  "status": "unknown", "severity": null, "evidence_ids": [], "finding_ids": [],
  "reason": "no compiler available in this environment; explorer verification not reproduced (L10)", "pin_id": "P1", "limitation_id": "L10" }

{ "check_id": "T-PROXY-UPGRADE-HISTORY", "surface": "token_controls",
  "name": "Implementation timeline complete for deployment..P1 with callers and initializers",
  "status": "pass", "severity": null, "evidence_ids": ["E83"], "finding_ids": [], "reason": null, "pin_id": "P1" }

{ "check_id": "T-PROXY-REACHABILITY", "surface": "token_controls",
  "name": "Upgrade path executes from the resolved admin (counterfactual on a verified fork)",
  "status": "finding", "severity": "high", "evidence_ids": ["E84"], "finding_ids": ["F51"], "reason": null, "pin_id": "P1" }
```

Finding row:
```json
{ "finding_id": "F51", "surface": "token_controls", "severity": "high",
  "proposition": "At P1 the token at <proxy> on chain <chain_id> delegates to <impl> (code hash <hash>); the EIP-1967 admin slot holds <admin>, whose owner() is the EOA <eoa>; a counterfactual upgradeTo from <admin> on a verified fork at block <fork_block> replaced the implementation slot with no timelock.",
  "chain_id": <chain_id>, "address": "<proxy>", "pin_or_tx": "P1",
  "evidence_ids": ["E81", "E82", "E84"], "evidence_type": "rpc_storage", "confidence": "proven",
  "alternatives": "The implementation source is unverified here; behavior after an upgrade is whatever the next implementation does.",
  "coverage": "slots and admin chain full at P1; source correspondence not reproduced (L10)",
  "stale_conditions": "any Upgraded/AdminChanged/OwnershipTransferred after P1",
  "is_historical": false }
```

Unresolved questions:
- "Whether the verified source at the explorer reproduces <impl>'s runtime (no compiler available, L10)."
- "Whether the custom proxy's implementation was ever changed without an event (trace methods unavailable)."

## What remains unknown if it cannot be completed

- Who can change the token's behavior and whether the change path is live — `A-UPGRADE` and
  `token_controls` cannot be rated `low`; state `unknown` or the rung reached.
- Whether the published source is the deployed code (`H-DEV` `unknown`; every reading of that source is
  `source_unverified`).
- Whether balance- or supply-changing paths exist behind the fallback that selectors did not reveal.
- The history of upgrades and initializations, so "renounced"/"immutable since launch" claims remain claims.

## Common mistakes

- Reading `implementation()`/`admin()` through the proxy from an ordinary address and concluding "no proxy"
  when the call was routed to the implementation; read the slots.
- Treating an explorer's verified badge, or "Similar Match", as `source_verified`.
- Comparing bytecode with the metadata tail included, then reporting "does not match".
- Ignoring immutables and reporting "matches except a few bytes"; the few bytes are the comparison.
- Quoting decompiler pseudo-code as the contract's source.
- Running a simulation without `fork_guard.py` exit 0 (E-DECL-FORK), or reporting a fork result without the
  counterfactual label.
- Stopping at the proxy admin without resolving its owner and threshold.
- Treating `Upgraded` logs as the complete upgrade history for a custom proxy.
- Escalating to rung 4 for a question rung 1 already answered.
