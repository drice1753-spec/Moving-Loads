# Surface B - Liquidity custody

Rating keys: `canonical_lp_principal_custody` (checks `B-CANON`, `B-PRINCIPAL`) and
`side_pool_removal_risk` (check `B-SIDE`). Read this file for "is liquidity locked", LP owner, locker,
position NFT, hook, or side-pool questions; always in `broad` mode. Platform mechanics beyond what is
here: `references/platforms/uniswap-v3.md`, `references/platforms/uniswap-v4.md`,
`references/platforms/pons-style-launches.md`.

## Purpose

Identify every material pool for the exact token by address or complete pool key, then resolve, per
position, who can remove the principal today and by which executable path. Separate the canonical
pool(s) from side pools, because a locked canonical position says nothing about liquidity elsewhere,
about price support, or about the position staying in range.

## What can change the verdict

- A removal path for the canonical principal that a single key can execute at P1: LP tokens held by an
  EOA or the deployer, a position NFT owned or approved to an EOA, a locker whose admin can withdraw,
  rescue, extend-in-reverse, transfer the lock, make arbitrary calls, or upgrade.
- A locker, vault or hook that is a proxy or has an owner (the custody authority is that owner).
- Side pools holding meaningful depth with removable liquidity, or a "canonical" label attached to a
  pool that is not where trading actually settles.
- A v3/v4 position that is out of range at P1 (no exit depth in the sell direction) or whose tick range
  is so narrow that a small price move empties it.
- Unlock time already past, or close to P1, or extendable only by the same key that can withdraw.
- Discovery coverage: a factory, fee tier, DEX or block range not searched cannot support "no side
  pool"; state the universe and its limits.
- A v4 hook whose permission bits let it block or reroute liquidity actions, or whose owner can.

## Procedure

Every read cites P1. Pools are scope addresses with role `pool`; managers, NFTs, lockers and hooks get
roles `position_manager`, `position_nft`, `locker`, `hook`; discovery claims get `discovery` rows (`S1..`).

1. Declare the discovery universe BEFORE searching, as a `discovery` row: which factories and DEX
   versions (v2-style, v3-style, v4 PoolManager) on this chain, which fee tiers, which block range
   (deployment block .. P1), the `eth_getLogs` window size and pagination used, and what was excluded.
   Endpoint log-range caps and timeouts are `coverage.limitations`, never findings.
