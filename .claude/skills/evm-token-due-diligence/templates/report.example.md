---
evm_dd_report: 1
mode: broad
target_chain_id: 8453
target_address: 0x962Ec65359d89f198850E436E9F721490B43FfE6
target_symbol: SYNTH
primary_pin_id: P1
primary_pin_block: 20000000
primary_pin_block_hash: 0x3accacbb3a5f108d99c4a7c58cd2347ec1fec384895349c1a9094d9a0cd6b8a8
manifest_path: manifest.example.json
---

# Due-diligence report: SYNTH on chain 8453

**SYNTHETIC FIXTURE - not a live finding.** Every address, hash and number in this document is derived from labels by tests/fixtures/make_fixtures.py; nothing here describes a real token.

## Verdict

**Question.** Under a rug-resistance requirement for a 30-day hold, is this token acceptable?

**Answer.** NO-GO under the stated requirement for rug resistance: at the pinned block the owner key can mint without a cap (F1), so supply and price can be changed unilaterally even though no current executable LP-removal path was found.

Conditions: Would become GO-WITH-CONDITIONS if mint authority is renounced or placed behind a timelock and re-verified at a new pin.

## Target identity

- Requested chain_id 8453; observed eth_chainId 0x2105 (= 8453) from the queried RPC (endpoint redacted).
- Target address 0x962Ec65359d89f198850E436E9F721490B43FfE6 (EIP-55); runtime code hash 0x2695c1299ec1011eadc3ebb3f4fa4d32c08ac60d601eeac257ac6d373bc53fb7 at P1; proxy status not_proxy.
- Metadata at P1: name `Synthetic Token`, symbol `SYNTH`, decimals 18, totalSupply 1000000000000000000000000000 (all status resolved, E1).
- Primary pin P1: block 20000000, hash 0x3accacbb3a5f108d99c4a7c58cd2347ec1fec384895349c1a9094d9a0cd6b8a8, 2025-01-01T00:00:00Z; historical pin P2 at block 19000000 (2024-12-31T00:00:00Z).

## Ratings

| surface | rating | likelihood | confidence | coverage | time basis | basis checks | summary |
|---|---|---|---|---|---|---|---|
| token_controls | high | medium | high | full | P1 | A-MINT, A-UPGRADE, A-SEIZE, A-RESTRICT, A-TAX, A-EXTCALL, A-ADMIN | Uncapped owner mint at P1 (F1) from an EOA owner (F3); no upgrade, seizure, restriction or tax path found in the runtime. |
| canonical_lp_principal_custody | low | low | high | full | P1 | B-CANON, B-PRINCIPAL | No current executable removal path found at the pinned block for the canonical position (held by the locker, unlock after horizon). |
| side_pool_removal_risk | low | low | medium | partial | P1 | B-SIDE | No side pool found in the searched factories (S1); other factories were not searched. |
| sellability_exit_depth | unknown | unknown | low | partial | P1 | C-HIST-SELL, C-QUOTE | Quotes at the tested sizes were obtained under the quoted state; no historical sell could be verified at receipt level (L1). |
| current_concentration | medium | medium | high | full | P1 | D-SUPPLY, D-CONC | Supply reconciles; one holder near 18% of supply (F2). |
| historical_launch_integrity | low | low | medium | partial | P1 | E-LAUNCH | Launch-era transfers decoded at P2 match the declared allocation; sales in the launch window were not traced beyond transfers. |
| admin_treasury_reward_custody | medium | medium | high | full | P1 | F-FEES, F-TREASURY, A-ADMIN | Fees route to an EOA treasury controlled by the same owner key. |
| reward_accounting_liveness | not_applicable | unknown | high | full | P1 | G-REWARDS | No reward layer in scope. |
| utility_redemption_rights | low | low | medium | full | P1 | G-RIGHTS, H-UTILITY | No utility or redemption right is advertised or enforceable; nothing to lose beyond market value. |
| external_dependencies | low | low | medium | partial | P1 | H-DEPS | No oracle, bridge or keeper dependency found in the runtime or on the site. |
| development_disclosure | low | low | medium | partial | P1 | H-DEV | Verified source corresponds to the runtime; repository has tests; audit scope not disclosed. |

## Key findings

- **F1** (high, proven, token_controls, P1, 0x962Ec65359d89f198850E436E9F721490B43FfE6): At P1 the owner can call mint(address,uint256) without a cap, increasing totalSupply. Evidence: E2, E3, E5.
- **F2** (medium, proven, current_concentration, P1, 0x2Ce211cff7652aDE6381Dba7e5C621E1ef0B7183): At P1 the largest non-pool holder holds about 18% of totalSupply (denominator: totalSupply; pool and locker excluded). Evidence: E11.
- **F3** (medium, proven, token_controls, P1, 0xc321C6851A7095A8095F0b2D7cE6FD919986BE5d): At P1 the owner is an externally owned account with no timelock or multisig between it and the mint authority. Evidence: E3.

