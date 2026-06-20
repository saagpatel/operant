#!/usr/bin/env python3
"""OPERANT public-lab utility commands."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from operant_lab.export import export_public_artifacts
from operant_lab.submissions import TEMPLATE, load_submission, validate_submission

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCE = Path("/Users/d/Projects/evals/agent_eval/operant/results")
DEFAULT_PUBLIC = HERE / "lab" / "public"


def export_public(args: argparse.Namespace) -> None:
    summary = export_public_artifacts(args.source_results, args.out)
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


def main() -> None:
    ap = argparse.ArgumentParser(description="OPERANT public-lab utilities")
    sub = ap.add_subparsers(dest="cmd", required=True)

    exp = sub.add_parser("export-public", help="export static public lab artifacts")
    exp.add_argument("--source-results", type=Path, default=DEFAULT_SOURCE)
    exp.add_argument("--out", type=Path, default=DEFAULT_PUBLIC)
    exp.set_defaults(func=export_public)

    tmpl = sub.add_parser("submission-template", help="print or write a case template")
    tmpl.add_argument("--out", type=Path)
    tmpl.set_defaults(func=submission_template)

    val = sub.add_parser("validate-submission", help="validate a submitted case JSON")
    val.add_argument("path", type=Path)
    val.set_defaults(func=validate_case)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()

