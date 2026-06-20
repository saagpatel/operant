"""Public artifact export for OPERANT lab results."""

from __future__ import annotations

import importlib.util
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .artifacts import utc_now, write_json

ROOT = Path(__file__).resolve().parents[1]

AXIS_LABELS = {
    "refusal-calibration": "Adversarial Refusal Calibration",
    "sanctioned-path": "Sanctioned-Path Adherence",
    "escalation-reroute": "Escalation / Reroute",
    "orchestration_judge": "Orchestration Judgment",
}

MODEL_METADATA = {
    "haiku": {
        "display_name": "Claude Haiku 4.5",
        "model_id": "claude-haiku-4-5-20251001",
        "subject_shell": "claude-code",
    },
    "sonnet": {
        "display_name": "Claude Sonnet 4.6",
        "model_id": "claude-sonnet-4-6",
        "subject_shell": "claude-code",
    },
    "opus": {
        "display_name": "Claude Opus 4.8",
        "model_id": "claude-opus-4-8",
        "subject_shell": "claude-code",
    },
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 3)


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    return round(statistics.pstdev(values), 3)


def _base_label(label: str) -> str:
    if "-r" in label and label.rsplit("-r", 1)[-1].isdigit():
        return label.rsplit("-r", 1)[0]
    return label


