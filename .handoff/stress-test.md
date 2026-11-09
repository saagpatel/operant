# Open Sittings stress test

Read-only assessment of the pick and packets 00–05 against this checkout, 2026-11-08.
Only this report is changed. CheckSeal references below use the read-only sibling
checkout, abbreviated `checkseal/` (its `pyproject.toml:7` declares version 0.1.1).
The runner, scoring bridge, binding, receipt, review and web implementations are
not present here; contract risks below are requirements to resolve, not claims of
exploits in an implemented service. `sittings/manifest.py:27-34` already names the
future `sittings/scoring.py`; calling `scorer_manifest()` currently fails on that
missing file. No provider, private run data, home-directory contract, or network
was accessed.

## Decision

The pick's strong claim—proving which model really answered—is not established by
this design. A useful first release can certify that an operator's trusted key
signed particular public inputs, captured answers and a reproducible calculation,
with separately qualified relay observations. It cannot establish unseen-model
performance, provider truthfulness, general agent safety, or absence of answer-key
lookup. The main blockers are incomplete replay dependencies, score-admission
policy, browser canonicalization, and safe handling of historical scorer code.

## 1. Determinism and reproducibility

**The mathematical core is deterministic; the full existing entry points are not
functions of the proposed bound file lists alone.**

- `score_operant.py:241-269,283-355,358-411` parses answer text, applies fixed
  decision sets/refusal markers and case regexes, then counts and rounds results.
  These functions do not read files, environment variables, clocks, home directories
  or randomness. Their mutable module globals include `WITHHELD`, `PROCEEDED`,
  `VALID_LABELS` (`:59-61`), `REFUSAL_MARKERS` (`:77-151`) and
  `RISK_FRAME_MARKERS` (`:166`); normal fresh imports initialize them from bound
  source, but a reused interpreter can mutate them. `selfserve.classify_band`
  (`operant_lab/selfserve.py:378-400`) is also deterministic and returns a dict;
  the sitting's string band must select its `"band"` member.
- **Corpus environment escape:** `score_operant.load_cases()` chooses
  `$OPERANT_CASES` ahead of its repository glob (`score_operant.py:169-202`).
  `selfserve.load_axes(None)` preserves that inherited setting; explicit overrides
  mutate process-wide environment and later restore it, without a lock
  (`operant_lab/selfserve.py:175-199`). A threaded server must not rely on that
  save/restore as request isolation. A probe found 40 canonical decision cases,
  but setting the variable to an unmatched glob produced zero cases. Implement
  packet 01's manifest-only loader literally (`01-runner.md:10-18`): no ambient
  glob override, no shared environment mutation.
- **Contract/prompt escape:** `selfserve.resolve_operator_contract()` reads an
  explicit path, `$OPERANT_OPERATOR_CONTRACT`, or the personal home contract
  (`operant_lab/selfserve.py:141-162`); `run_operant.load_operator_contract()`
  directly reads the home contract (`run_operant.py:114-121`). These change the
  delivered prompt and thus potentially the captured answers, not arithmetic on
  already captured answers. The sitting's pinned-contract requirement correctly
  excludes these paths (`00-contract.md:49-52`, `01-runner.md:13-15`).
  `run_operant.build_system_prompt()` and `agent_runners.build_byo_prompt()`
  themselves only combine supplied strings (`run_operant.py:135-136`,
  `operant_lab/agent_runners.py:49-67`).
- **CLI side state:** the root scorer's aggregate mode reads the result index and
  filters it against local receipts (`score_operant.py:428-434`); single-case
  scoring may consult receipts inferred from the report filename
  (`:480-508`). That gate reads run receipts and checks global receipt lineage
  (`operant_lab/artifacts.py:2012-2056,2059-2109`; `lineage.py:625-636`). Those
  files affect admission, not `score_one()` arithmetic. Do not use this CLI or
  its local receipt gate to rescore a hosted sitting.
- **Other execution state:** sibling modules are cached by name and loaded from
  live `REPO_ROOT` (`operant_lab/selfserve.py:55-75`). Repointing only a scorer
  file or using a temp directory does not prove all imports came from the bound
  snapshot. Dispatch timing/timeouts and external programs affect which answer
  arrives (`operant_lab/agent_runners.py:151-165,380-406`;
  `run_operant.py:184-206`); summary timestamps use the clock
  (`operant_lab/selfserve.py:78-79,419-420`). None is an additional clock input
  to the fixed-answer core. Proposed secret-seeded case shuffling changes the
  experiment's order, not its confusion-matrix formula; record order as required
  (`01-runner.md:23-24`). A fresh rerun of an agent is not answer replay.

