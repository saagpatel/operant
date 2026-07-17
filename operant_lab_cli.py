#!/usr/bin/env python3
"""OPERANT public-lab utility commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from operant_lab.export import export_public_artifacts
from operant_lab.inventory import inventory_runs
from operant_lab.public_contract import validate_public_artifacts
from operant_lab.submissions import TEMPLATE, load_submission, validate_submission

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path("/Users/d/Projects/evals/agent_eval/operant/results")
DEFAULT_PUBLIC = HERE / "lab" / "public"
DEFAULT_LAB_RUNS = HERE / "lab" / "runs"
DEFAULT_CODEX_APP_QUEUE = HERE / "lab" / "codex-app-queue"


def export_public(args: argparse.Namespace) -> None:
    summary = export_public_artifacts(
        args.source_results,
        args.out,
        lab_runs_dir=args.lab_runs if args.include_lab_runs else None,
        lab_labels=set(args.lab_labels) if args.lab_labels else None,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


def submission_template(args: argparse.Namespace) -> None:
    text = json.dumps(TEMPLATE, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text, end="")


def validate_case(args: argparse.Namespace) -> None:
    data = load_submission(args.path)
    errors = validate_submission(data)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: {args.path}")


def inventory_lab_runs(args: argparse.Namespace) -> None:
    rows = inventory_runs(
        queue_dir=args.queue_dir,
        runs_dir=args.runs_dir,
        labels=set(args.labels) if args.labels else None,
    )
    print(json.dumps(rows, indent=2, sort_keys=True))


def check_public_artifacts(args: argparse.Namespace) -> None:
    errors = validate_public_artifacts(
        args.public_dir,
        source_results=args.source_results,
        lab_runs_dir=args.lab_runs,
        private_case_overlays_dir=args.private_case_overlays,
    )
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"OK: {args.public_dir}")


def main() -> None:
    ap = argparse.ArgumentParser(description="OPERANT public-lab utilities")
    sub = ap.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export-public", help="export static public lab artifacts")
    exp.add_argument("--source-results", type=Path, default=DEFAULT_SOURCE)
    exp.add_argument("--out", type=Path, default=DEFAULT_PUBLIC)
    exp.add_argument("--include-lab-runs", action="store_true")
    exp.add_argument("--lab-runs", type=Path, default=DEFAULT_LAB_RUNS)
    exp.add_argument("--lab-labels", nargs="*")
    exp.set_defaults(func=export_public)

    tmpl = sub.add_parser("submission-template", help="print or write a case template")
    tmpl.add_argument("--out", type=Path)
    tmpl.set_defaults(func=submission_template)

    val = sub.add_parser("validate-submission", help="validate a submitted case JSON")
    val.add_argument("path", type=Path)
    val.set_defaults(func=validate_case)

    inv = sub.add_parser(
        "inventory-runs",
        help="print sanitized queue/run inventory without prompt text",
    )
    inv.add_argument("--queue-dir", type=Path, default=DEFAULT_CODEX_APP_QUEUE)
    inv.add_argument("--runs-dir", type=Path, default=DEFAULT_LAB_RUNS)
    inv.add_argument("--labels", nargs="*")
    inv.set_defaults(func=inventory_lab_runs)

    check_public = sub.add_parser(
        "check-public-artifacts",
        help="validate sanitized public artifact export contract",
    )
    check_public.add_argument("--public-dir", type=Path, default=DEFAULT_PUBLIC)
    check_public.add_argument(
        "--source-results",
        type=Path,
        help=(
            "optionally recompute private source-index digests without exposing "
            "paths or row contents"
        ),
    )
    check_public.add_argument(
        "--lab-runs",
        type=Path,
        help="optionally reconnect published receipt hashes to local run bytes",
    )
    check_public.add_argument(
        "--private-case-overlays",
        type=Path,
        help=(
            "optionally reconnect private case/oracle hashes without exposing "
            "paths or case contents"
        ),
    )
    check_public.set_defaults(func=check_public_artifacts)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
