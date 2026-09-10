"""Result-blind, identity-bound registration for future OPERANT experiments.

The registration is deliberately separate from a run receipt.  It records what
will be tested before dispatch, while a run receipt records what happened.  The
registration writer fixes the status to ``REGISTERED_NOT_EXECUTED`` and rejects
result-bearing fields so a score cannot be smuggled into the admission record.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "operant-experiment-preregistration.v1"
STATUS = "REGISTERED_NOT_EXECUTED"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

# These fields can contain subject answers or derived outcomes and therefore
# must never appear in a result-blind registration.
FORBIDDEN_RESULT_KEYS = {
    "answer",
    "answers",
    "decision",
    "final_answer",
    "metric_value",
    "metrics_observed",
    "output",
    "raw_output",
    "score",
    "scores",
    "transcript",
    "verdict",
}

REQUIRED_KEYS = {
    "schema",
    "status",
    "experiment_id",
    "registered_at",
    "research_question",
    "hypotheses",
    "primary_metrics",
    "exclusions",
    "stopping_rule",
    "analysis_plan",
    "bound_inputs",
    "execution_policy",
    "claim_ceiling",
    "prohibited_claims",
    "admission",
}
REQUIRED_BOUND_INPUT_NAMES = {"cases", "protocol", "subject", "dependencies"}


def canonical_json_bytes(value: Any) -> bytes:
    """Serialize registration data in the digest-stable JSON form."""
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def _text(value: Any, field: str, *, min_length: int = 1) -> list[str]:
    if not isinstance(value, str) or len(value.strip()) < min_length:
        return [f"{field} must be a non-empty string"]
    if len(value) > 4000 or any(ord(character) < 32 for character in value):
        return [f"{field} contains unsafe or oversized text"]
    return []


def _text_list(value: Any, field: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        return [f"{field} must be a non-empty list"]
    errors: list[str] = []
    for index, item in enumerate(value):
        errors.extend(_text(item, f"{field}[{index}]"))
    return errors


def _walk_forbidden_keys(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_RESULT_KEYS:
                errors.append(f"{path}: result-bearing key {key!r} is forbidden")
            _walk_forbidden_keys(child, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_forbidden_keys(child, f"{path}[{index}]", errors)


def _validate_bound_inputs(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        return ["bound_inputs must be a non-empty list"]
    errors: list[str] = []
    names: set[str] = set()
    for index, item in enumerate(value):
        path = f"bound_inputs[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{path} must be an object")
            continue
        if set(item) - {"name", "sha256", "count", "notes"}:
            errors.append(f"{path} has unsupported keys")
        name = item.get("name")
        if not isinstance(name, str) or not ID_RE.fullmatch(name):
            errors.append(f"{path}.name must be a filename-safe identifier")
        elif name in names:
            errors.append(f"{path}.name is duplicated")
        else:
            names.add(name)
        digest = item.get("sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            errors.append(f"{path}.sha256 must be a lowercase SHA-256 digest")
        if "count" in item and (
            type(item["count"]) is not int or item["count"] < 1
        ):
            errors.append(f"{path}.count must be a positive integer")
        if "notes" in item:
            errors.extend(_text(item["notes"], f"{path}.notes"))
    missing = REQUIRED_BOUND_INPUT_NAMES - names
    if missing:
        errors.append(f"bound_inputs is missing required identities: {sorted(missing)}")
    return errors


def validate_preregistration(data: Any) -> list[str]:
    """Return all structural errors without executing a subject or judge."""
    if not isinstance(data, dict):
        return ["registration must be a JSON object"]
    errors: list[str] = []
    missing = REQUIRED_KEYS - set(data)
    unexpected = set(data) - REQUIRED_KEYS
    if missing:
        errors.append(f"registration is missing required keys: {sorted(missing)}")
    if unexpected:
        errors.append(f"registration has unsupported keys: {sorted(unexpected)}")
    if data.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if data.get("status") != STATUS:
        errors.append(f"status must be {STATUS}")
    errors.extend(_text(data.get("experiment_id"), "experiment_id"))
    if not isinstance(data.get("experiment_id"), str) or not ID_RE.fullmatch(
        data.get("experiment_id", "")
    ):
        errors.append("experiment_id must be a filename-safe identifier")
    errors.extend(_text(data.get("registered_at"), "registered_at"))
    if isinstance(data.get("registered_at"), str):
        try:
            parsed_timestamp = datetime.fromisoformat(
                data["registered_at"].replace("Z", "+00:00")
            )
            if parsed_timestamp.tzinfo is None:
                errors.append("registered_at must include a timezone")
        except ValueError:
            errors.append("registered_at must be an ISO-8601 timestamp")
    errors.extend(_text(data.get("research_question"), "research_question"))
    for field in ("hypotheses", "primary_metrics", "exclusions"):
        errors.extend(_text_list(data.get(field), field))
    for field in ("stopping_rule", "analysis_plan", "claim_ceiling"):
        errors.extend(_text(data.get(field), field))
    errors.extend(_text_list(data.get("prohibited_claims"), "prohibited_claims"))
    errors.extend(_validate_bound_inputs(data.get("bound_inputs")))

    execution = data.get("execution_policy")
    if not isinstance(execution, dict):
        errors.append("execution_policy must be an object")
    else:
        required_execution = {
            "max_retries",
            "network_policy",
            "spend_policy",
            "tool_policy",
        }
        if required_execution - set(execution):
            errors.append("execution_policy is missing required keys")
        if set(execution) - required_execution:
            errors.append("execution_policy has unsupported keys")
        if type(execution.get("max_retries")) is not int or execution.get(
            "max_retries"
        ) != 0:
            errors.append("execution_policy.max_retries must be exactly 0")
        if execution.get("spend_policy") != "NO_PAID_MODEL_OR_API_SPEND":
            errors.append(
                "execution_policy.spend_policy must be "
                "NO_PAID_MODEL_OR_API_SPEND"
            )
        for field in ("network_policy", "spend_policy", "tool_policy"):
            errors.extend(_text(execution.get(field), f"execution_policy.{field}"))

    admission = data.get("admission")
    expected_admission = {
        "evaluation_role": "UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY",
        "confirmatory_eligible": False,
        "external_custody": "UNKNOWN",
        "served_model_identity": "UNKNOWN",
        "independent_replication": "UNKNOWN",
    }
    if admission != expected_admission:
        errors.append(
            "admission must retain the explicit non-confirmatory and unknown gates"
        )
    _walk_forbidden_keys(data, "registration", errors)
    return errors


def build_preregistration(spec: dict[str, Any], registered_at: str | None = None) -> dict[str, Any]:
    """Build a result-blind registration from a user-authored experiment spec.

    The caller supplies design text and digest records. Admission fields are
    fixed here so a spec cannot self-declare confirmatory status.
    """
    if not isinstance(spec, dict):
        raise TypeError("spec must be an object")
    unexpected = set(spec) - (REQUIRED_KEYS - {"schema", "status", "registered_at", "admission"})
    if unexpected:
        raise ValueError(f"spec has unsupported keys: {sorted(unexpected)}")
    registration = {
        **spec,
        "schema": SCHEMA,
        "status": STATUS,
        "registered_at": registered_at or utc_now(),
        "admission": {
            "evaluation_role": "UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY",
            "confirmatory_eligible": False,
            "external_custody": "UNKNOWN",
            "served_model_identity": "UNKNOWN",
            "independent_replication": "UNKNOWN",
        },
    }
    errors = validate_preregistration(registration)
    if errors:
        raise ValueError("invalid preregistration: " + "; ".join(errors))
    return registration


def _exclusive_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_preregistration(registration: dict[str, Any], path: Path) -> tuple[str, Path]:
    """Write registration and exclusive digest sidecar; never overwrite either."""
    errors = validate_preregistration(registration)
    if errors:
        raise ValueError("invalid preregistration: " + "; ".join(errors))
    payload = json.dumps(registration, indent=2, sort_keys=True, ensure_ascii=False).encode(
        "utf-8"
    ) + b"\n"
    digest = sha256_bytes(payload)
    digest_path = path.with_name(path.name + ".sha256")
    if path.exists() or digest_path.exists():
        raise FileExistsError("preregistration or digest sidecar already exists")
    _exclusive_write(path, payload)
    try:
        _exclusive_write(digest_path, (digest + "  " + path.name + "\n").encode("ascii"))
    except Exception:
        # Do not leave a registration that falsely appears fully admitted if its
        # sidecar could not be created. The registration itself remains recoverable.
        raise
    return digest, digest_path


def load_verified_preregistration(path: Path) -> tuple[dict[str, Any], str]:
    """Load and verify the registration bytes plus its adjacent digest sidecar."""
    raw = path.read_bytes()
    digest = sha256_bytes(raw)
    digest_path = path.with_name(path.name + ".sha256")
    sidecar = digest_path.read_text(encoding="ascii").strip().split()
    if len(sidecar) != 2 or sidecar[0] != digest or sidecar[1] != path.name:
        raise ValueError("preregistration digest sidecar mismatch")
    data = json.loads(raw)
    errors = validate_preregistration(data)
    if errors:
        raise ValueError("invalid preregistration: " + "; ".join(errors))
    return data, digest