For reproducible scoring, freeze the corpus, contract, prompts and scorer snapshot
before serving the first case, and score from those same bytes at finish. The
current manifest helpers reread the live filesystem on every invocation
(`sittings/manifest.py:37-58`), so otherwise start/finish edits can produce a
receipt binding different bytes from those actually used. Historical verification
must use the recorded file list, not today's glob. Pin/document the Python runtime
(preferred patch: `.python-version:1`) and import environment too; source hashes
alone do not reconstruct Python/stdlib or attest loaded code.

## 2. Missing bound dependencies

The current corpus manifest contains exactly the pinned example contract and
`operant_cases.json`, `operant_axis2_cases.json`, `operant_axis4_cases.json`.
Those three files contain all 40 canonical decision cases. Axis 3 is excluded by
`sittings/manifest.py:17-23,37-41`. Templates and generated splits are not read by
the fixed-case scoring core, so they need not be added just because they exist.

| Missing file(s) | Actual dependency and impact |
| --- | --- |
| `operant_lab/subjects.py` | Imported at module load by bound `run_operant.py:51`; its `ClaudeCodeAdapter` is instantiated at `:56`. Not used by the direct string-building function, but necessary to import that module from a standalone snapshot. |
| `operant_lab/lineage.py` | Imported at module load by bound `operant_lab/artifacts.py:22-25`, itself imported by `score_operant.py:45-48`. Thus even pure scoring cannot import this exact tree from only the listed files. Its private-data reads are needed only if the CLI receipt gates are invoked. It also imports POSIX `fcntl` unconditionally (`operant_lab/lineage.py:5`), limiting replay portability. |
| `operant_lab/__init__.py`, `sittings/__init__.py` | Existing package initialization is outside the lists. Both currently contain docstrings only (`operant_lab/__init__.py:1-6`, `sittings/__init__.py:1-4`), so this is closure/future-drift exposure, not a current arithmetic dependency. |
| `sittings/canonical.py`, `sittings/manifest.py` | Define digest serialization and which files are bound (`canonical.py:14-25`, `manifest.py:13-58`), but neither is in `SCORER_FILES`. A bridge that imports them needs them during historical replay; the future bridge's exact imports cannot yet be checked. Bootstrap the manifest format independently or version and preserve these helpers explicitly. |
| `score_orchestration.py`, `operant_axis3_cases.json`, `run_orchestration.py` | `selfserve.load_axes()` loads both axes unconditionally (`selfserve.py:191-192`), and `build_system_prompts()` loads both prompt builders (`:165-172`). These are real extra reads/imports if those convenience APIs are reused, despite decision-only dispatch. A probe loaded 17 axis-3 cases through `load_axes()`. Avoid those APIs in the bridge rather than expanding the decision seal to axis 3. |
| `operant_lab/adapter_worker.py`, plus `score_my_agent.py` if wrapped as a CLI | Python adapters invoke the worker (`agent_runners.py:263-277`); self-serve input binding reads it and the orchestration scorer (`selfserve.py:107-125`). `score_my_agent.py:237-244` still loads both axes and prompt builders before deciding which axes to dispatch. These are client/CLI dependencies, not necessary for the proposed HTTP host's direct scoring path. |

`SCORER_FILES` is only six paths (`sittings/manifest.py:27-34`); its present list
is not a complete executable closure. Adding `subjects.py` and `lineage.py` fixes
two immediate missing imports, but the more conservative scope is a minimal
decision-only bridge plus an explicit tested replay closure. A successful replay
test must remove access to the live checkout and inherited module cache; using
unchanged live modules can mask all these omissions.

## 3. Score inflation and hosted attack surface

### Scores that a valid signature does not make meaningful

**Public answer lookup needs no model.** Cases publish IDs, prompts, classes,
expected decisions, rationales and bypass patterns (for example
`operant_cases.json:24-40`, `operant_axis2_cases.json:12-33`,
`operant_axis4_cases.json:12-28`). `/next` explicitly returns the ID
(`00-contract.md:178`). An outside client can return `DECISION:` plus the first
expected label. A zero-provider probe scored **OCS 1.0, accuracy 1.0, no bypass
failures, 40/40**. Shuffling does not stop a lookup keyed by ID or prompt.
The README already says the surface holdout does not prevent contamination
(`README.md:98-104`). A seal honestly binds the submitted answer bytes and their
calculation, not that an unassisted model solved unfamiliar tasks.

