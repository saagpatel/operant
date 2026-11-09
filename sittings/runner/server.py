"""HTTP transport for hosted sittings.

Hook contracts (trusted operator code, never submitter-provided):
  binding_hook(record_without_binding, relay_log) -> binding dict
  receipt_hook(record) -> receipt dict | None
  review_hook(record, receipt) -> status str | None

Extra routes are (method, path_regex, handler) triples matched with fullmatch.
They live under /api/sittings/<id>/ and use the same authentication, TTL, JSON
limits and store lock as core routes. handler(request) receives a dict with
method, path, headers, body, sitting_id and sitting (mutable persisted state).
It returns a JSON dict, or {"status": int, "body": dict}. Changes to sitting
are saved on success. Relay handlers may append entries to sitting['relay_log'];
entries with case_id and call_id are associated with an accepted answer.
Run only one RunnerServer per data directory.
"""

from __future__ import annotations

import base64
import copy
import hmac
import json
import math
import random
import re
import secrets
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from sittings import manifest, scoring
from sittings.canonical import canonical_sha256, sha256_hex
from sittings.runner.store import SITTING_ID, SittingStore

MAX_BODY_BYTES = 64 * 1024
SITTING_PATH = re.compile(r"/api/sittings/(?P<id>sit_[a-z2-7]{26})/(?P<action>[^/]+)")


def unbound_binding(record: dict, relay_log: list[dict] | None) -> dict:
    evidence: dict[str, object] = {}
    return {
        "format": "operant.model-binding/v1",
        "level": "unbound",
        "claimed_model": record["claimed_model"],
        "observed_model": None,
        "binds": [],
        "does_not_bind": ["which model produced the answers"],
        "evidence": evidence,
        "evidence_sha256": canonical_sha256(evidence),
    }


def no_receipt(record: dict) -> None:
    return None


def no_review(record: dict, receipt: dict | None) -> None:
    return None


