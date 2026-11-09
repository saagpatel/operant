"""Which bytes a sitting binds: the decision corpus and the scorer.

A file manifest is {"sha256": <aggregate>, "files": [{"path", "sha256", "bytes"}, ...]},
files sorted by repo-relative POSIX path. The aggregate is the canonical sha256 of the
list of {"path", "sha256"} pairs (byte counts are informative, not hashed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sittings.canonical import canonical_sha256, sha256_hex

REPO_ROOT = Path(__file__).resolve().parent.parent

# The decision corpus: every matched-pair file the OCS scorer loads by default.
CORPUS_GLOB = "operant*_cases.json"
# Files outside the decision schema share the glob; score_operant.load_cases skips them.
CORPUS_EXCLUDE = frozenset({"operant_axis3_cases.json"})
# The pinned operator contract every sitting is examined under. A hosted runner never
# falls back to a local ~/.claude/CLAUDE.md: that would leak the host's private file.
OPERATOR_CONTRACT = "examples/example-operator-contract.md"

# The exact bytes that build the prompts and turn answers into a score.
# Changing any of them changes scorer.sha256.
SCORER_FILES = (
    "run_operant.py",
    "operant_lab/agent_runners.py",
    "score_operant.py",
    "operant_lab/selfserve.py",
    "operant_lab/artifacts.py",
    "sittings/scoring.py",
)


def corpus_paths(root: Path = REPO_ROOT) -> list[str]:
    cases = [
        p.name for p in root.glob(CORPUS_GLOB) if p.is_file() and p.name not in CORPUS_EXCLUDE
    ]
    return sorted([*cases, OPERATOR_CONTRACT])


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
