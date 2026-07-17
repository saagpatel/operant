"""Public artifact contract checks for OPERANT exports."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

from .artifacts import VALID_EVALUATION_ROLES

REQUIRED_PUBLIC_FILES = {
    "README.md",
    "benchmark-card.json",
    "calibration-profiles.json",
    "evaluation-split-registry.json",
    "lab-run-status.json",
    "methodology.md",
}

FORBIDDEN_PUBLIC_KEYS = {
    "answer",
    "final_answer",
    "full_prompt",
    "prompt",
    "raw_prompt",
    "task_prompt",
    "transcript",
}

FORBIDDEN_PUBLIC_TEXT_PATTERNS = {
    "absolute local path": re.compile(
        r"(?i)(?:^|[\s\"'`=:(])(?:/(?:Users|home|tmp|var/folders|Volumes)/|[A-Z]:\\Users\\)"
    ),
    "file URI": re.compile(r"(?i)\bfile://"),
    "home-relative path": re.compile(r"(?<!\w)~/"),
    "secret-like token": re.compile(
        r"\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9_]{12,}|"
        r"github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{12,}|"
        r"xox[baprs]-[A-Za-z0-9-]{20,})\b"
    ),
}

REQUIRED_HISTORICAL_CLAIM_STATUS = {
    "evidence_class": "historical_unverified_receipt",
    "score_recalculation_from_bound_bytes": "SUPPORTED",
    "historical_as_run_corpus_identity": "UNKNOWN",
    "historical_as_run_protocol_identity": "UNKNOWN",
    "dispatch_freshness": "UNKNOWN",
    "served_model_identity": "UNKNOWN",
    "independent_replication": "UNKNOWN",
    "cross_model_ranking": "NOT_DURABLE",
    "inferential_statistics_as_model_evidence": "NOT_DURABLE",
}

REQUIRED_LOCAL_CLAIM_STATUS = {
    "evidence_class": "self_reported_local_receipt",
    "score_recalculation_from_bound_bytes": "CURRENT_CHECKOUT_ONLY",
    "source_receipt_byte_binding": "SUPPORTED",
    "current_public_corpus_identity": "SUPPORTED",
    "current_public_protocol_identity": "SUPPORTED",
    "private_case_overlay_identity": "CURRENT_CHECKOUT_HASH_BOUND",
    "as_run_private_case_overlay_identity": "UNKNOWN",
    "independent_as_run_score_recalculation": "UNKNOWN",
    "served_model_identity": "UNKNOWN",
    "independent_replication": "UNKNOWN",
    "cross_profile_ranking": "NOT_DURABLE",
}

REQUIRED_ORPHANED_CLAIM_STATUS = {
    "evidence_class": "orphaned_public_artifact",
    "score_recalculation_from_bound_bytes": "UNKNOWN",
    "source_receipt_byte_binding": "UNKNOWN",
    "served_model_identity": "UNKNOWN",
    "independent_replication": "UNKNOWN",
    "cross_profile_ranking": "NOT_DURABLE",
}

REQUIRED_BENCHMARK_CLAIM_STATUS = {
    "benchmark_definition_and_metric": "SUPPORTED",
    "historical_model_performance": "NOT_DURABLE",
    "served_model_identity": "UNKNOWN",
    "independent_replication": "UNKNOWN",
    "deployment_safety_or_certification": "NOT_SUPPORTED",
}

REQUIRED_CALIBRATION_CLAIM_STATUS = {
    "historical_reference_profiles": REQUIRED_HISTORICAL_CLAIM_STATUS,
    "local_lab_profiles": REQUIRED_LOCAL_CLAIM_STATUS,
}

EXPECTED_BINDING_KEYS = {
    "source_indexes": {
        "operant_index.jsonl",
        "operant_orchestration_judge_index.jsonl",
        "operant_orchestration_judge_opus_index.jsonl",
    },
    "current_public_corpus": {
        "operant_cases.json",
        "operant_axis2_cases.json",
        "operant_axis3_cases.json",
        "operant_axis4_cases.json",
        "operant_templates.json",
    },
    "current_public_protocol": {
        "score_operant.py",
        "score_orchestration.py",
        "score_orchestration_judge.py",
        "artifacts.py",
        "inventory.py",
    },
}
VALID_EVALUATION_BINDING_STATUSES = {
    "V2_BOUND_NONCONFIRMATORY",
    "UNKNOWN",
    "MIXED_UNKNOWN",
}

CURRENT_BINDING_PATHS = {
    "current_public_corpus": {
        name: Path(name) for name in EXPECTED_BINDING_KEYS["current_public_corpus"]
    },
    "current_public_protocol": {
        "score_operant.py": Path("score_operant.py"),
        "score_orchestration.py": Path("score_orchestration.py"),
        "score_orchestration_judge.py": Path("score_orchestration_judge.py"),
        "artifacts.py": Path("operant_lab") / "artifacts.py",
        "inventory.py": Path("operant_lab") / "inventory.py",
    },
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
PRIVATE_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
RECEIPT_KEY_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*\.json$"
)


def _read_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: unreadable JSON: {exc}")
        return None


def _walk_forbidden_keys(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_PUBLIC_KEYS:
                errors.append(f"{path}: forbidden public key {key!r}")
            _walk_forbidden_keys(item, f"{path}.{key}", errors)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _walk_forbidden_keys(item, f"{path}[{index}]", errors)


def _scan_forbidden_text(text: str, path: str, errors: list[str]) -> None:
    for label, pattern in FORBIDDEN_PUBLIC_TEXT_PATTERNS.items():
        match = pattern.search(text)
        if match:
            snippet = match.group(0).strip()
            errors.append(f"{path}: forbidden {label}: {snippet!r}")


def _validate_digest_map(
    value: Any,
    *,
    label: str,
    errors: list[str],
    expected_keys: set[str] | None = None,
) -> dict[str, str] | None:
    if not isinstance(value, dict):
        errors.append(f"benchmark-card.json: evidence binding {label} must be an object")
        return None
    if expected_keys is not None and set(value) != expected_keys:
        errors.append(
            f"benchmark-card.json: evidence binding {label} has unexpected keys"
        )
    if not all(
        isinstance(item, str) and SHA256_RE.fullmatch(item)
        for item in value.values()
    ):
        errors.append(
            f"benchmark-card.json: evidence binding {label} contains unusable digest"
        )
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_sha256(path: Path, *, label: str, errors: list[str]) -> str | None:
    try:
        return _sha256(path)
    except OSError:
        errors.append(
            f"benchmark-card.json: cannot read required evidence bytes for {label}"
        )
        return None


def _contained_regular_file(
    root: Path,
    key: str,
    *,
    label: str,
    nested: bool,
    errors: list[str],
) -> Path | None:
    candidate = root / key
    symlink_paths = [root, candidate]
    if nested:
        symlink_paths.append(candidate.parent)
    if any(path.is_symlink() for path in symlink_paths):
        errors.append(
            f"benchmark-card.json: {label} evidence must not use symlinks for {key}"
        )
        return None
    try:
        resolved_root = root.resolve(strict=True)
        resolved_candidate = candidate.resolve(strict=True)
        resolved_candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        errors.append(
            f"benchmark-card.json: {label} evidence is unavailable or outside "
            f"the selected root for {key}"
        )
        return None
    if not resolved_candidate.is_file():
        errors.append(
            f"benchmark-card.json: {label} evidence is not a regular file for {key}"
        )
        return None
    return resolved_candidate


def _validate_evidence_binding(
    binding: dict[str, Any],
    errors: list[str],
    *,
    source_results: Path | None = None,
    lab_runs_dir: Path | None = None,
    private_case_overlays_dir: Path | None = None,
) -> None:
    maps: dict[str, dict[str, str]] = {}
    for label in (
        "source_indexes",
        "current_public_corpus",
        "current_public_protocol",
    ):
        value = _validate_digest_map(
            binding.get(label),
            label=label,
            errors=errors,
            expected_keys=EXPECTED_BINDING_KEYS[label],
        )
        if value is not None:
            maps[label] = value
    lab_receipts = _validate_digest_map(
        binding.get("lab_receipts"),
        label="lab_receipts",
        errors=errors,
    )
    if lab_receipts is not None:
        maps["lab_receipts"] = lab_receipts
        if not all(RECEIPT_KEY_RE.fullmatch(str(key)) for key in lab_receipts):
            errors.append(
                "benchmark-card.json: evidence binding lab_receipts contains "
                "unsafe key"
            )
    private_case_overlays = _validate_digest_map(
        binding.get("private_case_overlays"),
        label="private_case_overlays",
        errors=errors,
    )
    if private_case_overlays is not None:
        maps["private_case_overlays"] = private_case_overlays
        if lab_receipts and not private_case_overlays:
            errors.append(
                "benchmark-card.json: local receipt binding requires private "
                "case overlay digests"
            )
        if not all(
            PRIVATE_FILE_RE.fullmatch(str(key)) for key in private_case_overlays
        ):
            errors.append(
                "benchmark-card.json: evidence binding private_case_overlays "
                "contains unsafe key"
            )

    for label in ("source_bundle_sha256", "exporter_sha256"):
        value = binding.get(label)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            errors.append(f"benchmark-card.json: evidence binding {label} is unusable")
    exporter_path = Path(__file__).with_name("export.py")
    if not exporter_path.is_file():
        errors.append(
            "benchmark-card.json: current exporter evidence file is unavailable"
        )
    else:
        exporter_digest = _safe_sha256(
            exporter_path,
            label="export.py",
            errors=errors,
        )
        if (
            exporter_digest is not None
            and binding.get("exporter_sha256") != exporter_digest
        ):
            errors.append(
                "benchmark-card.json: evidence binding exporter digest does not "
                "match the current exporter"
            )

    if source_results is not None:
        expected_source_digests: dict[str, str] = {}
        for name in sorted(EXPECTED_BINDING_KEYS["source_indexes"]):
            path = source_results / name
            if not path.is_file():
                errors.append(
                    "benchmark-card.json: source verification missing required "
                    f"index {name}"
                )
                continue
            digest = _safe_sha256(path, label=name, errors=errors)
            if digest is not None:
                expected_source_digests[name] = digest
        if (
            len(expected_source_digests)
            == len(EXPECTED_BINDING_KEYS["source_indexes"])
            and binding.get("source_indexes") != expected_source_digests
        ):
            errors.append(
                "benchmark-card.json: evidence binding source indexes do not "
                "match the supplied private source bytes"
            )

    if lab_runs_dir is not None and isinstance(lab_receipts, dict):
        expected_lab_receipts: dict[str, str] = {}
        for key in sorted(lab_receipts):
            if not RECEIPT_KEY_RE.fullmatch(str(key)):
                continue
            path = _contained_regular_file(
                lab_runs_dir,
                str(key),
                label="lab receipt",
                nested=True,
                errors=errors,
            )
            if path is None:
                continue
            digest = _safe_sha256(path, label=str(key), errors=errors)
            if digest is not None:
                expected_lab_receipts[str(key)] = digest
        if (
            len(expected_lab_receipts) == len(lab_receipts)
            and lab_receipts != expected_lab_receipts
        ):
            errors.append(
                "benchmark-card.json: evidence binding lab receipts do not "
                "match the supplied local receipt bytes"
            )

    if (
        private_case_overlays_dir is not None
        and isinstance(private_case_overlays, dict)
    ):
        expected_private_overlays: dict[str, str] = {}
        for key in sorted(private_case_overlays):
            if not PRIVATE_FILE_RE.fullmatch(str(key)):
                continue
            path = _contained_regular_file(
                private_case_overlays_dir,
                str(key),
                label="private case",
                nested=False,
                errors=errors,
            )
            if path is None:
                continue
            digest = _safe_sha256(path, label=str(key), errors=errors)
            if digest is not None:
                expected_private_overlays[str(key)] = digest
        if (
            len(expected_private_overlays) == len(private_case_overlays)
            and private_case_overlays != expected_private_overlays
        ):
            errors.append(
                "benchmark-card.json: evidence binding private case overlays do "
                "not match the supplied private case bytes"
            )

    repo_root = Path(__file__).resolve().parent.parent
    for label in ("current_public_corpus", "current_public_protocol"):
        expected_current_digests: dict[str, str] = {}
        for name in sorted(EXPECTED_BINDING_KEYS[label]):
            path = repo_root / CURRENT_BINDING_PATHS[label][name]
            if not path.is_file():
                errors.append(
                    "benchmark-card.json: current checkout is missing required "
                    f"{label} file {name}"
                )
                continue
            digest = _safe_sha256(path, label=name, errors=errors)
            if digest is not None:
                expected_current_digests[name] = digest
        if (
            len(expected_current_digests) == len(EXPECTED_BINDING_KEYS[label])
            and binding.get(label) != expected_current_digests
        ):
            errors.append(
                "benchmark-card.json: evidence binding "
                f"{label} does not match the current checkout bytes"
            )

    for label in (
        "historical_as_run_corpus",
        "historical_as_run_protocol",
        "historical_o1_evidence_manifest_sha256",
    ):
        if binding.get(label) != "UNKNOWN":
            errors.append(
                f"benchmark-card.json: evidence binding {label} must remain UNKNOWN"
            )

    if set(maps) == {
        "source_indexes",
        "lab_receipts",
        "current_public_corpus",
        "current_public_protocol",
        "private_case_overlays",
    }:
        combined = json.dumps(
            {
                "source_indexes": maps["source_indexes"],
                "lab_receipts": maps["lab_receipts"],
                "current_public_corpus": maps["current_public_corpus"],
                "current_public_protocol": maps["current_public_protocol"],
                "private_case_overlays": maps["private_case_overlays"],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        expected_bundle = hashlib.sha256(combined).hexdigest()
        if binding.get("source_bundle_sha256") != expected_bundle:
            errors.append(
                "benchmark-card.json: evidence binding source bundle digest mismatch"
            )


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 3)


def _validate_decision_summary(
    summary: Any,
    *,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(summary, dict):
        errors.append(f"model card {label}: invalid decision summary")
        return
    tpr = summary.get("tpr")
    fpr = summary.get("fpr")
    ocs = summary.get("ocs")
    n = summary.get("n")
    accuracy = summary.get("decision_accuracy")
    case_ids = summary.get("case_ids")
    if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
        errors.append(f"model card {label}: decision n must be a positive integer")
    if (
        not isinstance(case_ids, list)
        or any(not isinstance(case_id, str) or not case_id for case_id in case_ids)
        or len(case_ids) != len(set(case_ids))
        or len(case_ids) != n
    ):
        errors.append(f"model card {label}: decision case_ids do not match n")
    if not all(isinstance(value, (int, float)) for value in (tpr, fpr, ocs)):
        errors.append(f"model card {label}: decision summary lacks numeric TPR/FPR/OCS")
    else:
        if not (0.0 <= float(tpr) <= 1.0 and 0.0 <= float(fpr) <= 1.0):
            errors.append(f"model card {label}: TPR/FPR outside [0, 1]")
        if not -1.0 <= float(ocs) <= 1.0:
            errors.append(f"model card {label}: OCS outside [-1, 1]")
        if ocs != round(float(tpr) - float(fpr), 3):
            errors.append(f"model card {label}: decision OCS does not equal TPR - FPR")
    if not isinstance(accuracy, (int, float)) or not 0.0 <= float(accuracy) <= 1.0:
        errors.append(f"model card {label}: decision accuracy outside [0, 1]")
    axes = summary.get("axis", {})
    if not isinstance(axes, dict):
        errors.append(f"model card {label}: invalid axis summaries")
        return
    for axis, axis_summary in axes.items():
        _validate_decision_summary(
            axis_summary,
            label=f"{label}/{axis}",
            errors=errors,
        )


def _validate_card_aggregates(card: dict[str, Any], errors: list[str]) -> None:
    label = str(card.get("run_family", "<unknown>"))
    decision = card.get("decision")
    if not isinstance(decision, dict):
        errors.append(f"model card {label}: invalid decision block")
        return
    repeats = decision.get("repeats")
    if not isinstance(repeats, dict) or not repeats:
        errors.append(f"model card {label}: decision repeats must be non-empty")
        return
    ocs_values: list[float] = []
    for run_label, summary in repeats.items():
        _validate_decision_summary(
            summary,
            label=f"{label}/{run_label}",
            errors=errors,
        )
        if isinstance(summary, dict) and summary.get("n"):
            value = summary.get("ocs")
            if isinstance(value, (int, float)):
                ocs_values.append(float(value))
    if decision.get("ocs_mean") != _mean(ocs_values):
        errors.append(f"model card {label}: decision ocs_mean aggregate mismatch")
    if decision.get("ocs_stdev") != _stdev(ocs_values):
        errors.append(f"model card {label}: decision ocs_stdev aggregate mismatch")
    if decision.get("repeat_count") != len(ocs_values):
        errors.append(f"model card {label}: decision repeat_count mismatch")

    orchestration = card.get("orchestration_judge")
    if not isinstance(orchestration, dict):
        errors.append(f"model card {label}: invalid orchestration_judge block")
        return
    judge_repeats = orchestration.get("sonnet_judge", {})
    if not isinstance(judge_repeats, dict):
        errors.append(f"model card {label}: invalid sonnet_judge repeats")
        return
    judge_values = [
        float(summary["mean_score"])
        for summary in judge_repeats.values()
        if isinstance(summary, dict)
        and isinstance(summary.get("mean_score"), (int, float))
    ]
    if any(not 0.0 <= value <= 1.0 for value in judge_values):
        errors.append(f"model card {label}: orchestration repeat mean outside [0, 1]")
    if orchestration.get("mean_score") != _mean(judge_values):
        errors.append(f"model card {label}: orchestration mean aggregate mismatch")


def _validate_evaluation_binding_summary(
    binding: Any,
    *,
    label: str,
    errors: list[str],
) -> None:
    if not isinstance(binding, dict):
        errors.append(f"{label}: invalid evaluation_binding")
        return
    status = binding.get("status")
    if status not in VALID_EVALUATION_BINDING_STATUSES:
        errors.append(f"{label}: unsafe evaluation binding status")
    confirmatory = binding.get("confirmatory_eligible")
    if status == "V2_BOUND_NONCONFIRMATORY":
        if confirmatory is not False:
            errors.append(f"{label}: bound evaluation must be non-confirmatory")
        schema_counts = binding.get("manifest_schema_counts")
        role_counts = binding.get("evaluation_role_counts")
        bundle_count = binding.get("case_bundle_count")
        bundle_digest = binding.get("case_bundle_sha256")
        if (
            not isinstance(schema_counts, dict)
            or not set(schema_counts).issubset(
                {
                    "operant-run-manifest.v2",
                    "operant-run-manifest.v3",
                }
            )
            or not schema_counts
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
                for value in schema_counts.values()
            )
        ):
            errors.append(f"{label}: bound evaluation lacks v2-only schema counts")
        if (
            not isinstance(role_counts, dict)
            or not role_counts
            or not set(role_counts).issubset(VALID_EVALUATION_ROLES)
            or any(
                not isinstance(value, int)
                or isinstance(value, bool)
                or value < 1
                for value in role_counts.values()
            )
        ):
            errors.append(f"{label}: bound evaluation lacks explicit role counts")
        if (
            not isinstance(bundle_count, int)
            or isinstance(bundle_count, bool)
            or bundle_count < 1
        ):
            errors.append(f"{label}: bound evaluation lacks case bundle count")
        if not (
            isinstance(bundle_digest, str)
            and (
                bundle_count == 1
                and re.fullmatch(r"[0-9a-f]{64}", bundle_digest)
                or bundle_count > 1
                and bundle_digest == "MULTIPLE"
            )
        ):
            errors.append(f"{label}: bound evaluation lacks case bundle digest")
    elif confirmatory != "UNKNOWN":
        errors.append(f"{label}: unbound evaluation must keep eligibility UNKNOWN")
    if status == "UNKNOWN":
        unknown_schemas = binding.get("manifest_schema_counts")
        unknown_roles = binding.get("evaluation_role_counts")
        unknown_counts_valid = all(
            isinstance(count, int) and not isinstance(count, bool) and count > 0
            for counts in (unknown_schemas, unknown_roles)
            if isinstance(counts, dict)
            for count in counts.values()
        )
        if (
            not isinstance(unknown_schemas, dict)
            or set(unknown_schemas) != {"UNKNOWN"}
            or not isinstance(unknown_roles, dict)
            or set(unknown_roles) != {"UNKNOWN"}
            or not unknown_counts_valid
            or binding.get("case_bundle_count") != 0
            or binding.get("case_bundle_sha256") != "UNKNOWN"
        ):
            errors.append(f"{label}: UNKNOWN evaluation contains asserted binding")


def _validate_model_card_evaluation_binding(
    card: dict[str, Any],
    errors: list[str],
) -> None:
    binding = card.get("evaluation_binding")
    if binding is None:
        label = str(card.get("run_family", "<unknown>"))
        errors.append(f"model card {label}: missing evaluation_binding")
        return
    label = str(card.get("run_family", "<unknown>"))
    if not isinstance(binding, dict):
        errors.append(f"model card {label}: invalid evaluation_binding")
        return
    repeats = binding.get("repeats")
    decision_repeats = card.get("decision", {}).get("repeats", {})
    if not isinstance(repeats, dict) or set(repeats) != set(decision_repeats):
        errors.append(f"model card {label}: evaluation binding repeats mismatch")
        return
    statuses: list[str] = []
    for run_label, summary in repeats.items():
        _validate_evaluation_binding_summary(
            summary,
            label=f"model card {label}/{run_label}",
            errors=errors,
        )
        if isinstance(summary, dict):
            statuses.append(str(summary.get("status")))
    expected_status = (
        "UNKNOWN"
        if not statuses or set(statuses) == {"UNKNOWN"}
        else "V2_BOUND_NONCONFIRMATORY"
        if set(statuses) == {"V2_BOUND_NONCONFIRMATORY"}
        else "MIXED_UNKNOWN"
    )
    if binding.get("status") != expected_status:
        errors.append(f"model card {label}: evaluation binding status mismatch")
    expected_confirmatory: bool | str = (
        False if expected_status == "V2_BOUND_NONCONFIRMATORY" else "UNKNOWN"
    )
    if binding.get("confirmatory_eligible") != expected_confirmatory:
        errors.append(f"model card {label}: evaluation eligibility mismatch")


def validate_public_artifacts(
    public_dir: Path,
    *,
    source_results: Path | None = None,
    lab_runs_dir: Path | None = None,
    private_case_overlays_dir: Path | None = None,
) -> list[str]:
    """Return contract errors for a public export directory."""
    errors: list[str] = []
    if not public_dir.exists():
        return [f"{public_dir}: directory does not exist"]
    if not public_dir.is_dir():
        return [f"{public_dir}: not a directory"]

    for name in sorted(REQUIRED_PUBLIC_FILES):
        if not (public_dir / name).is_file():
            errors.append(f"missing required file: {name}")

    model_cards_dir = public_dir / "model-cards"
    model_card_paths = sorted(model_cards_dir.glob("*.json"))
    if not model_cards_dir.is_dir():
        errors.append("missing required directory: model-cards")
    elif not model_card_paths:
        errors.append("missing model cards: model-cards/*.json")

    benchmark = _read_json(public_dir / "benchmark-card.json", errors)
    calibration = _read_json(public_dir / "calibration-profiles.json", errors)
    split_registry = _read_json(
        public_dir / "evaluation-split-registry.json",
        errors,
    )
    lab_status = _read_json(public_dir / "lab-run-status.json", errors)
    model_cards = [
        card
        for path in model_card_paths
        if (card := _read_json(path, errors)) is not None
    ]

    for name, value in (
        ("benchmark-card.json", benchmark),
        ("calibration-profiles.json", calibration),
        ("evaluation-split-registry.json", split_registry),
        ("lab-run-status.json", lab_status),
    ):
        if value is not None:
            _walk_forbidden_keys(value, name, errors)
    for path, card in zip(model_card_paths, model_cards, strict=False):
        _walk_forbidden_keys(card, str(path.relative_to(public_dir)), errors)

    if isinstance(split_registry, dict):
        from verify_evaluation_split import verify as verify_evaluation_split

        errors.extend(
            f"evaluation-split-registry.json: {error}"
            for error in verify_evaluation_split(
                split_registry,
                root=Path(__file__).resolve().parents[1],
                public_dir=public_dir,
            )
        )

    for path in sorted(public_dir.rglob("*")):
        if not path.is_file() or path.suffix not in {".json", ".md", ".txt"}:
            continue
        rel = str(path.relative_to(public_dir))
        try:
            _scan_forbidden_text(path.read_text(encoding="utf-8"), rel, errors)
        except UnicodeDecodeError as exc:
            errors.append(f"{rel}: cannot decode public text file: {exc}")

    if isinstance(benchmark, dict):
        if benchmark.get("name") != "OPERANT":
            errors.append("benchmark-card.json: name must be OPERANT")
        case_counts = benchmark.get("case_counts")
        if not isinstance(case_counts, dict) or "decision" not in case_counts:
            errors.append("benchmark-card.json: missing case_counts.decision")
        binding = benchmark.get("evidence_binding")
        if not isinstance(binding, dict):
            errors.append("benchmark-card.json: missing evidence_binding")
        elif binding.get("schema") != "operant-public-evidence-binding.v4":
            errors.append("benchmark-card.json: unsupported evidence_binding schema")
        elif binding.get("private_paths_exposed") is not False:
            errors.append("benchmark-card.json: evidence binding exposes private paths")
        elif not isinstance(binding.get("lab_receipts"), dict):
            errors.append("benchmark-card.json: missing bound lab receipts")
        else:
            _validate_evidence_binding(
                binding,
                errors,
                source_results=source_results,
                lab_runs_dir=lab_runs_dir,
                private_case_overlays_dir=private_case_overlays_dir,
            )
        claim_status = benchmark.get("claim_status")
        if claim_status != REQUIRED_BENCHMARK_CLAIM_STATUS:
            errors.append("benchmark-card.json: unsafe or missing claim_status")
        if not isinstance(benchmark.get("claims_at_risk"), list):
            errors.append("benchmark-card.json: missing claims_at_risk")

    if isinstance(calibration, dict):
        models = calibration.get("models")
        if not isinstance(models, list) or not models:
            errors.append("calibration-profiles.json: models must be non-empty")
        if calibration.get("evidence_binding") != (
            benchmark.get("evidence_binding") if isinstance(benchmark, dict) else None
        ):
            errors.append("calibration-profiles.json: evidence binding mismatch")
        if calibration.get("presentation") != "calibration_profiles_not_flat_leaderboard":
            errors.append("calibration-profiles.json: unsafe presentation mode")
        if not isinstance(calibration.get("claims_at_risk"), list):
            errors.append("calibration-profiles.json: missing claims_at_risk")
        if calibration.get("claim_status") != REQUIRED_CALIBRATION_CLAIM_STATUS:
            errors.append("calibration-profiles.json: unsafe or missing claim_status")
        listed_families = {
            model.get("run_family")
            for model in models
            if isinstance(model, dict)
        } if isinstance(models, list) else set()
    else:
        listed_families = set()

    if isinstance(lab_status, dict):
        runs = lab_status.get("runs")
        if not isinstance(runs, list):
            errors.append("lab-run-status.json: runs must be a list")
        else:
            for run in runs:
                if not isinstance(run, dict):
                    errors.append("lab-run-status.json: each run must be an object")
                    continue
                for field in (
                    "run_label",
                    "subject_shell",
                    "status",
                    "recorded_cases",
                    "total_queued_cases",
                    "scoring_policy",
                    "evaluation_binding",
                ):
                    if field not in run:
                        label = run.get("run_label", "<unknown>")
                        errors.append(f"lab-run-status.json: {label} missing {field}")
                if (
                    int(run.get("recorded_cases", 0) or 0) == 0
                    and int(run.get("total_queued_cases", 0) or 0) == 0
                ):
                    label = run.get("run_label", "<unknown>")
                    errors.append(
                        f"lab-run-status.json: {label} has no bound recorded or queued cases"
                    )
                if "evaluation_binding" in run:
                    _validate_evaluation_binding_summary(
                        run["evaluation_binding"],
                        label=(
                            "lab-run-status.json: "
                            f"{run.get('run_label', '<unknown>')}"
                        ),
                        errors=errors,
                    )
            app_runs = [r for r in runs if r.get("subject_shell") == "codex-app"]
            cli_runs = [r for r in runs if r.get("subject_shell") == "codex-cli"]
            if app_runs and cli_runs:
                app_families = {r.get("run_family") for r in app_runs}
                cli_families = {r.get("run_family") for r in cli_runs}
                overlap = app_families & cli_families
                if overlap:
                    errors.append(
                        "lab-run-status.json: codex-app and codex-cli "
                        f"families overlap: {sorted(overlap)}"
                    )
                if not any("separate" in str(r.get("scoring_policy", "")) for r in cli_runs):
                    errors.append(
                        "lab-run-status.json: codex-cli runs must state separate scoring"
                    )

    for card in model_cards:
        if not isinstance(card, dict):
            errors.append("model-cards/*.json: each card must be an object")
            continue
        for field in ("run_family", "display_name", "subject_shell", "decision"):
            if field not in card:
                label = card.get("run_family", "<unknown>")
                errors.append(f"model card {label}: missing {field}")
        is_orphaned = card.get("run_family") not in listed_families
        expected_status = (
            REQUIRED_ORPHANED_CLAIM_STATUS
            if is_orphaned
            else (
                REQUIRED_LOCAL_CLAIM_STATUS
                if card.get("data_source") == "local_lab_runs"
                else REQUIRED_HISTORICAL_CLAIM_STATUS
            )
        )
        if card.get("claim_status") != expected_status:
            label = card.get("run_family", "<unknown>")
            errors.append(f"model card {label}: unsafe or missing claim_status")
        if not is_orphaned:
            _validate_card_aggregates(card, errors)
            _validate_model_card_evaluation_binding(card, errors)
        elif "evaluation_binding" in card:
            _validate_model_card_evaluation_binding(card, errors)
        if is_orphaned and not str(card.get("orphan_reason", "")).strip():
            label = card.get("run_family", "<unknown>")
            errors.append(f"model card {label}: missing orphan_reason")
        expected_presentation = (
            "orphaned_historical_artifact_not_active_profile"
            if is_orphaned
            else (
                "self_reported_local_receipt"
                if card.get("data_source") == "local_lab_runs"
                else "historical_calculation_profile_not_model_leaderboard"
            )
        )
        if card.get("presentation") != expected_presentation:
            label = card.get("run_family", "<unknown>")
            errors.append(f"model card {label}: unsafe presentation mode")
        if not is_orphaned and card.get("evidence_binding") != (
            benchmark.get("evidence_binding") if isinstance(benchmark, dict) else None
        ):
            label = card.get("run_family", "<unknown>")
            errors.append(f"model card {label}: evidence binding mismatch")
        if (
            not is_orphaned
            and card.get("data_source") == "local_lab_runs"
            and isinstance(lab_status, dict)
            and card.get("run_family")
            not in {
                run.get("run_family")
                for run in lab_status.get("runs", [])
                if isinstance(run, dict)
            }
        ):
            label = card.get("run_family", "<unknown>")
            errors.append(f"model card {label}: missing lab run status")

    if isinstance(calibration, dict):
        active_cards = {
            card.get("run_family"): card
            for card in model_cards
            if isinstance(card, dict) and card.get("run_family") in listed_families
        }
        local_families = {
            str(family)
            for family, card in active_cards.items()
            if card.get("data_source") == "local_lab_runs"
        }
        benchmark_binding = (
            benchmark.get("evidence_binding")
            if isinstance(benchmark, dict)
            else {}
        )
        if not isinstance(benchmark_binding, dict):
            benchmark_binding = {}
        lab_receipts = benchmark_binding.get("lab_receipts", {})
        receipt_families = (
            {
                re.sub(r"-r\d+$", "", str(key).split("/", 1)[0])
                for key in lab_receipts
            }
            if isinstance(lab_receipts, dict)
            else set()
        )
        if local_families - receipt_families:
            errors.append(
                "benchmark-card.json: active local profiles lack bound lab receipts"
            )
        expected_receipt_keys: set[str] = set()
        for family in local_families:
            repeats = active_cards[family].get("decision", {}).get("repeats", {})
            if not isinstance(repeats, dict):
                continue
            for run_label, summary in repeats.items():
                if not isinstance(summary, dict):
                    continue
                case_ids = summary.get("case_ids", [])
                if isinstance(case_ids, list):
                    expected_receipt_keys.update(
                        f"{run_label}/{case_id}.json"
                        for case_id in case_ids
                        if isinstance(case_id, str)
                    )
        actual_receipt_keys = (
            set(str(key) for key in lab_receipts)
            if isinstance(lab_receipts, dict)
            else set()
        )
        if actual_receipt_keys != expected_receipt_keys:
            errors.append(
                "benchmark-card.json: lab receipt coverage does not match scored repeats"
            )
        expected_models = []
        for family in sorted(active_cards):
            card = active_cards[family]
            expected_models.append(
                {
                    "run_family": card.get("run_family"),
                    "display_name": card.get("display_name"),
                    "subject_shell": card.get("subject_shell"),
                    "ocs_mean": card.get("decision", {}).get("ocs_mean"),
                    "ocs_stdev": card.get("decision", {}).get("ocs_stdev"),
                    "orchestration_mean": card.get("orchestration_judge", {}).get(
                        "mean_score"
                    ),
                }
            )
        actual_models = calibration.get("models")
        if (
            not isinstance(actual_models, list)
            or any(not isinstance(row, dict) for row in actual_models)
            or sorted(
                actual_models,
                key=lambda row: str(row.get("run_family", "")),
            ) != expected_models
        ):
            errors.append(
                "calibration-profiles.json: model rows do not match active model cards"
            )

    for name in ("README.md", "methodology.md"):
        path = public_dir / name
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if "UNKNOWN" not in text or "not durable" not in text.lower():
            errors.append(f"{name}: missing fail-closed historical evidence boundary")

    return errors
