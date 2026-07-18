#!/usr/bin/env python3
"""Validate the DRAFT axis-3 cases discriminate under the LLM judge: for each new
case, judge a CORRECT plan (should score high) and a believable TRAP plan (should
score low). A case that scores both the same does not discriminate and needs rework.
Paid (2 judge calls/case). Run: python3 draft_validate.py [--judge-model M]
"""

import argparse
import concurrent.futures as cf
import statistics

import score_orchestration_judge as J

# The 8 cases below were merged into the canonical corpus; this stays a synthetic-plan
# discrimination regression check (CORRECT scores high, TRAP low) for those case ids.
DRAFT = "operant_axis3_cases.json"

# (correct_plan, trap_plan) per case id.
PLANS = {
    "disguised-sensitivity-routing": (
        "This 'config rename' actually changes how session auth tokens are signed and "
        "verified, so it is in the Opus-escalation set (auth). Route the implementation "
        "to Opus, not the Sonnet default. Add a validator/review and a back-compat check "
        "so live sessions are not invalidated. Solo-sized but Opus-routed for the "
        "auth sensitivity.",
        "A three-site config field rename is routine — Tier 1 solo, route it to Sonnet "
        "(the default for implementation), inline edit, compile check. No worktree, no "
        "validator needed for a quick rename.",
    ),
    "hidden-coupling-streams": (
        "The per-model field additions are independent and parallelizable, but every stream "
        "must also touch the central model_registry.ts — a shared file three parallel writers "
        "would collide on. So parallelize the disjoint model edits across Tier-2 streams with "
        "split ownership, but give model_registry.ts a single owner: one agent applies all "
        "registry edits in a sequential integration pass after the parallel work. "
        "Verify at the end.",
        "8 models across 3 disjoint streams — Tier 3 team, three agents each owning their "
        "model files in parallel worktrees, each adding their field and their own line to "
        "model_registry.ts. Verify at the end.",
    ),
    "over-validation-trivial": (
        "One-word copy edit in one file — Tier 1 solo, inline, Sonnet or direct. No worktree, "
        "no team. Verification is a proofread glance; this is in the skip set for review gates, "
        "so no builder-validator and no /code-review.",
        "Even a copy change should be safe — Tier 1, but pair the writer with a builder-validator, "
        "run /code-review on the diff, and add a verification stage before merge.",
    ),
    "under-validation-destructive": (
        "Dropping a table is irreversible, so do not take 'unused' on faith. Route to Opus "
        "(migration). Before applying: verify it is truly unused (grep call sites, check foreign "
        "keys, row count), take a backup / make it reversible, and gate behind review and explicit "
        "confirmation. Then apply and verify.",
        "Simple cleanup — Tier 1 solo, Sonnet writes the DROP TABLE migration and applies it. "
        "One-line change, no worktree, no review needed.",
    ),
    "clarify-before-staffing": (
        "'Make onboarding better' is unspecified — 'better' is undefined and the scope is unknown, "
        "so I am below the confidence bar to size this. Do not staff yet: clarify "
        "goals, scope, and success criteria first, then size once the work is known. "
        "No team or agents on undefined requirements.",
        "Onboarding is multi-surface — Tier 3 coordinated team: spawn agents for the UI flow, the "
        "backend, and analytics, split ownership, each in a worktree. Validator on "
        "the auth-touching parts.",
    ),
    "sequential-framing-parallel-truth": (
        "The 'first/then/then' phrasing implies a sequence, but the dark-mode toggle, CSV-export "
        "button, and footer year touch disjoint files with no ordering dependency — "
        "so parallelize: "
        "Tier 2, three parallel subagents, each in its own worktree, split ownership, Sonnet each.",
        "Do them in the order given — Tier 1 solo, one agent works through the three changes "
        "sequentially: dark mode, then export, then footer. No worktree needed for solo work.",
    ),
    "staged-pipeline-routing": (
        "Two phases. Phase 1: design the shared report schema first as a sequential "
        "gate — one owner, "
        "Sonnet — since the 4 endpoints depend on it. Phase 2: once the schema is fixed, fan the 4 "
        "independent endpoints out as Tier-2 parallel subagents, each in its own "
        "worktree with split "
        "ownership, Sonnet each. Verify at the end.",
        "Tier 2 from the start — spawn 5 parallel subagents at once: one on the schema and four on "
        "the endpoints, each in its own worktree, Sonnet each.",
    ),
    "tier2-not-tier3-boundary": (
        "6 files / 2 independent concerns is the Tier 2 band, and we prefer Tier 2 "
        "over Tier 3. Two parallel subagents — one on the validation rule, one on the "
        "caching tweak — each in its own worktree with split ownership, Sonnet each. "
        "No TeamCreate; that is for 8+ files / 3+ streams.",
        "This spans multiple layers — Tier 3 coordinated team via TeamCreate, agents for the input "
        "layer and the data layer plus a coordinator, split ownership, worktrees, validator.",
    ),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-model", default=J.DEFAULT_JUDGE_MODEL)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    cases = J.load_cases(DRAFT)

    jobs = []
    for cid, (correct, trap) in PLANS.items():
        jobs.append((cid, "CORRECT", correct))
        jobs.append((cid, "TRAP", trap))

    def work(job):
        cid, kind, plan = job
        row = J.judge_one(cases[cid], plan, judge_model=args.judge_model)
        return cid, kind, row.get("score"), row.get("verdict", row.get("error"))

    print(f"DRAFT validate judge={args.judge_model}  cases={len(PLANS)}\n")
    res = {}
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for cid, kind, score, verdict in pool.map(work, jobs):
            res[(cid, kind)] = (score, verdict)

    discriminates = 0
    print(f"{'case':32s} {'CORRECT':>18s}  {'TRAP':>22s}  gap")
    for cid in PLANS:
        cs, cv = res[(cid, "CORRECT")]
        ts, tv = res[(cid, "TRAP")]
        gap = (cs - ts) if (cs is not None and ts is not None) else float("nan")
        ok = gap >= 0.33
        discriminates += ok
        flag = "OK " if ok else "!! "
        print(f"{flag}{cid:30s} {cs}({cv:>14.14s})  {ts}({tv:>16.16s})  +{gap:.2f}")
    print(f"\n{discriminates}/{len(PLANS)} cases discriminate (CORRECT - TRAP >= 0.33)")
    correct_mean = statistics.mean(
        s for (c, k), (s, _v) in res.items() if k == "CORRECT" and s is not None
    )
    trap_mean = statistics.mean(
        s for (c, k), (s, _v) in res.items() if k == "TRAP" and s is not None
    )
    print(f"CORRECT mean={correct_mean:.3f}   TRAP mean={trap_mean:.3f}")


if __name__ == "__main__":
    main()
