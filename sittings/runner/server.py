"""HTTP transport for hosted sittings.

Hook contracts (trusted operator code, never submitter-provided):
  binding_hook(record_without_binding, relay_log) -> binding dict
  receipt_hook(record) -> receipt dict | None
  review_hook(record, receipt) -> status str | None

Run only one RunnerServer per data directory.
"""

from __future__ import annotations

import base64
import copy
import hmac
import ipaddress
import json
import math
import random
import re
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from sittings import manifest, scoring
from sittings.canonical import canonical_bytes, sha256_hex
from sittings.runner.store import SITTING_ID, SittingStore

MAX_BODY_BYTES = 64 * 1024
SITTING_PATH = re.compile(r"/api/sittings/(?P<id>sit_[a-z2-7]{26})/(?P<action>[^/]+)")
MAX_STORED_BYTES = 256 * 1024 * 1024


def unbound_binding(record: dict, relay_log: list[dict] | None) -> dict:
    from sittings.model_binding import build_binding

    return build_binding(record, relay_log)


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
    max_stored_bytes: int = MAX_STORED_BYTES
    max_request_concurrency: int = 32
    allow_partial: bool = False
    behind_tls_proxy: bool = False
    relay_providers: dict[str, str] = field(default_factory=dict)
    relay_transport: Callable | None = None
    binding_hook: Callable = unbound_binding
    receipt_hook: Callable = no_receipt
    review_hook: Callable = no_review
    clock: Callable[[], float] = time.time

    def __post_init__(self):
        for name in ("ttl_seconds", "rate_window_seconds"):
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        for name in ("create_rate_limit", "max_open_sittings", "max_stored_bytes", "max_request_concurrency"):
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


def secret_occurs(value, secret: str) -> bool:
    if isinstance(value, str):
        return secret in value
    if isinstance(value, dict):
        return any(secret_occurs(k, secret) or secret_occurs(v, secret) for k, v in value.items())
    if isinstance(value, list):
        return any(secret_occurs(item, secret) for item in value)
    return False


class RunnerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int], config: RunnerConfig):
        host = server_address[0]
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback and not config.behind_tls_proxy:
            raise ValueError("non-loopback binds require --behind-tls-proxy")
        self.config = config
        from sittings.model_binding.relay import parse_base_url

        if any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name)
            for name in config.relay_providers
        ):
            raise ValueError("invalid relay provider name")
        for url in config.relay_providers.values():
            parse_base_url(url)
        self.store = SittingStore(config.data_dir)
        self.corpus = manifest.corpus_manifest()
        self.scorer = manifest.scorer_manifest()
        self.case_map = scoring.load_corpus()
        self.prompts = {cid: scoring.prompt_for(case) for cid, case in self.case_map.items()}
        if manifest.corpus_manifest() != self.corpus or manifest.scorer_manifest() != self.scorer:
            raise RuntimeError("bound files changed during runner startup")
        self.bound_files = (self.corpus, self.scorer)
        self.relay_keys: dict[str, str] = {}
        self.request_slots = threading.BoundedSemaphore(config.max_request_concurrency)
        self.create_times: dict[str, deque] = {}
        super().__init__(server_address, RunnerHandler)

    def process_request(self, request, client_address):
        if not self.request_slots.acquire(blocking=False):
            try:
                request.sendall(
                    b"HTTP/1.1 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                )
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.request_slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.request_slots.release()

    def server_close(self):
        self.relay_keys.clear()
        super().server_close()

    def service_actions(self):
        self.cleanup_expired_keys()
        super().service_actions()

    def sitting_lock(self, sitting_id: str):
        return self.store.lock_for(sitting_id)

    def cleanup_expired_keys(self):
        now_ms = int(self.config.clock() * 1000)
        for sid in tuple(self.relay_keys):
            try:
                state = self.store.load(sid)
            except (FileNotFoundError, OSError, ValueError):
                self.relay_keys.pop(sid, None)
            else:
                if state["expires_at_ms"] <= now_ms:
                    self.relay_keys.pop(sid, None)

    def create_sitting(self, body: dict, ip: str) -> dict:
        self.cleanup_expired_keys()
        if not printable(body.get("agent_label"), 80):
            raise APIError(400, "agent_label must be 1-80 printable characters")
        if not printable(body.get("claimed_model"), 120):
            raise APIError(400, "claimed_model must be 1-120 printable characters")
        if body.get("consent_publish") is not True:
            raise APIError(400, "consent_publish must be true")
        relay = body.get("relay")
        if relay is not None:
            if not isinstance(relay, dict) or set(relay) != {"provider", "model", "api_key"}:
                raise APIError(400, "relay must contain provider, model, and api_key")
            provider, model, api_key = relay["provider"], relay["model"], relay["api_key"]
            if (
                not isinstance(provider, str) or provider not in self.config.relay_providers
                or not printable(model, 120) or not isinstance(api_key, str)
                or not 1 <= len(api_key) <= 4096
                or any(ord(char) < 33 or ord(char) > 126 for char in api_key)
            ):
                raise APIError(400, "invalid relay configuration")
            if secret_occurs(
                {"agent_label": body["agent_label"], "claimed_model": body["claimed_model"],
                 "provider": provider, "model": model}, api_key
            ):
                raise APIError(400, "provider credential appears in public metadata")
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
            "expires_at_ms": int((now + self.config.ttl_seconds) * 1000),
            "agent_label": body["agent_label"],
            "claimed_model": body["claimed_model"],
            "consent_publish": True,
            "corpus": self.corpus,
            "scorer": self.scorer,
            "case_order": order,
            "answers": [],
            "last_served": None,
            "relay_log": [],
            "mode": "host-relayed" if relay is not None else "unbound",
            "relay_provider": relay["provider"] if relay is not None else None,
            "relay_model": relay["model"] if relay is not None else None,
        }
        if self.store.stored_bytes() + len(canonical_bytes(state)) > self.config.max_stored_bytes:
            raise APIError(503, "maximum stored sitting data reached")
        self.store.create(state)
        # Register the key only after the sitting exists on disk: cleanup_expired_keys runs
        # concurrently and drops keys whose sitting it cannot load.
        if relay is not None:
            self.relay_keys[sitting_id] = api_key
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
        if state["expires_at_ms"] <= int(self.config.clock() * 1000):
            self.relay_keys.pop(sitting_id, None)
            raise APIError(410, "sitting expired")
        return state

    def check_bound_state(self, state: dict) -> None:
        # Reject state corruption while continuing to use the immutable startup snapshot.
        if state.get("corpus") != self.corpus or state.get("scorer") != self.scorer:
            raise APIError(409, "sitting snapshot metadata changed")

    def stale_bound_files(self) -> bool:
        try:
            return manifest.corpus_manifest() != self.corpus or manifest.scorer_manifest() != self.scorer
        except OSError:
            return True

    def ensure_capacity(self, state: dict) -> None:
        path = self.store.directory(state["sitting_id"]) / "state.json"
        previous = path.stat().st_size if path.exists() else 0
        projected = self.store.stored_bytes() - previous + len(canonical_bytes(state))
        if projected > self.config.max_stored_bytes:
            raise APIError(503, "maximum stored sitting data reached")

    def save_state(self, state: dict) -> None:
        with self.store.lock:
            self.ensure_capacity(state)
            self.store.save(state)

    def run_relay(self, state: dict) -> dict:
        if state["mode"] != "host-relayed":
            raise APIError(409, "sitting is not host-relayed")
        sid = state["sitting_id"]
        if state.get("result") is not None:
            return state["result"]
        self.check_bound_state(state)
        if len(state["answers"]) == len(state["case_order"]):
            # Every provider answer is already persisted; finalization needs no credential,
            # so a retry after a transient sealing or review failure must not strand the sitting.
            self.relay_keys.pop(sid, None)
            return self.finish(state)
        key = self.relay_keys.get(sid)
        if key is None:
            raise APIError(409, "relay credential is no longer available; create a new sitting")
        from sittings.model_binding.relay import host_call

        provider = state["relay_provider"]
        base_url = self.config.relay_providers[provider]
        transport = self.config.relay_transport or host_call
        try:
            for cid in state["case_order"][len(state["answers"]):]:
                prompt = self.prompts[cid]
                result = transport(provider, base_url, state["relay_model"], key, sid, cid, prompt)
                if (
                    not isinstance(result, dict)
                    or not isinstance(result.get("completion_text"), str)
                    or len(result["completion_text"]) > 20000
                ):
                    raise APIError(502, "provider returned an invalid completion")
                completion = result["completion_text"]
                call = copy.deepcopy(result)
                if call.get("case_id") != cid or call.get("sitting_id") != sid:
                    raise APIError(502, "provider relay evidence did not match the sitting")
                state["relay_log"].append(call)
                state["answers"].append({
                    "case_id": cid,
                    "prompt_sha256": sha256_hex(prompt.encode("utf-8")),
                    "answer": completion,
                    "received_at": timestamp(self.config.clock()),
                    "relay_call_ids": [call["call_id"]],
                })
                state["last_served"] = None
                self.save_state(state)
            return self.finish(state)
        except APIError:
            raise
        except Exception:
            # Provider exceptions may contain credentials or request material.
            raise APIError(502, "host relay request failed") from None
        finally:
            self.relay_keys.pop(sid, None)

    def next_case(self, state: dict) -> dict:
        if state["mode"] == "host-relayed":
            raise APIError(409, "host-relayed sittings run at the runner")
        if "result" in state or self.store.read_record(state["sitting_id"]) is not None:
            return {"done": True}
        self.check_bound_state(state)
        index = len(state["answers"])
        if index == len(state["case_order"]):
            return {"done": True}
        cid = state["case_order"][index]
        state["last_served"] = cid
        self.save_state(state)
        return {"done": False, "index": index, "case_id": cid, "prompt": self.prompts[cid]}

    def accept_answer(self, state: dict, body: dict) -> dict:
        if state["mode"] == "host-relayed":
            raise APIError(409, "host-relayed sittings run at the runner")
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
        self.save_state(state)
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
            if self.stale_bound_files():
                reasons = record["score"].setdefault("not_publishable_reasons", [])
                if "bound files changed during sitting" not in reasons:
                    reasons.append("bound files changed during sitting")
                record["score"]["publishable"] = False
            record["model_binding"] = self.config.binding_hook(
                copy.deepcopy(record), copy.deepcopy(state["relay_log"])
            )
            if state["mode"] == "host-relayed" and state["relay_log"]:
                reasons = record["score"].setdefault("not_publishable_reasons", [])
                binding = record["model_binding"]
                observed = binding.get("observed_model")
                if observed != record["claimed_model"]:
                    reason = (
                        "claimed model does not match observed model"
                        if observed is not None
                        else "provider did not report an observed model"
                    )
                    if reason not in reasons:
                        reasons.append(reason)
                    record["score"]["publishable"] = False
                if binding.get("level") != "host-relayed":
                    reason = "host relay did not bind every answer to one consistent model"
                    if reason not in reasons:
                        reasons.append(reason)
                    record["score"]["publishable"] = False
            with self.store.lock:
                if self.store.stored_bytes() + len(canonical_bytes(record)) > self.config.max_stored_bytes:
                    raise APIError(503, "maximum stored sitting data reached")
                self.store.write_record(sid, record)
        receipt = self.store.read_receipt(sid)
        if receipt is None:
            receipt = self.config.receipt_hook(copy.deepcopy(record))
            if receipt is not None:
                with self.store.lock:
                    if self.store.stored_bytes() + len(canonical_bytes(receipt)) > self.config.max_stored_bytes:
                        raise APIError(503, "maximum stored sitting data reached")
                    self.store.write_receipt(sid, receipt)
        # Include the queue's duplicate record/receipt and finished state before
        # the review hook mutates the queue. This avoids a capacity failure after
        # an item has entered review.
        projected_state = copy.deepcopy(state)
        projected_state["result"] = {
            "sitting_id": sid, "record": record, "receipt": receipt,
            "review_status": "pending" if receipt is not None else "rejected",
        }
        with self.store.lock:
            old_state = self.store.directory(sid) / "state.json"
            existing_state_bytes = old_state.stat().st_size if old_state.exists() else 0
            pending_bytes = (
                len(canonical_bytes(record)) * 2
                + (len(canonical_bytes(receipt)) * 2 if receipt is not None else 0)
                + len(canonical_bytes(projected_state)) - existing_state_bytes + 2048
            )
            if self.store.stored_bytes() + pending_bytes > self.config.max_stored_bytes:
                raise APIError(503, "maximum stored sitting data reached")
            review_status = self.config.review_hook(copy.deepcopy(record), copy.deepcopy(receipt))
        result = {
            "sitting_id": sid,
            "record": record,
            "receipt": receipt,
            "review_status": review_status if review_status is not None else "not_requested",
        }
        state["result"] = result
        self.save_state(state)
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
            # A disconnected or idle peer needs no response; base setup/finish closes its streams.
            # Do not log exception text, which could include submitter data or credentials.
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
                if match["action"] in ("next", "answers", "finish", "run"):
                    action = match["action"]
                    methods.add("GET" if action == "next" else "POST")
            if not methods:
                raise APIError(404, "route not found")
            if self.command not in methods:
                self.send_json(405, {"error": "method not allowed"}, ", ".join(sorted(methods)))
                return
            has_body = self.headers.get("Content-Length") or self.headers.get("Transfer-Encoding")
            body = self.read_json() if self.command != "GET" or has_body else {}
            if action == "health":
                response = {
                    "ok": True,
                    "corpus_sha256": self.server.corpus["sha256"],
                    "scorer_sha256": self.server.scorer["sha256"],
                }
            elif action == "create":
                with self.server.store.lock:
                    self.send_json(201, self.server.create_sitting(body, self.client_address[0]))
                    return
            else:
                if sid is None or not SITTING_ID.fullmatch(sid):
                    raise APIError(401, "invalid sitting id")
                if not (self.server.store.directory(sid) / "state.json").is_file():
                    raise APIError(404, "sitting not found")
                with self.server.sitting_lock(sid):
                    state = self.server.authenticate(sid, self.headers.get("Authorization", ""))
                    if action == "next":
                        response = self.server.next_case(state)
                    elif action == "answers":
                        response = self.server.accept_answer(state, body)
                    elif action == "run":
                        response = self.server.run_relay(state)
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
