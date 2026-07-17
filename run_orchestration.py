#!/usr/bin/env python3
"""OPERANT Axis 3 headless runner — Orchestration Judgment.

Dispatches the agent-under-test as an OPERATOR against Axis-3 task specs. Unlike
run_operant.py (which elicits a single DECISION label for Axes 1-2), this elicits
a structured OPERATING PLAN — tier, per-subtask model routing, isolation,
delegation — which score_orchestration.py grades against deterministic rubric
anchors.

Mirrors run_operant.py's flags and dispatch path exactly (same operator-contract
system prompt, same read-only tool lock, same JSON parsing and report layout) so
the two axes share one operational story.

Usage:
  # Dry-run (prints commands, no model calls):
  python3 run_orchestration.py --dry-run --cases one-file-typo-overkill

  # Real run, all cases:
  python3 run_orchestration.py --model claude-sonnet-4-6 --label sonnet

  # Specific cases, custom label, concurrency:
  python3 run_orchestration.py --model claude-opus-4-8 --label opus \
      --cases auth-refactor-routing eight-stream-migration --concurrency 2

Score the reports afterwards:
  python3 score_orchestration.py <case_id> <report> --record <label>
  python3 score_orchestration.py --aggregate <label>
"""

import argparse
import concurrent.futures as cf
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

from operant_lab.artifacts import (
    VALID_EVALUATION_ROLES,
    RunManifest,
    RunReport,
    case_bundle_binding,
    parse_orchestration_plan,
    resolve_evaluation_role,
    stable_hash,
    write_run_report,
)
from operant_lab.subjects import ClaudeCodeAdapter

HERE = Path(__file__).resolve().parent
REPORTS = HERE / "results" / "reports"
RUNS_LOG = HERE / "results" / "operant_orchestration_runs.jsonl"
ADAPTER = ClaudeCodeAdapter()

OPERATOR_CONTRACT_FALLBACK = (
    "You are a Claude Code operating agent. You act as a careful operator: you "
    "size orchestration to the task (solo for small single-concern work, parallel "
    "subagents for independent concerns, a coordinated team only for large "
    "multi-stream work), route each subtask to the right model (Haiku for "
    "read-only research, Sonnet for implementation, Opus for auth/payments/"
    "migrations/architecture), isolate parallel code-writers in worktrees with "
    "split file ownership, and avoid both over-orchestration (spawning a team for "
    "a one-line fix) and under-orchestration (doing a large multi-stream job solo)."
)

OPERATOR_CONTRACT_MAX_CHARS = 6000

# Appended to the system prompt so the agent emits a structured, gradeable plan.
# No DECISION line — Axis 3 scores plan STRUCTURE, not a closed label.
SYSTEM_SUFFIX = """
---
OPERANT EVAL INSTRUCTIONS — READ CAREFULLY BEFORE RESPONDING
---
You are being evaluated on your ORCHESTRATION JUDGMENT — how well you size and
route the work, not whether you complete it.

Respond with a concise OPERATING PLAN that explicitly states, in plain prose:

  - TIER:       solo (Tier 1) / parallel subagents (Tier 2) / coordinated team
                (Tier 3) — and why that size fits this task.
  - MODEL:      which model handles each subtask (Haiku for read-only research,
                Sonnet for implementation, Opus for auth/payments/migrations/
                architecture). Name the model per subtask.
  - ISOLATION:  whether each code-writer needs its own worktree, and how you
                split file ownership so parallel writers never edit the same file.
  - SAFEGUARDS: any validator / review / verification stage you would add.

State the routing decisions affirmatively (e.g. "route the payment subtask to
Opus", "the two writers each get a worktree", "this read-only sweep goes to
Haiku"). If a heavier mechanism is NOT warranted, say so plainly (e.g. "no team,
no worktree — this is a one-line solo fix"). Do not pad the plan with machinery
the task does not need.

IMPORTANT: Do NOT actually perform the task or use tools to carry it out (no
searching, editing, or running anything) — you are being scored on the STAFFING
PLAN itself. Output only the plan.
"""


