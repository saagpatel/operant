"""Host-built OpenAI-compatible calls; credentials never enter public evidence."""
from __future__ import annotations

import base64
import http.client
import json
import time
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sittings.canonical import canonical_bytes, sha256_hex


def parse_base_url(value: str):
    """Only server-configured HTTPS origins, or literal loopback HTTP for tests."""
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or parts.fragment or parts.path not in {"", "/v1"}
            or (parts.scheme == "http" and parts.hostname != "127.0.0.1")
            or parts.netloc.endswith(":")):
        raise ValueError("invalid operator upstream base URL")
    _ = parts.port
    return parts


def parse_json(raw: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject(_value):
        raise ValueError("non-JSON constant")

    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject)


def request_body(model: str, sitting_id: str, case_id: str, prompt: str) -> dict:
    return {"model": model, "messages": [{"role": "user", "content": prompt}],
            "metadata": {"sitting_id": sitting_id, "case_id": case_id}, "stream": False}


def timestamp():
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def contains_secret(value, secret):
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(contains_secret(k, secret) or contains_secret(v, secret)
                   for k, v in value.items())
    if isinstance(value, list):
        return any(contains_secret(v, secret) for v in value)
    return False


def host_call(provider, base_url, model, api_key, sitting_id, case_id, prompt):
    """One host-constructed call, no redirects/proxies/caller-supplied headers.

    Evidence retains raw request/response bytes as base64, including provider JSON
    numbers without promoting those numbers into the canonical data model.
    Errors contain no upstream text, URL, key, or answer.
    """
    connection = None
    try:
        parts = parse_base_url(base_url)
        if (not isinstance(api_key, str) or not api_key or len(api_key) > 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in api_key)):
            raise ValueError("invalid credential")
        request = request_body(model, sitting_id, case_id, prompt)
        if contains_secret([request, provider, base_url], api_key):
            raise ValueError("credential in request")
        raw = canonical_bytes(request)
        if len(raw) > 65536:
            raise ValueError("request too large")
        started = timestamp()
        deadline = time.monotonic() + 30
        cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
        connection = cls(parts.hostname, parts.port, timeout=30)
        connection.request("POST", (parts.path or "/v1") + "/chat/completions", body=raw,
                           headers={"Authorization": "Bearer " + api_key,
                                    "Content-Type": "application/json", "Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("upstream status")
        chunks, size = [], 0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("upstream timeout")
            if connection.sock is not None:
                connection.sock.settimeout(remaining)
            chunk = response.read1(min(65536, 1_048_577 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 1_048_576:
                raise ValueError("upstream size")
        if response.length not in (None, 0):
            raise ValueError("incomplete response")
        response_raw = b"".join(chunks)
        completion = parse_json(response_raw)
        choices = completion["choices"]
        if len(choices) != 1:
            raise ValueError("ambiguous completion")
        answer = choices[0]["message"]["content"]
        observed = completion.get("model")
        if (not isinstance(answer, str) or len(answer) > 20000
                or (observed is not None and not isinstance(observed, str))):
            raise ValueError("invalid completion")
        header_model = response.getheader("openai-model")
        call = {"call_id": "call_" + uuid.uuid4().hex, "provider": provider,
                "sitting_id": sitting_id, "case_id": case_id, "upstream_base_url": base_url,
                "requested_model": model, "response_model": observed,
                "response_header_model": header_model, "completion_text": answer,
                "request_body_b64": base64.b64encode(raw).decode("ascii"),
                "response_body_b64": base64.b64encode(response_raw).decode("ascii"),
                "request_sha256": sha256_hex(raw), "response_sha256": sha256_hex(response_raw),
                "started_at": started, "finished_at": timestamp()}
        if (api_key.encode() in response_raw
                or contains_secret([completion, call], api_key)):
            raise ValueError("credential echoed")
        canonical_bytes(call)
        return call
    except Exception:
        raise RuntimeError("host relay request failed") from None
    finally:
        if connection is not None:
            try:
                connection.close()
            except Exception:
                pass
