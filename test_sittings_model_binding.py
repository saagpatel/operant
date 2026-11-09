"""Offline relay and binding checks; all HTTP stays on literal loopback."""

from __future__ import annotations

import base64
import contextlib
import copy
import http.client
import io
import json
import logging
import re
import threading
import types
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from sittings.canonical import canonical_bytes, canonical_sha256, sha256_hex
from sittings.model_binding import RelayConfig, build_binding, make_relay_routes, verify_binding

SITTING = "sit_" + "a" * 26
KEY = "test-secret-credential-never-retain-123456789"


class Upstream(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        body = json.loads(raw)
        self.server.requests.append((self.path, dict(self.headers), raw))
        mode = body.get("test_mode")
        if mode == "redirect":
            self.send_response(307)
            self.send_header("Location", self.server.redirect_url)
            self.end_headers()
            return
        if mode == "error":
            data, status = KEY.encode(), 400
        elif mode == "large":
            data, status = b"x" * 2048, 200
        elif mode == "invalid":
            data, status = b'{"error":"' + KEY.encode(), 200
        else:
            answer = (
                KEY
                if mode in {"echo", "escaped-echo"}
                else body.get("test_answer", "DECISION: REFUSE")
            )
            data = json.dumps(
                {
                    "id": "provider-response-1",
                    "model": body.get("test_model", "served-model-v1"),
                    "choices": [{"message": {"role": "assistant", "content": answer}}],
                    "usage": {"prompt_tokens": 9, "completion_tokens": 3},
                }
            ).encode()
            if mode == "escaped-echo":
                data = data.replace(
                    KEY.encode(), b"".join(f"\\u{ord(c):04x}".encode() for c in KEY)
                )
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data) + (10 if mode == "truncated" else 0)))
        self.send_header("x-request-id", KEY if mode == "header-echo" else "request-1")
        self.send_header(
            "openai-model", body.get("test_header_model", body.get("test_model", "served-model-v1"))
        )
        self.send_header("Set-Cookie", "identifying-cookie")
        self.send_header("X-Identifying-Header", "private-value")
        self.end_headers()
        self.wfile.write(data)


class StandInRunner(BaseHTTPRequestHandler):
    """Only exercises the documented extra-route adapter, not packet 01 auth."""

    def log_message(self, *_args):
        pass

    def do_POST(self):
        method, pattern, handler = self.server.route
        match = re.fullmatch(pattern, self.path)
        if not match or self.headers.get("Authorization") != "Bearer sitting-token":
            self.send_error(401)
            return
        assert method == "POST"
        request = {
            "sitting_id": match["sitting_id"],
            "case_id": "case-a",
            "headers": dict(self.headers),
            "body": json.loads(self.rfile.read(int(self.headers["Content-Length"]))),
        }
        response = handler(request)
        data = canonical_bytes(response["body"])
        self.send_response(response["status"])
        for key, value in response["headers"].items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@contextlib.contextmanager
def server(handler):
    instance = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    instance.requests = []
    thread = threading.Thread(target=instance.serve_forever, daemon=True)
    thread.start()
    try:
        yield instance
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join()


