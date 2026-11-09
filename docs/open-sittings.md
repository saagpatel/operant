# Open Sittings

An OPERANT sitting records answers to the public decision corpus, deterministically
scores those answers, and seals the record in an operator-signed T1 receipt using
CheckSeal's DSSE and Ed25519 primitives. It covers the decision axes only. A receipt
binds the published corpus and scorer bytes, the submitted answers, the reproducible
score, and the model binding at its stated level. It does not certify model identity,
general agent safety, performance on unseen tasks, or independent reasoning. The
corpus and expected decisions are public, so a submitter or model can look up answers.

## Sit the corpus

For an outside agent running on your machine, start the runner and use the client.
The client sends prompts to your local adapter, command, or endpoint; the runner
receives only the resulting answer text. The runner never executes submitter code.
Generate an operator key first. Key generation prints its fingerprint; publish and
pin that fingerprint through a separate trusted channel.

```sh
python -m sittings.receipt keygen --private ./operator-key.pem \
  --public ./operator-public.pem --pubkeys ./sittings-pubkey.json
python -m sittings.runner --data-dir ./sitting-data --key ./operator-key.pem
python -m sittings.client --runner http://127.0.0.1:8765 \
  --label my-agent --claimed-model model-name \
  --adapter path/to/agent.py:respond --out ./sitting-results
```

This produces an `unbound` sitting: the claimed model is display text, and no
provider was observed by the runner.

The client requires HTTPS for remote runners because sitting bearer tokens travel
with requests. Local HTTP is allowed only for the literal hosts `127.0.0.1`, `::1`,
and `localhost`. Unbound runs can explicitly opt into remote HTTP with
`--allow-insecure-runner`; this exposes their bearer tokens. Host-relayed runs
cannot use that override: provider keys require HTTPS except on those local hosts.

For host-relayed sittings, configure the runner's provider base URL as an operator.
The URL is selected by the provider name in `--relay-provider`; clients cannot choose
arbitrary URLs. Start with a key that is not exposed in shell history:

```sh
python -m sittings.runner --data-dir ./sitting-data --key ./operator-key.pem \
  --relay-provider acme=https://api.example/v1
read -s OPERANT_PROVIDER_KEY
export OPERANT_PROVIDER_KEY
python -m sittings.client \
  --runner https://sittings.example --label my-agent \
  --claimed-model model-name --relay-provider acme --relay-model model-name \
  --relay-key-env OPERANT_PROVIDER_KEY --out ./sitting-results
```

The runner makes one host-built, non-streaming OpenAI-compatible chat-completions
call per case and records the completion text exactly as returned. Its request
includes sitting and case context with the case prompt. The provider key remains in
runner memory from sitting creation until `/run` finishes, the sitting expires, or
the runner process exits.
Configure TLS at the hosting proxy; the
runner refuses a non-loopback bind unless started with `--behind-tls-proxy`. The
runner itself does not terminate TLS.

The only binding levels are `unbound` and `host-relayed`. `host-relayed` means every
answer exactly equals the corresponding completion text, all calls reported one
consistent model, and the runner built and sent each request to its configured
provider. A requested alias may differ from the provider's report. It does not mean
the provider's model report is independently verified. A claimed-model mismatch is
visible and makes the sitting non-publishable.

## What the receipt binds

The signed statement binds corpus and scorer manifests, the canonical digest of the
record and answers, the score, the binding, and the sealing time as recorded by the
host. The fixed seal claims bind the corpus bytes, scorer bytes, answers, recomputed
score, and model binding at its stated level. They do not bind the submitter's honesty
about setup beyond the binding, whether the agent was tuned on this public corpus,
the orchestration axis, submitter identity, or whether the local T1 signing key was
compromised. The public key file demonstrates consistency with the host key. It does
not establish independent operator identity; pin the key fingerprint through a
separate trusted channel.

For host-relayed evidence, request and response bodies are included so the prompt and
completion can be rechecked. Credentials and request headers are excluded. The
binding always says it does not establish provider honesty, public-corpus lookup, or
anything about the submitter's environment outside the relayed calls. Full fixed
language is shown in the receipt and at
[model binding](open-sittings-model-binding.md).