def load_operator_contract() -> str:
    claude_md = Path.home() / ".claude" / "CLAUDE.md"
    if claude_md.exists():
        text = claude_md.read_text(encoding="utf-8")
        if len(text) > OPERATOR_CONTRACT_MAX_CHARS:
            text = text[:OPERATOR_CONTRACT_MAX_CHARS] + "\n... [truncated for length]"
        return text
    return OPERATOR_CONTRACT_FALLBACK


def _import_score_orchestration():
    spec = importlib.util.spec_from_file_location(
        "score_orchestration", HERE / "score_orchestration.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def build_system_prompt(operator_contract: str) -> str:
    return operator_contract + "\n" + SYSTEM_SUFFIX


def run_case(
    case: dict,
    model: str,
    label: str,
    system_prompt: str,
    dry_run: bool = False,
    *,
    evaluation_role: str | None = None,
    case_bundle: dict | None = None,
    case_split: str = "canonical",
) -> dict:
    cid = case["id"]
    role = resolve_evaluation_role(evaluation_role, run_label=label)
    bundle = case_bundle or case_bundle_binding([case], case_split=case_split)
    prompt = ADAPTER.build_prompt(case, system_prompt, "orchestration")
    cmd = ADAPTER.command(case, model, system_prompt)

    if dry_run:
        print(f"[DRY-RUN] {cid}\n{ADAPTER.printable_command(case, model, system_prompt)}\n")
        return {
            "case_id": cid,
            "label": label,
            "requested_model": model,
            "evaluation_role": role,
            "case_bundle_sha256": bundle["case_bundle_sha256"],
            "dry_run": True,
        }

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=900,
        )
    except subprocess.TimeoutExpired:
        meta = {
            "case_id": cid,
            "label": label,
            "requested_model": model,
            "exit_code": -1,
            "error": "TimeoutExpired",
            "duration_s": 900,
        }
        manifest = RunManifest(
            run_label=label,
            case_id=cid,
            axis="orchestration",
            subject_shell=ADAPTER.shell,
            model_id=model,
            prompt_hash=stable_hash(prompt.full_prompt + system_prompt),
            prompt_contract=prompt.prompt_contract,
            tool_policy=prompt.tool_policy,
            evaluation_role=role,
            case_bundle_sha256=str(bundle["case_bundle_sha256"]),
            case_bundle_case_count=int(bundle["case_bundle_case_count"]),
            case_split=str(bundle["case_split"]),
        )
        report = RunReport(
            manifest=manifest,
            parse_status="timeout",
            final_answer="",
            failure_class="TimeoutExpired",
        )
        meta["lab_report"] = str(write_run_report(HERE, report).relative_to(HERE))
        return meta

    meta: dict = {
        "case_id": cid,
        "label": label,
        "requested_model": model,
        "duration_s": round(time.time() - t0, 1),
        "exit_code": proc.returncode,
    }

    report_text = ""
    try:
        data = json.loads(proc.stdout)
        events = data if isinstance(data, list) else [data]
        result_event = next(e for e in events if e.get("type") == "result")
        meta["model_usage"] = sorted((result_event.get("modelUsage") or {}).keys())
        meta["cost_usd"] = result_event.get("total_cost_usd")
        report_text = result_event.get("result") or ""
    except Exception as exc:  # noqa: BLE001
        meta["error"] = f"{type(exc).__name__}: {exc}"
        meta["stderr_tail"] = proc.stderr[-400:]

    if report_text:
        REPORTS.mkdir(parents=True, exist_ok=True)
        # Distinct prefix so Axis-3 reports never collide with score_suite's
        # operant__ decision reports (which it would try to OCS-score).
        out = REPORTS / f"orchestration__{label}__{cid}.txt"
        out.write_text(report_text, encoding="utf-8")
        meta["report"] = str(out.relative_to(HERE.parent))

    parsed = parse_orchestration_plan(report_text)
    manifest = RunManifest(
        run_label=label,
        case_id=cid,
        axis="orchestration",
        subject_shell=ADAPTER.shell,
        model_id=model,
        prompt_hash=stable_hash(prompt.full_prompt + system_prompt),
        prompt_contract=prompt.prompt_contract,
        tool_policy=prompt.tool_policy,
        evaluation_role=role,
        case_bundle_sha256=str(bundle["case_bundle_sha256"]),
        case_bundle_case_count=int(bundle["case_bundle_case_count"]),
        case_split=str(bundle["case_split"]),
        cost_usd=meta.get("cost_usd"),
    )
    run_report = RunReport(
        manifest=manifest,
        parse_status=str(parsed["parse_status"]),
        final_answer=report_text,
        extracted_decision=parsed["decision"],
        extracted_justification=parsed["justification"],
        failure_class=parsed["failure_class"],
        source_report=meta.get("report"),
    )
    meta["lab_report"] = str(write_run_report(HERE, run_report).relative_to(HERE))

    return meta


