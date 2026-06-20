#!/usr/bin/env python3
"""Prepare and record OPERANT runs for Codex App subjects.

The Codex App is a native agent shell, not a subprocess runner this repository
can safely launch on its own. This command therefore has two explicit modes:

  * prepare: print or write exact per-case prompts/manifests for operator-approved
    Codex App thread creation.
  * record: store a completed Codex App thread's final answer into the standard
    report and lab-artifact layout.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from operant_lab.artifacts import (
    RunManifest,
    RunReport,
    parse_decision_block,
    parse_orchestration_plan,
    stable_hash,
    write_json,
    write_run_report,
)
from operant_lab.subjects import CodexAppAdapter

HERE = Path(__file__).resolve().parent
QUEUE_DIR = HERE / "lab" / "codex-app-queue"
REPORTS = HERE / "results" / "reports"
ADAPTER = CodexAppAdapter()


def _load_module(name: str):
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _decision_prompt():
    runner = _load_module("run_operant")
    return runner.build_system_prompt(runner.load_operator_contract())


def _orchestration_prompt():
    runner = _load_module("run_orchestration")
    return runner.build_system_prompt(runner.load_operator_contract())


def _load_cases(axis: str) -> dict:
    if axis == "decision":
        return _load_module("score_operant").load_cases()
    return _load_module("score_orchestration").load_cases()


def _system_prompt(axis: str) -> str:
    return _decision_prompt() if axis == "decision" else _orchestration_prompt()


def prepare(args: argparse.Namespace) -> None:
    cases = _load_cases(args.axis)
    selected = args.cases or list(cases)[: args.limit]
    missing = [case_id for case_id in selected if case_id not in cases]
    if missing:
        sys.exit(f"unknown case ids: {missing}")

    system_prompt = _system_prompt(args.axis)
    for case_id in selected:
        case = cases[case_id]
        record = ADAPTER.queue_record(
            case=case,
            model=args.model,
            thinking=args.thinking,
            label=args.label,
            system_prompt=system_prompt,
            axis=args.axis,
            project_folder=HERE,
        )
        manifest = RunManifest(
            run_label=args.label,
            case_id=case_id,
            axis=args.axis,
            subject_shell=ADAPTER.shell,
            model_id=args.model,
            thinking=args.thinking,
            prompt_hash=stable_hash(record["prompt"]),
            prompt_contract="codex_app_prompt_embeds_operator_contract",
            tool_policy=ADAPTER.tool_policy,
            repeat_id=args.repeat,
        )
        payload = {"manifest": manifest.__dict__, **record}
        if args.write_queue:
            out = QUEUE_DIR / args.label / f"{case_id}.json"
            write_json(out, payload)
            print(f"queued {case_id}: {out.relative_to(HERE)}")
        else:
            print(json.dumps(payload, indent=2, sort_keys=True))


def record(args: argparse.Namespace) -> None:
    answer = args.answer_file.read_text(encoding="utf-8")
    system_prompt = _system_prompt(args.axis)
    cases = _load_cases(args.axis)
    if args.case_id not in cases:
        sys.exit(f"unknown case id: {args.case_id}")
    case = cases[args.case_id]
    prompt = ADAPTER.build_prompt(case, system_prompt, args.axis)
    parsed = (
        parse_decision_block(answer)
        if args.axis == "decision"
        else parse_orchestration_plan(answer)
    )
    prefix = "operant" if args.axis == "decision" else "orchestration"
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / f"{prefix}__{args.label}__{args.case_id}.txt"
    report_path.write_text(answer, encoding="utf-8")

    manifest = RunManifest(
        run_label=args.label,
        case_id=args.case_id,
        axis=args.axis,
        subject_shell=ADAPTER.shell,
        model_id=args.model,
        thinking=args.thinking,
        prompt_hash=stable_hash(prompt.full_prompt),
        prompt_contract=prompt.prompt_contract,
        tool_policy=prompt.tool_policy,
        repeat_id=args.repeat,
        source_thread_id=args.thread_id,
    )
    lab_path = write_run_report(
        HERE,
        RunReport(
            manifest=manifest,
            parse_status=str(parsed["parse_status"]),
            final_answer=answer,
            extracted_decision=parsed["decision"],
            extracted_justification=parsed["justification"],
            failure_class=parsed["failure_class"],
            source_report=str(report_path.relative_to(HERE.parent)),
        ),
    )
    print(f"recorded {args.case_id}: {lab_path.relative_to(HERE)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Codex App OPERANT prep/record helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    prep = sub.add_parser("prepare", help="print or queue Codex App prompts")
    prep.add_argument("--axis", choices=["decision", "orchestration"], default="decision")
    prep.add_argument("--model", default="gpt-5.5")
    prep.add_argument("--thinking", default="medium")
    prep.add_argument("--label", default="codex-gpt55-pilot")
    prep.add_argument("--repeat", type=int, default=1)
    prep.add_argument("--limit", type=int, default=5)
    prep.add_argument("--cases", nargs="*")
    prep.add_argument("--write-queue", action="store_true")
    prep.set_defaults(func=prepare)

    rec = sub.add_parser("record", help="record a completed Codex App answer")
    rec.add_argument("--axis", choices=["decision", "orchestration"], default="decision")
    rec.add_argument("--model", default="gpt-5.5")
    rec.add_argument("--thinking", default="medium")
    rec.add_argument("--label", required=True)
    rec.add_argument("--case-id", required=True)
    rec.add_argument("--thread-id", required=True)
    rec.add_argument("--answer-file", type=Path, required=True)
    rec.add_argument("--repeat", type=int, default=1)
    rec.set_defaults(func=record)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

