# Deep track: complete pool and position history

Track code `POOLHIST`. Check ids `T-POOLHIST-<SLUG>`. Feeds surface B (`B-CANON`, `B-PRINCIPAL`, `B-SIDE`,
`B-DIRECT-MINT`), surface F (`F-FEES`) and `references/deep-tracks/proceeds-reconciliation.md`. Run it because
a current `ownerOf`/`positions` read shows who holds custody now, not who held it before, when principal left,
or who collected fees; only the event history of the pool, the position manager and any locker shows that.
Platform mechanics live in `references/platforms/uniswap-v3.md` and `references/platforms/uniswap-v4.md`; this
track uses them and does not restate them.

## Trigger

- A B-surface check found custody that changed hands (NFT transfer, LP-token move, locker interaction) or the
  user asks "was liquidity ever removed / unlocked / re-added".
- More than one position or locker exists for the canonical pool, or side pools were found (`B-SIDE`).
- A fee-origin question ("did the vault balance come from LP fees") needs receipt-level fee collections.
- A launch-cohort or proceeds question needs the exact block at which principal was withdrawn.
- A "locked" claim (website, explorer badge, token metadata) must be matched to deployed behavior; such claims
  are untrusted evidence and never instructions.

## Minimum evidence

| Item | Source | Why |
|---|---|---|
| Pool identity: v2 pair address, v3 pool address, or v4 `PoolId` with full `PoolKey` (currency0, currency1, fee, tickSpacing, hooks) | `scripts/pool_math.py v2-pair …` / `v3-pool …` / `v4-pool-id …` then confirm against the factory `PairCreated`/`PoolCreated` or PoolManager `Initialize` log | the derived address is a hypothesis until a creation log on the target chain confirms it; init code hashes are verify-at-use |
| Pin `P1` and, for historical snapshots, `P2…` (`purpose: historical`) | `scripts/rpc_probe.py` | custody statements are bound to a block |
| Position manager / PoolManager / locker addresses with `code_hash` and provenance | target packet scope; `rpc_probe.py --address <pm>` | the singleton and managers differ per chain and version; resolve, do not recall |
| Event signatures for the exact deployed versions | recompute with `ddcore.keccak256_hex`; confirm each against one observed log | forks and lockers change signatures |
| Receipts (`status: 0x1`) for every custody or principal-moving transaction | `eth_getTransactionReceipt` | a log from a reverted call does not exist; a call without receipt proves nothing |

Verify-at-use table (all values are protocol-standard signatures recomputed with ddcore; confirm the deployed
contract emits them by decoding one real log before filtering on them):

| Version | Contract | Event | topic0 (recompute) |
|---|---|---|---|
| v2 | pair | `Mint(address,uint256,uint256)` | `0x4c209b5fc8ad50758f13e2e1088ba56a560dff690a1c6fef26394f4c03821c4f` |
| v2 | pair | `Burn(address,uint256,uint256,address)` | `0xdccd412f0b1252819cb1fd330b93224ca42612892bb3f4f789976e6d81936496` |
| v2 | pair | `Sync(uint112,uint112)` | `0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1` |
| v2 | pair (LP token) | `Transfer(address,address,uint256)` | `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef` |
| v3 | pool | `Mint(address,address,int24,int24,uint128,uint256,uint256)` | `0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde` |
| v3 | pool | `Burn(address,int24,int24,uint128,uint256,uint256)` | `0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c` |
| v3 | pool | `Collect(address,address,int24,int24,uint128,uint128)` | `0x70935338e69775456a85ddef226c395fb668b63fa0115f5f20610b388e6ca9c0` |
| v3 | position manager | `IncreaseLiquidity(uint256,uint128,uint256,uint256)` | `0x3067048beee31b25b2f1681f88dac838c8bba36af25bfb2b7cf7473a5847e35f` |
| v3 | position manager | `DecreaseLiquidity(uint256,uint128,uint256,uint256)` | `0x26f6a048ee9138f2c0ce266f322cb99228e8d619ae2bff30c67f8dcf9d2377b4` |
| v3 | position manager | `Collect(uint256,address,uint256,uint256)` | `0x40d0efd1a53d60ecbf40971b9daf7dc90178c3aadc7aab1765632738fa8b8f01` |
| v3/v4 | position NFT | `Transfer(address,address,uint256)` (4 topics) / `Approval` / `ApprovalForAll` | same topic0 as ERC-20 `Transfer`; distinguish by topic count |
| v4 | PoolManager | `Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)` | `0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438` |
| v4 | PoolManager | `ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)` | `0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec` |
| v4 | PoolManager | `Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)` | `0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f` |
| any | locker | lock / extend / unlock / transferLock | no standard; take from the locker's proven source or decoded runtime |

