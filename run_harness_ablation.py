#!/usr/bin/env python3
"""Preregister, run, and verify the deterministic OPERANT harness ablation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from operant_lab.harness_ablation import (
    build_preregistration,
    run_experiment,
    verify_receipt,
    write_preregistration,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    preregister = commands.add_parser(
        "preregister",
        help="write a result-blind preregistration and digest sidecar",
    )
    preregister.add_argument("--out", required=True, type=Path)
    preregister.add_argument("--timestamp", default=None)

    run = commands.add_parser(
        "run",
        help="run only from a clean commit containing the bound preregistration",
    )
    run.add_argument("--preregistration", required=True, type=Path)
    run.add_argument("--out-dir", required=True, type=Path)

    verify = commands.add_parser("verify", help="verify a prompt-free result receipt")
    verify.add_argument("--receipt", required=True, type=Path)

    args = parser.parse_args()
    if args.command == "preregister":
        preregistration = build_preregistration(args.timestamp)
        digest, digest_path = write_preregistration(preregistration, args.out)
        print(
            json.dumps(
                {
                    "status": "PREREGISTERED_NOT_EXECUTED",
                    "preregistration": str(args.out),
                    "sha256": digest,
                    "digest_sidecar": str(digest_path),
                },
                indent=2,
            )
        )
        return
    if args.command == "run":
        receipt_path = run_experiment(args.preregistration, args.out_dir)
        result = verify_receipt(receipt_path)
        result["receipt"] = str(receipt_path)
        print(json.dumps(result, indent=2))
        return
    result = verify_receipt(args.receipt)
    result["receipt"] = str(args.receipt)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