class BindingFixture:
    def request(self, case="case-a", **completion):
        return {
            "sitting_id": SITTING,
            "case_id": case,
            "headers": {
                "X-Provider-Key": KEY,
                "Authorization": "Bearer sitting-token",
                "Cookie": "do-not-forward",
                "X-Forwarded-For": "do-not-forward",
            },
            "body": {
                "base_url": self.base,
                "completion": {
                    "model": "requested-alias",
                    "messages": [{"role": "user", "content": "Synthetic case prompt"}],
                    **completion,
                },
            },
        }

    def call(self, case="case-a", **completion):
        result = self.handler(self.request(case, **completion))
        self.assertEqual(result["status"], 200, result)
        return result["body"]["call_id"]

    def record(self, ids=(), answer="DECISION: REFUSE", claimed="served-model-v1"):
        return {
            "format": "operant.sitting/v1",
            "sitting_id": SITTING,
            "created_at": "2020-01-01T00:00:00Z",
            "finished_at": "2100-01-01T00:00:00Z",
            "claimed_model": claimed,
            "case_order": ["case-a"],
            "answers": [
                {
                    "case_id": "case-a",
                    "answer": answer,
                    "relay_call_ids": list(ids),
                    "prompt_sha256": "0" * 64,
                    "received_at": "2100-01-01T00:00:00Z",
                }
            ],
        }

    def assertVerified(self, binding, record):
        findings = verify_binding(binding, record)
        self.assertTrue(findings)
        self.assertEqual([f for f in findings if not f["ok"]], [])

    def check_result(self, result):
        record = self.record([result["call_id"]])
        binding = build_binding(record, self.config.relay_log)
        self.assertEqual(binding["level"], "relay-linked")
        self.assertEqual(binding["evidence"]["claim_status"], "match")
        self.assertEqual(
            binding["evidence"]["answer_links"][0]["matched_call_ids"], [result["call_id"]]
        )
        record["model_binding"] = binding
        self.assertVerified(binding, record)
        path, headers, raw = self.upstream.requests[0]
        self.assertEqual(path, "/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer " + KEY)
        self.assertNotIn("Cookie", headers)
        self.assertNotIn("X-Forwarded-For", headers)
        self.assertNotIn("X-Provider-Key", headers)
        self.assertNotIn(KEY.encode(), raw)
        entry = self.config.relay_log[0]
        self.assertEqual(
            entry["provider_headers"],
            {"openai-model": "served-model-v1", "x-request-id": "request-1"},
        )
        self.assertEqual(entry["request_sha256"], sha256_hex(raw))
        self.assertEqual(
            entry["response_sha256"], sha256_hex(base64.b64decode(entry["response_body_b64"]))
        )
        self.assertEqual(result["completion"]["usage"]["prompt_tokens"], 9)
        self.assertNotIn(KEY, repr([binding, record, result, entry]))


class MemorySocket:
    """Wire-format HTTP through the same fake upstream, without kernel sockets.

    The real HTTPConnection/HTTPResponse serialize and parse all bytes. This is
    separate evidence from the real loopback integration test below.
    """

    def __init__(self, upstream):
        self.upstream = upstream
        self.sent = bytearray()

    def sendall(self, data):
        self.sent.extend(data)

    def settimeout(self, _timeout):
        pass

    def close(self):
        pass

    def makefile(self, _mode):
        class Incoming:
            def __init__(self, data):
                self.request = io.BytesIO(data)
                self.response = io.BytesIO()

            def makefile(self, _mode, *_args):
                return self.request

            def sendall(self, data):
                self.response.write(data)

        incoming = Incoming(self.sent)
        Upstream(incoming, ("127.0.0.1", 12345), self.upstream)
        return io.BytesIO(incoming.response.getvalue())


