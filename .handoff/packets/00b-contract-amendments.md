# 00b — Contract amendments after the stress test

`.handoff/stress-test.md` (Sol, read-only) found real gaps. These amendments override
`00-contract.md` where they conflict. Decisions are final for v1.

## A1. No floats in anything hashed or signed
Python emits `1.0`, JS emits `1`; a browser cannot reproduce Python's canonical bytes
for floats. Rule: every hashed or signed object (record, predicate, binding, evidence,
rows) contains only strings, booleans, null, lists, objects, and integers within
±(2^53−1). Rates (`ocs`, `tpr`, `fpr`, `accuracy`, `safe_correct_rate`) are strings
with exactly 6 decimals, e.g. `"0.825000"`, `"-0.100000"`. `canonical_bytes` raises on
float, NaN/Infinity, non-ASCII keys, or unsafe integers. Signatures are always checked
over the raw decoded payload bytes, never over a re-serialization. Golden vectors live in
`web/test/` and a Python test, generated from the same fixture.

## A2. Score object and public admission
`score` = `{ocs, tpr, fpr, accuracy, safe_correct_rate, n_cases, n_scored, n_unparseable,
n_unanswered, n_bypass_failures, band, publishable, not_publishable_reasons: []}`; the
formulas are `score_operant`'s, unchanged. `publishable` is true only when every case in
the bound corpus has exactly one accepted, parseable answer and both classes are present.
Partial finish (server `--allow-partial`) yields a non-publishable record. The review queue
refuses to approve a non-publishable record. The table shows accuracy, coverage, and
bypass failures beside OCS.

## A3. Two manifests, split by role, each a tested closure
- **corpus** ("what was asked"): the decision case files, the pinned operator contract,
  and the prompt-builder source closure (`run_operant.py`, `operant_lab/agent_runners.py`,
  and whatever they import at module load).
- **scorer** ("how answers became a score"): the minimal replay closure for
  `sittings/scoring.py:score_answers` — every repo `.py` file imported when scoring,
  including package `__init__.py`, `sittings/canonical.py`, `sittings/manifest.py`.
A test runs the replay in a fresh subprocess (`python -I`) whose working directory is a
temp snapshot containing **only** the scorer manifest files plus corpus case files, and
asserts identical score bytes; a test also asserts the set of repo files in
`sys.modules` after scoring equals the scorer manifest. Scoring must not read env vars
(`OPERANT_CASES`, `OPERANT_OPERATOR_CONTRACT`), the home directory, or call
`selfserve.load_axes` / `build_system_prompts` / `resolve_operator_contract`.

## A4. Bytes frozen per sitting
The runner computes both manifests and loads the corpus once at startup, holds them in
memory, and serves and scores every sitting from that snapshot. At finish it re-hashes
the live files; if they changed the record is marked non-publishable
(`"bound files changed during sitting"`).

## A5. Model-binding honesty
- No level may contain "attested" or "verified" unless an actual provider-signed
  artifact is checked. Levels are host observations.
- The strongest relay level requires the **host** to construct the exact final request
  for each case (the case prompt, sitting id, case id) and the recorded answer to equal
  the relayed completion text exactly. Substring / near-verbatim linkage is cut.
- `does_not_bind` always includes: the provider's honesty about what it served; that the
  answer did not come from a lookup of the public corpus (the corpus, expected decisions
  included, is public; a model told the answer can repeat it); anything about the
  submitter's setup outside relayed calls.
- Evidence that is public must not contain API keys or provider request headers beyond
  an allowlisted, non-secret set; request/response bodies are public for host-built
  calls (they are the case prompt and the answer), hashed otherwise.

## A6. Verifier never executes untrusted code
Python verify order: parse → keyid agreement (wrapper keyid == signature keyid ==
trusted key's CheckSeal keyid `ed25519:<first 16 hex of sha256(raw pubkey)>`) → Ed25519
over the PAE of the raw payload → only then anything else. Rescoring runs only when the
receipt's scorer sha256 is listed in `sittings/trusted_scorers.json` (operator-approved
releases; the current scorer is added by the integration packet) and runs in a
`python -I` subprocess inside a temp snapshot of the bound blobs; otherwise the rescore
finding is `ok: false, detail: "scorer release not trusted; not executed"` and the report
is incomplete (not ok). Paths in manifests are validated: relative POSIX, no `..`, no
absolute, no duplicates, digest is 64 lowercase hex; symlinks refused when materializing.

## A7. Names and claims
The receipt is an **OPERANT operator-signed T1 receipt using CheckSeal's DSSE and
Ed25519 primitives**. It is not a CheckSeal N1 public seal and docs must not say so.
Private key files are created exclusively with mode 0600. The pubkey file proves
consistency with the host's key, not independent operator identity; the doc publishes
the key fingerprint so a stranger can pin it out of band.

## A8. Runner hardening
Per-sitting lock around next/answer/finish/expiry; finish is idempotent (second call
returns the same record); global caps on open sittings, stored bytes, and request
concurrency; slow-body read timeout. TLS is the host's job (documented in the hosting
step); the runner refuses to bind a non-loopback address unless `--behind-tls-proxy`
is passed.
