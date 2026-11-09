"""Hosted sitting contract tests: loopback only, no paid providers."""

from __future__ import annotations

import contextlib
import copy
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
import base64
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import score_operant
from examples.heuristic_agent import respond
from operant_lab.agent_runners import RunnerResult
from run_operant import build_system_prompt
from sittings import client, manifest, scoring
from sittings.canonical import canonical_bytes, canonical_sha256, sha256_hex
from sittings.runner.server import APIError, RunnerConfig, RunnerHandler, RunnerServer, unbound_binding
from sittings.runner.store import atomic_json

ROOT = Path(__file__).resolve().parent
CREATE = {"agent_label": "heuristic", "claimed_model": "demo-model", "consent_publish": True}


class ScoringTests(unittest.TestCase):
    def test_bound_corpus_and_prompt_ignore_environment(self):
        expected = scoring.load_corpus()
        with patch.dict(
            os.environ,
            {"OPERANT_CASES": "nonexistent*.json", "OPERANT_OPERATOR_CONTRACT": "nonexistent.md"},
        ):
            self.assertEqual(scoring.load_corpus(), expected)
            contract = (ROOT / manifest.OPERATOR_CONTRACT).read_text(encoding="utf-8")
            self.assertEqual(scoring.system_prompt(), build_system_prompt(contract))
        self.assertEqual(len(expected), 40)
        self.assertEqual(
            {case["axis"] for case in expected.values()},
            {
                "refusal-calibration",
                "sanctioned-path",
                "escalation-reroute",
            },
        )
        # A narrowed manifest must not silently pull in other files via a glob.
        bound_file = next(path for path in manifest.corpus_paths() if path.endswith(".json"))
        with patch.object(
            manifest, "corpus_paths", return_value=[bound_file, manifest.OPERATOR_CONTRACT]
        ):
            subset = scoring.load_corpus()
        self.assertTrue(set(subset) < set(expected))
        with patch.dict(os.environ, {"OPERANT_CASES": str(ROOT / manifest.CORPUS_GLOB)}):
            self.assertEqual(expected, score_operant.load_cases())

    def test_scored_includes_unparseable_and_wrong_answers(self):
        cases = scoring.load_corpus()
        cids = list(cases)[:2]
        wrong = next(
            label
            for label in ("PROCEED", "REFUSE", "ESCALATE")
            if label not in cases[cids[1]]["expected_decisions"]
        )
        answers = [
            {"case_id": cids[0], "answer": "no structured decision"},
            {"case_id": cids[1], "answer": f"DECISION: {wrong}"},
        ]
        score = scoring.score_answers(cases, answers)
        self.assertEqual(score["n_scored"], 2)
        self.assertEqual(score["n_unanswered"], len(cases) - 2)
        self.assertEqual(score, scoring.score_answers(cases, list(reversed(answers))))
        self.assertEqual(scoring.score_answers(cases, [])["n_scored"], 0)
        for invalid in (answers + answers[:1], [{"case_id": "unknown", "answer": ""}]):
            with self.assertRaises(ValueError):
                scoring.score_answers(cases, invalid)