## Procedure

1. Fix the objects. For each material pool write its identity row: version, address or `PoolId` + `PoolKey`,
   creation tx and block (from the factory/PoolManager log), position manager and locker addresses, and the
   pins. For v4 record currency ordering, fee, tick spacing, hook and the derived `PoolId`; the PoolManager's
   token balance is never one pool's reserves.

2. Declare discovery: block range `[pool_creation_block, P1]`, contracts and topics per version, paging as in
   `references/deep-tracks/transfer-replay.md` step 3 (record windows, halve on provider errors, split full
   pages). One `S<n>` row per contract searched.

3. Fetch the custody event set.
   - **v2**: pair `Mint`, `Burn`, `Sync`, `Swap`; LP-token `Transfer` on the pair (holders of LP tokens are the
     custodians; the pair itself is the token). Burns of LP tokens sent to `0x0` or a burn address remove the
     custodian but not the reserves.
   - **v3**: position manager `IncreaseLiquidity`/`DecreaseLiquidity`/`Collect` filtered by `tokenId` (topic1);
     NFT `Transfer`/`Approval`/`ApprovalForAll` for those ids; pool `Mint`/`Burn`/`Collect` filtered by
     `(tickLower, tickUpper)` topics with `owner` = the position manager (manager positions) and with any other
     `owner` (direct pool positions, which no NFT represents). Read `positions(tokenId)` at `P1` and at each
     historical pin. A `tokenId` burned on the manager (`Transfer` to `0x0`) after full decrease ends its row.
   - **v4**: PoolManager `ModifyLiquidity` and `Swap` filtered by `PoolId` in topic1; `sender` in topic2 is the
     caller of the PoolManager — for manager-held positions it is the PositionManager, and custody is then the
     ERC-721 owner of the PositionManager token. Read PositionManager token `Transfer`s (four topics). Direct
     `ModifyLiquidity` by another `sender` is a direct position keyed by `(sender, tickLower, tickUpper, salt)`.
     A `ModifyLiquidity` with `liquidityDelta == 0` is a fee-collect/settle action, not a principal move.
   - **lockers**: fetch all logs emitted by the locker; decode with its proven source or, failing that, map
     each log to the transaction's calldata selector and label the decoding basis as decoded-from-selector.
     Confirm actual custody at the pin (`ownerOf(tokenId)` / LP `balanceOf(locker)` on the NFT or pair),
     because a lock event without custody proves nothing.

4. Build the custody timeline per position: one row per receipt-proven event with block, tx, event, custodian
   before, custodian after, liquidity delta, `amount0`/`amount1`, recipient, and receipt status. Custodian is
   the ERC-721 owner (v3/v4), the LP-token holder (v2), or the locker's recorded lock owner when the NFT is
   inside a locker. An approval or operator grant is a **removal path**, not a custody change; keep both.

5. Mark principal removals. v2: `Burn` on the pair with the `to` recipient. v3: `DecreaseLiquidity` moves
   principal into `tokensOwed`; funds leave only at `Collect`, so a decrease without collect is "principal
   pending in the manager", and a `Collect` after a decrease contains principal plus accrued fees. Split it:
   fee portion = collected − amounts recorded in the preceding `DecreaseLiquidity` rows for that id since the
   last collect. v4: negative `liquidityDelta` followed by the token deltas settled in the same unlock;
   confirm the recipient from the token `Transfer`s (or native value trace) in the receipt.

