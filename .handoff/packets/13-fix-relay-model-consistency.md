# 13 — Browser relay check: one consistent model, matching observed_model

Review finding (major), `web/lib/verify.mjs` ~672 `checkRelay`: each response is validated alone, but
nothing checks that all calls report one consistent model or that it equals `binding.observed_model`.
A signed receipt with calls from two models, or an observed_model contradicting its responses, passes.

Fix in `web/lib/verify.mjs`, mirroring the Python rules in `sittings/model_binding/__init__.py` (read
`build_binding` and its claim_status / observed_model / header logic and match them exactly):
require one non-empty response model across all calls (response body `model`, `response_model`, and
`response_header_model` consistent per the Python rules), equal to `binding.observed_model`, and the
evidence `claim_status` consistent with claimed vs observed. For `host-relayed` level any inconsistency
fails `model_binding.relay`. Regression tests with re-signed receipts: mixed response models; observed_model
contradicting responses; claim_status contradicting claimed/observed. Keep all existing tests passing.
Also review `checkRelay` once more for any other field the Python `build_binding` derives from evidence
that the browser could recompute without score replay or prompt reconstruction, and check it too.

Edit only `web/`. No Python edits.

## Acceptance
```sh
node --test web/test/
```
