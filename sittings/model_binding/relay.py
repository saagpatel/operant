"""Credential-free relay evidence and the plain-dict runner route adapter.

See docs/open-sittings-model-binding.md for the trusted adapter contract.
"""

from __future__ import annotations

import base64
import copy
import http.client
import json
import math
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sittings.canonical import canonical_bytes, sha256_hex

PROVIDER_HEADERS = frozenset({"openai-model", "x-request-id", "request-id"})
ROUTE = r"/api/sittings/(?P<sitting_id>sit_[a-z2-7]{26})/relay/chat/completions"


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def parse_base_url(value: str):
    """Operator policy accepts HTTPS bases, plus literal loopback HTTP for tests."""
    parts = urlsplit(value)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or parts.path not in {"", "/v1"}
        or (parts.scheme == "http" and parts.hostname != "127.0.0.1")
        or parts.netloc.endswith(":")
    ):
        raise ValueError("invalid operator upstream base URL")
    _ = parts.port  # Validate port before any requests.
    return parts


def _contains_secret(value, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(
            _contains_secret(k, secret) or _contains_secret(v, secret) for k, v in value.items()
        )
    if isinstance(value, list):
        return any(_contains_secret(v, secret) for v in value)
    return False


def parse_json(data: bytes):
    """Reject non-JSON constants and duplicate keys (no ambiguous evidence)."""

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def bad_constant(_value):
        raise ValueError("non-JSON constant")

    def finite_float(value):
        result = float(value)
        if not math.isfinite(result):
            raise ValueError("non-finite JSON number")
        return result

    return json.loads(
        data.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_constant=bad_constant,
        parse_float=finite_float,
    )


@dataclass
class RelayConfig:
    allowed_base_urls: tuple[str, ...] = ()
    max_calls_per_sitting: int = 80
    max_response_bytes: int = 1_048_576
    timeout_seconds: float = 30.0
    relay_log: list[dict] = field(default_factory=list)


def make_relay_routes(config: RelayConfig) -> list[tuple]:
    """Return (method, full-match regex, handler) routes; empty policy disables relay.

    handler({sitting_id, case_id, headers, body}) -> {status, headers, body}.
    The runner MUST authenticate/authorize sitting_id and case_id before calling.
    body = {base_url, completion}; headers contains X-Provider-Key. All remaining
    caller headers are ignored. The returned body is a JSON object, not bytes.
    """
    # Snapshot policy: mutating a config later cannot silently extend the allowlist.
    allowed = {value: parse_base_url(value) for value in config.allowed_base_urls}
    cap, size, timeout = (
        config.max_calls_per_sitting,
        config.max_response_bytes,
        config.timeout_seconds,
    )
    if (
        type(cap) is not int
        or cap < 1
        or type(size) is not int
        or size < 1
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("invalid relay limits")
    if not allowed:
        return []
    lock = threading.Lock()
    counts: dict[str, int] = {}

    def reply(status, body):
        return {"status": status, "headers": {"Content-Type": "application/json"}, "body": body}

    def handle(request):
        connection = None
        try:
            sitting_id, case_id = request["sitting_id"], request["case_id"]
            if (
                not isinstance(sitting_id, str)
                or not re.fullmatch(r"sit_[a-z2-7]{26}", sitting_id)
                or not isinstance(case_id, str)
                or not case_id
                or len(case_id) > 200
            ):
                return reply(400, {"error": "invalid relay context"})
            headers = {k.lower(): v for k, v in request["headers"].items()}
            secret = headers.get("x-provider-key")
            if (
                not isinstance(secret, str)
                or not secret
                or len(secret) > 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in secret)
            ):
                return reply(400, {"error": "invalid provider credential"})
            body = request["body"]
            if not isinstance(body, dict) or set(body) != {"base_url", "completion"}:
                return reply(400, {"error": "invalid relay request"})
            base_url = body["base_url"]
            if not isinstance(base_url, str) or base_url not in allowed:
                return reply(403, {"error": "upstream not allowlisted"})
            completion = body["completion"]
            if (
                not isinstance(completion, dict)
                or not isinstance(completion.get("model"), str)
                or not completion["model"]
                or completion.get("stream", False) is not False
                or not isinstance(completion.get("messages"), list)
            ):
                return reply(400, {"error": "invalid non-streaming completion"})
            # Includes metadata: no caller-supplied credential echo may reach evidence.
            if _contains_secret([body, sitting_id, case_id], secret):
                return reply(400, {"error": "credential appears in request data"})
            request_bytes = canonical_bytes(completion)
            if len(request_bytes) > 65_536:
                return reply(413, {"error": "relay request too large"})
            parse_json(request_bytes)
            with lock:
                count = counts.get(sitting_id, 0)
                if count >= cap:
                    return reply(429, {"error": "sitting relay call cap reached"})
                counts[sitting_id] = count + 1  # Failed attempts also consume budget.
            parts = allowed[base_url]
            started_at, deadline = timestamp(), time.monotonic() + timeout
            cls = (
                http.client.HTTPSConnection
                if parts.scheme == "https"
                else http.client.HTTPConnection
            )
            connection = cls(parts.hostname, parts.port, timeout=timeout)
            # No proxy environment, redirects, caller headers, cookies or URL credentials.
            connection.request(
                "POST",
                (parts.path or "/v1") + "/chat/completions",
                body=request_bytes,
                headers={
                    "Authorization": "Bearer " + secret,
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            connection.sock.settimeout(max(0.001, deadline - time.monotonic()))
            upstream = connection.getresponse()
            if upstream.status != 200:
                return reply(502, {"error": "upstream did not return a completion"})
            chunks, length = [], 0
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return reply(504, {"error": "upstream time limit exceeded"})
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                chunk = upstream.read1(min(65_536, size + 1 - length))
                if not chunk:
                    break
                chunks.append(chunk)
                length += len(chunk)
                if length > size:
                    return reply(502, {"error": "upstream response too large"})
            if upstream.length not in (None, 0):
                return reply(502, {"error": "incomplete upstream response"})
            response_bytes = b"".join(chunks)
            response = parse_json(response_bytes)
            selected = {}
            for key, value in upstream.getheaders():
                key = key.lower()
                if key in PROVIDER_HEADERS:
                    if key in selected:
                        return reply(502, {"error": "ambiguous upstream headers"})
                    selected[key] = value
            if not isinstance(response, dict) or not isinstance(response.get("choices"), list):
                return reply(502, {"error": "invalid upstream completion"})
            entry = {
                "call_id": "call_" + uuid.uuid4().hex,
                "sitting_id": sitting_id,
                "case_id": case_id,
                "upstream_base_url": base_url,
                "upstream_host": parts.netloc,
                "requested_model": completion["model"],
                "response_model": response.get("model"),
                "provider_response_id": response.get("id"),
                "provider_headers": selected,
                "request_body_b64": base64.b64encode(request_bytes).decode("ascii"),
                "response_body_b64": base64.b64encode(response_bytes).decode("ascii"),
                "request_sha256": sha256_hex(request_bytes),
                "response_sha256": sha256_hex(response_bytes),
                "started_at": started_at,
                "finished_at": timestamp(),
            }
            if any(
                response.get(k) is not None and not isinstance(response[k], str)
                for k in ("model", "id")
            ):
                return reply(502, {"error": "invalid upstream metadata"})
            # Fail closed even for escaped JSON echoes; never persist a redacted fake body/hash.
            if secret.encode() in response_bytes or _contains_secret([response, entry], secret):
                return reply(502, {"error": "credential appears in upstream data"})
            with lock:
                config.relay_log.append(copy.deepcopy(entry))
            return reply(200, {"call_id": entry["call_id"], "completion": response})
        except Exception:
            # Do not stringify provider, socket or parser exceptions: they may contain secrets.
            return reply(502, {"error": "relay request failed"})
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass  # Closing errors must not expose provider exception text either.

    return [("POST", ROUTE, handle)]
