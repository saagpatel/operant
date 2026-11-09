# 02 — Served-model binding (research-hard core)

Read `.handoff/packets/00-contract.md` first; it is binding.

## Goal
Give each sitting the strongest honest statement available about *which model actually
produced the answers*, record precisely what that statement proves and what it does not,
and make it re-checkable by a stranger. Overclaiming is the failure mode that matters
most: a binding that says more than its evidence supports is a bug, a weak binding that
says exactly what it is, is correct.

## The problem, honestly
The agent runs on the submitter's machine. The host never sees its weights or its
process. Any signal is one of:
- **Relay**: the agent sends its model calls through the runner, which forwards them to
  an operator-allowlisted upstream provider (OpenAI-compatible `/v1/chat/completions`
  first; Anthropic Messages optional) with the submitter's own API key passed per
  request and never stored or logged. The runner then *observes* the provider's
  response (`model` field, response id, headers such as `openai-model` / request ids,
  usage). This proves what the upstream *said* it served for calls that went through the
  relay. It does not prove the agent's final answer came from those calls, unless you
  add a linkage check; design one (for example: the answer must be a verbatim or
  near-verbatim substring of a relayed completion for that case, with the matched call
  id recorded; or the runner itself makes the final call so the answer *is* the relayed
  completion). Decide which modes exist and name them precisely.
- **Provider attestation**: some providers return verifiable artifacts (signed response
  headers, response ids retrievable later via the provider API). Use only what you can
  verify offline in tests; describe the rest as future work rather than claiming it.
- **Fingerprint probes**: a small set of controlled probe prompts interleaved by the
  runner, whose answers are compared with known per-model behavior. At best this is
  "consistent with", never identity. Include it only if you can make it honest and
  deterministic; it is optional.
- **Nothing**: `unbound`, the floor, always available, always labeled.

## Scope
1. `sittings/model_binding/` with the API in the contract: `build_binding(record_without_binding, relay_log)` and `verify_binding(binding, record)`, plus the relay:
   - `relay.py`: a handler factory the runner mounts through `RunnerConfig(extra_routes=...)` (packet 01 defines that hook; in your worktree it does not exist yet, so build the relay against a tiny local stand-in and keep the integration surface a plain function `make_relay_routes(config) -> list[(method, path_regex, handler)]` where `handler(request) -> response` uses simple dicts — document the exact shape so the integration packet can wire it).
   - Relay config: an operator allowlist of upstream base URLs (default empty → relay disabled), per-sitting call cap, response size cap, timeout. Submitter keys are forwarded in the upstream `Authorization` header and nowhere else: not in logs, records, evidence, errors, or exceptions. Strip hop-by-hop and identifying headers. No redirects followed. No upstream other than the allowlist, ever (no SSRF via `base_url` in the request).
   - `relay_log` entries record what is needed for evidence: call id, case id it was attributed to, upstream host, requested model, response `model`, provider response id, selected provider headers, sha256 of the request body and response body, timestamps. Evidence must let a verifier re-check consistency (hashes, linkage of answers to relayed completions, that observed models agree with each other and with the claim) without the API key.
2. Levels (refine names if you have a better precise set, and document them): something like `relay-attested` (every answer is linked to a relayed completion and all relayed calls report the same model), `relay-observed` (calls went through the relay but linkage is partial), `unbound`. Each level has fixed `binds` / `does_not_bind` text. Mismatch between `claimed_model` and the observed model must be visible (do not silently pick either). Always include in `does_not_bind`: that the upstream provider is honest about what it served; anything about the submitter's setup beyond the relayed calls.
3. `docs/open-sittings-model-binding.md`: the threat model, each level, exactly what it binds and does not, known attacks and which ones each level stops (e.g. agent ignores relayed output and answers from another model; replayed completions from another sitting; a malicious allowlisted upstream; a submitter who controls their own "provider" endpoint — only real-provider hosts belong on the allowlist and the doc says why), and what would be needed for stronger binding later.
4. `test_sittings_model_binding.py`: a fake upstream (local `http.server` on 127.0.0.1:0, allowlisted in tests) that reports a model; full relay round trip; key never appears in logs/evidence/exceptions (grep every produced byte); non-allowlisted upstream refused; redirect not followed; linkage success and failure; claimed≠observed shown; mixed observed models downgrade the level; tampering with any evidence field or `evidence_sha256` makes `verify_binding` fail on that field; `unbound` verifies and says what it does not bind.

## Out of scope
The runner server itself (01), signing (03), web (04). Do not edit files owned by other packets.

## Acceptance
```sh
uv run python -m unittest test_sittings_model_binding -v
uv run ruff check sittings ./*.py
```
