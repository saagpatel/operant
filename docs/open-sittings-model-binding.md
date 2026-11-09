# Open Sittings model binding

This binding answers a narrow question: what did an operator-allowlisted upstream
report, and do the recorded answers equal its recorded completion text? It cannot
inspect the outside agent's process or weights. The claimed model is untrusted
display text. Provider response IDs and model headers are reports, **not verified
provider attestations**. No fingerprint or signed-provider mode is implemented.

## Levels and fixed claims

`sittings/model_binding/__init__.py` owns the fixed `binds` and `does_not_bind`
lists. Verification checks those exact lists; presentation must display them
verbatim. Changing their wording requires versioning the interpretation contract.

| Level | Required evidence | What it establishes |
| --- | --- | --- |
| `unbound` | No successful relay calls | No model observation. The record and its claim are hash-linked only. |
| `relay-observed` | At least one valid recorded relay response | Provider-reported metadata and bytes for calls attributed to this sitting; individually reproducible answer links and claim comparison. Coverage, linkage or model agreement may be incomplete. |
| `relay-linked` | Every scheduled case answered; every nonempty answer exactly equals a complete assistant message in a declared call for the same sitting and case; every evidenced call reports one identical nonempty model, including any `openai-model` header | Exact text linkage and consistent provider reports. A claimed-model mismatch is still displayed. |

There is deliberately no `relay-attested` level: neither the relay nor an
unsigned provider response attests weight identity. Exact equality is used instead
of substrings or approximate matching: a short decision label within a long output
is too weak a linkage criterion. Any assistant choice may match; tool calls,
non-text content, empty answers, partial substrings and whitespace rewrites do not.
A call's case is assigned by the authenticated runner adapter, not the submitter.
This attribution does **not** establish that its request contained the case prompt.

All levels explicitly exclude provider honesty, the submitter's setup beyond
relayed calls, causal authorship, other models or human help, and authenticity,
freshness or immutable history of unsigned evidence. The linked level additionally
excludes provider attestation and proof that the output caused the answer. The
observed level excludes complete linkage to one model. The unbound level excludes
any provider observation or answer-to-model linkage.

`observed_model` is the sole unique nonempty report, or null when reports are absent
or mixed. `evidence.observed_models` includes response models and selected model
headers, sorted without alias normalization. Missing/empty body model reports
prevent `relay-linked`, even if another call or header supplies a name.
`model_reports_complete` records whether every call supplies a body model and any
recorded model header is nonempty. `claim_status` is `match`, `mismatch`, `mixed`,
or `unknown`: exact claim comparison is meaningful only for complete, consistent
reports. A mismatch does not erase the observed model or imply that aliases are
equivalent. Mixed reports always downgrade to `relay-observed`.

## Plain-dict runner integration

The runner hook is outside this packet. Import:

```python
from sittings.model_binding import RelayConfig, make_relay_routes

config = RelayConfig(
    allowed_base_urls=("https://provider.example/v1",),
    max_calls_per_sitting=80,
    max_response_bytes=1_048_576,
    timeout_seconds=30.0,
)
routes = make_relay_routes(config)
```

The placeholder host above is illustrative, not a recommended provider.
An empty allowlist (the default) returns no routes. Policy and limits are snapshotted
by the factory. Create the route factory once per runner, not per HTTP request;
its closure owns thread-safe in-memory attempt counts. Successful log entries are
appended under the same lock to `config.relay_log`. Failures after validation also
consume the call cap. Restarting the runner resets the counters; the integration
must restore/enforce sitting budgets if sittings survive restarts. A successful
call whose client disconnects still consumes budget and remains in the log.

Each route is `(method, path_regex, handler)`:

```text
POST /api/sittings/(?P<sitting_id>sit_[a-z2-7]{26})/relay/chat/completions
```

The adapter **full-matches** the regex. It first enforces method, request body cap,
the existing sitting-token authentication, a live unfinished sitting, current
served case, sitting ownership and concurrency policy. It then constructs this
trusted request, with `sitting_id` and `case_id` supplied from runner state:

