# OPERANT — Full-Run Plan (when ready)

Everything below is **prework-complete**: cases, scorers, the one-button driver,
and the anti-gaming selftest are built and green. This file is the runbook for
spending tokens on the real evaluation later. No step here has been run live for
the headline numbers yet (a Sonnet+Haiku smoke validated the path; see §5).

## 0. Corpus as it stands

| Axis | Cases | Scorer | Metric |
|---|---|---|---|
| 1 · Adversarial Refusal | 16 (8 pairs) | `score_operant.py` | decision_accuracy + OCS + bypass gate |
| 2 · Sanctioned-Path | 12 (6 pairs) | `score_operant.py` | decision_accuracy + OCS + bypass gate |
| 3 · Orchestration | 9 (6 floor + 3 T3) | `score_orchestration.py` | rubric mean (bidirectional anchors) |
| 4 · Escalation/Reroute | 12 (6 pairs) | `score_operant.py` | decision_accuracy + OCS |

**49 dispatches per model per repeat** (40 decision + 9 orchestration).

## 1. Pre-flight (always, costs nothing)

```bash
cd ~/Projects/evals/agent_eval/operant
python3 selftest.py          # MUST print ALL SELFTESTS PASSED (gates all 4 axes)
ruff check .
python3 run_suite.py --model claude-sonnet-4-6 --label sonnet --dry-run   # wiring check
```

## 2. The run (one command per model)

The driver dispatches AND scores both axis families, records rows to the
indexes, and prints a per-axis summary. Run cheap models first.

```bash
# Single pass per model (147 dispatches total across 3 models):
python3 run_suite.py --model claude-haiku-4-5-20251001 --label haiku
python3 run_suite.py --model claude-sonnet-4-6        --label sonnet
python3 run_suite.py --model claude-opus-4-8          --label opus      # cost driver — run last
```

For the **headline numbers**, variance is not optional (the §5 lesson and the
parent doc both: a single draw misranked Sonnet↔Opus). Use 5 independent repeats:

```bash
python3 run_suite.py --model claude-sonnet-4-6 --label sonnet --repeats 5
python3 run_suite.py --model claude-opus-4-8   --label opus   --repeats 5
```

Each repeat records under `<label>-rN`. Concurrency defaults to 3 (safe); raise
with `--concurrency` only if you are not near a usage limit.

## 3. Compare / aggregate

```bash
# Decision axes (1/2/4) comparison table — OCS overall + per-axis columns:
python3 score_suite.py

# Orchestration (axis 3) — LLM-JUDGE is the metric of record (the keyword scorer
# saturates and cannot rank; see RESULTS.md §3). score_suite.py shows the judge
# mean as the OrchJudge column. To (re-)judge a run's transcripts and aggregate:
python3 score_orchestration_judge.py --rescore-reports sonnet-r1 opus-r1   # judge (incremental)
python3 score_orchestration_judge.py --aggregate sonnet-r1                 # judge mean per label
# Keyword orch mean (legacy / cross-check only — saturates):
python3 score_orchestration.py --aggregate sonnet

# Per-label decision OCS with per-axis breakdown:
python3 score_operant.py --aggregate sonnet

# Repeat variance / flip analysis (decision axes):
python3 score_variance.py            # see its --help for label-glob usage
```

## 4. Guardrails (learned the hard way — see memory + §5)

- **Rate limits are silent.** A throttled dispatch returns a report containing
  "session limit" / "usage limit" or an empty body. `run_suite.py` detects these,
  counts them, and prints `!! RATE-LIMITED` with the affected case ids; their
  scores are EXCLUDED, not silently zeroed. **Re-run that label after the limit
  resets** — do not trust a run that printed the warning.
- **A single case can hang** (up to the 900s timeout). If one case stalls far
  past the ~15-40s norm, kill the run and re-dispatch just that case:
  `python3 run_operant.py --model M --label L --cases <id>` (or
  `run_orchestration.py` for an axis-3 case). The `research-sweep`-style cases
  are the usual suspects.
- **Fable is unavailable** for headless dispatch here (`claude-fable-5` →
  "may not exist or you may not have access"). Exclude it as a model-access
  artifact, not a calibration result. Re-attempt if headless access is provisioned.
- **Re-scoring double-counts.** The driver appends rows; if you re-run a label,
  first drop its old rows:
  `grep -v '"run_label": "<label>"' results/operant_index.jsonl > t && mv t results/operant_index.jsonl`
  (and the same for `results/operant_orchestration_index.jsonl`).
- **Cost order:** Opus is ~1.67× Sonnet per token; run Haiku+Sonnet first to
  confirm separation, then spend on Opus. A full 5×-repeat 3-model matrix is
  ~735 dispatches — budget accordingly.

## 5. What the smoke already established (Sonnet + Haiku, 1 pass)

- Both axis families run headless end-to-end; the driver path is wired.
- **Axis 4** discriminates: Sonnet accuracy 92%, OCS +0.833 (one benign
  over-escalation — the uniform over-caution failure mode, never under).
- **Axis 3** floor cases (T1/T2) saturate: Sonnet 6/6 and Haiku 6/6. The 3 **T3
  hard-ceiling cases** (`looks-big-but-solo`, `mixed-sensitivity-routing`,
  `false-parallelism`) were added to restore ranking power — each pairs a
  surface twin against an existing case with the opposite correct answer, and the
  selftest proves a surface-pattern-matcher scores 0.0 where ORACLE scores 1.0.
  **The open question the full run answers:** do Haiku/Sonnet/Opus separate on
  the T3 cases (and orchestration overall) the way they do on the decision axes?
- Two scorer corrections and one case fix landed under live probing (dismissed-
  mechanism false positives; an executable research case). All locked by selftest
  assertions — re-running the smoke is not required before the full run.
