"""Sanitized queue/run inventory for OPERANT lab artifacts."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

from .artifacts import (
    SHA256_RE,
    VALID_EVALUATION_ROLES,
    scoring_block_reason,
    stable_hash,
    validate_execution_binding,
    validate_run_manifest_v3,
    validate_run_manifest_v4,
    validate_run_manifest_v5,
    validate_run_manifest_v6,
)

ROOT = Path(__file__).resolve().parents[1]
BOUND_NONCONFIRMATORY = "V2_BOUND_NONCONFIRMATORY"
UNKNOWN_BINDING = "UNKNOWN"
INVALID_BINDING = "INVALID"


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


def load_decision_cases(score_operant: Any | None = None) -> dict[str, Any]:
    """Load canonical cases plus optional local follow-up case overlays.

    Prompt-bearing follow-up case files live under an ignored lab/private path.
    When present locally they let inventory/export score private lab runs without
    publishing prompt text. Public clones that lack those files still work; they
    simply report unknown-case status for those private labels.
    """
    score_operant = score_operant or _load_score_operant()
    original_cases_glob = os.environ.get("OPERANT_CASES")
    cases = score_operant.load_cases()
    private_case_dir = ROOT / "lab" / "followup" / "private"
    for path in sorted(private_case_dir.glob("*cases*.json")):
        os.environ["OPERANT_CASES"] = str(path)
        cases.update(score_operant.load_cases())
    if original_cases_glob is None:
        os.environ.pop("OPERANT_CASES", None)
    else:
        os.environ["OPERANT_CASES"] = original_cases_glob
    return cases


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
    manifest: dict[str, Any],
    score_operant: Any,
) -> str:
    if run_data is None:
        return "queued"
    block_reason = scoring_block_reason(manifest)
    if block_reason:
        return block_reason
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
            manifest=manifest,
            score_operant=score_operant,
        )
    if run_data is None:
        return "queued"
    block_reason = scoring_block_reason(manifest)
    if block_reason:
        return block_reason
    judge_row = run_data.get("judge_row")
    if isinstance(judge_row, dict) and judge_row.get("verdict"):
        return f"judge:{judge_row['verdict']}"
    return "unscored"


def _manifest_binding_projection(
    manifest: dict[str, Any],
    *,
    source: str,
) -> dict[str, Any]:
    """Expose only non-sensitive v2 binding metadata; legacy absence stays UNKNOWN."""
    schema = manifest.get("manifest_schema")
    unknown = {
        "status": UNKNOWN_BINDING,
        "source": source,
        "manifest_schema": str(schema) if schema else "UNKNOWN",
        "evaluation_role": "UNKNOWN",
        "case_bundle_sha256": "UNKNOWN",
        "case_bundle_case_count": "UNKNOWN",
        "case_split": "UNKNOWN",
        "confirmatory_eligible": "UNKNOWN",
    }
    if schema not in {
        "operant-run-manifest.v2",
        "operant-run-manifest.v3",
        "operant-run-manifest.v4",
        "operant-run-manifest.v5",
        "operant-run-manifest.v6",
    }:
        return unknown
    if schema in {
        "operant-run-manifest.v3",
        "operant-run-manifest.v4",
        "operant-run-manifest.v5",
        "operant-run-manifest.v6",
    }:
        execution = manifest.get("execution_binding")
        validator = {
            "operant-run-manifest.v3": validate_run_manifest_v3,
            "operant-run-manifest.v4": validate_run_manifest_v4,
            "operant-run-manifest.v5": validate_run_manifest_v5,
            "operant-run-manifest.v6": validate_run_manifest_v6,
        }[schema]
        if (
            not isinstance(execution, dict)
            or validate_execution_binding(execution)
            or validator(manifest)
        ):
            return {
                **unknown,
                "status": INVALID_BINDING,
                "manifest_schema": schema,
            }

    role = manifest.get("evaluation_role")
    digest = manifest.get("case_bundle_sha256")
    count = manifest.get("case_bundle_case_count")
    split = manifest.get("case_split")
    confirmatory = manifest.get("confirmatory_eligible")
    valid = (
        isinstance(role, str)
        and role in VALID_EVALUATION_ROLES
        and isinstance(digest, str)
        and SHA256_RE.fullmatch(digest) is not None
        and isinstance(count, int)
        and not isinstance(count, bool)
        and count > 0
        and isinstance(split, str)
        and bool(split.strip())
        and confirmatory is False
    )
    if not valid:
        return {
            **unknown,
            "status": INVALID_BINDING,
            "manifest_schema": str(schema),
        }
    return {
        "status": BOUND_NONCONFIRMATORY,
        "source": source,
        "manifest_schema": str(schema),
        "evaluation_role": role,
        "case_bundle_sha256": digest,
        "case_bundle_case_count": count,
        "case_split": split,
        "confirmatory_eligible": False,
    }


def _evaluation_binding(
    *,
    queue_manifest: dict[str, Any],
    run_manifest: dict[str, Any],
    has_run: bool,
) -> dict[str, Any]:
    source = "run_receipt" if has_run else "queue_manifest"
    selected = run_manifest if has_run else queue_manifest
    projection = _manifest_binding_projection(selected, source=source)
    if not has_run or not queue_manifest:
        return projection

    queue_projection = _manifest_binding_projection(
        queue_manifest,
        source="queue_manifest",
    )
    if queue_projection["status"] == INVALID_BINDING:
        return {
            **projection,
            "status": INVALID_BINDING,
            "evaluation_role": "UNKNOWN",
            "case_bundle_sha256": "UNKNOWN",
            "case_bundle_case_count": "UNKNOWN",
            "case_split": "UNKNOWN",
            "confirmatory_eligible": "UNKNOWN",
        }
    if (
        projection["status"] == BOUND_NONCONFIRMATORY
        and queue_projection["status"] == BOUND_NONCONFIRMATORY
    ):
        comparable = (
            "evaluation_role",
            "case_bundle_sha256",
            "case_bundle_case_count",
            "case_split",
            "confirmatory_eligible",
        )
        if any(projection[key] != queue_projection[key] for key in comparable):
            return {
                **projection,
                "status": INVALID_BINDING,
                "evaluation_role": "UNKNOWN",
                "case_bundle_sha256": "UNKNOWN",
                "case_bundle_case_count": "UNKNOWN",
                "case_split": "UNKNOWN",
                "confirmatory_eligible": "UNKNOWN",
            }
    return projection


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
    cases = load_decision_cases(score_operant)
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
                "source_queue_sha256": manifest.get("source_queue_sha256"),
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
                "evaluation_binding": _evaluation_binding(
                    queue_manifest=queue_manifest,
                    run_manifest=run_manifest,
                    has_run=run_data is not None,
                ),
            }
        )
    return rows
