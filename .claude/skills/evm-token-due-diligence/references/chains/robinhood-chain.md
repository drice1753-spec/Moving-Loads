# Chain: Robinhood Chain (research snapshot; everything verify-at-use)

Read this after `references/chains/chain-verification.md` when the requested chain id is 4663 or the user
names Robinhood Chain. It records what the research (2026-09-05) found, with sources and the research's
own confidence, so that a run on this chain knows what to verify and what to expect from its
infrastructure. Nothing here is asserted as current fact: official documentation pages and the explorer
were reachable only through search-engine snippets during research, so most items are medium confidence
and every number must be re-read from the chain or its current documentation at use time.

## What the research found

| Topic | Research claim | Source (as cited by the research) | Confidence |
|---|---|---|---|
| Stack | Public, permissionless EVM L2 built on Arbitrum Orbit / Nitro ("Arbitrum Dedicated Blockchains"), settling to Ethereum L1 with EIP-4844 blob data availability (a rollup, not AnyTrust); ArbOS 61 per L2BEAT; mainnet nodes run `offchainlabs/nitro-node` from a custom genesis | docs.robinhood.com/chain/, L2BEAT | high |
| Chain ids | mainnet 4663 (`0x1237`); testnet 46630 (`0xB626`) | docs.robinhood.com/chain/connecting/ | high |
| Public RPC | `https://rpc.mainnet.chain.robinhood.com` (shared, rate-limited, documented for wallet connectivity and testing); testnet `https://rpc.testnet.chain.robinhood.com`; a sequencer feed WebSocket reported by a third party; third-party providers (Alchemy recommended by Robinhood; QuickNode, dRPC, Chainstack, Blockdaemon, Validation Cloud, others) | docs connecting page; provider pages | high for hosts, medium for provider list |
| Explorer | `https://robinhoodchain.blockscout.com` (Blockscout; contract verification; `/api/v2`); testnet explorer and faucet under `*.testnet.chain.robinhood.com`; `robinscan.io`, `hood-chain.com` are third-party | explorer | high |
| Gas token and fees | ETH bridged from Ethereum (no chain token, no airdrop); fee = L2 execution + L1 blob data; first-come-first-served sequencer ordering, no priority gas auction | docs gas-and-fees page | high |
| Block time and finality | ~100 ms blocks with sequencer soft confirmations; hard finality when batches are posted and confirmed on Ethereum; canonical L2->L1 withdrawals subject to a 7-day challenge period | docs transaction-finality page | medium |
| Dates | public testnet 2026-02-10; public mainnet 2026-07-01 with Uniswap (v2/v3/v4/UniswapX), Chainlink, Alchemy, BitGo, 0x, LayerZero, TRM Labs as day-one partners | Robinhood newsroom | high |
| Sequencer and governance | single Robinhood-operated sequencer; L2BEAT Stage 0 (some coverage "below Stage 0"); ArbOS 61 transaction filtering: an authorized filterer can register any tx hash in a precompile and the state transition then fails it, including force-included transactions; core contracts upgradable without delay per L2BEAT; fraud proofs (BoLD) restricted to a whitelisted validator set (2 actors per L2BEAT); official docs describe an 8-signer Security Council (Robinhood 2 seats), 6-of-8 with a 7-day timelock for routine actions, 7-of-8 emergency bypass - a tension with L2BEAT's "no delay" finding | L2BEAT project page; docs governance page | medium |
| Bridge | canonical: Arbitrum bridge UI (Ethereum -> Robinhood Chain); deposits ~10 minutes; withdrawals ~7 days via the Outbox; third-party fast routes (Relay, Across; `robinbridge.xyz` is Relay-powered) | docs bridging page | medium |
| L1 protocol contracts | mainnet Rollup `0x23A19d23e89166adedbDcB432518AB01e4272D94`, SequencerInbox `0xBd0D173EEb87D57A09521c24388a12789F33ba96`, Inbox `0x1A07cc4BD17E0118BdB54D70990D2158AbAD7a2D`, Bridge `0xDf8755334ce7A73cCF6b581C02eA649AE3E864b3`, Outbox `0xf0ce991ea4A0d2400A4AB49b20ae333f6Dce3DE9` (snippet-derived) | docs protocol-contracts page | low |
| RPC method support | standard Nitro JSON-RPC (`eth_getLogs`, `eth_call`, subscriptions); `debug_*`/`trace_*` and archive access offered by third-party providers; whether the official public endpoint exposes them is undocumented | provider docs | medium |
| Stock Tokens | tokenized debt securities issued by Robinhood Assets (Jersey) Limited, deployed as standard 18-decimal ERC-20s with per-asset Chainlink feeds; addresses served by a Stock Token API; not offered to U.S. persons and restricted in several jurisdictions | docs stock-tokens pages | medium |
| Ecosystem | no first-party launchpad and no chain token; third-party launchpads: Pons (two factories; `references/platforms/pons-style-launches.md`), hood.fun (independent), Pools.trade (Uniswap Labs, 2026-08-05), TrustSwap; a 90-day gas subsidy for Robinhood Wallet users ran from mainnet launch; Uniswap v2/v3/v4 present since launch; the v4 PoolManager address on this chain was only partially captured (prefix `0x8366a39c...`) | news, docs | medium |
| Incident | ~14-minute disruption on 2026-09-04; accounts conflict (block production halt vs. only blob posting to Ethereum stalling); no funds reported lost; no root-cause post-mortem found as of 2026-09-05 | news | medium |

