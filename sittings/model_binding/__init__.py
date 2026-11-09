"""Recheckable host observations, with exactly two v1 binding levels."""
from __future__ import annotations

import base64
import copy
import re
from datetime import datetime

from sittings.canonical import canonical_sha256, sha256_hex
from .relay import parse_base_url, parse_json, request_body

__all__ = ["build_binding", "verify_binding"]
FORMAT = "operant.model-binding/v1"
COMMON_LIMITS = [
    "The provider's honesty about what it served; no provider signature or weights are checked.",
    "That the answer did not come from a lookup of the public corpus, including its public expected decisions.",
    "Anything about the submitter's setup outside relayed calls.",
]
POLICY = {
    "unbound": (["The record includes a submitter-supplied model claim only."],
                ["Which model produced the answers."] + COMMON_LIMITS),
    "host-relayed": ([
        "For every case the host constructed the final request with the case prompt, sitting id and case id and sent it to an operator-allowlisted provider.",
        "Every recorded answer equals that call's completion text exactly.",
        "The provider reported one consistent model; the claimed and observed model comparison is recorded.",
    ], COMMON_LIMITS),
}
CALL_FIELDS = {
    "call_id", "provider", "sitting_id", "case_id", "upstream_base_url", "requested_model",
    "response_model", "response_header_model", "completion_text", "request_body_b64",
    "response_body_b64", "request_sha256", "response_sha256", "started_at", "finished_at",
}


def _validate_call(call, record):
    if not isinstance(call, dict) or set(call) != CALL_FIELDS:
        raise ValueError("invalid relay evidence schema")
    if not re.fullmatch(r"call_[0-9a-f]{32}", call["call_id"]):
        raise ValueError("invalid relay call id")
    if call["sitting_id"] != record["sitting_id"] or call["case_id"] not in record["case_order"]:
        raise ValueError("invalid relay context")
    parse_base_url(call["upstream_base_url"])
    if not all(isinstance(call[k], str) and call[k]
               for k in ("provider", "requested_model", "started_at", "finished_at")):
        raise ValueError("invalid relay metadata")
    for key in ("response_model", "response_header_model"):
        if call[key] is not None and not isinstance(call[key], str):
            raise ValueError("invalid observed model")
    for kind in ("request", "response"):
        raw = base64.b64decode(call[kind + "_body_b64"], validate=True)
        if sha256_hex(raw) != call[kind + "_sha256"]:
            raise ValueError("relay body digest mismatch")
    request_raw = base64.b64decode(call["request_body_b64"], validate=True)
    request = parse_json(request_raw)
    prompt = request["messages"][0]["content"]
    if not isinstance(prompt, str) or request != request_body(
            call["requested_model"], record["sitting_id"], call["case_id"], prompt):
        raise ValueError("request is not host-built")
    response = parse_json(base64.b64decode(call["response_body_b64"], validate=True))
    if (len(response["choices"]) != 1
            or response["choices"][0]["message"]["content"] != call["completion_text"]
            or response.get("model") != call["response_model"]
            or not isinstance(call["completion_text"], str)):
        raise ValueError("completion mismatch")
    instants = []
    for field in ("started_at", "finished_at"):
        if not call[field].endswith("Z"):
            raise ValueError("invalid relay timestamp")
        instants.append(datetime.fromisoformat(call[field][:-1] + "+00:00"))
    if instants[0] > instants[1]:
        raise ValueError("invalid relay time order")
    return sha256_hex(prompt.encode("utf-8"))


def build_binding(record_without_binding: dict, relay_log: list[dict] | None) -> dict:
    record = record_without_binding
    calls = copy.deepcopy(relay_log or [])
    if not isinstance(calls, list):
        raise ValueError("invalid relay evidence")
    seen = set()
    linked = len(calls) == len(record.get("case_order", [])) and bool(calls)
    models = set()
    requests = set()
    cases = set()
    for call in calls:
        try:
            prompt_hash = _validate_call(call, record)
            if call["call_id"] in seen or call["case_id"] in cases:
                raise ValueError("duplicate relay call")
            seen.add(call["call_id"])
            cases.add(call["case_id"])
            requests.add((call["provider"], call["upstream_base_url"], call["requested_model"]))
            model = call["response_model"]
            if model:
                models.add(model)
            else:
                linked = False
            if call["response_header_model"] not in (None, model):
                linked = False
            answers = [a for a in record["answers"] if a["case_id"] == call["case_id"]]
            linked = linked and len(answers) == 1 and (
                answers[0]["answer"] == call["completion_text"]
                and answers[0]["prompt_sha256"] == prompt_hash
                and answers[0]["relay_call_ids"] == [call["call_id"]])
        except Exception:
            raise ValueError("invalid relay evidence") from None
    linked = linked and len(models) == 1 and len(requests) == 1 and (
        len(record["answers"]) == len(cases) and cases == set(record["case_order"]))
    level = "host-relayed" if linked else "unbound"
    observed = next(iter(models)) if len(models) == 1 else None
    evidence = {"calls": calls, "claim_status": "unknown" if observed is None else (
        "match" if record["claimed_model"] == observed else "mismatch")}
    binds, limits = POLICY[level]
    return {"format": FORMAT, "level": level, "claimed_model": record["claimed_model"],
            "observed_model": observed, "binds": list(binds), "does_not_bind": list(limits),
            "evidence": evidence, "evidence_sha256": canonical_sha256(evidence)}


def verify_binding(binding: dict, record: dict) -> list[dict]:
    try:
        expected = build_binding(record, binding["evidence"]["calls"])
        findings = []
        for field in expected:
            findings.append({"field": "model_binding." + field,
                             "ok": binding.get(field) == expected[field],
                             "detail": "matches recomputed host observation"})
        findings.append({"field": "model_binding.schema", "ok": set(binding) == set(expected),
                         "detail": "exact v1 schema"})
        return findings
    except Exception:
        return [{"field": "model_binding", "ok": False, "detail": "invalid binding evidence"}]
