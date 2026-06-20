# OPERANT Public Changelog

## 2026-06-20 - GPT-5.5 Codex App Profile Completed

The public lab artifacts now include the completed native Codex App decision
profile for `codex-gpt55-decision-r1`: 40 recorded cases out of 40 queued
decision cases, 40 parse-ok cases, 0 queued cases, and public score counts of
35 correct / 5 incorrect.

The profile remains marked experimental because it is a single native-shell lab
run, not a repeated headline benchmark run. The local
`codex-cli-gpt55-decision-gap-r1` profile remains a separate subject-shell
profile and should not be merged into the Codex App result. See
`docs/gpt55-codex-lab-interpretation.md` and
`docs/gpt55-codex-error-analysis.md` for prompt-free interpretation notes.

## 2026-06-20 - Sanctioned-Path Follow-Up Slice Planned

The sanitized error analysis now has an execution path: run a focused
sanctioned-path follow-up slice before broadening the benchmark. The plan lives
at `docs/gpt55-sanctioned-path-followup-plan.md`; the prompt-free slice manifest
lives at `lab/followup/gpt55-sanctioned-path-slice-v1.json`.