**Parse failures can inflate raw OCS.** `aggregate()` excludes unparseable answers
from both denominators (`score_operant.py:358-389`), despite its docstring saying
they cannot inflate OCS. A probe answering one guard and one benign case correctly,
with the other 38 answers nonempty but unparseable, produced **OCS 1.0, accuracy
0.05, unparseable 38**. Requiring one posted answer per case does not prevent this.
The existing self-serve CLI blocks incomplete, undefined or unparseable publication
(`operant_lab/selfserve.py:462-498`, `score_my_agent.py:199-206`). The sitting
score shape omits metric status, parse failures, exact accuracy and bypass failures
(`00-contract.md:73-74`), and packet 01 permits partial finish (`01-runner.md:27`).
Specify fail-closed public admission for a complete, uniquely identified,
parseable, two-class cohort; do not silently change the hash-bound formula.

**OCS is not exact-label accuracy or a bypass safety gate.** Its matrix uses only
class and `withheld`, never `safe_and_correct` or `bypass_used`
(`score_operant.py:327-355,365-410`). A second full-cohort probe selecting REFUSE
for every guarded case and PROCEED for every benign-open case scored **OCS 1.0
but accuracy 0.825**. A wrong REFUSE instead of ESCALATE still has the right
binary polarity (`operant_axis4_cases.json:13-18`). Bypass failures reduce the
separate safe/correct rate, not OCS. Even perfect exact-label lookup requires no
justification: extraction accepts a decision field/line, with the last valid label
winning (`score_operant.py:255-269`). Publish accuracy, parse/coverage and bypass
counts beside OCS; describe it as a decision-text diagnostic, not tested actions.

**Relay linkage does not establish independent reasoning.** Packet 02 allows
substring/near-substring linkage or a host-issued final call (`02-model-binding.md:20-25`).
A submitter can ask the genuine claimed model to repeat a precomputed correct
label. Exact linkage would then honestly establish that the observed completion
contained those answer bytes, while proving neither faithful use of the benchmark
prompt nor absence of an answer key. Substring matching is especially weak when
one response contains every possible decision label. Require exact whole-answer
linkage to a host-constructed current-case request for the strongest relay mode,
bound to sitting ID, case ID, prompt hash and call ID; still disclose public-corpus
contamination and provider self-report. Hashes and provider response IDs alone
cannot let a stranger reconstruct withheld request/completion contents
(`02-model-binding.md:38-40`). A locally signed observation is not a provider's
signature, and fingerprint consistency is explicitly not identity (`:29-33`).

### Host boundaries to enforce before deployment

- **No hosted adapter execution or arbitrary endpoint fetch.** The contract promises
  this (`00-contract.md:16-18`), but `score_my_agent.py:223-233` accepts all three
  local runner types. `make_runner()` selects subprocess, Python adapter, or
  arbitrary HTTP (`agent_runners.py:409-442`); subprocesses execute supplied argv
  (`:157-165`), adapters execute the worker (`:263-277`), and HTTP defaults to
  `urllib.request.urlopen` (`:326,380-387`). Keep these capabilities on the
  submitter's client only, as packet 01 says (`01-runner.md:30`). A literal hosted
  wrapper around the CLI would violate the contract.
- **Relay egress and secrets:** exact operator-controlled scheme/host/port/path
  selection, no redirects, credential/header scrubbing, bounded request and response
  sizes, timeouts and call quotas must hold for every route/error path
  (`02-model-binding.md:37-42`). Do not let a user-controlled URL suffix, provider
  path, proxy environment, DNS destination, or forwarded header bypass the
  allowlist. Reject non-finite/deep/malformed provider JSON. These are design
  review targets; no implemented relay exists here to demonstrate them.
- **State and resource abuse:** token auth, body caps, sequential answers, expiry,
  per-IP creation rate and open-sitting caps are useful (`00-contract.md:174-181`,
  `01-runner.md:23-29`), but do not bound concurrent connections, slow uploads,
  total retained answers/evidence, pending queue growth, repeated finishes, or
  distributed best-of-many attempts. Synchronize next/answer/finish/expiry per
  sitting, make finish idempotent, and set aggregate storage/connection/evidence
  budgets. Decide attempt reporting; review approval alone is not anti-cherry-pick
  evidence. TLS is excluded from packet 01 (`:33-34`), so bearer tokens and provider
  keys require a deployment transport boundary before real use.
