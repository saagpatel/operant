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
RESULTS_ROOT = Path("lab") / "runs"


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
    repeat_id: int | None = None
    thinking: str | None = None
    case_split: str = "canonical"
    created_at: str = field(default_factory=utc_now)
    source_thread_id: str | None = None
    source_queue_file: str | None = None
    thread_container: str | None = None
    cost_usd: float | None = None


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
