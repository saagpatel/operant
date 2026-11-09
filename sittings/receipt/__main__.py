"""Command-line interface for local sitting receipts."""

import argparse
import json
from pathlib import Path

from .sign import export_pubkey_json, generate_key, load_signer, seal_record
from .verify import verify_receipt


def main():
    parser = argparse.ArgumentParser(
        description="Create and verify an operator-signed T1 receipt (CheckSeal DSSE + Ed25519)."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    keygen = sub.add_parser("keygen")
    keygen.add_argument("--private", required=True)
    keygen.add_argument("--public", required=True)
    keygen.add_argument("--pubkeys", required=True)
    seal = sub.add_parser("seal")
    seal.add_argument("--record", required=True)
    seal.add_argument("--private", required=True)
    seal.add_argument("--out", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--receipt", required=True)
    verify.add_argument("--record", required=True)
    verify.add_argument("--pubkeys", required=True)
    verify.add_argument("--blob-root")
    verify.add_argument("--repo-root")
    args = parser.parse_args()

    def read(path):
        return json.loads(Path(path).read_text(encoding="utf-8"))

    def write(path, value):
        Path(path).write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    if args.command == "keygen":
        signer = generate_key(args.private, args.public)
        write(args.pubkeys, export_pubkey_json(signer))
        print("OPERANT operator-signed T1 receipt (CheckSeal DSSE + Ed25519)")
        print(f"Key fingerprint: {signer.keyid}")
    elif args.command == "seal":
        write(args.out, seal_record(read(args.record), load_signer(args.private)))
    else:
        report = verify_receipt(
            read(args.receipt),
            pubkeys=read(args.pubkeys),
            record=read(args.record),
            blob_root=args.blob_root,
            repo_root=args.repo_root,
        )
        for finding in report.findings:
            print(f"{finding['field']}\t{'OK' if finding['ok'] else 'FAIL'}\t{finding['detail']}")
        return 0 if report.ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
