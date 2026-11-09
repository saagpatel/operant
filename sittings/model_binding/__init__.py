"""Offline, internally checkable provider reports; never weight identity attestation."""

from __future__ import annotations

import base64
import copy
import re
from datetime import datetime

from sittings.canonical import canonical_sha256, sha256_hex

from .relay import PROVIDER_HEADERS, RelayConfig, make_relay_routes, parse_base_url, parse_json

__all__ = ["RelayConfig", "make_relay_routes", "build_binding", "verify_binding"]

FORMAT = "operant.model-binding/v1"
COMMON_LIMITS = [
    "The upstream provider is honest about what it served; no weights or provider signature are verified.",
    "Anything about the submitter's setup beyond the relayed calls.",
    "Causal authorship of the answers, use of the case prompt, or absence of other models or human help.",
    "Authenticity, freshness, or immutable history of this unsigned evidence; hashes check consistency only.",
]
POLICY = {
    "unbound": (
        [
            "The record and claimed model are integrity-linked to this binding; no relay calls are evidenced."
        ],
        ["Any provider observation or linkage of answers to a model."] + COMMON_LIMITS,
    ),
    "relay-observed": (
        [
            "The evidence records provider-reported metadata and bytes for the attributed relay calls.",
            "Recorded answer links, model agreement, and claim comparisons are reproducible offline.",
        ],
        ["Complete answer linkage to one consistently reported model."] + COMMON_LIMITS,
    ),
    "relay-linked": (
        [
            "Every scheduled case has an answer exactly equal to an assistant completion in a declared relay call for that sitting and case.",
            "Every evidenced relay call reports the same nonempty model, including any recorded model header.",
            "The exact comparison between the claimed and reported model is recorded, including mismatches.",
        ],
        ["Provider attestation or proof that the linked output caused the submitted answer."]
        + COMMON_LIMITS,
    ),
}
CALL_FIELDS = {
    "call_id",
    "sitting_id",
    "case_id",
    "upstream_base_url",
    "upstream_host",
    "requested_model",
    "response_model",
    "provider_response_id",
    "provider_headers",
    "request_body_b64",
    "response_body_b64",
    "request_sha256",
    "response_sha256",
    "started_at",
    "finished_at",
}


def _bare_record(record):
    return {key: value for key, value in record.items() if key != "model_binding"}


