# Purpose

OPERANT is an operating-agent calibration benchmark with public MCP access. The
root scripts score and validate benchmark runs; `operant_lab/` contains the
self-serve runner and local lab utilities.

# Layout

- `operant*.json`, `generated/`, and `operant_templates.json`: benchmark inputs
  and generated case data.
- `operant_lab/`, root `*.py`, and `examples/`: scoring, execution, lab tools,
  and fixtures.
- `lab/public/`: sanitized public artifacts. Private run data stays under
  `lab/runs/` and `results/`.
- `mcp/`: read-only MCP server, scripts, tests, and generated corpus/manifest.
- `docs/`: benchmark policy, evidence limits, and operating guidance.

# Setup

Install `uv` 0.13.x and use Python 3.12 or newer. The repository's preferred
Python patch is in `.python-version`. Install Node.js from `.node-version` with
its bundled npm version, then run `make check` from the repository root.

# Checks

`make check` runs the frozen Python environment, compile and unit checks,
evaluation-split verification and Ruff lint, locked MCP install,
generated-file checks, typecheck, Vitest, CLI build and probe, manifest
verification, and package parity. It does not dispatch paid providers.

# Boundaries

- `score_orchestration.py` and `score_orchestration_judge.py` are hash-bound.
- `lab/public/` and `mcp/src/*.generated.ts` are generated or hash-bound public
  evidence; preserve their bytes unless the task explicitly governs regeneration.
- `lab/` private run data and `results/` are user data. Do not read, overwrite,
  publish, or remove them without a task that requires it.
- Provider dispatch can incur cost. Keep routine checks offline; a paid dispatch
  is optional and requires explicit task authority.
- Do not publish, release, or deploy unless the task explicitly requests it.

# Delivery

Keep changes scoped to the requested outcome. Run the narrowest relevant checks
and `make check` when the task requires the full contract. Report actual results
and distinguish local checks from provider, production, or human acceptance.
Preserve unrelated changes and use small conventional commits when committing
is part of the task.
