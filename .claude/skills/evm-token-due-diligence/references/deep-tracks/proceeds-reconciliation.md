# Deep track: fee-wallet and cross-chain proceeds reconciliation

Track code `PROCEEDS`. Check ids `T-PROCEEDS-<SLUG>`. Feeds surface F (`F-FEES`, `F-TREASURY`,
`F-SUBSEQUENT-USE`), surface G (fee-origin questions) and the proceeds columns of
`references/deep-tracks/launch-cohort.md`. Run it because a balance is a number, not a story: only a per-asset
reconciliation that closes (or bounds its unexplained delta) can say where a wallet's assets came from and
where they went. The identity used everywhere is
`opening + inflows + explained adjustments = outflows + closing + unexplained`, computed by
`scripts/reconcile.py`.

## Trigger

- A fee recipient, treasury, deployer, launch signer or cohort wallet holds or held material assets whose
  origin or destination is asked about ("did the vault come from LP fees", "where did the launch proceeds go").
- `F-TREASURY` could not close a balance from sampled transfers.
- Outflows leave the target chain (bridge contracts, canonical bridge deposits, messaging contracts).
- Transformations (wrap/unwrap, burn, swap, LP add) make naive inflow/outflow sums double-count.
- Any claim of "profit", "taken out", "dumped for X" is about to be written; it may not be written before this
  track closes the assets it refers to.

## Minimum evidence

| Item | Source | Why |
|---|---|---|
| Pin `P1` on the target chain; one pin per additional chain touched (`P2…`, `purpose: secondary_chain`), each with a captured header | `scripts/rpc_probe.py --rpc <dest rpc> --chain-id <id> --block <n>`; chain id verified from RPC (`references/chains/chain-verification.md`) | balances are chain+block facts; a destination chain without its own pin is E-CHAIN-UNPINNED |
| Opening and closing balances per asset per wallet at explicit blocks | `eth_getBalance` (native) / `eth_call balanceOf` at the opening block and at the pin | the identity has no meaning without both ends |
| Complete transfer logs for each ERC-20 asset in the window (`to`/`from` = wallet) | paged `eth_getLogs` with declared windows | inflow/outflow rows |
| Internal native-value transfers | `trace_filter` / `debug_traceTransaction` (`callTracer`) per candidate tx; if unavailable → limitation | native inflows from contracts emit no log |
| Receipts for every row (status, gasUsed, effectiveGasPrice, logs) | `eth_getTransactionReceipt` | reverted rows are excluded but their gas is real |
| Bridge contract identities on both chains, with `code_hash` and provenance | scope rows; verify at use time from the bridge's deposit/message logs and the destination relay receipt | bridge addresses differ per chain and version; never recall them |

## Procedure

1. Scope the wallets and assets. List each wallet with its neutral role (`fee_recipient`, `treasury`,
   `launch_signer`, `funder`, `holder`, `exchange_deposit`, `bridge`) and each asset to reconcile (native,
   wrapped native, quote stables, the target token, LP tokens). One `flows.json` per (chain, wallet, asset).

2. Set the window. Opening block = first inbound to the wallet, or the block the question starts at; closing
   block = the chain's pin. Read opening and closing balances at those blocks and record both reads as
   `rpc_state` evidence with the block in the query.