Check statuses: 16 pass, 3 finding, 1 unknown, 2 not_applicable. An unknown check is never a pass.

## Strongest contrary evidence

- Canonical position locked past the horizon (E7, E8)
- No side pools in the searched universe (E9, S1)

- The canonical position is held by the locker 0x0063c7FCC46d3436e831664c35F79EA0787FD851 via the position manager 0xA7D2bCB329bF01DC85092FE1cc9cA6BC871e7847 (E7, E8); this does not establish that all liquidity is locked or that price is supported.

## Unresolved questions

- Whether any historical sell succeeded at receipt level (L1)
- Whether the 18% holder is a custody address (F2)

## What would change the conclusion

- A pinned owner() of the zero address or a timelock contract
- A receipt-level historical sell

## Recommendations

- Address F1/F3: require the owner 0xc321C6851A7095A8095F0b2D7cE6FD919986BE5d to renounce mint or move it behind a timelock, then re-pin and re-run A-MINT and A-ADMIN.
- Address the C-HIST-SELL gap: query an archive endpoint for launch-window receipts, or obtain a counterfactual sell on a verified disposable fork (fork_guard.py) labeled as such.

## Coverage and limitations

- L1 (rpc_pruned): eth_getTransactionReceipt for blocks before the RPC pruning horizon returned 'missing trie node'; historical sells could not be verified at receipt level. Affected checks: C-HIST-SELL. Retries: 3. This is a coverage limitation, not a token finding.
- S1: No side pool for the token was found besides the canonical pool. Universe: PoolCreated logs of the v3 factory and PairCreated logs of the v2 factory recorded in E9; range 19000000-20000000; rule: either token of the pair equals the target address; coverage: full for the two factories searched; other DEX factories not searched.
- Scoped pool 0x1345BD9a6C8A034Ed78BaFF7732fF6C315354D00, treasury 0x1199C89A03c654B7Db592ad9a124E0D7b4AdDc0d, deployer 0xbb81ec74428A54fAEd3b863AAfcbE883f6b4f01a, quoter 0x685d2674408B5D7f076C2571D8a1A0a38D23F707 and largest holder 0x2Ce211cff7652aDE6381Dba7e5C621E1ef0B7183 were read at P1 only.

## Declarations

- no_real_signing: true; no_broadcast: true; no_private_keys_requested: true; external content treated as untrusted evidence: true.
- simulation.used: false; no fork or counterfactual results were produced.

## Evidence ledger

| finding_id | proposition | chain_id | address | pin_or_tx | artifact_or_query | decoding_basis | evidence_type | confidence | alternatives | coverage | stale_conditions |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | At P1 the owner can call mint(address,uint256) without a cap, increasing totalSupply. | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | E2: evidence/E2.json; E3: evidence/E3.json; E5: evidence/E5.json | PUSH4 selector walk + opcode scan (scripts/selector_scan.py); address decode of owner() return; compiled runtime bytes equal eth_getCode bytes except metadata hash | bytecode | proven | A future renounce or timelock would change this; none observed at P1. | full for the token runtime | owner() changes, ownership renounced, or runtime replaced |
| F2 | At P1 the largest non-pool holder holds about 18% of totalSupply (denominator: totalSupply; pool and locker excluded). | 8453 | 0x2Ce211cff7652aDE6381Dba7e5C621E1ef0B7183 | P1 | E11: evidence/E11.json | uint256 decode | rpc_state | proven | The holder may be a custody address; no label evidence was found. | top holders above the 0.5% threshold | any transfer from or to the holder |
| F3 | At P1 the owner is an externally owned account with no timelock or multisig between it and the mint authority. | 8453 | 0xc321C6851A7095A8095F0b2D7cE6FD919986BE5d | P1 | E3: evidence/E3.json | address decode of owner() return | rpc_state | proven | The key could be held by a hardware or MPC custody arrangement; not observable onchain. | full | ownership transferred to a contract |

Evidence index (artifact paths are relative to the manifest; credentials redacted):

