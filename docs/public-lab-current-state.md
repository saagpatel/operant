# OPERANT Public Lab Current State

Last verified checkpoint: `v0.6-public-lab` at commit
`0842c0c8b0c9177afba689dc6f7eb1e5d2bc296b`.

This note is a prompt-free restart aid for future public-lab work. It summarizes
what is published, what is intentionally private, and which lanes should not be
reopened without new evidence.

## Published Surface

The current public export includes these lab labels:

| run label | subject shell | recorded cases | score outcome | status |
|---|---|---:|---|---|
| `codex-gpt55-exact-smoke-r1` | `codex-app` | 2 | 2 correct | smoke complete |
| `codex-gpt55-decision-r1` | `codex-app` | 40 | 35 correct, 5 incorrect | experimental complete |
| `codex-cli-gpt55-decision-gap-r1` | `codex-cli` | 18 | 16 correct, 2 incorrect | local gap profile |
| `codex-gpt55-sanctioned-path-followup-r1` | `codex-app` | 8 | 8 correct | follow-up complete |
| `codex-gpt55-refusal-calibration-followup-r1` | `codex-cli` | 6 | 5 correct, 1 incorrect | local follow-up complete |
| `codex-gpt55-local-authority-followup-r1` | `codex-cli` | 4 | 2 correct, 2 incorrect | local follow-up complete |

Use `lab/public/lab-run-status.json` for run coverage and
`lab/public/model-cards/*.json` for scored profile summaries. Keep Codex App and
local Codex CLI profiles separate by subject shell.

## Closed Lanes

The sanctioned-path follow-up is closed for now: the focused App-native slice
recorded 8 parse-ok cases, 8 correct outcomes, OCS 1.0, and no bypass failures.

The escalation-reroute miss is documented as exact-label calibration only. It
does not justify a dedicated follow-up unless a future run repeats the pattern
or shows OCS movement.

Do not start a broad new full-profile run from the current evidence alone. The
known open signal is narrower: local CLI benign-open local-authority
over-withhold.

## Open Signal

The local-authority follow-up confirmed a local CLI false-positive pattern:
both paired guard-warranted cases scored correct, while both benign-open cases
scored incorrect. The public model card carries a `known_limitations` caveat for
that signal.

Treat this as a model/profile interpretation finding, not as permission to
publish raw prompts, final answers, transcripts, queue payloads, or held-out
reports.

## Safe Resume Workflow

Before changing public-lab artifacts:

1. Confirm `git status --short --branch` is clean on `main`.
2. Read `README.md`, `docs/public-changelog.md`,
   `docs/gpt55-codex-error-analysis.md`, and this note.
3. Use `python3 operant_lab_cli.py inventory-runs` for sanitized queue/run
   inventory if run state is in question.
4. Keep benchmark prompts, final answers, transcripts, queue payloads, and
   held-out report text out of chat, docs, PR bodies, and release notes.
5. Prefer docs or exporter-owned caveats before adding new benchmark runs.

Minimum verification for docs-only changes:

```bash
python3 operant_lab_cli.py check-public-artifacts
git diff --check
```

Minimum verification when exporter code or public JSON changes:

```bash
python3 -m py_compile operant_lab/*.py operant_lab_cli.py run_codex_app.py selftest.py
python3 selftest.py
ruff check operant_lab operant_lab_cli.py run_codex_app.py selftest.py
python3 operant_lab_cli.py export-public --include-lab-runs --lab-labels codex-gpt55-exact-smoke-r1
python3 operant_lab_cli.py export-public --out /tmp/operant-public-historical-check
```
