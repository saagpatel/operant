# OPERANT Evaluation Split Policy

OPERANT separates adaptive development evidence from confirmatory evaluation
evidence. This policy does not relabel historical runs as confirmatory.

## Current Decision

No existing OPERANT score is confirmatory under the checked registry.

- The canonical suite is open and has been used for analysis and iteration.
  Its historical selection, exposure, and adaptation history is incomplete.
- `generated/operant_public_cases.json` is an open development surface.
- A private split from `gen_cases.py` uses a disjoint deterministic seed domain
  but reuses the same public templates, finite slot pools, decision structure,
  and scorer. Repeated fillers remain possible. It is a publicly derivable
  surface holdout only.
- GPT-5.5 sanctioned-path, refusal-calibration, and local-authority follow-ups
  were designed from observed misses. They are adaptive diagnostics, regardless
  of whether their prompt text remains private.
- Smoke and coverage-completion runs are non-confirmatory checks.

These classifications concern evaluation role. They do not decide whether a
score was calculated correctly, whether a receipt is authentic, or whether a
model identity is proven.

## Confirmatory Admission Gate

A future result may be called confirmatory only when all of these facts are
captured before unblinding:

1. A timestamped preregistration binds the research question, hypotheses,
   primary metrics, exclusions, stopping rule, and analysis plan.
2. Immutable hashes bind the cases, operator contract, scorer, judge policy,
   subject shell, model configuration, and dependencies.
3. The case family is structurally independent of development and error-analysis
   cases, not merely a new surface rendering of shared templates.
4. Case-selection authorship, model or agent exposure, and prior use are
   recorded.
5. The set is sealed before subject outputs and has an auditable unblinding
   event.
6. Subject identity and treatment receipts are independently verifiable.
7. Failed, null, excluded, and interrupted attempts remain preserved.
8. Any deviation is recorded before results are interpreted.

If any gate evidence is unavailable, confirmatory status remains `UNKNOWN` or
`NOT_ESTABLISHED`; it is never inferred from secrecy, file presence, a fresh
seed, or a successful score calculation.

## Run-Level Binding

New runner receipts use `operant-run-manifest.v2`. Each receipt carries:

- a non-confirmatory evaluation role;
- one SHA-256 digest over the sorted exact case objects and split label used by
  that invocation;
- the bound case count and split label;
- `confirmatory_eligible: false`.

Unknown families default to
`UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY`. Known model-specific follow-up
families resolve to adaptive diagnostic roles. The v2 manifest rejects a
`CONFIRMATORY` role; admitting a future confirmatory set requires a new checked
workflow after every admission gate above is evidenced. Historical receipts
without these fields remain historical and `UNKNOWN`; they are not backfilled.

## Checked Registry

`lab/public/evaluation-split-registry.json` records the current split and run
family dispositions without exposing private prompts. Run:

```bash
python3 verify_evaluation_split.py
python3 -m unittest test_evaluation_split.py
```

The verifier fails if an adaptive or surface-holdout lane is upgraded, a scored
family is omitted, bound evidence drifts, or public wording again implies that
surface randomization proves contamination resistance.
