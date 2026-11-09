"""Hash the source bytes that define a sitting's corpus and scoring protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sittings.canonical import canonical_sha256, sha256_hex

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_GLOB = "operant*_cases.json"
CORPUS_EXCLUDE = frozenset({"operant_axis3_cases.json"})
OPERATOR_CONTRACT = "examples/example-operator-contract.md"

# Imports used when the host builds subject prompts. run_operant and these modules
# are included even though most of their runtime helpers are unrelated to prompt text.
PROMPT_FILES = (
    "run_operant.py",
    "operant_lab/__init__.py",
    "operant_lab/agent_runners.py",
    "operant_lab/artifacts.py",
    "operant_lab/lineage.py",
    "operant_lab/subjects.py",
    "sittings/prompts.py",
)
# Minimal repo-local replay closure for sittings.scoring.score_answers. This scorer
# contains a documented local copy of score_operant's deterministic scoring core,
# avoiding its environment- and filesystem-oriented CLI imports.
SCORER_FILES = (
    "sittings/__init__.py",
    "sittings/canonical.py",
    "sittings/manifest.py",
    "sittings/scoring.py",
)


def corpus_paths(root: Path = REPO_ROOT) -> list[str]:
    cases = [
        p.name for p in root.glob(CORPUS_GLOB) if p.is_file() and p.name not in CORPUS_EXCLUDE
    ]
    return sorted([*cases, OPERATOR_CONTRACT, *PROMPT_FILES])


def files_manifest(paths: list[str] | tuple[str, ...], root: Path = REPO_ROOT) -> dict[str, Any]:
    files = []
    for rel in sorted(paths):
        data = (root / rel).read_bytes()
        files.append({"path": rel, "sha256": sha256_hex(data), "bytes": len(data)})
    aggregate = canonical_sha256([{"path": f["path"], "sha256": f["sha256"]} for f in files])
    return {"sha256": aggregate, "files": files}


def corpus_manifest(root: Path = REPO_ROOT) -> dict[str, Any]:
    return files_manifest(corpus_paths(root), root)


def scorer_manifest(root: Path = REPO_ROOT) -> dict[str, Any]:
    return files_manifest(SCORER_FILES, root)
