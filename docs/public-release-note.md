# OPERANT Public Lab Release Note

OPERANT is an operating-agent calibration benchmark. It asks whether an agent
should act, refuse, escalate, reroute, or choose a different operating plan
rather than asking only whether the agent can complete a coding task.

The current public surface includes the headline Claude calibration profiles and
the native-shell public lab artifacts under `lab/public/`. Those artifacts are
designed for outside readers who need the benchmark metadata, scored calibration
profiles, and run coverage status without raw benchmark prompts or model
transcripts.

## What Is Included

- `lab/public/benchmark-card.json`: benchmark metadata, case counts, and public
  split policy.
- `lab/public/calibration-profiles.json`: compact index of exported model/run
  families and headline calibration fields.
- `lab/public/lab-run-status.json`: sanitized run coverage and scoring-policy
  status for included native-shell lab labels.
- `lab/public/model-cards/*.json`: per-profile scored decision and orchestration
  summaries.
- `lab/public/methodology.md`: short methodology note for interpreting public
  lab exports.
- `lab/public/README.md`: artifact index for the public export directory.

## Current GPT-5.5 Lab Status

The Codex App profile is intentionally marked partial and experimental:
`codex-gpt55-decision-r1` has 22 recorded cases out of 40 queued decision cases.
Queued-only cases are excluded from Codex App scoring until recorded.

The local CLI gap profile is intentionally separate:
`codex-cli-gpt55-decision-gap-r1` covers 18 cases under the `codex-cli` subject
shell. It does not backfill, merge into, or replace the native `codex-app`
profile.

The exact smoke profile remains a two-case Codex App smoke run:
`codex-gpt55-exact-smoke-r1`.

## What Is Excluded

Public artifacts deliberately exclude raw benchmark prompts, final answers, full
model transcripts, and held-out reports. The checked public export contract can
be validated with:

```bash
python3 operant_lab_cli.py check-public-artifacts
```

That contract verifies required public files, JSON parseability, model-card
presence, absence of prompt-like public fields, and separation between Codex App
and local CLI native-shell profiles.
