"""Sanitized queue/run inventory for OPERANT lab artifacts."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

from .artifacts import stable_hash

ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _iter_json_files(path: Path) -> list[Path]:
    if not path.exists():
        return []
    return sorted(p for p in path.glob("*/*.json") if p.is_file())


def _load_score_operant():
    spec = importlib.util.spec_from_file_location(
        "score_operant", ROOT / "score_operant.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _record_key(data: dict[str, Any], path: Path) -> tuple[str, str]:
    manifest = data.get("manifest", {})
    run_label = data.get("run_label") or manifest.get("run_label")
    case_id = data.get("case_id") or manifest.get("case_id")
    if not run_label or not case_id:
        raise ValueError(f"missing run_label/case_id in {path}")
    return str(run_label), str(case_id)


def _queue_prompt_hash(data: dict[str, Any]) -> str | None:
    manifest = data.get("manifest", {})
    if manifest.get("prompt_hash"):
        return str(manifest["prompt_hash"])
    prompt = data.get("prompt")
    if isinstance(prompt, str):
        return stable_hash(prompt)
    return None


def _risk_tags(case: dict[str, Any] | None, manifest: dict[str, Any]) -> list[str]:
    axis = str((case or {}).get("axis") or manifest.get("axis") or "unknown")
    tags = [f"axis:{axis}"]
    if case is None:
        tags.append("case:unknown")
        return tags

    side = case.get("side")
    cls = case.get("class")
    if side:
        tags.append(f"side:{side}")
    if cls:
        tags.append(f"class:{cls}")
    if case.get("withholds_dangerous_op_expected"):
        tags.append("expected:withhold")
    else:
        tags.append("expected:direct-ok")
    if case.get("bypass_patterns"):
        tags.append("bypass-patterned")
    return tags


def _decision_score_outcome(
    *,
    case: dict[str, Any] | None,
    run_data: dict[str, Any] | None,
    score_operant: Any,
) -> str:
    if run_data is None:
        return "queued"
    parse_status = str(run_data.get("parse_status") or "unknown")
    if parse_status != "ok":
        return f"parse:{parse_status}"
    if case is None:
        return "unscored:unknown-case"
    final_answer = run_data.get("final_answer")
    if not isinstance(final_answer, str):
        return "unscored:missing-answer"

    row = score_operant.score_one(case, final_answer)
    if row.get("bypass_used"):
        return "bypass_failure"
    if row.get("decision") == "UNPARSEABLE":
        return "unparseable"
    if row.get("decision_accuracy") is True:
        return "correct"
    return "incorrect"


def _score_outcome(
    *,
    case: dict[str, Any] | None,
    run_data: dict[str, Any] | None,
    manifest: dict[str, Any],
    score_operant: Any,
) -> str:
    run_axis = str(manifest.get("axis") or "decision")
    if run_axis != "orchestration":
        return _decision_score_outcome(
            case=case,
            run_data=run_data,
            score_operant=score_operant,
        )
    if run_data is None:
        return "queued"
    judge_row = run_data.get("judge_row")
    if isinstance(judge_row, dict) and judge_row.get("verdict"):
        return f"judge:{judge_row['verdict']}"
    return "unscored"


def inventory_runs(
    *,
    queue_dir: Path,
    runs_dir: Path,
    root: Path = ROOT,
    labels: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return sanitized inventory rows without raw prompts or final answers."""
    queue_by_key: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    run_by_key: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}

    for path in _iter_json_files(queue_dir):
        data = _read_json(path)
        key = _record_key(data, path)
        if labels is None or key[0] in labels:
            queue_by_key[key] = (path, data)

    for path in _iter_json_files(runs_dir):
        data = _read_json(path)
        key = _record_key(data, path)
        if labels is None or key[0] in labels:
            run_by_key[key] = (path, data)

    score_operant = _load_score_operant()
    cases = score_operant.load_cases()
    rows: list[dict[str, Any]] = []
    for key in sorted(set(queue_by_key) | set(run_by_key)):
        run_label, case_id = key
        queue_entry = queue_by_key.get(key)
        run_entry = run_by_key.get(key)
        queue_path = queue_entry[0] if queue_entry else None
        queue_data = queue_entry[1] if queue_entry else None
        run_data = run_entry[1] if run_entry else None

        queue_manifest = (queue_data or {}).get("manifest", {})
        run_manifest = (run_data or {}).get("manifest", {})
        manifest = {**queue_manifest, **run_manifest}
        case = cases.get(case_id)

        source_queue_file = manifest.get("source_queue_file")
        if queue_path is not None:
            queue_file_path = _relative(queue_path, root)
        elif source_queue_file:
            queue_file_path = str(source_queue_file)
        else:
            queue_file_path = None

        prompt_hash = (
            _queue_prompt_hash(queue_data)
            if queue_data is not None
            else manifest.get("prompt_hash")
        )

        rows.append(
            {
                "case_id": case_id,
                "queue_file_path": queue_file_path,
                "prompt_hash": prompt_hash,
                "run_label": run_label,
                "thread_id": manifest.get("source_thread_id"),
                "parse_status": (run_data or {}).get("parse_status"),
                "score_outcome": _score_outcome(
                    case=case,
                    run_data=run_data,
                    manifest=manifest,
                    score_operant=score_operant,
                ),
                "risk_tags": _risk_tags(case, manifest),
            }
        )
    return rows
