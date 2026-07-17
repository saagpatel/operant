"""Immutable run artifacts and parse helpers for OPERANT lab runs."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
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
DEPENDENCY_LOCK_CANDIDATES = (
    "pylock.toml",
    "requirements.lock",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
)
VALID_MODEL_IDENTITY_STATUSES = {
    "AMBIGUOUS",
    "MATCHED",
    "MISMATCH",
    "UNKNOWN",
}
EXECUTION_BINDING_KEYS = {
    "schema",
    "input_binding",
    "harness",
    "dependency_lock",
    "source_state",
    "environment",
    "model_observation",
    "replay_class",
    "pre_dispatch_sha256",
    "completion_sha256",
}
INPUT_BINDING_KEYS = {
    "delivered_prompt_sha256",
    "logical_system_sha256",
    "stdin_sha256",
    "argv_sha256",
    "cwd_class",
    "tool_policy_sha256",
    "timeout_seconds",
    "output_mode",
    "dispatch_settings_sha256",
}
ENVIRONMENT_FACT_KEYS = {
    "python_implementation",
    "python_version",
    "python_executable_name",
    "os_system",
    "os_release",
    "machine",
    "processor",
    "cpu_count",
}


def utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def stable_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(root: Path, *args: str) -> bytes | None:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            check=False,
        )
    except OSError:
        return None
    return proc.stdout if proc.returncode == 0 else None


def _source_state(root: Path) -> dict[str, Any]:
    commit_bytes = _git_output(root, "rev-parse", "HEAD")
    status = _git_output(root, "status", "--porcelain=v1", "-z")
    tracked = _git_output(root, "diff", "--binary", "HEAD")
    staged = _git_output(root, "diff", "--binary", "--cached")
    untracked = _git_output(
        root,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    )
    if None in (commit_bytes, status, tracked, staged, untracked):
        return {
            "commit": "UNKNOWN",
            "dirty": "UNKNOWN",
            "dirty_state_sha256": "UNKNOWN",
        }
    untracked_rows = []
    for raw_path in untracked.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / relative
        if path.is_file():
            content_sha256 = _file_sha256(path)
        else:
            content_sha256 = "UNKNOWN"
        untracked_rows.append(
            {
                "path_sha256": stable_hash(relative),
                "content_sha256": content_sha256,
            }
        )
    dirty_payload = {
        "status_sha256": hashlib.sha256(status).hexdigest(),
        "tracked_diff_sha256": hashlib.sha256(tracked).hexdigest(),
        "staged_diff_sha256": hashlib.sha256(staged).hexdigest(),
        "untracked": sorted(
            untracked_rows,
            key=lambda row: row["path_sha256"],
        ),
    }
    return {
        "commit": commit_bytes.decode("ascii", errors="replace").strip(),
        "dirty": bool(status),
        "dirty_state_sha256": _canonical_hash(dirty_payload),
    }


def _environment_binding() -> dict[str, Any]:
    facts = {
        "python_implementation": platform.python_implementation(),
        "python_version": platform.python_version(),
        "python_executable_name": Path(sys.executable).name,
        "os_system": platform.system() or "UNKNOWN",
        "os_release": platform.release() or "UNKNOWN",
        "machine": platform.machine() or "UNKNOWN",
        "processor": platform.processor() or "UNKNOWN",
        "cpu_count": os.cpu_count() or "UNKNOWN",
    }
    return {
        "facts": facts,
        "sha256": _canonical_hash(facts),
    }


def _dependency_binding(root: Path) -> dict[str, Any]:
    locks = [
        root / name
        for name in DEPENDENCY_LOCK_CANDIDATES
        if (root / name).is_file()
    ]
    if not locks:
        return {
            "status": "UNKNOWN",
            "files": [],
            "sha256": "UNKNOWN",
            "reason": "NO_RELEVANT_PYTHON_LOCKFILE",
        }
    files = {
        path.relative_to(root).as_posix(): _file_sha256(path)
        for path in sorted(locks)
    }
    return {
        "status": "LOCKED",
        "files": sorted(files),
        "sha256": _canonical_hash(files),
        "reason": None,
    }


def _model_observation(
    *,
    requested_model_id: str,
    observed_model_ids: list[str] | None,
    source: str,
) -> dict[str, Any]:
    candidates = observed_model_ids or []
    if any(
        not isinstance(candidate, str)
        or not candidate
        or candidate != candidate.strip()
        for candidate in candidates
    ):
        raise ValueError("provider-reported model candidates must be exact nonempty strings")
    observed = sorted(set(candidates))
    if not observed:
        status = "UNKNOWN"
    elif len(observed) > 1:
        status = "AMBIGUOUS"
    elif observed[0] == requested_model_id:
        status = "MATCHED"
    else:
        status = "MISMATCH"
    return {
        "requested_model_id": requested_model_id,
        "provider_reported_candidates": observed,
        "comparison_status": status,
        "evidence_source": source if observed else "NOT_EXPOSED",
        "raw_result_envelope_sha256": "UNKNOWN",
        "final_answer_sha256": "UNKNOWN",
        "served_model_identity": "UNKNOWN",
    }


def build_execution_binding(
    *,
    root: Path,
    exact_prompt: str,
    system_prompt: str,
    stdin_text: str | None,
    command: list[str] | None,
    cwd_class: str,
    tool_policy: str,
    timeout_seconds: int | None,
    output_mode: str,
    dispatch_settings: dict[str, Any],
    harness_files: list[Path],
    requested_model_id: str,
) -> dict[str, Any]:
    """Build immutable pre-dispatch evidence without storing prompt contents."""
    root = root.resolve()
    harness = {}
    for path in harness_files:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"harness file is outside repository: {path}") from exc
        if not resolved.is_file():
            raise ValueError(f"missing harness file: {relative}")
        harness[relative] = _file_sha256(resolved)
    environment = _environment_binding()
    dependency = _dependency_binding(root)
    model_observation = _model_observation(
        requested_model_id=requested_model_id,
        observed_model_ids=None,
        source="NOT_EXPOSED",
    )
    input_binding = {
        "delivered_prompt_sha256": stable_hash(exact_prompt),
        "logical_system_sha256": stable_hash(system_prompt),
        "stdin_sha256": stable_hash(stdin_text) if stdin_text is not None else "NONE",
        "argv_sha256": _canonical_hash(command) if command is not None else "UNKNOWN",
        "cwd_class": cwd_class,
        "tool_policy_sha256": stable_hash(tool_policy),
        "timeout_seconds": timeout_seconds if timeout_seconds is not None else "UNKNOWN",
        "output_mode": output_mode,
        "dispatch_settings_sha256": _canonical_hash(dispatch_settings),
    }
    binding = {
        "schema": "operant-execution-binding.v1",
        "input_binding": input_binding,
        "harness": {
            "files": sorted(harness),
            "sha256": _canonical_hash(harness),
        },
        "dependency_lock": dependency,
        "source_state": _source_state(root),
        "environment": environment,
        "model_observation": model_observation,
        "replay_class": "INPUT_BOUND_NOT_REPLAYABLE",
        "completion_sha256": "UNKNOWN",
    }
    binding["pre_dispatch_sha256"] = _canonical_hash(
        {
            **{
                key: value
                for key, value in binding.items()
                if key
                not in {
                    "model_observation",
                    "pre_dispatch_sha256",
                    "completion_sha256",
                }
            },
            "requested_model_id": requested_model_id,
        }
    )
    return binding


def complete_execution_binding(
    binding: dict[str, Any],
    *,
    provider_reported_candidates: list[str] | None,
    evidence_source: str,
    raw_result_envelope: str | bytes | None,
    final_answer: str,
) -> dict[str, Any]:
    completed = deepcopy(binding)
    requested = completed.get("model_observation", {}).get("requested_model_id")
    if not isinstance(requested, str) or not requested:
        raise ValueError("execution binding lacks requested model identity")
    observation = _model_observation(
        requested_model_id=requested,
        observed_model_ids=provider_reported_candidates,
        source=evidence_source,
    )
    observation["raw_result_envelope_sha256"] = (
        hashlib.sha256(
            raw_result_envelope.encode("utf-8")
            if isinstance(raw_result_envelope, str)
            else raw_result_envelope
        ).hexdigest()
        if raw_result_envelope is not None
        else "UNKNOWN"
    )
    observation["final_answer_sha256"] = stable_hash(final_answer)
    completed["model_observation"] = observation
    completed["completion_sha256"] = _canonical_hash(
        {
            key: value
            for key, value in completed.items()
            if key != "completion_sha256"
        }
    )
    errors = validate_execution_binding(completed)
    if errors:
        raise ValueError("invalid completed execution binding: " + "; ".join(errors))
    return completed


def execution_input_mismatches(
    binding: dict[str, Any],
    *,
    exact_prompt: str,
    system_prompt: str,
    stdin_text: str | None,
    command: list[str] | None,
    cwd_class: str,
    tool_policy: str,
    timeout_seconds: int | None,
    output_mode: str,
    dispatch_settings: dict[str, Any],
) -> list[str]:
    expected = {
        "delivered_prompt_sha256": stable_hash(exact_prompt),
        "logical_system_sha256": stable_hash(system_prompt),
        "stdin_sha256": stable_hash(stdin_text) if stdin_text is not None else "NONE",
        "argv_sha256": _canonical_hash(command) if command is not None else "UNKNOWN",
        "cwd_class": cwd_class,
        "tool_policy_sha256": stable_hash(tool_policy),
        "timeout_seconds": timeout_seconds if timeout_seconds is not None else "UNKNOWN",
        "output_mode": output_mode,
        "dispatch_settings_sha256": _canonical_hash(dispatch_settings),
    }
    actual = binding.get("input_binding")
    if not isinstance(actual, dict):
        return ["input binding is missing"]
    return [
        field
        for field, expected_value in expected.items()
        if actual.get(field) != expected_value
    ]


def validate_execution_binding(binding: dict[str, Any]) -> list[str]:
    errors = []
    if set(binding) != EXECUTION_BINDING_KEYS:
        errors.append("execution binding fields are not exact")
    if binding.get("schema") != "operant-execution-binding.v1":
        errors.append("unsupported execution binding schema")
    input_binding = binding.get("input_binding")
    if not isinstance(input_binding, dict):
        errors.append("input binding is missing")
        input_binding = {}
    elif set(input_binding) != INPUT_BINDING_KEYS:
        errors.append("input binding fields are not exact")
    for input_field in (
        "delivered_prompt_sha256",
        "logical_system_sha256",
        "tool_policy_sha256",
        "dispatch_settings_sha256",
    ):
        if not SHA256_RE.fullmatch(str(input_binding.get(input_field) or "")):
            errors.append(f"{input_field} must be a lowercase SHA-256")
    stdin_hash = input_binding.get("stdin_sha256")
    if stdin_hash != "NONE" and not SHA256_RE.fullmatch(str(stdin_hash or "")):
        errors.append("stdin_sha256 must be SHA-256 or NONE")
    command_hash = input_binding.get("argv_sha256")
    if command_hash != "UNKNOWN" and not SHA256_RE.fullmatch(str(command_hash or "")):
        errors.append("argv_sha256 must be SHA-256 or UNKNOWN")
    if not str(input_binding.get("cwd_class") or "").strip():
        errors.append("cwd class is missing")
    if not str(input_binding.get("output_mode") or "").strip():
        errors.append("output mode is missing")

    harness = binding.get("harness")
    if (
        not isinstance(harness, dict)
        or set(harness) != {"files", "sha256"}
        or not isinstance(harness.get("files"), list)
        or not harness["files"]
        or not SHA256_RE.fullmatch(str(harness.get("sha256") or ""))
    ):
        errors.append("harness binding is incomplete")

    dependency = binding.get("dependency_lock")
    if (
        not isinstance(dependency, dict)
        or set(dependency) != {"status", "files", "sha256", "reason"}
        or dependency.get("status") not in {
        "LOCKED",
        "UNKNOWN",
        }
    ):
        errors.append("dependency lock status is invalid")
    elif dependency["status"] == "LOCKED":
        if not SHA256_RE.fullmatch(str(dependency.get("sha256") or "")):
            errors.append("bound dependency lock lacks SHA-256")
    elif dependency.get("sha256") != "UNKNOWN":
        errors.append("unknown dependency lock must not assert SHA-256")

    source = binding.get("source_state")
    if not isinstance(source, dict):
        errors.append("source state is missing")
    else:
        if set(source) != {"commit", "dirty", "dirty_state_sha256"}:
            errors.append("source state fields are not exact")
        commit = source.get("commit")
        if commit != "UNKNOWN" and not re.fullmatch(r"[0-9a-f]{40,64}", str(commit)):
            errors.append("source commit is invalid")
        dirty = source.get("dirty")
        if dirty not in {True, False, "UNKNOWN"}:
            errors.append("source dirty state is invalid")
        dirty_hash = source.get("dirty_state_sha256")
        if dirty_hash != "UNKNOWN" and not SHA256_RE.fullmatch(
            str(dirty_hash or "")
        ):
            errors.append("source dirty-state digest is invalid")

    environment = binding.get("environment")
    if (
        not isinstance(environment, dict)
        or set(environment) != {"facts", "sha256"}
        or not isinstance(environment.get("facts"), dict)
    ):
        errors.append("environment facts are missing")
    elif set(environment["facts"]) != ENVIRONMENT_FACT_KEYS:
        errors.append("environment fact fields are not exact")
    elif environment.get("sha256") != _canonical_hash(environment["facts"]):
        errors.append("environment digest mismatch")

    observation = binding.get("model_observation")
    if not isinstance(observation, dict):
        errors.append("model observation is missing")
    else:
        if set(observation) != {
            "requested_model_id",
            "provider_reported_candidates",
            "comparison_status",
            "evidence_source",
            "raw_result_envelope_sha256",
            "final_answer_sha256",
            "served_model_identity",
        }:
            errors.append("model observation fields are not exact")
        status = observation.get("comparison_status")
        requested = observation.get("requested_model_id")
        observed = observation.get("provider_reported_candidates")
        if status not in VALID_MODEL_IDENTITY_STATUSES:
            errors.append("model observation status is invalid")
        if not isinstance(requested, str) or not requested:
            errors.append("requested model identity is missing")
        if (
            not isinstance(observed, list)
            or any(not isinstance(value, str) or not value for value in observed)
            or observed != sorted(set(observed))
        ):
            errors.append("observed model identities are invalid")
        elif status == "UNKNOWN" and observed:
            errors.append("unknown model identity must not assert observations")
        elif status == "MATCHED" and observed != [requested]:
            errors.append("matched model identity does not equal request")
        elif status == "MISMATCH" and (
            len(observed) != 1 or observed == [requested]
        ):
            errors.append("model mismatch is not evidenced")
        elif status == "AMBIGUOUS" and len(observed) < 2:
            errors.append("ambiguous model identity lacks multiple observations")
        raw_digest = observation.get("raw_result_envelope_sha256")
        if raw_digest != "UNKNOWN" and not SHA256_RE.fullmatch(
            str(raw_digest or "")
        ):
            errors.append("raw result envelope digest is invalid")
        final_answer_digest = observation.get("final_answer_sha256")
        if final_answer_digest != "UNKNOWN" and not SHA256_RE.fullmatch(
            str(final_answer_digest or "")
        ):
            errors.append("final answer digest is invalid")
        if observation.get("served_model_identity") != "UNKNOWN":
            errors.append("served model identity must remain UNKNOWN")
        evidence_source = observation.get("evidence_source")
        if status == "UNKNOWN":
            if evidence_source != "NOT_EXPOSED":
                errors.append("unknown model observation must not assert identity evidence")
        elif (
            evidence_source not in {"provider_result_modelUsage"}
            or raw_digest == "UNKNOWN"
        ):
            errors.append("observed model identity lacks bound provider evidence")
        if raw_digest != "UNKNOWN" and final_answer_digest == "UNKNOWN":
            errors.append("completed result lacks final answer digest")

    if binding.get("replay_class") != "INPUT_BOUND_NOT_REPLAYABLE":
        errors.append("unsupported replay class")
    pre_dispatch = {
        **{
            key: value
            for key, value in binding.items()
            if key
            not in {
                "model_observation",
                "pre_dispatch_sha256",
                "completion_sha256",
            }
        },
        "requested_model_id": (
            observation.get("requested_model_id") if observation else None
        ),
    }
    if binding.get("pre_dispatch_sha256") != _canonical_hash(pre_dispatch):
        errors.append("pre-dispatch digest mismatch")
    completion = binding.get("completion_sha256")
    completed_payload = {
        key: value for key, value in binding.items() if key != "completion_sha256"
    }
    if observation and (
        observation.get("comparison_status") != "UNKNOWN"
            or observation.get("raw_result_envelope_sha256") != "UNKNOWN"
    ):
        if completion != _canonical_hash(completed_payload):
            errors.append("completed execution binding digest mismatch")
    elif completion != "UNKNOWN":
        errors.append("unknown model observation must not assert completion digest")
    return errors


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
    execution_binding: dict[str, Any]
    repeat_id: int | None = None
    thinking: str | None = None
    case_split: str = "canonical"
    created_at: str = field(default_factory=utc_now)
    source_thread_id: str | None = None
    source_queue_file: str | None = None
    thread_container: str | None = None
    cost_usd: float | None = None
    manifest_schema: str = field(default="operant-run-manifest.v3", init=False)
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
        binding_errors = validate_execution_binding(self.execution_binding)
        if binding_errors:
            raise ValueError(
                "invalid execution binding: " + "; ".join(binding_errors)
            )
        requested_model = self.execution_binding["model_observation"][
            "requested_model_id"
        ]
        if requested_model != self.model_id:
            raise ValueError("manifest model_id does not match requested model binding")


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

    def __post_init__(self) -> None:
        block_reason = scoring_block_reason(asdict(self.manifest))
        if block_reason and (self.score_row is not None or self.judge_row is not None):
            raise ValueError(f"blocked receipt cannot carry scores: {block_reason}")
        if (
            block_reason
            and block_reason != "incomplete_execution_receipt"
            and self.parse_status != block_reason
        ):
            raise ValueError(
                "blocked receipt parse_status must preserve reason: "
                f"{block_reason}"
            )
        if (
            self.parse_status == "ok"
            or self.final_answer
            or self.score_row is not None
            or self.judge_row is not None
        ) and self.manifest.execution_binding.get("completion_sha256") == "UNKNOWN":
            raise ValueError("successful output requires completed execution binding")
        answer_digest = self.manifest.execution_binding["model_observation"].get(
            "final_answer_sha256"
        )
        if answer_digest != "UNKNOWN" and answer_digest != stable_hash(self.final_answer):
            raise ValueError("final answer does not match completed execution binding")


def scoring_block_reason(manifest: dict[str, Any]) -> str | None:
    schema = manifest.get("manifest_schema")
    if schema in {None, "operant-run-manifest.v1", "operant-run-manifest.v2"}:
        return None
    if schema != "operant-run-manifest.v3":
        return "invalid_execution_binding"
    binding = manifest.get("execution_binding")
    if not isinstance(binding, dict) or validate_execution_binding(binding):
        return "invalid_execution_binding"
    requested_model = binding["model_observation"]["requested_model_id"]
    if manifest.get("model_id") != requested_model:
        return "invalid_execution_binding"
    if binding.get("completion_sha256") == "UNKNOWN":
        return "incomplete_execution_receipt"
    status = binding["model_observation"]["comparison_status"]
    if status in {"AMBIGUOUS", "MISMATCH"}:
        return f"identity_blocked:{status.lower()}"
    return None


def receipt_scoring_block_reason(
    root: Path,
    *,
    run_label: str,
    case_id: str,
    require_receipt: bool = False,
) -> str | None:
    path = artifact_path(root, run_label, case_id)
    if not path.is_file():
        return "missing_execution_receipt" if require_receipt else None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid_execution_binding"
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        return "invalid_execution_binding"
    if manifest.get("run_label") != run_label or manifest.get("case_id") != case_id:
        return "receipt_identity_mismatch"
    reason = scoring_block_reason(manifest)
    if reason:
        return reason
    if manifest.get("manifest_schema") == "operant-run-manifest.v3":
        bound_answer = (
            manifest.get("execution_binding", {})
            .get("model_observation", {})
            .get("final_answer_sha256")
        )
        if bound_answer != stable_hash(str(data.get("final_answer") or "")):
            return "receipt_output_binding_mismatch"
    return None


def receipt_output_scoring_block_reason(
    root: Path,
    *,
    run_label: str,
    case_id: str,
    final_answer: str,
    require_receipt: bool = False,
) -> str | None:
    reason = receipt_scoring_block_reason(
        root,
        run_label=run_label,
        case_id=case_id,
        require_receipt=require_receipt,
    )
    if reason:
        return reason
    path = artifact_path(root, run_label, case_id)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "invalid_execution_binding"
    if data.get("final_answer") != final_answer:
        return "receipt_output_mismatch"
    manifest = data.get("manifest", {})
    if manifest.get("manifest_schema") == "operant-run-manifest.v3":
        bound_answer = (
            manifest.get("execution_binding", {})
            .get("model_observation", {})
            .get("final_answer_sha256")
        )
        if bound_answer != stable_hash(str(data.get("final_answer") or "")):
            return "receipt_output_binding_mismatch"
    return None


def filter_unblocked_index_rows(
    root: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Exclude persisted score rows whose corresponding local receipt is blocked."""
    filtered = []
    for row in rows:
        label = row.get("run_label")
        case_id = row.get("case_id")
        if not isinstance(label, str) or not isinstance(case_id, str):
            continue
        if receipt_scoring_block_reason(
            root,
            run_label=label,
            case_id=case_id,
        ):
            continue
        filtered.append(row)
    return filtered


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


def write_json_exclusive(path: Path, data: dict[str, Any]) -> None:
    """Atomically publish JSON exactly once without a check-then-write race."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(
            data,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n"
    ).encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=".exclusive-",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def artifact_path(root: Path, run_label: str, case_id: str) -> Path:
    safe_case = case_id.replace("/", "_")
    return root / RESULTS_ROOT / run_label / f"{safe_case}.json"


def ensure_run_receipt_slot(root: Path, run_label: str, case_id: str) -> None:
    path = artifact_path(root, run_label, case_id)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite run receipt: {path}")


def write_run_report(root: Path, report: RunReport) -> Path:
    path = artifact_path(root, report.manifest.run_label, report.manifest.case_id)
    write_json_exclusive(path, asdict(report))
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
