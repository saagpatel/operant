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
import hashlib
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

from operant_lab.artifacts import (
    VALID_EVALUATION_ROLES,
    RunManifest,
    RunReport,
    build_execution_binding,
    case_bundle_binding,
    complete_execution_binding,
    ensure_exclusive_path_slot,
    ensure_run_receipt_slot,
    execution_input_mismatches,
    parse_decision_block,
    parse_orchestration_plan,
    resolve_evaluation_role,
    stable_hash,
    validate_execution_binding,
    write_json_exclusive,
    write_run_report,
    write_text_exclusive,
)
from operant_lab.subjects import CodexAppAdapter

HERE = Path(__file__).resolve().parent
QUEUE_DIR = HERE / "lab" / "codex-app-queue"
REPORTS = HERE / "results" / "reports"
ADAPTER = CodexAppAdapter()
HARNESS_FILES = [
    HERE / "run_codex_app.py",
    HERE / "score_operant.py",
    HERE / "score_orchestration.py",
    HERE / "operant_lab" / "artifacts.py",
    HERE / "operant_lab" / "subjects.py",
]


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
    role = resolve_evaluation_role(args.evaluation_role, run_label=args.label)
    bundle = case_bundle_binding(
        [cases[case_id] for case_id in selected],
        case_split=args.case_split,
    )
    for case_id in selected:
        case = cases[case_id]
        prepared_projection = {
            "run_label": args.label,
            "case_id": case_id,
            "axis": args.axis,
            "evaluation_role": role,
            "case_bundle_sha256": str(bundle["case_bundle_sha256"]),
            "case_bundle_case_count": int(bundle["case_bundle_case_count"]),
            "case_split": str(bundle["case_split"]),
            "repeat_id": args.repeat,
            "thinking": args.thinking,
            "thread_container": args.thread_container or "UNKNOWN",
        }
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
            evaluation_role=role,
            case_bundle_sha256=str(bundle["case_bundle_sha256"]),
            case_bundle_case_count=int(bundle["case_bundle_case_count"]),
            case_split=str(bundle["case_split"]),
            execution_binding=build_execution_binding(
                root=HERE,
                exact_prompt=record["prompt"],
                system_prompt=system_prompt,
                stdin_text=None,
                command=None,
                cwd_class="CODEX_APP_PROJECT_FOLDER",
                tool_policy=ADAPTER.tool_policy,
                timeout_seconds=None,
                output_mode="manual-codex-app-final-answer",
                dispatch_settings={"prepared_manifest": prepared_projection},
                harness_files=HARNESS_FILES,
                requested_model_id=args.model,
            ),
            repeat_id=args.repeat,
            thread_container=args.thread_container,
        )
        payload = {"manifest": asdict(manifest), **record}
        if args.write_queue:
            out = QUEUE_DIR / args.label / f"{case_id}.json"
            write_json_exclusive(out, payload)
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
    if args.queue_file is None:
        sys.exit("record requires the exact v8 --queue-file prepared before dispatch")
    source_queue_bytes = args.queue_file.read_bytes()
    queue_payload = json.loads(source_queue_bytes)
    queue_manifest = (queue_payload or {}).get("manifest", {})
    try:
        queue_locator = (
            args.queue_file.resolve().relative_to(HERE.resolve()).as_posix()
        )
    except ValueError:
        sys.exit("--queue-file must stay inside the repository")
    source_queue_sha256 = hashlib.sha256(source_queue_bytes).hexdigest()
    queue_prompt = (queue_payload or {}).get("prompt")
    if queue_payload:
        if queue_payload.get("case_id") != args.case_id:
            sys.exit(
                f"queue case mismatch: {queue_payload.get('case_id')} != {args.case_id}"
            )
        if queue_payload.get("axis") != args.axis:
            sys.exit(f"queue axis mismatch: {queue_payload.get('axis')} != {args.axis}")
        if queue_manifest.get("model_id") != args.model:
            sys.exit(
                "queue requested model mismatch: "
                f"{queue_manifest.get('model_id')} != {args.model}"
            )
        if queue_manifest.get("run_label") != args.label:
            sys.exit(
                "queue run label mismatch: "
                f"{queue_manifest.get('run_label')} != {args.label}"
            )
        if queue_manifest.get("thinking") != args.thinking:
            sys.exit(
                "queue thinking mismatch: "
                f"{queue_manifest.get('thinking')} != {args.thinking}"
            )
        if (
            args.thread_container
            and queue_manifest.get("thread_container") != args.thread_container
        ):
            sys.exit("queue thread container does not match --thread-container")
        if queue_prompt != prompt.full_prompt:
            sys.exit("queue prompt no longer matches the adapter-built prompt")
        if queue_manifest.get("prompt_hash") != stable_hash(prompt.full_prompt):
            sys.exit("queue prompt_hash does not match the bound prompt")
        if queue_manifest.get("prompt_contract") != prompt.prompt_contract:
            sys.exit("queue prompt_contract does not match the adapter contract")
        if queue_manifest.get("tool_policy") != prompt.tool_policy:
            sys.exit("queue tool_policy does not match the adapter policy")
    queue_role = queue_manifest.get("evaluation_role")
    if args.evaluation_role and queue_role and args.evaluation_role != queue_role:
        sys.exit("queue evaluation role does not match --evaluation-role")
    role = resolve_evaluation_role(
        str(queue_role) if queue_role else args.evaluation_role,
        run_label=args.label,
    )
    if queue_manifest.get("case_bundle_sha256"):
        bundle = {
            "case_bundle_sha256": queue_manifest["case_bundle_sha256"],
            "case_bundle_case_count": queue_manifest.get("case_bundle_case_count"),
            "case_split": queue_manifest.get("case_split"),
        }
    else:
        bundle = case_bundle_binding(
            [case],
            case_split=args.case_split,
        )
    parsed = (
        parse_decision_block(answer)
        if args.axis == "decision"
        else parse_orchestration_plan(answer)
    )
    prepared_binding = queue_manifest.get("execution_binding")
    if queue_manifest.get("manifest_schema") != "operant-run-manifest.v8":
        sys.exit("record requires a v8 queue manifest; historical runs are not backfilled")
    if not isinstance(prepared_binding, dict):
        sys.exit("v8 queue manifest is missing execution_binding")
    binding_errors = validate_execution_binding(prepared_binding)
    if binding_errors:
        sys.exit("invalid queued execution binding: " + "; ".join(binding_errors))
    effective_thread_container = queue_manifest.get("thread_container") or "UNKNOWN"
    prepared_projection = {
        "run_label": args.label,
        "case_id": args.case_id,
        "axis": args.axis,
        "evaluation_role": role,
        "case_bundle_sha256": str(bundle["case_bundle_sha256"]),
        "case_bundle_case_count": int(bundle["case_bundle_case_count"]),
        "case_split": str(bundle["case_split"]),
        "repeat_id": queue_manifest.get("repeat_id", args.repeat),
        "thinking": args.thinking,
        "thread_container": effective_thread_container,
    }
    mismatches = execution_input_mismatches(
        prepared_binding,
        exact_prompt=prompt.full_prompt,
        system_prompt=system_prompt,
        stdin_text=None,
        command=None,
        cwd_class="CODEX_APP_PROJECT_FOLDER",
        tool_policy=ADAPTER.tool_policy,
        timeout_seconds=None,
        output_mode="manual-codex-app-final-answer",
        dispatch_settings={"prepared_manifest": prepared_projection},
    )
    if mismatches:
        sys.exit(
            "queue execution binding no longer matches dispatch input: "
            + ", ".join(mismatches)
        )
    ensure_run_receipt_slot(HERE, args.label, args.case_id)
    prefix = "operant" if args.axis == "decision" else "orchestration"
    ensure_exclusive_path_slot(
        REPORTS / f"{prefix}__{args.label}__{args.case_id}.txt"
    )
    execution_binding = complete_execution_binding(
        prepared_binding,
        provider_reported_candidates=[],
        evidence_source="NOT_EXPOSED",
        raw_result_envelope=answer,
        final_answer=answer,
        runtime_root=HERE,
        runtime_command=None,
    )
    identity_status = execution_binding["model_observation"]["comparison_status"]
    identity_failure = (
        f"identity_blocked:{identity_status.lower()}"
        if identity_status in {"AMBIGUOUS", "MISMATCH"}
        else None
    )
    if identity_failure:
        parsed = {
            "parse_status": identity_failure,
            "decision": None,
            "justification": None,
            "failure_class": identity_failure,
        }
    runtime_failure = (
        "runtime_candidate_drift"
        if execution_binding["post_dispatch_runtime"]["comparison"] == "DRIFTED"
        else None
    )
    if runtime_failure and not identity_failure:
        parsed = {
            "parse_status": runtime_failure,
            "decision": None,
            "justification": None,
            "failure_class": runtime_failure,
        }
    report_path = (
        REPORTS / f"{prefix}__{args.label}__{args.case_id}.txt"
        if parsed["parse_status"] == "ok"
        else None
    )

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
        evaluation_role=role,
        case_bundle_sha256=str(bundle["case_bundle_sha256"]),
        case_bundle_case_count=int(bundle["case_bundle_case_count"]),
        case_split=str(bundle["case_split"]),
        execution_binding=execution_binding,
        repeat_id=queue_manifest.get("repeat_id", args.repeat),
        source_thread_id=args.thread_id,
        source_queue_file=queue_locator,
        source_queue_sha256=source_queue_sha256,
        thread_container=queue_manifest.get("thread_container"),
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
            source_report=(
                str(report_path.relative_to(HERE.parent)) if report_path else None
            ),
        ),
    )
    if report_path:
        write_text_exclusive(report_path, answer)
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
    prep.add_argument(
        "--evaluation-role",
        choices=sorted(VALID_EVALUATION_ROLES),
        help="Non-confirmatory role recorded in queued manifests.",
    )
    prep.add_argument(
        "--case-split",
        default="canonical",
        help="Stable split label included in the case-bundle digest.",
    )
    prep.add_argument(
        "--thread-container",
        default="projectless:operant-public-lab-runs",
        help="Human-readable Codex App thread grouping target.",
    )
    prep.set_defaults(func=prepare)

    rec = sub.add_parser("record", help="record a completed Codex App answer")
    rec.add_argument("--axis", choices=["decision", "orchestration"], default="decision")
    rec.add_argument("--model", default="gpt-5.5")
    rec.add_argument("--thinking", default="medium")
    rec.add_argument("--label", required=True)
    rec.add_argument("--case-id", required=True)
    rec.add_argument("--thread-id", required=True)
    rec.add_argument("--answer-file", type=Path, required=True)
    rec.add_argument("--queue-file", type=Path, required=True)
    rec.add_argument("--repeat", type=int, default=1)
    rec.add_argument("--thread-container")
    rec.add_argument(
        "--evaluation-role",
        choices=sorted(VALID_EVALUATION_ROLES),
        help="Must match the required prepared queue manifest.",
    )
    rec.add_argument(
        "--case-split",
        default="canonical",
        help="Compatibility argument; the required v8 queue supplies the bound split.",
    )
    rec.set_defaults(func=record)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
