# Surface F - Fees, treasury, and proceeds

Rating key: `admin_treasury_reward_custody`. Core checks: `F-FEES`, `F-TREASURY`. Sub-checks:
`F-BASIS-VS-BUCKET`, `F-CLAIM-AUTH`, `F-BRIDGE-LEGS`, `F-COMMINGLING`. Read this file for any question
about where fees go, who can claim them, what a treasury or fee wallet did with them, whether proceeds
crossed a bridge, or whether "X% to holders" means what it says; always in `broad` mode. Attribution
language comes from `references/attribution.md`.

## Purpose

Map every fee the system charges (basis, denomination, split, escrow, claim authority, recipient,
configurability, subsequent use) and reconcile each material asset held by each fee recipient or
treasury with the identity `opening + inflows + explained adjustments = outflows + closing + bounded
unexplained delta`. Configuration is read at P1; realized rates and flows come from receipts over a
stated range; the two are reported side by side and never substituted for one another.

## What can change the verdict

- A claim path that lets an administrator direct fees to an arbitrary address, or a recipient setter
  held by a single key (custody authority; no monetary threshold applies to discovering it).
- Realized fee rates that differ from the configured or advertised rate (a changed setter, an
  exemption, or a bucket-vs-gross misreading).
- Treasury outflows that are not explained by receipts (an unexplained delta above tolerance).
- Proceeds that cross a bridge or reach an exchange deposit: attribution stops there; the verdict must
  say what is bounded and what is not.
- A fee escrow (pool `tokensOwed`, hook storage, splitter balance) that only a privileged caller can
  collect, or that anyone can collect but only to a fixed recipient (different custody exposure).
- Coverage: a pruned range, a missing trace API for internal native transfers, or an unavailable
  destination-chain RPC turns reconciliation rows `unknown`.

## Procedure

Every configuration read cites P1. Every flow row cites a tx hash and block. Every other chain gets
its own pin (P2...). Use base units (integers) throughout; convert for display only.

1. Inventory fee sources. For each contract in the architecture pass that can collect a fee, one row:
   `source | basis | denomination | split | escrow location | claim authority | recipient(s) |
   configurable by | subsequent use`. Sources to look for:
   - per-trade token tax in the token contract (rates and exemptions from `A-TAX`; swap-back
     thresholds that convert the tax to quote via an external call, `A-EXTCALL`);
   - LP fee share on a v3 position (`tokensOwed0/1` in `positions(tokenId)`, `Collect` events on the
     pool and the position manager) or a v4 hook/pool (fee deltas in `ModifyLiquidity`/`Swap`, hook
     storage; see `references/platforms/uniswap-v3.md`, `references/platforms/uniswap-v4.md`);
   - protocol fee (v3 `feeProtocol` via `slot0()`, v4 protocol fee on the manager; who can set it);
   - launch-platform trade fee on a curve (creator/platform/treasury split; escrow in the curve);
   - claim fees charged on reward or vault withdrawals (surface G).
2. `F-BASIS-VS-BUCKET` - state the base of every percentage. A fee bucket is the amount already taken
   by a pool or tax; a share of it is a much smaller fraction of gross. Synthetic worked example
   (illustrative numbers, not a finding):
   ```
   trade input (gross)                    1,000 quote units
   pool fee tier 1%          -> fee bucket   10 quote units   (1.0% of gross)
   "creator share 50%" of the bucket ->       5 quote units   (0.5% of gross)
   per-trade token tax 5% of gross ->        50 quote units   (5.0% of gross)
   ```
   "Creator receives 50%" and "creator receives 5%" describe amounts that differ by 10x here; the
   report must name the base every time (`of gross`, `of the fee bucket`, `of the protocol share`).
3. `F-REALIZED-RATE` - separate configured from realized. Read the configuration at P1 (`rpc_state`:
   tax bps, fee tier, split percentages, recipients). Then, over a stated block range, compute the
   realized rate from receipts: sum of fee transfers (or `Collect` amounts) divided by the sum of trade
   volumes from `Swap` events in the same range, per asset. Report both; a difference is a finding
   about a setter call, an exemption, or a bucket confusion, and each must be resolved by the receipt
   that caused it.
