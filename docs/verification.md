# Local verification

Run commands from the repository root unless a step says otherwise. The
[CI workflow](../.github/workflows/ci.yml) defines the broader verification lane;
these checks use local fixtures and do not require model-provider credentials.

## Prerequisites

CI uses Python 3.12.13, Node 22.22.0 and Ruff 0.15.22. The MCP package requires
Node `>=22.22.0 <23` and declares npm 10.9.4 in
[`mcp/package.json`](../mcp/package.json). Use an isolated development environment
for tooling; install the pinned Ruff version there (`python3 -m pip install
--no-deps ruff==0.15.22`). The Python scoring and self-serve fixture checks use the
standard library. MCP dependency installation downloads locked packages; the
checks below do not dispatch agents or contact the deployed MCP endpoint.

## Python checks

For changes to the self-serve runner, start with its hermetic fixture gate:

```bash
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md python3 selftest_selfserve.py
```

For broader Python/scoring changes, follow CI:

```bash
python3 -m compileall -q operant_lab *.py
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md python3 selftest.py
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md python3 -m unittest discover -p 'test_*.py'
python3 verify_evaluation_split.py
ruff check operant_lab *.py --extend-per-file-ignores 'score_orchestration.py:E501,score_orchestration_judge.py:E501'
```

Keep the two Ruff exceptions: those files are hash-bound into public evidence.
The repo has no separate configured Python formatter or typecheck command.
The explicit bundled operator contract avoids the runner's default personal
`~/.claude/CLAUDE.md` fallback. Selftests exercise fake/local agents and temporary
outputs. A live `run_operant.py`
or `run_suite.py` invocation can dispatch paid agents; it is not a test substitute.
`run_suite.py --dry-run` avoids model calls but reads the personal
`~/.claude/CLAUDE.md` contract and does not use `OPERANT_OPERATOR_CONTRACT`; keep
that optional personal-input wiring lane separate from fixture verification.
The README's [heuristic demo](../README.md#try-it-in-10-seconds) is a zero-spend
end-to-end example, but writes report/badge files under `results/self-serve/`.

## MCP checks

Install the lockfile, then use a focused test file for the changed contract:

```bash
npm ci --ignore-scripts --prefix mcp
npm --prefix mcp test -- test/server.test.ts
```

`--ignore-scripts` skips dependency lifecycle scripts. The locked esbuild platform
package still supports the CLI build. For the full local MCP contract:

```bash
npm --prefix mcp run typecheck
npm --prefix mcp test
npm --prefix mcp run build:cli
npm --prefix mcp run verify:manifest
npm --prefix mcp run parity
```

The CLI build writes ignored `mcp/dist/`. Signature verification uses the checked-in
public key; it does not require a signing private key. Parity compares local `package.json`
and `server.json` version metadata. There is no separate configured MCP lint/format
script. For corpus/manifest changes, CI also runs `build:corpus` and `build:wellknown`
and requires no diff in `mcp/src/corpus.generated.ts` or
`mcp/src/well-known.generated.ts`; these generators overwrite those files, so run
that lane in an isolated checkout and inspect any diff rather than discarding it.

`npm --prefix mcp run probe:mcp` exercises only the local stdio CLI after
`build:cli`. Currently that script, `build:corpus` and `build:wellknown` derive filesystem paths
from URL `.pathname`, so checkout paths containing spaces can fail with `%20`
paths. Use a checkout path without spaces for those existing scripts. For a local
stdio smoke in a path containing spaces, the same installed probe library accepts
an explicit filesystem path (run this block from `mcp/` after `build:cli`):

```bash
node --input-type=module <<'JS'
import assert from 'node:assert/strict';
import { join } from 'node:path';
import { probeStdioServer } from 'saagar-mcp-kit/stdio-probe';
const { callResults } = await probeStdioServer(process.execPath,
  [join(process.cwd(), 'dist/stdio.js')], {
    serverName: 'operant-mcp',
    tools: ['compare_models', 'get_case', 'get_methodology', 'get_results', 'list_cases'],
    clientName: 'local-verification', calls: [{ name: 'get_results', arguments: {} }],
  });
const result = JSON.parse(callResults[0].content[0].text);
assert.ok(result.models.length > 0);
assert.ok(result.caveat.length > 0 && !result.caveat.includes('reliable ranking'));
assert.equal(result.results_status, 'CALCULATION_PROFILES_NOT_DURABLE_MODEL_CLAIMS');
assert.equal(result.claim_status.historical_reference_profiles.cross_model_ranking, 'NOT_DURABLE');
console.log('Local stdio probe passed.');
JS
```

## Presentation and external lanes

For changed public reports, badges or their presentation, inspect the intended
local output in a browser and check labels, evidence boundaries and rendering.
Pure documentation or nonvisual contract changes do not need browser verification.
The fixture gates establish local behavior only. Model dispatch, live HTTP probes,
manifest signing, `wrangler dev`, `deploy`, public exports and package publication
are separate actions; do not use them as routine verification or overwrite private
receipts/results to obtain a clean test run.