## Verify and publish

The browser verifier checks the signature, signed displayed fields, corpus/scorer
file hashes, answer digest, and binding structure available to the browser. It does
not execute Python or rescore. A green browser check therefore means the browser
subset passed; it is not a full receipt verification.

The full CLI verifier checks the receipt field by field. It parses the receipt,
requires the wrapper, signature, and trusted-key key IDs to agree, verifies Ed25519
over the original decoded DSSE payload bytes, compares the record and answer digests,
re-hashes every manifest file, validates model binding, and re-scores in an isolated
`python -I` subprocess. Rescoring runs only for scorer hashes listed in
`sittings/trusted_scorers.json`; unknown releases are not executed and verification
reports incomplete.

```sh
python -m sittings.receipt verify --receipt ID.receipt.json \
  --record ID.record.json --pubkeys sittings-pubkey.json \
  --blob-root web-data/blobs --repo-root .
```

Finished sittings enter a local review queue. Reviewers inspect the record, then
approve only a publishable sitting whose receipt passes full CLI verification.
Approval copies the bound bytes into content-addressed blobs and publishes the
receipt, record, public key, and table row under the chosen web data directory:

```sh
python -m sittings.review list --data-dir ./sitting-data --web-data ./web-data --state pending
python -m sittings.review approve ID --data-dir ./sitting-data \
  --web-data ./web-data --pubkey ./sittings-pubkey.json --blob-source .
```

Repeat `--pubkey` to trust multiple key documents during key rotation. Entries are
deduplicated by key ID; conflicting key encodings for the same ID are rejected.
Concurrent approval and unpublish commands share a file lock in the web data directory.

Publication is sealed-only. The public table shows OCS with accuracy, case coverage,
bypass failures, binding level, and the claimed model. Review approval does not make
the result independent, confirmatory, or resistant to public-answer lookup.

## Receipt and scorer releases

The receipt format is `operant.sitting-receipt/v1`; the record format is
`operant.sitting/v1`. Receipt verification preserves each bound corpus and scorer
file as content-addressed bytes, so a later scorer update does not rewrite history.
Adding a scorer hash to `sittings/trusted_scorers.json` is an explicit operator
decision to permit that exact replay closure to execute during CLI verification.
Keep old trusted releases only when their provenance and review remain acceptable.

## Runner configuration and limits

The runner flags are `--host`, `--port`, `--data-dir`, `--key`, `--ttl-seconds`,
`--create-rate-limit`, `--rate-window-seconds`, `--max-open-sittings`,
`--allow-partial`, `--behind-tls-proxy`, repeatable `--relay-provider NAME=BASE_URL`,
`--max-stored-bytes`, and `--max-request-concurrency`. Bind to loopback for local development. For a
public deployment, place it behind a TLS proxy and pass `--behind-tls-proxy` only
when that boundary is configured. Provider base URLs are operator-controlled; do
not configure submitter-controlled endpoints.

Bodies are capped at 64 KiB and answers at 20,000 characters. The runner applies
creation, open-sitting, request-concurrency, stored-byte, per-sitting call, response
size, and timeout limits. It does not dispatch an agent or orchestration judge. A
partial finish may be retained for review, but it is non-publishable. Provider
failures do not create a successful relay binding. Logs and errors must never include
keys, authorization headers, tokens, or raw provider errors.

Defaults are a two-hour TTL, ten creations per peer per hour, 100 open sittings,
256 MiB of stored data, and 32 concurrent requests. Body reads time out after
30 seconds of inactivity. Each upstream call has a 30-second deadline and a 1 MiB
response limit; the host makes one call per corpus case.

For the process-level offline demonstration, run `uv run python -m sittings.e2e_local
--workdir ./local-demo` with a fresh directory. It runs the outside heuristic agent
and a clearly labeled fake OpenAI-compatible test provider, publishes both through
review, verifies their receipts, and rejects a tampered copy. It requires permission
to bind loopback sockets and makes no paid provider calls.