## Verify-at-use table

| Item | Research value | Verify at use time by |
|---|---|---|
| Chain id | 4663 (`0x1237`); testnet 46630 (`0xb626`) | `eth_chainId` on the endpoint used; `rpc_probe.py --chain-id 4663` stops on mismatch |
| Public RPC host | `rpc.mainnet.chain.robinhood.com` | the current docs connecting page; then `eth_chainId`; record the redacted endpoint; expect rate limits (`rpc_rate_limit`) |
| Explorer | `robinhoodchain.blockscout.com` | fetch a tx hash obtained from RPC and confirm the explorer's network; discovery only |
| Native currency | ETH (bridged) | a receipt's `gasUsed x effectiveGasPrice`; the wrapped-native contract by `Deposit`/`Withdrawal` behavior, not by symbol |
| Block time | ~100 ms | `eth_getBlockByNumber` timestamps over a span of blocks at P1 (many blocks share one second) |
| Finality tags | `safe`/`finalized` semantics of a Nitro-style L2 | probe `--block finalized`; if the tag is rejected fall back to `latest` or a numbered block and record which (`rpc_error` limitation) |
| Withdrawal delay | 7 days | the bridge documentation at use time; an Outbox claim receipt for a real leg |
| Sequencer / filtering / upgrade delay | single sequencer; ArbOS filtering; docs vs L2BEAT tension | current L2BEAT page and docs governance page; record both statements with dates as `manual_note` |
| L1 contract addresses | table above (low) | the docs protocol-contracts page at use time; `eth_getCode` on Ethereum at an Ethereum pin |
| DEX infrastructure (v2/v3 factories, v4 PoolManager/PositionManager/StateView/Quoter) | present per launch announcements; addresses not captured | the Uniswap deployments page, then `PoolCreated`/`Initialize` receipts from the target's own logs; `references/platforms/uniswap-v3.md`, `uniswap-v4.md` |
| Launch platforms | Pons V1/V2 factory addresses in `references/platforms/pons-style-launches.md`; others unverified | the emitter of the launch event in the target's launch receipt (the tx `to` may be a forwarder) and that emitter's code hash |
| Stock Token contracts and feeds | served by an API | the API response is `api`-class discovery; confirm each contract by code and a Chainlink `AggregatorV3Interface` read at P1 |
| Trace / archive availability | third-party providers | capability probes (`references/chains/chain-verification.md` section 4) on the endpoint actually used |

