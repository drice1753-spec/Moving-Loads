# Chain verification and infrastructure checks

Read this before the first RPC read on any chain, and again for every additional chain a run touches
(bridge legs, cross-chain treasuries, a second deployment of a dependency). Why: a symbol exists on many
chains, the same address string exists on every EVM chain (CREATE2 and nonce reuse), an endpoint URL says
nothing about which chain answers, and a pin without a verified chain id binds nothing. Everything
chain-specific in this file (ids, hosts, finality numbers) is "verify at use time"; the procedures are the
content.

## 1. Verify the chain id first (the same-symbol-substitution guard)

1. Read `eth_chainId` live from the endpoint you will use - never from a cache, never from a chain list -
   and compare it with the REQUESTED chain id. `scripts/rpc_probe.py` does exactly this before any other
   read; on mismatch it writes the packet with `identity.status = CHAIN_MISMATCH` and exits 3, and on an
   unreadable chain id it writes `UNVERIFIED`, exits 1 and reads nothing else. Exit 3: STOP - a mismatch
   is an identity failure recorded under `identity`, never a coverage limitation; fix the endpoint or ask
   the user which chain was meant. Exit 1: STOP and report the limitation (nothing could be pinned).
   Exit 0: continue. Without `--chain-id` the packet says `OBSERVED_ONLY`: obtain the user's explicit
   confirmation of the observed id, re-run with `--chain-id`, and require `MATCH` before anything else.
   The validator rejects a manifest whose requested and observed chains differ (`E-CHAIN-MISMATCH`;
   fixture `tests/fixtures/reject-same-symbol-other-chain`).
   ```
   python3 <skill-root>/scripts/rpc_probe.py --rpc URL --address 0x<target> --chain-id <requested> --block finalized --out target-packet.json
   # --block finalized where the endpoint supports the tag; fall back to latest if it is rejected, and record which (section 3)
   ```
2. `eth_chainId` is authoritative; `net_version` is legacy and can differ on some chains. Chain names are
   labels (`target.requested.chain_name`): a name the user gave may be mapped to a REQUESTED id (step 4)
   but never decides anything - `eth_chainId` does.
3. Record `web3_clientVersion` in `observed.client_version` (the node software hints at the stack and
   therefore at finality semantics, section 3). A load balancer can front several chains: verify per
   endpoint and per session, and re-verify at the end of a long run by re-reading the primary pin with
   `eth_getBlockByHash` (section 2).
