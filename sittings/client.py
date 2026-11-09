"""Sit the hosted decision corpus using an agent running on this machine."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from operant_lab.agent_runners import AgentRunner, make_runner
from sittings.runner.store import SITTING_ID, atomic_json


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: str | None = None,
    body: dict | None = None,
    timeout: float = 30,
) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        # Do not echo a remote error body, URL, token or an agent's answer.
        raise RuntimeError(f"runner returned HTTP {exc.code}") from None
    except (urllib.error.URLError, OSError, ValueError):
        raise RuntimeError("runner request failed") from None


def run_sitting(
    runner_url: str, label: str, claimed_model: str, agent: AgentRunner, *, timeout: float = 30
) -> dict:
    """Run the same create/next/answer/finish flow used by the CLI.

    Only agent.respond executes the submitter's configured adapter/command/endpoint,
    locally. Those settings never cross the runner HTTP boundary.
    """
    if urlsplit(runner_url).scheme not in ("http", "https"):
        raise ValueError("runner URL must use http or https")
    created = request_json(
        runner_url,
        "POST",
        "/api/sittings",
        timeout=timeout,
        body={
            "agent_label": label,
            "claimed_model": claimed_model,
            "consent_publish": True,
        },
    )
    sitting_id = created["sitting_id"]
    if not isinstance(sitting_id, str) or not SITTING_ID.fullmatch(sitting_id):
        raise RuntimeError("runner returned an invalid sitting id")
    path = f"/api/sittings/{sitting_id}"
    token = created["token"]
    while True:
        case = request_json(runner_url, "GET", path + "/next", token=token, timeout=timeout)
        if case["done"]:
            break
        result = agent.respond(case["prompt"])
        if not result.ok or not isinstance(result.text, str) or not result.text.strip():
            raise RuntimeError("agent failed to produce an answer; sitting was not finished")
        request_json(
            runner_url,
            "POST",
            path + "/answers",
            token=token,
            timeout=timeout,
            body={"case_id": case["case_id"], "answer": result.text},
        )
    finished = request_json(
        runner_url, "POST", path + "/finish", token=token, body={}, timeout=timeout
    )
    if (
        finished.get("sitting_id") != sitting_id
        or finished["record"].get("sitting_id") != sitting_id
    ):
        raise RuntimeError("runner returned a different sitting id")
    return finished


def positive_int(value: str) -> int:
    result = int(value)
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", required=True, help="hosted runner base URL")
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--claimed-model", required=True, help="display claim; does not establish identity"
    )
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--cmd", help="local command template containing {prompt}")
    sources.add_argument("--adapter", help="local module:func or path.py:func")
    sources.add_argument("--endpoint", help="agent endpoint called from this machine")
    parser.add_argument("--cmd-stdin", action="store_true")
    parser.add_argument("--http-method", default="POST")
    parser.add_argument("--http-header", action="append", default=[], metavar="Name: value")
    parser.add_argument("--http-body", default='{"prompt": "{prompt}"}')
    parser.add_argument("--answer-path")
    parser.add_argument(
        "--timeout", type=positive_int, default=900, help="per-agent timeout seconds"
    )
    parser.add_argument("--runner-timeout", type=positive_int, default=30)
    parser.add_argument(
        "--out", type=Path, help="directory for returned record and optional receipt"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.cmd_stdin and not args.cmd:
        parser.error("--cmd-stdin requires --cmd")
    headers = {}
    for header in args.http_header:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            parser.error("--http-header must be Name: value")
        headers[name.strip()] = value.strip()
    try:
        agent = make_runner(
            cmd=args.cmd,
            cmd_stdin=args.cmd_stdin,
            adapter=args.adapter,
            endpoint=args.endpoint,
            http_method=args.http_method,
            http_headers=headers,
            http_body=args.http_body,
            answer_path=args.answer_path,
            timeout=args.timeout,
        )
        result = run_sitting(
            args.runner, args.label, args.claimed_model, agent, timeout=args.runner_timeout
        )
        sid = result["sitting_id"]
        print(json.dumps(result["record"]["score"], sort_keys=True))
        print(f"Record saved on runner: sittings/{sid}/record.json")
        if args.out:
            record_path = args.out / f"{sid}.record.json"
            atomic_json(record_path, result["record"])
            print(f"Record saved locally: {record_path}")
            if result["receipt"] is not None:
                receipt_path = args.out / f"{sid}.receipt.json"
                atomic_json(receipt_path, result["receipt"])
                print(f"Receipt saved locally: {receipt_path}")
        return 0
    except (ValueError, RuntimeError, OSError):
        # Agent/provider errors can contain secrets; retain only the failure category.
        print("Sitting failed; no score was accepted by this client.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
