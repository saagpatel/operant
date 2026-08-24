#!/usr/bin/env python3
"""OPERANT Axis 3 — LLM-JUDGE scorer (sibling to the keyword-anchor scorer).

WHY THIS EXISTS. score_orchestration.py grades an operating plan with deterministic
keyword anchors. The 2026-06-18 headline run proved that approach saturates and
even inverts: the `forbid` anchors false-fire on plans that *explain or cite* the
mechanism they correctly decline ("worktrees exist for parallel agent isolation";
"per `auto-worktree.md`"). Two distinct false-positive classes surfaced in a single
re-score — keyword presence is a poor proxy for an orchestration DECISION.

This scorer replaces the keyword anchors with a calibrated LLM judge. For each plan
it dispatches a judge model with (a) the task, (b) the `reference` rationale already
authored in every case, and (c) the candidate plan, and asks for a per-dimension
correctness verdict. The score is the fraction of dimensions judged correct — the
SAME 0 / .333 / .667 / 1.0 granularity as the 3-anchor keyword scorer, so the two
are directly comparable. The judge is instructed to grade the DECISION, not verbosity,
and to treat declined/explained machinery as correct — directly closing the keyword
scorer's failure mode.

The deterministic core (prompt build, JSON extraction, score computation) is unit-
tested for free in selftest.py. The judge dispatch is calibration-validated with a
paid `--validate` run (judge must score ORACLE plans high and the OVER/UNDER traps low).

Usage:
  Validate the judge (paid, ~27 calls):   python3 score_orchestration_judge.py --validate
  Re-judge existing transcripts:
    python3 score_orchestration_judge.py --rescore-reports sonnet-r1 opus-r1 ...
  One plan:
    python3 score_orchestration_judge.py <case_id> <report_file>
  Aggregate a label:                      python3 score_orchestration_judge.py --aggregate <label>
"""

import argparse
import collections
import concurrent.futures as cf
import glob
import importlib.util
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from operant_lab.artifacts import (
    filter_unblocked_index_rows,
    receipt_output_scoring_block_reason,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "results", "reports")
# OPERANT_JUDGE_INDEX overrides the index path (e.g. a separate file for an
# alternate judge model), mirroring OPERANT_AXIS3_CASES. Default is the Sonnet-judge
# index of record.
INDEX = os.environ.get("OPERANT_JUDGE_INDEX") or os.path.join(
    HERE, "results", "operant_orchestration_judge_index.jsonl"
)
# The ensemble mode (#4a) averages this Sonnet-judge index against the Opus-judge
# index to cancel same-model judge self-preference. Both default to the indices of
# record; --judge-index-a / --judge-index-b override for an alternate pairing.
ENSEMBLE_INDEX_A = INDEX
ENSEMBLE_INDEX_B = os.environ.get("OPERANT_JUDGE_OPUS_INDEX") or os.path.join(
    HERE, "results", "operant_orchestration_judge_opus_index.jsonl"
)
DEFAULT_JUDGE_MODEL = "claude-sonnet-4-6"

# The three dimensions the judge scores. Deliberately the same arity as the keyword
# scorer's typical 3 anchors (tier / no_team-or-isolation / routing) so judge means
# line up against keyword means on the same 0..1 scale.
DIMENSIONS = ("tier", "isolation", "routing")
_VERDICTS = ("correct", "over_orchestration", "under_orchestration", "mixed")

