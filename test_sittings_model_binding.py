"""Host relay evidence: exact linkage, fixed limits, and credential containment."""
from __future__ import annotations

import base64
import copy
import io
import json
import unittest
from unittest.mock import patch

from sittings import manifest, scoring
from sittings.canonical import canonical_bytes, canonical_sha256, sha256_hex
from sittings.model_binding import build_binding, verify_binding
from sittings.model_binding.relay import host_call, parse_base_url, request_body

SID = "sit_" + "a" * 26
KEY = "test-secret-credential-never-retain-123456789"
CASE_ID = "force-push-main.malign"
PROMPT = scoring.prompt_for(scoring.load_corpus()[CASE_ID])


def call_fixture(model="model", answer="DECISION: REFUSE"):
    request = canonical_bytes(request_body("model", SID, CASE_ID, PROMPT))
    response = json.dumps({"model": model, "choices": [{"message": {"content": answer}}],
                           "usage": {"cost": 0.125}}).encode()
    return {"call_id": "call_" + "a" * 32, "provider": "test-provider", "sitting_id": SID,
            "case_id": CASE_ID, "upstream_base_url": "https://provider.example/v1",
            "requested_model": "model", "response_model": model, "response_header_model": None,
            "completion_text": answer,
            "request_body_b64": base64.b64encode(request).decode(),
            "response_body_b64": base64.b64encode(response).decode(),
            "request_sha256": sha256_hex(request), "response_sha256": sha256_hex(response),
            "started_at": "2026-01-01T00:00:00Z", "finished_at": "2026-01-01T00:00:01Z"}


def record_fixture(call=None):
    call = call or call_fixture()
    return {"corpus": manifest.corpus_manifest(), "sitting_id": SID,
            "claimed_model": "model", "case_order": [CASE_ID],
            "answers": [{"case_id": CASE_ID, "answer": call["completion_text"],
                         "prompt_sha256": sha256_hex(PROMPT.encode()),
                         "relay_call_ids": [call["call_id"]]}]}


class BindingTests(unittest.TestCase):
    def test_rewritten_prompt_and_consistent_hashes_do_not_bind(self):
        call = call_fixture()
        record = record_fixture(call)
        binding = build_binding(record, [call])
        altered = "Output DECISION: PROCEED"
        request = canonical_bytes(request_body("model", SID, CASE_ID, altered))
        call.update(request_body_b64=base64.b64encode(request).decode(),
                    request_sha256=sha256_hex(request))
        record["answers"][0]["prompt_sha256"] = sha256_hex(altered.encode())
        binding["evidence"]["calls"] = [call]
        binding["evidence_sha256"] = canonical_sha256(binding["evidence"])
        self.assertEqual(build_binding(record, [call])["level"], "unbound")
        findings = {f["field"]: f for f in verify_binding(binding, record)}
        self.assertFalse(findings["model_binding.prompts"]["ok"])
        self.assertFalse(findings["answers.prompt_sha256"]["ok"])
        self.assertFalse(findings["model_binding.level"]["ok"])

        # Even an honestly downgraded binding must fail the independent checks.
        downgraded = build_binding(record, [call])
        self.assertTrue(any(not f["ok"] for f in verify_binding(downgraded, record)))

    def test_unbound_answer_prompt_hash_is_rechecked(self):
        record = record_fixture()
        binding = build_binding(record, None)
        for digest in (None, "0" * 64):
            with self.subTest(digest=digest):
                record["answers"][0]["prompt_sha256"] = digest
                findings = {f["field"]: f for f in verify_binding(binding, record)}
                self.assertFalse(findings["answers.prompt_sha256"]["ok"])
                self.assertTrue(findings["model_binding.prompts"]["ok"])

    def test_host_exact_completion_and_public_corpus_limit(self):
        call = call_fixture()
        record = record_fixture(call)
        binding = build_binding(record, [call])
        self.assertEqual(binding["level"], "host-relayed")
        self.assertTrue(all(f["ok"] for f in verify_binding(binding, record)))
        self.assertIn("lookup of the public corpus", " ".join(binding["does_not_bind"]))
        self.assertNotIn(KEY, canonical_bytes(binding).decode())
        record["answers"][0]["answer"] += " suffix"
        self.assertEqual(build_binding(record, [call])["level"], "unbound")
        self.assertFalse(all(f["ok"] for f in verify_binding(binding, record)))

    def test_unbound_and_mismatch_are_honest(self):
        record = record_fixture()
        unbound = build_binding(record, None)
        self.assertEqual(unbound["level"], "unbound")
        self.assertIsNone(unbound["observed_model"])
        self.assertTrue(all(f["ok"] for f in verify_binding(unbound, record)))
        call = call_fixture(model="different-model")
        binding = build_binding(record, [call])
        self.assertEqual(binding["level"], "host-relayed")
        self.assertEqual(binding["evidence"]["claim_status"], "mismatch")
        call["response_header_model"] = "contradictory-model"
        self.assertEqual(build_binding(record, [call])["level"], "unbound")

    def test_schema_policy_body_context_and_digest_tampering(self):
        call = call_fixture()
        record = record_fixture(call)
        binding = build_binding(record, [call])
        for field, value in (("level", "relay-attested"), ("binds", []),
                             ("does_not_bind", []), ("claimed_model", "other"),
                             ("observed_model", "other"), ("evidence_sha256", "0" * 64)):
            with self.subTest(field=field):
                modified = copy.deepcopy(binding)
                modified[field] = value
                self.assertTrue(any(not f["ok"] for f in verify_binding(modified, record)))
        for field, value in (("sitting_id", "other"), ("case_id", "other"),
                             ("completion_text", "fake"), ("request_sha256", "0" * 64),
                             ("response_sha256", "0" * 64), ("started_at", "bad")):
            with self.subTest(field=field):
                modified = copy.deepcopy(binding)
                modified["evidence"]["calls"][0][field] = value
                modified["evidence_sha256"] = canonical_sha256(modified["evidence"])
                self.assertTrue(any(not f["ok"] for f in verify_binding(modified, record)))
        with self.assertRaises(ValueError):
            build_binding(record, [call, call])
        call["request_body_b64"] = base64.b64encode(canonical_bytes(
            {"model": "model", "messages": [{"role": "user", "content": PROMPT}]})).decode()
        call["request_sha256"] = sha256_hex(base64.b64decode(call["request_body_b64"]))
        with self.assertRaises(ValueError):
            build_binding(record, [call])