def _instant(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("expected UTC timestamp")
    return datetime.fromisoformat(value[:-1] + "+00:00")


def _call_findings(call, record, prefix):
    findings = []

    def check(field, ok, detail):
        findings.append({"field": prefix + "." + field, "ok": bool(ok), "detail": detail})

    if not isinstance(call, dict):
        check("shape", False, "Expected a relay evidence object.")
        return findings
    check("shape", set(call) == CALL_FIELDS, "Exact relay evidence schema.")
    check(
        "call_id",
        isinstance(call.get("call_id"), str)
        and re.fullmatch(r"call_[0-9a-f]{32}", call["call_id"]),
        "Runner-generated call id.",
    )
    check("sitting_id", call.get("sitting_id") == record.get("sitting_id"), "Same sitting.")
    check("case_id", call.get("case_id") in record.get("case_order", []), "Scheduled case.")
    try:
        parts = parse_base_url(call["upstream_base_url"])
        check("upstream_host", parts.netloc == call.get("upstream_host"), "Host matches base URL.")
        check(
            "upstream_base_url",
            True,
            "Syntactically valid base; historical allowlist is not attested.",
        )
    except (KeyError, TypeError, ValueError):
        check("upstream_base_url", False, "Invalid base URL.")
    for key in ("response_model", "provider_response_id"):
        check(
            key, call.get(key) is None or isinstance(call.get(key), str), "String or absent report."
        )
    headers = call.get("provider_headers")
    check(
        "provider_headers",
        isinstance(headers, dict)
        and set(headers) <= PROVIDER_HEADERS
        and all(isinstance(v, str) for v in headers.values()),
        "Only selected provider headers.",
    )
    for kind in ("request", "response"):
        try:
            raw = base64.b64decode(call[kind + "_body_b64"], validate=True)
            check(
                kind + "_body_b64",
                base64.b64encode(raw).decode("ascii") == call[kind + "_body_b64"],
                "Canonical base64 body bytes.",
            )
            check(
                kind + "_sha256",
                sha256_hex(raw) == call.get(kind + "_sha256"),
                "SHA-256 of decoded body bytes.",
            )
            body = parse_json(raw)
            if kind == "request":
                check(
                    "requested_model",
                    isinstance(body["model"], str)
                    and bool(body["model"])
                    and body["model"] == call.get("requested_model"),
                    "Requested model matches body.",
                )
                check(
                    "request_body_b64",
                    isinstance(body["messages"], list)
                    and body.get("stream", False) is False
                    and len(raw) <= 65_536,
                    "Non-streaming completion request.",
                )
            else:
                check("response_body_b64", isinstance(body["choices"], list), "Completion choices.")
                check(
                    "response_model",
                    body.get("model") == call.get("response_model"),
                    "Reported model matches response body.",
                )
                check(
                    "provider_response_id",
                    body.get("id") == call.get("provider_response_id"),
                    "Provider response id matches response body.",
                )
        except (KeyError, TypeError, ValueError, UnicodeError):
            check(kind + "_body_b64", False, "Invalid body encoding or JSON structure.")
    try:
        created, finished = _instant(record["created_at"]), _instant(record["finished_at"])
        start, end = _instant(call["started_at"]), _instant(call["finished_at"])
        check("started_at", created <= start <= end <= finished, "Call inside sitting interval.")
        check("finished_at", created <= start <= end <= finished, "Call inside sitting interval.")
    except (KeyError, TypeError, ValueError):
        check("started_at", False, "Invalid sitting or call timestamp.")
        check("finished_at", False, "Invalid sitting or call timestamp.")
    return findings


def _derive(record, calls):
    models = set()
    complete = bool(calls)
    by_id = {}
    for call in calls:
        by_id[call["call_id"]] = call
        model = call["response_model"]
        if not model:
            complete = False
        else:
            models.add(model)
        header = call["provider_headers"].get("openai-model")
        if header is not None:
            if not header:
                complete = False
            else:
                models.add(header)
    observed = next(iter(models)) if len(models) == 1 else None
    claim_status = (
        "mixed"
        if len(models) > 1
        else "unknown"
        if not complete
        else "match"
        if observed == record["claimed_model"]
        else "mismatch"
    )
    links = []
    for answer in record["answers"]:
        matched = []
        for call_id in answer["relay_call_ids"]:
            call = by_id.get(call_id)
            if call is None or call["case_id"] != answer["case_id"]:
                continue
            body = parse_json(base64.b64decode(call["response_body_b64"]))
            if any(
                isinstance(choice, dict)
                and isinstance(choice.get("message"), dict)
                and choice["message"].get("role") == "assistant"
                and choice["message"].get("content") == answer["answer"]
                and bool(answer["answer"])
                for choice in body["choices"]
            ):
                matched.append(call_id)
        links.append(
            {
                "case_id": answer["case_id"],
                "answer_sha256": sha256_hex(answer["answer"].encode("utf-8")),
                "declared_call_ids": copy.deepcopy(answer["relay_call_ids"]),
                "matched_call_ids": matched,
            }
        )
    all_linked = (
        bool(record["case_order"])
        and len(links) == len(record["case_order"])
        and {v["case_id"] for v in links} == set(record["case_order"])
        and all(v["matched_call_ids"] for v in links)
    )
    level = (
        "unbound"
        if not calls
        else "relay-linked"
        if all_linked and complete and len(models) == 1
        else "relay-observed"
    )
    evidence = {
        "record_sha256": canonical_sha256(_bare_record(record)),
        "calls": copy.deepcopy(calls),
        "answer_links": links,
        "observed_models": sorted(models),
        "model_reports_complete": complete,
        "claim_status": claim_status,
    }
    binds, limits = POLICY[level]
    return {
        "format": FORMAT,
        "level": level,
        "claimed_model": record["claimed_model"],
        "observed_model": observed,
        "binds": list(binds),
        "does_not_bind": list(limits),
        "evidence": evidence,
        "evidence_sha256": canonical_sha256(evidence),
    }


def _validate_record(record):
    # Validate only the inputs this packet consumes; scoring belongs to packet 01.
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("claimed_model"), str)
        or not isinstance(record.get("case_order"), list)
        or not all(isinstance(v, str) for v in record["case_order"])
        or len(set(record["case_order"])) != len(record["case_order"])
        or not isinstance(record.get("answers"), list)
    ):
        raise ValueError("invalid binding record")
    seen = set()
    for answer in record["answers"]:
        if (
            not isinstance(answer, dict)
            or not isinstance(answer.get("case_id"), str)
            or answer["case_id"] not in record["case_order"]
            or answer["case_id"] in seen
            or not isinstance(answer.get("answer"), str)
            or not isinstance(answer.get("relay_call_ids"), list)
            or not all(isinstance(v, str) for v in answer["relay_call_ids"])
            or len(set(answer["relay_call_ids"])) != len(answer["relay_call_ids"])
        ):
            raise ValueError("invalid binding answer")
        seen.add(answer["case_id"])