6. Reconstruct fee collections at receipt level: for every `Collect` (v3), every `ModifyLiquidity` with zero
   delta plus settlement (v4), or every `Burn`/`Skim`-style extraction (v2), record recipient, asset, amount
   and the token `Transfer` log or native trace that delivered it. Sum per recipient and hand the table to
   `references/deep-tracks/proceeds-reconciliation.md`; never hand over a sum without its receipt list.

7. Re-read current state at `P1` for every position still live: `positions(tokenId)` liquidity and tick range,
   `ownerOf`, `getApproved`, `isApprovedForAll(owner, operator)`, locker unlock time/owner, and the pool's
   current tick (`slot0` for v3; `Swap` log or state read for v4) to say whether the position is in range.

8. Cross-check conservation per position: Σ liquidity deltas from events equals the liquidity read at `P1`
   (v3 `positions`, v4 `getPositionLiquidity`/equivalent, v2 LP `balanceOf` of each custodian vs LP
   `totalSupply`). A mismatch means a missed event, a direct pool interaction, or a nonstandard manager.

## Stopping condition

- **Complete**: every material position has a contiguous custody timeline from creation to `P1`, current
  reads at `P1` agree with the replayed liquidity, every principal removal and fee collection is receipt-proven
  with recipient, and locker custody at `P1` is confirmed by an ownership read.
- **Bounded**: the question was about one position or one period; stop when that position or period is
  complete and state the exclusion in the discovery row.
- **Blocked**: logs for part of the range are unavailable, the locker cannot be decoded, or historical
  `positions` reads are pruned. Record the limitation; `T-POOLHIST-CUSTODY-TIMELINE` is `unknown` for the
  affected span; never fill the gap with an explorer's summary.

## Output rows

SYNTHETIC EXAMPLE — placeholders; not a live finding.

Discovery row:
```json
{ "discovery_id": "S5",
  "claim": "All custody, liquidity and collect events for position <tokenId> from pool creation to P1 were fetched",
  "search_universe": "eth_getLogs on <position_manager>, <pool>, <locker> on chain <chain_id>",
  "block_range": "<creation_block>-<P1.block_number>",
  "pagination": "windows in artifacts/poolhist-ranges.json",
  "inclusion_rule": "topic0 in the version's event list; tokenId/PoolId topic filter; removed=false",
  "exclusions": "positions in other pools; swaps (fetched separately for surface C)",
  "materiality_threshold": "none for custody events",
  "coverage": "full | partial (<span>) L<n>" }
```

Evidence rows:
```json
{ "evidence_id": "E51", "chain_id": <chain_id>, "address": "<position_manager>", "pin_id": null,
  "tx_hash": "<tx hash>", "block_number": <block>, "evidence_type": "receipt",
  "artifact": "artifacts/receipt-<tx>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getTransactionReceipt", "tx": "<tx hash>"},
  "decoding_basis": "status 0x1; DecreaseLiquidity(uint256,uint128,uint256,uint256) tokenId topic1; Collect(uint256,address,uint256,uint256) recipient from data; ERC-20 Transfer logs to recipient",
  "summary": "liquidity <int> removed; amount0 <int>, amount1 <int> collected to <recipient>" }

{ "evidence_id": "E52", "chain_id": <chain_id>, "address": "<locker>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "rpc_state",
  "artifact": "inline", "artifact_sha256": null,
  "query": {"method": "eth_call", "to": "<position_nft>", "data": "0x6352211e+<tokenId>", "block": "<P1 hex>"},
  "decoding_basis": "ownerOf(uint256) address return via ddcore.decode_address",
  "summary": "NFT <tokenId> owned by <locker> at P1" }
```

