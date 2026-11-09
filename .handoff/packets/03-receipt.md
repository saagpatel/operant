# 03 — CheckSeal receipt generator and verifier

Read `.handoff/packets/00-contract.md` first; it is binding.

## Goal
A finished sitting produces a signed, versioned `operant.sitting-receipt/v1` receipt
binding corpus bytes, scorer bytes, answers, score, and the model binding; tampering
with any bound field fails verification, and an old receipt stays verifiable against the
scorer bytes it was graded with.

## Scope
1. Add `checkseal[sign]==0.1.1` to the `dev` dependency group in `pyproject.toml` and update `uv.lock` (`uv lock`; this packet has network). Keep `uv sync --frozen` working.
2. `sittings/receipt/`: 
   - `sign.py`: `seal_record(record, signer) -> receipt dict` building the Statement exactly as the contract says (canonical JSON payload), signed with `checkseal.sign.local.LocalKeySigner` over CheckSeal's DSSE PAE (read CheckSeal's `dsse.py` and `seal.py` — installed package source is under the venv too — and reuse its functions). Key handling: `load_signer(path)` / `generate_key(out_priv, out_pub)` reusing checkseal's PEM helpers; private key file created 0600; `export_pubkey_json(signer) -> operant.sittings-pubkey/v1` object (contract).
   - `verify.py`: `verify_receipt(receipt, *, pubkeys, record=None, blob_root=None, repo_root=None, rescore=True) -> Report` where `Report` lists findings `{field, ok, detail}` for: format/version known; signature valid for a trusted keyid; subject digest equals `canonical_sha256(record)` when a record is given; `answers_sha256` matches; corpus manifest aggregate recomputed from the listed files and each file's bytes re-hashed from `blob_root/<sha256>` or `repo_root/<path>`; same for scorer; score recomputed by loading the **bound** scorer bytes (from blobs when the scorer has changed since — load them into an isolated temp dir and import from there, never from the live repo when hashes differ) and rescoring the record's answers against the bound corpus; model binding re-checked by calling `sittings.model_binding.verify_binding` if importable (packet 02 builds it in parallel; if not importable, report that field as `ok: false, detail: "binding verifier unavailable"`, never skip silently). `Report.ok` is true only if every finding is ok.
   - `__main__.py`: `python -m sittings.receipt keygen|seal|verify ...` CLI; `verify` prints the field-by-field table and exits non-zero on any failure.
   - The fixed `seal_does_not_bind` list (contract): at least — the submitter's honesty about their setup beyond what the model binding covers; that the agent was not tuned on the public corpus; anything about the orchestration axis; identity of the submitter; that the host signing key was not compromised (T1 local key — state it).
3. `test_sittings_receipt.py`: build a record fixture by hand (contract shape, real corpus/scorer manifests from `sittings.manifest`, answers from `examples/heuristic_agent.py`, score from a scoring helper you write locally in the test if `sittings.scoring` does not exist yet — `sittings/scoring.py` is packet 01's; do not create it, guard with `unittest.skipUnless` only for the rescore test); valid receipt verifies; flipping one byte or field in each of: payload, signature, keyid, corpus file bytes, scorer file bytes, an answer, score, binding level, binding evidence, sitting id → fails on the right field; unknown receipt_format refused; untrusted key refused; old-scorer case: write a modified scorer blob set and show a receipt sealed against old bytes still verifies via blobs while the live file differs.

## Out of scope
Runner server, model binding logic, web, review queue.

## Acceptance
```sh
uv sync --frozen
uv run python -m unittest test_sittings_receipt -v
uv run ruff check sittings ./*.py
```
