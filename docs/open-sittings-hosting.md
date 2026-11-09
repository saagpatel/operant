# Hosting Open Sittings (operator runbook)

This is the one step that needs the operator: put the runner on the internet behind
TLS, publish the signing key, and mount the static table and verify page on
`/instruments`. Everything before this step is tested locally (`uv run python -m
sittings.e2e_local --workdir DIR`).

Placeholders: `RUNNER_HOST` (for example `sittings.saagarpatel.dev`), `SITE` (the
`portfolio-index` checkout that deploys `saagarpatel.dev`), `OPERANT` (an `operant`
checkout on `main`), `STATE` (a private directory on the runner host, for example
`/srv/operant`).

## 1. Runner host (any small Linux box or Mac with uv and Caddy)

```sh
git clone https://github.com/saagpatel/operant.git OPERANT && cd OPERANT && uv sync --frozen
mkdir -p STATE/keys STATE/sittings-data && chmod 700 STATE STATE/keys
uv run python -m sittings.receipt keygen \
  --private STATE/keys/operator-key.pem \
  --public STATE/keys/operator-public.pem \
  --pubkeys STATE/keys/sittings-pubkey.json
# Note the printed "Key fingerprint: ed25519:..." for step 3.
```

Run the runner on loopback; Caddy terminates TLS in front of it:

```sh
uv run python -m sittings.runner --host 127.0.0.1 --port 8765 \
  --data-dir STATE/sittings-data --key STATE/keys/operator-key.pem \
  --relay-provider openai=https://api.openai.com/v1
```

`/etc/caddy/Caddyfile`:

```
RUNNER_HOST {
	reverse_proxy 127.0.0.1:8765
}
```

```sh
sudo systemctl reload caddy
curl -s https://RUNNER_HOST/api/health
```

Add only real provider hosts to `--relay-provider`; a submitter-controlled endpoint
would let them invent the observed model (see `docs/open-sittings-model-binding.md`).
Behind a local proxy every request arrives from `127.0.0.1`, so the per-IP create
limit (`--create-rate-limit`, default 10 per hour) acts as one global limit; the
runner does not trust `X-Forwarded-For` in v1. Raise it if outside traffic grows.
Keep the runner under a service manager (systemd unit or launchd) so it restarts.

## 2. Mount the table and verify page on /instruments

```sh
cd SITE
mkdir -p instruments/sittings/data
rsync -a --delete --exclude test --exclude data OPERANT/web/ instruments/sittings/
cp STATE/keys/sittings-pubkey.json instruments/sittings/data/sittings-pubkey.json
printf '{"format": "operant.instruments-rows/v1", "rows": []}\n' > instruments/sittings/data/rows.json
```

Add a roster entry pointing at `/instruments/sittings/` in
`scripts/sources/instruments/instruments.json`, rebuild with
`python3 scripts/build-instruments.py`, commit, and push the branch that deploys.

## 3. Publish the key fingerprint out of band

Put the `ed25519:...` fingerprint from step 1 in the /instruments roster text (and
the OPERANT README). The verify page shows the fingerprint of the key that signed
each receipt; a stranger compares the two.

## 4. First outside sitting (the definition of done)

From any other machine:

Use the HTTPS URL: the client refuses remote HTTP by default. The
`--allow-insecure-runner` escape hatch is for unbound runs only and exposes bearer
tokens; it never permits sending relay provider keys over remote HTTP. HTTP is
allowed locally only for `127.0.0.1`, `::1`, and `localhost`.

```sh
uv run python -m sittings.client --runner https://RUNNER_HOST --label NAME \
  --claimed-model MODEL --cmd 'AGENT --stdin' --cmd-stdin
```

Then review and publish on the runner host:

```sh
uv run python -m sittings.review list --data-dir STATE/sittings-data --state pending
uv run python -m sittings.review approve SITTING_ID --data-dir STATE/sittings-data \
  --web-data SITE/instruments/sittings/data \
  --pubkey STATE/keys/sittings-pubkey.json --blob-source OPERANT
```

Commit `SITE/instruments/sittings/data`, push, and open
`https://saagarpatel.dev/instruments/sittings/`: the row shows a green seal, and
its receipt link re-checks in the browser. A stranger can run the full re-check
with the CLI command printed on the verify page.

## Upkeep

- A scorer change changes `scorer.sha256`. Add the new release to
  `sittings/trusted_scorers.json` deliberately; old rows stay verifiable from the
  published blobs.
- Rotate the key by adding a second key to `sittings-pubkey.json`; keep the old one
  so old receipts still verify.
- Back up `STATE/keys` offline. Losing the key does not invalidate published rows.
