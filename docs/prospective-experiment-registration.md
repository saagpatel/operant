# Prospective experiment registration

OPERANT's run manifests bind the inputs of an attempt after dispatch. The
`operant-experiment-preregistration.v1` contract adds the earlier control point:
an experiment design can be written, hashed, reviewed, and committed before a
subject is ever invoked.

The registration is intentionally not a confirmatory certificate. The writer
fixes the admission fields to:

- `REGISTERED_NOT_EXECUTED`
- `evaluation_role: UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY`
- `confirmatory_eligible: false`
- `external_custody`, `served_model_identity`, and `independent_replication`:
  `UNKNOWN`

It also rejects result-bearing keys such as `score`, `answers`, `decision`, and
`transcript`. This keeps observed outcomes in run receipts rather than allowing
the preregistration to become a post-hoc result container.

## Register a design

Create a JSON spec containing design decisions and digest records. The spec must
include `experiment_id`, `research_question`, `hypotheses`, `primary_metrics`,
`exclusions`, `stopping_rule`, `analysis_plan`, `bound_inputs`,
`execution_policy`, `claim_ceiling`, and `prohibited_claims`.

Each `bound_inputs` row has a unique filename-safe `name` and a lowercase
SHA-256 `sha256` digest. Include at least the case bundle, scorer/protocol,
subject or adapter, and dependency lock/configuration bytes. A `count` may be
included for case-like inputs.

The execution policy requires `max_retries: 0` and
`spend_policy: NO_PAID_MODEL_OR_API_SPEND`; network and tool behavior must be
declared explicitly. For example:

```json
{
  "experiment_id": "adapter-diagnostic-r1",
  "research_question": "Does the adapter preserve the declared output contract?",
  "hypotheses": ["Malformed and failed attempts remain excluded from scoring.", "..."],
  "primary_metrics": ["ocs", "decision_accuracy"],
  "exclusions": ["Exclude only attempts rejected by the declared parse contract."],
  "stopping_rule": "Run the complete matrix once; stop on any missing cell.",
  "analysis_plan": "Report the predeclared metrics and preserve all failed attempts.",
  "bound_inputs": [
    {"name": "cases", "sha256": "<64 lowercase hex>", "count": 40},
    {"name": "protocol", "sha256": "<64 lowercase hex>"},
    {"name": "subject", "sha256": "<64 lowercase hex>"},
    {"name": "dependencies", "sha256": "<64 lowercase hex>"}
  ],
  "execution_policy": {
    "max_retries": 0,
    "network_policy": "DISABLED",
    "spend_policy": "NO_PAID_MODEL_OR_API_SPEND",
    "tool_policy": "NO_TOOLS"
  },
  "claim_ceiling": "Local, non-confirmatory diagnostic over the bound bytes.",
  "prohibited_claims": ["No named-model ranking, certification, or external replication claim."]
}
```

Write and verify it with the public-lab CLI:

```bash
python3 operant_lab_cli.py register-experiment \
  --spec /path/to/adapter-diagnostic-spec.json \
  --out experiments/preregistrations/adapter-diagnostic-r1.json

python3 operant_lab_cli.py verify-experiment-registration \
  experiments/preregistrations/adapter-diagnostic-r1.json
```

Registration and its `.sha256` sidecar are exclusive-write artifacts: an
existing file is never overwritten. Commit both files, review the design, and
only then dispatch the subject. A registration digest proves byte integrity,
not authorship, immutable custody, provider identity, or reproducibility. The
subsequent run must still emit v8 receipts and preserve failed, interrupted,
null, and excluded attempts.

This workflow does not admit a confirmatory evaluation. The evaluation-split
policy's independent case-selection, custody, identity, and unblinding gates
remain separate and must be evidenced by a future checked workflow before any
confirmatory or named-model claim is made.
