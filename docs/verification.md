# Local verification

Use uv 0.13.9 or later in the 0.13 series and the Python and Node.js versions in
[`.python-version`](../.python-version) and [`.node-version`](../.node-version).
From the repository root, run:

```bash
make check
```

`make check` uses `uv sync --frozen` and `npm ci`; it does not upgrade the
locked dependencies. It runs the Python selftest and unittest suite, evaluation
split checks, Ruff lint, MCP generated-file checks, typecheck, MCP tests,
CLI build and probe, package parity, and web verifier tests. It runs live manifest authentication
when `MCP_TRUSTED_PUBLISHER_PUB` points to an independently provisioned public
key; otherwise it reports that authentication was not run. The explicit
`npm run verify:manifest` command remains fail-closed when the pin is missing.
Before an authenticated live release, provision the pin independently and run
`make verify-manifest`.
The check does not dispatch agents or contact a deployed MCP endpoint.

For a focused Python fixture check, use:

```bash
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python selftest.py
```

The environment variable selects a checked-in example contract and avoids the
personal `~/.claude/CLAUDE.md` fallback. `run_suite.py --dry-run` is a separate
personal-input wiring check and is not part of routine fixture verification.

The generated MCP scripts run from paths containing spaces as covered by the
Vitest suite. If a generator changes a checked-in artifact, review that diff as
a public-data change rather than discarding it. Manifest verification requires
an independently provisioned publisher public key; the adjacent bundle key is
not a trust anchor. Live provider calls, public exports, deployment, and package
publication are separate operations.

The current Ruff format baseline is not clean, so
`make check` does not run the formatter until that migration is reviewed.
