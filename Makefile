.PHONY: check check-python check-mcp check-web verify-manifest repro-check-mcp sbom-mcp

check: check-python check-mcp check-web

check-web:
	node --test web/test/*.test.mjs

verify-manifest:
	npm --prefix mcp run verify:manifest

check-python:
	@command -v uv >/dev/null 2>&1 || { echo 'uv is required; install uv 0.13.9 or any later 0.13.x and retry.' >&2; exit 127; }
	uv sync --frozen
	uv run python -m compileall -q operant_lab ./*.py
	OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python selftest.py
	OPERANT_OPERATOR_CONTRACT=examples/example-operator-contract.md uv run python -m unittest discover -p 'test_*.py'
	uv run python verify_evaluation_split.py
	uv run ruff check operant_lab ./*.py

check-mcp:
	@command -v npm >/dev/null 2>&1 || { echo 'npm is required; install the Node.js version in .node-version and retry.' >&2; exit 127; }
	npm ci --prefix mcp
	npm --prefix mcp run build:corpus
	npm --prefix mcp run build:wellknown
	git diff --exit-code -- mcp/src/corpus.generated.ts mcp/src/well-known.generated.ts
	npm --prefix mcp run typecheck
	npm --prefix mcp test
	npm --prefix mcp run build:cli
	npm --prefix mcp run probe:mcp
	@if [ -n "$${MCP_TRUSTED_PUBLISHER_PUB:-}" ]; then \
		npm --prefix mcp run verify:manifest; \
	else \
		echo 'independent publisher pin not configured; live manifest verification NOT RUN'; \
	fi
	npm --prefix mcp run parity
	git diff --exit-code -- score_orchestration.py score_orchestration_judge.py lab/public/ mcp/src/corpus.generated.ts mcp/src/well-known.generated.ts

# Release build of the npm package, run from mcp/ in a fresh clone; {out} is the output directory.
MCP_PACK = npm ci --ignore-scripts --no-audit --no-fund && npm run build:cli && npm pack --pack-destination {out}
REPRO_REPORT ?=
SBOM_OUT ?= saagar-operant-mcp.cdx.json

# Pack the committed MCP package twice in fresh clones and compare SHA-256 of the tarballs.
repro-check-mcp:
	python3 mcp/scripts/check_reproducible_build.py --subdir mcp $(if $(REPRO_REPORT),--report $(REPRO_REPORT)) --build '$(MCP_PACK)'

# CycloneDX SBOM of the MCP package's production dependencies, from the lockfile.
sbom-mcp:
	npm sbom --prefix mcp --package-lock-only --omit dev --sbom-format cyclonedx --sbom-type application > $(SBOM_OUT)
