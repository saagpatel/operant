# 00 — Open Sittings shared contract

Every packet reads this first. It fixes the interfaces so packets can be built in
parallel. Do not change an interface here inside a packet; if one is wrong, say so in
your report's open questions and work around it locally.

## What we are building

OPERANT Open Sittings: an outside agent "sits" the OPERANT **decision corpus** (axes
1/2/4, scored deterministically by `score_operant.py`; the orchestration axis and its
LLM judge are out of scope) through a small hosted runner. The runner serves prompts,
collects answers, scores them, records a served-model binding, and emits a signed,
versioned receipt. Approved receipts appear on a sealed-only public table; a verify
page re-checks any receipt field by field.

Outside agents only answer prompts. Nothing a submitter sends is ever executed on the
host: no code, no commands, no adapters, no URLs fetched on their behalf except the
operator-allowlisted upstreams of the relay (packet 02).

## Layout (who owns what)

| Path | Owner packet |
|---|---|
| `sittings/__init__.py`, `sittings/canonical.py`, `sittings/manifest.py` | contract (exists; do not edit) |
| `sittings/scoring.py`, `sittings/runner/`, `sittings/client.py`, `test_sittings_runner.py` | 01 |
| `sittings/model_binding/`, `test_sittings_model_binding.py` | 02 |
| `sittings/receipt/`, `test_sittings_receipt.py` | 03 |
| `web/` (instruments table, verify page, `web/lib/`) | 04 |
| `sittings/review/`, `test_sittings_review.py` | 05 |
| `docs/open-sittings.md`, README section, e2e script | 06 (integration) |

Tests are root-level `test_*.py` files so `python -m unittest discover -p 'test_*.py'`
(part of `make check`) picks them up. Python 3.12, stdlib only, except the receipt
packet which may use `checkseal[sign]==0.1.1` (Ed25519 via `cryptography`).

## Canonical bytes

`sittings/canonical.py`: canonical JSON is
`json.dumps(v, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
`canonical_sha256(v)` is the hex sha256 of those bytes. The browser verifier must
reproduce them exactly (sort object keys by UTF-16 code unit order is NOT the same as
Python's code point order for astral characters; keys in this format are ASCII, so
plain sort is fine — keep keys ASCII).

## Bound bytes

`sittings/manifest.py`: `corpus_manifest()` and `scorer_manifest()` each return
`{"sha256": <aggregate>, "files": [{"path", "sha256", "bytes"}]}`. The aggregate is
`canonical_sha256([{"path", "sha256"}, ...])` over files sorted by path. Corpus = the
decision case files plus the pinned operator contract `examples/example-operator-contract.md`
(the runner always uses that contract; never `~/.claude/CLAUDE.md`). Scorer = the
files that build prompts and score answers, including `sittings/scoring.py`.

## Sitting record — `operant.sitting/v1`

Written by the runner (01) at finish, as `<data_dir>/sittings/<sitting_id>/record.json`.

```json
{
  "format": "operant.sitting/v1",
  "sitting_id": "sit_<26 lowercase base32 chars>",
  "created_at": "2026-11-08T20:00:00Z",
  "finished_at": "2026-11-08T20:20:00Z",
  "agent_label": "<submitter string, <=80 chars, printable>",
  "claimed_model": "<submitter string, <=120 chars; display only, never trusted>",
  "corpus": {"sha256": "...", "files": [...]},
  "scorer": {"sha256": "...", "files": [...]},
  "case_order": ["<case_id>", "..."],
  "answers": [
    {"case_id": "...", "prompt_sha256": "...", "answer": "<text, <=20000 chars>",
     "received_at": "...", "relay_call_ids": ["..."]}
  ],
  "score": {"ocs": 0.0, "tpr": 0.0, "fpr": 0.0, "n_scored": 0, "n_cases": 0,
            "n_unanswered": 0, "band": "<selfserve.classify_band label>"},
  "model_binding": { "...": "operant.model-binding/v1 object, see below" }
}
```

`answers_sha256` (used by the receipt) is `canonical_sha256(record["answers"])`.
`score` must be exactly reproducible by `sittings.scoring.score_answers(case_map, answers)`
from the bound corpus and answers. `relay_call_ids` is `[]` when no relay was used.

## Model binding — `operant.model-binding/v1`

Produced by `sittings.model_binding` (02). The runner calls it; the receipt embeds it
verbatim; the verifiers re-check it.

```json
{
  "format": "operant.model-binding/v1",
  "level": "relay-attested | relay-observed | fingerprint-consistent | unbound",
  "claimed_model": "...",
  "observed_model": "<what the upstream reported, or null>",
  "binds": ["plain-English statements of exactly what this binding establishes"],
  "does_not_bind": ["plain-English statements of what it does not establish"],
  "evidence": { "...": "level-specific, every item re-checkable" },
  "evidence_sha256": "<canonical_sha256(evidence)>"
}
```

Level names are owned by packet 02 and may be refined there, but `binds`,
`does_not_bind`, and `level` are always present, and `unbound` always exists as the
honest floor. Verifiers display `binds` and `does_not_bind` verbatim; they never print
a stronger claim than `level` supports.

Python API (02 exports from `sittings/model_binding/__init__.py`):

- `build_binding(record_without_binding: dict, relay_log: list[dict] | None) -> dict`
- `verify_binding(binding: dict, record: dict) -> list[Finding]` where
  `Finding = {"field": str, "ok": bool, "detail": str}`
- The relay pieces the runner mounts: see packet 02.

## Receipt — `operant.sitting-receipt/v1`

Produced by `sittings.receipt` (03) using CheckSeal's DSSE + Ed25519 local signer
(T1). File `<sitting_id>.receipt.json`:

```json
{
  "receipt_format": "operant.sitting-receipt/v1",
  "keyid": "<checkseal LocalKeySigner keyid>",
  "envelope": {"payloadType": "application/vnd.in-toto+json",
               "payload": "<base64 of the Statement bytes>",
               "signatures": [{"keyid": "...", "sig": "<base64 Ed25519 over DSSE PAE>"}]}
}
```

Statement (the signed payload, canonical JSON bytes):

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [{"name": "operant-sitting:<sitting_id>",
               "digest": {"sha256": "<canonical_sha256(record)>"}}],
  "predicateType": "https://operant.saagarpatel.dev/sitting-receipt/v1",
  "predicate": {
    "receipt_format": "operant.sitting-receipt/v1",
    "sitting_id": "...", "agent_label": "...", "claimed_model": "...",
    "sealed_at": "...",
    "corpus": {"sha256": "...", "files": [...]},
    "scorer": {"sha256": "...", "files": [...]},
    "answers_sha256": "...",
    "score": {"...": "same object as record.score"},
    "model_binding": {"...": "same object as record.model_binding"},
    "seal_binds": ["corpus bytes", "scorer bytes", "the answers", "the score recomputed from them", "the model binding at its stated level"],
    "seal_does_not_bind": ["see docs/open-sittings.md; fixed list owned by 03"]
  }
}
```

