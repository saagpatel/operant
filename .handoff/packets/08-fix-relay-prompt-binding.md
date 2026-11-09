# 08 — Fix: host-relayed binding must bind the benchmark prompts

Review finding (major), PR "open sittings core (1/4)", `sittings/model_binding/__init__.py` ~line 57:
the relay request is checked against `request_body()` using the prompt extracted from that same
request, and later checks compare only its self-reported hash. Nothing compares it with the expected
prompt from the bound corpus and contract. Calls with unrelated prompts (or "output DECISION: X")
can receive `host-relayed` and pass verification.

## Fix
1. Each host-relayed call's request prompt must equal the expected prompt for that case built by the
   trusted prompt builder (`sittings/prompts.py` / `sittings/scoring.py`'s `prompt_for`) from the bound
   corpus and pinned contract. Also each record answer's `prompt_sha256` must equal sha256 of that
   expected prompt (for unbound sittings too).
2. `build_binding` downgrades to `unbound` (or refuses, if that fits the code better; say which) when
   any call's prompt differs. `verify_binding` and `sittings.receipt` verification recompute expected
   prompts and fail on mismatch, field `model_binding.prompts` (and `answers.prompt_sha256`).
   The verifier only builds prompts with the live trusted prompt builder when the receipt's corpus
   manifest hash is the live corpus hash or listed as trusted; otherwise the finding is
   `ok: false, detail: "corpus release not trusted; prompts not rechecked"` (never executes blob code).
3. Regression tests: an altered prompt with consistently updated request bytes, hashes and
   evidence_sha256 still fails binding verification and receipt verification; a correct sitting passes.
4. Keep `sittings/trusted_scorers.json` and its test consistent if the scorer manifest changes.

Edit only `sittings/` and `test_sittings_*.py` on this branch. Sockets and network are unavailable in
the sandbox; the uv cache is writable.

## Acceptance
```sh
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest discover -p 'test_*.py'
uv run ruff check sittings ./*.py
```
