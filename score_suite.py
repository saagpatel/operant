#!/usr/bin/env python3
"""OPERANT score_suite — results aggregator and comparison-table tool.

Discovers run labels from results/reports/operant__<label>__<case_id>.txt,
scores each report through score_operant.py, and prints a fixed-width
comparison table plus a GitHub-flavored markdown version.

Usage:
  python3 score_suite.py [--labels L1 L2 ...] [--record] [--reports-dir PATH]
"""

import argparse
import importlib.util
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from operant_lab.artifacts import (
    filter_unblocked_index_rows,
    receipt_output_scoring_block_reason,
)

HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Import score_operant from sibling file (mirrors selftest.py exactly)
# ---------------------------------------------------------------------------


def _load_score_operant():
    spec = importlib.util.spec_from_file_location(
        "score_operant", HERE / "score_operant.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_so = _load_score_operant()
load_cases = _so.load_cases
score_one = _so.score_one
aggregate = _so.aggregate

INDEX = os.path.join(HERE, "results", "operant_index.jsonl")
# Axis-3 metric OF RECORD: the LLM-judge index. The keyword score_orchestration
# scorer saturates and cannot rank (RESULTS.md sec 3) — the judge does. This table
# surfaces the judge orchestration mean per label, read-only (no model calls), as a
# first-class column so the default suite report matches the validated conclusion.
JUDGE_INDEX = os.path.join(HERE, "results", "operant_orchestration_judge_index.jsonl")


def orch_judge_means(index_path: str = JUDGE_INDEX) -> dict[str, tuple[float, int]]:
    """{run_label: (mean_judge_score, n)} from the orchestration judge index.
    Empty if the index is absent — the column then renders '-' everywhere."""
    by_label: dict[str, list[float]] = defaultdict(list)
    rows = []
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
    for row in filter_unblocked_index_rows(HERE, rows):
        if row.get("score") is not None:
            by_label[row["run_label"]].append(row["score"])
    return {lbl: (sum(v) / len(v), len(v)) for lbl, v in by_label.items() if v}


# ---------------------------------------------------------------------------
# Report file discovery
# ---------------------------------------------------------------------------

REPORT_RE = re.compile(r"^operant__(.+)__([^_][^_]*.+)\.txt$")
#  filename: operant__<label>__<case_id>.txt
#  label = segment between first __ and last __
#  case_id = everything after the last __ (may contain dots, no __)


def discover_labels(reports_dir: Path) -> dict[str, dict[str, Path]]:
    """Return {label: {case_id: path}} for all report files found."""
    result: dict[str, dict[str, Path]] = defaultdict(dict)
    if not reports_dir.exists():
        return result
    for p in sorted(reports_dir.iterdir()):
        m = REPORT_RE.match(p.name)
        if m:
            label, case_id = m.group(1), m.group(2)
            result[label][case_id] = p
    return result


# ---------------------------------------------------------------------------
# Per-label scoring
# ---------------------------------------------------------------------------


def score_label(
    label: str,
    label_files: dict[str, Path],
    cases: dict,
    record: bool,
) -> tuple[list[dict], int]:
    """Score all report files for a label. Returns (rows, missing_count)."""
    rows: list[dict] = []
    missing = 0

    for case_id, case in cases.items():
        if case_id not in label_files:
            missing += 1
            continue
        report_text = label_files[case_id].read_text(encoding="utf-8")
        if receipt_output_scoring_block_reason(
            HERE,
            run_label=label,
            case_id=case_id,
            final_answer=report_text,
            require_receipt=record,
        ):
            missing += 1
            continue
        row = score_one(case, report_text)
        row["model"] = label
        row["run_label"] = label
        rows.append(row)

    if record and rows:
        os.makedirs(os.path.dirname(INDEX), exist_ok=True)
        with open(INDEX, "a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    return rows, missing


# ---------------------------------------------------------------------------
# Table building
# ---------------------------------------------------------------------------

AXES = ["refusal-calibration", "sanctioned-path", "escalation-reroute"]


def _fmt_ocs(agg: dict | None) -> str:
    if agg is None or agg["n"] == 0:
        return "-"
    return f"{agg['ocs']:+.3f}"


def _fmt_pct(val: float | None) -> str:
    if val is None:
        return "-"
    return f"{val:.0%}"


def build_row(
    label: str,
    rows: list[dict],
    missing: int,
) -> dict:
    """Build a summary dict for one label."""
    if not rows:
        return {
            "label": label,
            "n": 0,
            "accuracy": "-",
            "ocs_overall": "-",
            "ocs_refusal": "-",
            "ocs_sanctioned": "-",
            "ocs_escalation": "-",
            "safe_correct": "-",
            "bypass_fails": "-",
            "unparseable": "-",
            "missing": missing,
        }

    agg = aggregate(rows)

    by_axis: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_axis[r.get("axis", "refusal-calibration")].append(r)

    agg_by_axis = {ax: aggregate(ax_rows) for ax, ax_rows in by_axis.items()}

    return {
        "label": label,
        "n": agg["n"],
        "accuracy": _fmt_pct(agg["decision_accuracy"]),
        "ocs_overall": _fmt_ocs(agg),
        "ocs_refusal": _fmt_ocs(agg_by_axis.get("refusal-calibration")),
        "ocs_sanctioned": _fmt_ocs(agg_by_axis.get("sanctioned-path")),
        "ocs_escalation": _fmt_ocs(agg_by_axis.get("escalation-reroute")),
        "safe_correct": _fmt_pct(agg["safe_and_correct_rate"]),
        "bypass_fails": str(len(agg["bypass_failures"])),
        "unparseable": str(agg["unparseable"]),
        "missing": missing,
        # Keep raw ocs for sorting
        "_ocs_raw": agg["ocs"],
    }


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------

COLUMNS = [
    ("label", "Label"),
    ("n", "n"),
    ("accuracy", "Accuracy"),
    ("ocs_overall", "OCS(overall)"),
    ("ocs_refusal", "OCS:refusal-cal"),
    ("ocs_sanctioned", "OCS:sanctioned"),
    ("ocs_escalation", "OCS:escalation"),
    ("safe_correct", "Safe&Correct"),
    ("bypass_fails", "BypassFails"),
    ("unparseable", "Unparseable"),
    ("orch_judge", "OrchJudge†"),
    ("missing", "Missing"),
]


def render_fixed(summary_rows: list[dict]) -> str:
    """Fixed-width table."""
    col_widths = {key: len(header) for key, header in COLUMNS}
    for row in summary_rows:
        for key, _ in COLUMNS:
            col_widths[key] = max(col_widths[key], len(str(row.get(key, ""))))

    sep = "  ".join("-" * col_widths[k] for k, _ in COLUMNS)
    header = "  ".join(header.ljust(col_widths[k]) for k, header in COLUMNS)

    lines = [header, sep]
    for row in summary_rows:
        line = "  ".join(str(row.get(k, "")).ljust(col_widths[k]) for k, _ in COLUMNS)
        lines.append(line)

    return "\n".join(lines)


def render_markdown(summary_rows: list[dict]) -> str:
    """GitHub-flavored markdown table."""
    col_widths = {key: len(header) for key, header in COLUMNS}
    for row in summary_rows:
        for key, _ in COLUMNS:
            col_widths[key] = max(col_widths[key], len(str(row.get(key, ""))))

    def row_str(vals: list[str]) -> str:
        cells = " | ".join(v.ljust(col_widths[k]) for (k, _), v in zip(COLUMNS, vals))
        return f"| {cells} |"

    header_vals = [header for _, header in COLUMNS]
    sep_vals = ["-" * col_widths[k] for k, _ in COLUMNS]

    lines = [
        row_str(header_vals),
        row_str(sep_vals),
    ]
    for row in summary_rows:
        lines.append(row_str([str(row.get(k, "")) for k, _ in COLUMNS]))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Aggregate and compare OPERANT benchmark runs."
    )
    ap.add_argument(
        "--labels",
        nargs="+",
        metavar="LABEL",
        help="restrict to these run labels (default: all discovered)",
    )
    ap.add_argument(
        "--record",
        action="store_true",
        help=f"append scored rows to {INDEX}",
    )
    ap.add_argument(
        "--reports-dir",
        default=str(HERE / "results" / "reports"),
        metavar="PATH",
        help="directory containing operant__<label>__<case_id>.txt files",
    )
    args = ap.parse_args()

    reports_dir = Path(args.reports_dir)
    all_labels = discover_labels(reports_dir)

    if not all_labels:
        print(f"No report files found in {reports_dir}", file=sys.stderr)
        sys.exit(1)

    selected_labels = args.labels if args.labels else sorted(all_labels)
    unknown = set(selected_labels) - set(all_labels)
    if unknown:
        print(
            f"Warning: labels not found in {reports_dir}: {sorted(unknown)}",
            file=sys.stderr,
        )
        selected_labels = [lbl for lbl in selected_labels if lbl not in unknown]

    if not selected_labels:
        print("No labels to process.", file=sys.stderr)
        sys.exit(1)

    cases = load_cases()
    jmeans = orch_judge_means()

    summary_rows: list[dict] = []
    for label in selected_labels:
        label_files = all_labels[label]
        rows, missing = score_label(label, label_files, cases, record=args.record)
        srow = build_row(label, rows, missing)
        jm = jmeans.get(label)
        srow["orch_judge"] = f"{jm[0]:.3f}" if jm else "-"
        summary_rows.append(srow)

    # Sort by overall OCS descending (missing/zero-case labels go last)
    summary_rows.sort(key=lambda r: r.get("_ocs_raw", float("-inf")), reverse=True)

    fixed = render_fixed(summary_rows)
    md = render_markdown(summary_rows)

    print(fixed)
    legend = (
        "\n† OrchJudge = axis-3 orchestration mean, LLM-judge scored "
        "(score_orchestration_judge.py) — the metric OF RECORD. The keyword "
        "score_orchestration scorer saturates/cannot rank (RESULTS.md §3). "
        "'-' = no judge rows for that label yet."
    )
    print(legend)
    print()
    print("=== MARKDOWN ===")
    print(md)
    print(legend)


if __name__ == "__main__":
    main()
