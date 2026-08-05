"""Regression tests for FailureEvalCandidateV1 admission (synthetic only)."""

from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from operant_lab.failure_eval import (
    CandidateAdmissionError,
    FailureEvalCandidateV1,
    admission_receipt,
    admit_candidates,
    compute_dedup_key,
    load_candidate,
)

ROOT = Path(__file__).resolve().parent
CANDIDATE = (
    ROOT
    / "fixtures"
    / "failure-eval-candidates"
    / "operant-selfserve-nonzero-exit-v1.json"
)


class FailureEvalCandidateTests(unittest.TestCase):
    def payload(self) -> dict:
        return json.loads(CANDIDATE.read_text(encoding="utf-8"))

    def external_candidate(self, directory: Path, payload: dict | None = None) -> tuple[Path, Path]:
        authority = directory / "operator-goal.txt"
        authority.write_text("synthetic operator admission authority\n", encoding="utf-8")
        authority_digest = "sha256:" + hashlib.sha256(authority.read_bytes()).hexdigest()
        candidate_payload = copy.deepcopy(payload or self.payload())
        candidate_payload["admission"]["source_digest"] = authority_digest
        candidate = directory / "candidate.json"
        candidate.write_text(json.dumps(candidate_payload), encoding="utf-8")
        return candidate, authority

    def test_exact_receipts_and_external_authority_validate_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path, authority = self.external_candidate(Path(td))
            candidate = load_candidate(path, admission_authorities=[authority])
        self.assertIsInstance(candidate, FailureEvalCandidateV1)
        self.assertEqual(candidate.candidate_id, "operant-selfserve-nonzero-exit-v1")
        self.assertEqual(candidate.dedup_key, compute_dedup_key(self.payload()))
        self.assertEqual(candidate.admission["admission_source_type"], "user_goal")
        self.assertFalse(candidate.admission["independent_custody"])
        receipt = admission_receipt([candidate])
        self.assertEqual(receipt["status"], "ADMITTED")
        self.assertIn("reproduction_receipt_digest", receipt["candidates"][0])
        self.assertIn("publication_review_digest", receipt["candidates"][0])
        self.assertNotIn("command", receipt["candidates"][0])

    def test_candidate_boolean_cannot_self_authorize(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path, authority = self.external_candidate(Path(td))
            with self.assertRaisesRegex(CandidateAdmissionError, "separately supplied"):
                load_candidate(path, admission_authorities=[])
            wrong = Path(td) / "wrong-authority.txt"
            wrong.write_text("different authority\n", encoding="utf-8")
            with self.assertRaisesRegex(CandidateAdmissionError, "matches source_digest"):
                load_candidate(path, admission_authorities=[wrong])
            candidate = load_candidate(path, admission_authorities=[authority])
            self.assertTrue(candidate.admission["human_admitted"])

    def test_repository_file_cannot_be_its_own_admission_authority(self) -> None:
        payload = self.payload()
        payload["admission"]["source_digest"] = (
            "sha256:" + hashlib.sha256(CANDIDATE.read_bytes()).hexdigest()
        )
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "candidate.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(CandidateAdmissionError, "outside"):
                load_candidate(path, admission_authorities=[CANDIDATE])

    def test_missing_unknown_and_bad_timestamps_fail_closed(self) -> None:
        missing = self.payload()
        del missing["expected_invariant"]
        with self.assertRaises(CandidateAdmissionError):
            FailureEvalCandidateV1.from_dict(missing)

        unknown = self.payload()
        unknown["unreviewed_extension"] = True
        with self.assertRaises(CandidateAdmissionError):
            FailureEvalCandidateV1.from_dict(unknown)

        timestamp = self.payload()
        timestamp["admission"]["admitted_at"] = "2026-08-05T00:00:00"
        with self.assertRaisesRegex(CandidateAdmissionError, "timezone"):
            FailureEvalCandidateV1.from_dict(timestamp)

    def test_receipt_and_publication_review_digests_are_mandatory(self) -> None:
        reproduction = self.payload()
        reproduction["reproducibility"]["receipt_digest"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as td:
            path, authority = self.external_candidate(Path(td), reproduction)
            with self.assertRaisesRegex(CandidateAdmissionError, "reproduction receipt digest"):
                load_candidate(path, admission_authorities=[authority])

        publication = self.payload()
        publication["privacy"]["publication_review_digest"] = "sha256:" + "0" * 64
        with tempfile.TemporaryDirectory() as td:
            path, authority = self.external_candidate(Path(td), publication)
            with self.assertRaisesRegex(CandidateAdmissionError, "publication review digest"):
                load_candidate(path, admission_authorities=[authority])

    def test_human_flag_and_semantic_tampering_fail_closed(self) -> None:
        automatic = self.payload()
        automatic["admission"]["human_admitted"] = False
        with self.assertRaises(CandidateAdmissionError):
            FailureEvalCandidateV1.from_dict(automatic)

        tampered = self.payload()
        tampered["expected_invariant"] = "A different invariant"
        with self.assertRaisesRegex(CandidateAdmissionError, "dedup_key mismatch"):
            FailureEvalCandidateV1.from_dict(tampered)

    def test_batch_rejects_semantic_duplicate_with_different_id(self) -> None:
        first = FailureEvalCandidateV1.from_dict(self.payload())
        duplicate = self.payload()
        duplicate["candidate_id"] = "operant-selfserve-nonzero-exit-copy"
        second = FailureEvalCandidateV1.from_dict(duplicate)
        with patch(
            "operant_lab.failure_eval.load_candidate", side_effect=[first, second]
        ):
            with self.assertRaisesRegex(CandidateAdmissionError, "duplicate failure dedup_key"):
                admit_candidates(
                    [Path("first.json"), Path("second.json")], admission_authorities=[]
                )


if __name__ == "__main__":
    unittest.main()
