"""Public artifact contract checks for OPERANT exports."""

from __future__ import annotations

import hashlib
import json
import re
import statistics
from pathlib import Path
from typing import Any

REQUIRED_PUBLIC_FILES = {
    "README.md",
    "benchmark-card.json",
    "calibration-profiles.json",
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
    "dispatch_freshness": "UNKNOWN",
    "served_model_identity": "UNKNOWN",
    "independent_replication": "UNKNOWN",
    "cross_model_ranking": "NOT_DURABLE",
    "inferential_statistics_as_model_evidence": "NOT_DURABLE",
}

REQUIRED_LOCAL_CLAIM_STATUS = {
    "evidence_class": "self_reported_local_receipt",
    "score_recalculation_from_bound_bytes": "SUPPORTED",
    "source_receipt_byte_binding": "SUPPORTED",
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
    "corpus": {
        "operant_cases.json",
        "operant_axis2_cases.json",
        "operant_axis3_cases.json",
        "operant_axis4_cases.json",
        "operant_templates.json",
    },
    "protocol": {
        "score_operant.py",
        "score_orchestration.py",
        "score_orchestration_judge.py",
    },
}

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _read_json(path: Path, errors: list[str]) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"{path}: invalid JSON: {exc}")
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


def _validate_evidence_binding(binding: dict[str, Any], errors: list[str]) -> None:
    maps: dict[str, dict[str, str]] = {}
    for label in ("source_indexes", "corpus", "protocol"):
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

    for label in ("source_bundle_sha256", "exporter_sha256"):
        value = binding.get(label)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            errors.append(f"benchmark-card.json: evidence binding {label} is unusable")
    historical = binding.get("historical_evidence_manifest_sha256")
    if historical != "UNKNOWN" and (
        not isinstance(historical, str) or not SHA256_RE.fullmatch(historical)
    ):
        errors.append(
            "benchmark-card.json: evidence binding historical manifest digest is unusable"
        )

    if set(maps) == {"source_indexes", "lab_receipts", "corpus", "protocol"}:
        combined = json.dumps(
            {
                "source_indexes": maps["source_indexes"],
                "lab_receipts": maps["lab_receipts"],
                "corpus": maps["corpus"],
                "protocol": maps["protocol"],
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


def validate_public_artifacts(public_dir: Path) -> list[str]:
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
    lab_status = _read_json(public_dir / "lab-run-status.json", errors)
    model_cards = [
        card
        for path in model_card_paths
        if (card := _read_json(path, errors)) is not None
    ]

    for name, value in (
        ("benchmark-card.json", benchmark),
        ("calibration-profiles.json", calibration),
        ("lab-run-status.json", lab_status),
    ):
        if value is not None:
            _walk_forbidden_keys(value, name, errors)
    for path, card in zip(model_card_paths, model_cards, strict=False):
        _walk_forbidden_keys(card, str(path.relative_to(public_dir)), errors)

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
        elif binding.get("schema") != "operant-public-evidence-binding.v2":
            errors.append("benchmark-card.json: unsupported evidence_binding schema")
        elif binding.get("private_paths_exposed") is not False:
            errors.append("benchmark-card.json: evidence binding exposes private paths")
        elif not isinstance(binding.get("lab_receipts"), dict):
            errors.append("benchmark-card.json: missing bound lab receipts")
        else:
            _validate_evidence_binding(binding, errors)
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
