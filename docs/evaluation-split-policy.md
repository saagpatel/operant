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

New runner receipts use `operant-run-manifest.v7`. Each receipt carries:

- a non-confirmatory evaluation role;
- one SHA-256 digest over the sorted exact case objects and split label used by
  that invocation;
- the bound case count and split label;
- an `operant-execution-binding.v5` over exact prompt/system bytes, command or
  stdin shape, tool policy, timeout, output mode, dispatch settings, harness
  bytes, source state, dependency-lock state, and a sanitized environment
  snapshot;
- a requested-model identifier plus exact, unaliased provider-reported model
  candidates when a structured result envelope exposes them;
- `confirmatory_eligible: false`.

The execution hashes are input bindings, not proof of replayability. Receipts
therefore classify replayability as `INPUT_BOUND_NOT_REPLAYABLE`. A provider
candidate is evidence about what the tool reported, not proof of the model
actually served; `served_model_identity` remains `UNKNOWN`. Exact mismatches
and ambiguous multi-model reports preserve the attempt output but are blocked
from every score, rescore, variance, judge, inventory, and export path.
Nonzero process exits, provider-declared error results, and unparsable outputs
are also preserved as failed receipts, including the process exit code when
applicable, but cannot emit a deterministic report projection or enter
scoring/export. Codex queue-derived
receipts bind the exact source queue bytes by SHA-256. Receipt publication
precedes report projection, preventing receipt-construction failures from
leaving a scoreable orphan report.

Zero-cost fixture burn-in covers the native, Codex CLI, and Codex App producer
paths plus suite/export consumption. It verifies local harness contracts, not
provider availability, served-model identity, or authentic provider burn-in.

Persisted v7 manifests carry a core digest over interpretation-critical
metadata, so later changes to shell, role, split, queue provenance, timestamps,
or treatment labels invalidate the receipt. Source state is classified as
`CLEAN_COMMIT`, `DIRTY_DIGEST_ONLY`, or `UNKNOWN`; a dirty-state digest is not
enough to reconstruct the working tree. Python lockfiles are classified as
`LOCKFILE_PRESENT_UNVERIFIED` unless active-environment linkage is separately
proved. New receipts bind the resolved pre-dispatch executable candidate's
basename, SHA-256, and byte size without invoking it, or preserve an explicit
UNKNOWN reason. This does not prove that the same bytes were executed. Runtime
version remains `UNKNOWN` because version commands are not run without a proven
no-side-effect contract; no receipt should be called replayable on the basis of
executable, Python, or OS facts alone. After a returned subprocess attempt, the
runner recaptures the executable candidate. `MATCHED` means only that the
pre/post candidate snapshots agree; it does not attest the process image,
exclude change-and-restore races, or prove the bytes executed by the kernel.
`DRIFTED` blocks scoring. Failed launch, timeout, unavailable candidate, and
manual App paths remain unassessed or `UNKNOWN`, never a post-dispatch pass.
Kernel-observed process-image identity remains separately `UNKNOWN`; PID paths,
one-point dynamic code-signing observations, and candidate hashes are not promoted
to executed-image attestation. The approval-gated macOS boundary is documented
in `docs/process-image-attestation-boundary.md`.

Unknown families default to
`UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY`. Known model-specific follow-up
families resolve to adaptive diagnostic roles. The manifest writer rejects a
`CONFIRMATORY` role; admitting a future confirmatory set requires a new checked
workflow after every admission gate above is evidenced. Historical receipts
without these execution fields remain historical and `UNKNOWN`; they are not
backfilled. A receipt that claims v3/v4/v5/v6/v7 but omits or malforms its execution binding
is invalid rather than historical.

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