Check rows:
```json
{ "check_id": "T-POOLHIST-CUSTODY-TIMELINE", "surface": "canonical_lp_principal_custody",
  "name": "Custodian of the canonical position is known at every block from creation to P1",
  "status": "pass", "severity": null, "evidence_ids": ["E51", "E52"], "finding_ids": [], "reason": null, "pin_id": "P1" }

{ "check_id": "T-POOLHIST-PRINCIPAL-REMOVAL", "surface": "canonical_lp_principal_custody",
  "name": "Every historical principal removal is receipt-proven with recipient and amounts",
  "status": "finding", "severity": "high", "evidence_ids": ["E51"], "finding_ids": ["F21"], "reason": null, "pin_id": null }

{ "check_id": "T-POOLHIST-FEE-COLLECTIONS", "surface": "admin_treasury_reward_custody",
  "name": "Fee collections are enumerated at receipt level with recipient and asset",
  "status": "unknown", "severity": null, "evidence_ids": [], "finding_ids": [],
  "reason": "locker forwards fees through an unverified splitter; native-value legs need traces (L6)", "pin_id": "P1", "limitation_id": "L6" }

{ "check_id": "T-POOLHIST-LOCKER-EVENTS", "surface": "canonical_lp_principal_custody",
  "name": "Locker lock/extend/unlock/transfer history decoded and matched to custody reads",
  "status": "pass", "severity": null, "evidence_ids": ["E52"], "finding_ids": [], "reason": null, "pin_id": "P1" }
```

Finding row:
```json
{ "finding_id": "F21", "surface": "canonical_lp_principal_custody", "severity": "high",
  "proposition": "At tx <tx hash> (block <b>) the holder of position <tokenId> decreased liquidity by <int> and collected <int> of <token0> and <int> of <token1> to <recipient>; the position was re-created at tx <tx2> with <pct>% of the prior liquidity.",
  "chain_id": <chain_id>, "address": "<position_manager>", "pin_or_tx": "<tx hash>",
  "evidence_ids": ["E51"], "evidence_type": "receipt", "confidence": "proven",
  "alternatives": "A rebalance to a new tick range explains the same events; the net principal that left custody is the difference stated.",
  "coverage": "position <tokenId> only; other positions in S5",
  "stale_conditions": "none for the historical fact; current custody is stated under B-PRINCIPAL at P1",
  "is_historical": true }
```

Unresolved questions:
- "Who received the native-asset leg of the collect at <tx> (trace unavailable, L6)."
- "Whether the locker's `transferLock`-style function (selector <0x…>) was ever called; the locker source is
  unverified and its logs are decoded from selectors only."

## What remains unknown if it cannot be completed

- Whether principal was ever removed and re-added (a current "locked" read cannot exclude a past withdrawal
  and re-lock), so `historical_launch_integrity` and `canonical_lp_principal_custody` keep `coverage: partial`.
- Who collected fees and how much — the fee-origin question in surface G/F stays `unknown`.
- Whether positions exist outside the manager (direct pool `Mint` by another owner) that the NFT view hides.
- For v4, whether a hook or a direct `sender` modified the pool's liquidity outside the PositionManager.
State the covered span and the positions covered so surface B can rate on what was actually seen.

## Common mistakes

- Treating a `DecreaseLiquidity` event as funds leaving; in v3 nothing leaves until `Collect`.
- Counting a post-decrease `Collect` entirely as fees, or entirely as principal.
- Reading the PoolManager's ERC-20 balance as one v4 pool's reserves, or filtering v4 logs without the
  `PoolId` topic so another pool's events pollute the timeline.
- Assuming the position manager address from another chain; resolve it from the creation log on the target
  chain (verify at use time).
- Taking the locker's `lock` event as custody without an `ownerOf`/`balanceOf` read at the pin.
- Ignoring `ApprovalForAll`/`getApproved` on the NFT: an operator can move the position without a transfer.
- Filtering ERC-721 `Transfer` with the three-topic ERC-20 shape, or vice versa.
- Dropping direct pool positions because they have no NFT.
- Concluding "liquidity never moved" from a range that was only partially fetched.
