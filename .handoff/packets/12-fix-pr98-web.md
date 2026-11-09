# 12 — Fix PR 3/4 web verifier findings

Edit only `web/` (no Python). Each fix gets a regression in `web/test/verify.test.mjs`.

1. **major** `web/lib/verify.mjs` ~73: unpaired UTF-16 surrogates survive `canonicalJSON`, then
   TextEncoder turns them into U+FFFD, so replacing a signed answer's U+FFFD with an escaped lone
   surrogate passes the record and answers digest checks. Python's canonicalization rejects it. Reject
   unpaired surrogates (in keys and string values) during canonical encoding; regression: substitute a
   lone surrogate into an answer containing signed U+FFFD → verification fails on `record`/`answers`.
2. **major** `web/lib/verify.mjs` ~629: the host-relayed check hashes response bytes but never compares
   the provider response's completion with `call.completion_text`. Parse the decoded response body
   (OpenAI-compatible: `choices[0].message.content` string; validate structure strictly, mirror
   `sittings/model_binding` Python parsing — read it), require it to equal `completion_text` and the
   recorded answer, and require the response `model` to equal `response_model`. Regression: a correctly
   signed receipt whose answer and completion_text say one thing while the recorded response body says
   another fails on `model_binding.relay`.
3. CodeQL: `web/test/verify.test.mjs` ~708 incomplete string escaping (escape backslashes too, or use a
   safer construction); `web/lib/verify.mjs` ~681 useless initial assignment of `evidenceDetail`.

## Acceptance
```sh
node --test web/test/
```