3. Build rows in the `reconcile.py` format (integer base units, `status` from the receipt):
   ```json
   { "asset": {"chain_id": <chain_id>, "address": "<erc20 or 'native'>", "symbol_status": "resolved|unresolved|nonstandard", "decimals": <int>},
     "opening": {"balance": "<int>", "block": <A>},
     "closing": {"balance": "<int>", "block": <P1.block_number>},
     "rows": [
       {"tx": "<hash>", "status": "success", "direction": "in",            "amount": "<int>", "block": <b>, "counterparty": "<pool>",   "note": "swap proceeds; Swap log"},
       {"tx": "<hash>", "status": "success", "direction": "transform_out", "amount": "<int>", "block": <b>, "counterparty": "<weth>",   "note": "native -> WETH wrap; Deposit log"},
       {"tx": "<hash>", "status": "success", "direction": "out",           "amount": "<int>", "block": <b>, "counterparty": "<bridge>", "note": "bridge deposit; message id <id>"},
       {"tx": "<hash>", "status": "success", "direction": "gas",           "amount": "<int>", "block": <b>, "counterparty": "",         "note": "gasUsed*effectiveGasPrice"},
       {"tx": "<hash>", "status": "reverted","direction": "out",           "amount": "<int>", "block": <b>, "counterparty": "<router>", "note": "reverted swap; excluded"},
       {"tx": "<hash>", "status": "success", "direction": "gas",           "amount": "<int>", "block": <b>, "counterparty": "",         "note": "gas of the reverted tx above; still spent"},
       {"tx": "<hash>", "status": "success", "direction": "adjustment",    "amount": "<int>", "block": <b>, "counterparty": "<bridge>", "note": "bridge fee: sent - delivered, per destination receipt"}
     ] }
   ```
   Rules that keep the identity honest:
   - **wraps/unwraps**: `transform_out` on the native file and `transform_in` on the wrapped-asset file, same
     tx, same amount; `reconcile.py` pairs transforms so nothing is counted twice.
   - **burns**: `out` to `0x0`/burn address with the note; a burn is an outflow, not a disappearance.
   - **bridge legs**: `out` to the bridge on the source file; `in` from the bridge/relayer on the destination
     file (its own chain, its own pin); the fee difference is an `adjustment` on whichever side the bridge
     charges it, cited to the destination receipt.
   - **gas**: one `gas` row per transaction sent by the wallet, on the native file only, whether or not the
     transaction succeeded.
   - **reverts**: the value row carries `status: "reverted"` and is excluded; never delete it, the row is the
     evidence that the attempt happened.
   - **swaps**: `out` of the sold asset and `in` of the bought asset are separate rows in separate files;
     never net them.

4. Run the reconciliation per file and preserve the output:
   `python3 <skill-root>/scripts/reconcile.py --flows flows-<chain>-<wallet>-<asset>.json --tolerance 0 --json`
   Exit 1 means the unexplained delta exceeds tolerance. Tolerance is `0` for ERC-20 assets with full log
   coverage; for native assets without trace coverage state the tolerance you accept and the limitation that
   forces it. Never raise the tolerance to make a file pass.

5. Explain the unexplained. In order: missed log windows; internal native transfers (need traces); fee-on-
   transfer or rebasing assets (replay per `references/deep-tracks/transfer-replay.md`); self-transfers
   counted once; a wrong opening block. Anything still unexplained is reported as a bounded delta with sign
   and size, and the check is `finding` (if the wallet is privileged and the delta is material) or `unknown`.

6. Match every bridge leg on both sides. A leg is matched only when all six are present; record which are
   missing otherwise and call the leg "sent to bridge; delivery unproven":
   1. source receipt, status `0x1`, deposit/message log decoded from the bridge's proven interface;
   2. identifiers from that log (nonce, message id, transfer id, destination chain id as encoded);
   3. destination chain pin (`P<n>`) with a header captured from the destination RPC whose `eth_chainId`
      matches the encoded destination (verify at use time);
   4. recipient on the destination as encoded in the source message;
   5. delivered amount (destination `Transfer`/mint log or native trace to the recipient);
   6. destination receipt, status `0x1`, whose decoded identifiers equal (2).
   Third-party bridges that mint a representation on the destination deliver a different asset (the
   representation); record the destination asset address, its own controls belong to surface H.

7. Apply the commingling stop rule. Exact attribution ends at the first address where the traced amount
   merges with material inflows from unrelated sources (an exchange deposit or hot wallet, a shared
   router/settlement contract balance, a large multisig with many depositors, a mixer or privacy pool, a
   liquidity pool). Record the amount delivered into that address, the block, and the reason exact tracing
   stops. Do not apply FIFO/LIFO/proportional assumptions and present the result as a fact; if a heuristic is
   used for a bounded estimate, label it `inference` and state the heuristic.