- **Verifier code execution is a distinct high-risk boundary.** Packet 03 mandates
  importing historical scorer blobs from a temp directory (`03-receipt.md:15`).
  That directory is not a sandbox: imported Python can execute arbitrary code.
  Verify a pretrusted operator signature and an operator-approved scorer release
  before loading any code, or provide data-only verification and trusted offline
  replay. Never execute arbitrary uploaded/blob-selected Python just because its
  hash is internally consistent. Strictly validate relative paths, digest syntax,
  duplicate paths/IDs, symlinks and containment before materializing blobs or
  copying from `blob_source` (`05-review-queue.md:12-14`).
- **Publication trust:** approval needs real full verification, not just the stub
  used by queue tests (`05-review-queue.md:12-14`). Validate equality between all
  duplicated record/predicate/row fields, prompts regenerated from the snapshot,
  and the exact expected corpus/case set. Signature validity alone cannot detect
  a host that signs an internally wrong score. Separate atomic file writes also
  do not make a multi-file publication transactional; publish the row last after
  blobs/record/receipt are durable (`05-review-queue.md:12`). Preserve text-only
  rendering and constrain IDs/fetch paths (`04-web.md:13-14`); signed user text is
  still untrusted display content.

## 4. Contract/code and CheckSeal compatibility

1. **Canonical JSON is underspecified across Python and JS, even with ASCII keys.**
   The contract focuses on key ordering (`00-contract.md:38-43`), but Python's
   actual serializer (`sittings/canonical.py:14-17`) emits
   `{"fpr":0.0,"ocs":1.0}` while `JSON.stringify` emits
   `{"fpr":0,"ocs":1}`. Both forms were checked locally. Real scores contain
   those floats (`score_operant.py:379-406`). After JSON parsing a browser loses
   the distinction between `1` and `1.0`, so plain recursive key sorting cannot
   reproduce the record's canonical digest (`00-contract.md:133-134`,
   `04-web.md:12`). The signature must always verify the original decoded
   payload bytes, not a reserialization. Fix the canonical data domain/wire
   representation and golden vectors before separate Python/JS implementations;
   reject non-finite numbers, unsafe integers, duplicate keys and invalid Unicode.
   ASCII key restrictions must apply recursively, including relay evidence.
2. **DSSE envelope/PAE is compatible.** The required payload type and base64
   signature/payload shape match `checkseal/src/checkseal/dsse.py:16-22,41-54`.
   Lengths are UTF-8 byte lengths. `LocalKeySigner.sign(data)` signs exactly the
   supplied bytes (`sign/local.py:63-64`); explicitly pass `pae(type, payload)`,
   as CheckSeal does (`seal.py:55-60,74-77`). No different PAE is necessary.
3. **Key IDs have a specific format and are not authority.** CheckSeal computes
   `ed25519:` plus the first 16 hex characters of SHA-256 of the raw 32-byte public
   key (`sign/local.py:31-38`), not of PEM/SPKI or a full hex digest. Require the
   wrapper keyid, signature keyid and trusted key entry to agree; wrapper keyid is
   not itself in DSSE's signed bytes. CheckSeal's local verifier checks keyid
   against its key (`:78-85`); copied JS signature code does not do that mapping
   (`checkseal/js/checkseal_verify.mjs:114-123`). A same-origin public-key JSON
   file plus receipts proves consistency with that site's selected key, not
   independently anchored operator identity. Document a separately trusted key
   fingerprint/rotation policy if that stronger claim is wanted.
4. **The receipt is a custom OPERANT T1 format, not a public CheckSeal N1 seal.**
   CheckSeal explicitly reserves public N1 for T2 (`sign/local.py:1-4`), rejects
   public T1 in `seal.py:48-54`, and returns `attested_authority=False` for local
   verification (`sign/local.py:85`). The contract can reuse the primitive signer
   for its custom Statement; it must not call that N1 compliance or external
   attestation. `checkseal.seal.sign_local()` expects CheckSeal's Predicate, so
   it is not a drop-in signer for this custom predicate. The existing JS verifier
   expects `predicate.subject` and `predicate.checks`, unlike this contract
   (`checkseal_verify.mjs:77-78,94-105,133-135`). Reuse PAE/crypto, not that entire
   verifier. Its key import is raw (`:59-60,114-119`); packet 04's SPKI import
   (`04-web.md:12`) is correct only with the specified SPKI DER export.
5. **PEM helper permissions need extra handling.** Packet 03 requires 0600 keys
   (`03-receipt.md:14`), but `write_private_pem()` uses ordinary `open(...,"wb")`
   without setting mode (`checkseal/src/checkseal/sign/local.py:88-97`). Implement
   exclusive restrictive creation; merely reusing the helper does not meet it.
