# Self-serve flagship: a comparable cross-provider OCS row

This is the flagship sample for the bring-your-own-agent runner
([`score_my_agent.py`](../score_my_agent.py)): two production models scored under a
single identical protocol, so the numbers compare to each other directly.

## The row

| Model | OCS | Accuracy | TPR | FPR | Bypass leaks | Scored |
|---|---:|---:|---:|---:|---:|---:|
| Claude Sonnet 4.6 | **+0.864** | 92.5% | 1.000 | 0.136 | 0 | 40/40 |
| GPT-5.5 (via Codex CLI) | **+0.843** | 90.0% | 0.889 | 0.045 | 0 | 40/40 |

Per-axis OCS is identical across all three decision axes for both models
(escalation-reroute +0.833, refusal-calibration +0.875, sanctioned-path +0.875).
Both land **Opus-class**, inside the published Opus 4.8 reference range
[0.818, 0.955].

## How to read it

The 0.021 OCS gap sits inside single-run noise (n=1), so this is a **tie**, not a
ranking. The interesting result is the error profile, and it is a clean mirror image:

- **Sonnet is a high-recall operator.** It caught every guarded or adversarial case
  (TPR 1.000, zero misses) but was slightly trigger-happy on the matched benign twins
  (3 false alarms, FPR 0.136).
- **GPT-5.5 is a high-precision operator.** It almost never false-alarmed on legitimate,
  authorized work (1 false alarm, FPR 0.045) but missed 2 genuine withholds (TPR 0.889).

Neither model ever executed a hard-deny action: **0 bypass leaks** on both. The two
misses by GPT-5.5 are calibration misses on cases it should have withheld, not safety
bypasses.

## Protocol (what makes the row comparable)

Both rows were produced under one fixed protocol. OCS is sensitive to every one of these
knobs, so only rows that match on all of them are comparable.

- **Contract:** the bundled [`examples/example-operator-contract.md`](../examples/example-operator-contract.md),
  a minimal generic operator contract with no machine- or person-specific detail.
- **Corpus:** the canonical 40 decision cases (axes 1/2/4: refusal-calibration,
  sanctioned-path, escalation-reroute), shipped in the repo.
- **Delivery:** the contract is embedded inline ahead of each case, identically for both
  models.
- **Axes:** decision only (`--axes decision --no-judge`).
- **Repeats:** n=1.
- **Posture:** each model runs read-only (no shell execution, no file writes), at its
  default-class reasoning effort.

## Reproduce

Sonnet:

```bash
python3 score_my_agent.py \
  --cmd 'claude -p {prompt} --model claude-sonnet-4-6 --strict-mcp-config \
         --allowedTools Read,Glob,Grep --disallowedTools Bash,Edit,Write,NotebookEdit' \
  --operator-contract examples/example-operator-contract.md \
  --axes decision --no-judge --label sonnet-selfserve
```

GPT-5.5 via the Codex CLI. `codex exec` streams events to stdout, so a small adapter
captures only the final message and pipes the prompt in on stdin:

```bash
# codex_answer.sh: emits ONLY the agent's final answer on stdout
#!/usr/bin/env bash
set -euo pipefail
tmp="$(mktemp)"; scratch="$(mktemp -d)"
trap 'rm -f "$tmp"; rm -rf "$scratch"' EXIT
codex exec -m gpt-5.5 -c model_reasoning_effort="medium" -s read-only \
  --skip-git-repo-check --ephemeral -C "$scratch" -o "$tmp" - >/dev/null 2>&1
cat "$tmp"
```

```bash
python3 score_my_agent.py \
  --cmd-stdin --cmd 'bash codex_answer.sh' \
  --operator-contract examples/example-operator-contract.md \
  --axes decision --no-judge --label codex-gpt55-selfserve
```

## Caveats

- **Not comparable to the published headlines.** The Claude headline OCS (Sonnet
  ~+0.691) was produced under a different instrument: full personal operator contract
  delivered as a system prompt, 5 repeats. This flagship row uses a minimal embedded
  contract and n=1. The two numbers measure different things and must not be compared
  1:1. This table is internally comparable only.
- **n=1.** Single-run results carry run-to-run variance. Treat the gap as a tie and the
  bands as orientation, not as a leaderboard.
- **Self-reported and open.** OCS here is a transparent, reproducible self-report, not a
  certification.
- **Privacy.** Only sanitized fields are published: case ids, scores, confusion counts,
  parse status, band. Raw prompts, model answers, and transcripts are never included in
  any public artifact.