## Implications for diligence on this chain

- Exit depends on the sequencer. A single operator orders transactions and, per L2BEAT, a filterer can
  cause any transaction (even a force-included one) to fail without delay. Any "sellable" conclusion on
  this chain is conditional on sequencer liveness and on the target's transactions not being filtered;
  record it under `external_dependencies` (`H-DEPS`) with the L2BEAT and docs statements dated, and say
  in the verdict that this is a chain-level dependency, not a token property. Whether the filter list is
  readable on-chain was not established: state it as unknown unless a precompile read succeeds.
- Bridge legs take time and pass through third parties. Canonical withdrawals wait ~7 days (Outbox claim
  receipts on Ethereum are the destination evidence; `F-BRIDGE-LEGS`); fast routes settle through relayer
  liquidity, which is commingling for attribution purposes (`F-COMMINGLING`). An Ethereum pin (`P2`) is
  required for any L1 leg.
- Explorer maturity. Blockscout is young on this chain: labels are sparse, "verified" source is the
  explorer's claim, and holder or pool listings are discovery only. Verified source from Blockscout is
  the right starting point for Pons-style contracts (the GitHub repository was found inconsistent), but
  correspondence with the deployed runtime must still be established (`H-SOURCE-CORRESPONDENCE`).
- Archive and traces. The public endpoint is rate-limited and its `debug_*`/`trace_*` support is
  undocumented; expect to need a third-party archive endpoint (verify its `eth_chainId` too) for
  historical pins, Transfer replay and native-value legs (a native quote asset means sells pay out in ETH
  with no `Transfer` log). Probe first; record `rpc_pruned`/`rpc_error` limitations rather than narrowing
  scope silently.
- Block cadence. At ~100 ms blocks a day is on the order of 864,000 blocks: size `eth_getLogs` windows by
  time, not by habit; second-resolution timestamps repeat across ~10 blocks, so "same-block" and
  seconds-based launch windows (snipe taxes) span several blocks - order by (block, tx index, log index).
- Finality. Soft-confirmed blocks can be reordered by the sequencer; pin to `finalized` when the endpoint
  supports it and re-verify the pin hash at the end of the run; treat the 2026-09-04 disruption as a
  reminder that liveness gaps happen and are chain dependencies, not token findings.
- Governance and upgrades. The chain's upgrade authority can change ArbOS configuration; whether a delay
  applies is disputed between sources. This is an `H-DEPS` row whenever the decision question spans more
  than the immediate pin.
- Platforms present (hedged). Uniswap v2/v3/v4 were announced as live from mainnet launch and Pons is
  documented as deployed only here; confirm every factory, PoolManager and hook from receipts before using
  the platform references. Pools.trade and hood.fun are separate platforms with their own factories -
  never infer the platform from a token's name or website.
- Stock Tokens as quote or backing assets. If a target's pool, vault or backing uses a Stock Token, the
  exit ends in an issuer-dependent instrument with jurisdictional restrictions and an oracle dependency
  (Chainlink feed): route to `references/deep-tracks/dependency-redemption.md` and rate
  `utility_redemption_rights` and `external_dependencies` on that basis.

## What the research could not establish (state as unknown until read)

- Whether the official public RPC exposes `debug_*`/`trace_*` namespaces or archive state (third-party
  providers advertise both).
- The exact current L2BEAT stage wording, the on-chain upgrade delay (docs: 7-day Security Council
  timelock; L2BEAT: none), and the number and identity of whitelisted BoLD validators.
- The root cause of the 2026-09-04 disruption (block production halt vs. blob posting delay).
- Full addresses of the Uniswap v4 PoolManager, PositionManager, StateView and quoter on this chain (only
  a prefix was captured) and of the v2/v3 factories; whether the v3 factory has the 1 bp tier enabled.
