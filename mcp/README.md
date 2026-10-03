# operant-mcp

MCP server for the [OPERANT](https://github.com/saagpatel/operant) AI operating-agent calibration benchmark. Read-only and stateless: baked corpus, zero runtime egress.

> **Research-integrity boundary:** named-model rows are retained calculation
> views, not durable performance claims. Historical dispatch freshness,
> served-model identity, and as-run corpus/protocol identity remain unknown.
> The server exposes this status directly and does not support model ranking,
> equivalence, or significance claims.

## What is OPERANT?

OPERANT measures whether an AI operating-agent correctly discriminates between cases that require withholding execution (guard_warranted) and cases where proceeding is correct (benign_open). The headline metric is OCS (Operational Calibration Score) = TPR - FPR (Youden's J). Axes: adversarial refusal calibration, sanctioned-path adherence, orchestration judgment, and escalation/reroute.

## Install

**stdio (local, via npx):**

```bash
npx saagar-operant-mcp
```

**Remote (streamable HTTP, no install):**

```
https://operant.saagarpatel.dev/mcp
```

**Claude Desktop / Claude Code:**

```json
{
  "mcpServers": {
    "operant": {
      "command": "npx",
      "args": ["saagar-operant-mcp"]
    }
  }
}
```

## Tools

| Tool | Description |
|---|---|
| `get_results` | Retained calculation profiles plus freshness, claim status, claims at risk, and the evidence boundary. |
| `compare_models` | Side-by-side inspection with `comparison_status=NOT_DURABLE`; not a performance ranking. |
| `get_methodology` | Benchmark design: axes, OCS formula, decision labels, scoring blocks. |
| `list_cases` | Case metadata (no task prompts): id, axis, tier, grounding. Filter by axis or get all 37. |
| `get_case` | Full case: task prompts, expected decisions, grounding rationale, bypass patterns. |

All tools are `readOnlyHint: true`. None takes a URL or filesystem path.

## Resources

| URI | Description |
|---|---|
| `operant://results` | Calibration profiles JSON |
| `operant://methodology` | Benchmark design JSON |

## Prompt

| Name | Description |
|---|---|
| `score_my_agent` | Ready prompt explaining how to run OPERANT against your own agent and read OCS. |

## Running OPERANT against your agent

See the `score_my_agent` prompt or the root README's
[zero-spend heuristic demo](../README.md#try-it-in-10-seconds). Live agent
dispatch requires separate provider access and can incur spend.

For local development, use [Local verification](../docs/verification.md): it
covers Node/npm prerequisites, focused tests, typecheck/build and local stdio
verification without contacting the deployed endpoint.

## License

MIT