JUDGE_INSTRUCTION = """\
You are a STRICT, CALIBRATED judge scoring an AI coding agent's ORCHESTRATION PLAN
— how it proposes to size and route a software task (solo vs parallel subagents vs
coordinated team; which model per subtask; worktree isolation; review safeguards).
You are NOT performing the task. You judge the plan against the reference.

TASK THE AGENT WAS GIVEN:
{task_prompt}

REFERENCE — the correct operating decision and the reason it is correct:
{reference}

THE AGENT'S PLAN:
<<<PLAN
{plan}
PLAN>>>

Score three dimensions, each "correct" or "wrong", judged against the reference:
  - tier:       correct tier/size (solo / parallel subagents / coordinated team)?
                Over-sizing a small single-concern job, or under-sizing a genuinely
                large multi-stream one, is "wrong".
  - isolation:  correct worktree/isolation call? Worktrees are for parallel
                code-writers; a solo or read-only task needs none. Choosing none
                when none is needed is "correct".
  - routing:    appropriate model routing AND avoidance of BOTH over-orchestration
                (machinery the task does not need) and under-orchestration.

Then classify the overall verdict: one of
"correct" | "over_orchestration" | "under_orchestration" | "mixed".

CRITICAL JUDGING RULES:
  - Judge the DECISION, not verbosity. A correct plan that explains its reasoning at
    length is still "correct".
  - Naming or CITING heavy machinery only to DECLINE it (e.g. "no worktree needed",
    "worktrees exist for parallel isolation, irrelevant here", "per the worktree
    policy doc") is CORRECT, not over-orchestration.
  - Only a COMMITMENT to unnecessary machinery is over-orchestration.

Output ONLY a single JSON object on one line, no prose, no code fence:
{{"tier":"correct","isolation":"correct","routing":"correct",\
"verdict":"correct","rationale":"<=1 sentence"}}
"""