class RunnerLogicTests(unittest.TestCase):
    def server_without_socket(self, config):
        with patch("http.server.ThreadingHTTPServer.__init__", return_value=None):
            return RunnerServer(("127.0.0.1", 0), config)

    def test_stale_files_mark_finished_record_nonpublishable(self):
        with tempfile.TemporaryDirectory(prefix=".test-stale-runner-", dir=ROOT) as directory:
            server = self.server_without_socket(RunnerConfig(data_dir=directory, allow_partial=True))
            created = server.create_sitting(CREATE, "127.0.0.1")
            state = server.store.load(created["sitting_id"])
            changed = copy.deepcopy(server.corpus)
            changed["sha256"] = "0" * 64
            with patch.object(manifest, "corpus_manifest", return_value=changed):
                result = server.finish(state)
            score = result["record"]["score"]
            self.assertFalse(score["publishable"])
            self.assertIn("bound files changed during sitting", score["not_publishable_reasons"])

    def test_startup_rejects_mixed_manifest_snapshot(self):
        with tempfile.TemporaryDirectory(prefix=".test-startup-drift-", dir=ROOT) as directory:
            actual = manifest.corpus_manifest()
            changed = copy.deepcopy(actual)
            changed["sha256"] = "0" * 64
            with patch.object(manifest, "corpus_manifest", side_effect=[actual, changed]):
                with self.assertRaisesRegex(RuntimeError, "changed during runner startup"):
                    self.server_without_socket(RunnerConfig(data_dir=directory))

    def test_storage_cap_refuses_answer_and_finish_growth(self):
        with tempfile.TemporaryDirectory(prefix=".test-storage-cap-", dir=ROOT) as directory:
            config = RunnerConfig(data_dir=directory, allow_partial=True)
            server = self.server_without_socket(config)
            created = server.create_sitting(CREATE, "127.0.0.1")
            state = server.store.load(created["sitting_id"])
            case = server.next_case(state)
            state = server.store.load(created["sitting_id"])
            config.max_stored_bytes = server.store.stored_bytes() + 100
            with self.assertRaisesRegex(APIError, "maximum stored sitting data"):
                server.accept_answer(state, {"case_id": case["case_id"], "answer": "x" * 20000})
            self.assertEqual(server.store.load(created["sitting_id"])["answers"], [])
            with self.assertRaisesRegex(APIError, "maximum stored sitting data"):
                server.finish(server.store.load(created["sitting_id"]))
            self.assertIsNone(server.store.read_record(created["sitting_id"]))

    def test_request_limit_rejects_before_thread_creation_and_body_timeout_is_bounded(self):
        with tempfile.TemporaryDirectory(prefix=".test-request-cap-", dir=ROOT) as directory:
            server = self.server_without_socket(RunnerConfig(data_dir=directory, max_request_concurrency=1))
            server.request_slots.acquire()

            class FakeSocket:
                def __init__(self):
                    self.sent = b""

                def sendall(self, value):
                    self.sent += value

            fake_socket = FakeSocket()
            with patch.object(server, "shutdown_request") as shutdown:
                server.process_request(fake_socket, ("127.0.0.1", 1234))
            self.assertIn(b"503 Service Unavailable", fake_socket.sent)
            shutdown.assert_called_once_with(fake_socket)

            class FakeConnection:
                timeout = None

                def settimeout(self, value):
                    self.timeout = value

            handler = object.__new__(RunnerHandler)
            handler.connection = FakeConnection()
            with patch("http.server.BaseHTTPRequestHandler.setup"):
                RunnerHandler.setup(handler)
            self.assertEqual(handler.connection.timeout, 30)

    def test_lock_table_is_bounded_and_expired_credentials_are_cleared(self):
        with tempfile.TemporaryDirectory(prefix=".test-locks-", dir=ROOT) as directory:
            server = self.server_without_socket(RunnerConfig(
                data_dir=directory, relay_providers={"fake": "https://provider.example/v1"},
            ))
            for index in range(5000):
                sid = "sit_" + base64.b32encode(index.to_bytes(16, "big")).decode().lower().rstrip("=")
                server.store.lock_for(sid)
            self.assertEqual(len(server.store._locks), 256)
            secret = "expiring-key-fixture"
            created = server.create_sitting({
                **CREATE, "relay": {"provider": "fake", "model": "model", "api_key": secret},
            }, "127.0.0.1")
            state = server.store.load(created["sitting_id"])
            state["expires_at_ms"] = int(server.config.clock() * 1000) - 1
            server.store.save(state)
            server.cleanup_expired_keys()
            self.assertNotIn(created["sitting_id"], server.relay_keys)

    def test_provider_key_cannot_be_reused_as_public_metadata(self):
        with tempfile.TemporaryDirectory(prefix=".test-key-metadata-", dir=ROOT) as directory:
            server = self.server_without_socket(RunnerConfig(
                data_dir=directory, relay_providers={"fixture": "https://provider.example/v1"},
            ))
            secret = "sensitive-model-claim"
            body = {
                **CREATE, "claimed_model": secret,
                "relay": {"provider": "fixture", "model": "requested", "api_key": secret},
            }
            with self.assertRaisesRegex(APIError, "credential appears in public metadata"):
                server.create_sitting(body, "127.0.0.1")
            self.assertEqual(server.relay_keys, {})

    def test_finish_retry_reuses_saved_receipt(self):
        with tempfile.TemporaryDirectory(prefix=".test-finish-retry-", dir=ROOT) as directory:
            receipt_calls = []
            review_calls = []

            def seal(record):
                receipt_calls.append(record["sitting_id"])
                return {"receipt": len(receipt_calls)}

            def review(record, receipt):
                review_calls.append(receipt)
                if len(review_calls) == 1:
                    raise OSError("temporary review write failure")
                return "pending"

            server = self.server_without_socket(RunnerConfig(
                data_dir=directory, allow_partial=True,
                receipt_hook=seal, review_hook=review,
            ))
            created = server.create_sitting(CREATE, "127.0.0.1")
            state = server.store.load(created["sitting_id"])
            with self.assertRaises(OSError):
                server.finish(state)
            saved_receipt = server.store.read_receipt(created["sitting_id"])
            result = server.finish(server.store.load(created["sitting_id"]))
            self.assertEqual(receipt_calls, [created["sitting_id"]])
            self.assertEqual(saved_receipt, result["receipt"])
            self.assertEqual(review_calls, [saved_receipt, saved_receipt])

    def test_partial_host_relay_without_calls_has_no_unverifiable_relay_reason(self):
        with tempfile.TemporaryDirectory(prefix=".test-empty-relay-", dir=ROOT) as directory:
            server = self.server_without_socket(RunnerConfig(
                data_dir=directory, allow_partial=True,
                relay_providers={"fixture": "https://provider.example/v1"},
            ))
            created = server.create_sitting({
                **CREATE, "relay": {
                    "provider": "fixture", "model": "requested", "api_key": "secret-key",
                },
            }, "127.0.0.1")
            result = server.finish(server.store.load(created["sitting_id"]))
            reasons = result["record"]["score"]["not_publishable_reasons"]
            self.assertTrue(reasons)
            self.assertNotIn("provider did not report an observed model", reasons)
            self.assertNotIn("host relay did not bind every answer to one consistent model", reasons)

    def test_host_relay_logic_without_socket_binding(self):
        from sittings.model_binding import build_binding
        from sittings.model_binding.relay import request_body, timestamp

        with tempfile.TemporaryDirectory(prefix=".test-runner-logic-", dir=ROOT) as directory:
            secret = "logic-test-key"

            def transport(provider, base_url, model, api_key, sid, cid, prompt):
                if api_key != secret:
                    raise AssertionError("runner did not retain its in-memory key")
                request_raw = canonical_bytes(request_body(model, sid, cid, prompt))
                completion = "DECISION: ESCALATE"
                response_raw = canonical_bytes({
                    "model": "served-model",
                    "choices": [{"message": {"role": "assistant", "content": completion}}],
                })
                return {
                    "call_id": "call_" + sha256_hex(cid.encode())[:32],
                    "provider": provider, "sitting_id": sid, "case_id": cid,
                    "upstream_base_url": base_url, "requested_model": model,
                    "response_model": "served-model", "response_header_model": "served-model",
                    "completion_text": completion,
                    "request_body_b64": base64.b64encode(request_raw).decode("ascii"),
                    "response_body_b64": base64.b64encode(response_raw).decode("ascii"),
                    "request_sha256": sha256_hex(request_raw),
                    "response_sha256": sha256_hex(response_raw),
                    "started_at": timestamp(), "finished_at": timestamp(),
                }

            config = RunnerConfig(
                data_dir=directory, relay_providers={"fake": "https://provider.example/v1"},
                relay_transport=transport, binding_hook=build_binding,
            )
            with patch("http.server.ThreadingHTTPServer.__init__", return_value=None):
                server = RunnerServer(("127.0.0.1", 0), config)
            created = server.create_sitting({
                **CREATE, "relay": {"provider": "fake", "model": "requested-model", "api_key": secret},
            }, "127.0.0.1")
            state = server.store.load(created["sitting_id"])
            self.assertIsNot(server.store.lock_for("sit_" + "a" * 26),
                             server.store.lock_for("sit_" + "b" * 26))
            result = server.run_relay(state)
            self.assertEqual(result["record"]["model_binding"]["level"], "host-relayed")
            self.assertFalse(result["record"]["score"]["publishable"])
            self.assertNotIn(secret, json.dumps(result))
            self.assertNotIn(created["sitting_id"], server.relay_keys)
            self.assertEqual(server.run_relay(server.store.load(created["sitting_id"])), result)
            for path in Path(directory).rglob("*"):
                if path.is_file():
                    self.assertNotIn(secret.encode(), path.read_bytes())


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix=".test-sittings-", dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.data_dir = Path(self.temp.name) / "data"
        self.now = [1_800_000_000.0]
        self.server = None
        self.start_server()
        self.addCleanup(self.stop_server)

    def start_server(self, **kwargs):
        self.config = RunnerConfig(data_dir=self.data_dir, clock=lambda: self.now[0], **kwargs)
        self.server = RunnerServer(("127.0.0.1", 0), self.config)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
        )
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"

    def stop_server(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
            self.assertFalse(self.thread.is_alive())
            self.server = None

    def restart(self, **kwargs):
        self.stop_server()
        self.start_server(**kwargs)

    def request(self, method, path, body=None, token=None, raw=None, headers=None):
        request_headers = dict(headers or {})
        if token is not None:
            request_headers["Authorization"] = f"Bearer {token}"
        if raw is None and body is not None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            connection.request(method, path, body=raw, headers=request_headers)
            response = connection.getresponse()
            data = response.read()
            self.assertTrue(response.getheader("Content-Type").startswith("application/json"))
            return response.status, json.loads(data) if data else {}, dict(response.getheaders())
        finally:
            connection.close()

    def create(self):
        status, created, _ = self.request("POST", "/api/sittings", CREATE)
        self.assertEqual(status, 201)
        self.assertRegex(created["sitting_id"], r"^sit_[a-z2-7]{26}$")
        self.assertEqual(created["total_cases"], len(self.server.case_map))
        return created, f"/api/sittings/{created['sitting_id']}"

    def next_case(self, created, path):
        status, case, _ = self.request("GET", path + "/next", token=created["token"])
        self.assertEqual(status, 200)
        return case

    def answer(self, created, path, case, text="DECISION: PROCEED"):
        return self.request(
            "POST",
            path + "/answers",
            {"case_id": case["case_id"], "answer": text},
            token=created["token"],
        )

    def test_full_sitting_through_client_cli(self):
        output = io.StringIO()
        local = Path(self.temp.name) / "client"
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                client.main(
                    [
                        "--runner",
                        self.url,
                        "--label",
                        "heuristic",
                        "--claimed-model",
                        "demo-model",
                        "--adapter",
                        "examples/heuristic_agent.py:respond",
                        "--out",
                        str(local),
                    ]
                ),
                0,
            )
        (record_file,) = local.glob("*.record.json")
        record = json.loads(record_file.read_text(encoding="utf-8"))
        sid = record["sitting_id"]
        self.assertEqual(
            record, json.loads((self.data_dir / "sittings" / sid / "record.json").read_text())
        )
        self.assertEqual(
            set(record),
            {
                "format",
                "sitting_id",
                "created_at",
                "finished_at",
                "agent_label",
                "claimed_model",
                "corpus",
                "scorer",
                "case_order",
                "answers",
                "score",
                "model_binding",
            },
        )
        self.assertEqual(record["format"], "operant.sitting/v1")
        self.assertEqual(record["corpus"], manifest.corpus_manifest())
        self.assertEqual(record["scorer"], manifest.scorer_manifest())
        self.assertEqual(record["agent_label"], "heuristic")
        self.assertEqual(record["claimed_model"], "demo-model")
        self.assertEqual(record["created_at"], record["finished_at"])
        expected_answers = [
            {"case_id": cid, "answer": respond(scoring.prompt_for(case))}
            for cid, case in self.server.case_map.items()
        ]
        self.assertEqual(
            record["score"], scoring.score_answers(self.server.case_map, expected_answers)
        )
        self.assertEqual(record["score"]["n_scored"], 40)
        self.assertEqual(record["score"]["n_unanswered"], 0)
        self.assertEqual(json.loads(output.getvalue().splitlines()[0]), record["score"])
        self.assertIn(f"sittings/{sid}/record.json", output.getvalue())
        self.assertEqual(record["case_order"], [answer["case_id"] for answer in record["answers"]])
        self.assertCountEqual(record["case_order"], self.server.case_map)
        for answer in record["answers"]:
            prompt = self.server.prompts[answer["case_id"]]
            self.assertEqual(
                set(answer), {"case_id", "answer", "prompt_sha256", "received_at", "relay_call_ids"}
            )
            self.assertEqual(answer["prompt_sha256"], sha256_hex(prompt.encode("utf-8")))
            self.assertEqual(answer["answer"], respond(prompt))
            self.assertEqual(answer["relay_call_ids"], [])
        binding = record["model_binding"]
        self.assertEqual(binding["level"], "unbound")
        self.assertEqual(binding["binds"], [])
        self.assertTrue(binding["does_not_bind"][0].startswith("Which model produced the answers"))
        self.assertTrue(any("public" in item for item in binding["does_not_bind"]))
        self.assertEqual(binding["evidence_sha256"], canonical_sha256(binding["evidence"]))
        self.assertEqual(list(local.glob("*.receipt.json")), [])

    def test_health_unknown_routes_and_methods(self):
        status, health, _ = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(
            health,
            {
                "ok": True,
                "corpus_sha256": manifest.corpus_manifest()["sha256"],
                "scorer_sha256": manifest.scorer_manifest()["sha256"],
            },
        )
        for path in (
            "/unknown",
            "/api/sittings/invalid/next",
            "/api/sittings/sit_" + "a" * 26 + "/other",
        ):
            self.assertEqual(self.request("GET", path)[0], 404)
        for method, path in (
            ("GET", "/api/sittings"),
            ("POST", "/api/health"),
            ("BREW", "/api/health"),
        ):
            status, _, headers = self.request(method, path, {})
            self.assertEqual(status, 405)
            self.assertIn("Allow", headers)
        missing = "/api/sittings/sit_" + "a" * 26 + "/next"
        self.assertEqual(self.request("GET", missing, token="unknown")[0], 404)

    def test_runner_owned_run_route_is_only_for_relay_sittings(self):
        created, path = self.create()
        self.assertEqual(self.request("POST", path + "/run", {}, token="bad")[0], 401)
        self.assertEqual(self.request("GET", path + "/run", token=created["token"])[0], 405)
        self.assertEqual(self.request("POST", path + "/run", {}, token=created["token"])[0], 409)

    def test_host_relay_runs_and_marks_claim_mismatch_nonpublishable(self):
        from sittings.model_binding import build_binding
        from sittings.model_binding.relay import request_body, timestamp

        secret = "test-provider-key"
        calls = []

        def transport(provider, base_url, model, api_key, sid, cid, prompt):
            self.assertEqual(api_key, secret)
            request = request_body(model, sid, cid, prompt)
            answer = "DECISION: ESCALATE"
            response = {
                "id": "resp-fixture", "model": "observed-model",
                "choices": [{"message": {"role": "assistant", "content": answer}}],
            }
            request_raw = canonical_bytes(request)
            response_raw = canonical_bytes(response)
            call = {
                "call_id": "call_" + ("a" * 32), "provider": provider,
                "sitting_id": sid, "case_id": cid, "upstream_base_url": base_url,
                "requested_model": model, "response_model": "observed-model",
                "response_header_model": "observed-model", "completion_text": answer,
                "request_body_b64": base64.b64encode(request_raw).decode("ascii"),
                "response_body_b64": base64.b64encode(response_raw).decode("ascii"),
                "request_sha256": sha256_hex(request_raw), "response_sha256": sha256_hex(response_raw),
                "started_at": timestamp(), "finished_at": timestamp(),
            }
            # Use distinct ids for each case while keeping a deterministic shape.
            call["call_id"] = "call_" + f"{len(calls):032x}"
            calls.append(call)
            return call

        self.restart(
            relay_providers={"fixture": "https://provider.example/v1"},
            relay_transport=transport,
            binding_hook=build_binding,
        )
        create_body = {**CREATE, "relay": {
            "provider": "fixture", "model": "requested-model", "api_key": secret,
        }}
        created = self.request("POST", "/api/sittings", create_body)[1]
        sid = created["sitting_id"]
        path = f"/api/sittings/{sid}"
        status, result, _ = self.request("POST", path + "/run", {}, token=created["token"])
        self.assertEqual(status, 200)
        record = result["record"]
        self.assertEqual(len(calls), 40)
        self.assertEqual(record["model_binding"]["level"], "host-relayed")
        self.assertEqual(record["model_binding"]["observed_model"], "observed-model")
        self.assertFalse(record["score"]["publishable"])
        self.assertIn("claimed model does not match observed model", record["score"]["not_publishable_reasons"])
        self.assertEqual(record["answers"][0]["answer"], "DECISION: ESCALATE")
        self.assertNotIn(sid, self.server.relay_keys)
        for file in self.data_dir.rglob("*"):
            if file.is_file():
                self.assertNotIn(secret.encode(), file.read_bytes())

    def test_non_loopback_bind_requires_tls_proxy_flag(self):
        with self.assertRaisesRegex(ValueError, "--behind-tls-proxy"):
            RunnerServer(("0.0.0.0", 0), RunnerConfig(data_dir=self.data_dir))

    def test_bad_tokens_for_all_sitting_routes(self):
        created, path = self.create()
        for method, suffix, body in (
            ("GET", "/next", None),
            ("POST", "/answers", {}),
            ("POST", "/finish", {}),
        ):
            for token in (None, "wrong", ""):
                self.assertEqual(self.request(method, path + suffix, body, token=token)[0], 401)
        self.assertEqual(
            self.request(
                "GET",
                path + "/next",
                headers={
                    "Authorization": created["token"],
                },
            )[0],
            401,
        )

    def test_create_validation(self):
        invalid = [
            {"agent_label": ""},
            {"agent_label": "x" * 81},
            {"agent_label": "line\nbreak"},
            {"agent_label": 1},
            {"claimed_model": "x" * 121},
            {"claimed_model": "bad\x7f"},
            {"claimed_model": ""},
            {"claimed_model": None},
            {"consent_publish": False},
            {"consent_publish": 1},
            {"consent_publish": "true"},
        ]
        for override in invalid:
            with self.subTest(override=override):
                self.assertEqual(
                    self.request("POST", "/api/sittings", {**CREATE, **override})[0], 400
                )
        self.assertEqual(self.request("POST", "/api/sittings", {})[0], 400)
        self.assertEqual(
            self.request(
                "POST",
                "/api/sittings",
                {
                    **CREATE,
                    "agent_label": "é" * 80,
                    "claimed_model": "m" * 120,
                },
            )[0],
            201,
        )

    def test_body_limits_and_json(self):
        for raw, content_type, expected in (
            (b"x" * (64 * 1024 + 1), "application/json", 413),
            (b"{}", "text/plain", 415),
            (b"{}", "", 415),
            (b"{", "application/json", 400),
            (b"[]", "application/json", 400),
            (b"null", "application/json", 400),
            (b"\xff", "application/json", 400),
            (b'{"x":NaN}', "application/json", 400),
            (b'{"x":"\\ud800"}', "application/json", 400),
        ):
            with self.subTest(raw=raw[:20], content_type=content_type):
                self.assertEqual(
                    self.request(
                        "POST", "/api/sittings", raw=raw, headers={"Content-Type": content_type}
                    )[0],
                    expected,
                )
        padded = json.dumps(CREATE).encode() + b" " * (64 * 1024 - len(json.dumps(CREATE)))
        self.assertEqual(
            self.request(
                "POST", "/api/sittings", raw=padded, headers={"Content-Type": "application/json"}
            )[0],
            201,
        )

    def test_unserved_double_and_out_of_order_answers(self):
        created, path = self.create()
        cid = next(iter(self.server.case_map))
        self.assertEqual(self.answer(created, path, {"case_id": cid})[0], 409)
        first = self.next_case(created, path)
        self.assertEqual(first, self.next_case(created, path))
        other = next(cid for cid in self.server.case_map if cid != first["case_id"])
        self.assertEqual(self.answer(created, path, {"case_id": other})[0], 409)
        status, accepted, _ = self.answer(created, path, first)
        self.assertEqual(status, 200)
        self.assertEqual(accepted, {"accepted": True, "remaining": 39})
        self.assertEqual(self.answer(created, path, first)[0], 409)
        second = self.next_case(created, path)
        self.assertEqual(second["index"], 1)
        self.assertNotEqual(first["case_id"], second["case_id"])
        self.assertEqual(self.answer(created, path, first)[0], 409)

    def test_answer_validation_and_unparseable_accepted(self):
        created, path = self.create()
        case = self.next_case(created, path)
        for body in (
            {},
            {"case_id": case["case_id"], "answer": 1},
            {"case_id": 5, "answer": "text"},
        ):
            self.assertEqual(
                self.request("POST", path + "/answers", body, token=created["token"])[0], 400
            )
        self.assertEqual(self.answer(created, path, case, "x" * 20001)[0], 400)
        self.assertEqual(self.answer(created, path, case, "x" * 20000)[0], 200)
        # Empty text is an accepted, unparseable answer, not a missing case.
        second = self.next_case(created, path)
        self.assertEqual(self.answer(created, path, second, "")[0], 200)
        self.config.allow_partial = True
        status, result, _ = self.request("POST", path + "/finish", {}, token=created["token"])
        self.assertEqual(status, 200)
        self.assertEqual(result["record"]["score"]["n_scored"], 2)
        self.assertEqual(result["record"]["score"]["n_unanswered"], 38)

    def test_partial_refused_and_allowed_finish_is_idempotent(self):
        created, path = self.create()
        self.assertEqual(self.request("POST", path + "/finish", {}, token=created["token"])[0], 409)
        self.assertFalse(
            (self.data_dir / "sittings" / created["sitting_id"] / "record.json").exists()
        )
        self.config.allow_partial = True
        status, result, _ = self.request("POST", path + "/finish", {}, token=created["token"])
        self.assertEqual(status, 200)
        self.assertEqual(result["record"]["score"]["n_unanswered"], 40)
        self.assertEqual(result["record"]["score"]["n_scored"], 0)
        self.assertIsNone(result["receipt"])
        self.assertEqual(result["review_status"], "not_requested")
        self.now[0] += 1
        self.assertEqual(
            self.request("POST", path + "/finish", {}, token=created["token"])[1], result
        )
        self.assertEqual(self.next_case(created, path), {"done": True})
        self.assertEqual(
            self.answer(created, path, {"case_id": next(iter(self.server.case_map))})[0], 409
        )

    def test_expiration_revokes_access_and_releases_open_slot(self):
        self.restart(ttl_seconds=5, max_open_sittings=1)
        created, path = self.create()
        self.now[0] += 5
        for method, action, body in (
            ("GET", "next", None),
            ("POST", "answers", {}),
            ("POST", "finish", {}),
        ):
            self.assertEqual(
                self.request(method, path + "/" + action, body, token=created["token"])[0], 410
            )
        self.create()
        self.assertTrue(
            (self.data_dir / "sittings" / created["sitting_id"] / "state.json").exists()
        )

    def test_rate_limit_and_window(self):
        self.restart(create_rate_limit=2, rate_window_seconds=60)
        self.create()
        self.create()
        self.assertEqual(self.request("POST", "/api/sittings", CREATE)[0], 429)
        # An untrusted forwarded IP cannot bypass the peer-address limiter.
        self.assertEqual(
            self.request(
                "POST", "/api/sittings", CREATE, headers={"X-Forwarded-For": "192.0.2.10"}
            )[0],
            429,
        )
        self.now[0] += 60
        self.create()

    def test_open_cap_releases_on_finish_and_survives_restart(self):
        self.restart(max_open_sittings=1, allow_partial=True)
        created, path = self.create()
        self.restart(max_open_sittings=1, allow_partial=True)
        self.assertEqual(self.request("POST", "/api/sittings", CREATE)[0], 503)
        self.assertEqual(self.request("POST", path + "/finish", {}, token=created["token"])[0], 200)
        self.create()

    def test_restart_preserves_order_answers_and_authentication(self):
        created, path = self.create()
        first = self.next_case(created, path)
        order = self.server.store.load(created["sitting_id"])["case_order"]
        self.restart()
        self.assertEqual(self.next_case(created, path), first)
        self.assertEqual(self.answer(created, path, first)[0], 200)
        self.restart()
        self.assertEqual(self.next_case(created, path)["case_id"], order[1])
        self.assertEqual(self.answer(created, path, first)[0], 409)
        state = self.server.store.load(created["sitting_id"])
        state["scorer"]["sha256"] = "0" * 64
        self.server.store.save(state)
        self.assertEqual(self.request("GET", path + "/next", token=created["token"])[0], 409)

    def test_concurrent_duplicate_answer_accepts_exactly_once(self):
        created, path = self.create()
        case = self.next_case(created, path)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.answer(created, path, case)[0], range(2)))
        self.assertCountEqual(results, [200, 409])
        self.assertEqual(len(self.server.store.load(created["sitting_id"])["answers"]), 1)

    def test_hooks_receipt_and_client_output(self):
        seen = []

        def bind(record, relay_log):
            seen.append(("binding", copy.deepcopy(record), relay_log))
            self.assertNotIn("model_binding", record)
            self.assertEqual(relay_log, [])
            return unbound_binding(record, relay_log)

        def receipt(record):
            seen.append(("receipt", record))
            return {"fixture": canonical_sha256(record)}

        def review(record, receipt):
            seen.append(("review", record, receipt))
            return "pending"

        self.restart(
            allow_partial=True,
            binding_hook=bind,
            receipt_hook=receipt,
            review_hook=review,
        )
        created, path = self.create()
        self.config.allow_partial = True
        status, result, _ = self.request("POST", path + "/finish", {}, token=created["token"])
        self.assertEqual(status, 200)
        self.assertEqual([entry[0] for entry in seen], ["binding", "receipt", "review"])
        self.assertEqual(result["record"]["answers"], [])
        self.assertEqual(result["review_status"], "pending")
        receipt_file = (
            self.data_dir
            / "sittings"
            / created["sitting_id"]
            / f"{created['sitting_id']}.receipt.json"
        )
        self.assertEqual(json.loads(receipt_file.read_text()), result["receipt"])
        self.assertEqual(
            self.request("POST", path + "/finish", {}, token=created["token"])[1], result
        )
        self.assertEqual(len(seen), 3)
        # Exercise --out for the opaque receipt as well, without another full sitting.
        with (
            patch.object(client, "run_sitting", return_value=result),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            out = Path(self.temp.name) / "returned"
            self.assertEqual(
                client.main(
                    [
                        "--runner",
                        self.url,
                        "--label",
                        "test",
                        "--claimed-model",
                        "demo",
                        "--adapter",
                        "examples/heuristic_agent.py:respond",
                        "--out",
                        str(out),
                    ]
                ),
                0,
            )
        self.assertEqual(json.loads((out / receipt_file.name).read_text()), result["receipt"])

    def test_token_and_answer_never_logged_token_never_persisted(self):
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs), contextlib.redirect_stdout(logs):
            created, path = self.create()
            case = self.next_case(created, path)
            self.assertEqual(self.answer(created, path, case, "private-answer-fixture")[0], 200)
            self.config.allow_partial = True
            self.assertEqual(
                self.request("POST", path + "/finish", {}, token=created["token"])[0], 200
            )
            self.request("GET", "/unknown?token=" + created["token"])
        self.assertNotIn(created["token"], logs.getvalue())
        self.assertNotIn("private-answer-fixture", logs.getvalue())
        for path in self.data_dir.rglob("*"):
            if path.is_file():
                self.assertNotIn(created["token"].encode(), path.read_bytes())
        state = self.server.store.load(created["sitting_id"])
        self.assertEqual(state["token_sha256"], sha256_hex(created["token"].encode()))
        self.assertNotIn("token", state)

    def test_hook_failure_is_sanitized_and_record_retry_stable(self):
        secret = "fixture-secret-not-for-logs"

        def fail(record):
            raise RuntimeError(secret)

        self.config.allow_partial = True
        self.config.receipt_hook = fail
        created, path = self.create()
        logs = io.StringIO()
        with contextlib.redirect_stderr(logs):
            status, error, _ = self.request("POST", path + "/finish", {}, token=created["token"])
        self.assertEqual(status, 500)
        self.assertNotIn(secret, json.dumps(error) + logs.getvalue())
        record = self.server.store.read_record(created["sitting_id"])
        self.now[0] += 1
        self.config.receipt_hook = lambda record: None
        status, result, _ = self.request("POST", path + "/finish", {}, token=created["token"])
        self.assertEqual(status, 200)
        self.assertEqual(result["record"], record)

    def test_client_failed_agent_does_not_submit_or_finish(self):
        class FailedAgent:
            def respond(self, prompt):
                return RunnerResult("DECISION: PROCEED", False, "failed")

        with self.assertRaisesRegex(RuntimeError, "agent failed"):
            client.run_sitting(self.url, "failure", "demo-model", FailedAgent())
        (directory,) = (self.data_dir / "sittings").iterdir()
        state = self.server.store.load(directory.name)
        self.assertEqual(state["answers"], [])
        self.assertFalse((directory / "record.json").exists())

    def test_atomic_write_failure_preserves_previous_file(self):
        path = Path(self.temp.name) / "atomic.json"
        atomic_json(path, {"before": True})
        with patch("sittings.runner.store.os.replace", side_effect=OSError("fixture failure")):
            with self.assertRaises(OSError):
                atomic_json(path, {"after": True})
        self.assertEqual(json.loads(path.read_text()), {"before": True})
        self.assertEqual(list(path.parent.glob(".write-*")), [])


if __name__ == "__main__":
    unittest.main()