- Whether the ArbOS transaction filter list is readable through a precompile from an ordinary RPC.
- Any Stock Token contract address or feed address (served by an API that was not reachable).

## How to state results on this chain (bounded phrasing)

- "Sellable at the tested sizes under the quoted state at P1 (chain 4663, block N, hash 0x..), conditional
  on sequencer liveness and on the transaction not being filtered (chain-level dependency, E<n>; L2BEAT
  and docs statements dated <date>)."
- "No current executable removal path found at the pinned block for position #<id> held by locker 0x..;
  the hook at 0x.. has permission bits <list> (E<n>); not established: hook owner behavior after P1,
  sequencer availability."
- "Unknown because historical state was unavailable: the public endpoint returned `rpc_pruned` for block
  <old> (L<n>); the launch-window cohort is `partial`; no third-party archive endpoint was supplied."
- "Bridge leg <k>: source receipt on chain 4663 (tx 0x..) matched to an Outbox claim on chain 1 (tx 0x..,
  pin P2); delivered amount X; the 7-day challenge period had elapsed at P2."
- Never: "the chain is fast so exits are instant"; "Robinhood-backed, therefore reliable"; "the token is on
  a regulated chain" as a token property.

## Common mistakes on this chain

- Treating `latest` as final: a soft-confirmed block on a single-sequencer L2 is not a settled state; pin
  `finalized` when supported and record the reorg check.
- Sizing `eth_getLogs` windows in blocks as if blocks were 12 seconds apart; a "1,000-block launch window"
  here is under two minutes.
- Reading a seconds-based launch window (snipe tax, cooldown) as one block; several blocks share a
  timestamp.
- Labeling a launched token as "Pons" or "Pools.trade" from its website; the factory in the launch receipt
  is the only platform identity, and hood.fun is not Robinhood-affiliated.
- Reporting a sale into a Stock Token pool as an exit to a liquid asset without the issuer and oracle
  dependency rows.
- Using an Ethereum pin for L1 bridge contracts without its own `P2`, or citing the low-confidence L1
  addresses above without `eth_getCode` at that pin.
- Presenting the 2026-09-04 disruption or the sequencer model as a finding about the token; they are
  `H-DEPS` rows and coverage context.

## Onboarding steps for this (or any unfamiliar) chain

1. `rpc_probe.py --chain-id 4663 --block finalized` against the endpoint you intend to use; stop on
   `CHAIN_MISMATCH` (exit 3); if `finalized` is rejected, re-run with `latest` or a numbered block and
   record which tag the pin used (`rpc_error` limitation; `chain-verification.md` section 3).
2. Record `web3_clientVersion`; confirm a Nitro-style client; note in `known_limitations` that finality
   semantics, filtering and single-sequencer dependency were taken from documentation dated at use time.
3. Run the capability probes (state at pin, archive at an older block, `eth_getLogs` window, traces,
   block receipts, rate limits) and write the limitation rows before any discovery search.
4. Confirm the native currency and the wrapped-native contract by behavior; identify the quote asset of
   every candidate pool by address and class.
5. Confirm the explorer serves chain 4663 with a tx hash from RPC; use it for candidate discovery and
   source downloads only.
6. Find DEX and launch infrastructure from the target's own receipts (`PairCreated`/`PoolCreated`/
   `Initialize`, launch events), then cross-check against the DEX and platform documentation; load
   `references/platforms/uniswap-v3.md`, `uniswap-v4.md` and `pons-style-launches.md` as applicable.
7. Size log windows to the block time and order launch-window events by block and index.
8. Add an Ethereum pin (`P2`) for any bridge leg or L1 contract read; match legs end to end.
9. Write the chain-level dependencies (sequencer, filtering, upgrade authority, bridge delay) into the
   packet and into `H-DEPS`; freeze the packet; re-verify the pin hash when the run ends.
