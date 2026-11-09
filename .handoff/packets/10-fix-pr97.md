# 10 — Fix PR 2/4 review findings (runner, client, review queue)

Branch already contains the runner/client/review/e2e code. Fix each item with a regression test.

1. **major** `sittings/client.py` ~67: remote `http://` runner URLs are accepted and host-relayed creation
   sends `relay.api_key` over them. Require `https://` for any runner URL when relay options are used,
   except literal loopback (`127.0.0.1`, `::1`, `localhost`) HTTP for local tests. Validate before any
   request carrying credentials. Tests for both cases. (Unbound sittings over http to non-loopback: also
   refuse unless an explicit `--allow-insecure-runner` flag is given; bearer tokens travel too.)
2. **major** `sittings/review/queue.py` ~306: publication does an unlocked read-modify-write of
   `rows.json`; concurrent approvals/unpublish can lose rows. Serialize publish/unpublish with an
   inter-process lock (`fcntl.flock` on a lock file inside the web-data dir) covering rows and pubkey
   read/merge/write. Regression test with concurrent approvals (threads or processes) — every approved
   row remains.
3. **minor** `sittings/review/queue.py` ~216: multiple `--pubkey` files produce a list passed to
   `verify_receipt`, which expects one pubkey document. Validate and merge key documents (dedupe by keyid,
   reject conflicting key bytes for the same keyid) before verifying. Test approval with two key files.
4. CodeQL threads: `sittings/runner/__main__.py` ~75 possibly-uninitialized `config`; empty `except`
   at `sittings/runner/__main__.py` ~82 and `sittings/runner/server.py` ~508 (log or comment why it is
   safe, never swallow silently); `test_sittings_integration.py` ~41 mixed implicit/explicit returns;
   `test_sittings_runner.py` ~57 use `assertLess`.

Update `docs/open-sittings.md` / `docs/open-sittings-hosting.md` if CLI behavior changes. If any scorer
manifest file changes, update `sittings/trusted_scorers.json` to the new hash only (its test enforces it).
Sandbox: no sockets or network; uv cache writable. Socket tests run at review.

## Acceptance
```sh
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest discover -p 'test_*.py'
uv run ruff check sittings ./*.py
```
