# Surface A - Token code and control

Rating key: `token_controls`. Core checks: `A-MINT`, `A-UPGRADE`, `A-SEIZE`, `A-RESTRICT`, `A-TAX`,
`A-EXTCALL`, `A-ADMIN`. Read this file for any question about mint, pause, blacklist, tax, upgrade,
owner, roles, "renounced", or "what can they change or take"; always in `broad` mode.

## Purpose

Establish, at the pinned block, every authority that can change balances, restrict transfers, take
tokens, or replace the code that decides those things - and who holds each authority today. Two
answers are required and must be kept apart: what the CURRENT code permits, and what an administrator
could INTRODUCE by replacing the code (upgradeability dominates every other finding on this surface).

## What can change the verdict

- Any live mint, seizure, balance-rewrite, pause, blacklist or arbitrary-call authority held by a key
  that is not time-locked and not multi-party. No monetary threshold applies to discovering these.
- An upgrade path (proxy admin, beacon owner, diamond owner, custom implementation setter): the code
  read at P1 is then only the code as of P1.
- The identity of the holder of each authority: EOA vs multisig (threshold, owner set, modules) vs
  timelock (delay, proposers, executors, open executor) vs another contract with its own admin.
- Configurable taxes and limits whose maximum is unbounded or admin-settable, and exemptions that let
  privileged wallets bypass what ordinary holders face.
- A "renounced" or "no owner" claim that leaves roles, an implementation admin, an external
  controller contract, a hardcoded privileged address, or a locker/vault admin in place.
- Source that does not correspond to the deployed runtime (the analysis was of the wrong code).

## Procedure

Work on runtime bytecode and storage first; read source only after correspondence is established.
Every read cites P1 (`target-packet.json` -> `pin`); never `latest`.

