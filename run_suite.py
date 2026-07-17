#!/usr/bin/env python3
"""OPERANT full-suite driver — one button for a complete model run.

Dispatches AND scores both axis families for a model in one command, so a full
evaluation run is a single invocation per model instead of a manual run-then-
score-each-case loop:

  - Decision axes (1 refusal-calibration, 2 sanctioned-path, 4 escalation-reroute)
    via run_operant.run_case -> score_operant -> records to operant_index.jsonl
  - Orchestration axis (3) via run_orchestration.run_case -> score_orchestration
    -> records to operant_orchestration_index.jsonl

It adds the operational discipline the threat-model notes call for:
  - --repeats N: each repeat gets its own label suffix (`<label>-r1`, ...) so
    variance/flip analysis (score_variance.py) has independent draws.
  - RATE-LIMIT GUARD: every empty / "session limit" / unparseable report is
    counted and printed LOUDLY — a silently rate-limited cell never masquerades
    as a real score. Re-run those labels after the limit resets.
  - --dry-run: prints the plan and the exact per-case commands, no model calls.

Usage:
  # Verify wiring, spend nothing:
  python3 run_suite.py --model claude-sonnet-4-6 --label sonnet --dry-run

  # Real single run of every axis for one model:
  python3 run_suite.py --model claude-sonnet-4-6 --label sonnet

  # Variance run (5 independent repeats), the headline-number protocol:
  python3 run_suite.py --model claude-opus-4-8 --label opus --repeats 5

After running every model, compare:
  python3 score_suite.py                         # decision axes table (OCS per axis)
  python3 score_orchestration.py --aggregate <label>   # orchestration mean
"""

import argparse
import concurrent.futures as cf
import importlib.util
import json
from pathlib import Path

from operant_lab.artifacts import (
    VALID_EVALUATION_ROLES,
    case_bundle_binding,
    filter_unblocked_index_rows,
    receipt_output_scoring_block_reason,
    resolve_evaluation_role,
    scoring_block_reason,
)

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "results" / "reports"
DECISION_INDEX = HERE / "results" / "operant_index.jsonl"
ORCH_INDEX = HERE / "results" / "operant_orchestration_index.jsonl"

# Markers that mean a dispatch did not return a real answer (rate-limit / empty).
RATE_LIMIT_MARKERS = ("session limit", "usage limit", "rate limit")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def read_judge_rows(index_path: Path, label: str) -> list:
    """Judge-index rows belonging to one run label. Pure (no dispatch) so the
    label->summary path that produces the axis-3 metric-of-record line is selftest-
    covered without paying for a single judge call. Missing index -> [] (never raises)."""
    if not index_path.exists():
        return []
    rows = []
    for ln in index_path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            r = json.loads(ln)
            if r.get("run_label") == label:
                rows.append(r)
    return filter_unblocked_index_rows(HERE, rows)


def judge_label(sjudge, label: str, judge_model: str, concurrency: int) -> dict:
    """Run the axis-3 LLM-judge over THIS run's orchestration transcripts for `label`
    and return the judge aggregate — the axis-3 metric of record. Incremental and
    idempotent: rescore_reports skips transcripts already in the judge index and the
    dispatcher retries transient 529s, so we then aggregate the FULL label off the
    index (not just the newly-judged subset) to stay correct across re-runs."""
    sjudge.rescore_reports([label], judge_model, concurrency)
    rows = read_judge_rows(Path(sjudge.INDEX), label)
    summary = sjudge.aggregate(rows)
    summary["run_label"] = label
    return summary


