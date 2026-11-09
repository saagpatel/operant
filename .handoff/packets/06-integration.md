# 06 — Python integration: apply the amendments and wire the sitting end to end

Read `.handoff/packets/00-contract.md`, then `.handoff/packets/00b-contract-amendments.md`
(it overrides 00 where they conflict, including A9's decided binding levels), then
`.handoff/stress-test.md`. Packets 01/02/03/05 are merged on this branch: `sittings/scoring.py`,
`sittings/runner/`, `sittings/client.py`, `sittings/model_binding/`, `sittings/receipt/`,
`sittings/review/` and their `test_sittings_*.py`. You own all of `sittings/` and those tests
now. Do not touch `web/` (another packet), `lab/public/`, `mcp/`, `score_orchestration*.py`.

## Goal
One coherent v1: a sitting created on the runner ends as a signed receipt in the review
queue; approval publishes it into `web/data/`; `python -m sittings.receipt verify` re-checks
it field by field, including a sandboxed rescore. Every amendment A1–A9 holds and has a test.

## Work
1. **A1 canonical numbers**: `sittings/canonical.py` rejects floats, NaN/Inf, non-ASCII keys,
   unsafe ints (you may edit it now). Convert every rate to a 6-decimal string. Add
   `test_sittings_canonical.py` with golden vectors; also write the same vectors to
   `sittings/testdata/canonical-vectors.json` (input JSON text → expected canonical text →
   sha256) so the web packet can test against them.
2. **A2 score + admission**: extend `score_answers` to the A2 shape; `publishable` logic;
   review `approve` refuses non-publishable records.
3. **A3 manifests**: split corpus (cases + contract + prompt-builder closure) and scorer
   (replay closure) in `sittings/manifest.py`. Make `sittings/scoring.py` a minimal replay
   closure (it may stop importing `operant_lab.selfserve` if that drags in more; a small local
   `classify_band` copy is fine if documented and bound). Tests: `sys.modules` repo files after
   scoring == scorer manifest; replay in `python -I` subprocess in a temp snapshot of only the
   scorer files + case files yields identical canonical score bytes.
4. **A4** freeze at runner startup; stale-bytes check at finish.
5. **A5/A9 binding**: rename levels to exactly `host-relayed` and `unbound`; cut agent-routed
   relay and substring linkage from the code path the runner uses (keep nothing dead: delete
   unused code). Add a host-relayed sitting mode to the runner: `POST /api/sittings` with
   `relay: {provider: <allowlisted name>, model, api_key}`, then `POST /api/sittings/<id>/run`
   makes the runner itself call the provider once per case with the host-built request
   (OpenAI-compatible chat completions; provider base URLs come only from server config
   `--relay-provider name=https://host/v1` flags), records answers = completion text exactly,
   and builds the binding from the relay log. The key lives only in memory for the duration of
   `run` and appears nowhere on disk, in logs, records, evidence, or errors (grep test).
   Claimed≠observed → non-publishable with the reason shown. Fixed `binds`/`does_not_bind`
   text per A5. Client gets `--relay-provider NAME --relay-model M --relay-key-env VAR`.
6. **A6 verifier**: the exact order in A6; keyid agreement; `sittings/trusted_scorers.json`
   with the current scorer sha256 (and a test that it matches `scorer_manifest()`, so a scorer
   change forces a deliberate update); rescore only for trusted releases, in a `python -I`
   subprocess in a temp snapshot built from blobs or the repo at the bound hashes; strict path
   and digest validation; symlinks refused. Finish 03's missing tests: the full tamper matrix
   from packet 03 and the old-scorer-via-blobs case (old scorer listed as trusted in the test's
   own trusted file).
7. **A7**: key files created with `os.open(..., O_CREAT|O_EXCL, 0o600)`; wording in code and
   CLI output says "operator-signed T1 receipt (CheckSeal DSSE + Ed25519)"; `keygen` prints the
   key fingerprint.
8. **A8 runner hardening** as listed; refuse non-loopback bind without `--behind-tls-proxy`.
9. **Wiring**: `python -m sittings.runner --data-dir D --key K` plugs binding, receipt
   (sealing at finish) and review (enqueue) hooks; `--web-data` is NOT a runner flag
   (publishing happens only through `python -m sittings.review approve`).
10. **Local end-to-end script** `sittings/e2e_local.py` (`uv run python -m sittings.e2e_local
    --workdir DIR`): generates a key, starts the runner on 127.0.0.1:0 in a subprocess, sits
    the full corpus with `examples/heuristic_agent.py` through `sittings.client` as a separate
    process (the outside agent), runs a host-relayed sitting against a local fake
    OpenAI-compatible provider process (clearly labeled a test provider in output), approves
    both through `sittings.review` into `DIR/web-data`, runs `sittings.receipt verify` on both,
    tampers a copy of one receipt and shows verify fails, and prints a summary. Also a test
    that runs it.
11. `docs/open-sittings.md`: what a sitting is, how to sit (unbound agent via client;
    host-relayed endpoint), the seal's exact binds/does-not-bind (incl. public-corpus lookup),
    levels, verification steps (browser subset vs full CLI), review flow, receipt versioning
    and trusted scorer releases, runner flags and limits. Merge or link
    `docs/open-sittings-model-binding.md` so they agree (A9 levels).

## Sandbox notes
Your sandbox cannot bind loopback sockets and cannot reach the network. Write socket tests
anyway (they run outside the sandbox at review); verify logic with in-process fakes. The uv
cache is writable; `uv run` works offline for the locked env.

## Acceptance (run outside the sandbox by the reviewer; run what you can)
```sh
uv sync --frozen
OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest discover -p 'test_*.py'
uv run ruff check sittings ./*.py
uv run python -m sittings.e2e_local --workdir "$TMPDIR/os-e2e"
```