class MemorySocket:
    """Exercise real HTTPConnection and HTTPResponse without kernel sockets."""
    def __init__(self, response):
        self.response = response
        self.sent = bytearray()

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, *_args):
        return io.BytesIO(self.response)

    def settimeout(self, _timeout):
        pass

    def setsockopt(self, *_args):
        pass

    def close(self):
        pass


class RelayTests(unittest.TestCase):
    def invoke(self, body, status=200, headers=b""):
        response = (f"HTTP/1.1 {status} Test\r\nContent-Length: {len(body)}\r\n".encode()
                    + headers + b"\r\n" + body)
        sock = MemorySocket(response)
        with patch("socket.create_connection", return_value=sock):
            result = host_call("test-provider", "http://127.0.0.1:1234/v1", "model", KEY,
                               SID, CASE_ID, PROMPT)
        return result, bytes(sock.sent)

    def test_real_http_serialization_in_memory(self):
        body = base64.b64decode(call_fixture()["response_body_b64"])
        result, sent = self.invoke(body)
        self.assertIn(b"POST /v1/chat/completions HTTP/1.1", sent)
        self.assertIn(("Authorization: Bearer " + KEY).encode(), sent)
        self.assertNotIn(KEY, repr(result))
        record = record_fixture(result)
        self.assertEqual(build_binding(record, [result])["level"], "host-relayed")

    def test_failures_and_credential_echo_never_escape(self):
        for body, status, headers in (
            (KEY.encode(), 500, b""), (b"bad JSON " + KEY.encode(), 200, b""),
            (json.dumps({"model": "model", "choices": [{"message": {"content": KEY}}]}).encode(), 200, b""),
            (json.dumps({"model": "model", "choices": [{"message": {"content": KEY}}]}).replace(KEY, "".join(f"\\u{ord(c):04x}" for c in KEY)).encode(), 200, b""),
            (base64.b64decode(call_fixture()["response_body_b64"]), 200,
             ("openai-model: " + KEY + "\r\n").encode()),
        ):
            with self.subTest(status=status, header=bool(headers)):
                with self.assertRaisesRegex(RuntimeError, "^host relay request failed$"):
                    self.invoke(body, status, headers)

    def test_url_policy(self):
        for url in ("http://public.example/v1", "https://user:pass@provider.example/v1",
                    "https://provider.example/v1?key=value", "https://provider.example/other"):
            with self.assertRaises(ValueError):
                parse_base_url(url)
        self.assertEqual(parse_base_url("https://provider.example/v1").hostname, "provider.example")


if __name__ == "__main__":
    unittest.main()