def build_binding(record_without_binding: dict, relay_log: list[dict] | None) -> dict:
    """Build from this sitting's successful runner-owned log; invalid evidence fails closed."""
    _validate_record(record_without_binding)
    calls = [] if relay_log is None else copy.deepcopy(relay_log)
    if not isinstance(calls, list):
        raise ValueError("invalid relay evidence")
    seen = set()
    for index, call in enumerate(calls):
        findings = _call_findings(call, record_without_binding, f"evidence.calls[{index}]")
        if any(not f["ok"] for f in findings) or call["call_id"] in seen:
            raise ValueError("invalid relay evidence")
        seen.add(call["call_id"])
    return _derive(record_without_binding, calls)


def _compare(expected, actual, path, findings, integrity_ok=True):
    """Report leaf paths; a digest mismatch cannot honestly localize the changed leaf."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            child = path + "." + key
            if key not in expected or key not in actual:
                findings.append(
                    {"field": child, "ok": False, "detail": "Unexpected or missing field."}
                )
            else:
                _compare(expected[key], actual[key], child, findings, integrity_ok)
    elif isinstance(expected, list) and isinstance(actual, list) and expected and actual:
        for index in range(max(len(expected), len(actual))):
            child = f"{path}[{index}]"
            if index >= min(len(expected), len(actual)):
                findings.append(
                    {"field": child, "ok": False, "detail": "Unexpected or missing item."}
                )
            else:
                _compare(expected[index], actual[index], child, findings, integrity_ok)
    else:
        same = type(expected) is type(actual) and expected == actual
        findings.append(
            {
                "field": path,
                "ok": same and integrity_ok,
                "detail": "Matches recomputed value."
                if same and integrity_ok
                else "Evidence digest mismatch; covered field cannot be trusted (not localized)."
                if same and not integrity_ok
                else "Does not match recomputed value.",
            }
        )


def verify_binding(binding: dict, record: dict) -> list[dict]:
    """Check schema, all hashes, derived links/model comparisons and fixed claim text.

    This is a consistency check. Authentication requires packet 03's external seal.
    """
    findings = []
    try:
        _validate_record(record)
        evidence = binding["evidence"]
        digest_ok = canonical_sha256(evidence) == binding.get("evidence_sha256")
        findings.append(
            {
                "field": "evidence_sha256",
                "ok": digest_ok,
                "detail": "Canonical SHA-256 of evidence.",
            }
        )
        if not digest_ok:
            _compare(evidence, evidence, "evidence", findings, integrity_ok=False)
        calls = evidence["calls"]
        if not isinstance(calls, list):
            raise ValueError("invalid calls")
        for index, call in enumerate(calls):
            findings.extend(_call_findings(call, record, f"evidence.calls[{index}]"))
        # Rebuild after structural checks: invalid body evidence cannot be promoted.
        expected = build_binding(record, calls)
        for key in sorted(set(expected) | set(binding)):
            if key not in expected or key not in binding:
                findings.append(
                    {"field": key, "ok": False, "detail": "Unexpected or missing field."}
                )
            else:
                _compare(
                    expected[key],
                    binding[key],
                    key,
                    findings,
                    integrity_ok=digest_ok if key == "evidence" else True,
                )
    except (
        KeyError,
        TypeError,
        ValueError,
        UnicodeError,
        AttributeError,
        OverflowError,
        RecursionError,
    ):
        findings.append(
            {
                "field": "evidence",
                "ok": False,
                "detail": "Malformed binding, record or relay evidence.",
            }
        )
    return findings
