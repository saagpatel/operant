# OPERANT Public-Harness Ablation Protocol

This protocol measures whether presentation cues in the public OPERANT decision
harness are load-bearing for the repository's transparent deterministic heuristic
baseline. It is a harness diagnostic, not a model evaluation, leaderboard,
certification, or confirmatory result.

## Boundary

- Subject: `examples/heuristic_agent.py:respond`, bound by source digest.
- Corpus: the 40 public decision cases and 20 matched pairs across axes 1, 2,
  and 4.
- Spend: none. The runner makes no network or provider calls.
- Results: local, prompt-free receipts under the gitignored `results/` tree.
- Public changes: reusable tooling, tests, this protocol, and a prospectively
  committed preregistration only. Experimental outputs must not be committed or
  added to public lab/model-card artifacts.

The experiment cannot support named-model performance, deployment safety,
independent validation, contamination resistance, or certification claims.

## Preregister Before Execution

Create a timestamped preregistration, review it, and commit it with its digest
sidecar before running the subject:

```bash
python3 run_harness_ablation.py preregister \
  --out experiments/preregistrations/<timestamp>-operant-harness-ablation.json
```

The run command fails unless the preregistration and sidecar are committed at
the current clean `HEAD` and the bound code, corpus, scorer, subject, and
treatment hashes still match.

## Treatments

The registered matrix includes:

1. canonical public presentation;
2. opaque subject-visible metadata;
3. seeded degrouping and order changes;
4. a minimal response contract without the full decision glossary;
5. an equivalent alias for the canonical `CASE PROMPT:` marker;
6. seeded, hand-reviewed semantic aliases for public lexical cues; and
7. a combined ablation.

Only `task_prompt` may change in a lexical treatment. Ground-truth class,
accepted decisions, dangerous-operation expectation, bypass patterns, axis,
side, pair identity, and all other fields must remain unchanged. That invariant
is machine-checked. The semantic aliases are authored paraphrases, not
independently adjudicated cases.

## Metrics And Accounting

Each treatment runs over five preregistered seed/order passes. The five passes
measure deterministic sensitivity; they do not inflate the independent case
denominator beyond 40 cases and 20 matched pairs.

The prompt-free receipt reports OCS, exact decision accuracy, benign
over-withhold, guarded under-withhold or bypass, per-axis movement, pair
consistency, unparseable outputs, dispatch failures, and seed/order ranges. It
uses a seeded paired cluster bootstrap over matched pairs for OCS and accuracy
deltas, plus Wilson intervals for bounded rates.

Every attempt is recorded once and never retried. Raw prompts, answers,
transcripts, queue payloads, and bypass evidence are excluded from receipts.
The runner performs a second in-process deterministic replay and binds both
result-core digests.

## Run And Verify

After the preregistration commit:

```bash
python3 run_harness_ablation.py run \
  --preregistration experiments/preregistrations/<timestamp>-operant-harness-ablation.json \
  --out-dir results/harness-ablation/<run-id>

python3 run_harness_ablation.py verify \
  --receipt results/harness-ablation/<run-id>/receipt.json
```

The local receipt is unsigned consistency evidence. It does not prove
authorship, immutable history, or independent custody.

## Confirmatory Admission Decision

A future confirmatory evaluation requires every admission fact in
`docs/evaluation-split-policy.md`, including structurally independent cases,
sealed treatment before execution, distinct custody or independent generation,
identity-bound dispatch, immutable attempt manifests, complete failure
accounting, reproducible hashes, and a non-public treatment that cannot leak
through the repository.

If any required fact is false or unknown, the terminal decision is:

> STOP — confirmatory treatment not currently admissible.

More local tooling cannot substitute for missing independence or custody.
