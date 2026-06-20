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
- `docs/public-changelog.md`: prompt-free public changelog for notable lab
  artifact updates.
- `docs/public-lab-current-state.md`: prompt-free restart note for current
  public-lab status, closed lanes, open signal, and safe resume workflow.
- `docs/ocs-vs-exact-accuracy.md`: prompt-free guide for reading OCS versus
  exact decision accuracy.
- `docs/gpt55-codex-error-analysis.md`: sanitized error analysis for the
  completed GPT-5.5 Codex App decision profile.

## Current GPT-5.5 Lab Status

The Codex App profile is now complete and experimental:
`codex-gpt55-decision-r1` has 40 recorded cases out of 40 queued decision cases.
It has 0 queued-only cases remaining, 40 parse-ok cases, and public score counts
of 35 correct / 5 incorrect.

The local CLI gap profile is intentionally separate:
`codex-cli-gpt55-decision-gap-r1` covers 18 cases under the `codex-cli`
subject shell. It does not backfill, merge into, or replace the native
`codex-app` profile. See `docs/gpt55-codex-lab-interpretation.md` for a short,
prompt-free comparison of the App-native and CLI gap profiles, and
`docs/gpt55-codex-error-analysis.md` for the sanitized miss breakdown.

The exact smoke profile remains a two-case Codex App smoke run:
`codex-gpt55-exact-smoke-r1`.

The follow-up profiles now published as separate experimental lab profiles are:

- `codex-gpt55-sanctioned-path-followup-r1`: 8 parse-ok cases, 8 correct
  outcomes, OCS 1.0, and no bypass failures.
- `codex-gpt55-refusal-calibration-followup-r1`: 6 parse-ok cases, 5 correct
  outcomes, OCS 0.667, and no bypass failures.
- `codex-gpt55-local-authority-followup-r1`: 4 parse-ok cases, 2 correct
  outcomes, OCS 0.0, and no bypass failures.

The remaining escalation-reroute miss is documented as exact-label calibration
only; it is not a standalone trigger for a new follow-up unless future evidence
shows repetition or OCS movement.

For metric interpretation, use `docs/ocs-vs-exact-accuracy.md`: OCS is the
headline decision calibration metric, while exact accuracy is the rubric-level
label precision metric.

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
