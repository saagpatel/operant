"""Sit the hosted decision corpus using an agent running on this machine."""

from __future__ import annotations

import argparse
import json
import os
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


def validate_runner_url(
    runner_url: str, *, relay: bool, allow_insecure_runner: bool = False
) -> None:
    url = urlsplit(runner_url)
    if url.scheme not in ("http", "https") or not url.hostname:
        raise ValueError("runner URL must use http or https and include a host")
    if url.scheme == "http" and url.hostname not in ("127.0.0.1", "::1", "localhost"):
        if relay:
            raise ValueError("host relay requires an https runner URL (except literal loopback)")
        if not allow_insecure_runner:
            raise ValueError("remote http runner requires --allow-insecure-runner")


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
    runner_url: str,
    label: str,
    claimed_model: str,
    agent: AgentRunner | None = None,
    *,
    relay: dict[str, str] | None = None,
    timeout: float = 1300,
    allow_insecure_runner: bool = False,
) -> dict:
    """Run the same create/next/answer/finish flow used by the CLI.

    For unbound runs, only agent.respond executes the submitter's adapter,
    command or endpoint locally. Relay runs send only provider/model/key to the
    host; the runner builds and sends every case request itself.
    """
    validate_runner_url(
        runner_url, relay=relay is not None, allow_insecure_runner=allow_insecure_runner
    )
    if (agent is None) == (relay is None):
        raise ValueError("select exactly one of local agent or host relay")
    created = request_json(
        runner_url,
        "POST",
        "/api/sittings",
        timeout=timeout,
        body={
            "agent_label": label,
            "claimed_model": claimed_model,
            "consent_publish": True,
            **({"relay": relay} if relay is not None else {}),
        },
    )
    sitting_id = created["sitting_id"]
    if not isinstance(sitting_id, str) or not SITTING_ID.fullmatch(sitting_id):
        raise RuntimeError("runner returned an invalid sitting id")
    path = f"/api/sittings/{sitting_id}"
    token = created["token"]
    if relay is not None:
        finished = request_json(
            runner_url,
            "POST",
            path + "/run",
            token=token,
            body={},
            timeout=timeout,
        )
        if (
            finished.get("sitting_id") != sitting_id
            or finished.get("record", {}).get("sitting_id") != sitting_id
        ):
            raise RuntimeError("runner returned a different sitting id")
        return finished
    while True:
        case = request_json(runner_url, "GET", path + "/next", token=token, timeout=timeout)
        if case["done"]:
            break
        assert agent is not None
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
    parser.add_argument(
        "--allow-insecure-runner", action="store_true",
        help="allow remote HTTP for unbound runs, exposing bearer tokens; never allows relay keys",
    )
    parser.add_argument("--label", required=True)
    parser.add_argument(
        "--claimed-model", required=True, help="display claim; does not establish identity"
    )
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--cmd", help="local command template containing {prompt}")
    sources.add_argument("--adapter", help="local module:func or path.py:func")
    sources.add_argument("--endpoint", help="agent endpoint called from this machine")
    parser.add_argument("--relay-provider", help="host-configured provider name")
    parser.add_argument("--relay-model", help="model name sent in host-built requests")
    parser.add_argument(
        "--relay-key-env", help="environment variable holding the provider API key"
    )
    parser.add_argument("--cmd-stdin", action="store_true")
    parser.add_argument("--http-method", default="POST")
    parser.add_argument("--http-header", action="append", default=[], metavar="Name: value")
    parser.add_argument("--http-body", default='{"prompt": "{prompt}"}')
    parser.add_argument("--answer-path")
    parser.add_argument(
        "--timeout", type=positive_int, default=900, help="per-agent timeout seconds"
    )
    parser.add_argument(
        "--runner-timeout", type=positive_int, default=1300,
        help="runner request timeout seconds (covers all host-relayed cases in /run)",
    )
    parser.add_argument(
        "--out", type=Path, help="directory for returned record and optional receipt"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    agent_selected = any((args.cmd, args.adapter, args.endpoint))
    relay_selected = any((args.relay_provider, args.relay_model, args.relay_key_env))
    if agent_selected == relay_selected:
        parser.error("choose a local agent source or all three --relay-* options")
    if relay_selected and not all((args.relay_provider, args.relay_model, args.relay_key_env)):
        parser.error("host relay requires --relay-provider, --relay-model, and --relay-key-env")
    if agent_selected and not any((args.cmd, args.adapter, args.endpoint)):
        parser.error("a local agent source is required")
    if args.cmd_stdin and not args.cmd:
        parser.error("--cmd-stdin requires --cmd")
    try:
        validate_runner_url(
            args.runner, relay=relay_selected, allow_insecure_runner=args.allow_insecure_runner
        )
    except ValueError as exc:
        parser.error(str(exc))
    headers = {}
    for header in args.http_header:
        name, separator, value = header.partition(":")
        if not separator or not name.strip():
            parser.error("--http-header must be Name: value")
        headers[name.strip()] = value.strip()
    try:
        relay = None
        agent = None
        if relay_selected:
            api_key = os.environ.get(args.relay_key_env)
            if not api_key:
                parser.error(f"environment variable {args.relay_key_env} is unset or empty")
            relay = {
                "provider": args.relay_provider,
                "model": args.relay_model,
                "api_key": api_key,
            }
        else:
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
            args.runner,
            args.label,
            args.claimed_model,
            agent,
            relay=relay,
            timeout=args.runner_timeout,
            allow_insecure_runner=args.allow_insecure_runner,
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
