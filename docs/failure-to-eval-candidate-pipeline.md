# Failure-to-eval candidate admission

`FailureEvalCandidateV1` turns a reproducible harness failure into a reviewable
regression-evaluation candidate without silently promoting telemetry, transcripts, or
an automated suggestion into the benchmark.

Admission is fail-closed. `operant_lab.failure_eval` requires:

- an exact `operant-failure-eval-candidate.v1` schema with no unknown fields;
- source lineage and a lowercase SHA-256 digest;
- an argv-form command bound to an exact receipt for at least two matching deterministic
  reproductions;
- a contained, exact-byte publication/privacy review that approves only synthetic content;
- a minimal fixture and explicit expected invariant;
- an explicit human-admission claim whose source digest resolves against separately supplied
  authority bytes outside the repository; and
- a recomputed semantic deduplication key. A batch rejects duplicate IDs and duplicate
  failure keys even when the display IDs differ.

The first admitted candidate is
`fixtures/failure-eval-candidates/operant-selfserve-nonzero-exit-v1.json`. Its
minimal reproduction is entirely synthetic: a child prints a decision-shaped stdout
value and a diagnostic, then exits 7. The candidate binds the user-goal attachment
digest as `admission_source_type=user_goal`. That records operator authorization for
this regression only; `independent_custody` is explicitly false, so it must not be
described as independent review, certification, or third-party admission.

## Enforced invariant

A shell attempt is eligible for scoring only when its exit metadata is an exact Python
integer equal to zero. `ShellCommandRunner` retains only SHA-256 digests and byte
counts for failed-process stdout/stderr, so secrets or other raw diagnostics cannot
flow into retained results or console/CI logs. Successful stdout is capped at 1 MiB;
oversized answers fail without retaining answer text. Score admission checks the exit
metadata again and rejects nonzero,
boolean, string, or otherwise malformed values independently of the runner.

If any requested subject dispatch is incomplete, the parent `score_my_agent.py`
command exits nonzero and publishes no report, summary JSON, SVG badge, or badge
snippet. It invalidates those exact prior projection paths before dispatch so a stale
successful artifact cannot be mistaken for the failed attempt. Python and HTTP
runners remain compatible because they do not claim process-exit metadata; their
existing exception, HTTP, timeout, and empty-answer behavior remains fail-closed.

## Validate locally

No network, model call, or third-party package is required:

```bash
python3 -m operant_lab.failure_eval \
  --admission-authority /path/to/exact-user-goal-or-review-record \
  fixtures/failure-eval-candidates/operant-selfserve-nonzero-exit-v1.json
python3 -m unittest test_failure_eval_candidate.py
python3 selftest_selfserve.py
```

These checks validate exact reproduction/publication receipts, tamper and duplicate rejection,
external human-authority binding, digest-only nonzero diagnostics, independent score
admission, stale-artifact invalidation, and parent CLI failure status.
