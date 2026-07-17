"""Immutable run artifacts and parse helpers for OPERANT lab runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
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
MAX_EXACT_JSON_INTEGER = (1 << 53) - 1
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
EXECUTION_BINDING_BASE_KEYS = {
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
EXECUTION_BINDING_V3_KEYS = EXECUTION_BINDING_BASE_KEYS | {"subject_runtime"}
EXECUTION_BINDING_V4_KEYS = EXECUTION_BINDING_V3_KEYS | {
    "post_dispatch_runtime"
}
EXECUTION_BINDING_V5_KEYS = EXECUTION_BINDING_V4_KEYS | {
    "process_image_identity"
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
RUN_MANIFEST_V4_KEYS = {
    "run_label",
    "case_id",
    "axis",
    "subject_shell",
    "model_id",
    "prompt_hash",
    "prompt_contract",
    "tool_policy",
    "evaluation_role",
    "case_bundle_sha256",
    "case_bundle_case_count",
    "execution_binding",
    "repeat_id",
    "thinking",
    "case_split",
    "created_at",
    "source_thread_id",
    "source_queue_file",
    "source_queue_sha256",
    "thread_container",
    "cost_usd",
    "manifest_core_sha256",
    "manifest_schema",
    "confirmatory_eligible",
}
RUN_MANIFEST_V3_KEYS = RUN_MANIFEST_V4_KEYS - {"manifest_core_sha256"}
RUN_MANIFEST_V5_KEYS = RUN_MANIFEST_V4_KEYS
RUN_MANIFEST_V6_KEYS = RUN_MANIFEST_V4_KEYS
RUN_MANIFEST_V7_KEYS = RUN_MANIFEST_V4_KEYS


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and SHA256_RE.fullmatch(value) is not None


def _is_nonnegative_finite_number(value: Any) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return 0 <= value <= MAX_EXACT_JSON_INTEGER
    return math.isfinite(value) and value >= 0


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


def _try_canonical_hash(value: Any) -> str | None:
    try:
        return _canonical_hash(value)
    except (TypeError, ValueError):
        return None


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
    except (OSError, RuntimeError, ValueError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _unknown_source_state() -> dict[str, Any]:
    return {
        "commit": "UNKNOWN",
        "dirty": "UNKNOWN",
        "dirty_state_sha256": "UNKNOWN",
        "reconstruction": "UNKNOWN",
    }


def _source_snapshot(root: Path) -> dict[str, Any] | None:
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
        return None
    untracked_rows = []
    for raw_path in untracked.split(b"\0"):
        if not raw_path:
            continue
        relative = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / relative
        if path.is_symlink():
            content_sha256 = stable_hash(os.readlink(path))
            content_kind = "SYMLINK_TARGET"
        elif path.is_file():
            content_sha256 = _file_sha256(path)
            content_kind = "FILE"
        else:
            content_sha256 = "UNKNOWN"
            content_kind = "OTHER"
        untracked_rows.append(
            {
                "path_sha256": stable_hash(relative),
                "content_sha256": content_sha256,
                "content_kind": content_kind,
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
        "reconstruction": (
            "DIRTY_DIGEST_ONLY" if status else "CLEAN_COMMIT"
        ),
    }


def _source_state(root: Path) -> dict[str, Any]:
    try:
        first = _source_snapshot(root)
    except OSError:
        return _unknown_source_state()
    if first is None:
        return _unknown_source_state()
    try:
        second = _source_snapshot(root)
    except OSError:
        return _unknown_source_state()
    if second is None or second != first:
        return _unknown_source_state()
    return second


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


def _subject_runtime_binding(
    command: list[str] | None,
    *,
    root: Path,
) -> dict[str, Any]:
    version_reason = "NOT_QUERIED_TO_PRESERVE_NO_SIDE_EFFECT_BOUNDARY"
    if not command or not command[0]:
        return {
            "status": "UNKNOWN",
            "requested_executable_name": "UNKNOWN",
            "resolved_executable_name": "UNKNOWN",
            "executable_sha256": "UNKNOWN",
            "executable_size_bytes": "UNKNOWN",
            "version": "UNKNOWN",
            "version_reason": version_reason,
            "coverage": "UNKNOWN",
            "reason": "NO_EXECUTABLE_DISPATCH",
        }
    requested = Path(command[0]).name or "UNKNOWN"
    command_path = Path(command[0])
    if command_path.is_absolute():
        candidate = command[0]
    elif os.sep in command[0] or (os.altsep and os.altsep in command[0]):
        candidate = str(root / command_path)
    else:
        candidate = shutil.which(command[0])
    if not candidate:
        return {
            "status": "UNKNOWN",
            "requested_executable_name": requested,
            "resolved_executable_name": "UNKNOWN",
            "executable_sha256": "UNKNOWN",
            "executable_size_bytes": "UNKNOWN",
            "version": "UNKNOWN",
            "version_reason": version_reason,
            "coverage": "UNKNOWN",
            "reason": "EXECUTABLE_NOT_FOUND",
        }
    try:
        resolved = Path(candidate).resolve(strict=True)
        if not resolved.is_file() or not os.access(resolved, os.X_OK):
            raise OSError("resolved executable is not a regular executable file")
        before = resolved.stat()
        digest = _file_sha256(resolved)
        after = resolved.stat()
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise OSError("resolved executable changed during capture")
        size = after.st_size
    except (OSError, RuntimeError, ValueError):
        return {
            "status": "UNKNOWN",
            "requested_executable_name": requested,
            "resolved_executable_name": "UNKNOWN",
            "executable_sha256": "UNKNOWN",
            "executable_size_bytes": "UNKNOWN",
            "version": "UNKNOWN",
            "version_reason": version_reason,
            "coverage": "UNKNOWN",
            "reason": "EXECUTABLE_CAPTURE_FAILED",
        }
    return {
        "status": "PRE_DISPATCH_EXECUTABLE_BYTES_BOUND",
        "requested_executable_name": requested,
        "resolved_executable_name": resolved.name,
        "executable_sha256": digest,
        "executable_size_bytes": size,
        "version": "UNKNOWN",
        "version_reason": version_reason,
        "coverage": "PRE_DISPATCH_CANDIDATE_BYTES_ONLY",
        "reason": None,
    }


def _unassessed_post_dispatch_runtime() -> dict[str, Any]:
    return {
        "status": "NOT_ASSESSED",
        "resolved_executable_name": "UNKNOWN",
        "executable_sha256": "UNKNOWN",
        "executable_size_bytes": "UNKNOWN",
        "comparison": "UNKNOWN",
        "coverage": "UNKNOWN",
        "reason": "DISPATCH_RESULT_NOT_AVAILABLE",
    }


def _unknown_process_image_identity(
    command: list[str] | None,
) -> dict[str, Any]:
    return {
        "status": "UNKNOWN",
        "kernel_observed_cdhash": "UNKNOWN",
        "kernel_observed_signing_id": "UNKNOWN",
        "kernel_observed_team_id": "UNKNOWN",
        "kernel_observed_pidversion": "UNKNOWN",
        "evidence_source": "NOT_CAPTURED",
        "coverage": "UNKNOWN",
        "reason": (
            "KERNEL_EXEC_ATTESTATION_NOT_CONFIGURED"
            if command
            else "NO_LOCAL_PROCESS_DISPATCH"
        ),
    }


def _post_dispatch_runtime_binding(
    pre_dispatch: dict[str, Any],
    command: list[str] | None,
    *,
    root: Path,
) -> dict[str, Any]:
    if not command:
        return {
            **_unassessed_post_dispatch_runtime(),
            "status": "UNKNOWN",
            "reason": "NO_EXECUTABLE_DISPATCH",
        }
    if pre_dispatch.get("status") != "PRE_DISPATCH_EXECUTABLE_BYTES_BOUND":
        return {
            **_unassessed_post_dispatch_runtime(),
            "status": "UNKNOWN",
            "reason": "PRE_DISPATCH_CANDIDATE_UNAVAILABLE",
        }
    post_dispatch = _subject_runtime_binding(command, root=root)
    if post_dispatch.get("status") != "PRE_DISPATCH_EXECUTABLE_BYTES_BOUND":
        return {
            **_unassessed_post_dispatch_runtime(),
            "status": "UNKNOWN",
            "reason": "POST_DISPATCH_CANDIDATE_UNAVAILABLE",
        }
    comparison = (
        "MATCHED"
        if (
            post_dispatch["resolved_executable_name"],
            post_dispatch["executable_sha256"],
            post_dispatch["executable_size_bytes"],
        )
        == (
            pre_dispatch["resolved_executable_name"],
            pre_dispatch["executable_sha256"],
            pre_dispatch["executable_size_bytes"],
        )
        else "DRIFTED"
    )
    return {
        "status": "POST_DISPATCH_EXECUTABLE_BYTES_BOUND",
        "resolved_executable_name": post_dispatch["resolved_executable_name"],
        "executable_sha256": post_dispatch["executable_sha256"],
        "executable_size_bytes": post_dispatch["executable_size_bytes"],
        "comparison": comparison,
        "coverage": "PRE_POST_DISPATCH_CANDIDATE_BYTES_ONLY",
        "reason": None,
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
    try:
        files = {
            path.relative_to(root).as_posix(): _file_sha256(path)
            for path in sorted(locks)
        }
    except OSError:
        return {
            "status": "UNKNOWN",
            "files": [],
            "sha256": "UNKNOWN",
            "reason": "LOCKFILE_CAPTURE_FAILED",
        }
    return {
        "status": "LOCKFILE_PRESENT_UNVERIFIED",
        "files": sorted(files),
        "sha256": _canonical_hash(files),
        "reason": "ACTIVE_ENVIRONMENT_NOT_PROVEN",
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
        "schema": "operant-execution-binding.v5",
        "input_binding": input_binding,
        "harness": {
            "files": sorted(harness),
            "sha256": _canonical_hash(harness),
        },
        "dependency_lock": dependency,
        "source_state": _source_state(root),
        "environment": environment,
        "subject_runtime": _subject_runtime_binding(command, root=root),
        "post_dispatch_runtime": _unassessed_post_dispatch_runtime(),
        "process_image_identity": _unknown_process_image_identity(command),
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
                    "post_dispatch_runtime",
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
    runtime_root: Path,
    runtime_command: list[str] | None,
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
    if completed.get("schema") in {
        "operant-execution-binding.v4",
        "operant-execution-binding.v5",
    }:
        pre_dispatch_runtime = completed.get("subject_runtime")
        if not isinstance(pre_dispatch_runtime, dict):
            raise ValueError("execution binding lacks pre-dispatch runtime evidence")
        completed["post_dispatch_runtime"] = _post_dispatch_runtime_binding(
            pre_dispatch_runtime,
            runtime_command,
            root=runtime_root.resolve(),
        )
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


def _validate_execution_binding(binding: dict[str, Any]) -> list[str]:
    errors = []
    binding_schema = binding.get("schema")
    if binding_schema not in {
        "operant-execution-binding.v1",
        "operant-execution-binding.v2",
        "operant-execution-binding.v3",
        "operant-execution-binding.v4",
        "operant-execution-binding.v5",
    }:
        errors.append("unsupported execution binding schema")
    expected_keys = {
        "operant-execution-binding.v3": EXECUTION_BINDING_V3_KEYS,
        "operant-execution-binding.v4": EXECUTION_BINDING_V4_KEYS,
        "operant-execution-binding.v5": EXECUTION_BINDING_V5_KEYS,
    }.get(binding_schema, EXECUTION_BINDING_BASE_KEYS)
    if set(binding) != expected_keys:
        errors.append("execution binding fields are not exact")
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
        if not _is_sha256(input_binding.get(input_field)):
            errors.append(f"{input_field} must be a lowercase SHA-256")
    stdin_hash = input_binding.get("stdin_sha256")
    if stdin_hash != "NONE" and not _is_sha256(stdin_hash):
        errors.append("stdin_sha256 must be SHA-256 or NONE")
    command_hash = input_binding.get("argv_sha256")
    if command_hash != "UNKNOWN" and not _is_sha256(command_hash):
        errors.append("argv_sha256 must be SHA-256 or UNKNOWN")
    cwd_class = input_binding.get("cwd_class")
    if not isinstance(cwd_class, str) or not cwd_class.strip():
        errors.append("cwd class is missing")
    output_mode = input_binding.get("output_mode")
    if not isinstance(output_mode, str) or not output_mode.strip():
        errors.append("output mode is missing")
    timeout_seconds = input_binding.get("timeout_seconds")
    if timeout_seconds != "UNKNOWN" and (
        not isinstance(timeout_seconds, int)
        or isinstance(timeout_seconds, bool)
        or timeout_seconds < 1
    ):
        errors.append("timeout_seconds must be positive or UNKNOWN")

    harness = binding.get("harness")
    if (
        not isinstance(harness, dict)
        or set(harness) != {"files", "sha256"}
        or not isinstance(harness.get("files"), list)
        or not harness["files"]
        or any(
            not isinstance(name, str)
            or not name
            or Path(name).is_absolute()
            or ".." in Path(name).parts
            or Path(name).as_posix() != name
            for name in harness["files"]
        )
        or harness["files"] != sorted(set(harness["files"]))
        or not _is_sha256(harness.get("sha256"))
    ):
        errors.append("harness binding is incomplete")

    dependency = binding.get("dependency_lock")
    if (
        not isinstance(dependency, dict)
        or set(dependency) != {"status", "files", "sha256", "reason"}
    ):
        errors.append("dependency lock status is invalid")
    elif binding_schema == "operant-execution-binding.v1":
        if dependency.get("status") not in {"LOCKED", "UNKNOWN"}:
            errors.append("dependency lock status is invalid")
        elif dependency["status"] == "LOCKED":
            if not _is_sha256(dependency.get("sha256")):
                errors.append("bound dependency lock lacks SHA-256")
        elif dependency.get("sha256") != "UNKNOWN":
            errors.append("unknown dependency lock must not assert SHA-256")
    elif dependency.get("status") == "LOCKFILE_PRESENT_UNVERIFIED":
        files = dependency.get("files")
        if (
            not isinstance(files, list)
            or not files
            or not all(isinstance(name, str) for name in files)
            or files != sorted(set(files))
            or any(name not in DEPENDENCY_LOCK_CANDIDATES for name in files)
        ):
            errors.append("lockfile evidence is incomplete")
        if not _is_sha256(dependency.get("sha256")):
            errors.append("bound dependency lock lacks SHA-256")
        if dependency.get("reason") != "ACTIVE_ENVIRONMENT_NOT_PROVEN":
            errors.append("lockfile evidence overstates active environment linkage")
    elif dependency.get("status") == "UNKNOWN" and (
        dependency.get("sha256") != "UNKNOWN"
        or dependency.get("files") != []
        or dependency.get("reason")
        not in {
            "NO_RELEVANT_PYTHON_LOCKFILE",
            "LOCKFILE_CAPTURE_FAILED",
        }
    ):
        errors.append("unknown dependency lock carries contradictory evidence")
    elif dependency.get("status") != "UNKNOWN":
        errors.append("dependency lock status is invalid")

    source = binding.get("source_state")
    if not isinstance(source, dict):
        errors.append("source state is missing")
    else:
        expected_source_fields = {
            "commit",
            "dirty",
            "dirty_state_sha256",
        }
        if binding_schema in {
            "operant-execution-binding.v2",
            "operant-execution-binding.v3",
            "operant-execution-binding.v4",
            "operant-execution-binding.v5",
        }:
            expected_source_fields.add("reconstruction")
        if set(source) != expected_source_fields:
            errors.append("source state fields are not exact")
        commit = source.get("commit")
        dirty = source.get("dirty")
        dirty_hash = source.get("dirty_state_sha256")
        reconstruction = source.get("reconstruction")
        if binding_schema == "operant-execution-binding.v1":
            if commit != "UNKNOWN" and (
                not isinstance(commit, str)
                or not re.fullmatch(r"[0-9a-f]{40,64}", commit)
            ):
                errors.append("source commit is invalid")
            if dirty not in {True, False, "UNKNOWN"}:
                errors.append("source dirty state is invalid")
            if dirty_hash != "UNKNOWN" and not _is_sha256(dirty_hash):
                errors.append("source dirty-state digest is invalid")
        elif commit == "UNKNOWN":
            if (
                dirty != "UNKNOWN"
                or dirty_hash != "UNKNOWN"
                or reconstruction != "UNKNOWN"
            ):
                errors.append("unknown source state carries contradictory evidence")
        elif (
            not isinstance(commit, str)
            or not re.fullmatch(r"[0-9a-f]{40,64}", commit)
        ):
            errors.append("source commit is invalid")
        elif (
            not isinstance(dirty, bool)
            or not _is_sha256(dirty_hash)
            or reconstruction
            != ("DIRTY_DIGEST_ONLY" if dirty else "CLEAN_COMMIT")
        ):
            errors.append("source reconstruction state is inconsistent")

    environment = binding.get("environment")
    if (
        not isinstance(environment, dict)
        or set(environment) != {"facts", "sha256"}
        or not isinstance(environment.get("facts"), dict)
    ):
        errors.append("environment facts are missing")
    elif set(environment["facts"]) != ENVIRONMENT_FACT_KEYS:
        errors.append("environment fact fields are not exact")
    elif environment.get("sha256") != _try_canonical_hash(environment["facts"]):
        errors.append("environment digest mismatch")
    elif binding_schema in {
        "operant-execution-binding.v2",
        "operant-execution-binding.v3",
        "operant-execution-binding.v4",
        "operant-execution-binding.v5",
    }:
        facts = environment["facts"]
        for name in ENVIRONMENT_FACT_KEYS - {"cpu_count"}:
            if not isinstance(facts.get(name), str) or not facts[name].strip():
                errors.append(f"environment {name} must be a non-empty string")
        executable_name = facts.get("python_executable_name")
        if (
            isinstance(executable_name, str)
            and Path(executable_name).name != executable_name
        ):
            errors.append("environment python executable must be a basename")
        cpu_count = facts.get("cpu_count")
        if cpu_count != "UNKNOWN" and (
            not isinstance(cpu_count, int)
            or isinstance(cpu_count, bool)
            or cpu_count < 1
        ):
            errors.append("environment cpu_count must be positive or UNKNOWN")

    if binding_schema in {
        "operant-execution-binding.v3",
        "operant-execution-binding.v4",
        "operant-execution-binding.v5",
    }:
        runtime = binding.get("subject_runtime")
        runtime_fields = {
            "status",
            "requested_executable_name",
            "resolved_executable_name",
            "executable_sha256",
            "executable_size_bytes",
            "version",
            "version_reason",
            "coverage",
            "reason",
        }
        if not isinstance(runtime, dict) or set(runtime) != runtime_fields:
            errors.append("subject runtime binding is incomplete")
        else:
            status = runtime.get("status")
            requested_name = runtime.get("requested_executable_name")
            if (
                not isinstance(requested_name, str)
                or not requested_name
                or Path(requested_name).name != requested_name
            ):
                errors.append("subject runtime requested name is invalid")
            if (
                runtime.get("version") != "UNKNOWN"
                or runtime.get("version_reason")
                != "NOT_QUERIED_TO_PRESERVE_NO_SIDE_EFFECT_BOUNDARY"
            ):
                errors.append("subject runtime version evidence is overstated")
            if status == "PRE_DISPATCH_EXECUTABLE_BYTES_BOUND":
                resolved_name = runtime.get("resolved_executable_name")
                size = runtime.get("executable_size_bytes")
                if (
                    not isinstance(resolved_name, str)
                    or not resolved_name
                    or Path(resolved_name).name != resolved_name
                    or not _is_sha256(runtime.get("executable_sha256"))
                    or not isinstance(size, int)
                    or isinstance(size, bool)
                    or size < 0
                    or runtime.get("coverage")
                    != "PRE_DISPATCH_CANDIDATE_BYTES_ONLY"
                    or runtime.get("reason") is not None
                ):
                    errors.append("subject runtime byte binding is inconsistent")
            elif status == "UNKNOWN":
                if (
                    runtime.get("resolved_executable_name") != "UNKNOWN"
                    or runtime.get("executable_sha256") != "UNKNOWN"
                    or runtime.get("executable_size_bytes") != "UNKNOWN"
                    or runtime.get("coverage") != "UNKNOWN"
                    or runtime.get("reason")
                    not in {
                        "NO_EXECUTABLE_DISPATCH",
                        "EXECUTABLE_NOT_FOUND",
                        "EXECUTABLE_CAPTURE_FAILED",
                    }
                ):
                    errors.append("unknown subject runtime carries evidence")
            else:
                errors.append("subject runtime status is invalid")

    if binding_schema in {
        "operant-execution-binding.v4",
        "operant-execution-binding.v5",
    }:
        post_runtime = binding.get("post_dispatch_runtime")
        post_fields = {
            "status",
            "resolved_executable_name",
            "executable_sha256",
            "executable_size_bytes",
            "comparison",
            "coverage",
            "reason",
        }
        if not isinstance(post_runtime, dict) or set(post_runtime) != post_fields:
            errors.append("post-dispatch runtime binding is incomplete")
        else:
            post_status = post_runtime.get("status")
            if post_status == "POST_DISPATCH_EXECUTABLE_BYTES_BOUND":
                post_name = post_runtime.get("resolved_executable_name")
                post_size = post_runtime.get("executable_size_bytes")
                pre_runtime = binding.get("subject_runtime")
                expected_comparison = (
                    "MATCHED"
                    if isinstance(pre_runtime, dict)
                    and (
                        post_name,
                        post_runtime.get("executable_sha256"),
                        post_size,
                    )
                    == (
                        pre_runtime.get("resolved_executable_name"),
                        pre_runtime.get("executable_sha256"),
                        pre_runtime.get("executable_size_bytes"),
                    )
                    else "DRIFTED"
                )
                if (
                    not isinstance(post_name, str)
                    or not post_name
                    or Path(post_name).name != post_name
                    or not _is_sha256(post_runtime.get("executable_sha256"))
                    or not isinstance(post_size, int)
                    or isinstance(post_size, bool)
                    or post_size < 0
                    or post_runtime.get("comparison") not in {"MATCHED", "DRIFTED"}
                    or post_runtime.get("comparison") != expected_comparison
                    or post_runtime.get("coverage")
                    != "PRE_POST_DISPATCH_CANDIDATE_BYTES_ONLY"
                    or post_runtime.get("reason") is not None
                ):
                    errors.append("post-dispatch runtime byte binding is inconsistent")
            elif post_status in {"NOT_ASSESSED", "UNKNOWN"}:
                valid_reason = (
                    post_runtime.get("reason") == "DISPATCH_RESULT_NOT_AVAILABLE"
                    if post_status == "NOT_ASSESSED"
                    else post_runtime.get("reason")
                    in {
                        "NO_EXECUTABLE_DISPATCH",
                        "PRE_DISPATCH_CANDIDATE_UNAVAILABLE",
                        "POST_DISPATCH_CANDIDATE_UNAVAILABLE",
                    }
                )
                if (
                    post_runtime.get("resolved_executable_name") != "UNKNOWN"
                    or post_runtime.get("executable_sha256") != "UNKNOWN"
                    or post_runtime.get("executable_size_bytes") != "UNKNOWN"
                    or post_runtime.get("comparison") != "UNKNOWN"
                    or post_runtime.get("coverage") != "UNKNOWN"
                    or not valid_reason
                ):
                    errors.append("unknown post-dispatch runtime carries evidence")
            else:
                errors.append("post-dispatch runtime status is invalid")

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
        if raw_digest != "UNKNOWN" and not _is_sha256(raw_digest):
            errors.append("raw result envelope digest is invalid")
        final_answer_digest = observation.get("final_answer_sha256")
        if final_answer_digest != "UNKNOWN" and not _is_sha256(
            final_answer_digest
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

    observation_completed = bool(observation) and (
        observation.get("comparison_status") != "UNKNOWN"
        or observation.get("raw_result_envelope_sha256") != "UNKNOWN"
    )
    if binding_schema == "operant-execution-binding.v5":
        process_identity = binding.get("process_image_identity")
        identity_fields = {
            "status",
            "kernel_observed_cdhash",
            "kernel_observed_signing_id",
            "kernel_observed_team_id",
            "kernel_observed_pidversion",
            "evidence_source",
            "coverage",
            "reason",
        }
        if (
            not isinstance(process_identity, dict)
            or set(process_identity) != identity_fields
        ):
            errors.append("process image identity binding is incomplete")
        elif (
            process_identity.get("status") != "UNKNOWN"
            or process_identity.get("kernel_observed_cdhash") != "UNKNOWN"
            or process_identity.get("kernel_observed_signing_id") != "UNKNOWN"
            or process_identity.get("kernel_observed_team_id") != "UNKNOWN"
            or process_identity.get("kernel_observed_pidversion") != "UNKNOWN"
            or process_identity.get("evidence_source") != "NOT_CAPTURED"
            or process_identity.get("coverage") != "UNKNOWN"
            or process_identity.get("reason")
            not in {
                "KERNEL_EXEC_ATTESTATION_NOT_CONFIGURED",
                "NO_LOCAL_PROCESS_DISPATCH",
            }
        ):
            errors.append("process image identity evidence is overstated")
        else:
            subject_runtime = binding.get("subject_runtime")
            expected_reason = (
                "NO_LOCAL_PROCESS_DISPATCH"
                if isinstance(subject_runtime, dict)
                and subject_runtime.get("reason") == "NO_EXECUTABLE_DISPATCH"
                else "KERNEL_EXEC_ATTESTATION_NOT_CONFIGURED"
            )
            if process_identity.get("reason") != expected_reason:
                errors.append("process image identity reason contradicts dispatch")

    if binding_schema in {
        "operant-execution-binding.v4",
        "operant-execution-binding.v5",
    }:
        post_status = (
            binding.get("post_dispatch_runtime", {}).get("status")
            if isinstance(binding.get("post_dispatch_runtime"), dict)
            else None
        )
        if observation_completed and post_status == "NOT_ASSESSED":
            errors.append("completed execution lacks post-dispatch runtime assessment")
        if not observation_completed and post_status != "NOT_ASSESSED":
            errors.append("pre-dispatch binding carries post-dispatch runtime evidence")

    if binding.get("replay_class") != "INPUT_BOUND_NOT_REPLAYABLE":
        errors.append("unsupported replay class")
    pre_dispatch = {
        **{
            key: value
            for key, value in binding.items()
            if key
            not in {
                "model_observation",
                "post_dispatch_runtime",
                "pre_dispatch_sha256",
                "completion_sha256",
            }
        },
        "requested_model_id": (
            observation.get("requested_model_id") if observation else None
        ),
    }
    if binding.get("pre_dispatch_sha256") != _try_canonical_hash(pre_dispatch):
        errors.append("pre-dispatch digest mismatch")
    completion = binding.get("completion_sha256")
    completed_payload = {
        key: value for key, value in binding.items() if key != "completion_sha256"
    }
    if observation_completed:
        if completion != _try_canonical_hash(completed_payload):
            errors.append("completed execution binding digest mismatch")
    elif completion != "UNKNOWN":
        errors.append("unknown model observation must not assert completion digest")
    return errors


def validate_execution_binding(binding: Any) -> list[str]:
    """Return validation errors for every JSON value; never raise on shape."""
    try:
        return _validate_execution_binding(binding)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ["execution binding contains invalid JSON types"]


def _validate_run_manifest_v3(manifest: dict[str, Any]) -> list[str]:
    """Validate the historical v3 shape without silently upgrading it."""
    errors: list[str] = []
    if set(manifest) != RUN_MANIFEST_V3_KEYS:
        errors.append("manifest fields are not exact")
    if manifest.get("manifest_schema") != "operant-run-manifest.v3":
        errors.append("unsupported manifest schema")
    binding = manifest.get("execution_binding")
    if not isinstance(binding, dict):
        errors.append("execution binding is missing")
        return errors
    if binding.get("schema") != "operant-execution-binding.v1":
        errors.append("v3 manifest requires execution binding v1")
    binding_errors = validate_execution_binding(binding)
    if binding_errors:
        errors.extend(f"execution binding: {error}" for error in binding_errors)
        return errors
    if manifest.get("model_id") != binding["model_observation"]["requested_model_id"]:
        errors.append("model_id does not match requested model binding")
    if manifest.get("evaluation_role") not in VALID_EVALUATION_ROLES:
        errors.append("evaluation role is invalid")
    if manifest.get("confirmatory_eligible") is not False:
        errors.append("v3 manifest cannot claim confirmatory eligibility")
    if not _is_sha256(manifest.get("prompt_hash")):
        errors.append("prompt_hash must be a lowercase SHA-256")
    if not _is_sha256(manifest.get("case_bundle_sha256")):
        errors.append("case_bundle_sha256 must be a lowercase SHA-256")
    return errors


def _validate_run_manifest_current(
    manifest: dict[str, Any],
    *,
    manifest_schema: str,
    binding_schema: str,
) -> list[str]:
    """Validate a core-digested manifest without constructor history."""
    errors: list[str] = []
    if set(manifest) != RUN_MANIFEST_V5_KEYS:
        errors.append("manifest fields are not exact")
    if manifest.get("manifest_schema") != manifest_schema:
        errors.append("unsupported manifest schema")
    for field_name in (
        "run_label",
        "case_id",
        "subject_shell",
        "model_id",
        "prompt_contract",
        "tool_policy",
        "case_split",
    ):
        if not isinstance(manifest.get(field_name), str) or not manifest[
            field_name
        ].strip():
            errors.append(f"{field_name} must be a non-empty string")
    if not isinstance(manifest.get("axis"), str) or not manifest["axis"].strip():
        errors.append("axis must be a non-empty string")
    if manifest.get("evaluation_role") not in VALID_EVALUATION_ROLES:
        errors.append("evaluation role is invalid")
    if manifest.get("confirmatory_eligible") is not False:
        errors.append("core-digested manifest cannot claim confirmatory eligibility")
    expected_core = _try_canonical_hash(
        {
            key: value
            for key, value in manifest.items()
            if key != "manifest_core_sha256"
        }
    )
    if manifest.get("manifest_core_sha256") != expected_core:
        errors.append("manifest core digest mismatch")
    if not _is_sha256(manifest.get("prompt_hash")):
        errors.append("prompt_hash must be a lowercase SHA-256")
    if not _is_sha256(manifest.get("case_bundle_sha256")):
        errors.append("case_bundle_sha256 must be a lowercase SHA-256")
    case_count = manifest.get("case_bundle_case_count")
    if (
        not isinstance(case_count, int)
        or isinstance(case_count, bool)
        or case_count < 1
    ):
        errors.append("case_bundle_case_count must be positive")
    created_at = manifest.get("created_at")
    if not isinstance(created_at, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        created_at,
    ):
        errors.append("created_at must be canonical UTC seconds")
    else:
        try:
            datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        except ValueError:
            errors.append("created_at is not a real timestamp")
    repeat_id = manifest.get("repeat_id")
    if repeat_id is not None and (
        not isinstance(repeat_id, int)
        or isinstance(repeat_id, bool)
        or repeat_id < 1
    ):
        errors.append("repeat_id must be a positive integer or null")
    for optional_text in (
        "thinking",
        "source_thread_id",
        "thread_container",
    ):
        value = manifest.get(optional_text)
        if value is not None and (
            not isinstance(value, str) or not value.strip()
        ):
            errors.append(f"{optional_text} must be non-empty or null")
    queue_file = manifest.get("source_queue_file")
    queue_sha = manifest.get("source_queue_sha256")
    if (queue_file is None) != (queue_sha is None):
        errors.append("source queue path and digest must appear together")
    if queue_file is not None:
        if (
            not isinstance(queue_file, str)
            or not queue_file.strip()
            or Path(queue_file).is_absolute()
            or ".." in Path(queue_file).parts
        ):
            errors.append("source_queue_file must be a safe relative path")
        if not _is_sha256(queue_sha):
            errors.append("source_queue_sha256 must be a lowercase SHA-256")
    cost = manifest.get("cost_usd")
    if cost is not None and not _is_nonnegative_finite_number(cost):
        errors.append("cost_usd must be non-negative or null")

    binding = manifest.get("execution_binding")
    if not isinstance(binding, dict):
        errors.append("execution binding is missing")
        return errors
    binding_errors = validate_execution_binding(binding)
    if binding_errors:
        errors.extend(f"execution binding: {error}" for error in binding_errors)
        return errors
    if binding.get("schema") != binding_schema:
        errors.append(
            f"{manifest_schema} requires {binding_schema}"
        )
    requested_model = binding["model_observation"]["requested_model_id"]
    if manifest.get("model_id") != requested_model:
        errors.append("model_id does not match requested model binding")
    input_binding = binding["input_binding"]
    if manifest.get("prompt_hash") != input_binding["delivered_prompt_sha256"]:
        errors.append("prompt_hash does not match delivered prompt binding")
    if stable_hash(str(manifest.get("tool_policy") or "")) != input_binding[
        "tool_policy_sha256"
    ]:
        errors.append("tool_policy does not match execution binding")
    return errors


def validate_run_manifest_v3(manifest: Any) -> list[str]:
    """Return historical-v3 validation errors for every JSON value."""
    try:
        return _validate_run_manifest_v3(manifest)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ["v3 manifest contains invalid JSON types"]


def validate_run_manifest_v4(manifest: Any) -> list[str]:
    """Return v4 validation errors for every JSON value."""
    try:
        return _validate_run_manifest_current(
            manifest,
            manifest_schema="operant-run-manifest.v4",
            binding_schema="operant-execution-binding.v2",
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ["v4 manifest contains invalid JSON types"]


def validate_run_manifest_v5(manifest: Any) -> list[str]:
    """Return v5 validation errors for every JSON value."""
    try:
        return _validate_run_manifest_current(
            manifest,
            manifest_schema="operant-run-manifest.v5",
            binding_schema="operant-execution-binding.v3",
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ["v5 manifest contains invalid JSON types"]


def validate_run_manifest_v6(manifest: Any) -> list[str]:
    """Return v6 validation errors for every JSON value."""
    try:
        return _validate_run_manifest_current(
            manifest,
            manifest_schema="operant-run-manifest.v6",
            binding_schema="operant-execution-binding.v4",
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ["v6 manifest contains invalid JSON types"]


def validate_run_manifest_v7(manifest: Any) -> list[str]:
    """Return v7 validation errors for every JSON value."""
    try:
        return _validate_run_manifest_current(
            manifest,
            manifest_schema="operant-run-manifest.v7",
            binding_schema="operant-execution-binding.v5",
        )
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return ["v7 manifest contains invalid JSON types"]


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
    source_queue_sha256: str | None = None
    thread_container: str | None = None
    cost_usd: float | None = None
    manifest_core_sha256: str = field(default="UNKNOWN", init=False)
    manifest_schema: str = field(default="operant-run-manifest.v7", init=False)
    confirmatory_eligible: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if self.cost_usd is not None and not _is_nonnegative_finite_number(
            self.cost_usd
        ):
            raise ValueError("invalid v7 run manifest: cost_usd is invalid")
        manifest = asdict(self)
        self.manifest_core_sha256 = _canonical_hash(
            {
                key: value
                for key, value in manifest.items()
                if key != "manifest_core_sha256"
            }
        )
        errors = validate_run_manifest_v7(asdict(self))
        if errors:
            raise ValueError("invalid v7 run manifest: " + "; ".join(errors))


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
    process_exit_code: int | None = None

    def __post_init__(self) -> None:
        block_reason = scoring_block_reason(asdict(self.manifest))
        if block_reason and (self.score_row is not None or self.judge_row is not None):
            raise ValueError(f"blocked receipt cannot carry scores: {block_reason}")
        if (
            self.score_row is not None or self.judge_row is not None
        ) and self.parse_status != "ok":
            raise ValueError("scored receipt must have parse_status ok")
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


def _scoring_block_reason(manifest: dict[str, Any]) -> str | None:
    schema = manifest.get("manifest_schema")
    if schema in {None, "operant-run-manifest.v1", "operant-run-manifest.v2"}:
        if "execution_binding" in manifest:
            return "invalid_execution_binding"
        return None
    if schema == "operant-run-manifest.v3":
        if validate_run_manifest_v3(manifest):
            return "invalid_execution_binding"
    elif schema == "operant-run-manifest.v4":
        if validate_run_manifest_v4(manifest):
            return "invalid_execution_binding"
    elif schema == "operant-run-manifest.v5":
        if validate_run_manifest_v5(manifest):
            return "invalid_execution_binding"
    elif schema == "operant-run-manifest.v6":
        if validate_run_manifest_v6(manifest):
            return "invalid_execution_binding"
    elif schema == "operant-run-manifest.v7":
        if validate_run_manifest_v7(manifest):
            return "invalid_execution_binding"
    else:
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
    post_runtime = binding.get("post_dispatch_runtime")
    if (
        isinstance(post_runtime, dict)
        and post_runtime.get("comparison") == "DRIFTED"
    ):
        return "runtime_candidate_drift"
    return None


def scoring_block_reason(manifest: Any) -> str | None:
    """Return a conservative score gate for every JSON value."""
    try:
        return _scoring_block_reason(manifest)
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return "invalid_execution_binding"


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
    if not isinstance(data, dict):
        return "invalid_execution_binding"
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        return "invalid_execution_binding"
    if manifest.get("run_label") != run_label or manifest.get("case_id") != case_id:
        return "receipt_identity_mismatch"
    reason = scoring_block_reason(manifest)
    if reason:
        return reason
    if manifest.get("manifest_schema") in {
        "operant-run-manifest.v3",
        "operant-run-manifest.v4",
        "operant-run-manifest.v5",
        "operant-run-manifest.v6",
        "operant-run-manifest.v7",
    }:
        bound_answer = (
            manifest.get("execution_binding", {})
            .get("model_observation", {})
            .get("final_answer_sha256")
        )
        if bound_answer != stable_hash(str(data.get("final_answer") or "")):
            return "receipt_output_binding_mismatch"
    if data.get("parse_status") != "ok":
        return f"receipt_not_scoreable:{data.get('parse_status') or 'unknown'}"
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
    if not isinstance(data, dict):
        return "invalid_execution_binding"
    if data.get("final_answer") != final_answer:
        return "receipt_output_mismatch"
    manifest = data.get("manifest", {})
    if manifest.get("manifest_schema") in {
        "operant-run-manifest.v3",
        "operant-run-manifest.v4",
        "operant-run-manifest.v5",
        "operant-run-manifest.v6",
        "operant-run-manifest.v7",
    }:
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
    payload = (
        json.dumps(
            data,
            indent=2,
            sort_keys=True,
            default=_json_default,
        )
        + "\n"
    ).encode("utf-8")
    _write_bytes_exclusive(path, payload)


def write_text_exclusive(path: Path, text: str) -> None:
    """Atomically publish UTF-8 text exactly once."""
    _write_bytes_exclusive(path, text.encode("utf-8"))


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
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
    ensure_exclusive_path_slot(artifact_path(root, run_label, case_id))


def ensure_exclusive_path_slot(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite evidence artifact: {path}")


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
