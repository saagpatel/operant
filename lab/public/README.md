# OPERANT Public Artifacts

This directory contains sanitized public exports for OPERANT. The files describe calibration profiles and lab run status, not raw benchmark prompts, model transcripts, or held-out reports.

## Files

- `benchmark-card.json`: benchmark-level metadata, case counts, and public split policy.
- `calibration-profiles.json`: compact index of exported model/run families and headline calibration fields.
- `lab-run-status.json`: sanitized run coverage and scoring-policy status for included native-shell lab labels.
- `model-cards/*.json`: per-profile scored decision and orchestration summaries.
- `methodology.md`: short methodology note for interpreting public lab exports.

## Interpretation

Treat `lab-run-status.json` as the coverage/status inventory and `model-cards/*.json` as the scored calibration profile surface. Native-shell profiles are intentionally separate: local CLI gap runs do not backfill or merge into Codex App native-shell profiles.

Queued-only cases are excluded from scoring until recorded. Public artifacts deliberately omit raw prompts and final answers. When both Codex App and local Codex CLI profiles are present, compare them by subject shell and overlapping case coverage rather than treating them as one flat leaderboard.
