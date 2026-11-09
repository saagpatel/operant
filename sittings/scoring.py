"""Deterministic scoring against the manifest-bound decision corpus."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import score_operant
from operant_lab.agent_runners import build_byo_prompt
from operant_lab.selfserve import classify_band
from run_operant import build_system_prompt
from sittings import manifest


def load_corpus() -> dict[str, dict[str, Any]]:
    """Load only the decision case files listed by the sitting corpus manifest."""
    cases: dict[str, dict[str, Any]] = {}
    for relative_path in manifest.corpus_paths():
        if relative_path == manifest.OPERATOR_CONTRACT:
            continue
        path = Path(manifest.REPO_ROOT, relative_path)
        data = json.loads(path.read_text(encoding="utf-8"))
        # Mirror score_operant.load_cases' behavior for corpus files that don't
        # use the matched-pair schema, without consulting its environment glob.
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
                case.update(
                    pair_id=pair["pair_id"],
                    tier=pair["tier"],
                    axis=axis,
                    side=side,
                )
                cases[case_id] = case
    return cases


def system_prompt() -> str:
    """Build the prompt from the contract file pinned in the corpus manifest."""
    contract = Path(manifest.REPO_ROOT, manifest.OPERATOR_CONTRACT).read_text(encoding="utf-8")
    return build_system_prompt(contract)


def prompt_for(case: dict[str, Any]) -> str:
    return build_byo_prompt(case, system_prompt())


def score_answers(
    case_map: dict[str, dict[str, Any]], answers: list[dict[str, str]]
) -> dict[str, Any]:
    """Score accepted answers and report unanswered cases separately.

    An answer counts as scored once ``score_one`` returns a row, including a
    row whose decision is unparseable. The scorer excludes unparseable rows
    from the OCS confusion matrix itself.
    """
    seen: set[str] = set()
    rows = []
    for answer in answers:
        case_id = answer["case_id"]
        if case_id not in case_map or case_id in seen:
            raise ValueError("unknown or duplicate case")
        seen.add(case_id)
        rows.append(score_operant.score_one(case_map[case_id], answer["answer"]))

    aggregate = score_operant.aggregate(rows)
    return {
        "ocs": aggregate["ocs"],
        "tpr": aggregate["tpr"],
        "fpr": aggregate["fpr"],
        "n_scored": len(rows),
        "n_cases": len(case_map),
        "n_unanswered": len(case_map) - len(seen),
        "band": classify_band(aggregate["ocs"])["band"],
    }