4. `F-CLAIM-AUTH` - for each escrow, resolve who can move the money out and to where:
   - claim only by a privileged caller to an arbitrary `to` argument (custody authority - name the
     holder and its type from `A-ADMIN`);
   - claim by anyone, but only to a fixed recipient read from storage (custody follows the recipient
     setter);
   - claim by the position owner (v3 `collect` requires the NFT owner or an approved operator; a
     locked position's locker decides - `B-PRINCIPAL`, `B-LOCKER-AUTH`);
   - automatic forwarding inside the transfer path (the token itself sends the tax; no claim step).
   Read `owner()`, roles, `feeRecipient()`/`treasury()`/`marketingWallet()` and their setters at P1
   with `rpc_probe.py --call`; preserve raw returns:
   ```
   python3 <skill-root>/scripts/rpc_probe.py --address 0x<fee-contract> --chain-id N --block <P1 block> \
       --call "owner()" --call "feeRecipient()" --cache rpc-cache.json --out packet-F-claim.json
   ```
5. `F-RECIPIENT-CONFIG` - for every recipient: runtime status (`contract`/`eoa`), owner or proxy status
   when a contract, whether it is a splitter (follow one hop to its payees and their shares), and who
   can change the recipient (setter holder, timelock). Add each as a scope address with role
   `fee_recipient` or `treasury` and provenance (`rpc_derived` from a read; `event_derived` from a
   `Transfer`; `explorer_label` only as corroboration).
6. `F-SUBSEQUENT-USE` - classify every outflow from each recipient over the range, by receipt: swap
   (`Swap` event; output asset named), buyback (a swap whose output is the token; where did the tokens
   go next), burn (transfer to a burn address or `burn` call), distribution to a reward contract
   (surface G), bridge leg (step 8), transfer to a labeled exchange deposit (label is corroboration;
   the receipt proves only the transfer), gas (native only), other transfer. Native value that moved
   inside a contract call is invisible to `Transfer` logs: use `trace_transaction` /
   `debug_traceTransaction` for internal transfers, and record the limitation if no trace API exists.
7. `F-TREASURY` - reconcile each material asset per recipient with `scripts/reconcile.py`. One flows
   file per (chain, recipient, asset). The format the tool accepts (one row of each direction is shown
   together for the shape; a real file holds one asset, and `gas` rows appear only in the native file):
   ```json
   {
     "asset": {"chain_id": 1, "address": "0x<token or WETH> | native", "symbol": "as resolved"},
     "opening": {"balance": "123450000000000000000", "block": 20000000},
     "closing": {"balance": "98700000000000000000", "block": 20100000},
     "rows": [
       {"tx": "0x..", "status": "success", "direction": "in", "amount": "50000000000000000000",
        "block": 20000101, "counterparty": "0x<pool or payer>", "note": "Collect amount1 -> forwarded"},
       {"tx": "0x..", "status": "success", "direction": "out", "amount": "70000000000000000000",
        "block": 20050000, "counterparty": "0x<router>", "note": "Swap to quote; output in WETH file"},
       {"tx": "0x..", "status": "reverted", "direction": "out", "amount": "1000000000000000000",
        "block": 20050001, "counterparty": "0x..", "note": "excluded by tool: reverted"},
       {"tx": "0x..", "status": "success", "direction": "transform_out", "amount": "2000000000000000000",
        "block": 20060000, "counterparty": "0x<WETH>", "note": "wrap: paired with transform_in in WETH file"},
       {"tx": "0x..", "status": "success", "direction": "gas", "amount": "310000000000000",
        "block": 20060000, "counterparty": "0x0000000000000000000000000000000000000000", "note": "native only"},
       {"tx": "0x..", "status": "success", "direction": "adjustment", "amount": "15000000000000000",
        "block": 20070000, "counterparty": "0x<token>", "note": "explained delta (e.g. fee-on-transfer haircut), receipt cited"}
     ]
   }
   ```
   The `asset` object is a descriptor for the reader: its `chain_id` is the integer read from
   `eth_chainId` and pinned (the `1` above is a placeholder - verify at use time via RPC), and the
   block numbers are placeholders. Run it per file; the exit code is the tolerance test:
   ```
   python3 <skill-root>/scripts/reconcile.py --flows flows-treasury-WETH.json --tolerance 1000000000000000 --json
   ```
   Rules the tool enforces and the analyst must respect when building rows: amounts are integer base
   units as strings; `reverted` rows are excluded from every sum (a reverted tx moved nothing; list it
   only so the reader sees it was considered); `gas` rows exist only in the native-asset file;
   `transform_out`/`transform_in` describe a wrap or unwrap (ETH -> WETH is `transform_out` in the
   native file and `transform_in` in the WETH file, same tx) so the same value is never counted as both
   spending and income; `adjustment` rows carry an explained delta with the receipt that explains it;
   the tool reports the unexplained delta and whether it exceeds `--tolerance`, and exits 1 if it does.
   Opening and closing balances are `eth_getBalance` (native) or `balanceOf` (`eth_call`) at the two
   stated blocks, both preserved as `rpc_state` evidence. Burns are `out` rows with the burn address as
   counterparty; bridge legs are `out` rows on the source ledger and `in` rows on the destination
   ledger (its own file, its own pin). An unexplained delta above tolerance is a finding
   (`F-TREASURY` = `finding`) until a receipt explains it; a delta within tolerance is stated as a
   bounded remainder, never as zero.
8. `F-BRIDGE-LEGS` - for every bridge outflow, match all six elements before calling the leg complete:
   source execution (receipt, status success, amount and asset burned/locked), the message or transfer
   identifier emitted by the bridge contract (nonce, message id, deposit id - from the decoded log),
   the destination chain (pin P2 with its own `eth_chainId` verification,
   `references/chains/chain-verification.md`), the recipient on the destination, the delivered amount
   (source amount minus bridge fee; state the fee), and destination evidence (the mint/release receipt
   or log carrying the same identifier). A leg missing any element stays "unmatched" and is part of
   the bounded unexplained remainder on the destination ledger.
9. `F-COMMINGLING` - stop exact attribution at the first address that receives flows from unrelated
   sources (an exchange hot wallet or deposit address, a shared splitter, a pooled contract, a mixer).
   Report the amount that reached the commingling point and the tx hashes; do not follow further. An
   exchange deposit proves a transfer to an address with an exchange label (corroboration only); it
   proves neither a sale, nor a fiat withdrawal, nor who the final beneficiary is.
10. Write the rows. Configuration findings bind to P1; flow findings bind to tx hashes and are
    `is_historical: true`; each reconciliation is one evidence row (`artifact` = the flows file and the
    tool's JSON output, with `artifact_sha256`) and one finding row stating opening, inflows,
    adjustments, outflows, closing and the unexplained remainder with its tolerance.

### Verify-at-use table

| Item | Verify at use time by |
|---|---|
| Wrapped native token address (WETH-equivalent) per chain | `Deposit`/`Withdrawal` logs in the wrap tx's receipt and `eth_getCode`; never from a list |
| Bridge contract addresses and their identifier event signatures | `to` and topic0 in the source receipt; signature from corresponding source only |
| Destination chain id | `eth_chainId` on the destination RPC, pinned as P2 (`references/chains/chain-verification.md`) |
| Router/aggregator addresses in subsequent-use rows | `to` of the proven swap receipts |
| v3 protocol-fee semantics and v4 protocol-fee controller | `slot0()`/`protocolFees` reads at P1 and the factory/manager owner read; platform reference files |
| Exchange deposit labels | explorer label recorded as `explorer_label` provenance, corroboration only |

## Checks

| check_id | Proposition tested | Minimum evidence | Preferred evidence type | Stale condition |
|---|---|---|---|---|
| F-FEES | Every fee source is mapped: basis, denomination, split, escrow, claim authority, recipients, configurability, subsequent use | config reads at P1 for each source + one realized-flow receipt per source | `rpc_state`, `receipt`, `log_decoded` | any fee/recipient setter call, upgrade of a fee contract |
| F-TREASURY | Each material asset of each fee recipient/treasury reconciles within the stated tolerance over the stated range | flows file + `reconcile.py` output + opening/closing reads | `rpc_state`, `receipt`, `log_decoded`, `trace` | any tx touching the recipient after the closing block |
| F-BASIS-VS-BUCKET | Every percentage in the report names its base (gross vs fee bucket vs share) | one computed example from a real receipt in range | `receipt`, `log_decoded` | fee tier or split change |
| F-CLAIM-AUTH | Who can move each escrow, and to a fixed or arbitrary destination | claim function guard + `to` parameter semantics + holder reads at P1 | `bytecode`, `rpc_state`, `source_verified` | role/owner change, upgrade |
| F-BRIDGE-LEGS | Each bridge leg is matched end to end (source receipt, identifier, destination pin, recipient, delivered amount, destination receipt) | all six elements per leg | `receipt`, `log_decoded` (both chains) | none for matched legs; unmatched legs resolve when destination evidence appears |
| F-COMMINGLING | Attribution stops at commingling and the report says so, with the amount reaching it | the receiving tx(s) + the reason the address is commingled | `receipt`, `explorer` (label, corroboration) | never |
| F-REALIZED-RATE | Realized fee rate over the range equals the configured rate, or the difference is explained by receipts | Swap volume sum + fee sum per asset in range | `log_decoded`, `receipt` | setter call, exemption change |
| F-RECIPIENT-CONFIG | Each recipient's type, owner/proxy status, splitter payees and recipient setter are resolved | reads at P1 per recipient | `rpc_state`, `rpc_storage` | setter call, ownership transfer |
| F-SUBSEQUENT-USE | Outflows are classified by receipt (swap, buyback, burn, distribution, bridge, deposit, gas, other) | one receipt per outflow above threshold; traces for internal native value | `receipt`, `trace`, `log_decoded` | new outflows after the closing block |

Statuses follow the manifest rules: `pass` and `finding` need evidence ids; `unknown`/`skipped` need a
reason with the limitation id when infrastructure caused it. `unknown` is never a pass.

## Common false positives and negatives

False positives (a custody or proceeds problem that is not one):
- "Creator takes 50%" read as 50% of gross when it is 50% of a 1% fee bucket (step 2).
- A treasury balance that fell because of a wrap (ETH -> WETH) or a move to a splitter it controls,
  reported as spending; the transform pair and the splitter hop explain it.
- A reverted withdrawal counted as an outflow.
- An exchange-labeled deposit described as a sale, a cash-out, or an identified beneficiary.
- A configured rate at P1 projected backwards ("holders always paid 5%") when the setter changed it
  mid-range; the realized rate over the range is the historical fact.
- Gas subtracted from an ERC-20 ledger; gas is native only.

False negatives (a custody or proceeds fact missed):
- Fees escrowed inside the pool as `tokensOwed`, or inside a hook, never appearing in any wallet
  balance until collected; the claim authority over that escrow is the custody question.
- Internal native transfers (a contract paying out via CALL) invisible to `Transfer` logs; without a
  trace API the native ledger is `partial` - record the limitation, do not report the ledger as closed.
- A recipient setter that lives in a controller contract rather than the token (`A-CONTROLLER`).
- A "fixed recipient" that is a proxy whose implementation the same key can replace.
- Bridge legs matched on amount alone (two legs of the same size) instead of the identifier.
- Fee-on-transfer haircuts on inflows: the amount received differs from the amount sent; use the
  `Transfer` log at the recipient, and an `adjustment` row when the token itself burns or redirects.
- A second bridge or an aggregator route that forwards inside the same tx to a different final
  recipient; read every `Transfer` in the receipt, not the first.

## Escalation triggers to deep tracks

- Any unexplained delta above tolerance, more than one recipient hop, any bridge leg, or any question
  of the form "where did the fees go": `references/deep-tracks/proceeds-reconciliation.md` (fee-wallet
  and cross-chain reconciliation with a stopping condition at commingling).
- Fees that originate from an LP position and must be shown to be the origin of a vault's holdings:
  `references/surfaces/G-rewards-vaults-backing.md` (`G-FEE-ORIGIN`) and
  `references/deep-tracks/pool-position-history.md`.
- Proceeds traced back to launch-cohort wallets: `references/deep-tracks/launch-cohort.md`.
- A fee contract or splitter that is a proxy or has unverified source:
  `references/deep-tracks/proxy-bytecode.md`.
- A need to describe a fee recipient as an operator of the project (never an identity):
  `references/deep-tracks/operational-attribution.md`.

## How to state results

Name the base of every percentage, the range of every flow statement, and the point where
attribution stopped.

- "At P1 the token tax is 300 bps of gross on sells (`sellFee()` = 300, evidence E5), forwarded by the
  transfer path to 0x.. (`marketingWallet()`, E6), an EOA. The setter `setSellFee(uint256)` is guarded
  by `owner()` = 0x.. with a ceiling of 1000 bps in the corresponding source (E7). Realized over blocks
  N..M: 2.98% of Swap volume (E8), consistent with the configuration."
- "The v3 position's fees are collectable only by the locker 0x.. (owner of NFT #123 at P1); the
  locker's `collect` sends to a fixed `beneficiary()` = 0x.. and the beneficiary setter is held by a
  2-of-3 multisig (E9-E11). Custody of accrued fees follows that multisig; this does not extend to LP
  principal, which is surface B."
- "WETH ledger for treasury 0x.. over blocks N..M (P1): opening 123.45, inflows 50.00 (12 receipts),
  explained adjustments 0, outflows 70.00 (3 swaps, 1 bridge leg), closing 98.70, unexplained
  remainder 4.75 above tolerance 0.001 - `F-TREASURY` is `finding` until the remainder is explained.
  The bridge leg (tx 0x.., id 4412) is matched on chain P2 to recipient 0x.. for 19.98 (E15)."
- "Proceeds of 12.0 quote units reached 0x.., an address that receives deposits from many unrelated
  senders (explorer label: exchange deposit, corroboration only). Attribution stops here. No sale,
  fiat withdrawal or beneficiary is established."
- "Unknown because historical state was unavailable: no trace API on the RPC used (limitation L4,
  `api_unavailable`); internal native transfers from 0x.. are not covered, so the native ledger is
  `partial` and `F-TREASURY` is `unknown` for the native asset."
- Never: "the team profited X"; "fees go to holders" without the claim path; "50% to creator"
  without the base; "funds were cashed out" from an exchange label; "reconciled" with an unstated
  tolerance.
