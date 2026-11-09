# 09 — Fix PR review threads on open sittings core

1. **Bug (P2)** `sittings/receipt/verify.py` ~line 168 `_rescore`: when a sitting was scored with
   `score_answers(..., non_publishable_reasons=["bound files changed during sitting"])`, the scorer puts
   the supplied reason before its own coverage/parseability reasons, but replay scores without the
   argument and appends the reason afterward, so canonical bytes differ and a valid receipt fails with
   `score mismatch`. Make replay pass the recorded host-supplied reasons through the same
   `score_answers` argument so ordering is identical by construction (derive which reasons were
   host-supplied in one documented way). Regression test: a partial, stale-bytes sitting sealed with
   the real API verifies; tampering its reasons still fails.
2. **Cyclic import** `sittings/prompts.py` <-> `sittings/scoring.py` (CodeQL). Break the cycle (e.g. the
   shared helpers live in one module, or prompts imports nothing from scoring). Keep
   `sittings/manifest.py` closures and their tests correct; update `sittings/trusted_scorers.json` if
   the scorer manifest hash changes (its test enforces this).
3. `test_sittings_scoring.py` ~line 189: use `assertLessEqual` instead of `assertTrue(a <= b)`.

Edit only `sittings/` and `test_sittings_*.py`. No network or sockets in the sandbox; uv cache writable.

## Acceptance
```sh
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest discover -p 'test_*.py'
uv run ruff check sittings ./*.py
```
