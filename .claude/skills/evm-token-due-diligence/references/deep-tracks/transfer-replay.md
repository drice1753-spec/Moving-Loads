# Deep track: full holder / Transfer replay

Track code `REPLAY`. Check ids `T-REPLAY-<SLUG>`. Feeds surface D (`references/surfaces/D-supply-concentration.md`,
checks `D-SUPPLY`, `D-CONC`) and, when the question is historical, surface E (`E-LAUNCH`) and
`references/deep-tracks/launch-cohort.md`. Run it because sampled `balanceOf` reads and explorer holder lists
cannot show where a discrepancy comes from; only a log-by-log replay from deployment to the pin can.

## Trigger

Run this track when any of the following is true; otherwise stay with the sampled reconciliation in surface D.

- `D-SUPPLY` shows a difference between `totalSupply()` at the pin and the sum of sampled or explorer-listed
  balances that cannot be explained by the sample size.
- The runtime contains a path that changes balances without an ordinary `Transfer` (rebasing, `_balances`
  rewrite, seizure, shares math, fee-on-transfer burn without an event) — `A-MINT`/`A-SEIZE` finding or
  `D-NONTRANSFER-CHANGES` unknown.
- A historical holder question is asked ("who held what at block B", "did the launch wallets ever go to zero",
  "how many addresses ever received tokens").
- The launch-cohort track needs an authoritative per-address inbound/outbound ledger.
- An explorer holder page is unavailable, rate-limited, or contradicts RPC reads (`explorer_unavailable` is a
  coverage limitation, never a token finding).

Do not run it merely because a holder count looks large; cost scales with log volume and it must be bounded
by the question.

## Minimum evidence

Without every item below the track cannot start; record the missing item as a coverage limitation and leave
the dependent checks `unknown`.

| Item | Source | Why it is required |
|---|---|---|
| Frozen target packet with `P1` (block number, hash, timestamp) | `scripts/rpc_probe.py --out packet.json` | replay must end at a pinned block, never `latest` |
| Token `code_hash` and `accounting_model` | packet `runtime.code_hash`, `metadata.accounting_model` | decides which events change balances |
| Deployment block or an earlier lower bound | packet `deployment.block_number`; else binary-search the first block where `eth_getCode` is non-empty | the replay start; starting later silently drops mints |
| Event signatures that move balances for this runtime | `scripts/selector_scan.py --code-file runtime.hex --json` (selectors + opcode flags) and verified source when correspondence is proven (`references/deep-tracks/proxy-bytecode.md`) | a replay that decodes only `Transfer` is wrong for wrappers and rebasing tokens |
| An RPC that serves `eth_getLogs` over the whole range and `eth_call` at historical blocks | probe one small window and one historical `balanceOf` first | pruned or capped providers make the result partial, not wrong |

## Procedure

1. Compute the topic hashes you will filter on; never paste them from memory. All values below were produced
   by this command and must be recomputed at use time:
   `python3 -c "import sys; sys.path.insert(0,'<skill-root>/scripts'); from ddcore import keccak256_hex as k; print(k(b'Transfer(address,address,uint256)'))"`

   | Event | topic0 (recompute) | Balance effect |
   |---|---|---|
   | `Transfer(address,address,uint256)` | `0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef` | move; `from == 0x0` mint; `to == 0x0` burn |
   | `Deposit(address,uint256)` (WETH9-style wrapper) | `0xe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c` | mint to `dst` with no `Transfer` |
   | `Withdrawal(address,uint256)` (WETH9-style wrapper) | `0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65` | burn from `src` with no `Transfer` |
   | `TransferSingle` / `TransferBatch` (ERC-1155) | `0xc3d58168…0f62` / `0x4a39dc06…f7fb` | per-id balances; replay per id |
   | Rebase / index / shares events | none standard — take the exact signature from decoded runtime or proven source | balance = shares × index; replay shares, read index at the pin |

   ERC-721 `Transfer` shares the ERC-20 topic0 but carries four topics (indexed `tokenId`). Reject any log whose
   topic count does not match the token's declared shape and record the count of rejected logs.

