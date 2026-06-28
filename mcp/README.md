# operant-mcp

MCP server for the [OPERANT](https://github.com/saagpatel/operant) AI operating-agent calibration benchmark. Read-only and stateless: baked corpus, zero runtime egress.

## What is OPERANT?

OPERANT measures whether an AI operating-agent correctly discriminates between cases that require withholding execution (guard_warranted) and cases where proceeding is correct (benign_open). The headline metric is OCS (Operational Calibration Score) = TPR - FPR (Youden's J). Axes: adversarial refusal calibration, sanctioned-path adherence, orchestration judgment, and escalation/reroute.

## Install

**stdio (local, via npx):**

```bash
npx saagar-operant-mcp
```

**Remote (streamable HTTP, no install):**

```
https://operant-mcp.saagar210.workers.dev/mcp
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
| `get_results` | All 9 model calibration profiles (OCS, stdev, orchestration score). Not a flat leaderboard. |
| `compare_models` | Side-by-side comparison of two models by display_name substring. |
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

See the `score_my_agent` prompt, or run from the repo root:

```bash
python run_operant.py     # axis 1 (refusal-calibration)
python score_my_agent.py  # full calibration profile
```

## License

MIT
