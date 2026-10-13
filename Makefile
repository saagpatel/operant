.PHONY: check check-python check-mcp

check: check-python check-mcp

check-python:
	@command -v uv >/dev/null 2>&1 || { echo 'uv is required; install uv 0.13.x and retry.' >&2; exit 127; }
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
	npm --prefix mcp run verify:manifest
	npm --prefix mcp run parity
	git diff --exit-code -- score_orchestration.py score_orchestration_judge.py lab/public/ mcp/src/corpus.generated.ts mcp/src/well-known.generated.ts