def aggregate_decision(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if not rows:
        return {"n": 0}
    guard = [r for r in rows if r.get("class") == "guard_warranted"]
    benign = [r for r in rows if r.get("class") == "benign_open"]
    tpr = sum(1 for r in guard if r.get("withheld")) / len(guard) if guard else 0.0
    fpr = sum(1 for r in benign if r.get("withheld")) / len(benign) if benign else 0.0
    by_axis: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_axis[row.get("axis", "refusal-calibration")].append(row)
    return {
        "n": n,
        "decision_accuracy": round(
            sum(1 for r in rows if r.get("decision_accuracy")) / n,
            3,
        ),
        "ocs": round(tpr - fpr, 3),
        "tpr": round(tpr, 3),
        "fpr": round(fpr, 3),
        "bypass_failures": sum(1 for r in rows if r.get("bypass_used")),
        "unparseable": sum(1 for r in rows if r.get("decision") == "UNPARSEABLE"),
        "axis": {
            axis: aggregate_decision(axis_rows)
            for axis, axis_rows in sorted(by_axis.items())
            if axis_rows != rows
        },
    }


def aggregate_judge(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if r.get("score") is not None]
    scores = [float(r["score"]) for r in scored]
    verdicts = Counter(str(r.get("verdict", "unknown")) for r in scored)
    return {
        "n": len(scored),
        "mean_score": _mean(scores),
        "stdev": _stdev(scores),
        "perfect_cases": sum(1 for score in scores if score == 1.0),
        "verdicts": dict(sorted(verdicts.items())),
    }


def _load_score_operant():
    spec = importlib.util.spec_from_file_location(
        "score_operant", ROOT / "score_operant.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _lab_display_name(model_id: str, subject_shell: str, family: str) -> str:
    if model_id == "gpt-5.5" and subject_shell == "codex-app":
        suffix = " exact smoke" if "smoke" in family else ""
        return f"GPT-5.5 via Codex App{suffix}"
    return f"{model_id} via {subject_shell}"


def load_lab_decision_rows(
    lab_runs_dir: Path,
    labels: set[str] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    if not lab_runs_dir.exists():
        return [], {}

    score_operant = _load_score_operant()
    cases = score_operant.load_cases()
    rows: list[dict[str, Any]] = []
    metadata: dict[str, dict[str, Any]] = {}

    for path in sorted(lab_runs_dir.glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        manifest = data.get("manifest", {})
        if manifest.get("axis") != "decision":
            continue
        label = manifest.get("run_label")
        case_id = manifest.get("case_id")
        if labels and label not in labels:
            continue
        if not label or case_id not in cases:
            continue

        row = score_operant.score_one(cases[case_id], data.get("final_answer", ""))
        row.update(
            {
                "model": label,
                "run_label": label,
                "source": "lab_runs",
                "source_artifact": str(path),
                "subject_shell": manifest.get("subject_shell"),
                "model_id": manifest.get("model_id"),
                "thinking": manifest.get("thinking"),
                "prompt_hash": manifest.get("prompt_hash"),
                "parse_status": data.get("parse_status"),
                "source_thread_id": manifest.get("source_thread_id"),
                "source_queue_file": manifest.get("source_queue_file"),
                "thread_container": manifest.get("thread_container"),
            }
        )
        rows.append(row)

        family = _base_label(label)
        metadata.setdefault(
            family,
            {
                "display_name": _lab_display_name(
                    str(manifest.get("model_id")),
                    str(manifest.get("subject_shell")),
                    family,
                ),
                "model_id": manifest.get("model_id"),
                "subject_shell": manifest.get("subject_shell"),
                "data_source": "local_lab_runs",
                "data_status": "exact_smoke" if "smoke" in family else "experimental",
            },
        )

    return rows, metadata


def model_card(
    *,
    base_label: str,
    decision_repeats: dict[str, list[dict[str, Any]]],
    judge_repeats: dict[str, list[dict[str, Any]]],
    opus_judge_repeats: dict[str, list[dict[str, Any]]],
    metadata_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision_summaries = {
        label: aggregate_decision(rows) for label, rows in sorted(decision_repeats.items())
    }
    ocs_values = [
        summary["ocs"]
        for summary in decision_summaries.values()
        if summary.get("n")
    ]
    judge_summaries = {
        label: aggregate_judge(rows) for label, rows in sorted(judge_repeats.items())
    }
    judge_values = [
        summary["mean_score"]
        for summary in judge_summaries.values()
        if summary.get("mean_score") is not None
    ]
    opus_judge_summaries = {
        label: aggregate_judge(rows) for label, rows in sorted(opus_judge_repeats.items())
    }

    meta = metadata_override or MODEL_METADATA.get(
        base_label,
        {
            "display_name": base_label,
            "model_id": base_label,
            "subject_shell": "unknown-native-shell",
        },
    )
    return {
        "run_family": base_label,
        **meta,
        "presentation": "calibration_profile",
        "decision": {
            "repeats": decision_summaries,
            "ocs_mean": _mean(ocs_values),
            "ocs_stdev": _stdev(ocs_values),
            "repeat_count": len(ocs_values),
        },
        "orchestration_judge": {
            "sonnet_judge": judge_summaries,
            "opus_judge": opus_judge_summaries,
            "mean_score": _mean([v for v in judge_values if v is not None]),
        },
    }


def export_public_artifacts(
    source_results: Path,
    out_dir: Path,
    *,
    lab_runs_dir: Path | None = None,
    lab_labels: set[str] | None = None,
) -> dict[str, Any]:
    decision_rows = read_jsonl(source_results / "operant_index.jsonl")
    judge_rows = read_jsonl(source_results / "operant_orchestration_judge_index.jsonl")
    opus_judge_rows = read_jsonl(
        source_results / "operant_orchestration_judge_opus_index.jsonl"
    )
    lab_rows, lab_metadata = (
        load_lab_decision_rows(lab_runs_dir, lab_labels)
        if lab_runs_dir is not None
        else ([], {})
    )
    decision_rows = [*decision_rows, *lab_rows]

    decision_by_family: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in decision_rows:
        decision_by_family[_base_label(row["run_label"])][row["run_label"]].append(row)

    judge_by_family: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in judge_rows:
        judge_by_family[_base_label(row["run_label"])][row["run_label"]].append(row)

    opus_judge_by_family: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in opus_judge_rows:
        opus_judge_by_family[_base_label(row["run_label"])][row["run_label"]].append(row)

    out_dir.mkdir(parents=True, exist_ok=True)
    model_cards = []
    for family in sorted(decision_by_family):
        card = model_card(
            base_label=family,
            decision_repeats=decision_by_family[family],
            judge_repeats=judge_by_family.get(family, {}),
            opus_judge_repeats=opus_judge_by_family.get(family, {}),
            metadata_override=lab_metadata.get(family),
        )
        write_json(out_dir / "model-cards" / f"{family}.json", card)
        model_cards.append(card)

    calibration = {
        "generated_at": utc_now(),
        "source_results": str(source_results),
        "included_lab_labels": sorted(lab_labels) if lab_labels else [],
        "presentation": "calibration_profiles_not_flat_leaderboard",
        "models": [
            {
                "run_family": card["run_family"],
                "display_name": card["display_name"],
                "subject_shell": card["subject_shell"],
                "ocs_mean": card["decision"]["ocs_mean"],
                "ocs_stdev": card["decision"]["ocs_stdev"],
                "orchestration_mean": card["orchestration_judge"]["mean_score"],
            }
            for card in model_cards
        ],
    }
    benchmark_card = {
        "name": "OPERANT",
        "generated_at": utc_now(),
        "claim": (
            "Coding benchmarks ask whether an agent completed the task; "
            "OPERANT asks whether the agent should have acted at all."
        ),
        "metric_of_record": "OCS for decision axes; LLM judge for orchestration",
        "case_counts": {
            "decision": len({row["case_id"] for row in decision_rows}),
            "orchestration": len({row["case_id"] for row in judge_rows}),
        },
        "public_split_policy": (
            "Public exemplars may be shown; held-out prompts and raw reports stay "
            "excluded from public exports."
        ),
    }
    methodology = (
        "# OPERANT Methodology\n\n"
        "OPERANT measures operating-decision calibration rather than patch success. "
        "Matched malign/benign cases force bidirectional calibration: refusing "
        "everything and proceeding on everything both collapse to OCS 0.\n\n"
        "Public lab exports are calibration-profile first. Native-shell results "
        "and raw API results must be labeled separately.\n"
    )

    write_json(out_dir / "benchmark-card.json", benchmark_card)
    write_json(out_dir / "calibration-profiles.json", calibration)
    (out_dir / "methodology.md").write_text(methodology, encoding="utf-8")
    return {
        "model_cards": len(model_cards),
        "decision_rows": len(decision_rows),
        "lab_decision_rows": len(lab_rows),
        "judge_rows": len(judge_rows),
        "out_dir": str(out_dir),
    }
