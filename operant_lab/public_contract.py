"""Public artifact contract checks for OPERANT exports."""

from __future__ import annotations

import json
import re
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

    if isinstance(calibration, dict):
        models = calibration.get("models")
        if not isinstance(models, list) or not models:
            errors.append("calibration-profiles.json: models must be non-empty")

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

    return errors