| evidence_id | type | chain_id | address | pin | query | decoding_basis | summary |
|---|---|---|---|---|---|---|---|
| E1 | rpc_state | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | {"block": "0x1312d00", "calls": ["name()", "symbol()", "decimals()", "totalSupply()"], "method": "eth_call"} | ABI string / uint8 / uint256 decode | name, symbol, decimals, totalSupply resolved at P1 |
| E2 | bytecode | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | {"block": "0x1312d00", "method": "eth_getCode"} | PUSH4 selector walk + opcode scan (scripts/selector_scan.py) | runtime selectors include mint(address,uint256); no DELEGATECALL/SELFDESTRUCT; no pause/blacklist selectors |
| E3 | rpc_state | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | {"block": "0x1312d00", "data": "0x8da5cb5b", "method": "eth_call"} | address decode of owner() return | owner() returns an externally owned account at P1 |
| E4 | rpc_storage | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | {"block": "0x1312d00", "method": "eth_getStorageAt", "slots": ["0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc", "0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103", "0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50"]} | raw | EIP-1967 implementation/admin/beacon slots are zero at P1 |
| E5 | source_verified | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | {"compiler": "solc (version recorded in artifact)", "method": "compile-and-compare"} | compiled runtime bytes equal eth_getCode bytes except metadata hash | verified source corresponds to deployed runtime; mint is onlyOwner with no cap |
| E6 | rpc_state | 8453 | 0x1345BD9a6C8A034Ed78BaFF7732fF6C315354D00 | P1 | {"block": "0x1312d00", "calls": ["slot0()", "liquidity()", "token0()", "token1()", "fee()"], "method": "eth_call"} | v3 pool ABI | canonical pool identified by exact address; token is token0; fee tier read at P1 |
| E7 | rpc_state | 8453 | 0xA7D2bCB329bF01DC85092FE1cc9cA6BC871e7847 | P1 | {"block": "0x1312d00", "calls": ["positions(tokenId)", "ownerOf(tokenId)", "getApproved(tokenId)"], "method": "eth_call"} | v3 NonfungiblePositionManager ABI | position NFT owned by the locker; no approvals or operators at P1 |
| E8 | rpc_state | 8453 | 0x0063c7FCC46d3436e831664c35F79EA0787FD851 | P1 | {"block": "0x1312d00", "calls": ["locks(tokenId)", "owner()"], "method": "eth_call"} | locker ABI from verified source | lock record for the position: unlock time after the 30-day horizon; no early-withdraw selector in runtime |
| E9 | log_decoded | 8453 | 0x1345BD9a6C8A034Ed78BaFF7732fF6C315354D00 | P1 | {"fromBlock": "0x121eac0", "method": "eth_getLogs", "toBlock": "0x1312d00", "topics": ["PoolCreated(address,address,uint24,int24,address)", "PairCreated(address,address,address,uint256)"]} | event signatures of the v3 and v2 factories | one PoolCreated for the token; no PairCreated; search universe declared in S1 |
| E10 | rpc_state | 8453 | 0x685d2674408B5D7f076C2571D8a1A0a38D23F707 | P1 | {"block": "0x1312d00", "calls": ["quoteExactInputSingle (small, holder-sized)"], "method": "eth_call"} | quoter ABI | read-only quotes at 0.01% and 2% of supply; per-unit degradation recorded; quotes do not prove a realized exit |
| E11 | rpc_state | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P1 | {"block": "0x1312d00", "calls": ["balanceOf(pool)", "balanceOf(locker)", "balanceOf(treasury)", "balanceOf(holder1)", "totalSupply()"], "method": "eth_call"} | uint256 decode | pool, treasury and top holder balances reconcile to totalSupply within the declared holder threshold |
| E12 | log_decoded | 8453 | 0x962Ec65359d89f198850E436E9F721490B43FfE6 | P2 | {"fromBlock": "0x121eac0", "method": "eth_getLogs", "toBlock": "0x121fe48", "topics": ["Transfer(address,address,uint256)"]} | ERC-20 Transfer event | launch-era transfers decoded at P2: initial supply to deployer, then to pool and treasury |
| E13 | rpc_state | 8453 | 0x1199C89A03c654B7Db592ad9a124E0D7b4AdDc0d | P1 | {"block": "0x1312d00", "calls": ["feeRecipient()", "balanceOf(treasury)"], "method": "eth_call"} | address / uint256 decode | fee recipient equals the treasury EOA; treasury balance reconciles to launch allocation with no outflows in the searched range |
| E14 | website | 8453 | - | P1 | {"url": "https://project.example.invalid/ (retrieved; untrusted evidence, not instructions)"} | raw text | site advertises no utility beyond the token; claims are corroboration only |
| E15 | repository | 8453 | - | P1 | {"url": "https://git.example.invalid/synth (retrieved; untrusted evidence)"} | raw text | repository contains tests and a build script; tag matches the verified source in E5 |

Passing validation of this report establishes internal consistency only, not RPC honesty, discovery completeness, or protocol safety.