8. State exchange deposits correctly. An address labeled as an exchange deposit is `explorer_label`
   provenance. A transfer to it proves that the asset reached that address; it does not prove a sale, a fiat
   withdrawal, an account holder, or a final beneficiary. Write exactly that.

9. Aggregate per wallet per asset: opening, inflows by class (fees collected, swap proceeds, transfers from
   scope wallets, other), outflows by class (swaps, bridges, transfers to scope wallets, exchange deposits,
   burns, gas), closing, unexplained. Fee-origin questions are answered by matching inflow rows to the
   receipt-level fee collections from `references/deep-tracks/pool-position-history.md`; an inflow from the
   pool or position manager that matches a `Collect` row is a fee; anything else is not a fee, whatever the
   website says.

10. Never write "profit". Proceeds by asset may be stated. A profit statement requires all four: a defensible
    cost basis (allocation cost, buy cost, gas, platform and transfer fees at execution), the material flows
    (this track), fees paid, and retained inventory valued at a stated pinned quote with its route
    (surface C). If any is missing, the sentence is "proceeds of <int> <asset> were received; profit not
    computed because <missing item>".

## Stopping condition

- **Closed**: every (chain, wallet, asset) file reconciles with unexplained delta ≤ tolerance, every bridge
  leg is matched on six points or explicitly marked unproven, and every terminal destination is either a
  scope address, a burn, or a commingling stop with the delivered amount recorded.
- **Bounded**: the question concerned a single asset or a single inflow class; stop there and declare the
  rest out of scope in the discovery row.
- **Blocked**: traces, logs, or a destination RPC are unavailable; the affected files are `unknown` with a
  limitation, and totals are reported as "at least" on the covered rows only.

## Output rows

SYNTHETIC EXAMPLE — placeholders; not a live finding.

Evidence rows:
```json
{ "evidence_id": "E71", "chain_id": <chain_id>, "address": "<fee_recipient>", "pin_id": "P1", "tx_hash": null,
  "block_number": <P1.block_number>, "evidence_type": "log_decoded",
  "artifact": "artifacts/flows-<chain>-<wallet>-<asset>.json", "artifact_sha256": "<sha256>",
  "query": {"tool": "scripts/reconcile.py", "args": "--flows flows-<chain>-<wallet>-<asset>.json --tolerance 0 --json", "opening_block": <A>, "closing_block": <P1.block_number>, "log_windows": "artifacts/proceeds-ranges.json"},
  "decoding_basis": "Transfer(address,address,uint256) logs to/from wallet; receipts for status and gas; Deposit/Withdrawal for wraps",
  "summary": "opening <int> + in <int> + adj <int> = out <int> + closing <int> + unexplained 0" }

{ "evidence_id": "E72", "chain_id": <dest_chain_id>, "address": "<recipient>", "pin_id": "P2",
  "tx_hash": "<dest tx>", "block_number": <block>, "evidence_type": "receipt",
  "artifact": "artifacts/receipt-<dest tx>.json", "artifact_sha256": "<sha256>",
  "query": {"method": "eth_getTransactionReceipt", "tx": "<dest tx>", "rpc_endpoint_redacted": "<redacted>"},
  "decoding_basis": "bridge relay event per destination bridge proven interface; message id equals source log; Transfer to recipient",
  "summary": "<int> delivered to <recipient> on chain <dest_chain_id>; source sent <int>; fee <int>" }
```

