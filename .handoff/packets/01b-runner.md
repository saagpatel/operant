# 01b — Finish the hosted sitting runner (second attempt)

Branch already has `sittings/scoring.py` from a first attempt; keep and improve it. Implement everything else in packet 01 below — the runner, client, and the full test list. Do not stop early: an unfinished packet is not done. `uv` works: its cache directory is writable; run `uv run ...` normally (do not override UV_CACHE_DIR). Decision for the open question: `n_scored` counts answers the scorer scored (including ones it graded as wrong/unparseable if score_one returns a row for them); `n_unanswered` counts cases with no accepted answer.

# 01 — Hosted sitting runner

Read `.handoff/packets/00-contract.md` first; it is binding.

## Goal
An outside agent completes a full sitting of the OPERANT decision corpus over HTTP and
receives a score, through a stdlib runner that wraps the existing self-serve scoring.

## Scope
1. `sittings/scoring.py`: `load_corpus()` → `{case_id: case}` from exactly the bound
   corpus files (reuse `score_operant.load_cases` via `operant_lab.selfserve.load_axes`
   or equivalent, but the result must be a function of `manifest.corpus_paths()` only);
   `system_prompt()` built from the pinned contract `manifest.OPERATOR_CONTRACT` via
   `run_operant.build_system_prompt` (never `resolve_operator_contract`'s home-dir
   fallback); `prompt_for(case)` = `agent_runners.build_byo_prompt(case, system_prompt())`;
   `score_answers(case_map, answers: list[dict]) -> dict` producing the contract `score`
   object by reusing `score_operant.score_one` / `aggregate` and `selfserve.classify_band`.
   Deterministic: same inputs, same output.
2. `sittings/runner/`: `server.py` with a `ThreadingHTTPServer` implementing the runner
   API in the contract; `store.py` for on-disk state under `--data-dir` (atomic writes,
   one directory per sitting, token stored only as a sha256); `__main__.py` so
   `python -m sittings.runner --host 127.0.0.1 --port 8765 --data-dir DIR` serves.
   - Sitting ids: `sit_` + 26 lowercase base32 chars from `secrets`. Tokens from `secrets.token_urlsafe(32)`, compared with `hmac.compare_digest`.
   - Cases served in a per-sitting shuffled order (seeded from `secrets`, recorded in `case_order`).
   - Input validation: 64 KiB body cap (413), JSON only (415/400), label/claimed_model length and printable-character checks, `consent_publish` must be true, answers ≤ 20000 chars, a case answered once (409), only the case last served by `next` may be answered (409).
   - Simple per-IP create rate limit (configurable, default 10/hour) and a max-open-sittings cap; sittings expire after a configurable TTL (default 2h).
   - `finish`: refuses (409) until every case is answered or `--allow-partial` is set on the server; builds the record (contract `operant.sitting/v1`), calls hook functions for the model binding and the receipt, writes `record.json`, and returns it. The hooks are injectable: `RunnerConfig(binding_hook=..., receipt_hook=..., review_hook=...)`. Defaults: binding hook returns a minimal `unbound` binding object (see contract; `binds: []`, `does_not_bind: ["which model produced the answers"]`), receipt hook returns `None`, review hook does nothing. Packets 02/03/05 plug in later; do not import them.
   - A hook point for extra routes: `RunnerConfig(extra_routes=[(method, path_regex, handler)])` so packet 02 can mount relay routes later.
   - Never log answers or tokens. Never execute or fetch anything the submitter sends.
3. `sittings/client.py`: `python -m sittings.client --runner URL --label L --claimed-model M (--cmd TEMPLATE [--cmd-stdin] | --adapter mod:func | --endpoint URL [...])` — reuse `operant_lab.agent_runners.make_runner` (look at `score_my_agent.py` for how it builds a runner from these flags) so the outside agent runs on the submitter's machine, pulls prompts, posts answers, finishes, and prints the score plus where the record was saved. Optional `--out` to save the returned record/receipt.
4. `test_sittings_runner.py`: start the server on port 0 in a thread; drive a full sitting with `examples/heuristic_agent.py`'s `respond` through the client code path; assert the score equals scoring the same answers directly with `score_answers`; tests for 401/404/405/409/413, double answer, answering an unserved case, partial finish refused, expired sitting, rate limit, record written with the contract fields and the corpus/scorer manifests, token not present in any file under the data dir.

## Out of scope
Model binding logic, signing, the review queue, web pages, TLS, deployment.

## Acceptance
```sh
uv run python -m unittest test_sittings_runner -v
uv run ruff check sittings ./*.py
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest discover -p 'test_*.py'
```