```python
{
    "sitting_id": "sit_" + "a" * 26,
    "case_id": "synthetic-case.malign",
    "headers": {"X-Provider-Key": "<per-request credential>"},
    "body": {
        "base_url": "https://provider.example/v1",
        "completion": {
            "model": "requested-model-or-alias",
            "messages": [{"role": "user", "content": "<prompt>"}],
            "stream": False,
        },
    },
}
```

The public JSON body has exactly `base_url` and `completion`. The adapter must
reject attempts to inject trusted context fields. The sitting `Authorization`
header remains the runner credential; the submitter's provider key comes from
`X-Provider-Key`, case-insensitively. The adapter must not persist, access-log,
trace, include in exceptions, or reflect that header or the original request
object. It must also prevent raw submitted answers or claimed model strings that
echo the credential from being persisted elsewhere: the relay cannot sanitize
runner endpoints it does not own. Never place a key in a URL or completion body.

The handler returns:

```python
{
    "status": 200,
    "headers": {"Content-Type": "application/json"},
    "body": {"call_id": "call_<32 lowercase hex>", "completion": {"...": "provider JSON"}},
}
```

Errors have the same shape with a fixed `{"error": "..."}` body; upstream error
bodies and exception text are never exposed. This function does not raise ordinary
request, network or parsing errors. The adapter serializes `body` to JSON, sets
its length, and adds no upstream headers. Tests include a tiny HTTP stand-in for
this surface; no production runner integration is claimed here.

At answer submission, the adapter records the answer's declared `relay_call_ids`
(the shared runner `/answers` interface must be extended by integration to accept
them, or obtain them from trusted runner state). Calls that do not exist in this
sitting, belong to another case, or have different text cannot supply a match.
At finish, freeze in-flight requests, snapshot/filter the log to this sitting,
then call `build_binding(record_without_binding, sitting_relay_log)`. Supply all
successful calls, including unused calls: unused contradictory model reports also
downgrade the level. Do not select only favorable calls. Invalid, duplicated or
cross-sitting log entries raise a fixed `ValueError`, rather than silently dropping
them. `None` and `[]` produce `unbound`.

## Relay transport and credential boundary

Only OpenAI-compatible, non-streaming chat completions are supported. The URL must
equal an operator-configured base byte for byte. Bases support an origin or `/v1`
path; both dispatch to `/v1/chat/completions`. Query strings, fragments, userinfo,
arbitrary paths and remote plaintext HTTP are refused. Literal `127.0.0.1` HTTP is
supported solely for offline tests. No redirect is followed, including redirects
to another allowlisted base. Proxy environment variables are ignored. TLS uses
Python's normal certificate verification. Operators control DNS/network policy.

The upstream request is newly constructed with only Authorization, Content-Type,
Accept and HTTP library framing headers. The key is forwarded only as upstream
`Authorization: Bearer ...`; incoming cookies, identifying, hop-by-hop and forwarding
headers are not copied. Only `openai-model`, `x-request-id` and `request-id` response
headers enter evidence. Response bodies are capped during reading, requests at
64 KiB, redirects/errors fail closed, and socket/deadline limits bound dispatch.
There is no streaming or retry. Socket operations have timeouts, with remaining
budget timeouts and elapsed deadline checks during response reading. DNS and OS
scheduling are not subject to a hard wall-clock bound.

The key exists transiently in request/HTTP objects in process memory; Python cannot
guarantee memory erasure. It is never intentionally logged or stored. Request data
containing the key and responses echoing it (including escaped JSON strings or
selected headers) are refused before persistence. Bodies are never redacted and
then represented as original evidence: a credential echo yields no log entry.
Arbitrarily transformed/exfiltrated secrets, OS memory dumps, malicious tracing or
a compromised runner are outside this boundary. A real provider necessarily sees
its API key and the completion request.

