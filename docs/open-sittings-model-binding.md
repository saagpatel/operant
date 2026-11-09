# Open Sittings model binding

V1 has exactly two binding levels. Both describe what the runner observed; neither
proves which model weights produced an answer.

| Level | Meaning |
| --- | --- |
| `unbound` | An outside agent supplied its own answers. No provider observation is linked to them. |
| `host-relayed` | For every case, the runner built and sent the request to an operator-allowlisted provider using the submitter's key, every response reported the same model, and each recorded answer equals the returned completion text exactly. |

Agent-routed relay is not part of v1. The host-relayed endpoint accepts the provider
name, requested model, and API key when a sitting is created. The provider base URL
comes only from the runner operator's `--relay-provider NAME=HTTPS_BASE_URL` setting.
The runner constructs one non-streaming OpenAI-compatible chat-completions request
per case. The request includes the case prompt, sitting ID, and case ID in host-built
context; submitters cannot provide a request body or URL. The runner stores the exact
completion text as the answer and builds the binding from its complete relay log.

Every case must report one consistent observed model. A requested alias may differ
from the provider's reported model; no alias normalization or equivalence inference
is performed. If the submitter's claimed model and observed model differ, the receipt
shows the mismatch and the sitting is not publishable as host-relayed.

The fixed `binds` and `does_not_bind` statements are in
`sittings/model_binding/__init__.py`; verification checks those statements and the
evidence. They are displayed verbatim. Every binding says it does not establish:
provider honesty about what it served; that an answer was not copied from the public
corpus (including expected decisions); or anything about the submitter's setup beyond
the relayed calls. It also does not establish causal authorship, exclusive model use,
human involvement, unseen-task performance, general safety, submitter identity, or
freshness and immutable history of unsigned evidence.

The public evidence for host-built calls contains the request and response bodies so
the exact prompt and completion can be checked. It excludes credentials and provider
request headers; only an allowlisted set of non-secret response headers may be kept.
The API key exists in runner memory from sitting creation until `/run`, expiry, or
runner shutdown, is sent to the configured upstream as a bearer credential, and is
not written to records, evidence, logs, or errors. A real provider receives the key
and request. HTTPS
certificate checks apply to configured upstreams. Literal loopback HTTP is reserved
for local tests.

The binding is signed as part of the receipt by the host's local key. That signature
authenticates the host's captured bytes under the pinned key; it is not a provider
signature or weight attestation. See [Open Sittings](open-sittings.md) for receipt
verification and publication.
