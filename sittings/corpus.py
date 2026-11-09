"""Load the decision cases shared by scoring and prompt verification."""

import json
from pathlib import Path
from typing import Any

from sittings import manifest


def load_corpus(
    root: Path = manifest.REPO_ROOT, *, corpus_bytes: dict[str, bytes] | None = None,
) -> dict[str, dict[str, Any]]:
    """Load only case bytes from the explicit corpus manifest paths."""
    cases: dict[str, dict[str, Any]] = {}
    paths = manifest.corpus_paths(root) if corpus_bytes is None else sorted(corpus_bytes)
    for relative_path in paths:
        if relative_path == manifest.OPERATOR_CONTRACT or relative_path in manifest.PROMPT_FILES:
            continue
        raw = (root / relative_path).read_bytes() if corpus_bytes is None else corpus_bytes[relative_path]
        data = json.loads(raw)
        if "pairs" not in data:
            continue
        file_axis = data.get("axis", "refusal-calibration")
        for pair in data["pairs"]:
            axis = pair.get("axis", file_axis)
            for side in ("malign", "benign"):
                case = dict(pair[side])
                case_id = case["id"]
                if case_id in cases:
                    raise ValueError(f"duplicate case id {case_id!r}")
                case.update(pair_id=pair["pair_id"], tier=pair["tier"], axis=axis, side=side)
                cases[case_id] = case
    return cases


