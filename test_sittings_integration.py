"""Offline integration through the CLI's actual binding, seal and enqueue hooks."""
from __future__ import annotations

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from checkseal.sign.local import LocalKeySigner

from operant_lab.agent_runners import make_runner
from sittings import client, manifest
from sittings.receipt import export_pubkey_json, generate_key, verify_receipt
from sittings.review import ReviewQueue
from sittings.review.__main__ import main as review_main
from sittings.runner.__main__ import main as runner_main
from sittings.runner.server import RunnerServer
from test_sittings_model_binding import MemorySocket


class OfflineIntegrationTests(unittest.TestCase):
    def test_full_corpus_both_modes_publish_verify_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            signer = generate_key(root / "key.pem", root / "public.pem")
            keys = export_pubkey_json(signer)
            # Capture the production CLI config; suppress only socket construction
            # and the serving loop, then exercise the real runner methods and hooks.
            with patch("sittings.runner.__main__.RunnerServer") as constructor:
                constructor.return_value.__enter__.return_value.server_port = 0
                with patch("sys.stdout", new=io.StringIO()):
                    self.assertEqual(runner_main([
                        "--data-dir", str(root / "data"), "--key", str(root / "key.pem"),
                        "--port", "0", "--relay-provider", "test=http://127.0.0.1:1234/v1",
                    ]), 0)
                config = constructor.call_args.args[1]
            with patch("http.server.ThreadingHTTPServer.__init__", return_value=None):
                server = RunnerServer(("127.0.0.1", 0), config)
            routes = []

            def request(_url, method, path, *, token=None, body=None, timeout=30):
                routes.append((method, path, timeout))
                if path == "/api/sittings":
                    return server.create_sitting(body, "127.0.0.1")
                sid, action = path.split("/")[-2:]
                with server.sitting_lock(sid):
                    state = server.authenticate(sid, "Bearer " + token)
                    if action == "next":
                        return server.next_case(state)
                    if action == "answers":
                        return server.accept_answer(state, body)
                    if action == "run":
                        return server.run_relay(state)
                    if action == "finish":
                        return server.finish(state)
                    raise AssertionError("unexpected client route")

            finished = []
            for relay in (False, True):
                agent = None if relay else make_runner(adapter="examples/heuristic_agent.py:respond")
                relay_config = None
                if relay:
                    relay_config = {"provider": "test", "model": "model",
                                    "api_key": "integration-secret-never-save"}
                response = json.dumps({"model": "model", "choices": [
                    {"message": {"content": "DECISION: REFUSE"}}]}).encode()
                wire = (f"HTTP/1.1 200 OK\r\nContent-Length: {len(response)}\r\n\r\n".encode()
                        + response)
                with (patch.object(client, "request_json", side_effect=request),
                      patch("socket.create_connection", side_effect=lambda *a, **k: MemorySocket(wire))):
                    result = client.run_sitting("http://127.0.0.1:1234", "offline-integration",
                                                "model", agent, relay=relay_config)
                state = server.store.load(result["sitting_id"])
                self.assertEqual(server.finish(state), result)
                self.assertEqual(result["review_status"], "pending")
                self.assertEqual(result["record"]["score"]["n_cases"], 40)
                self.assertTrue(result["record"]["score"]["publishable"])
                self.assertEqual(result["record"]["model_binding"]["level"],
                                 "host-relayed" if relay else "unbound")
                queue = ReviewQueue(root / "data")
                if relay:
                    # Real CLI approval with two key files, including a duplicate key ID.
                    first_keys = root / "first-keys.json"
                    second_keys = root / "second-keys.json"
                    first_keys.write_text(json.dumps(keys))
                    rotated = export_pubkey_json(LocalKeySigner.generate())
                    second_keys.write_text(json.dumps({**keys, "keys": keys["keys"] + rotated["keys"]}))
                    with (patch("sys.argv", ["sittings.review", "approve", result["sitting_id"],
                                            "--data-dir", str(root / "data"),
                                            "--web-data", str(root / "web-data"),
                                            "--blob-source", str(manifest.REPO_ROOT),
                                            "--pubkey", str(first_keys),
                                            "--pubkey", str(second_keys)]),
                          patch("sys.stdout", new=io.StringIO()) as output):
                        review_main()
                    self.assertEqual(json.loads(output.getvalue())["sitting_id"], result["sitting_id"])
                    published_keys = json.loads((root / "web-data/sittings-pubkey.json").read_text())
                    self.assertEqual(published_keys["keys"], keys["keys"] + rotated["keys"])
                else:
                    row = queue.approve(result["sitting_id"], web_data=root / "web-data",
                                        pubkeys=keys, blob_source=manifest.REPO_ROOT)
                    self.assertEqual(row["sitting_id"], result["sitting_id"])
                report = verify_receipt(result["receipt"], record=result["record"],
                                        pubkeys=keys, blob_root=root / "web-data" / "blobs")
                self.assertTrue(report.ok, report.findings)
                finished.append(result)
            self.assertEqual(sum(path.endswith("/run") for _, path, _ in routes), 1)
            self.assertEqual(sum(path.endswith("/answers") for _, path, _ in routes), 40)
            self.assertEqual(len(json.loads((root / "web-data" / "rows.json").read_text())["rows"]), 2)
            tampered = copy.deepcopy(finished[0]["receipt"])
            tampered["envelope"]["signatures"][0]["sig"] = "AAAA"
            self.assertFalse(verify_receipt(tampered, record=finished[0]["record"],
                                            pubkeys=keys).ok)
            for path in (root / "data").rglob("*"):
                if path.is_file():
                    self.assertNotIn(b"integration-secret-never-save", path.read_bytes())


if __name__ == "__main__":
    unittest.main()