1. Resolve the runtime set. From the packet take `runtime.code_hash`, `runtime.proxy.status`,
   `implementation`, `admin`, `beacon`. Save the raw `eth_getCode` response for the target AND for the
   implementation (and the beacon's `implementation()` for beacon proxies) at P1 as `bytecode` evidence;
   the packet keeps only hash and size. Deduplicate by code hash (identical bytecode is analyzed once,
   but findings bind to each address because storage differs).
2. Scan selectors and opcodes on EVERY runtime in the set, not only the proxy:
   ```
   python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E1-token-code.hex --json > evidence/E2-token-selectors.json
   python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E3-impl-code.hex --json > evidence/E4-impl-selectors.json
   ```
   Read the tool's caveat literally: a selector's presence is not reachability, its absence is not
   safety (OpenZeppelin v5 tokens route every balance change through an internal `_update` override
   that has no selector of its own; custom proxies dispatch through fallback). Record DELEGATECALL,
   SELFDESTRUCT, CALL, CREATE, CREATE2 flags per runtime.
3. Apply the source-correspondence rule before reading any published source. Treat source as
   `source_verified` only when the compiled runtime's keccak256 equals the code hash at P1, or when a
   recompilation with the recorded compiler version and settings matches byte-for-byte outside the CBOR
   metadata trailer and declared immutables, with the differences listed. An explorer "verified" badge,
   a repository, or decompiler output is `source_unverified` / `repository` (discovery only). If
   correspondence cannot be established, work from bytecode, storage, historical calls and traces, and
   record the gap as a limitation (`source_unavailable` or `other`) - never as a token finding.
4. `A-MINT` - inventory every supply-increase path, then resolve each holder of it:
   - explicit `mint(address,uint256)` / `mint(uint256)` / `issue` / `reward` and their guards
     (`onlyOwner`, `MINTER_ROLE`, `minters(address)` mapping, `bridge()`/`isBridge` minters);
   - hidden minters: reward or airdrop distributors and bridges that hold mint authority are minters;
     add each as a scope address (`reward_distributor`, `bridge`) and resolve ITS owner and proxy status;
   - rebasing multipliers (`rebase`, `setIndex`, `setRewardMultiplier`, `index()`): an admin changes
     every balance without a Transfer event;
   - balance rewrites (`setBalance`, `updateBalance`, `adjust`, any admin function that writes the
     balances mapping) and shares-based accounting (`sharesOf`, `totalShares`, `getPooledTokenByShares`)
     where the units a holder sees are a function of an admin-controlled rate;
   - a cap (`cap()`) bounds the amount only if the cap setter does not exist or is time-locked.
   Read the current values with `rpc_probe.py --call` (raw return data is preserved in `probes[]`):
   ```
   python3 <skill-root>/scripts/rpc_probe.py --address 0x<token> --chain-id N --block <P1 block> \
       --call "cap()" --call "minters(address):0x<candidate>" --call "hasRole(bytes32,address):0x<MINTER_ROLE>,0x<candidate>" \
       --cache rpc-cache.json --out packet-A-mint.json
   ```
   Corroborate with behavior: `Transfer` logs from the zero address after launch, and `totalSupply()`
   drift between two pinned blocks that the event sum does not explain (escalate; see below).
5. `A-SEIZE` - `burnFrom` without allowance consumption, `forceTransfer`, `seize`, `confiscate`,
   `clawback`, `recover(address,address)`, `adminTransfer`, `transferFrom` overrides that skip allowance
   for privileged callers, and balance rewrites (already listed). Where source is unverified, a
   historical `trace` of a privileged call that moved a third party's balance is the decisive evidence.
6. `A-RESTRICT` - pause (`paused()`, `pause()`), blacklist/whitelist (`isBlacklisted`, `blacklist`,
   `bots`, `isExcluded`, `whitelist`), cooldowns (`cooldown`, `lastTrade`), per-tx and per-wallet limits
   (`maxTxAmount`, `maxWallet`), trading gates (`tradingActive`, `enableTrading`, `launch`, `startBlock`).
   Read each current value at P1. A gate that is open at P1 with a setter still held is a finding about
   authority, not about the current value.
7. `A-TAX` - buy, sell and wallet-to-wallet taxes; exemptions (`isExcludedFromFees`) and who can set
   them; the maximum each setter accepts (a `require(fee <= MAX)` visible in verified source or absent);
   tax recipients and whether the recipient setter lives in the token or in a separate contract; swap-back
   thresholds that turn the tax into an external call (step 8). Record current rates as basis points at
   P1 and the configurable ceiling as a separate row; hand both to `C-TAX-NET`.
8. `A-EXTCALL` - external calls on the transfer path (dividend trackers, swap-back to a router, hooks,
   `_beforeTokenTransfer`/`_update` callbacks to another contract), `delegatecall` and `selfdestruct`
   presence, and arbitrary-call functions (`execute`, `call`, `multicall`, `rescueTokens`,
   `withdrawETH`, `sweep`). For each: who can call it, what targets it can reach, whether the target is
   admin-settable. A transfer path that calls an admin-settable address is an upgrade path in disguise.
9. `A-UPGRADE` - classify and resolve the authority, one row per layer:
   - EIP-1967 transparent: admin slot -> ProxyAdmin contract -> its `owner()` -> that owner's type;
   - UUPS: implementation exposes `upgradeToAndCall(address,bytes)` (and `proxiableUUID()`); the
     authority is whatever `_authorizeUpgrade` checks - read `owner()`/roles ON THE PROXY (storage lives
     there) and treat unverified source as unknown authority;
   - beacon: beacon slot -> beacon `implementation()` and `owner()`; `upgradeTo` on the beacon changes
     every proxy behind it at once;
   - EIP-2535 diamonds: `diamondCut(...)`, loupe `facets()`/`facetAddress(bytes4)`; each facet is an
     implementation; owner via the ownership facet;
   - custom: DELEGATECALL whose target is loaded from storage or set by a selector such as
     `setImplementation`/`upgrade`; escalate to `references/deep-tracks/proxy-bytecode.md`.
   The token's admin contracts (controller, tax-config, ProxyAdmin) can themselves be proxies: repeat.
   Record the implementation admin SEPARATELY from the token owner; they are often different keys.
10. `A-ADMIN` - resolve who holds each authority at P1 and who can change the holder:
    - Ownable: `owner()`, `pendingOwner()` (two-step); a pending owner is a future authority.
    - AccessControl: enumerate members by replaying `RoleGranted(bytes32,address,address)` and
      `RoleRevoked(bytes32,address,address)` logs from deployment to P1 (`eth_getLogs`, topic0 =
      `ddcore.keccak256_hex(b"RoleGranted(bytes32,address,address)")`), net grants minus revokes per
      role, then CONFIRM each surviving member with `hasRole(bytes32,address)` at P1 (replay alone can
      miss non-standard grant paths). Read `getRoleAdmin(role)` for every role: the admin of a role can
      re-grant it. Role ids are keccak256 of the role name for named roles and `0x00..00` for the
      default admin; compute, never assume, and enumerate observed role ids from the logs rather than
      guessing names.
    - Safe-style multisig: `getThreshold()`, `getOwners()` (dynamic array: offset word, length word,
      then one address word per owner - decode from the preserved raw return), modules via
      `getModulesPaginated(address,uint256)` (a module executes WITHOUT threshold signatures and is an
      authority in its own right), guard if present. Threshold 1-of-N is functionally a single key.
    - Timelock: `getMinDelay()`, proposer/executor/canceller members via the same role replay, open
      executor (executor role granted to the zero address means anyone can execute a ripe operation),
      pending operations (`CallScheduled` without matching `CallExecuted`/`Cancelled`); the timelock's
      own admin role can shorten the delay.
    - For every holder: runtime status (`contract`/`eoa`), and scope rows with roles `owner`,
      `role_holder`, `multisig`, `timelock`, `proxy_admin`, `beacon`, `implementation`.
    Run the reads against each admin contract, always at the frozen pin:
    ```
    python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<admin contract> --chain-id N --block <P1 block number> \
        --call "owner()" --call "getThreshold()" --cache rpc-cache.json --out packet-A-admin-<n>.json
    ```
    Always pass `--block <P1 block number>` taken from the frozen packet's `pin.block_number`: a
    secondary packet produced without it pins to `latest`, names that pin `P1` too, and its probes may
    NOT be cited as P1. Before copying any probe into an evidence row assert
    `packet.pin.block_hash == frozen.pin.block_hash`; on mismatch discard the packet and re-run.
11. Test the "renounced" / "no owner" claim as a proposition, never as a conclusion. `owner()` equal to
    the zero address covers ONLY the Ownable owner. Check, and list in the finding, each of:
    remaining AccessControl roles; the implementation admin / beacon owner; external controller
    contracts (a `feeConfig()`/`controller()`/`taxWallet()` returning a contract whose owner sets
    what the token reads); hardcoded privileged addresses (PUSH20 candidates - take `push20_candidates` from
    `python3 <skill-root>/scripts/selector_scan.py --code-file <runtime> --json`, resolve each with
    `eth_getCode` and against the scope list; a hardcoded EOA that gates a branch is an owner without an
    `owner()`); and lockers or
    vaults holding LP or supply with their own admins (surface B, surface G).
12. Write the rows. Each authority becomes a finding with `proposition` naming the function, the
    holder address, the holder's type and the pin; each read becomes an evidence row with the exact
    `eth_call`/`eth_getLogs`/`eth_getStorageAt` query and decoding basis. Set the rating from the worst
    live authority; an upgrade path held by a single key is `critical` regardless of other checks.

Conventions used above are computed, not trusted (`ddcore.selector`, `ddcore.keccak256_hex`); slot
constants come from `ddcore` (`EIP1967_IMPLEMENTATION_SLOT`, `EIP1967_ADMIN_SLOT`, `EIP1967_BEACON_SLOT`,
`EIP1822_LOGIC_SLOT`). No chain-specific number is needed on this surface; if a platform's known
contract (a shared ProxyAdmin, a canonical bridge minter) enters the picture, record its address as
"verify at use time" and verify it from a receipt or a `factory()`/`admin()` read at P1.

## Checks

| check_id | surface | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|---|
| A-MINT | token_controls | Every path that can increase `totalSupply` or any balance without a transfer is inventoried, with the holder of each | selectors of every runtime + `eth_call` of each guard/role at P1 | `bytecode`, `rpc_state`, `log_decoded` (RoleGranted replay) | new role grant, upgrade, minter set change, rebase call after P1 |
| A-UPGRADE | token_controls | Who can change the code behind the token and each admin contract | EIP-1967/1822 slots at P1 + admin/beacon/diamond owner reads | `rpc_storage`, `rpc_state`, `bytecode` | `Upgraded`/`BeaconUpgraded`/`AdminChanged`/`DiamondCut` event, ownership transfer |
| A-SEIZE | token_controls | Who can move or rewrite a third party's balance | selectors + guard reads; trace of a historical privileged move when source is unverified | `bytecode`, `rpc_state`, `trace` | upgrade, role change |
| A-RESTRICT | token_controls | Pause, blacklist/whitelist, cooldown, limits, trading gate: current value and setter holder | each flag/value read at P1 + setter authority | `rpc_state`, `rpc_storage`, `bytecode` | any setter call after P1 (`Paused`, gate events) |
| A-TAX | token_controls | Tax rates, ceilings, exemptions, recipients and their setters | rates and exemption reads at P1 + ceiling from verified source or bytecode constant | `rpc_state`, `source_verified`, `bytecode` | setter call after P1 |
| A-EXTCALL | token_controls | External, delegate and arbitrary calls reachable from token or admin, and their targets | opcode flags + target reads (router, tracker, hook) at P1 | `bytecode`, `rpc_state`, `trace` | target setter call, upgrade |
| A-ADMIN | token_controls | Current holders of every authority, their type (EOA/multisig/timelock/contract), and who can change them | owner/pendingOwner/role members confirmed with `hasRole`, threshold, owners, modules, min delay at P1 | `rpc_state`, `log_decoded` | `OwnershipTransferred`, `RoleGranted/Revoked`, `ChangedThreshold`, `AddedOwner`, `EnabledModule`, `MinDelayChange` |
| A-SOURCE-MATCH | token_controls | Published source corresponds to the deployed runtime(s) | code hash at P1 vs compiled runtime hash, differences listed | `bytecode`, `source_verified` | upgrade (new implementation hash) |
| A-ROLE-REPLAY | token_controls | The RoleGranted/RoleRevoked replay covers deployment..P1 without gaps and agrees with `hasRole` | log range, pagination, per-role member list, `hasRole` confirmations | `log_decoded`, `rpc_state` | any role event after P1 |
| A-PUSH20 | token_controls | Hardcoded addresses in the runtime are resolved and classified | PUSH20 candidate list with `eth_getCode` status and scope match | `bytecode`, `rpc_state` | upgrade |
| A-CONTROLLER | token_controls | External contracts the token reads for fees/limits/recipients, and their admins | `eth_call` of each pointer at P1 + the pointed contract's owner/proxy status | `rpc_state`, `rpc_storage` | pointer setter call, controller upgrade |
| A-RENOUNCED | token_controls | The "renounced/no owner" claim, tested against roles, implementation admin, controllers, PUSH20, lockers | `owner()` at P1 plus every item of step 11 | `rpc_state`, `bytecode` | any of the above stale conditions |

Statuses follow the manifest rules: `pass` and `finding` need evidence ids; `unknown`/`skipped` need a
reason (cite the limitation id when an RPC failure caused it). `unknown` is never a pass.

## Common false positives and negatives

False positives (an authority that is not one):
- A `mint` selector that is only reachable from `constructor`-time logic already spent, or gated by a
  role that has no members at P1 AND whose admin role has no members (state both facts; still record
  the path, because a beacon or proxy admin can revive it).
- `burn(uint256)` on the caller's own balance is not seizure; `burnFrom` that consumes allowance is not
  seizure.
- `paused() == false` reported as "no pause": the authority exists while the setter is held.
- Two contracts sharing a code hash reported as one authority: storage differs; resolve each.
- A revert on `owner()` is a fact about the contract at P1 (no Ownable interface), not a limitation
  and not "renounced".

False negatives (an authority missed):
- Scanning only the proxy's bytecode: the implementation carries the logic; scan both.
- OpenZeppelin v5 `_update` overrides, fallback-dispatched custom proxies and diamonds: no selector
  names to match. Corroborate with behavior at P1: an `eth_call` of `transfer` with the `from` field set
  to a real holder (read-only; no key) reveals gates, taxes and blacklists in the revert data or in the
  return/balance effect.
- Mint authority held by a bridge, reward distributor or airdrop contract: the token's own owner list
  is clean while a third contract with an EOA admin can mint.
- The tax-wallet or fee setter living in a separate controller contract; PUSH20-hardcoded admins;
  a Safe module; an open executor on a timelock; a pending owner.
- Role membership taken from an explorer "read contract" page instead of replay + `hasRole`.
- Treating verified source as the deployed code without the code-hash comparison (step 3).

## Escalation triggers to deep tracks

- Proxy of any kind, custom dispatch, DELEGATECALL with a storage-loaded target, unverified or
  non-corresponding source, risky selectors without source: `references/deep-tracks/proxy-bytecode.md`
  (escalate gradually: runtime and selectors -> verified predecessors and compiler metadata -> storage
  and historical calls -> reconstruction or simulation; decompiler output is never verified source).
- `totalSupply()` or balances drift from the Transfer-event sum (rebasing, rewrites, shares):
  `references/deep-tracks/transfer-replay.md`, and `D-NONTRANSFER-CHANGES` in
  `references/surfaces/D-supply-concentration.md`.
- A minter or fee route is a reward distributor with epochs or backlog:
  `references/deep-tracks/reward-epoch.md`.
- An authority holder must be described as an operator or controller (never an identity):
  `references/deep-tracks/operational-attribution.md`, with language from `references/attribution.md`.
- A privileged function must be shown reachable or exercised at a past state:
  `trace` via `debug_traceTransaction`/`trace_transaction` on the historical call (read-only); do not
  simulate outside a fork verified by `python3 <skill-root>/scripts/fork_guard.py --rpc http://127.0.0.1:8545 --expect-chain-id N --out attestation.json`.

## How to state results

Bind each statement to the pin and to the holder; separate current code from replaceable code.

- "At P1 (block N, hash 0x..), `mint(address,uint256)` is callable by the holder of `MINTER_ROLE`;
  the sole confirmed member is 0x.. (`hasRole` at P1), a contract of type EIP-1967 proxy whose admin is
  the EOA 0x... No cap setter was found in either runtime. Confidence: proven for the role member;
  inference for 'no cap', because source correspondence was not established."
- "The token runtime at P1 contains no supply-increase selector and no DELEGATECALL; the code hash is
  0x... This binds only the code at P1: the EIP-1967 admin slot is non-zero and the ProxyAdmin owner is
  a 1-of-3 multisig, so the code can be replaced by one signer. Rating `critical` on A-UPGRADE; A-MINT
  `pass` for current code only."
- "`owner()` returns the zero address at P1. The claim 'renounced' is not established for the system:
  `DEFAULT_ADMIN_ROLE` has one confirmed member (0x..), and the fee recipient is read from controller
  0x.. whose `owner()` is the EOA 0x..."
- "No current executable seizure path found at the pinned block in the token or implementation
  runtime (selector scan and role reads, evidence E4, E7). This does not extend to code introduced by a
  future upgrade."
- "Unknown because historical state was unavailable: the RoleGranted replay could not cover blocks
  X..Y (limitation L2, `rpc_pruned`); `hasRole` was confirmed only for members observed in the covered
  range. A-ROLE-REPLAY is `unknown`; the `token_controls` rating cannot be `low`."
- Never: "ownership renounced, so the token is safe"; "no mint function, rug-proof"; "the team cannot
  change the tax" when the ceiling was not read.