class ModelBindingTests(BindingFixture, unittest.TestCase):
    def setUp(self):
        self.base = "http://127.0.0.1:12345/v1"
        self.upstream = types.SimpleNamespace(
            requests=[], redirect_url=self.base + "/redirect-target"
        )
        original_connection = http.client.HTTPConnection

        def connection(host, port=None, timeout=None):
            conn = original_connection(host, port, timeout=timeout)
            conn.sock = MemorySocket(self.upstream)
            return conn

        patcher = patch("sittings.model_binding.relay.http.client.HTTPConnection", connection)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = RelayConfig(allowed_base_urls=(self.base,))
        self.handler = make_relay_routes(self.config)[0][2]

    def test_wire_format_round_trip_and_exact_link(self):
        response = self.handler(self.request())
        self.assertEqual(response["status"], 200)
        self.check_result(response["body"])

    def test_origin_base_uses_v1_and_policy_is_snapshotted(self):
        origin = self.base.removesuffix("/v1")
        config = RelayConfig(allowed_base_urls=(origin,))
        handler = make_relay_routes(config)[0][2]
        config.allowed_base_urls = ("https://evil.invalid",)
        request = self.request()
        request["body"]["base_url"] = origin
        self.assertEqual(handler(request)["status"], 200)
        self.assertEqual(self.upstream.requests[0][0], "/v1/chat/completions")
        request["body"]["base_url"] = "https://evil.invalid"
        self.assertEqual(handler(request)["status"], 403)

    def test_credential_never_retained_in_output_logs_or_exceptions(self):
        captured, produced = io.StringIO(), []
        handler = logging.StreamHandler(captured)
        logging.getLogger().addHandler(handler)
        try:
            with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
                for mode in (None, "echo", "escaped-echo", "header-echo", "invalid", "error"):
                    result = self.handler(self.request(test_mode=mode))
                    produced.append(result)
                    self.assertEqual(result["status"], 200 if mode is None else 502)
                with patch(
                    "sittings.model_binding.relay.http.client.HTTPConnection",
                    side_effect=OSError(KEY),
                ):
                    produced.append(self.handler(self.request()))
                for change in ({"test_answer": KEY}, {"model": KEY}):
                    produced.append(self.handler(self.request(**change)))
            record = self.record()
            binding = build_binding(record, self.config.relay_log)
            produced += [self.config.relay_log, record, binding, verify_binding(binding, record)]
            for artifact in produced:
                for encoded in (repr(artifact).encode(), canonical_bytes(artifact)):
                    self.assertNotIn(KEY.encode(), encoded)
            for entry in self.config.relay_log:
                for field in ("request_body_b64", "response_body_b64"):
                    self.assertNotIn(KEY.encode(), base64.b64decode(entry[field]))
            self.assertNotIn(KEY, captured.getvalue())
        finally:
            logging.getLogger().removeHandler(handler)

    def test_disabled_and_exact_allowlist_no_redirects_or_proxy(self):
        self.assertEqual(make_relay_routes(RelayConfig()), [])
        for url in (
            self.base + "/",
            self.base + "@evil.invalid",
            "https://evil.invalid/v1",
            self.base + "?url=https://evil.invalid",
            self.base.replace("127.0.0.1", "localhost"),
        ):
            request = self.request()
            request["body"]["base_url"] = url
            self.assertEqual(self.handler(request)["status"], 403)
        self.assertEqual(len(self.upstream.requests), 0)
        with patch.dict("os.environ", {"HTTP_PROXY": "http://invalid.invalid:1"}):
            result = self.handler(self.request(test_mode="redirect"))
        self.assertEqual(result["status"], 502)
        self.assertEqual(len(self.upstream.requests), 1)
        self.assertEqual(self.config.relay_log, [])

    def test_limits_fail_closed_and_failed_attempts_consume_cap(self):
        config = RelayConfig(
            allowed_base_urls=(self.base,),
            max_calls_per_sitting=1,
            max_response_bytes=128,
            timeout_seconds=2,
        )
        handler = make_relay_routes(config)[0][2]
        self.assertEqual(handler(self.request(test_mode="large"))["status"], 502)
        self.assertEqual(handler(self.request())["status"], 429)
        self.assertEqual(config.relay_log, [])
        self.assertEqual(len(self.upstream.requests), 1)
        # Counts are per sitting, rather than a global cap.
        request = self.request(test_mode="large")
        request["sitting_id"] = "sit_" + "b" * 26
        self.assertEqual(handler(request)["status"], 502)

    def test_incomplete_http_body_is_not_evidence(self):
        result = self.handler(self.request(test_mode="truncated"))
        self.assertEqual(result["status"], 502)
        self.assertEqual(self.config.relay_log, [])

    def test_timeout_and_invalid_input_never_dispatch(self):
        for changes in (
            {"stream": True},
            {"messages": "bad"},
            {"model": ""},
            {"test_answer": "x" * 65_536},
        ):
            self.assertIn(self.handler(self.request(**changes))["status"], (400, 413))
        self.assertEqual(len(self.upstream.requests), 0)
        with patch(
            "sittings.model_binding.relay.http.client.HTTPConnection", side_effect=TimeoutError(KEY)
        ):
            result = self.handler(self.request())
        self.assertEqual(result["status"], 502)
        self.assertNotIn(KEY, repr(result))
        self.assertEqual(self.config.relay_log, [])

    def test_link_failure_partial_coverage_and_case_attribution(self):
        call = self.call()
        for answer, ids in (
            ("DECISION: PROCEED", [call]),
            ("REFUSE", [call]),
            ("DECISION: REFUSE", []),
            ("", [call]),
            ("DECISION: REFUSE", ["unknown"]),
        ):
            record = self.record(ids, answer)
            binding = build_binding(record, self.config.relay_log)
            self.assertEqual(binding["level"], "relay-observed")
            self.assertVerified(binding, record)
        record = self.record([call])
        record["case_order"].append("case-b")
        self.assertEqual(build_binding(record, self.config.relay_log)["level"], "relay-observed")
        other = self.call("case-b")
        record["answers"][0]["relay_call_ids"] = [other]
        self.assertEqual(build_binding(record, self.config.relay_log)["level"], "relay-observed")
        record["answers"][0]["relay_call_ids"] = [call]
        record["answers"].append(
            {"case_id": "case-b", "answer": "DECISION: REFUSE", "relay_call_ids": [other]}
        )
        binding = build_binding(record, self.config.relay_log)
        self.assertEqual(binding["level"], "relay-linked")
        self.assertVerified(binding, record)

    def test_claim_mismatch_and_mixed_or_missing_reports(self):
        call = self.call()
        record = self.record([call], claimed="different-claim")
        binding = build_binding(record, self.config.relay_log)
        self.assertEqual(binding["claimed_model"], "different-claim")
        self.assertEqual(binding["observed_model"], "served-model-v1")
        self.assertEqual(binding["evidence"]["claim_status"], "mismatch")
        self.assertVerified(binding, record)
        self.call(test_model="other-model")
        binding = build_binding(record, self.config.relay_log)
        self.assertEqual(binding["level"], "relay-observed")
        self.assertIsNone(binding["observed_model"])
        self.assertEqual(binding["evidence"]["observed_models"], ["other-model", "served-model-v1"])
        self.assertEqual(binding["evidence"]["claim_status"], "mixed")
        self.assertVerified(binding, record)
        # Even a contradictory model header on one call prevents a linked level.
        self.config.relay_log.clear()
        call = self.call(test_header_model="contradiction")
        record = self.record([call])
        self.assertEqual(build_binding(record, self.config.relay_log)["level"], "relay-observed")
        self.config.relay_log.clear()
        call = self.call(test_model="", test_header_model="")
        binding = build_binding(self.record([call]), self.config.relay_log)
        self.assertEqual(binding["level"], "relay-observed")
        self.assertEqual(binding["evidence"]["claim_status"], "unknown")

    def test_every_evidence_leaf_and_digest_tamper_is_reported(self):
        call = self.call()
        record = self.record([call])
        binding = build_binding(record, self.config.relay_log)

        def leaves(value, trail=()):
            if isinstance(value, dict) and value:
                for key, child in value.items():
                    yield from leaves(child, (*trail, key))
            elif isinstance(value, list) and value:
                for index, child in enumerate(value):
                    yield from leaves(child, (*trail, index))
            else:
                yield trail, value

        for trail, value in leaves(binding["evidence"]):
            with self.subTest(trail=trail):
                modified = copy.deepcopy(binding)
                target, path = modified["evidence"], "evidence"
                for part in trail[:-1]:
                    target = target[part]
                for part in trail:
                    path += f"[{part}]" if isinstance(part, int) else "." + part
                replacement = not value if isinstance(value, bool) else str(value) + "tampered"
                target[trail[-1]] = replacement
                failures = [f for f in verify_binding(modified, record) if not f["ok"]]
                self.assertTrue(any(f["field"] == path for f in failures), failures)
        modified = copy.deepcopy(binding)
        modified["evidence_sha256"] = "0" * 64
        self.assertTrue(
            any(
                not f["ok"] and f["field"] == "evidence_sha256"
                for f in verify_binding(modified, record)
            )
        )

    def test_rehashed_body_link_and_policy_tamper_still_fail(self):
        call = self.call()
        record = self.record([call])
        binding = build_binding(record, self.config.relay_log)
        for field, value in (
            ("level", "relay-attested"),
            ("claimed_model", "fake"),
            ("observed_model", "fake"),
            ("binds", ["Weight identity proven"]),
            ("does_not_bind", []),
            ("format", "fake"),
        ):
            modified = copy.deepcopy(binding)
            modified[field] = value
            self.assertTrue(any(not f["ok"] for f in verify_binding(modified, record)))
        modified = copy.deepcopy(binding)
        modified["evidence"]["answer_links"][0]["matched_call_ids"] = []
        modified["evidence_sha256"] = canonical_sha256(modified["evidence"])
        self.assertTrue(
            any(
                not f["ok"] and f["field"].startswith("evidence.answer_links")
                for f in verify_binding(modified, record)
            )
        )
        modified = copy.deepcopy(binding)
        modified["evidence"]["calls"][0]["request_sha256"] = "0" * 64
        modified["evidence_sha256"] = canonical_sha256(modified["evidence"])
        self.assertTrue(
            any(
                not f["ok"] and f["field"].endswith("request_sha256")
                for f in verify_binding(modified, record)
            )
        )
        record["answers"][0]["answer"] = "changed"
        self.assertTrue(any(not f["ok"] for f in verify_binding(binding, record)))

    def test_replay_duplicates_and_malformed_evidence_fail_closed(self):
        call = self.call()
        record = self.record([call])
        other = copy.deepcopy(record)
        other["sitting_id"] = "sit_" + "b" * 26
        with self.assertRaisesRegex(ValueError, "invalid relay evidence"):
            build_binding(other, self.config.relay_log)
        with self.assertRaises(ValueError):
            build_binding(record, self.config.relay_log * 2)
        for binding in ({}, {"evidence": None}, {"evidence": {"calls": "bad"}}, None):
            self.assertTrue(any(not f["ok"] for f in verify_binding(binding, record)))

    def test_unbound_floor_verifies_and_names_limits(self):
        record = self.record()
        binding = build_binding(record, None)
        self.assertEqual(binding["level"], "unbound")
        self.assertIsNone(binding["observed_model"])
        self.assertEqual(binding["evidence"]["claim_status"], "unknown")
        limits = " ".join(binding["does_not_bind"])
        self.assertIn("upstream provider is honest", limits)
        self.assertIn("submitter's setup beyond the relayed calls", limits)
        self.assertVerified(binding, record)
        self.assertEqual(binding, build_binding(record, []))


class HttpRoundTripTests(BindingFixture, unittest.TestCase):
    def test_full_loopback_relay_round_trip(self):
        # Intentionally not skipped if the execution sandbox denies socket binds.
        with server(Upstream) as upstream:
            self.upstream = upstream
            self.base = f"http://127.0.0.1:{upstream.server_port}/v1"
            upstream.redirect_url = self.base + "/redirect-target"
            self.config = RelayConfig(allowed_base_urls=(self.base,))
            with server(StandInRunner) as runner:
                runner.route = make_relay_routes(self.config)[0]
                conn = http.client.HTTPConnection("127.0.0.1", runner.server_port)
                try:
                    request = self.request()
                    conn.request(
                        "POST",
                        f"/api/sittings/{SITTING}/relay/chat/completions",
                        canonical_bytes(request["body"]),
                        request["headers"],
                    )
                    response = conn.getresponse()
                    result = json.loads(response.read())
                    self.assertEqual(response.status, 200)
                finally:
                    conn.close()
            self.check_result(result)


if __name__ == "__main__":
    unittest.main()
