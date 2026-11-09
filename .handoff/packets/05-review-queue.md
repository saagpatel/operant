# 05 — Submission review queue

Read `.handoff/packets/00-contract.md` first; it is binding.

## Goal
Finished outside sittings wait in a queue; only after Saagar approves one does it appear
in `web/data/` (and so on the public table). Nothing reaches `web/data/` without a
passing receipt verification at approval time.

## Scope
1. `sittings/review/queue.py`: file-based queue under `<data_dir>/review/{pending,approved,rejected}/<sitting_id>/` holding `record.json`, `receipt.json`, and `status.json` (`{state, submitted_at, decided_at, reason}`); atomic moves; `enqueue(record, receipt)` (the runner's `review_hook` signature: `review_hook(record, receipt) -> str` returning the state, default `"pending"`); `list(state)`, `show(id)`, `approve(id, *, web_data, verifier, pubkeys, blob_source)`, `reject(id, reason)`. A sitting without a receipt is refused at enqueue (state `"rejected"`, reason `"unsealed"`) — unsealed rows never get near the table.
2. `approve` runs `verifier(receipt, record=..., ...)` (inject it; default lazily imports `sittings.receipt.verify.verify_receipt`, which packet 03 builds in parallel — tests use a stub verifier) and refuses unless `report.ok`; then publishes per the contract: copies each bound corpus and scorer file's bytes into `web_data/blobs/<sha256>` (from `blob_source` — the repo root at the bound bytes; refuse if the live file's hash differs from the bound hash and no blob exists), writes `receipts/<id>.receipt.json`, `records/<id>.record.json`, and inserts/updates the row in `rows.json` (sorted by ocs desc then sealed_at), all atomically; idempotent on re-approve; `unpublish(id)` removes the row (keeps blobs).
3. `sittings/review/__main__.py`: `python -m sittings.review list|show|approve|reject|unpublish --data-dir D --web-data web/data [--pubkey FILE]`.
4. `test_sittings_review.py`: enqueue → pending; approve with passing stub verifier → row, receipt, record and blobs present and hashes match; approve with failing verifier → nothing written to web_data; unsealed enqueue → rejected and never publishable; reject; unpublish; idempotent approve; path traversal in sitting id refused; rows.json stays valid JSON after each operation.

## Out of scope
Runner, receipt signing, web pages, any network.

## Acceptance
```sh
uv run python -m unittest test_sittings_review -v
uv run ruff check sittings ./*.py
```
