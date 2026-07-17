#!/usr/bin/env python3
"""Re-score existing Axis-3 orchestration report transcripts against the CURRENT
score_orchestration scorer, with zero new model dispatches. Reads every
results/reports/orchestration__<label>__<case>.txt, recomputes the rubric score,
prints a before/after comparison (per model + per T3 case) against the recorded
index, and (with --write) rewrites results/operant_orchestration_index.jsonl with
the freshly-scored rows. Use after a scorer change to re-derive results from the
transcripts already paid for.
"""

import argparse
import collections
import glob
import json
import os
import re
import statistics
from pathlib import Path

import score_orchestration as so
from operant_lab.artifacts import (
    filter_unblocked_index_rows,
    receipt_output_scoring_block_reason,
)

HERE = os.path.dirname(os.path.abspath(__file__))
REPORTS = os.path.join(HERE, "results", "reports")
INDEX = so.INDEX
T3 = ("looks-big-but-solo", "mixed-sensitivity-routing", "false-parallelism")
NAME_RE = re.compile(r"orchestration__(?P<label>.+)__(?P<case>[^_]+)\.txt$")


def model_of(label):
    return label.split("-r")[0]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--write", action="store_true", help="rewrite the index with new scores"
    )
    args = ap.parse_args()

    cases = so.load_cases()

    # OLD scores from the recorded index.
    old = {}
    if os.path.exists(INDEX):
        rows = [json.loads(ln) for ln in open(INDEX) if ln.strip()]
        for row in filter_unblocked_index_rows(Path(HERE), rows):
            old[(row["run_label"], row["case_id"])] = row["score"]

    # NEW scores by re-running the current scorer over the transcripts.
    new_rows = []
    new = {}
    for path in sorted(glob.glob(os.path.join(REPORTS, "orchestration__*.txt"))):
        m = NAME_RE.search(os.path.basename(path))
        if not m:
            continue
        label, cid = m.group("label"), m.group("case")
        if cid not in cases:
            continue
        report = open(path, encoding="utf-8").read()
        if receipt_output_scoring_block_reason(
            Path(HERE),
            run_label=label,
            case_id=cid,
            final_answer=report,
            require_receipt=args.write,
        ):
            continue
        res = so.score_one(cases[cid], report)
        res["run_label"] = label
        new_rows.append(res)
        new[(label, cid)] = res["score"]

    # Per-model means, before/after.
    def model_means(scores):
        per_label = collections.defaultdict(list)
        for (label, _cid), sc in scores.items():
            per_label[label].append(sc)
        lab_mean = {lab: statistics.mean(v) for lab, v in per_label.items()}
        by_model = collections.defaultdict(list)
        for lab, mn in lab_mean.items():
            by_model[model_of(lab)].append(mn)
        return {m: statistics.mean(v) for m, v in by_model.items()}

    om, nm = model_means(old), model_means(new)
    print("=== ORCHESTRATION mean by model: BEFORE -> AFTER ===")
    for mdl in ("haiku", "sonnet", "opus"):
        if mdl in nm:
            b = om.get(mdl)
            print(
                f"  {mdl:7s} {b:.3f} -> {nm[mdl]:.3f}"
                if b is not None
                else f"  {mdl:7s}    -   -> {nm[mdl]:.3f}"
            )

    print("\n=== T3 cases: per-model mean, BEFORE -> AFTER ===")
    for c in T3:
        line = f"  {c:26s}"
        for mdl in ("haiku", "sonnet", "opus"):
            ob = [
                s for (lab, cid), s in old.items() if cid == c and model_of(lab) == mdl
            ]
            nb = [
                s for (lab, cid), s in new.items() if cid == c and model_of(lab) == mdl
            ]
            b = statistics.mean(ob) if ob else float("nan")
            n = statistics.mean(nb) if nb else float("nan")
            line += f"  {mdl}:{b:.2f}->{n:.2f}"
        print(line)

    # Cases whose score changed.
    changed = [(k, old.get(k), v) for k, v in sorted(new.items()) if old.get(k) != v]
    print(f"\n=== {len(changed)} (label,case) cells changed ===")
    for (label, cid), o, n in changed:
        print(f"  {label:10s} {cid:26s} {o} -> {n}")

    if args.write:
        with open(INDEX, "w", encoding="utf-8") as fh:
            for r in new_rows:
                fh.write(json.dumps(r) + "\n")
        print(f"\nrewrote {INDEX} with {len(new_rows)} re-scored rows")


if __name__ == "__main__":
    main()