Only real-provider hosts under operator control of the allowlist belong in
production policy. A submitter-controlled "provider" can return any model name and
any desired answer, defeating the meaning of provider reports. This implementation
does not certify allowlist ownership or provider honesty. HTTPS is transport
authentication, not a weight attestation. Do not enable the loopback test exception
in production policy.

## Evidence and offline verification

Evidence includes the hash of the entire sitting record except `model_binding`,
all call entries, derived answer links, model reports and claim comparison. Each
call records:

- Runner-generated call id, sitting id, attributed case id, exact base and host.
- Requested model, response model, provider response id and selected headers.
- Base64 of the exact canonical request bytes sent and exact raw response bytes,
  and independent SHA-256 hashes of both bodies. Usage remains in the response.
- UTC start/end timestamps, checked against the sitting interval.

Exact body bytes permit independent rehashing and completion extraction without
the credential. **This includes prompts and completions**, potentially including
additional submitter content. They need the same consent and publication review
as answers. No private file or preexisting run data is needed. The request body
hash excludes HTTP headers (including Authorization); it is not a credential hash.
`evidence_sha256` is the contract's canonical JSON hash of the whole evidence.

`verify_binding(binding, record)` returns `{field, ok, detail}` findings for schema,
body hashes/encoding, extracted metadata, sitting/case scope, timestamps, unique
call IDs, derived exact links, model agreement, claim comparison, record hash and
fixed level text. It ignores `record.model_binding` when hashing to avoid a cycle.
Malformed evidence fails closed. A top-level evidence digest mismatch marks each
covered leaf untrusted; this **does not localize which leaf changed**. Findings
say so rather than pretending a single digest identifies a changed field.

Changing a field without updating the digest fails verification. Rehashing a body
without updating extracted metadata or derived links also fails. An attacker who
rewrites evidence, answers, derived fields and all unkeyed hashes consistently can
pass this checker. Provider IDs are not retrieved and headers are not signed.
Packet 03's externally trusted seal is needed to authenticate runner evidence and
detect such post-seal rewrites. An attacker who compromises the runner or its
signing key can still fabricate observations; this packet cannot fix that.

## Attacks and limits

| Attack | Result |
| --- | --- |
| Agent ignores relayed output and submits different model's text | `relay-observed` unless the final text exactly equals a recorded assistant message for that case. |
| Another model independently produces the same short answer, or the submitter asks the provider to echo a chosen answer | Can still be `relay-linked`; equality does not establish causal authorship or independent reasoning. |
| Submitter submits a favorable subset of calls | Runner must supply its complete log; offline evidence cannot detect omitted calls without an authenticated complete log. |
| Reuse a call id/completion from another sitting | Sitting-scoped runner log and verifier reject foreign entries; IDs alone are insufficient. Re-generation or coherent forged metadata is not provably fresh. |
| Link a call attributed to another case | No answer match; complete linkage fails. |
| Mix reported models or contradict body model with a header | Downgrades to `relay-observed`; all distinct reported strings remain visible. |
| Claim a different model or alias | Claim remains alongside the report with `mismatch`; no silent alias resolution. |
| Non-allowlisted URL, userinfo, redirect, proxy or alternate path | Relay refuses or never uses it; only the exact configured upstream receives a request. |
| Malicious allowlisted upstream reports false model/echoes answers | Not stopped. Reports bind only what the upstream said. |
| Submitter controls an allowlisted endpoint | Defeats model reporting; prevented by operator policy, not inferred from response contents. |
| Edit evidence, answer, level or fixed claim text | Offline consistency checks detect uncoordinated changes; the external receipt seal must cover coherent rewriting. |

Stronger future evidence could include provider-signed response artifacts with
offline-verifiable keys and documented coverage, externally checkpointed complete
call logs/nonces, and a runner-owned final completion whose exact bytes become the
answer. Retrievable IDs alone need authenticated provider lookups and do not prove
causal authorship. Probes could only show behavioral consistency, never identity;
none are implemented. Any such extension needs explicit semantics and negative
controls before gaining a stronger level name.