2. Declare the discovery universe before fetching: address = target token only (never a same-symbol token),
   block range `[deployment_block, P1.block_number]`, topics `[[topic0 list]]`, inclusion rule "every log
   emitted by the target address whose topic0 is in the list", exclusions "none" (exclusions belong to the
   concentration step, not the replay). Write this as a `discovery` row (`S<n>`) now; fill `pagination` and
   `coverage` as you go.

3. Page `eth_getLogs` by block range. Provider caps (max blocks per window, max logs per response, timeout)
   are provider-specific — verify at use time from the provider's error message or documentation; never
   assume. Start with a modest window, halve it on any `rpc_error`/`rpc_timeout` that names the range or the
   result count, and split any window whose result count equals a suspected cap (a full page is a truncation
   signal, not a complete page). Use `ddcore.RpcClient` with `ResponseCache` so retries and re-runs are free
   and reproducible:

   ```python
   import sys; from pathlib import Path
   sys.path.insert(0, str(Path("<skill-root>/scripts").resolve()))
   from ddcore import RpcClient, ResponseCache, RpcCoverageError, int_to_hex
   rpc = RpcClient(url, cache=ResponseCache("replay-cache.json"))
   def page(lo, hi, window):
       ranges, logs = [], []
       while lo <= hi:
           to = min(lo + window - 1, hi)
           try:
               res = rpc.call("eth_getLogs", [{"address": token, "fromBlock": int_to_hex(lo),
                                               "toBlock": int_to_hex(to), "topics": [topic0_list]}])
           except RpcCoverageError as e:
               if window > 1: window //= 2; continue      # shrink, then retry the same lo
               raise                                        # 1-block window still fails: limitation
           ranges.append((lo, to, len(res))); logs += res; lo = to + 1
       return ranges, logs
   ```
   Record every `(from, to, count)` triple in the discovery row's `pagination` field (or in an artifact it
   points to). Gaps or overlaps between consecutive ranges invalidate the replay.

4. Normalize and de-duplicate: drop logs with `removed: true`; key each log by `(blockNumber, logIndex)`;
   reject duplicates from overlapping retries; sort by `(blockNumber, transactionIndex, logIndex)`. Decode
   `from`/`to` from topics 1–2 (`ddcore.decode_address` on the 32-byte topic) and `value` from data
   (`ddcore.decode_uint`). A token that emits `Transfer` with non-indexed addresses (96-byte data, one topic)
   is nonstandard: decode from data and record `decoding_basis: "nonstandard Transfer, all params in data"`.

5. Replay in order. Maintain `balance[address]` and `supply`. `from == 0x0` → mint (`supply += v`);
   `to == 0x0` → burn (`supply -= v`); otherwise move. For wrappers apply `Deposit`/`Withdrawal` as
   mint/burn. For shares-based or rebasing tokens replay the share-denominated events and convert at the pin
   with the index read by `eth_call` at `P1`. Flag any address whose balance goes negative at any point — that
   is either a missed range or a non-`Transfer` balance change; it is never "rounding".

6. Reconcile at the pin. Read `totalSupply()` at `P1` (`0x18160ddd`) and compare with replayed `supply`.
   Read `balanceOf` (`0x70a08231`) at `P1` for: every scope address; the top 50 replayed balances; 20
   addresses chosen by a recorded deterministic rule (for example every k-th address in sorted order); every
   address that went negative. Tolerance is exactly zero for `standard_erc20`; for rebasing/shares tokens the
   tolerance is the documented rounding of the conversion (state it in base units and cite the formula).

7. Explain every discrepancy or mark it unresolved. Candidate mechanisms, in the order to test them:
   a. paging gap or truncation — re-check the range list first, it is the cheapest cause;
   b. a balance-changing function without an event — list transactions **to** the token whose receipts carry
      no `Transfer` involving the affected address, then obtain a storage diff for each candidate
      (`debug_traceTransaction` with `{"tracer":"prestateTracer","tracerConfig":{"diffMode":true}}` or
      `trace_replayTransaction` with `["stateDiff"]`); a changed balance slot with no event is a finding for
      `A-SEIZE`/`A-MINT`, not a replay error;
   c. rebasing or index change — confirm the index read and recompute;
   d. `selfdestruct`/`CREATE2` redeploy at the same address (code hash changed between blocks) — compare
      `eth_getCode` at the deployment block and at `P1`;
   e. provider inconsistency — a second provider disagrees on the same query; record both, mark `rpc_error`.
   If tracing methods are unavailable, the mechanism stays unknown and the check is `unknown` with a
   `limitation_id`.