@dataclass
class RunnerConfig:
    data_dir: str | Path
    ttl_seconds: float = 2 * 60 * 60
    create_rate_limit: int = 10
    rate_window_seconds: float = 60 * 60
    max_open_sittings: int = 100
    allow_partial: bool = False
    binding_hook: Callable = unbound_binding
    receipt_hook: Callable = no_receipt
    review_hook: Callable = no_review
    extra_routes: list[tuple[str, str, Callable]] = field(default_factory=list)
    clock: Callable[[], float] = time.time

    def __post_init__(self):
        for name in ("ttl_seconds", "rate_window_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name in ("create_rate_limit", "max_open_sittings"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ValueError(f"{name} must be a positive integer")


class APIError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message


def timestamp(now: float) -> str:
    return (
        datetime.fromtimestamp(now, timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def printable(value, limit: int) -> bool:
    return isinstance(value, str) and 0 < len(value) <= limit and value.isprintable()


class RunnerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: RunnerConfig):
        self.config = config
        self.store = SittingStore(config.data_dir)
        self.case_map = scoring.load_corpus()
        self.prompts = {cid: scoring.prompt_for(case) for cid, case in self.case_map.items()}
        self.corpus = manifest.corpus_manifest()
        self.scorer = manifest.scorer_manifest()
        self.create_times: dict[str, deque] = {}
        self.extra_routes = [
            (method.upper(), re.compile(pattern), handler)
            for method, pattern, handler in config.extra_routes
        ]
        super().__init__(server_address, RunnerHandler)

    def create_sitting(self, body: dict, ip: str) -> dict:
        if not printable(body.get("agent_label"), 80):
            raise APIError(400, "agent_label must be 1-80 printable characters")
        if not printable(body.get("claimed_model"), 120):
            raise APIError(400, "claimed_model must be 1-120 printable characters")
        if body.get("consent_publish") is not True:
            raise APIError(400, "consent_publish must be true")
        now = self.config.clock()
        # Keep limiter memory bounded by successful creations in the current window.
        cutoff = now - self.config.rate_window_seconds
        for address in list(self.create_times):
            times = self.create_times[address]
            while times and times[0] <= cutoff:
                times.popleft()
            if not times:
                del self.create_times[address]
        times = self.create_times.setdefault(ip, deque())
        if len(times) >= self.config.create_rate_limit:
            raise APIError(429, "sitting creation rate limit exceeded")
        if self.store.open_count(now) >= self.config.max_open_sittings:
            raise APIError(503, "maximum open sittings reached")
        sitting_id = "sit_" + base64.b32encode(secrets.token_bytes(16)).decode(
            "ascii"
        ).lower().rstrip("=")
        token = secrets.token_urlsafe(32)
        order = list(self.case_map)
        random.Random(secrets.randbits(256)).shuffle(order)
        state = {
            "sitting_id": sitting_id,
            "token_sha256": sha256_hex(token.encode("utf-8")),
            "created_at": timestamp(now),
            "expires_at": now + self.config.ttl_seconds,
            "agent_label": body["agent_label"],
            "claimed_model": body["claimed_model"],
            "consent_publish": True,
            "corpus": self.corpus,
            "scorer": self.scorer,
            "case_order": order,
            "answers": [],
            "last_served": None,
            "relay_log": [],
        }
        self.store.create(state)
        times.append(now)
        return {
            "sitting_id": sitting_id,
            "token": token,
            "total_cases": len(order),
            "corpus_sha256": self.corpus["sha256"],
            "scorer_sha256": self.scorer["sha256"],
        }

    def authenticate(self, sitting_id: str, authorization: str) -> dict:
        try:
            state = self.store.load(sitting_id)
        except FileNotFoundError:
            raise APIError(404, "sitting not found") from None
        supplied = (
            authorization.removeprefix("Bearer ") if authorization.startswith("Bearer ") else ""
        )
        if not supplied or not hmac.compare_digest(
            sha256_hex(supplied.encode("utf-8")), state["token_sha256"]
        ):
            raise APIError(401, "invalid sitting token")
        if state["expires_at"] <= self.config.clock():
            raise APIError(410, "sitting expired")
        return state

    def check_bound_state(self, state: dict) -> None:
        if state["corpus"] != self.corpus or state["scorer"] != self.scorer:
            raise APIError(409, "bound corpus or scorer changed; start a new sitting")

    def next_case(self, state: dict) -> dict:
        if "result" in state or self.store.read_record(state["sitting_id"]) is not None:
            return {"done": True}
        self.check_bound_state(state)
        index = len(state["answers"])
        if index == len(state["case_order"]):
            return {"done": True}
        cid = state["case_order"][index]
        state["last_served"] = cid
        self.store.save(state)
        return {"done": False, "index": index, "case_id": cid, "prompt": self.prompts[cid]}

    def accept_answer(self, state: dict, body: dict) -> dict:
        if "result" in state or self.store.read_record(state["sitting_id"]) is not None:
            raise APIError(409, "sitting already finished")
        self.check_bound_state(state)
        cid, answer = body.get("case_id"), body.get("answer")
        if not isinstance(cid, str) or not isinstance(answer, str) or len(answer) > 20000:
            raise APIError(
                400, "case_id and answer must be strings; answer limit is 20000 characters"
            )
        if cid != state["last_served"] or any(row["case_id"] == cid for row in state["answers"]):
            raise APIError(409, "answer must be for the last served, unanswered case")
        state["answers"].append(
            {
                "case_id": cid,
                "prompt_sha256": sha256_hex(self.prompts[cid].encode("utf-8")),
                "answer": answer,
                "received_at": timestamp(self.config.clock()),
                "relay_call_ids": [
                    call["call_id"]
                    for call in state["relay_log"]
                    if call.get("case_id") == cid and isinstance(call.get("call_id"), str)
                ],
            }
        )
        state["last_served"] = None
        self.store.save(state)
        return {"accepted": True, "remaining": len(state["case_order"]) - len(state["answers"])}

    def finish(self, state: dict) -> dict:
        if "result" in state:
            return state["result"]
        self.check_bound_state(state)
        if len(state["answers"]) != len(state["case_order"]) and not self.config.allow_partial:
            raise APIError(409, "every case must be answered before finish")
        sid = state["sitting_id"]
        record = self.store.read_record(sid)
        if record is None:
            record = {
                key: copy.deepcopy(state[key])
                for key in (
                    "sitting_id",
                    "created_at",
                    "agent_label",
                    "claimed_model",
                    "corpus",
                    "scorer",
                    "case_order",
                    "answers",
                )
            }
            record.update(
                format="operant.sitting/v1",
                finished_at=timestamp(self.config.clock()),
                score=scoring.score_answers(self.case_map, record["answers"]),
            )
            record["model_binding"] = self.config.binding_hook(
                copy.deepcopy(record), copy.deepcopy(state["relay_log"])
            )
            self.store.write_record(sid, record)
        receipt = self.config.receipt_hook(copy.deepcopy(record))
        if receipt is not None:
            self.store.write_receipt(sid, receipt)
        review_status = self.config.review_hook(copy.deepcopy(record), copy.deepcopy(receipt))
        result = {
            "sitting_id": sid,
            "record": record,
            "receipt": receipt,
            "review_status": review_status if review_status is not None else "not_requested",
        }
        state["result"] = result
        self.store.save(state)
        return result


class RunnerHandler(BaseHTTPRequestHandler):
    server: RunnerServer

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def log_message(self, format, *args):
        # BaseHTTPRequestHandler can log raw request lines, including submitter text.
        pass

    def handle(self):
        try:
            super().handle()
        except (ConnectionError, TimeoutError):
            pass

    def send_json(self, status: int, body: dict, allow: str | None = None):
        data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if allow:
            self.send_header("Allow", allow)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def read_json(self) -> dict:
        lengths = self.headers.get_all("Content-Length", [])
        if self.headers.get("Transfer-Encoding") or len(lengths) != 1:
            raise APIError(400, "one Content-Length is required; transfer encoding is unsupported")
        if not lengths[0].isascii() or not lengths[0].isdigit():
            raise APIError(400, "invalid Content-Length")
        digits = lengths[0].lstrip("0") or "0"
        if len(digits) > 5:
            raise APIError(413, "request body exceeds 64 KiB")
        length = int(digits)
        if length > MAX_BODY_BYTES:
            raise APIError(413, "request body exceeds 64 KiB")
        if self.headers.get_content_type() != "application/json":
            raise APIError(415, "Content-Type must be application/json")
        raw = self.rfile.read(length)
        try:

            def reject_constant(value):
                raise ValueError("non-JSON constant")

            body = json.loads(raw.decode("utf-8"), parse_constant=reject_constant)
            # Reject lone surrogates even if JSON-escaped, before persisting UTF-8.
            json.dumps(body, ensure_ascii=False).encode("utf-8")
        except (UnicodeError, ValueError, RecursionError):
            raise APIError(400, "invalid UTF-8 JSON") from None
        if not isinstance(body, dict):
            raise APIError(400, "JSON body must be an object")
        return body

    def dispatch(self):
        try:
            path = urlsplit(self.path).path
            methods = set()
            action = None
            sid = None
            if path == "/api/health":
                methods.add("GET")
                action = "health"
            elif path == "/api/sittings":
                methods.add("POST")
                action = "create"
            match = SITTING_PATH.fullmatch(path)
            if match:
                sid = match["id"]
                if match["action"] in ("next", "answers", "finish"):
                    action = match["action"]
                    methods.add("GET" if action == "next" else "POST")
            extra = None
            for method, pattern, handler in self.server.extra_routes:
                if pattern.fullmatch(path):
                    methods.add(method)
                    if method == self.command:
                        extra = handler
            if not methods:
                raise APIError(404, "route not found")
            if self.command not in methods:
                self.send_json(405, {"error": "method not allowed"}, ", ".join(sorted(methods)))
                return
            has_body = self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding")
            body = self.read_json() if self.command != "GET" or has_body else {}
            with self.server.store.lock:
                if action == "health":
                    response = {
                        "ok": True,
                        "corpus_sha256": self.server.corpus["sha256"],
                        "scorer_sha256": self.server.scorer["sha256"],
                    }
                elif action == "create":
                    self.send_json(201, self.server.create_sitting(body, self.client_address[0]))
                    return
                else:
                    if sid is None or not SITTING_ID.fullmatch(sid):
                        raise APIError(401, "extra route must identify a sitting")
                    state = self.server.authenticate(sid, self.headers.get("Authorization", ""))
                    if extra is not None:
                        self.server.check_bound_state(state)
                        response = extra(
                            {
                                "method": self.command,
                                "path": path,
                                "headers": dict(self.headers),
                                "body": body,
                                "sitting_id": sid,
                                "sitting": state,
                            }
                        )
                        status = response.get("status", 200) if "body" in response else 200
                        if status < 400:
                            self.server.store.save(state)
                        self.send_json(status, response["body"] if "body" in response else response)
                        return
                    if action == "next":
                        response = self.server.next_case(state)
                    elif action == "answers":
                        response = self.server.accept_answer(state, body)
                    else:
                        response = self.server.finish(state)
                self.send_json(200, response)
        except APIError as exc:
            self.send_json(exc.status, {"error": exc.message})
        except Exception:
            # Hook and filesystem failures must not disclose tokens, answers or secrets.
            self.send_json(500, {"error": "internal runner error"})

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = do_OPTIONS = dispatch

    def __getattr__(self, name):
        # BaseHTTPRequestHandler otherwise emits HTML 501 for arbitrary methods.
        if name.startswith("do_"):
            return self.dispatch
        raise AttributeError(name)
