"""Immutable run artifacts and parse helpers for OPERANT lab runs."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

VALID_DECISIONS = {"PROCEED", "PROCEED_SANCTIONED", "REFUSE", "ESCALATE", "REROUTE"}
VALID_EVALUATION_ROLES = {
    "ADAPTIVE_COVERAGE_DIAGNOSTIC",
    "ADAPTIVE_DIAGNOSTIC",
    "OPEN_DEVELOPMENT",
    "SMOKE_ONLY",
    "UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY",
}
KNOWN_RUN_FAMILY_ROLES = {
    "codex-cli-gpt55-decision-gap": "ADAPTIVE_COVERAGE_DIAGNOSTIC",
    "codex-gpt55-exact-smoke": "SMOKE_ONLY",
    "codex-gpt55-local-authority-followup": "ADAPTIVE_DIAGNOSTIC",
    "codex-gpt55-refusal-calibration-followup": "ADAPTIVE_DIAGNOSTIC",
    "codex-gpt55-sanctioned-path-followup": "ADAPTIVE_DIAGNOSTIC",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RESULTS_ROOT = Path("lab") / "runs"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def case_bundle_binding(
    cases: list[dict[str, Any]],
    *,
    case_split: str,
) -> dict[str, str | int]:
    """Bind exact case objects without exposing their contents in the manifest."""
    if not cases:
        raise ValueError("case bundle must contain at least one case")
    if not isinstance(case_split, str) or not case_split.strip():
        raise ValueError("case_split must be a non-empty string")
    rows = []
    for case in cases:
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ValueError("every bound case must have a non-empty id")
        rows.append(
            {
                "case_id": case_id,
                "case_sha256": _canonical_hash(case),
            }
        )
    rows.sort(key=lambda row: row["case_id"])
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("case bundle contains duplicate ids")
    payload = {
        "schema": "operant-case-bundle.v1",
        "case_split": case_split,
        "cases": rows,
    }
    return {
        "case_bundle_sha256": _canonical_hash(payload),
        "case_bundle_case_count": len(rows),
        "case_split": case_split,
    }


def resolve_evaluation_role(
    explicit_role: str | None,
    *,
    run_label: str,
) -> str:
    """Return a non-confirmatory role; this manifest version cannot certify."""
    if explicit_role is not None:
        if explicit_role not in VALID_EVALUATION_ROLES:
            raise ValueError(f"unsupported evaluation role: {explicit_role}")
        return explicit_role
    base_label = re.sub(r"-r\d+$", "", run_label)
    return KNOWN_RUN_FAMILY_ROLES.get(
        base_label,
        "UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY",
    )


@dataclass
class RunManifest:
    run_label: str
    case_id: str
    axis: str
    subject_shell: str
    model_id: str
    prompt_hash: str
    prompt_contract: str
    tool_policy: str
    evaluation_role: str
    case_bundle_sha256: str
    case_bundle_case_count: int
    repeat_id: int | None = None
    thinking: str | None = None
    case_split: str = "canonical"
    created_at: str = field(default_factory=utc_now)
    source_thread_id: str | None = None
    source_queue_file: str | None = None
    thread_container: str | None = None
    cost_usd: float | None = None
    manifest_schema: str = field(default="operant-run-manifest.v2", init=False)
    confirmatory_eligible: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.evaluation_role not in VALID_EVALUATION_ROLES:
            raise ValueError(
                f"unsupported evaluation role: {self.evaluation_role}"
            )
        if not SHA256_RE.fullmatch(self.case_bundle_sha256):
            raise ValueError("case_bundle_sha256 must be a lowercase SHA-256")
        if self.case_bundle_case_count < 1:
            raise ValueError("case_bundle_case_count must be positive")
        if not self.case_split.strip():
            raise ValueError("case_split must be non-empty")


@dataclass
class RunReport:
    manifest: RunManifest
    parse_status: str
    final_answer: str
    extracted_decision: str | None = None
    extracted_justification: str | None = None
    failure_class: str | None = None
    score_row: dict[str, Any] | None = None
    judge_row: dict[str, Any] | None = None
    source_report: str | None = None


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(data, indent=2, sort_keys=True, default=_json_default) + "\n",
        encoding="utf-8",
    )


def artifact_path(root: Path, run_label: str, case_id: str) -> Path:
    safe_case = case_id.replace("/", "_")
    return root / RESULTS_ROOT / run_label / f"{safe_case}.json"


def write_run_report(root: Path, report: RunReport) -> Path:
    path = artifact_path(root, report.manifest.run_label, report.manifest.case_id)
    write_json(path, asdict(report))
    return path


def parse_decision_block(text: str) -> dict[str, str | None]:
    """Parse the final OPERANT decision block and classify common failures."""
    stripped = text.strip()
    if not stripped:
        return {
            "parse_status": "empty",
            "decision": None,
            "justification": None,
            "failure_class": "empty_result",
        }

    decision_re = re.compile(r"^DECISION:\s*([A-Z_]+)\s*$", re.MULTILINE)
    just_re = re.compile(r"^JUSTIFICATION:\s*(.+?)\s*$", re.MULTILINE)
    decisions = decision_re.findall(stripped)
    justifications = just_re.findall(stripped)

    if not decisions:
        return {
            "parse_status": "missing_decision",
            "decision": None,
            "justification": justifications[-1] if justifications else None,
            "failure_class": "missing_final_block",
        }
    if len(decisions) > 1:
        return {
            "parse_status": "duplicate_decision",
            "decision": decisions[-1],
            "justification": justifications[-1] if justifications else None,
            "failure_class": "multiple_decision_lines",
        }

    decision = decisions[0]
    if decision not in VALID_DECISIONS:
        return {
            "parse_status": "invalid_decision",
            "decision": decision,
            "justification": justifications[-1] if justifications else None,
            "failure_class": "invalid_decision_label",
        }

    last_lines = [line.strip() for line in stripped.splitlines() if line.strip()][-2:]
    trailing_ok = len(last_lines) == 2 and last_lines[0].startswith("DECISION:")
    trailing_ok = trailing_ok and last_lines[1].startswith("JUSTIFICATION:")
    if not trailing_ok:
        return {
            "parse_status": "trailing_text",
            "decision": decision,
            "justification": justifications[-1] if justifications else None,
            "failure_class": "text_after_final_block",
        }

    return {
        "parse_status": "ok",
        "decision": decision,
        "justification": justifications[-1] if justifications else None,
        "failure_class": None,
    }


def parse_orchestration_plan(text: str) -> dict[str, str | None]:
    stripped = text.strip()
    if not stripped:
        return {
            "parse_status": "empty",
            "decision": None,
            "justification": None,
            "failure_class": "empty_result",
        }
    required = ("tier", "model", "isolation", "safeguard")
    low = stripped.lower()
    missing = [word for word in required if word not in low]
    if missing:
        return {
            "parse_status": "incomplete_plan",
            "decision": None,
            "justification": ", ".join(missing),
            "failure_class": "missing_plan_fields",
        }
    return {
        "parse_status": "ok",
        "decision": None,
        "justification": None,
        "failure_class": None,
    }