8. Handle sibling deployments by code hash. When several tokens share `code_hash`, the event set, decoder and
   discrepancy playbook are identical and may be reused once; balances, ranges and results are never shared.
   Record the reuse as `decoding_basis: "same runtime code hash as <address>; decoder reused"`.

9. Hand off. Deliver the holder table at `P1` (address, replayed balance, `balanceOf` sample if read, class
   `unclassified`) to surface D for classification and denominators; deliver per-address inbound/outbound
   ledgers to `references/deep-tracks/launch-cohort.md` when it is running. Preserve the raw log artifact
   (JSON lines) and its sha256.

## Stopping condition

Stop when one of these holds, and say which:

- **Complete**: ranges are contiguous with no truncation signals, replayed `supply == totalSupply()` at
  `P1`, every sampled `balanceOf` equals the replayed balance within the stated tolerance, and every
  non-`Transfer` mechanism found is recorded as its own finding.
- **Explained**: discrepancies remain but each is tied to a cited mechanism with preferred-class evidence
  (storage diff, trace, or pinned index read); the conservation check is `finding` (if the mechanism is a
  privileged balance change) or `pass` with the explanation.
- **Blocked**: a range cannot be fetched at a 1-block window, historical `eth_call` is pruned, or tracing is
  unavailable and at least one discrepancy remains. Record the limitation, leave `T-REPLAY-CONSERVATION`
  `unknown`, and do not extrapolate from the covered portion to the uncovered one.

Do not continue past the pin, and do not widen the universe to other tokens or chains inside this track.

## Output rows

Template rows (SYNTHETIC EXAMPLE — placeholders in angle brackets; not a live finding). Use the manifest
objects exactly; the validator rejects a `pass` without evidence and an `unknown` without a reason.

Discovery row:
```json
{ "discovery_id": "S3",
  "claim": "All balance-changing logs of the target token from deployment to P1 were fetched",
  "search_universe": "eth_getLogs address=<token> topics=[Transfer, Deposit, Withdrawal] on chain <chain_id>",
  "block_range": "<deployment_block>-<P1.block_number>",
  "pagination": "windows recorded in artifacts/replay-ranges.json (n windows, max 1 truncation split)",
  "inclusion_rule": "every log emitted by <token> with topic0 in the list; removed=false; topic count 3",
  "exclusions": "none at this stage",
  "materiality_threshold": "none (replay is exhaustive)",
  "coverage": "full | partial: blocks <a>-<b> unavailable (L<n>)" }
```

Evidence rows:
```json
{ "evidence_id": "E41", "chain_id": <chain_id>, "address": "<token>", "pin_id": "P1", "tx_hash": null,
  "block_number": null, "evidence_type": "log_decoded",
  "artifact": "artifacts/replay-logs.jsonl", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getLogs", "address": "<token>", "fromBlock": "<hex>", "toBlock": "<hex>",
            "topics": ["<topic0 list>"], "windows": "artifacts/replay-ranges.json"},
  "decoding_basis": "Transfer(address,address,uint256) topic0 recomputed with ddcore.keccak256_hex; from/to from topics 1-2; value from data",
  "summary": "<n> logs, <m> mints, <k> burns, replayed supply <int>" }

{ "evidence_id": "E42", "chain_id": <chain_id>, "address": "<token>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "rpc_state",
  "artifact": "artifacts/replay-samples.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_call", "to": "<token>", "data": "0x18160ddd | 0x70a08231+<addr>", "block": "<P1 hex>"},
  "decoding_basis": "uint256 return via ddcore.decode_uint",
  "summary": "totalSupply and <n> balanceOf samples at P1" }

{ "evidence_id": "E43", "chain_id": <chain_id>, "address": "<token>", "pin_id": null,
  "tx_hash": "<tx hash>", "block_number": <block>, "evidence_type": "trace",
  "artifact": "artifacts/trace-<tx>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "debug_traceTransaction", "tx": "<tx hash>", "tracer": "prestateTracer", "diffMode": true},
  "decoding_basis": "storage slot keccak(pad(addr) ++ pad(<balances slot>)) changed with no Transfer log in receipt",
  "summary": "balance of <addr> changed by <int> without an event" }
```