def run_axis(
    *,
    runner,
    scorer,
    cases: dict,
    prefix: str,
    model: str,
    label: str,
    system_prompt: str,
    index_path: Path,
    concurrency: int,
    dry_run: bool,
    evaluation_role: str,
    case_split: str,
) -> dict:
    """Dispatch every case for one axis family, score each report, record rows.
    Returns the scorer's aggregate plus a rate-limit tally."""
    bundle = case_bundle_binding(list(cases.values()), case_split=case_split)
    if dry_run:
        for case in cases.values():
            runner.run_case(
                case,
                model,
                label,
                system_prompt,
                dry_run=True,
                evaluation_role=evaluation_role,
                case_bundle=bundle,
            )
        return {"dry_run": True, "n": len(cases)}

    REPORTS.mkdir(parents=True, exist_ok=True)
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {
            pool.submit(
                runner.run_case,
                c,
                model,
                label,
                system_prompt,
                False,
                evaluation_role=evaluation_role,
                case_bundle=bundle,
            ): c
            for c in cases.values()
        }
        metas = []
        for fut in cf.as_completed(futures):
            meta = fut.result()
            metas.append(meta)
            status = meta.get("model_usage") or meta.get("error", "no-result")
            print(
                f"  [{meta['case_id']}] {meta.get('duration_s', '?')}s -> {status}",
                flush=True,
            )

    meta_by_case = {str(meta["case_id"]): meta for meta in metas}
    rows, rate_limited, missing = [], [], []
    for cid, case in cases.items():
        meta = meta_by_case.get(cid, {})
        report_value = meta.get("report")
        if not report_value:
            missing.append(cid)
            continue
        execution_binding = meta.get("execution_binding")
        if not isinstance(execution_binding, dict):
            missing.append(cid)
            continue
        block_reason = scoring_block_reason(
            {
                "manifest_schema": "operant-run-manifest.v3",
                "model_id": model,
                "execution_binding": execution_binding,
            }
        )
        if block_reason:
            missing.append(cid)
            continue
        report_path = HERE.parent / str(report_value)
        expected_path = REPORTS / f"{prefix}__{label}__{cid}.txt"
        if report_path.resolve() != expected_path.resolve() or not report_path.exists():
            missing.append(cid)
            continue
        text = report_path.read_text(encoding="utf-8")
        if receipt_output_scoring_block_reason(
            HERE,
            run_label=label,
            case_id=cid,
            final_answer=text,
            require_receipt=True,
        ):
            missing.append(cid)
            continue
        low = text.lower()
        if not text.strip() or any(m in low for m in RATE_LIMIT_MARKERS):
            rate_limited.append(cid)
            continue
        row = scorer.score_one(case, text)
        row["model"] = label
        row["run_label"] = label
        rows.append(row)

    if rows:
        index_path.parent.mkdir(parents=True, exist_ok=True)
        with index_path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    agg = scorer.aggregate(rows)
    agg["rate_limited"] = rate_limited
    agg["missing"] = missing
    return agg