DSSE PAE is the standard `DSSEv1 <len(type)> <type> <len(payload)> <payload>` that
`checkseal/dsse.py` and `checkseal/js/checkseal_verify.mjs` implement. Reuse them; do
not hand-roll a different PAE.

## Content-addressed blobs and the public data directory

The verify page re-hashes bound files itself. On approval, review (05) publishes into
`web/data/` (the static site's data root):

- `web/data/blobs/<sha256>` — raw bytes of every corpus and scorer file a published
  receipt binds (so old rows stay verifiable after the scorer changes).
- `web/data/receipts/<sitting_id>.receipt.json` — the receipt.
- `web/data/records/<sitting_id>.record.json` — the full sitting record (answers
  included; submitters consent to publication at sitting creation).
- `web/data/rows.json` — `{"format": "operant.instruments-rows/v1", "rows": [ {"sitting_id", "agent_label", "claimed_model", "ocs", "tpr", "fpr", "band", "binding_level", "sealed_at", "receipt": "receipts/<id>.receipt.json", "record": "records/<id>.record.json"} ]}`
- `web/data/sittings-pubkey.json` — `{"format": "operant.sittings-pubkey/v1", "keys": [{"keyid", "alg": "ed25519", "public_key_pem", "spki_der_b64"}]}`

The table (04) renders a row only after it verifies that row's receipt signature and
that the row's displayed fields equal the signed predicate. A row that fails is not
rendered; the page shows a count of refused rows.

## Runner HTTP API (packet 01; relay routes added by 02)

All JSON. Request bodies capped at 64 KiB. `Authorization: Bearer <sitting token>` on
everything after create. Unknown routes 404, wrong method 405, bad token 401.

- `POST /api/sittings` `{agent_label, claimed_model, consent_publish: true, relay?: {...}}` → `201 {sitting_id, token, total_cases, corpus_sha256, scorer_sha256}`
- `GET /api/sittings/<id>/next` → `{done: false, index, case_id, prompt}` or `{done: true}`
- `POST /api/sittings/<id>/answers` `{case_id, answer}` → `{accepted: true, remaining}` (only the case `next` last served; one answer per case; re-POST is 409)
- `POST /api/sittings/<id>/finish` → `{sitting_id, record, receipt|null, review_status}`
- `GET /api/health` → `{ok: true, corpus_sha256, scorer_sha256}`

## Ground rules for every packet

- No network in tests. Bind servers to `127.0.0.1` with port 0.
- No real paths, names, or emails in code, fixtures, or commit messages.
- Do not touch `lab/public/`, `mcp/`, `score_orchestration*.py`, `results/`, `lab/runs/`.
- Run `uv run ruff check sittings ./*.py` and your packet's tests before reporting done.