Check rows:
```json
{ "check_id": "T-REPLAY-COVERAGE", "surface": "current_concentration",
  "name": "Log fetch covers deployment..P1 with contiguous, untruncated windows",
  "status": "pass", "severity": null, "evidence_ids": ["E41"], "finding_ids": [], "reason": null, "pin_id": "P1" }

{ "check_id": "T-REPLAY-CONSERVATION", "surface": "current_concentration",
  "name": "Replayed balances sum to totalSupply at P1 and match sampled balanceOf",
  "status": "finding", "severity": "high", "evidence_ids": ["E41", "E42", "E43"], "finding_ids": ["F17"],
  "reason": null, "pin_id": "P1" }

{ "check_id": "T-REPLAY-NONTRANSFER", "surface": "token_controls",
  "name": "Balance changes without Transfer events are enumerated with their mechanism",
  "status": "unknown", "severity": null, "evidence_ids": [], "finding_ids": [],
  "reason": "debug_traceTransaction unavailable on the provider (L4); one discrepancy of <int> base units unexplained",
  "pin_id": "P1", "limitation_id": "L4" }
```

Finding row:
```json
{ "finding_id": "F17", "surface": "current_concentration", "severity": "high",
  "proposition": "At tx <tx hash> the balance of <addr> on chain <chain_id> changed by <int> base units with no Transfer log; the change was executed through a call from <caller> to selector <0x…>.",
  "chain_id": <chain_id>, "address": "<token>", "pin_or_tx": "<tx hash>",
  "evidence_ids": ["E43", "E41"], "evidence_type": "trace", "confidence": "proven",
  "alternatives": "A nonstandard event we did not filter for could describe the same change; checked receipt logs: none from the token.",
  "coverage": "single transaction; other such calls enumerated in S3 window list",
  "stale_conditions": "none for the historical fact; the authority behind the path is a separate token_controls finding under A-SEIZE at P1",
  "is_historical": true }
```

Unresolved questions (add to `verdict.unresolved_questions`, one line each):
- "Blocks <a>–<b> could not be fetched at any window size (L<n>); holders whose only activity falls there
  are absent from the table."
- "One discrepancy of <int> base units at <addr> remains unexplained because tracing was unavailable."

## What remains unknown if it cannot be completed

- Whether `totalSupply()` at the pin equals the sum of holder balances (`D-SUPPLY` stays `unknown`; the
  `current_concentration` rating cannot be `low` without `coverage_qualified: true` and a note).
- Whether any balance was ever changed without an event — the strongest signal for hidden seizure or mint
  authority — so `A-MINT`/`A-SEIZE` cannot be closed on replay grounds.
- The historical holder set and therefore any launch-cohort ledger built on it; the cohort track must
  declare the same gap.
- Whether explorer holder lists are accurate; they remain discovery-class.
State the covered block ranges explicitly so a later run can resume from the gap rather than restart.

## Common mistakes

- Starting at the first `Transfer` seen on an explorer instead of the deployment block, dropping earlier mints
  from a constructor or an initializer.
- Treating a full page as complete instead of a truncation signal; the count cap is provider-specific and
  must be verified at use time.
- Replaying only `Transfer` on a WETH-style wrapper or a rebasing token, then "correcting" the mismatch by
  scaling balances. Reconstruct the mechanism or leave it unknown.
- Reading `balanceOf` at `latest` while replaying to `P1`; every read in this track carries the pin block.
- Mixing ERC-721 `Transfer` logs (four topics) into an ERC-20 replay because topic0 matches.
- Calling a negative interim balance "rounding". It is a gap or a non-event change.
- Reusing replay **results** across sibling tokens with the same code hash; only the decoder is reusable.
- Reporting an RPC-pruned range as "no activity"; a pruned range is a `rpc_pruned` limitation and the
  affected checks are `unknown`.