def main() -> None:
    ap = argparse.ArgumentParser(
        description="OPERANT full-suite driver (all axes, one model)"
    )
    ap.add_argument("--model", default="claude-sonnet-4-6", help="model id to dispatch")
    ap.add_argument("--label", default=None, help="run label (default: model id)")
    ap.add_argument(
        "--repeats",
        type=int,
        default=1,
        help="independent repeats (each gets -rN suffix)",
    )
    ap.add_argument(
        "--concurrency", type=int, default=3, help="max parallel dispatches"
    )
    ap.add_argument(
        "--judge",
        action="store_true",
        help="after orchestration dispatch, run the axis-3 LLM-judge on THIS run's "
        "transcripts and print the judge mean as the metric of record (off by "
        "default — gates all judge token-spend behind this flag)",
    )
    ap.add_argument(
        "--judge-model",
        default=None,
        help="judge model for --judge (default: the Sonnet judge of record)",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="print the plan, no model calls"
    )
    ap.add_argument(
        "--evaluation-role",
        choices=sorted(VALID_EVALUATION_ROLES),
        help="Non-confirmatory role recorded in every run receipt.",
    )
    ap.add_argument(
        "--case-split",
        default="canonical",
        help="Stable split label included in each axis case-bundle digest.",
    )
    args = ap.parse_args()

    base_label = args.label or args.model

    so = _load("score_operant")
    sorch = _load("score_orchestration")
    run_op = _load("run_operant")
    run_orch = _load("run_orchestration")
    # The judge scorer is loaded (and its tokens spent) only when --judge is passed.
    sjudge = _load("score_orchestration_judge") if args.judge else None
    judge_model = (
        (args.judge_model or sjudge.DEFAULT_JUDGE_MODEL) if args.judge else None
    )

    decision_cases = so.load_cases()
    orch_cases = sorch.load_cases()
    decision_prompt = run_op.build_system_prompt(run_op.load_operator_contract())
    orch_prompt = run_orch.build_system_prompt(run_orch.load_operator_contract())

    print(
        f"OPERANT SUITE — model={args.model}  base_label={base_label}  "
        f"repeats={args.repeats}  decision_cases={len(decision_cases)}  "
        f"orchestration_cases={len(orch_cases)}  concurrency={args.concurrency}"
    )
    if args.judge:
        print(
            f"  axis-3 LLM-judge ENABLED (metric of record) — judge={judge_model}, "
            f"index={Path(sjudge.INDEX).name}"
        )
    if args.dry_run:
        print("\n*** DRY RUN — no model calls ***\n")

    any_rate_limited = False
    for r in range(1, args.repeats + 1):
        label = base_label if args.repeats == 1 else f"{base_label}-r{r}"
        evaluation_role = resolve_evaluation_role(
            args.evaluation_role,
            run_label=label,
        )
        print(f"\n=== repeat {r}/{args.repeats}  label={label} ===")

        print("\n[decision axes 1/2/4]")
        d = run_axis(
            runner=run_op,
            scorer=so,
            cases=decision_cases,
            prefix="operant",
            model=args.model,
            label=label,
            system_prompt=decision_prompt,
            index_path=DECISION_INDEX,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            evaluation_role=evaluation_role,
            case_split=args.case_split,
        )
        print("\n[orchestration axis 3]")
        o = run_axis(
            runner=run_orch,
            scorer=sorch,
            cases=orch_cases,
            prefix="orchestration",
            model=args.model,
            label=label,
            system_prompt=orch_prompt,
            index_path=ORCH_INDEX,
            concurrency=args.concurrency,
            dry_run=args.dry_run,
            evaluation_role=evaluation_role,
            case_split=args.case_split,
        )

        if args.dry_run:
            continue

        print(
            f"\n  DECISION   [{label}] n={d['n']} accuracy={d['decision_accuracy']:.0%} "
            f"OCS={d['ocs']:+.3f} bypass_fails={len(d['bypass_failures'])} "
            f"unparseable={d['unparseable']}"
        )
        print(
            f"  ORCHESTR(kw) [{label}] n={o['n']} mean_score={o['mean_score']:.3f} "
            f"perfect={o['perfect_cases']}/{o['n']} failed_anchors={len(o['failed_anchors'])} "
            "  [keyword/legacy — saturates; LLM-judge is the metric of record below]"
        )
        if args.judge:
            print("\n[orchestration axis 3 — LLM-judge (metric of record)]")
            j = judge_label(sjudge, label, judge_model, args.concurrency)
            print(
                f"  ORCH(judge) [{label}] n={j['n']} mean_score={j['mean_score']:.3f} "
                f"perfect={j['perfect_cases']}/{j['n']} unparseable={j['unparseable']} "
                f"judge={judge_model}  [axis-3 METRIC OF RECORD]"
            )
        rl = d.get("rate_limited", []) + o.get("rate_limited", [])
        miss = d.get("missing", []) + o.get("missing", [])
        if rl:
            any_rate_limited = True
            print(
                f"  !! RATE-LIMITED / empty ({len(rl)}): {rl} — re-run label {label} after reset"
            )
        if miss:
            print(f"  !! MISSING reports ({len(miss)}): {miss}")

    print("\nDone.")
    if any_rate_limited:
        print(
            "WARNING: some cells were rate-limited — their scores are "
            "INCOMPLETE. Re-run after the limit resets."
        )
    if not args.dry_run:
        judged_note = (
            "  (axis-3 LLM-judge already ran inline above — metric of record)\n"
            if args.judge
            else f"  python3 score_orchestration_judge.py --rescore-reports {base_label}   "
            "# axis-3 LLM-judge (metric of record) — judges this run's transcripts\n"
        )
        print(
            "Compare across models:\n"
            "  python3 score_suite.py                 "
            "# decision OCS + OrchJudge (metric of record)\n"
            + judged_note
            + "  python3 score_orchestration_judge.py --aggregate "
            + f"{base_label}         # judge mean\n"
            f"  python3 score_orchestration.py --aggregate {base_label}               "
            "# keyword orch mean (legacy/saturating — cross-check only)"
        )


if __name__ == "__main__":
    main()