6. **Verification levels and signing time are claims, not independent proof.**
   Browser verification checks hashes/signatures but explicitly cannot rerun Python
   (`04-web.md:12`); its model-binding checks are weaker than semantic Python
   verification. `evidence_sha256` is an unkeyed consistency check. The site's
   green mark must state this scope. A T1 signature does not prove wall-clock
   `sealed_at`, immutable history or provider honesty. The pick's removal of the
   old caveat must not remove these necessary limits; `03-receipt.md:17` already
   requires disclosures, and `02-model-binding.md:40` requires provider limits.

## 5. Recommended scope cuts and open questions

Recommended first-release scope:

1. Keep decision-only scoring, one pinned public contract/corpus, a complete-cohort
   publication gate, and a small tested replay closure. Do not wrap the entire
   self-serve CLI or import orchestration convenience APIs on the host.
2. Ship an explicitly **operator-signed public-corpus result**. Keep `unbound`
   as a legitimate displayed level. Rename `relay-attested` to a precise
   host-observed/provider-reported level unless actual provider attestation exists.
   Cut fingerprints and near-verbatim linkage; if relay is necessary, support one
   allowlisted provider protocol and host-built exact final requests only.
3. Make browser verification signature/content verification with a visible
   not-rescored finding. Cut arbitrary historical-code execution on a hosted
   verifier; preserve blobs and allow replay only for trusted scorer releases in
   an appropriately isolated offline environment.
4. Cut claims of model certification, unseen-task validity, general safety,
   ranking, submitter identity and CheckSeal N1 compliance. Include coverage,
   exact accuracy and bypass counts, and retain contamination/relay/key limits
   alongside the sealed row. A seal remains useful for tamper detection and
   independently recalculating captured-answer results.

Open questions for the planner (interfaces were not edited):

- Is the desired product a signed submission/calculation receipt, or proof of
  served-model identity? Conservatively assessed as the former; the latter is
  not achieved by provider-reported metadata or probes in this design.
- Will public admission reject every partial/unparseable sitting and retain the
  original scorer formula, and where will coverage/accuracy/bypass diagnostics
  live in the shared schema? Conservatively recommend rejection, never silent
  OCS denominator changes.
- Which versioned replay dependency set and numeric canonicalization rules are
  authoritative? Current lists cannot support standalone replay; ASCII key
  sorting alone does not resolve cross-language number encoding.
- Which key is independently trusted, which scorer releases may execute, and
  what must a verifier do when record/blobs/binding verifier are unavailable?
  Conservatively report incomplete verification, not success or code execution.
- What evidence can be public enough to re-check exact relay request/answer
  linkage without leaking provider credentials or unrelated content? Current
  hash-only log requirements do not resolve this. Treat unresolved provenance
  as host observation, not independently verified provider identity.

## Verification performed

- Read-only source tracing of the named scorer, CLI, manifests, all three
  decision-corpus files, pick, packets 00–05, and CheckSeal 0.1.1 source. No live
  provider or hosted behavior was tested.
- `python3 -B -c '<in-memory scoring probes>'`: passed. Loaded the 40-case corpus,
  demonstrated the environment override, 17-case axis-3 convenience load, oracle
  1.0/100%, selective-parse 1.0/5%, binary-only 1.0/82.5%, and caught the expected
  missing `sittings/scoring.py` manifest error. No result files were written.
- Python/Node numeric serialization probes: passed and demonstrated the differing
  canonical bytes above. An initial heredoc form could not run because the shell
  could not create its temporary file; reran as `python3 -B -c` successfully.
- `uv run --offline --no-sync --no-cache ruff check sittings ./*.py`: passed using
  the available ambient Ruff; `ruff --version` reports 0.15.12, whereas
  `pyproject.toml:11` pins 0.15.22. This is not proof of frozen-environment lint.
  uv unexpectedly created an empty `.venv`; its exact generated files were
  inspected and that checker-created environment was removed.
- `ruff check --no-cache sittings ./*.py`: passed (0.15.12). No typecheck/build or
  broader test suite is needed for the sole Markdown change; no new tests added.
- Delivery acceptance: `test -s .handoff/stress-test.md` passed. `git diff --check`
  passed; `git status --short --untracked-files=all` lists only this report.
  `git diff --no-index --check /dev/null .handoff/stress-test.md` returned 1 with
  no diagnostics (the new file differs from `/dev/null`); a direct whitespace
  check is used for the untracked file. No git writes or delivery operations
  performed.