2. Discover pools from logs, not from lists:
   - v2-style: `PairCreated(address,address,address,uint256)` on each factory with the token as
     topic1 or topic2 (two queries per factory);
   - v3-style: `PoolCreated(address,address,uint24,int24,address)` on each factory with the token as
     topic1 or topic2, across ALL fee tiers (topic3 is the fee);
   - v4: `Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)` on the PoolManager
     with the token as currency0 or currency1 (verify the exact event signature against the deployed
     PoolManager's ABI or a known `Initialize` receipt before hashing it);
   - router usage: from the token's own `Transfer` logs, collect counterparties that also emit `Swap`
     (v2/v3) or appear as `Swap` pool ids (v4); a pool created by a factory you did not search shows up
     here;
   - aggregator or explorer listings: `explorer`/`dashboard` evidence, discovery only; every listed pool
     is then confirmed on-chain (`token0()`/`token1()`/`fee()` or pool key from `Initialize`).
   Cross-check each v2/v3 address against the deterministic computation; the tool prints the init code
   hash it used and warns that it must be verified against a `PoolCreated`/`PairCreated` event on this
   chain:
   ```
   python3 <skill-root>/scripts/pool_math.py v2-pair --factory 0x<factory> --token-a 0x<token> --token-b 0x<quote> [--init-code-hash 0x<from a verified PairCreated>]
   python3 <skill-root>/scripts/pool_math.py v3-pool --factory 0x<factory> --token-a 0x<token> --token-b 0x<quote> --fee 3000 [--init-code-hash 0x<verified>]
   ```
   For v4 record currency ordering (currency0 is the numerically lower address; native currency is the
   zero address), fee, tick spacing, hook address, and the derived PoolId:
   ```
   python3 <skill-root>/scripts/pool_math.py v4-pool-id --currency0 0x.. --currency1 0x.. --fee 3000 --tick-spacing 60 --hooks 0x..
   ```
   The PoolId must equal the `Initialize` topic1; a mismatch means a wrong key component.
3. Classify canonical vs side. Canonical = the pool(s) where the project's own launch or platform
   placed the principal and where the bulk of historical `Swap` volume settles (both stated with
   evidence); side = every other pool holding the token. A website's "official pool" is a claim
   (`website` evidence) to test, not the classification. `B-CANON` is `pass` only when each canonical
   pool is identified by exact address (v2/v3) or complete pool key + PoolId (v4) with reads at P1.
4. v2 custody, per canonical and material side pair:
   - the LP token IS the pair; replay the pair's own `Transfer`/`Mint` logs to enumerate LP holders,
     then confirm `balanceOf(holder)` at P1 for each material holder and `totalSupply()` of the pair;
   - classify each holder: burn address (zero address, `0x..dEaD`; check `eth_getCode` empty and treat
     the burn as conventional, not provable), locker contract, EOA, deployer, launch platform, vault;
   - removal path: `burn(address)` executes when LP tokens reach the pair, so whoever can transfer LP
     tokens can remove principal; `removeLiquidity*` on a router is just that path with a wrapper.
5. v3 custody, per canonical and material side pool:
   - position manager: find it from `IncreaseLiquidity(uint256,uint128,uint256,uint256)` and NFT
     `Transfer` logs whose `positions(tokenId)` decode to this pool's token0/token1/fee; positions can
     also be minted DIRECTLY on the pool by a contract (`Mint(address,address,int24,int24,uint128,uint256,uint256)`
     with a non-manager `owner`) - enumerate those from pool logs too;
   - `positions(tokenId)` decode: nonce, operator, token0, token1, fee, tickLower, tickUpper, liquidity,
     feeGrowthInside0LastX128, feeGrowthInside1LastX128, tokensOwed0, tokensOwed1 (twelve static words;
     record the raw return as the artifact);
   - `ownerOf(tokenId)`, `getApproved(tokenId)`, `isApprovedForAll(owner, candidateOperator)` for every
     operator seen in `ApprovalForAll` logs; an approved address or operator can call
     `decreaseLiquidity`, `collect`, `burn` and `safeTransferFrom` without being the owner;
   - if the owner is a locker or vault: its authority (step 7);
   - range: read `slot0()` on the pool for the current tick; compare with tickLower/tickUpper; convert
     with `python3 <skill-root>/scripts/pool_math.py v3-tick-to-price --tick N --decimals0 D0 --decimals1 D1`.
6. v4 custody, per pool key:
   - PositionManager positions are ERC-721 token ids; read the position info and liquidity for each id
     found from the manager's `Transfer`/`ModifyLiquidity`-related logs; the manager's own operator and
     approval semantics apply as in v3;
   - hook authority: the low-order bits of the hook address encode which hook callbacks are enabled
     (the exact bit layout is version-specific: verify against the deployed Hooks library or the v4-core
     version the PoolManager was built from, and record the mapping used). A hook with liquidity-related
     permissions can block or reroute `modifyLiquidity`; the hook contract's owner and upgradeability are
     custody authorities - resolve them with the surface A procedure;
   - PoolManager is a singleton: NEVER treat its token balance as one pool's reserves. Read pool-scoped
     state (`getSlot0(poolId)`, `getLiquidity(poolId)`, position info) through the StateView contract
     or `extsload` at the documented slots, and record which method was used;
   - native-currency pools have no wrapped-quote `Transfer` legs; use PoolManager events and traces.
7. Locker or vault authority (`B-LOCKER-AUTH`), for any contract holding LP tokens or a position NFT:
   - the lock record: id, owner/beneficiary, unlock time (compare with P1 `timestamp_unix`), amount or
     token id, fee-collection rights;
   - admin surface: `owner()`/roles (surface A procedure), and selectors for withdraw, unlock,
     `extendLock` (only extension is benign; check for a shorten path), `transferLock`/lock ownership
     transfer, `rescue`/`recover`/`emergencyWithdraw`, arbitrary call (`execute`, `call`, `multicall`),
     `setMigrator`/`migrate`, upgrade (proxy slots), and `selfdestruct` presence:
     ```
     python3 <skill-root>/scripts/selector_scan.py --code-file evidence/E9-locker-code.hex --json
     python3 <skill-root>/scripts/rpc_probe.py --address 0x<locker> --chain-id N --block <P1 block> --call "owner()" --call "getLock(uint256):<id>" --out packet-B-locker.json
     ```
   - the locker's own proxy status (an upgradeable locker is custody by its admin);
   - who can call each path today; a locker with an EOA admin and a `rescue` path is not custody.
8. Removal-path verdict per position (`B-PRINCIPAL`): state the executable path (holder -> function ->
   effect), the key(s) required, and the earliest time it can execute. "No current executable removal
   path found at the pinned block" requires: every LP token/NFT holder resolved, every approval and
   operator read, the locker admin surface read, and no unlock before P1. Record what was NOT read.
9. Depth and range (`B-RANGE`): for each canonical position, in-range or out-of-range at P1 and the
   direction; liquidity out of range provides no exit depth in that direction; per-position tick range
   matters more than the sum of liquidity. Hand pool addresses, keys and range facts to surface C.
10. Side pools (`B-SIDE`): inventory every discovered non-canonical pool with its reserves or liquidity
    at P1, LP/position holders and their removal paths (steps 4-8, abbreviated where depth is immaterial
    under the stated materiality rule), and rate removal risk separately. A side pool whose liquidity can
    be pulled in one transaction is the removal risk even when the canonical position is locked.

### Verify-at-use table (never assert from memory)

| Item | Why it matters | How to verify at use time |
|---|---|---|
| Factory addresses (v2-style, v3-style forks) | discovery universe, CREATE2 derivation | read `factory()` on a pool found via `Transfer`/`Swap` logs at P1; confirm a `PoolCreated`/`PairCreated` receipt emitted by that factory |
| Init code hashes | CREATE2 pool address computation | derive an address with `pool_math.py`, compare with a real `PoolCreated`/`PairCreated` event on this chain; on mismatch the hash is wrong for this deployment |
| Position manager address | position enumeration, approvals | `positionManager()`/`factory()` reads, an `IncreaseLiquidity` receipt whose emitter holds the pool's `Mint` owner slot |
| v4 PoolManager, PositionManager, StateView | pool-scoped state, PoolId | emitter of an `Initialize` receipt; `poolManager()` on the PositionManager and StateView; chain id from `eth_chainId` |
| Hook permission bit layout | hook authority | v4-core `Hooks` library version matching the PoolManager build; record the mapping used |
| Locker "official" addresses | custody authority | never by label; the holder of the LP/NFT at P1 is whatever `balanceOf`/`ownerOf` returns |

## Checks

| check_id | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|
| B-CANON | Each canonical pool is identified by exact address (v2/v3) or complete pool key + derived PoolId (v4), with the classification basis | creation log + `token0/token1/fee` or `Initialize` decode + PoolId match + volume basis | `log_decoded`, `rpc_state` | new canonical pool created; platform migration |
| B-PRINCIPAL | The executable LP-principal removal path(s) for the canonical position(s) and the key(s) that can run them at P1 | LP holders / NFT owner + approvals + operators + locker admin surface + unlock time | `rpc_state`, `bytecode`, `log_decoded` | LP/NFT `Transfer`, `Approval`, `ApprovalForAll`, lock transfer/extension, unlock time reached, locker upgrade |
| B-SIDE | Every side pool in the declared universe is inventoried with depth and removal risk | discovery row + per-pool reserves/liquidity at P1 + holder resolution | `log_decoded`, `rpc_state` | new pool created; liquidity added/removed |
| B-LOCKER-AUTH | The locker/vault holding LP or a position NFT has no admin path (withdraw, rescue, transfer, arbitrary call, upgrade, shorten) executable before unlock | selector scan + owner/roles + lock record + proxy slots | `bytecode`, `rpc_state`, `rpc_storage` | locker ownership change, upgrade, lock record change |
| B-OPERATOR-APPROVALS | No address other than the resolved custodian is approved or an operator for the canonical position | `getApproved` + `isApprovedForAll` for each observed operator at P1 | `rpc_state`, `log_decoded` | `Approval`/`ApprovalForAll` after P1 |
| B-HOOK-AUTH | (v4) The hook's permissions and its owner/upgradeability cannot block or reroute liquidity actions on the canonical pool | permission bits decoded + hook owner/proxy reads | `bytecode`, `rpc_state`, `rpc_storage` | hook upgrade, hook ownership change |
| B-RANGE | Canonical position(s) are in range at P1 and the tick range is stated per position | `slot0`/`getSlot0` + `positions` decode | `rpc_state` | any price move across a tick boundary |
| B-DIRECT-MINT | Liquidity minted directly on the pool (not via the NFT manager) is enumerated with its owner | pool `Mint` logs with non-manager owners + `positions(key)` on the pool | `log_decoded`, `rpc_state` | new `Mint`/`Burn` on the pool |
| B-DISCOVERY-COVERAGE | The declared universe was actually searched end-to-end | discovery row with ranges, pagination, and the limitation ids for gaps | `log_decoded`, `manual_note` | new factories/DEXes deployed; block range advances |

## Common false positives and negatives

False positives:
- "Liquidity locked" because LP tokens sit in a contract: the contract may be a vault with an owner
  `withdraw`, a proxy, or a locker with `rescue`; read its admin surface (step 7).
- A position NFT owned by a locker but with `getApproved` set to another address, or the locker itself
  approved-for-all to an operator: the operator can decrease liquidity.
- Treating LP sent to `0x..dEaD` as proven unrecoverable: it is conventional (no known key), not
  provable; say so.
- Counting a PoolManager token balance as pool reserves (v4), or summing liquidity across positions
  with different ranges as one depth figure.
- Reporting fee `collect` rights as principal removal: collecting owed fees reduces value but does not
  remove principal; keep the two propositions separate.
- Calling a pool canonical because a website or aggregator says so.

False negatives:
- Searching one factory or one fee tier; skipping v2-style forks; not scanning `Swap` counterparties.
- Missing positions minted directly on the pool by a launch contract (no NFT exists).
- Missing a second locker record (multiple lock ids), or a lock whose unlock time is already past.
- Not reading the locker's proxy slots; not checking the hook contract (v4) as an authority.
- A block-range gap in the log search reported as "no side pools" instead of `unknown` with a
  limitation id.

## Escalation triggers to deep tracks

- The current owner chain of a position is unclear (NFT moved through several contracts, LP tokens
  split across lockers, migrations), liquidity was added or removed after launch, or a history question
  is asked ("was it ever unlocked?"): `references/deep-tracks/pool-position-history.md`.
- Locker, vault or hook is a proxy, has DELEGATECALL, or has unverified/non-corresponding source:
  `references/deep-tracks/proxy-bytecode.md`.
- Position was created by a launch platform or bonding curve with its own custody rules:
  `references/platforms/pons-style-launches.md`, then `references/deep-tracks/launch-cohort.md` if the
  launch cohort matters.
- Removal happened historically and proceeds must be followed:
  `references/deep-tracks/proceeds-reconciliation.md`.
- Attributing a locker admin or operator as a role (never an identity):
  `references/deep-tracks/operational-attribution.md`.

## How to state results

- "Canonical pool: v3 pool 0x.. (token0 0x.., token1 0x.., fee 3000), identified from the factory
  `PoolCreated` log at block N (E5) and 9x% of historical `Swap` volume in the searched range (S1).
  Position NFT #123 on position manager 0x.. holds liquidity L across ticks [a, b], in range at P1
  (current tick t). Owner at P1: locker 0x..; `getApproved` zero; no operators (E8-E11)."
- "No current executable removal path found at the pinned block for position #123: the locker's
  unlock time is T (after P1 timestamp), its runtime has no rescue/arbitrary-call/upgrade selector and
  no DELEGATECALL (E12), and `owner()` of the locker is a 3-of-5 multisig (E13). Not established:
  liquidity in side pools, price support, or that the position remains in range."
- "Side pool: v2 pair 0x.. holds reserves (r0, r1) at P1; LP `totalSupply` is held 100% by the EOA
  0x.. (E20). A single transaction by that key removes this liquidity. `side_pool_removal_risk`:
  high; `canonical_lp_principal_custody` is rated separately."
- "v4 pool key {currency0, currency1, fee, tickSpacing, hooks} -> PoolId 0x.. matches the `Initialize`
  topic (E30). The hook at 0x.. has liquidity-related permission bits set (mapping from v4-core Hooks
  library, recorded in E31) and an EOA owner (E32); the hook owner is treated as a custody authority."
- "Unknown because historical state was unavailable: `PairCreated` logs for blocks X..Y could not be
  read (L3, `rpc_pruned`); B-SIDE is `unknown` for that range and the `side_pool_removal_risk` rating is
  `unknown`, not `low`."
- Never: "LP is locked so the token cannot be rugged"; "liquidity is deep" from a single position's
  total liquidity without range; "no side pools" without a declared universe.
