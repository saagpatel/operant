"""Run the complete local sitting, sealing, review, and verification flow."""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FAKE_PROVIDER = r'''
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args): pass
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        body = json.dumps({"id":"test-provider-response","model":request["model"],
            "choices":[{"message":{"role":"assistant","content":"DECISION: REFUSE"}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("x-request-id", "local-test")
        self.end_headers()
        self.wfile.write(body)

server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
print(server.server_port, flush=True)
server.serve_forever()
'''


def run(command: list[str], *, env: dict[str, str], cwd: Path = REPO) -> subprocess.CompletedProcess:
    result = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True, timeout=180)
    if result.returncode:
        details = result.stderr[-2000:] or result.stdout[-2000:]
        raise RuntimeError(f"command failed ({result.returncode}): {command[2:5]}\n{details}")
    return result


def wait_for_line(process: subprocess.Popen, timeout: float = 15) -> str:
    deadline = time.monotonic() + timeout
    if process.stdout is None:
        raise RuntimeError("local service output is unavailable")
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not selector.select(remaining):
                break
            line = process.stdout.readline()
            if line:
                return line.strip()
    raise RuntimeError("local service did not become ready")


def stop(process: subprocess.Popen | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def e2e(workdir: Path) -> dict:
    workdir.mkdir(parents=True, exist_ok=True)
    data = workdir / "runner-data"
    output = workdir / "results"
    web_data = workdir / "web-data"
    private_key = workdir / "operator-key.pem"
    public_key = workdir / "operator-public.pem"
    pubkeys = workdir / "sittings-pubkey.json"
    env = os.environ.copy()
    env.pop("OPERANT_CASES", None)
    env.pop("OPERANT_OPERATOR_CONTRACT", None)
    python = [sys.executable, "-m"]
    run(python + ["sittings.receipt", "keygen", "--private", str(private_key),
                  "--public", str(public_key), "--pubkeys", str(pubkeys)], env=env)

    provider = subprocess.Popen(
        [sys.executable, "-c", FAKE_PROVIDER], cwd=REPO, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    runner = None
    try:
        provider_port = int(wait_for_line(provider))
        print(f"Local test provider listening on 127.0.0.1:{provider_port}")
        runner = subprocess.Popen(
            python + ["sittings.runner", "--host", "127.0.0.1", "--port", "0",
                      "--data-dir", str(data), "--key", str(private_key),
                      "--relay-provider", f"local-test=http://127.0.0.1:{provider_port}/v1"],
            cwd=REPO, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        ready = wait_for_line(runner)
        marker = "listening on 127.0.0.1:"
        if marker not in ready:
            raise RuntimeError("runner did not report its listening address")
        runner_port = int(ready.rsplit(":", 1)[1])
        runner_url = f"http://127.0.0.1:{runner_port}"

        unbound = run(
            python + ["sittings.client", "--runner", runner_url, "--label", "heuristic-demo",
                      "--claimed-model", "local-heuristic", "--adapter",
                      "examples/heuristic_agent.py:respond", "--out", str(output)],
            env=env,
        )
        print("Unbound sitting completed through the separate heuristic client process.")

        relay_env = env | {"OPERANT_E2E_TEST_KEY": "local-test-key-not-for-production"}
        relayed = run(
            python + ["sittings.client", "--runner", runner_url, "--label", "test-provider-demo",
                      "--claimed-model", "local-test-model", "--relay-provider", "local-test",
                      "--relay-model", "local-test-model", "--relay-key-env",
                      "OPERANT_E2E_TEST_KEY", "--out", str(output)],
            env=relay_env,
        )
        records = sorted(output.glob("*.record.json"))
        if len(records) != 2:
            raise RuntimeError("expected records from both sitting modes")

        for path in records:
            record = json.loads(path.read_text(encoding="utf-8"))
            sid = record["sitting_id"]
            run(python + ["sittings.review", "approve", sid, "--data-dir", str(data),
                          "--web-data", str(web_data), "--pubkey", str(pubkeys),
                          "--blob-source", str(REPO)], env=env)
            receipt = output / f"{sid}.receipt.json"
            verified = run(python + ["sittings.receipt", "verify", "--receipt", str(receipt),
                                      "--record", str(path), "--pubkeys", str(pubkeys),
                                      "--blob-root", str(web_data / "blobs"),
                                      "--repo-root", str(REPO)], env=env)
            if "FAIL" in verified.stdout:
                raise RuntimeError(f"valid receipt failed verification for {sid}")

        tamper_source = output / f"{json.loads(records[0].read_text())[ 'sitting_id']}.receipt.json"
        tampered = workdir / "tampered.receipt.json"
        receipt_doc = json.loads(tamper_source.read_text(encoding="utf-8"))
        receipt_doc["keyid"] = "ed25519:" + "0" * 16
        tampered.write_text(json.dumps(receipt_doc), encoding="utf-8")
        record_path = next(p for p in records if p.stem.startswith(tamper_source.name.split(".")[0]))
        invalid = subprocess.run(
            python + ["sittings.receipt", "verify", "--receipt", str(tampered),
                      "--record", str(record_path), "--pubkeys", str(pubkeys),
                      "--blob-root", str(web_data / "blobs"), "--repo-root", str(REPO)],
            cwd=REPO, env=env, text=True, capture_output=True, timeout=60,
        )
        if invalid.returncode == 0:
            raise RuntimeError("tampered receipt unexpectedly verified")
        rows = json.loads((web_data / "rows.json").read_text(encoding="utf-8"))["rows"]
        return {
            "sittings": len(records), "published_rows": len(rows),
            "verified_receipts": len(records), "tampered_receipt_rejected": True,
            "test_provider": "local OpenAI-compatible fake provider",
            "client_output": unbound.stdout.strip().splitlines()[-1:],
            "relay_output": relayed.stdout.strip().splitlines()[-1:],
        }
    finally:
        stop(runner)
        stop(provider)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = e2e(args.workdir.resolve())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Local e2e failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
