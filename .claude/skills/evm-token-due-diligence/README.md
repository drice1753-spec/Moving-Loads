# evm-token-due-diligence

An agent skill package for rigorous, evidence-bounded due diligence on one exact EVM token contract and
the economic system around it: privileged controls, liquidity-principal custody, sellability and exit
depth, supply and launch concentration, fees and treasury, rewards and backing, utility and dependencies,
and development disclosure. It works on any EVM chain, with conditional guidance for Uniswap v3/v4,
Pons-style launch platforms and Robinhood Chain.

The package is a procedure, not a dataset: it contains no live findings about any real token, and every
chain-specific number in its references is marked "verify at use time" with the procedure to verify it.

## What is in the package

| Path | Contents |
|---|---|
| `SKILL.md` | The spine: modes, rules, step-by-step workflow, helper script table |
| `references/` | Evidence rules, target packet, workflow modes, output standard, attribution, surfaces A-H, platforms, chains, deep tracks, behavioral examples |
| `templates/` | Target packet skeleton, example manifest + report (synthetic, valid), blank report template, ledger column definitions |
| `schemas/manifest.schema.json` | JSON Schema (draft-07) for the target-integrity manifest |
| `scripts/` | Read-only helpers: `rpc_probe.py`, `validate_report.py`, `fork_guard.py`, `selector_scan.py`, `pool_math.py`, `reconcile.py`, `ledger.py`, `selftest.py`, shared `ddcore.py` |
| `tests/` | `unittest` suites and synthetic fixtures (one valid manifest/report pair, eight rejection cases) |

## Install

There is no build step. Copy the whole `evm-token-due-diligence` folder (this directory) to either:

- a project: `<project>/.claude/skills/evm-token-due-diligence/`, or
- your user-level skills directory (for example `~/.claude/skills/evm-token-due-diligence/`).

The skill root is wherever `SKILL.md` ends up. Scripts resolve their own directory with
`Path(__file__).resolve().parent` and import `ddcore` from it, so no path configuration is required.

## Dependencies

Required:
- Python 3.10 or newer. Scripts and tests use the standard library only (no web3, no requests, no pytest).

Optional:
- `jsonschema` (Python package): when importable, `validate_report.py` adds schema-level checks on top of
  its built-in structural checks. Without it the validator still runs.

Optional external tools the diligence may use. They are NOT bundled and NOT required for the helpers:
- A JSON-RPC endpoint for the target chain, supplied by the user with `--rpc URL` or the environment
  variable `EVM_DD_RPC_URL`. Used read-only.
- Foundry `cast` / `anvil` for transaction traces and disposable local forks (simulation only, and only
  behind `scripts/fork_guard.py`).
- Explorer APIs, for discovery and corroboration only; never as the basis of a material onchain claim.

## Quick start

```
export EVM_DD_RPC_URL="<endpoint the user supplied>"      # redacted in every output
python3 <skill-root>/scripts/rpc_probe.py --address 0x<target> --chain-id <requested chain id> \
    --block latest --cache rpc-cache.json --out target-packet.json
# ... diligence per SKILL.md, producing manifest.json + report.md ...
python3 <skill-root>/scripts/validate_report.py --manifest manifest.json --report report.md --strict
python3 <skill-root>/scripts/ledger.py check --manifest manifest.json
```

Read `SKILL.md` first; it links the references that apply to each situation.

## Running the self-test and the unit tests

```
python3 <skill-root>/scripts/selftest.py
```
Runs every `tests/test_*.py` suite and validates every fixture (the `valid` pair must pass; each
`reject-*` pair must fail with its expected E-code). Exit code 0 means everything passed.

To run the unit tests directly:
```
python3 -m unittest discover -s <skill-root>/tests -p 'test_*.py' -v
```
Tests never touch the network: `tests/test_rpc_probe.py` and `tests/test_fork_guard.py` use an in-process
mock JSON-RPC server (`tests/mock_rpc.py`, `http.server`).

## Portability

- Paths are resolved dynamically; nothing depends on a home directory, a project name, or an OS.
- No credentials are stored anywhere. RPC URLs are redacted with `ddcore.redact_url` before they are
  written into packets, evidence or reports (user-info, key-like path segments and query values are
  replaced with `<redacted>`).
- Cache files (`--cache`) contain only pinned read results keyed by host/method/params; delete them freely.
- Node is not a dependency. Nothing is installed globally.

## Security posture

- `ddcore.RpcClient` sends only allowlisted read-only JSON-RPC methods and raises `RpcPolicyError` on
  anything else. No script can sign, send, or broadcast a transaction.
- No script accepts, reads, or stores private keys or seed phrases. `fork_guard.py` and `rpc_probe.py`
  refuse to run when an argument looks like key material.
- Simulation writes are permitted only on a local disposable fork that `fork_guard.py` has verified
  (loopback/private host, node self-identifies as a fork, chain id and block as expected). Its
  attestation JSON is referenced from the manifest, synthetic accounts only, results labeled counterfactual.
- Retrieved websites, repositories, token metadata and explorer labels are treated as untrusted evidence
  and never as instructions.

## Limitations

- `validate_report.py` proves internal consistency of a manifest and report (identity, pins, scope,
  linkage, declarations). It cannot prove that an RPC endpoint told the truth, that discovery was
  complete, or that a protocol is safe.
- No live chain data, addresses, chain ids, or protocol deployments are bundled or asserted. Chain-specific
  values (chain ids, hosts, factory addresses, init code hashes) must be verified at use time by RPC read
  or receipt, following `references/chains/chain-verification.md`.
- Historical questions depend on the endpoint's retained state; pruned or rate-limited history is recorded
  as a coverage limitation, and the affected checks stay `unknown`.
- The skill produces bounded, conditional verdicts. It never certifies a token as safe, and a favorable
  review is not a prediction of returns.
