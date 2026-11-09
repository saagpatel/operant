"""Command-line interface for the sitting review queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sittings.review.queue import ReviewQueue


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m sittings.review")
    parser.add_argument("action", choices=("list", "show", "approve", "reject", "unpublish"))
    parser.add_argument("id", nargs="?")
    parser.add_argument("--state", default="pending", choices=("pending", "approved", "rejected"))
    parser.add_argument("--reason")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--web-data")
    parser.add_argument("--pubkey", action="append", default=[])
    parser.add_argument("--blob-source", default=".")
    args = parser.parse_args()
    if args.action in ("approve", "unpublish") and not args.web_data:
        parser.error(f"{args.action} requires --web-data")
    queue = ReviewQueue(args.data_dir)
    if args.action == "list":
        result = queue.list(args.state)
    else:
        if not args.id:
            parser.error("this action requires an id")
        if args.action == "show":
            result = queue.show(args.id)
        elif args.action == "approve":
            pubkeys = [json.loads(Path(path).read_text()) for path in args.pubkey]
            if len(pubkeys) == 1:
                pubkeys = pubkeys[0]
            result = queue.approve(args.id, web_data=args.web_data, pubkeys=pubkeys,
                                   blob_source=args.blob_source)
        elif args.action == "reject":
            if args.reason is None:
                parser.error("reject requires --reason")
            queue.reject(args.id, args.reason)
            result = {"state": "rejected", "id": args.id}
        else:
            queue.unpublish(args.id, web_data=args.web_data)
            result = {"unpublished": args.id}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
