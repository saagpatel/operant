# GPT-5.5 Codex Lab Interpretation

This note summarizes the public GPT-5.5 Codex lab profiles without raw prompts,
final answers, or transcripts. Treat it as a companion to `lab/public/` rather
than a replacement for the generated JSON artifacts.

## Status

`codex-gpt55-decision-r1` is the native Codex App profile. It is complete for
the 40 queued decision cases: 40 recorded, 40 parse-ok, 0 queued, 35 correct,
and 5 incorrect. Its public decision OCS is 0.808, with 0 bypass failures and 0
unparseable cases.

`codex-cli-gpt55-decision-gap-r1` is a local Codex CLI gap profile. It covers 18
cases under a separate `codex-cli` subject shell. It is useful for local
comparison, but it does not backfill, merge into, or replace the native
`codex-app` profile.

## App Versus CLI

The safest comparison is the 18-case overlap, not the full 40-case App profile
against the 18-case CLI profile.

| Profile | Scope | OCS | Accuracy | TPR | FPR | Bypass failures |
|---|---:|---:|---:|---:|---:|---:|
| Codex App | full 40 cases | 0.808 | 0.875 | 0.944 | 0.136 | 0 |
| Codex App | shared 18 cases | 0.889 | 0.889 | 1.000 | 0.111 | 0 |
| Codex CLI local | shared 18 cases | 0.778 | 0.889 | 1.000 | 0.222 | 0 |

On the shared cases, both shells withheld all guard-warranted cases and had the
same exact-decision accuracy. The App-native profile had a lower false-positive
rate on the overlap, which is why its overlap OCS is higher. The remaining
difference is a calibration detail, not evidence that the CLI run should be
folded into the App profile.

## Public Reading

For public reporting, cite the native App profile as the App result and the CLI
gap profile as a separate local subject-shell profile. Avoid case prompt text,
final answers, transcripts, or any conclusion that treats mixed subject shells
as one leaderboard row.
