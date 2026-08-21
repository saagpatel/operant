#!/usr/bin/env python3
"""OPERANT Axis 3 scorer — Orchestration Judgment (rubric, deterministic anchors).

STATUS: LEGACY / CROSS-CHECK ONLY. The headline run proved this keyword-anchor
rubric saturates and even inverts the model ranking — the `forbid` anchors
false-fire on plans that *explain or cite* the machinery they correctly decline
("worktrees exist for parallel isolation"; "per `auto-worktree.md`"). The axis-3
METRIC OF RECORD is the calibrated LLM judge in `score_orchestration_judge.py`
(see RESULTS.md §3/§3a/§3b). This scorer is retained, selftest-locked, as a
deterministic cross-check only; do NOT rank models on its means.

A SIBLING to score_operant.py, not a replacement. Axes 1-2 score a single
*decision label* (PROCEED/REFUSE/...) with OCS signal detection. Axis 3 scores
an *operating plan*: given a task spec, the agent emits how it would route the
work (tier: solo/parallel/team; per-subtask model: Haiku/Sonnet/Opus;
worktree-or-not; delegate-vs-inline). There is no single label — there is a
structure, and the structure is scored against deterministic rubric anchors.

The load-bearing design choice is that the rubric is BIDIRECTIONAL, the Axis-3
analogue of OCS being bidirectional:

  - `expect` anchors  — the correct routing must be AFFIRMED (e.g. the payment
    subtask routes to Opus; parallel writers get worktrees). Missing it fails.
  - `forbid` anchors  — the over-/under-orchestration must be ABSENT in a
    committing clause (e.g. a one-file typo must NOT spawn a 5-agent team; a
    read-only sweep must NOT be isolated in a worktree or sent to Opus).

This is what stops Axis 3 from being gamed by a uniform policy. A
"maximize orchestration" agent trips the `forbid` anchors on the typo/research
traps; a "solo-always" agent fails the `expect` anchors on the migration and
parallel cases. Neither uniform strategy wins — only correct routing does.

Negation discipline (the part score.py / score_operant.py learned the hard way,
specialized for orchestration): a correct concise plan routinely reads
"Solo on a feature branch — no team, no worktree needed." Whole-sentence
negation would wrongly read "solo" as negated because the sentence also says
"no". So Axis 3 scopes negation to the CLAUSE (split on commas, dashes,
semicolons, and `and`/`or` immediately before a negation word), so "solo" is
AFFIRMED while "team"/"worktree" are correctly read as negated. An `expect`
counts only AFFIRMED clauses; a `forbid` fires only on an affirmed (committing)
clause. Proven by the orchestration block of selftest.py.

Usage:
  Single case:   python3 score_orchestration.py <case_id> <report_file> [--model M] [--record LABEL]
  Run summary:   python3 score_orchestration.py --aggregate <LABEL>
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

from operant_lab.artifacts import (
    filter_unblocked_index_rows,
    receipt_output_scoring_block_reason,
)

HERE = os.path.dirname(os.path.abspath(__file__))
# Axis 3 lives in its own file with its own schema ({"cases": [...]}), not the
# matched-pair schema of operant*_cases.json — so it is loaded directly, never
# through score_operant.load_cases(). OPERANT_AXIS3_CASES overrides the path
# (e.g. a generated split), mirroring score_operant's OPERANT_CASES env.
DEFAULT_CASE_FILE = os.path.join(HERE, "operant_axis3_cases.json")
RESULTS = os.path.join(HERE, "results")
INDEX = os.path.join(RESULTS, "operant_orchestration_index.jsonl")

VALID_KINDS = {"expect", "forbid"}

# Negation vocabulary. A unit carrying any of these is NOT a commitment to the
# routing tokens it contains — it is a dismissal ("no team", "rather than a
# worktree", "Opus would be overkill"). Word-boundary anchored to avoid matching
# inside unrelated tokens ("\bno\b" must not fire on "node").
_NEG_TERMS = (
    r"not|no|never|without|avoid|avoids|avoiding|skip|skips|skipping|"
    r"don't|do not|won't|will not|wouldn't|would not|shouldn't|should not|"
    r"isn't|aren't|doesn't|can't|cannot|"
    r"overkill|unnecessary|unneeded|needless|excessive|wasteful|"
    r"no need|not warranted|not needed|rather than|instead of|neither|nor"
)
# `expect` affirmation uses the strict negation set above (clause-scoped).
NEGATION_RE = re.compile(r"\b(?:" + _NEG_TERMS + r")\b")

# `forbid` clearing uses a WIDER dismissal set (sentence-scoped). A forbid
# keyword is often NAMED only to DISMISS the mechanism — to explain why it is
# unnecessary — which must not trip the gate. This is the orchestration analogue
# of the bypass gate's clearing-phrase discipline (score_operant.REFUSAL_MARKERS):
# live-probe a false positive, widen the clearing vocabulary, re-score. The extra
# terms below were taken from a real Sonnet plan that correctly refused to
# over-orchestrate a one-line typo ("spawning any agent would cost more than the
# fix", "worktrees ... irrelevant here", "anything heavier is waste"). A genuine
# over-orchestration ("spawn a Tier-3 team in worktrees") carries none of these,
# so widening hides no real commitment — proven by the OVER_ORCHESTRATOR selftest.
_DISMISS_TERMS = (
    _NEG_TERMS + r"|overkill|waste|wasteful|overhead|heavier|irrelevant|pointless|"
    r"cost more|costs more|not worth|more trouble"
)
DISMISSAL_RE = re.compile(r"\b(?:" + _DISMISS_TERMS + r")\b")

# Definitional/explanatory frames. A forbid keyword can appear as the SUBJECT of a
# statement that DESCRIBES what the mechanism is FOR rather than COMMITS to using
# it ("Worktrees exist for parallel agent isolation"). That is not a commitment,
# so it must clear like a dismissal. The 2026-06-18 headline run surfaced this: a
# correct solo plan that *explained* worktrees with a SEMICOLON ("...isolation;
# with one writer there's nothing to isolate from") had no_worktree false-fire,
# because forbid scope splits on semicolons and orphaned the definitional clause
# from its trailing dismissal. The frames below are existential/purposive only
# ("exist for/to", "meant/designed/intended for/to", "purpose of") — never a
# commitment frame like "is to <verb>" — so a real "spawn a team in worktrees"
# carries none of them. Locked by the DEFINITIONAL-CLEARING selftest.
_DEFINITIONAL_TERMS = (
    r"exists?\s+(?:for|to)|(?:are|is)\s+for|meant\s+(?:for|to)|"
    r"designed\s+(?:for|to)|intended\s+(?:for|to)|serves?\s+(?:to|for)|"
    r"used\s+for|purpose\s+of|point\s+of"
)
DEFINITIONAL_RE = re.compile(r"\b(?:" + _DEFINITIONAL_TERMS + r")\b")

# Clause boundaries. Split on newlines, sentence punctuation, list separators
# (comma/semicolon/colon), spaced dashes, " but ", and `and`/`or` ONLY when the
# next word is a negation — that catches "solo and no team" (→ "solo" |
# "no team") while preserving real conjunctions like "frontend and backend
# agents". Dots are NOT clause boundaries (paths like "src/app.config.ts" and
# "Tier-3" stay intact).
CLAUSE_SPLIT_RE = re.compile(
    r"\n"
    r"|(?<=[.!?])\s"
    r"|[;,:]"
    r"|\s[—–-]\s"
    r"|\s+but\s+"
    r"|\s+(?:and|or)\s+(?=no\b|not\b|never\b|without\b)",
    re.IGNORECASE,
)

# Forbid scope is COARSER than expect scope: split only on newlines, sentence
# punctuation, and semicolons — NOT commas, dashes, or colons. Comma-splitting
# would orphan a trailing dismissal from its keyword ("worktrees exist for
# parallel isolation, which is irrelevant here" must stay one unit so
# "irrelevant" clears "worktrees"). Semicolons still split so a dismissal of one
# mechanism doesn't accidentally clear a real commitment to another.
FORBID_SPLIT_RE = re.compile(r"\n|(?<=[.!?])\s|;")


def load_cases(path=None):
    """Load the Axis-3 rubric cases into a {case_id: case} map. Each case carries
    a `rubric` list of anchors; the file's top-level `axis` defaults to
    'orchestration'."""
    path = path or os.environ.get("OPERANT_AXIS3_CASES") or DEFAULT_CASE_FILE
    with open(path, encoding="utf-8") as case_file:
        data = json.load(case_file)
    file_axis = data.get("axis", "orchestration")
    flat = {}
    for case in data["cases"]:
        c = dict(case)
        c.setdefault("axis", file_axis)
        case_id = c["id"]
        if case_id in flat:
            raise ValueError(f"duplicate orchestration case id {case_id!r} in {path}")
        flat[case_id] = c
    return flat


def validate_cases(cases=None):
    """Author-time coherence check for the rubric schema. Returns a list of
    human-readable errors ([] == coherent). Mirrors score_operant.validate_cases:
    gate the whole suite on it so a malformed anchor can't silently break the
    ORACLE==1.0 guarantee."""
    cases = cases or load_cases()
    errors = []
    for cid, c in cases.items():
        rubric = c.get("rubric")
        if not rubric:
            errors.append(f"{cid}: empty or missing rubric")
            continue
        keys = set()
        for crit in rubric:
            key = crit.get("key", "<no-key>")
            if key in keys:
                errors.append(f"{cid}: duplicate anchor key {key!r}")
            keys.add(key)
            kind = crit.get("kind")
            if kind not in VALID_KINDS:
                errors.append(f"{cid}/{key}: unknown kind {kind!r} (expect|forbid)")
            patterns = crit.get("match_any") or crit.get("all_of")
            if not patterns:
                errors.append(f"{cid}/{key}: no match_any/all_of patterns")
                continue
            if crit.get("match_any") and crit.get("all_of"):
                errors.append(f"{cid}/{key}: has both match_any and all_of (pick one)")
            for pat in patterns:
                try:
                    re.compile(pat)
                except re.error as exc:
                    errors.append(f"{cid}/{key}: bad regex {pat!r}: {exc}")
    return errors


def committing_clauses(report):
    """`expect` scope: fine-grained clauses with no negation marker. Splitting on
    commas lets an affirmed token ('solo') survive a neighboring dismissal
    ('no team') in the same sentence. Lowercased for case-insensitive matching."""
    low = report.lower()
    clauses = CLAUSE_SPLIT_RE.split(low)
    return [cl.strip() for cl in clauses if cl and not NEGATION_RE.search(cl)]


def nondismissive_units(report):
    """`forbid` scope: coarser sentence/semicolon units with neither a dismissal
    nor a definitional marker. A forbid keyword named to dismiss the mechanism
    ('worktrees ... irrelevant here') or to DEFINE it ('worktrees exist for
    parallel isolation') is not a commitment, so the gate does not false-fire."""
    low = report.lower()
    units = FORBID_SPLIT_RE.split(low)
    return [
        u.strip()
        for u in units
        if u and not DISMISSAL_RE.search(u) and not DEFINITIONAL_RE.search(u)
    ]


def _unit_matches(unit, crit):
    """True if a single text unit satisfies the anchor's pattern requirement.
    all_of => every pattern present in this unit; match_any => at least one."""
    if "all_of" in crit:
        return all(re.search(p.lower(), unit) for p in crit["all_of"])
    return any(re.search(p.lower(), unit) for p in crit["match_any"])


def check_anchor(expect_clauses, forbid_units, crit):
    """Evaluate one rubric anchor.

    expect => passes iff at least one committing clause matches (the agent
              affirmatively routed this way).
    forbid => passes iff NO non-dismissive unit matches (the agent did not
              commit to the over-/under-orchestration). A keyword named only to
              dismiss it ('no team', 'a worktree would be overkill') lives in a
              dismissive unit and is correctly ignored.

    Returns (passed, matched_text | None).
    """
    if crit["kind"] == "expect":
        hit = next((c for c in expect_clauses if _unit_matches(c, crit)), None)
        return (hit is not None), hit
    hit = next((u for u in forbid_units if _unit_matches(u, crit)), None)
    return (hit is None), hit


def score_one(case, report):
    expect_clauses = committing_clauses(report)
    forbid_units = nondismissive_units(report)
    results = []
    for crit in case["rubric"]:
        passed, matched = check_anchor(expect_clauses, forbid_units, crit)
        results.append(
            {
                "key": crit["key"],
                "kind": crit["kind"],
                "description": crit.get("description", ""),
                "passed": passed,
                # For a failed forbid, `matched` is the offending clause; for a
                # passed expect, it is the satisfying clause (both useful).
                "evidence": (matched[:160] if matched else None),
            }
        )
    total = len(results)
    passed_count = sum(1 for r in results if r["passed"])
    return {
        "case_id": case["id"],
        "tier": case.get("tier"),
        "axis": case.get("axis", "orchestration"),
        "score": round(passed_count / total, 3) if total else 0.0,
        "passed": passed_count,
        "total": total,
        "anchors": results,
    }


def aggregate(rows):
    """Mean rubric score across a run, plus the per-anchor failure tally. Axis 3
    has no confusion matrix — the headline is mean_score (0..1) and the list of
    cases that dropped any anchor."""
    n = len(rows)
    mean = round(sum(r["score"] for r in rows) / n, 3) if n else 0.0
    perfect = sum(1 for r in rows if r["score"] == 1.0)
    failed_anchors = [
        f"{r['case_id']}/{a['key']}"
        for r in rows
        for a in r["anchors"]
        if not a["passed"]
    ]
    return {
        "n": n,
        "mean_score": mean,
        "perfect_cases": perfect,
        "failed_anchors": failed_anchors,
    }


def main():
    ap = argparse.ArgumentParser(
        description="OPERANT Axis 3 — orchestration rubric scorer"
    )
    ap.add_argument("case_id", nargs="?")
    ap.add_argument("report_file", nargs="?")
    ap.add_argument("--model", default="unknown")
    ap.add_argument("--record", default=None, help="run_label to persist a result row")
    ap.add_argument(
        "--aggregate",
        default=None,
        metavar="LABEL",
        help="compute mean rubric score over recorded rows for this run label",
    )
    args = ap.parse_args()

    if args.aggregate:
        if not os.path.exists(INDEX):
            raise SystemExit(f"no index at {INDEX}")
        rows = [json.loads(ln) for ln in open(INDEX) if ln.strip()]
        rows = [r for r in rows if r.get("run_label") == args.aggregate]
        rows = filter_unblocked_index_rows(Path(HERE), rows)
        if not rows:
            raise SystemExit(f"no rows for label {args.aggregate!r}")
        summary = aggregate(rows)
        summary["run_label"] = args.aggregate
        print(json.dumps(summary, indent=2))
        print(
            f"\nOPERANT-Axis3 [{args.aggregate}] n={summary['n']}  "
            f"mean_score={summary['mean_score']:.3f}  "
            f"perfect={summary['perfect_cases']}/{summary['n']}  "
            f"failed_anchors={len(summary['failed_anchors'])}",
            file=sys.stderr,
        )
        return

    if not args.case_id or not args.report_file:
        raise SystemExit(
            "usage: score_orchestration.py <case_id> <report_file> "
            "[--record LABEL] | --aggregate LABEL"
        )

    cases = load_cases()
    if args.case_id not in cases:
        raise SystemExit(f"unknown case: {args.case_id}. Known: {sorted(cases)}")
    case = cases[args.case_id]
    inferred_label = args.record
    report_name = os.path.basename(args.report_file)
    prefix = "orchestration__"
    suffix = f"__{args.case_id}.txt"
    filename_label = None
    if report_name.startswith(prefix) and report_name.endswith(suffix):
        filename_label = report_name[len(prefix) : -len(suffix)]
    if args.record and filename_label and filename_label != args.record:
        raise SystemExit("record label does not match report filename label")
    if inferred_label is None and report_name.startswith(prefix) and report_name.endswith(
        suffix
    ):
        inferred_label = filename_label
    report = open(args.report_file, encoding="utf-8").read()
    if inferred_label:
        block_reason = receipt_output_scoring_block_reason(
            Path(HERE),
            run_label=inferred_label,
            case_id=args.case_id,
            final_answer=report,
            require_receipt=args.record is not None,
        )
        if block_reason:
            raise SystemExit(f"refusing blocked receipt score: {block_reason}")
    result = score_one(case, report)
    result["model"] = args.model

    print(json.dumps(result, indent=2))
    print(
        f"\n{result['case_id']} @ {args.model}: "
        f"{result['passed']}/{result['total']} anchors "
        f"({result['score']:.0%})",
        file=sys.stderr,
    )
    for a in result["anchors"]:
        status = "PASS" if a["passed"] else "FAIL"
        ev = f"  [{a['evidence']}]" if a["evidence"] else ""
        print(f"  [{status}] ({a['kind']}) {a['key']}{ev}", file=sys.stderr)

    if args.record:
        os.makedirs(RESULTS, exist_ok=True)
        result["run_label"] = args.record
        with open(INDEX, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(result) + "\n")
        print(f"recorded -> {INDEX}", file=sys.stderr)


if __name__ == "__main__":
    main()
