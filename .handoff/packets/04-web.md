# 04 — Sealed-only /instruments table and one-click verify page

Read `.handoff/packets/00-contract.md` first; it is binding (receipt format, canonical
JSON, `web/data/` layout, pubkey file).

## Goal
A static site under `web/` that can be served from any static host (later mounted at
`/instruments` on Saagar's site): a results table that renders sealed rows only, and a
verify page that re-checks a receipt from first principles, field by field.

## Scope
1. `web/lib/verify.mjs` — ES module, no dependencies, runs in browsers and in Node 22+ (`crypto.subtle`, Ed25519 via WebCrypto `importKey('spki', ..., {name: 'Ed25519'})`). Port the DSSE PAE and Ed25519 check from `the CheckSeal repository's `js/checkseal_verify.mjs`` (read it; copy the needed functions with a comment crediting the source file). Functions: `canonicalJSON(value)` matching Python's canonical form in the contract; `verifyReceipt(receipt, {pubkeys, fetchBlob, record}) -> {ok, findings: [{field, ok, detail}]}` checking: receipt_format known; signature by a trusted keyid; payload is a Statement with the expected predicateType; corpus aggregate recomputed from the files list and each file re-hashed from `fetchBlob(sha256)`; same for scorer; if a record is supplied, its canonical sha256 equals the subject digest and `answers_sha256` matches; model binding: `evidence_sha256` recomputed, level is a known name, and `binds` / `does_not_bind` shown verbatim. It must list what it does NOT check in the browser (score re-execution needs Python: show the exact CLI command `uv run python -m sittings.receipt verify ...`). Never print a bare "verified" for the model — print the binding level and its `binds` text.
2. `web/instruments/index.html` (+ small JS/CSS): loads `../data/rows.json` and `../data/sittings-pubkey.json`; for each row fetches the receipt, verifies the signature and that every displayed field (agent_label, claimed_model, ocs, tpr, fpr, band, binding_level, sealed_at) equals the signed predicate; renders only rows that pass, each with a green "sealed" mark that links to `../verify/?receipt=<sitting_id>`; shows "N rows refused (failed seal check)" for the rest; empty state when no rows. No unverified-claims caveat — the table is sealed-only by construction. Show binding level per row in words ("relay-attested", "unbound", ...) with a short tooltip of what it binds. Escape all text (no innerHTML with data).
3. `web/verify/index.html`: `?receipt=<id>` loads `../data/receipts/<id>.receipt.json` and `../data/records/<id>.record.json`; also accepts a pasted/uploaded receipt JSON. Shows each field's check as a row (pass/fail + detail), the bound hashes, the score, the binding level with `binds` and `does_not_bind` lists, the seal's own `seal_binds` / `seal_does_not_bind`, and the CLI command for the full re-check.
4. Plain, readable design; works at 375px width; light and dark via `prefers-color-scheme`; no external scripts or fonts.
5. `web/test/verify.test.mjs` run with `node --test web/test/`: generate an Ed25519 key with WebCrypto in the test, build a receipt fixture (Statement → canonical JSON → base64 payload → sign PAE) plus blobs in memory; assert a valid receipt verifies; altered payload byte, signature, corpus blob, scorer blob, record answer, binding evidence, unknown format, untrusted key each fail on the right field; `canonicalJSON` matches a few golden strings (write them down as literals computed with Python's `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, including non-ASCII values and nested arrays). Also test the table's row-filter function (export it from a module) refuses a row whose displayed ocs differs from the signed predicate.
6. Seed `web/data/` with an empty `rows.json` (`{"format": "operant.instruments-rows/v1", "rows": []}`) only. No fake rows committed.

## Out of scope
Python, the runner, deployment, editing Saagar's site repo.

## Acceptance
```sh
node --test web/test/
```
Do not push. Do not install packages.