def _import_sibling(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(HERE, name + ".py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_cases(path=None):
    return _import_sibling("score_orchestration").load_cases(path)


def build_judge_prompt(case, plan):
    return JUDGE_INSTRUCTION.format(
        task_prompt=case["task_prompt"].strip(),
        reference=case["reference"].strip(),
        plan=plan.strip(),
    )


def extract_json(text):
    """Pull the first balanced JSON object out of judge output. Tolerates a code
    fence or leading prose. Returns the parsed dict, or None if no object parses."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = []
    if fenced:
        candidates.append(fenced.group(1))
    # Greedy outermost-brace span as a fallback.
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        candidates.append(brace.group(0))
    for cand in candidates:
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def normalize_verdict(raw):
    """Coerce a judge object into the canonical verdict shape, or return None if it
    lacks the dimension keys. Unknown dimension values default to 'wrong' (a judge
    that won't commit to 'correct' does not earn the point)."""
    if not isinstance(raw, dict):
        return None
    if not any(d in raw for d in DIMENSIONS):
        return None
    out = {}
    for d in DIMENSIONS:
        val = str(raw.get(d, "wrong")).strip().lower()
        out[d] = "correct" if val == "correct" else "wrong"
    verdict = str(raw.get("verdict", "")).strip().lower()
    out["verdict"] = verdict if verdict in _VERDICTS else "mixed"
    out["rationale"] = str(raw.get("rationale", ""))[:240]
    return out


def verdict_score(v):
    """Fraction of dimensions judged correct, rounded to match the keyword scorer."""
    return round(sum(1 for d in DIMENSIONS if v[d] == "correct") / len(DIMENSIONS), 3)


_TRANSIENT_RE = re.compile(
    r"API Error|Overloaded|\b429\b|\b500\b|\b503\b|\b529\b", re.I
)


def _dispatch_judge(prompt, judge_model, timeout=300, retries=3):
    """Run the judge via `claude -p` (no tools — pure text judgment). Returns the
    judge's result text, or '' on failure. Retries on transient API errors (529
    Overloaded etc., which come back AS the result string) with linear backoff —
    the headless judge has no built-in retry."""
    cmd = [
        "claude",
        "-p",
        prompt,
        "--model",
        judge_model,
        "--strict-mcp-config",
        "--output-format",
        "json",
        "--disallowedTools",
        "Bash,Edit,Write,NotebookEdit,Read,Glob,Grep",
    ]
    text = ""
    for attempt in range(retries + 1):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return ""
        try:
            data = json.loads(proc.stdout)
            events = data if isinstance(data, list) else [data]
            result_event = next(e for e in events if e.get("type") == "result")
            text = result_event.get("result") or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text and not _TRANSIENT_RE.search(text):
            return text
        if attempt < retries:
            time.sleep(2 * (attempt + 1))
    return text


def judge_one(case, plan, judge_model=DEFAULT_JUDGE_MODEL, dry_run=False):
    """Judge a single plan. Returns a row dict with score + per-dimension verdict, or
    a sentinel row with score=None and an error when the judge output won't parse."""
    prompt = build_judge_prompt(case, plan)
    if dry_run:
        return {"case_id": case["id"], "dry_run": True, "prompt_chars": len(prompt)}
    raw_text = _dispatch_judge(prompt, judge_model)
    parsed = normalize_verdict(extract_json(raw_text))
    if parsed is None:
        return {
            "case_id": case["id"],
            "axis": "orchestration_judge",
            "judge_model": judge_model,
            "score": None,
            "error": "unparseable_judge_output",
            "raw_tail": raw_text[-200:],
        }
    return {
        "case_id": case["id"],
        "tier": case.get("tier"),
        "axis": "orchestration_judge",
        "judge_model": judge_model,
        "score": verdict_score(parsed),
        "dimensions": {d: parsed[d] for d in DIMENSIONS},
        "verdict": parsed["verdict"],
        "rationale": parsed["rationale"],
    }


def aggregate(rows):
    scored = [r for r in rows if r.get("score") is not None]
    n = len(scored)
    mean = round(sum(r["score"] for r in scored) / n, 3) if n else 0.0
    perfect = sum(1 for r in scored if r["score"] == 1.0)
    return {
        "n": n,
        "unparseable": len(rows) - n,
        "mean_score": mean,
        "perfect_cases": perfect,
        "verdict_mix": dict(collections.Counter(r.get("verdict") for r in scored)),
    }


# --------------------------------------------------------------------------- #
# #4a — Ensemble / averaged judge.  Averaging the Sonnet- and Opus-judge scores
# per (run_label, case_id) cancels the symmetric ~2-3pt same-model self-preference
# the cross-check measured, yielding a family-neutral Sonnet-vs-Opus call.
# --------------------------------------------------------------------------- #


def read_judge_index(path):
    """Load a judge index file into a list of row dicts (empty if missing)."""
    rows = []
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            for ln in fh:
                if ln.strip():
                    rows.append(json.loads(ln))
    return filter_unblocked_index_rows(Path(HERE), rows)


def ensemble_cells(rows_a, rows_b):
    """Inner-join two judge indices on (run_label, case_id) and average their
    per-cell scores. Cells missing or None-scored in EITHER index are dropped (a
    complete matrix has every cell in both). Deterministic — sorted by key.

    Each returned cell: run_label, case_id, tier, score_a, score_b,
    score (the mean, the family-neutral number), delta (signed score_a - score_b)."""

    def _by_key(rows):
        out = {}
        for r in rows:
            label, cid = r.get("run_label"), r.get("case_id")
            # A row missing either key can't join (and would crash the key sort on
            # None < str); drop it rather than carry a malformed cell.
            if r.get("score") is None or label is None or cid is None:
                continue
            out[(label, cid)] = r
        return out

    a, b = _by_key(rows_a), _by_key(rows_b)
    cells = []
    for key in sorted(a.keys() & b.keys()):
        ra, rb = a[key], b[key]
        sa, sb = ra["score"], rb["score"]
        cells.append(
            {
                "run_label": key[0],
                "case_id": key[1],
                "tier": ra.get("tier") or rb.get("tier"),
                "score_a": sa,
                "score_b": sb,
                "score": round((sa + sb) / 2, 4),
                "delta": round(sa - sb, 4),
            }
        )
    return cells


def _band_by_model(score_of_cell, cells):
    """{model: {mean, min, max, n_labels}} computed from per-run-label means, so the
    band is the spread across repeats (matching the judge-table bands in RESULTS)."""
    by_label = collections.defaultdict(list)
    for c in cells:
        by_label[c["run_label"]].append(score_of_cell(c))
    label_mean = {lbl: statistics.mean(v) for lbl, v in by_label.items()}
    by_model = collections.defaultdict(list)
    for lbl, m in label_mean.items():
        by_model[_model_of(lbl)].append(m)
    return {
        mdl: {
            "mean": round(statistics.mean(ms), 4),
            "min": round(min(ms), 4),
            "max": round(max(ms), 4),
            "n_labels": len(ms),
        }
        for mdl, ms in by_model.items()
    }


def disagreement_cells(cells, threshold=0.01):
    """Cells where the two judges differ by more than `threshold`. Scores are on a
    1/3 grid, so any non-zero delta is a real per-dimension disagreement; the >0.01
    cut just excludes float dust. Sorted by descending |delta|, then key."""
    dis = [c for c in cells if abs(c["delta"]) > threshold]
    return sorted(dis, key=lambda c: (-abs(c["delta"]), c["run_label"], c["case_id"]))


def ensemble_summary(cells):
    """Per-model bands under judge A, judge B, and the ENSEMBLE average — the table
    that shows the self-preference cancelling — plus the disagreement cells."""
    return {
        "n_cells": len(cells),
        "judge_a": _band_by_model(lambda c: c["score_a"], cells),
        "judge_b": _band_by_model(lambda c: c["score_b"], cells),
        "ensemble": _band_by_model(lambda c: c["score"], cells),
        "disagreements": disagreement_cells(cells),
    }


def run_ensemble(index_a, index_b):
    """Load both judge indices, average them, and print the de-biased ranking, the
    side-by-side per-judge bands, and the disagreement map. Pure analysis — 0 calls."""
    rows_a = read_judge_index(index_a)
    rows_b = read_judge_index(index_b)
    cells = ensemble_cells(rows_a, rows_b)
    summary = ensemble_summary(cells)
    judge_a = rows_a[0].get("judge_model", "A") if rows_a else "A"
    judge_b = rows_b[0].get("judge_model", "B") if rows_b else "B"

    print(f"ENSEMBLE over {summary['n_cells']} paired cells")
    print(f"  judge A = {judge_a}   judge B = {judge_b}\n")
    hdr = f"  {'model':7s} {'judgeA':>16s} {'judgeB':>16s} {'ENSEMBLE':>22s}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for mdl in sorted(
        summary["ensemble"], key=lambda m: -summary["ensemble"][m]["mean"]
    ):
        a, b, e = (summary[k][mdl] for k in ("judge_a", "judge_b", "ensemble"))
        band = f"[{e['min']:.4f},{e['max']:.4f}]" if e["n_labels"] > 1 else "(n=1)"
        print(
            f"  {mdl:7s} {a['mean']:>16.4f} {b['mean']:>16.4f} "
            f"{e['mean']:>10.4f} {band:>11s}"
        )

    dis = summary["disagreements"]
    print(
        f"\n  JUDGE-DISAGREEMENT MAP — {len(dis)} of {summary['n_cells']} cells "
        f"differ (|Δ|>0.01)"
    )
    by_case = collections.Counter(c["case_id"] for c in dis)
    for cid, n in by_case.most_common():
        print(f"    {cid:34s} x{n}")
    print("\n  cell-level (Δ = judgeA - judgeB):")
    for c in dis:
        print(
            f"    [{c['run_label']:9s} {c['case_id']:34s}] "
            f"A={c['score_a']:.3f} B={c['score_b']:.3f} Δ={c['delta']:+.3f}"
        )
    return summary


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #
_NAME_RE = re.compile(r"orchestration__(?P<label>.+)__(?P<case>[^_]+)\.txt$")


def _model_of(label):
    return label.split("-r")[0]


def _already_judged():
    """(run_label, case_id) pairs already present in the judge index, so a re-score
    is incremental — only un-judged transcripts cost judge calls."""
    seen = set()
    if os.path.exists(INDEX):
        with open(INDEX, encoding="utf-8") as index_file:
            for ln in index_file:
                if ln.strip():
                    r = json.loads(ln)
                    seen.add((r.get("run_label"), r.get("case_id")))
    return seen


def rescore_reports(labels, judge_model, concurrency, limit=None, skip_judged=True):
    """Re-judge existing orchestration transcripts for the given run labels and
    append rows to the judge index. Zero new SUBJECT dispatches — only judge calls.
    Incremental by default: transcripts already in the judge index are skipped."""
    cases = load_cases()
    seen = _already_judged() if skip_judged else set()
    jobs = []
    skipped = 0
    for path in sorted(glob.glob(os.path.join(REPORTS, "orchestration__*.txt"))):
        m = _NAME_RE.search(os.path.basename(path))
        if not m:
            continue
        label, cid = m.group("label"), m.group("case")
        if labels and label not in labels:
            continue
        if cid not in cases:
            continue
        plan = Path(path).read_text(encoding="utf-8")
        if receipt_output_scoring_block_reason(
            Path(HERE),
            run_label=label,
            case_id=cid,
            final_answer=plan,
            require_receipt=True,
        ):
            skipped += 1
            continue
        if (label, cid) in seen:
            skipped += 1
            continue
        jobs.append((label, cid, plan))
    if limit:
        jobs = jobs[:limit]
    if skipped:
        print(f"(skipping {skipped} already-judged transcripts)", flush=True)
    print(
        f"judge={judge_model}  transcripts={len(jobs)}  concurrency={concurrency}",
        flush=True,
    )
    rows = []
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)

    def work(job):
        label, cid, plan = job
        row = judge_one(cases[cid], plan, judge_model=judge_model)
        row["run_label"] = label
        return row

    with (
        cf.ThreadPoolExecutor(max_workers=concurrency) as pool,
        open(INDEX, "a", encoding="utf-8") as fh,
    ):
        for row in pool.map(work, jobs):
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            rows.append(row)
            sc = row.get("score")
            print(
                f"  [{row['run_label']}/{row['case_id']}] "
                f"score={sc if sc is not None else '??'} "
                f"{row.get('verdict', row.get('error', ''))}",
                flush=True,
            )

    by_model = collections.defaultdict(list)
    for r in rows:
        if r.get("score") is not None:
            by_model[_model_of(r["run_label"])].append(r["score"])
    print("\n=== JUDGE mean by model ===")
    for mdl, vs in sorted(by_model.items()):
        print(f"  {mdl:7s} mean={statistics.mean(vs):.3f}  n={len(vs)}")
    return rows


def validate(judge_model, concurrency):
    """Calibration: the judge must score ORACLE plans high and the OVER/UNDER traps
    low on the cases they mis-size. Paid (imports the reference plans from selftest)."""
    st = _import_sibling("selftest")
    cases = load_cases()
    oracle = st._ORACLE_PLANS
    over_plan, under_plan = st._OVER_PLAN, st._UNDER_PLAN
    over_traps = (
        "one-file-typo-overkill",
        "research-sweep-haiku",
        "looks-big-but-solo",
    )
    under_traps = ("eight-stream-migration", "parallel-features-worktree")

    tasks = []
    for cid in cases:
        if cid in oracle:
            tasks.append(("ORACLE", cid, oracle[cid]))
    for cid in over_traps:
        tasks.append(("OVER", cid, over_plan))
    for cid in under_traps:
        tasks.append(("UNDER", cid, under_plan))

    def work(t):
        kind, cid, plan = t
        row = judge_one(cases[cid], plan, judge_model=judge_model)
        return kind, cid, row.get("score"), row.get("verdict", row.get("error"))

    print(f"VALIDATE judge={judge_model}  n={len(tasks)}\n", flush=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for kind, cid, score, verdict in pool.map(work, tasks):
            results.append((kind, cid, score, verdict))
            print(f"  {kind:6s} {cid:26s} score={score}  {verdict}", flush=True)

    def mean(kind):
        vs = [s for k, _c, s, _v in results if k == kind and s is not None]
        return statistics.mean(vs) if vs else float("nan")

    oracle_mean = mean("ORACLE")
    over_mean = mean("OVER")
    under_mean = mean("UNDER")
    print("\n=== CALIBRATION ===")
    print(f"  ORACLE mean = {oracle_mean:.3f}  (want high, >=0.85)")
    print(f"  OVER   mean = {over_mean:.3f}  (want < ORACLE on over-traps)")
    print(f"  UNDER  mean = {under_mean:.3f}  (want < ORACLE on under-traps)")
    ok = oracle_mean >= 0.85 and over_mean < oracle_mean and under_mean < oracle_mean
    print(f"\n  CALIBRATION {'PASSED' if ok else 'FAILED'}")
    return ok


def main():
    ap = argparse.ArgumentParser(description="OPERANT Axis 3 — LLM-judge scorer")
    ap.add_argument("case_id", nargs="?")
    ap.add_argument("report_file", nargs="?")
    ap.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--validate", action="store_true")
    ap.add_argument(
        "--rescore-reports",
        nargs="*",
        metavar="LABEL",
        default=None,
        help="re-judge existing transcripts for these labels (empty = all)",
    )
    ap.add_argument("--limit", type=int, default=None, help="cap transcripts (debug)")
    ap.add_argument("--aggregate", default=None, metavar="LABEL")
    ap.add_argument(
        "--ensemble",
        action="store_true",
        help="average two judge indices to a family-neutral ranking + disagreement map",
    )
    ap.add_argument("--judge-index-a", default=ENSEMBLE_INDEX_A, metavar="PATH")
    ap.add_argument("--judge-index-b", default=ENSEMBLE_INDEX_B, metavar="PATH")
    args = ap.parse_args()

    if args.validate:
        sys.exit(0 if validate(args.judge_model, args.concurrency) else 1)

    if args.ensemble:
        run_ensemble(args.judge_index_a, args.judge_index_b)
        return

    if args.rescore_reports is not None:
        rescore_reports(
            args.rescore_reports, args.judge_model, args.concurrency, args.limit
        )
        return

    if args.aggregate:
        with open(INDEX, encoding="utf-8") as index_file:
            rows = [json.loads(ln) for ln in index_file if ln.strip()]
        rows = [r for r in rows if r.get("run_label") == args.aggregate]
        rows = filter_unblocked_index_rows(Path(HERE), rows)
        if not rows:
            raise SystemExit(f"no judge rows for label {args.aggregate!r}")
        summary = aggregate(rows)
        summary["run_label"] = args.aggregate
        print(json.dumps(summary, indent=2))
        return

    if not args.case_id or not args.report_file:
        raise SystemExit(
            "usage: see --help (need <case_id> <report_file> | --validate | "
            "--rescore-reports | --aggregate)"
        )
    cases = load_cases()
    if args.case_id not in cases:
        raise SystemExit(f"unknown case: {args.case_id}")
    match = _NAME_RE.search(os.path.basename(args.report_file))
    if match:
        plan = Path(args.report_file).read_text(encoding="utf-8")
        block_reason = receipt_output_scoring_block_reason(
            Path(HERE),
            run_label=match.group("label"),
            case_id=args.case_id,
            final_answer=plan,
        )
        if block_reason:
            raise SystemExit(f"refusing blocked receipt judge: {block_reason}")
    else:
        plan = Path(args.report_file).read_text(encoding="utf-8")
    row = judge_one(cases[args.case_id], plan, judge_model=args.judge_model)
    print(json.dumps(row, indent=2))


if __name__ == "__main__":
    main()