4. If the user supplied only a token symbol or token name (not an address), ask for chain id and address;
   never resolve either from a token list, an explorer search, or a dashboard. A chain NAME ("Base",
   "Arbitrum") given with an address MAY be mapped to the commonly cited id in section 6 as the REQUESTED
   id, provided the mapping is recorded in `requested.source` ("user said 'Base'; mapped to commonly cited
   id 8453, confirmed by eth_chainId") and `eth_chainId` on the endpoint confirms it; a different observed
   id is a mismatch - stop and ask which of the two the user meant.

## 2. One pin per chain touched

- The target chain gets `P1` (`primary_pin_id`); every further chain gets its own pin (`purpose:
  secondary_chain`), and every historical snapshot its own pin (`purpose: historical`). A chain referenced
  by any scope address, evidence row, finding or check without a pin fails validation
  (`E-CHAIN-UNPINNED`); a primary pin not on the target chain fails (`E-PIN-PRIMARY`).
- Pin with the probe (`--block finalized|safe|latest|N`; `finalized` where supported, section 3); the pin
  carries the raw header, so `E-PIN-HEADER-MISMATCH`/`E-PIN-PLACEHOLDER`/`E-PIN-TIME` catch edited or
  invented pins.
- Reorg check at the end of the run: `eth_getBlockByHash(pin.block_hash)` must still return the block. A
  null result means the pinned block was reorged: re-pin, redo every state read, and re-fetch receipts
  whose block hashes changed. Record the check as a `manual_note` evidence row.

## 3. Finality and reorgs, especially on L2s

Why: "current state" read at an unfinalized block can disappear, and a bridge leg that has not passed its
challenge period has not delivered anything yet.

- L1 (Ethereum-style proof of stake): `safe` = justified, `finalized` = two epochs behind the head
  (minutes, verify against the chain's own documentation); `latest` can reorg by a block or two.
- Rollups with a sequencer (OP-stack, Arbitrum-style/Nitro, others): the sequencer's soft confirmation is
  immediate but unfinalized; `safe` typically means derived from batches posted to L1, `finalized` means
  those L1 blocks are finalized; the sequencer can reorder or drop unsafe blocks, and a single sequencer is
  a liveness dependency for any exit. Some stacks add transaction filtering or forced-inclusion delays that
  can stop a specific transaction from executing; when the decision question is "can a holder exit", that
  is an `external_dependencies` item (`H-DEPS`), not a token finding.
- Challenge periods (commonly 7 days on optimistic rollups; verify) bound canonical withdrawals, not the
  validity of a state read; they matter for `F-BRIDGE-LEGS` and proceeds reconciliation.
- Rule: pin to `finalized` when the endpoint supports the tag and the decision tolerates the lag (the
  canonical `rpc_probe.py` command in `SKILL.md` does); otherwise `latest`, or `latest` minus a stated
  margin (choose the margin from the stack's reorg depth, and say why). Say which in the pin's `purpose`
  and in `## Coverage and limitations`. Historical snapshots go well past finality.
- An endpoint that rejects `finalized`/`safe` (error or null) is recorded as a limitation (`rpc_error`);
  re-run with `latest` or a numbered block and record which tag the pin actually used.

## 4. RPC capability probing, recorded as coverage

Why: a check that could not run because the endpoint cannot serve it is a coverage limitation, and the
report must say what was unavailable rather than silently narrowing the search. Probe once per endpoint,
record the results in `target.known_limitations` and `coverage.limitations` (kinds: `rpc_timeout`,
`rpc_rate_limit`, `rpc_pruned`, `rpc_error`, `dns_failure`, `api_unavailable`, `explorer_unavailable`,
`source_unavailable`, `out_of_scope`, `other`), and keep the raw responses. `ddcore.RpcClient` only permits
read-only methods and classifies failures into these kinds (`RpcCoverageError.kind`,
`.as_limitation("L<n>")`).

| Capability | Probe | Interpretation |
|---|---|---|
| State at the pin | `eth_getCode(target, P1)`; `eth_getStorageAt(target, 0x0, P1)` | must succeed; otherwise nothing is pinned |
| Archive state (historical snapshots, `P2`, replay confirmations) | the same two calls at an older block (deployment block, or P1 minus a stated distance), plus `eth_call balanceOf(holder)` there | "missing trie node" / "pruned" / "historical state" errors = `rpc_pruned`; historical checks become `unknown`, never `pass` |
| `eth_getLogs` window and result caps | request the token's `Transfer` topic over a window (start at 2,000 blocks); on "block range too large" / "more than N results" / timeout, halve the window and retry | record the final window size and any result cap as `discovery[].pagination`; gaps are limitation ids |
| Traces (native value legs, internal CREATE) | `debug_traceTransaction(tx, {"tracer":"callTracer"})` or `trace_transaction(tx)` on a known tx | unsupported = limitation for `C-HIST-SELL` native legs, `E-FUNDING`, `T-REPLAY-*`; not a finding |
| Batch receipts | `eth_getBlockReceipts(P1)` | unsupported = per-transaction receipts; slower, not narrower |
| Rate limits | observe HTTP 429 or "rate limit" errors during the probes | `rpc_rate_limit` with `retry_attempts`; slow down before narrowing scope |
| Fee data for gas costs | `eth_gasPrice`, `eth_feeHistory` | needed for exit-cost statements in native units |

```
python3 - <<'EOF'
import sys; sys.path.append("<skill-root>/scripts")
from ddcore import RpcClient, ResponseCache, RpcCoverageError, int_to_hex, hex_to_int
c = RpcClient("<RPC URL>", cache=ResponseCache("rpc-cache.json"))
observed = hex_to_int(c.call("eth_chainId", []))          # live, never cached
assert observed == <requested chain id>, f"chain mismatch: observed {observed}"
c.set_chain_id(observed)   # binds the cache to the live chain id; nothing is cached before this, and a
                           # cache file recorded for another chain raises RpcCoverageError (use one per packet)
pin = <P1 block number>; old = <deployment block or pin - 100000>; target = "0x<target>"
for name, method, params in [
    ("state@pin", "eth_getCode", [target, int_to_hex(pin)]),
    ("archive@old", "eth_getStorageAt", [target, "0x0", int_to_hex(old)]),
    ("logs-2000", "eth_getLogs", [{"address": target, "fromBlock": int_to_hex(pin - 2000), "toBlock": int_to_hex(pin),
        "topics": ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"]}]),
    ("trace", "debug_traceTransaction", ["0x<known tx>", {"tracer": "callTracer"}]),
    ("block-receipts", "eth_getBlockReceipts", [int_to_hex(pin)]),
]:
    try:
        r = c.call(method, params); print(name, "ok", (len(r) if isinstance(r, (list, str)) else type(r).__name__))
    except RpcCoverageError as e:
        print(name, "LIMITATION", e.as_limitation())
print("endpoint", c.redacted_url, "calls", len(c.calls))
EOF
```
Keep the printed limitation objects; they are the `coverage.limitations` rows (assign `L<n>` ids and the
affected check ids). Never write the endpoint URL unredacted (`ddcore.redact_url`).

## 5. Gas token and native currency in quotes

Why: a native amount labeled "ETH" on a chain whose gas token is not ETH misstates the exit asset, and a
v4 native pool quotes in whatever `address(0)` is on that chain.

- Determine the native currency from the chain's documentation and confirm it from a receipt: gas paid =
  `gasUsed x effectiveGasPrice` in native units; on rollups an L1-data component may sit in extra receipt
  fields (`l1Fee`-style on OP-stack, `gasUsedForL1`-style on Arbitrum-style stacks; names vary - read the
  receipt keys) and is part of exit cost.
- Wrapped native tokens are ordinary ERC-20 contracts per chain: identify by behavior (`deposit`/`withdraw`
  with `Deposit(address,uint256)` `0xe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c` and
  `Withdrawal(address,uint256)` `0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65`), not by
  symbol; a "WETH" on a chain whose native currency is not ETH is a bridged representation with a bridge
  dependency.
- Every quote names the quote asset by address and class: native, wrapped native, bridged representation
  (which bridge), issuer stablecoin (which issuer or bridge), or synthetic claim. "Sellable into X" ends
  where X's own exit is not established (`references/surfaces/G-rewards-vaults-backing.md`,
  `references/surfaces/H-utility-dependencies-dev.md`).
- v4 pools with `currency0 == address(0)` pay out in the chain's native currency
  (`references/platforms/uniswap-v4.md`); v2/v3 routes end in the wrapped token unless a router unwraps.
- Native balance deltas (sells paid in native currency) are visible only through traces or per-block
  balance reads; without trace support say so (section 4).

## 6. Commonly cited chain ids (verify via `eth_chainId` at use time)

Labels for the packet's `chain_name`, and the mapping source when the user names a chain instead of an
id (section 1 step 4: record the mapping in `requested.source`; `eth_chainId` confirms). The table is
never proof; the only proof is `eth_chainId` on the endpoint used.

| Chain (label) | Commonly cited id | Native currency (commonly cited) |
|---|---|---|
| Ethereum mainnet | 1 | ETH |
| Optimism (OP Mainnet) | 10 | ETH |
| Cronos | 25 | CRO |
| BNB Smart Chain | 56 | BNB |
| Gnosis | 100 | xDAI |
| Unichain | 130 | ETH |
| Polygon PoS | 137 | POL |
| Sonic | 146 | S |
| X Layer | 196 | OKB |
| opBNB | 204 | BNB |
| Fantom (Opera) | 250 | FTM |
| zkSync Era | 324 | ETH |
| World Chain | 480 | ETH |
| Polygon zkEVM | 1101 | ETH |
| Soneium | 1868 | ETH |
| Robinhood Chain (per research; `references/chains/robinhood-chain.md`) | 4663 | ETH |
| Mantle | 5000 | MNT |
| Base | 8453 | ETH |
| Mode | 34443 | ETH |
| Arbitrum One | 42161 | ETH |
| Arbitrum Nova | 42170 | ETH |
| Celo | 42220 | CELO |
| Avalanche C-Chain | 43114 | AVAX |
| Ink | 57073 | ETH |
| Linea | 59144 | ETH |
| Berachain | 80094 | BERA |
| Blast | 81457 | ETH |
| Scroll | 534352 | ETH |
| Zora | 7777777 | ETH |
| Sepolia (testnet) | 11155111 | ETH |
| OP Sepolia / Base Sepolia / Arbitrum Sepolia (testnets) | 11155420 / 84532 / 421614 | ETH |

An id not in this table is not suspicious; an id that DIFFERS from the requested one is a stop.

## 7. Infrastructure failures are coverage limitations, never token findings

- Timeouts, rate limits, pruned state, DNS failures, unreachable explorers or APIs, and unavailable source
  go into `coverage.limitations` with `limitation_id`, `kind`, `description` (method, params with the
  block, redacted endpoint, error text), `affected_check_ids`, `affected_addresses`, `retry_attempts`.
- Each affected check is `unknown` (or `skipped` with the reason) and cites the limitation id; the rating
  over it cannot be `low` without `coverage_qualified: true` and a `coverage_note`
  (`E-RATING-UNKNOWN-AS-LOW`). A limitation nobody references triggers `W-LIMITATION-UNREFERENCED`.
- Retry before recording: `RpcClient` retries timeouts and rate limits with backoff; try a second endpoint
  for the same chain (verify its chain id too) before declaring a range unreadable.
- Write "Unknown because historical state was unavailable (L3, `rpc_pruned`)", never "no early sales
  found" for a range that was not read. `references/examples/behavioral-examples.md` scenario 6 shows the
  shape.
- A revert of an `eth_call` is not an infrastructure failure: it is a fact about the contract at that
  block (record the revert data).

## 8. Explorers and dashboards are discovery only

- Use them to find candidates: deployment transaction, creator, labels, verified-source downloads, holder
  and pool listings, ABI hints. Then confirm every candidate on-chain: receipt (`contractAddress`, logs),
  `eth_getCode`, pinned reads, decoded logs. Evidence types `explorer`/`dashboard`/`website` are
  discovery-class; a material finding resting only on them draws `W-EVIDENCE-DISCOVERY-ONLY`.
- Confirm the explorer serves the requested chain: fetch a transaction hash you obtained from RPC and check
  the explorer's chain id or network label against `eth_chainId`; a multi-chain explorer UI can silently
  switch networks.
- An explorer's "verified" badge is the explorer's claim. Record downloaded source as `source_unverified`
  until correspondence with the deployed runtime hash is established (`H-SOURCE-CORRESPONDENCE`); then
  `source_verified`.
- Explorer API keys are credentials: redact them exactly like RPC keys; an unavailable or unauthorized
  explorer API is `explorer_unavailable`.
- Labels ("Uniswap V3: Pool", "Team wallet", "Locker") are `explorer_label` provenance and neutral roles
  apply (`references/attribution.md`).

## 9. Onboarding an unfamiliar chain (checklist)

1. Take chain id (or a chain name mapped per section 1 step 4 and recorded in `requested.source`) and
   address from the user; run the probe with `--block finalized`; stop on mismatch (exit 3) or an
   unverified id (exit 1).
2. Identify the stack from `web3_clientVersion` and the chain's documentation (L1, OP-stack, Nitro-style,
   zk rollup, sidechain); note block time, finality tags supported, sequencer model, challenge period,
   transaction-filtering or forced-inclusion features, upgrade authority. Record each as a
   `manual_note` with its source and as `H-DEPS` rows where material to the decision question.
3. Run the capability probes (section 4) and record limitations before scoping any search.
4. Resolve the native currency, the wrapped-native contract and the quote assets by behavior (section 5).
5. Confirm the explorer serves the chain (section 8); treat it as discovery.
6. Find DEX infrastructure from the target's own logs (`PairCreated`/`PoolCreated`/`Initialize` receipts),
   then cross-check against the DEX's deployment page; `references/platforms/*.md` for the reads.
7. Size log windows to the block time: a 100 ms chain produces on the order of 864,000 blocks per day, so a
   2,000-block window is minutes, and second-resolution timestamps repeat across blocks (order by block
   number and transaction index, not by timestamp).
8. Pin (`finalized` when supported), record the pin rationale, and re-verify the pin hash at the end.
9. Write the chain's known limitations into the packet before freezing it; a worked application is
   `references/chains/robinhood-chain.md`.
