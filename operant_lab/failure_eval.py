"""Fail-closed admission for failures proposed as OPERANT regression evaluations.

Candidate-authored claims are never sufficient for admission. Exact local fixture,
reproduction-receipt, publication-review, and separately supplied human-authority
bytes must all validate before ``ADMITTED`` can be emitted.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from operant_lab.public_contract import FORBIDDEN_PUBLIC_KEYS, FORBIDDEN_PUBLIC_TEXT_PATTERNS

SCHEMA = "operant-failure-eval-candidate.v1"
REPRODUCTION_SCHEMA = "operant-failure-reproduction-receipt.v1"
PUBLICATION_REVIEW_SCHEMA = "operant-failure-publication-review.v1"
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
REPO_ROOT = Path(__file__).resolve().parent.parent


class CandidateAdmissionError(ValueError):
    """A candidate is incomplete, malformed, duplicated, or not externally admitted."""


def _require_exact_keys(value: dict[str, Any], required: set[str], where: str) -> None:
    missing = sorted(required - value.keys())
    extra = sorted(value.keys() - required)
    if missing or extra:
        raise CandidateAdmissionError(f"{where} keys invalid: missing={missing}, extra={extra}")


def _required_text(value: Any, where: str, *, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateAdmissionError(f"{where} must be a non-empty string")
    text = value.strip()
    if len(text) > maximum or any(ord(character) < 32 for character in text):
        raise CandidateAdmissionError(
            f"{where} must be single-line text no longer than {maximum} characters"
        )
    return text


def _sha256(value: Any, where: str) -> str:
    text = _required_text(value, where, maximum=71)
    if not SHA256_RE.fullmatch(text):
        raise CandidateAdmissionError(f"{where} must be a lowercase sha256 digest")
    return text


def _timestamp(value: Any, where: str) -> str:
    text = _required_text(value, where, maximum=40)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateAdmissionError(f"{where} must be an ISO 8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise CandidateAdmissionError(f"{where} must include a timezone")
    return text


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _value_digest(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def compute_dedup_key(payload: dict[str, Any]) -> str:
    """Bind the semantic failure, not candidate IDs or replaceable review receipts."""
    try:
        normalized = {
            "schema": SCHEMA,
            "failure_signature": payload["lineage"]["failure_signature"],
            "source_digest": payload["lineage"]["source_digest"],
            "command": payload["reproducibility"]["command"],
            "fixture": payload["minimal_reproduction"]["fixture"],
            "steps": payload["minimal_reproduction"]["steps"],
            "expected_invariant": payload["expected_invariant"],
        }
    except (KeyError, TypeError) as exc:
        raise CandidateAdmissionError(f"cannot compute dedup key: missing {exc}") from exc
    return _value_digest(normalized)


@dataclass(frozen=True)
class FailureEvalCandidateV1:
    candidate_id: str
    title: str
    lineage: dict[str, Any]
    reproducibility: dict[str, Any]
    privacy: dict[str, Any]
    minimal_reproduction: dict[str, Any]
    expected_invariant: str
    admission: dict[str, Any]
    dedup_key: str

    @classmethod
    def from_dict(cls, payload: Any) -> "FailureEvalCandidateV1":
        if not isinstance(payload, dict):
            raise CandidateAdmissionError("candidate must be a JSON object")
        required = {
            "schema",
            "candidate_id",
            "title",
            "lineage",
            "reproducibility",
            "privacy",
            "minimal_reproduction",
            "expected_invariant",
            "admission",
            "dedup_key",
        }
        _require_exact_keys(payload, required, "candidate")
        if payload["schema"] != SCHEMA:
            raise CandidateAdmissionError(f"schema must be {SCHEMA!r}")

        candidate_id = _required_text(payload["candidate_id"], "candidate_id", maximum=80)
        if not ID_RE.fullmatch(candidate_id):
            raise CandidateAdmissionError("candidate_id has invalid characters or length")
        title = _required_text(payload["title"], "title", maximum=160)
        expected_invariant = _required_text(
            payload["expected_invariant"], "expected_invariant", maximum=512
        )

        lineage = payload["lineage"]
        if not isinstance(lineage, dict):
            raise CandidateAdmissionError("lineage must be an object")
        _require_exact_keys(
            lineage,
            {"discovery_source", "source_digest", "observed_at", "failure_signature"},
            "lineage",
        )
        _required_text(lineage["discovery_source"], "lineage.discovery_source", maximum=240)
        _sha256(lineage["source_digest"], "lineage.source_digest")
        _timestamp(lineage["observed_at"], "lineage.observed_at")
        _required_text(lineage["failure_signature"], "lineage.failure_signature", maximum=320)

        reproducibility = payload["reproducibility"]
        if not isinstance(reproducibility, dict):
            raise CandidateAdmissionError("reproducibility must be an object")
        _require_exact_keys(
            reproducibility,
            {"command", "deterministic", "receipt_ref", "receipt_digest"},
            "reproducibility",
        )
        command = reproducibility["command"]
        if not isinstance(command, list) or not command or not all(
            isinstance(part, str) and part and len(part) <= 1024 for part in command
        ):
            raise CandidateAdmissionError("reproducibility.command must be a bounded argv list")
        if reproducibility["deterministic"] is not True:
            raise CandidateAdmissionError("reproducibility.deterministic must be true")
        _required_text(reproducibility["receipt_ref"], "reproducibility.receipt_ref", maximum=240)
        _sha256(reproducibility["receipt_digest"], "reproducibility.receipt_digest")

        privacy = payload["privacy"]
        if not isinstance(privacy, dict):
            raise CandidateAdmissionError("privacy must be an object")
        _require_exact_keys(
            privacy,
            {"profile", "publication_review_ref", "publication_review_digest"},
            "privacy",
        )
        if privacy["profile"] != "synthetic-only-v1":
            raise CandidateAdmissionError("privacy.profile must be synthetic-only-v1")
        _required_text(
            privacy["publication_review_ref"], "privacy.publication_review_ref", maximum=240
        )
        _sha256(
            privacy["publication_review_digest"], "privacy.publication_review_digest"
        )

        minimal = payload["minimal_reproduction"]
        if not isinstance(minimal, dict):
            raise CandidateAdmissionError("minimal_reproduction must be an object")
        _require_exact_keys(minimal, {"fixture", "steps"}, "minimal_reproduction")
        _required_text(minimal["fixture"], "minimal_reproduction.fixture", maximum=240)
        steps = minimal["steps"]
        if not isinstance(steps, list) or not steps or not all(
            isinstance(step, str)
            and step.strip()
            and len(step) <= 320
            and not any(ord(character) < 32 for character in step)
            for step in steps
        ):
            raise CandidateAdmissionError("minimal_reproduction.steps must contain bounded text")

        admission = payload["admission"]
        if not isinstance(admission, dict):
            raise CandidateAdmissionError("admission must be an object")
        _require_exact_keys(
            admission,
            {
                "human_admitted",
                "admitted_by",
                "admitted_at",
                "admission_source_type",
                "source_digest",
                "independent_custody",
                "scope",
            },
            "admission",
        )
        if admission["human_admitted"] is not True:
            raise CandidateAdmissionError("explicit human admission is required")
        _required_text(admission["admitted_by"], "admission.admitted_by", maximum=120)
        _timestamp(admission["admitted_at"], "admission.admitted_at")
        if admission["admission_source_type"] not in {"user_goal", "review_record"}:
            raise CandidateAdmissionError("admission_source_type is not recognized")
        _sha256(admission["source_digest"], "admission.source_digest")
        if type(admission["independent_custody"]) is not bool:
            raise CandidateAdmissionError("admission.independent_custody must be boolean")
        _required_text(admission["scope"], "admission.scope", maximum=320)

        dedup_key = _sha256(payload["dedup_key"], "dedup_key")
        expected_key = compute_dedup_key(payload)
        if dedup_key != expected_key:
            raise CandidateAdmissionError(
                f"dedup_key mismatch: expected {expected_key}, got {dedup_key}"
            )
        return cls(
            candidate_id=candidate_id,
            title=title,
            lineage=dict(lineage),
            reproducibility=dict(reproducibility),
            privacy=dict(privacy),
            minimal_reproduction=dict(minimal),
            expected_invariant=expected_invariant,
            admission=dict(admission),
            dedup_key=dedup_key,
        )


def _contained_fixture(repo_root: Path, locator: str, where: str) -> Path:
    relative = Path(locator)
    if relative.is_absolute() or ".." in relative.parts:
        raise CandidateAdmissionError(f"{where} must be a contained relative path")
    target = repo_root / relative
    if target.is_symlink() or not target.is_file():
        raise CandidateAdmissionError(f"{where} must name a regular, non-symlink fixture")
    resolved_root = repo_root.resolve()
    resolved_target = target.resolve()
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        raise CandidateAdmissionError(f"{where} escapes the repository")
    return target


def _read_json(path: Path, where: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateAdmissionError(f"cannot load {where} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CandidateAdmissionError(f"{where} must be a JSON object")
    return payload


def _scan_public_value(value: Any, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in FORBIDDEN_PUBLIC_KEYS:
                raise CandidateAdmissionError(f"{where} contains forbidden public key {key!r}")
            _scan_public_value(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _scan_public_value(item, f"{where}[{index}]")
    elif isinstance(value, str):
        for label, pattern in FORBIDDEN_PUBLIC_TEXT_PATTERNS.items():
            if pattern.search(value):
                raise CandidateAdmissionError(f"{where} contains forbidden {label}")


def _verify_fixture_lineage(candidate: FailureEvalCandidateV1, repo_root: Path) -> Path:
    source = _contained_fixture(
        repo_root, candidate.lineage["discovery_source"], "lineage.discovery_source"
    )
    if _file_digest(source) != candidate.lineage["source_digest"]:
        raise CandidateAdmissionError("lineage source digest mismatch")
    minimal = _contained_fixture(
        repo_root, candidate.minimal_reproduction["fixture"], "minimal_reproduction.fixture"
    )
    fixture = _read_json(minimal, "minimal reproduction fixture")
    _require_exact_keys(
        fixture,
        {"schema", "fixture_id", "synthetic", "command", "observed_failure", "expected_behavior"},
        "minimal reproduction fixture",
    )
    if fixture["schema"] != "operant-synthetic-failure-reproduction.v1":
        raise CandidateAdmissionError("minimal reproduction fixture schema is unsupported")
    if fixture["synthetic"] is not True:
        raise CandidateAdmissionError("minimal reproduction fixture must declare synthetic=true")
    if fixture["command"] != candidate.reproducibility["command"]:
        raise CandidateAdmissionError("candidate command does not match its minimal fixture")
    _required_text(fixture["fixture_id"], "minimal fixture.fixture_id", maximum=80)
    _required_text(fixture["observed_failure"], "minimal fixture.observed_failure", maximum=320)
    _required_text(fixture["expected_behavior"], "minimal fixture.expected_behavior", maximum=320)
    _scan_public_value(fixture, "minimal reproduction fixture")
    return minimal


def _verify_reproduction_receipt(
    candidate: FailureEvalCandidateV1, repo_root: Path
) -> Path:
    path = _contained_fixture(
        repo_root, candidate.reproducibility["receipt_ref"], "reproducibility.receipt_ref"
    )
    if _file_digest(path) != candidate.reproducibility["receipt_digest"]:
        raise CandidateAdmissionError("reproduction receipt digest mismatch")
    receipt = _read_json(path, "reproduction receipt")
    _require_exact_keys(
        receipt,
        {
            "schema",
            "candidate_id",
            "command_digest",
            "failure_signature",
            "attempts",
            "matching_failures",
            "synthetic_only",
            "observed_at",
            "attempt_receipts",
        },
        "reproduction receipt",
    )
    if receipt["schema"] != REPRODUCTION_SCHEMA:
        raise CandidateAdmissionError("reproduction receipt schema is unsupported")
    if receipt["candidate_id"] != candidate.candidate_id:
        raise CandidateAdmissionError("reproduction receipt candidate_id mismatch")
    if receipt["command_digest"] != _value_digest(candidate.reproducibility["command"]):
        raise CandidateAdmissionError("reproduction receipt command digest mismatch")
    if receipt["failure_signature"] != candidate.lineage["failure_signature"]:
        raise CandidateAdmissionError("reproduction receipt failure signature mismatch")
    if receipt["synthetic_only"] is not True:
        raise CandidateAdmissionError("reproduction receipt must be synthetic-only")
    _timestamp(receipt["observed_at"], "reproduction receipt.observed_at")
    attempts = receipt["attempts"]
    matches = receipt["matching_failures"]
    records = receipt["attempt_receipts"]
    if type(attempts) is not int or attempts < 2:
        raise CandidateAdmissionError("reproduction receipt needs at least two attempts")
    if type(matches) is not int or matches != attempts:
        raise CandidateAdmissionError("every reproduction attempt must match")
    if not isinstance(records, list) or len(records) != attempts:
        raise CandidateAdmissionError("reproduction attempt receipt count mismatch")
    signatures: set[tuple[int, str, str]] = set()
    for index, raw_record in enumerate(records, start=1):
        if not isinstance(raw_record, dict):
            raise CandidateAdmissionError("reproduction attempt receipt must be an object")
        _require_exact_keys(
            raw_record,
            {
                "attempt",
                "exit_code",
                "stdout_digest",
                "stderr_digest",
                "stdout_bytes",
                "stderr_bytes",
                "matched",
            },
            f"reproduction attempt {index}",
        )
        if raw_record["attempt"] != index or type(raw_record["attempt"]) is not int:
            raise CandidateAdmissionError("reproduction attempts must be sequential")
        exit_code = raw_record["exit_code"]
        if type(exit_code) is not int or exit_code == 0:
            raise CandidateAdmissionError("reproduction failure exit code must be nonzero")
        stdout_digest = _sha256(raw_record["stdout_digest"], "attempt.stdout_digest")
        stderr_digest = _sha256(raw_record["stderr_digest"], "attempt.stderr_digest")
        for key in ("stdout_bytes", "stderr_bytes"):
            if type(raw_record[key]) is not int or raw_record[key] < 0:
                raise CandidateAdmissionError(f"attempt.{key} must be a non-negative integer")
        if raw_record["matched"] is not True:
            raise CandidateAdmissionError("every reproduction attempt must match")
        signatures.add((exit_code, stdout_digest, stderr_digest))
    if len(signatures) != 1:
        raise CandidateAdmissionError("reproduction attempts do not share one failure signature")
    return path


def _verify_publication_review(
    candidate: FailureEvalCandidateV1,
    repo_root: Path,
    *,
    fixture_path: Path,
    reproduction_path: Path,
) -> Path:
    path = _contained_fixture(
        repo_root, candidate.privacy["publication_review_ref"], "privacy.publication_review_ref"
    )
    if _file_digest(path) != candidate.privacy["publication_review_digest"]:
        raise CandidateAdmissionError("publication review digest mismatch")
    review = _read_json(path, "publication review")
    _require_exact_keys(
        review,
        {
            "schema",
            "candidate_id",
            "reviewed_at",
            "reviewer",
            "scope",
            "fixture_digest",
            "reproduction_receipt_digest",
            "synthetic_only",
            "secrets_detected",
            "real_user_data_detected",
            "approved_for_publication",
            "independent_custody",
        },
        "publication review",
    )
    if review["schema"] != PUBLICATION_REVIEW_SCHEMA:
        raise CandidateAdmissionError("publication review schema is unsupported")
    if review["candidate_id"] != candidate.candidate_id:
        raise CandidateAdmissionError("publication review candidate_id mismatch")
    _timestamp(review["reviewed_at"], "publication review.reviewed_at")
    _required_text(review["reviewer"], "publication review.reviewer", maximum=120)
    _required_text(review["scope"], "publication review.scope", maximum=320)
    if review["fixture_digest"] != _file_digest(fixture_path):
        raise CandidateAdmissionError("publication review fixture digest mismatch")
    if review["reproduction_receipt_digest"] != _file_digest(reproduction_path):
        raise CandidateAdmissionError("publication review reproduction receipt digest mismatch")
    for key, expected in {
        "synthetic_only": True,
        "secrets_detected": False,
        "real_user_data_detected": False,
        "approved_for_publication": True,
    }.items():
        if review[key] is not expected:
            raise CandidateAdmissionError(f"publication review.{key} must be {expected}")
    if type(review["independent_custody"]) is not bool:
        raise CandidateAdmissionError("publication review.independent_custody must be boolean")
    return path


def _verify_external_admission(
    candidate: FailureEvalCandidateV1,
    repo_root: Path,
    admission_authorities: list[Path],
) -> None:
    if not admission_authorities:
        raise CandidateAdmissionError("a separately supplied admission authority is required")
    repo = repo_root.resolve()
    matched = False
    for authority in admission_authorities:
        if authority.is_symlink() or not authority.is_file():
            raise CandidateAdmissionError("admission authority must be a regular non-symlink file")
        resolved = authority.resolve()
        if resolved == repo or repo in resolved.parents:
            raise CandidateAdmissionError("admission authority must be outside the candidate repository")
        if _file_digest(resolved) == candidate.admission["source_digest"]:
            matched = True
    if not matched:
        raise CandidateAdmissionError("no supplied admission authority matches source_digest")


def load_candidate(
    path: Path,
    *,
    repo_root: Path = REPO_ROOT,
    admission_authorities: list[Path],
) -> FailureEvalCandidateV1:
    payload = _read_json(path, "candidate")
    candidate = FailureEvalCandidateV1.from_dict(payload)
    fixture_path = _verify_fixture_lineage(candidate, repo_root)
    reproduction_path = _verify_reproduction_receipt(candidate, repo_root)
    _verify_publication_review(
        candidate,
        repo_root,
        fixture_path=fixture_path,
        reproduction_path=reproduction_path,
    )
    _verify_external_admission(candidate, repo_root, admission_authorities)
    return candidate


def admit_candidates(
    paths: list[Path],
    *,
    repo_root: Path = REPO_ROOT,
    admission_authorities: list[Path],
) -> list[FailureEvalCandidateV1]:
    """Validate a batch and reject semantic duplicates, including different IDs."""
    admitted: list[FailureEvalCandidateV1] = []
    seen_ids: set[str] = set()
    seen_dedup: set[str] = set()
    for path in paths:
        candidate = load_candidate(
            path, repo_root=repo_root, admission_authorities=admission_authorities
        )
        if candidate.candidate_id in seen_ids:
            raise CandidateAdmissionError(f"duplicate candidate_id: {candidate.candidate_id}")
        if candidate.dedup_key in seen_dedup:
            raise CandidateAdmissionError(f"duplicate failure dedup_key: {candidate.dedup_key}")
        seen_ids.add(candidate.candidate_id)
        seen_dedup.add(candidate.dedup_key)
        admitted.append(candidate)
    return admitted


def admission_receipt(candidates: list[FailureEvalCandidateV1]) -> dict[str, Any]:
    """Return a bounded receipt; fixture content and reproduction output stay private."""
    return {
        "schema": "operant-failure-eval-admission-receipt.v1",
        "status": "ADMITTED",
        "candidate_count": len(candidates),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "dedup_key": candidate.dedup_key,
                "admission_source_type": candidate.admission["admission_source_type"],
                "admission_source_digest": candidate.admission["source_digest"],
                "reproduction_receipt_digest": candidate.reproducibility["receipt_digest"],
                "publication_review_digest": candidate.privacy["publication_review_digest"],
                "independent_custody": candidate.admission["independent_custody"],
            }
            for candidate in candidates
        ],
        "claim_boundary": (
            "Admission proves exact local fixture, reproduction, publication-review, and "
            "separately supplied authority-byte bindings. It does not prove benchmark "
            "validity, independent custody, or external replication."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Validate FailureEvalCandidateV1 files")
    parser.add_argument(
        "--admission-authority",
        action="append",
        required=True,
        type=Path,
        help="Separately supplied user-goal or review-record bytes; repeatable",
    )
    parser.add_argument("candidate", nargs="+", type=Path)
    args = parser.parse_args(argv)
    try:
        candidates = admit_candidates(
            args.candidate, admission_authorities=args.admission_authority
        )
    except CandidateAdmissionError as exc:
        print(f"FAIL_CLOSED: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(admission_receipt(candidates), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