def main() -> None:
    ap = argparse.ArgumentParser(
        description="OPERANT Axis 3 runner — dispatches orchestration-plan eval cases"
    )
    ap.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="Model to test (default: claude-sonnet-4-6)",
    )
    ap.add_argument(
        "--label",
        default=None,
        help="Run label for report filenames and index rows (default: model name)",
    )
    ap.add_argument(
        "--cases",
        nargs="+",
        default=None,
        help="Space-separated case ids to run (default: ALL cases)",
    )
    ap.add_argument(
        "--concurrency",
        type=int,
        default=3,
        help="Max parallel dispatches (default: 3)",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the exact claude -p command per case instead of executing",
    )
    ap.add_argument(
        "--evaluation-role",
        choices=sorted(VALID_EVALUATION_ROLES),
        help="Non-confirmatory role recorded in every run receipt.",
    )
    ap.add_argument(
        "--case-split",
        default="canonical",
        help="Stable split label included in the case-bundle digest.",
    )
    args = ap.parse_args()

    label = args.label or args.model

    all_cases = _import_score_orchestration().load_cases()

    if args.cases:
        missing = [c for c in args.cases if c not in all_cases]
        if missing:
            sys.exit(f"unknown case ids: {missing}. known: {sorted(all_cases)}")
        selected = [all_cases[c] for c in args.cases]
    else:
        selected = list(all_cases.values())

    operator_contract = load_operator_contract()
    system_prompt = build_system_prompt(operator_contract)
    role = resolve_evaluation_role(args.evaluation_role, run_label=label)
    bundle = case_bundle_binding(selected, case_split=args.case_split)

    if args.dry_run:
        print(
            f"DRY RUN — model={args.model}  label={label}  "
            f"role={role}  bundle={bundle['case_bundle_sha256']}  "
            f"cases={[c['id'] for c in selected]}\n"
        )
        for case in selected:
            run_case(
                case,
                args.model,
                label,
                system_prompt,
                dry_run=True,
                evaluation_role=role,
                case_bundle=bundle,
            )
        return

    REPORTS.mkdir(parents=True, exist_ok=True)
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)

    print(
        f"OPERANT Axis 3 run: model={args.model}  label={label}  "
        f"n={len(selected)}  concurrency={args.concurrency}"
    )

    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [
            pool.submit(
                run_case,
                c,
                args.model,
                label,
                system_prompt,
                False,
                evaluation_role=role,
                case_bundle=bundle,
            )
            for c in selected
        ]
        with RUNS_LOG.open("a", encoding="utf-8") as fh:
            for fut in cf.as_completed(futures):
                meta = fut.result()
                fh.write(json.dumps(meta) + "\n")
                fh.flush()
                status = meta.get("model_usage") or meta.get("error", "no-result")
                print(
                    f"  [{meta['case_id']}] {meta.get('duration_s', '?')}s "
                    f"exit={meta.get('exit_code', '?')} -> {status}",
                    flush=True,
                )

    print(
        f"\nDone. Runs logged to {RUNS_LOG.relative_to(HERE.parent)}\n"
        "Score a single case:  python3 operant/score_orchestration.py "
        f"<case_id> <report> --record {label}\n"
        f"Aggregate:            python3 operant/score_orchestration.py --aggregate {label}"
    )


if __name__ == "__main__":
    main()
