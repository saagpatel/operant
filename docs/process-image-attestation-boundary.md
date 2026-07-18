# Process-Image Attestation Boundary

Status: `NOT_CONFIGURED`

Decision date: 2026-07-17

## Decision

OPERANT does not claim kernel-observed process-image identity. New execution
bindings preserve that evidence as `UNKNOWN`.

The pre/post executable-candidate hashes remain useful drift evidence, but they
do not prove which image the kernel executed.

One point-in-time local probe on macOS 26.5.2 launched
`["/usr/bin/env", "sleep", "2"]`, recorded `/usr/bin/env` as the
pre-dispatch candidate, then called `proc_pidpath(pid, buffer, buffer_size)`
through the system `libproc` library while the child was alive. The returned
path was `/bin/sleep`. This is a reproducible probe method, not durable
attestation evidence: process lifetime and scheduling can prevent an
observation, and the result describes only that local run.

Dynamic `codesign` validation of a live PID can identify and validate a signed
Mach-O image at one observation point. It is not a general attestation for this
harness because it can miss fast exits, later `exec` transitions, worker
children, unsigned or interpreted content, scripts, and model/runtime assets.
A PID path or on-disk signature must therefore not be promoted to executed-image
identity.

## Defensible future path

Apple Endpoint Security exec events expose the target process after `exec`
completes in the kernel and before its code starts running, including executable
and code-signing state. Apple also documents important limits: individual code
pages are validated as they are paged in, and complete pre-execution validation
can have significant performance cost.

Using Endpoint Security is outside the current repair authority. Apple requires:

- the restricted `com.apple.developer.endpoint-security.client` entitlement;
- a privileged client running as root;
- user approval through Transparency, Consent, and Control / Full Disk Access;
- installation and lifecycle management of an app, daemon, or system extension.

Those are account, privilege, consent, installation, and operating-surface
changes. They require explicit approval and a separate threat, privacy,
performance, and rollback review.

## Evidence hierarchy

1. Endpoint Security exec evidence with an audit-token PID version and
   kernel-observed code-signing state may support a future bounded identity
   claim.
2. Dynamic Security-framework or `codesign` observations are partial diagnostic
   evidence only.
3. `proc_pidpath`, path hashing, and pre/post candidate hashing do not attest an
   executed process image.
4. Without level 1 evidence, process-image identity remains `UNKNOWN`.

## Primary references

- [Apple Endpoint Security](https://developer.apple.com/documentation/endpointsecurity)
- [Apple `es_process_t`](https://developer.apple.com/documentation/endpointsecurity/es_process_t)
- [Apple Endpoint Security client requirements](https://developer.apple.com/documentation/endpointsecurity/client)
- [Apple Endpoint Security restricted entitlement](https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.endpoint-security.client)
- [Apple `SecCodeCopyGuestWithAttributes`](https://developer.apple.com/documentation/security/seccodecopyguestwithattributes(_:_:_:_:))
- [Apple `SecCodeCopySigningInformation`](https://developer.apple.com/documentation/security/seccodecopysigninginformation(_:_:_:))
