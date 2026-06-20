"""Case-submission governance for OPERANT."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VALID_STATES = {
    "candidate",
    "accepted_public",
    "accepted_private",
    "rejected",
    "needs_revision",
}
VALID_DECISIONS = {"PROCEED", "PROCEED_SANCTIONED", "REFUSE", "ESCALATE", "REROUTE"}
VALID_AXES = {
    "refusal-calibration",
    "sanctioned-path",
    "escalation-reroute",
    "orchestration",
}

REQUIRED_FIELDS = {
    "id",
    "state",
    "axis",
    "task_prompt",
    "expected_decisions",
    "matched_pair_id",
    "matched_side",
    "ambiguity_notes",
    "provenance",
    "leak_risk",
}

TEMPLATE: dict[str, Any] = {
    "id": "submission-example.benign",
    "state": "candidate",
    "axis": "refusal-calibration",
    "task_prompt": "Describe the operating decision the agent must make.",
    "expected_decisions": ["PROCEED"],
    "matched_pair_id": "submission-example",
    "matched_side": "benign",
    "ambiguity_notes": "Why this is or is not ambiguous.",
    "provenance": "Author, date, and source inspiration. Do not include secrets or PII.",
    "leak_risk": "public_ok",
}


def validate_submission(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    missing = sorted(REQUIRED_FIELDS - set(data))
    if missing:
        errors.append(f"missing required fields: {', '.join(missing)}")
    state = data.get("state")
    if state not in VALID_STATES:
        errors.append(f"state must be one of {sorted(VALID_STATES)}")
    axis = data.get("axis")
    if axis not in VALID_AXES:
        errors.append(f"axis must be one of {sorted(VALID_AXES)}")
    side = data.get("matched_side")
    if side not in {"benign", "malign", "single"}:
        errors.append("matched_side must be benign, malign, or single")
    expected = data.get("expected_decisions")
    if not isinstance(expected, list) or not expected:
        errors.append("expected_decisions must be a non-empty list")
    elif any(item not in VALID_DECISIONS for item in expected):
        errors.append(f"expected_decisions must use labels {sorted(VALID_DECISIONS)}")
    if state == "accepted_private" and data.get("leak_risk") == "public_ok":
        errors.append("accepted_private submissions must explain private leak risk")
    return errors


def load_submission(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))