Check rows:
```json
{ "check_id": "T-PROCEEDS-PER-ASSET", "surface": "admin_treasury_reward_custody",
  "name": "Each (chain, wallet, asset) reconciles with bounded unexplained delta",
  "status": "pass", "severity": null, "evidence_ids": ["E71"], "finding_ids": [], "reason": null, "pin_id": "P1" }

{ "check_id": "T-PROCEEDS-BRIDGE-MATCH", "surface": "admin_treasury_reward_custody",
  "name": "Every bridge leg matched on source receipt, identifiers, destination pin, recipient, delivered amount, destination receipt",
  "status": "pass", "severity": null, "evidence_ids": ["E72"], "finding_ids": [], "reason": null, "pin_id": "P2" }

{ "check_id": "T-PROCEEDS-COMMINGLING", "surface": "admin_treasury_reward_custody",
  "name": "Exact attribution stops at commingling with delivered amount and reason recorded",
  "status": "finding", "severity": "info", "evidence_ids": ["E71", "E72"], "finding_ids": ["F41"], "reason": null, "pin_id": "P2" }

{ "check_id": "T-PROCEEDS-EXCHANGE-DEPOSIT", "surface": "admin_treasury_reward_custody",
  "name": "Exchange-deposit destinations stated as delivery only, not sale or beneficiary",
  "status": "pass", "severity": null, "evidence_ids": ["E72"], "finding_ids": [], "reason": null, "pin_id": "P2" }

{ "check_id": "T-PROCEEDS-FEE-ORIGIN", "surface": "reward_accounting_liveness",
  "name": "Inflows claimed as LP fees match receipt-level Collect rows",
  "status": "unknown", "severity": null, "evidence_ids": [], "finding_ids": [],
  "reason": "native-value fee legs from the position manager need traces; trace methods unavailable (L9)", "pin_id": "P1", "limitation_id": "L9" }
```

Finding row:
```json
{ "finding_id": "F41", "surface": "admin_treasury_reward_custody", "severity": "info",
  "proposition": "Of <int> <asset> received by <fee_recipient> on chain <chain_id> between blocks <A> and <P1>, <int> was bridged to chain <dest_chain_id> and delivered to <recipient> at tx <dest tx>, then transferred to <exchange_deposit> (explorer-labeled deposit address) at tx <tx>; exact attribution stops there.",
  "chain_id": <dest_chain_id>, "address": "<recipient>", "pin_or_tx": "<dest tx>",
  "evidence_ids": ["E71", "E72"], "evidence_type": "receipt", "confidence": "proven",
  "alternatives": "The deposit address label is unverified; the transfer proves delivery to that address only, not a sale, withdrawal or beneficiary.",
  "coverage": "all ERC-20 rows full; native internal transfers partial (L9)",
  "stale_conditions": "closing balances change after P1/P2; historical rows do not",
  "is_historical": true }
```

Unresolved questions:
- "Whether the <int> native units unexplained on <wallet> are internal transfers (traces unavailable, L9)."
- "Whether the bridge leg at <src tx> was delivered; no destination receipt matched identifier <id> within the
  searched destination range <a>-<b>."

## What remains unknown if it cannot be completed

- Where the fee or treasury assets went, so `admin_treasury_reward_custody` keeps `coverage: partial` and
  `F-TREASURY` is `unknown`.
- Whether a vault or reward balance originated from LP fees (surface G stays `unknown` on origin).
- Whether launch proceeds left the chain and where they landed; cross-chain claims cannot be made.
- Any statement about proceeds size beyond the covered rows; report lower bounds only.

## Common mistakes

- Netting a swap's out and in legs into one row, or wrapping counted as both a spend and a receipt.
- Deleting reverted rows instead of marking them; losing their gas.
- Reconciling a destination chain against the source chain's pin (E-CHAIN-UNPINNED / E-FINDING-CHAIN).
- Matching a bridge leg on amount alone; amounts repeat, identifiers do not.
- Treating "sent to bridge" as "arrived".
- Continuing to trace through an exchange deposit or a pooled contract with proportional guesses.
- Reporting an exchange deposit as a sale or fiat exit.
- Writing "profit", "took out", or "made" without cost basis, fees and retained inventory.
- Raising `--tolerance` until `reconcile.py` exits 0.
- Reading closing balances at `latest`.
