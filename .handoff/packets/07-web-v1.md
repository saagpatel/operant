# 07 — Bring the web table and verify page to the v1 formats

Read `.handoff/packets/00-contract.md`, then `.handoff/packets/00b-contract-amendments.md`
(overrides 00), then `docs/open-sittings.md`. `web/` was built against 00 only, before the
amendments; the Python side (`sittings/`) now implements v1 and is the source of truth for
formats. Edit only `web/` (and `web/test/`). Do not edit Python. Do not commit or push.

## Work
1. **A1**: `canonicalJSON` must reject floats, non-ASCII keys and unsafe integers, and match
   every vector in `sittings/testdata/canonical-vectors.json` (load that file in the test).
   Signature checks use the raw decoded payload bytes. Rates are 6-decimal strings; display
   them as given (e.g. `0.825000` → show `0.825`), never via float math that changes them.
2. **A2**: the table shows OCS, accuracy, coverage (`n_scored`/`n_cases`), bypass failures,
   band; refuses rows whose record is not `publishable`; the verify page shows
   `not_publishable_reasons` if any.
3. **A6/A7**: keyid agreement (wrapper keyid == signature keyid == trusted key keyid, where the
   CheckSeal keyid is `ed25519:` + first 16 hex chars of sha256 of the raw 32-byte public key —
   derive raw bytes from the SPKI DER); show the key fingerprint; wording "operator-signed T1
   receipt (CheckSeal DSSE + Ed25519)"; never "CheckSeal seal" or "verified model".
4. **A9 levels**: exactly `host-relayed` and `unbound`; for `host-relayed` the verify page
   re-checks in-browser that each answer equals the recorded completion text of its relay call
   in the evidence and that evidence hashes match; show `binds` and `does_not_bind` verbatim.
5. The browser cannot rescore: show that finding as "not checked in browser" with the exact
   CLI command `uv run python -m sittings.receipt verify ...` (read the CLI's real flags from
   `sittings/receipt/__main__.py`).
6. **Real fixtures**: run `uv run python -m sittings.e2e_local --workdir web/test/fixtures/e2e`
   is NOT possible in your sandbox (no sockets). Instead the reviewer has placed a real
   published `web-data` tree at `web/test/fixtures/web-data/` (two sittings: one `unbound`,
   one `host-relayed` against a local test provider) and its pubkey. Make `node --test web/test/`
   verify both real receipts end to end (signature, keyid, corpus and scorer blobs, record digest,
   answers digest, binding evidence) and reject tampered copies you create in the test, field by
   field. Also test that the table's row filter accepts both real rows and refuses a row with an
   altered displayed field.
7. Keep: no external scripts, text-only DOM writes, 375px layout, light/dark.

## Acceptance
```sh
node --test web/test/
```
