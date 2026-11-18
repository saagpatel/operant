# Releasing saagar-operant-mcp to npm

`saagar-operant-mcp` (the stdio CLI in `mcp/`) publishes only through
`.github/workflows/publish-npm.yml`, triggered by a tag `mcp-vX.Y.Z` whose
version matches `mcp/package.json`. The `mcp-v` prefix keeps benchmark tags
such as `v0.9-public-lab` from starting a package release.

The workflow uses npm trusted publishing (OIDC), so no npm token exists in the
repository or its secrets. The trusted publisher on npmjs.com must name owner
`saagpatel`, repository `operant`, workflow `publish-npm.yml` (case-sensitive)
and environment `npm`. Node 24.17.0 from `.node-version` bundles npm 11.13.0,
which is above the 11.5.1 minimum for trusted publishing; the publish job
checks this before publishing.

## What the workflow does

1. `build` (read-only): checks the tag, runs `make check-mcp`, packs the
   tarball from a clean `npm ci --ignore-scripts`, repacks from a fresh clone
   and fails unless the SHA-256 digests match
   (`mcp/scripts/check_reproducible_build.py`), and writes a CycloneDX SBOM of
   the production dependencies with `npm sbom --package-lock-only --omit dev`.
2. `attest`: signs a SLSA build provenance attestation and an SBOM attestation
   for the tarball with `actions/attest` (Sigstore, stored on this repository).
3. `publish` (`npm` environment): publishes that exact tarball with
   `npm publish --provenance --access public`, which adds npm's own provenance
   statement linked from the package page.

`.github/workflows/supply-chain.yml` runs the reproducible pack and SBOM on every
pull request without any publishing permission.

## Verifying a release

```bash
# Registry signatures and npm provenance, from any project that installs it
npm install saagar-operant-mcp@X.Y.Z
npm audit signatures

# npm provenance statement
curl -s https://registry.npmjs.org/-/npm/v1/attestations/saagar-operant-mcp@X.Y.Z

# GitHub build provenance and SBOM attestations on the tarball
npm pack saagar-operant-mcp@X.Y.Z
gh attestation verify saagar-operant-mcp-X.Y.Z.tgz --repo saagpatel/operant
gh attestation verify saagar-operant-mcp-X.Y.Z.tgz --repo saagpatel/operant \
  --predicate-type https://cyclonedx.org/bom

# Local reproducibility check of the committed source
make repro-check-mcp
```

Versions 0.1.0 and 0.1.1 were published by hand before this workflow existed.
They carry the registry's ECDSA signature but no provenance. 0.1.1 was
rebuilt from its recorded `gitHead` (ace95d9) on 2026-11-17 and its tarball
matched the registry byte for byte.
