# OCS vs Exact Accuracy

This note explains how to read OPERANT decision results without raw prompts,
final answers, transcripts, queue payloads, or held-out reports.

## Short Version

Exact accuracy asks whether the model produced the exact expected decision
label.

OCS asks whether the model made the right safety-relevant action posture:
withhold when withholding is required, and avoid over-withholding when direct
action is allowed.

That means exact accuracy can move when OCS does not. A model can choose a
more guarded allowed-action label than the rubric expected and lose exact
accuracy without creating a worse safety posture.

## How To Read Misses

Use exact accuracy for rubric precision. It is useful when comparing whether a
model can distinguish nearby labels such as direct action, sanctioned action,
refusal, escalation, and reroute.

Use OCS for operating-safety calibration. It is the better headline signal when
the question is whether the model should have acted, withheld, or avoided a
bypass-like failure.

The miss buckets in `docs/gpt55-codex-error-analysis.md` should be read this
way:

| miss bucket | exact accuracy | OCS | interpretation |
|---|---:|---:|---|
| Guard-warranted under-withhold | down | down | safety-relevant gap |
| Benign-open over-withhold | down | down | false-positive caution |
| Exact-label mismatch, same binary action posture | down | unchanged | label calibration only |

## Current GPT-5.5 Example

The completed `codex-gpt55-decision-r1` Codex App profile has 40 parse-ok
cases, 35 correct outcomes, 5 incorrect outcomes, OCS 0.808, and no bypass
failures.

One incorrect case is an escalation-reroute exact-label mismatch with the same
binary action posture. It lowers exact accuracy, but it does not lower OCS or
justify a dedicated follow-up by itself. The public error analysis records that
case with sanitized inventory fields only.

## Reporting Guidance

- Report OCS as the headline decision calibration metric.
- Report exact accuracy when discussing label precision or rubric-level drift.
- Do not collapse Codex App and local Codex CLI profiles into one leaderboard
  row; compare them by subject shell and overlapping coverage.
- Do not publish raw prompts, final answers, transcripts, queue payloads, or
  held-out report text to explain a metric movement.
