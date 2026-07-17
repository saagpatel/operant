#!/usr/bin/env python3
"""Run queued OPERANT prompts through local Codex CLI exec.

This is intentionally separate from run_codex_app.py. Codex App lab profiles
represent native App subject threads; this runner is for local, ephemeral CLI
dispatch when an operator wants no new App threads.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from operant_lab.artifacts import (
    VALID_EVALUATION_ROLES,
    RunManifest,
    RunReport,
    case_bundle_binding,
    parse_decision_block,
    resolve_evaluation_role,
    stable_hash,
    write_run_report,
)
from operant_lab.inventory import inventory_runs
from operant_lab.subjects import CodexAppAdapter

HERE = Path(__file__).resolve().parent
QUEUE_DIR = HERE / "lab" / "codex-app-queue"
REPORTS = HERE / "results" / "reports"
ANSWERS = HERE / "results" / "codex-cli-answers"
RUNS_LOG = HERE / "results" / "codex_cli_runs.jsonl"
TOOL_POLICY = (
    "Codex CLI exec --ephemeral --ignore-rules --sandbox read-only "
    "-c approval_policy=never; prompt prohibits tools and task execution"
)
ADAPTER = CodexAppAdapter()


def _load_score_operant():
    spec = importlib.util.spec_from_file_location(
        "score_operant", HERE / "score_operant.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _canonical_queue_prompt(case: dict[str, Any], axis: str) -> str:
    if axis == "decision":
        run_spec = importlib.util.spec_from_file_location(
            "run_operant", HERE / "run_operant.py"
        )
    elif axis == "orchestration":
        run_spec = importlib.util.spec_from_file_location(
            "run_orchestration", HERE / "run_orchestration.py"
        )
    else:
        raise ValueError(f"unsupported queue axis: {axis}")
    run_module = importlib.util.module_from_spec(run_spec)  # type: ignore[arg-type]
    run_spec.loader.exec_module(run_module)  # type: ignore[union-attr]
    system_prompt = run_module.build_system_prompt(
        run_module.load_operator_contract()
    )
    return ADAPTER.build_prompt(case, system_prompt, axis).full_prompt


def _load_queue_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _queue_files_for_label(label: str) -> list[Path]:
    return sorted((QUEUE_DIR / label).glob("*.json"))


def queued_case_ids(source_label: str, cases: list[str] | None) -> list[str]:
    if cases:
        return cases
    rows = inventory_runs(
        queue_dir=QUEUE_DIR,
        runs_dir=HERE / "lab" / "runs",
        labels={source_label},
    )
    return [
        str(row["case_id"]) for row in rows if row.get("score_outcome") == "queued"
    ]


def _safe_case_path(case_id: str) -> str:
    return case_id.replace("/", "_")


def _answer_path(label: str, case_id: str, attempt_id: str) -> Path:
    return ANSWERS / label / f"{_safe_case_path(case_id)}__{attempt_id}.txt"


def _report_path(label: str, case_id: str) -> Path:
    return REPORTS / f"operant__{label}__{case_id}.txt"


def codex_command(args: argparse.Namespace, answer_path: Path) -> list[str]:
    return [
        "codex",
        "exec",
        "--model",
        args.model,
        "--sandbox",
        "read-only",
        "-c",
        "approval_policy=\"never\"",
        "--cd",
        str(HERE),
        "--ephemeral",
        "--ignore-rules",
        "--output-last-message",
        str(answer_path),
        "-",
    ]


def run_queue_file(
    path: Path, args: argparse.Namespace, cases: dict[str, Any]
) -> dict[str, Any]:
    payload = _load_queue_payload(path)
    case_id = str(payload.get("case_id") or "")
    if not case_id:
        raise ValueError(f"missing case_id in {path}")
    if case_id not in cases:
        raise ValueError(f"unknown case id in {path}: {case_id}")

    prompt = str(payload.get("prompt") or "")
    if not prompt:
        raise ValueError(f"missing prompt in {path}")
    queue_manifest = payload.get("manifest", {})
    recorded_prompt_hash = queue_manifest.get("prompt_hash")
    actual_prompt_hash = stable_hash(prompt)
    if not recorded_prompt_hash:
        raise ValueError(f"missing prompt_hash in {path}")
    if recorded_prompt_hash != actual_prompt_hash:
        raise ValueError(f"queue prompt hash mismatch in {path}")
    if queue_manifest.get("case_id") != case_id:
        raise ValueError(f"queue manifest case mismatch in {path}")
    outer_axis = str(payload.get("axis") or "")
    if queue_manifest.get("axis") != outer_axis:
        raise ValueError(f"queue manifest axis mismatch in {path}")
    if prompt != _canonical_queue_prompt(cases[case_id], outer_axis):
        raise ValueError(f"queue prompt no longer matches canonical case in {path}")

    attempt_id = uuid.uuid4().hex
    answer_path = _answer_path(args.label, case_id, attempt_id)
    answer_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = codex_command(args, answer_path)

    if args.dry_run:
        return {
            "case_id": case_id,
            "run_label": args.label,
            "source_queue_file": str(path.relative_to(HERE)),
            "prompt_hash": actual_prompt_hash,
            "evaluation_role": args.resolved_evaluation_role,
            "case_bundle_sha256": args.case_bundle["case_bundle_sha256"],
            "dry_run": True,
        }

    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=args.timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        failure_class = type(exc).__name__
        manifest = RunManifest(
            run_label=args.label,
            case_id=case_id,
            axis=outer_axis or "decision",
            subject_shell="codex-cli",
            model_id=args.model,
            thinking=args.thinking,
            prompt_hash=actual_prompt_hash,
            prompt_contract=str(
                queue_manifest.get("prompt_contract")
                or "codex_app_prompt_embeds_operator_contract"
            ),
            tool_policy=TOOL_POLICY,
            evaluation_role=args.resolved_evaluation_role,
            case_bundle_sha256=str(args.case_bundle["case_bundle_sha256"]),
            case_bundle_case_count=int(args.case_bundle["case_bundle_case_count"]),
            case_split=str(args.case_bundle["case_split"]),
            repeat_id=args.repeat,
            source_queue_file=str(path.relative_to(HERE)),
            thread_container="local:codex-cli-ephemeral",
        )
        lab_path = write_run_report(
            HERE,
            RunReport(
                manifest=manifest,
                parse_status="timeout"
                if isinstance(exc, subprocess.TimeoutExpired)
                else "launch_failure",
                final_answer="",
                failure_class=failure_class,
            ),
        )
        return {
            "case_id": case_id,
            "run_label": args.label,
            "source_queue_file": str(path.relative_to(HERE)),
            "prompt_hash": actual_prompt_hash,
            "exit_code": -1,
            "duration_s": round(time.time() - t0, 1),
            "parse_status": "timeout"
            if isinstance(exc, subprocess.TimeoutExpired)
            else "launch_failure",
            "score_outcome": "unscored",
            "error": failure_class,
            "lab_report": str(lab_path.relative_to(HERE)),
        }

    answer = answer_path.read_text(encoding="utf-8") if answer_path.exists() else ""
    if not answer.strip() and proc.stdout.strip():
        answer = proc.stdout.strip()
        answer_path.write_text(answer + "\n", encoding="utf-8")

    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = _report_path(args.label, case_id)
    report_path.write_text(answer, encoding="utf-8")

    parsed = parse_decision_block(answer)
    manifest = RunManifest(
        run_label=args.label,
        case_id=case_id,
        axis=str(payload.get("axis") or queue_manifest.get("axis") or "decision"),
        subject_shell="codex-cli",
        model_id=args.model,
        thinking=args.thinking,
        prompt_hash=actual_prompt_hash,
        prompt_contract=str(
            queue_manifest.get("prompt_contract")
            or "codex_app_prompt_embeds_operator_contract"
        ),
        tool_policy=TOOL_POLICY,
        evaluation_role=args.resolved_evaluation_role,
        case_bundle_sha256=str(args.case_bundle["case_bundle_sha256"]),
        case_bundle_case_count=int(args.case_bundle["case_bundle_case_count"]),
        case_split=str(args.case_bundle["case_split"]),
        repeat_id=args.repeat,
        source_queue_file=str(path.relative_to(HERE)),
        thread_container="local:codex-cli-ephemeral",
    )
    score_row = None
    if manifest.axis == "decision":
        score_row = _load_score_operant().score_one(cases[case_id], answer)

    lab_path = write_run_report(
        HERE,
        RunReport(
            manifest=manifest,
            parse_status=str(parsed["parse_status"]),
            final_answer=answer,
            extracted_decision=parsed["decision"],
            extracted_justification=parsed["justification"],
            failure_class=parsed["failure_class"],
            score_row=score_row,
            source_report=str(report_path.relative_to(HERE)),
        ),
    )
    meta = {
        "case_id": case_id,
        "run_label": args.label,
        "source_queue_file": str(path.relative_to(HERE)),
        "prompt_hash": manifest.prompt_hash,
        "exit_code": proc.returncode,
        "duration_s": round(time.time() - t0, 1),
        "parse_status": parsed["parse_status"],
        "score_outcome": (
            "correct"
            if score_row and score_row.get("decision_accuracy")
            else "incorrect"
            if score_row
            else "unscored"
        ),
        "lab_report": str(lab_path.relative_to(HERE)),
    }
    if not answer.strip():
        meta["error"] = "empty_result"
        if proc.stderr.strip():
            meta["stderr_tail"] = proc.stderr[-400:]
    return meta


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Run queued OPERANT prompts with local Codex CLI exec"
    )
    ap.add_argument("--source-label", required=True, help="Queue label to read")
    ap.add_argument("--label", required=True, help="Output lab run label")
    ap.add_argument("--model", default="gpt-5.5")
    ap.add_argument("--thinking", default="medium")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--cases", nargs="*")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--evaluation-role",
        choices=sorted(VALID_EVALUATION_ROLES),
        help="Non-confirmatory role for the executed queue subset.",
    )
    ap.add_argument(
        "--case-split",
        help="Stable split label; must match queued v2 manifests when present.",
    )
    args = ap.parse_args()

    wanted = set(queued_case_ids(args.source_label, args.cases))
    queue_files = [
        path
        for path in _queue_files_for_label(args.source_label)
        if _load_queue_payload(path).get("case_id") in wanted
    ]
    if not queue_files:
        sys.exit("no matching queued cases")

    cases = _load_score_operant().load_cases()
    queue_roles = {
        str(_load_queue_payload(path).get("manifest", {}).get("evaluation_role"))
        for path in queue_files
        if _load_queue_payload(path).get("manifest", {}).get("evaluation_role")
    }
    if len(queue_roles) > 1:
        sys.exit(f"queued evaluation roles disagree: {sorted(queue_roles)}")
    queue_role = next(iter(queue_roles), None)
    if args.evaluation_role and queue_role and args.evaluation_role != queue_role:
        sys.exit("queued evaluation role does not match --evaluation-role")
    args.resolved_evaluation_role = resolve_evaluation_role(
        args.evaluation_role or queue_role,
        run_label=args.label,
    )
    queue_splits = {
        str(_load_queue_payload(path).get("manifest", {}).get("case_split"))
        for path in queue_files
        if _load_queue_payload(path).get("manifest", {}).get("case_split")
    }
    if len(queue_splits) > 1:
        sys.exit(f"queued case splits disagree: {sorted(queue_splits)}")
    queue_split = next(iter(queue_splits), None)
    if args.case_split and queue_split and args.case_split != queue_split:
        sys.exit("queued case split does not match --case-split")
    resolved_split = args.case_split or queue_split or "canonical"
    args.case_bundle = case_bundle_binding(
        [cases[str(_load_queue_payload(path)["case_id"])] for path in queue_files],
        case_split=resolved_split,
    )
    RUNS_LOG.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"Codex CLI local run: source_label={args.source_label} "
        f"label={args.label} n={len(queue_files)} dry_run={args.dry_run}"
    )
    with RUNS_LOG.open("a", encoding="utf-8") as log:
        for path in queue_files:
            meta = run_queue_file(path, args, cases)
            log.write(json.dumps(meta, sort_keys=True) + "\n")
            log.flush()
            print(
                f"  {meta['case_id']} | {meta['source_queue_file']} | "
                f"{meta['prompt_hash']} | {meta.get('parse_status', 'dry-run')} | "
                f"{meta.get('score_outcome', 'queued')}"
            )


if __name__ == "__main__":
    main()
