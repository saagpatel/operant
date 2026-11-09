# 14 — Browser verifier enforces the exact v1 model-binding schema and policy

Review finding (major), `web/lib/verify.mjs` ~756: binding claims are only checked as string arrays and
unbound evidence only against its hash. A correctly signed receipt with null evidence, an invented
observed model, `binds=["Independently verified model identity"]` and `does_not_bind=[]` passes.

Goal: the browser verifier accepts exactly what Python's `sittings/model_binding` would produce, for every
check that needs neither score replay nor case-prompt reconstruction. Do this once and thoroughly so no
further binding gaps remain.

1. Add `sittings/testdata/model-binding-policy-v1.json` exported from Python's fixed policy (levels, exact
   `binds` / `does_not_bind` text per level, evidence key schema per level, claim_status values and how
   they derive from claimed/observed). Add a Python test asserting the JSON equals what
   `sittings/model_binding` uses (import its constants; do not change Python behavior).
2. `web/lib/verify.mjs`: embed the same policy (a module constant) and a Node test asserting it equals the
   JSON file. Enforce: exact top-level binding keys; known level; `binds`/`does_not_bind` exactly equal the
   policy for that level; evidence exact schema per level (unbound: whatever Python's unbound evidence is;
   host-relayed: calls list with exact call fields); `evidence_sha256`; `observed_model` derivation;
   `claim_status` derivation; model consistency across calls (already added); `claimed_model` equals the
   predicate's. Any deviation fails a specific `model_binding.*` field.
3. Update any synthetic test fixtures to valid v1 bindings. Signed negative tests: altered binds, missing
   limits, null evidence, extra evidence key, invented observed_model on unbound, wrong claim_status,
   unknown level. The real e2e fixtures under `web/test/fixtures/web-data/` must still pass.

Edit `web/`, `sittings/testdata/`, and add one Python test file `test_sittings_binding_policy.py`. Do not
change other Python code. No sockets/network.

## Acceptance
```sh
node --test web/test/
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest test_sittings_binding_policy -v
uv run ruff check sittings ./*.py
```
