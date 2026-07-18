#!/usr/bin/env python3
"""Fail-closed tests for sanitized evaluation-binding projections."""

from __future__ import annotations

import unittest

from operant_lab.export import (
    _binding_summary,
    _require_publishable_evaluation_bindings,
    model_card,
)
from operant_lab.inventory import (
    _evaluation_binding,
    _manifest_binding_projection,
)
from operant_lab.public_contract import (
    _validate_evaluation_binding_summary,
    _validate_model_card_evaluation_binding,
)


def _valid_manifest(**overrides):
    manifest = {
        "manifest_schema": "operant-run-manifest.v2",
        "evaluation_role": "OPEN_DEVELOPMENT",
        "case_bundle_sha256": "a" * 64,
        "case_bundle_case_count": 2,
        "case_split": "development",
        "confirmatory_eligible": False,
    }
    manifest.update(overrides)
    return manifest


class EvaluationBindingProjectionTests(unittest.TestCase):
    def test_legacy_manifest_remains_unknown(self) -> None:
        projection = _manifest_binding_projection(
            {"run_label": "historical"},
            source="run_receipt",
        )
        self.assertEqual(projection["status"], "UNKNOWN")
        self.assertEqual(projection["evaluation_role"], "UNKNOWN")
        self.assertEqual(projection["case_bundle_sha256"], "UNKNOWN")
        self.assertEqual(projection["confirmatory_eligible"], "UNKNOWN")

    def test_valid_v2_manifest_projects_only_sanitized_binding_fields(self) -> None:
        projection = _manifest_binding_projection(
            _valid_manifest(),
            source="run_receipt",
        )
        self.assertEqual(projection["status"], "V2_BOUND_NONCONFIRMATORY")
        self.assertEqual(projection["evaluation_role"], "OPEN_DEVELOPMENT")
        self.assertEqual(projection["case_bundle_sha256"], "a" * 64)
        self.assertIs(projection["confirmatory_eligible"], False)
        self.assertNotIn("prompt", projection)
        self.assertNotIn("final_answer", projection)

    def test_malformed_or_confirmatory_v2_manifest_is_invalid(self) -> None:
        for manifest in (
            _valid_manifest(confirmatory_eligible=True),
            _valid_manifest(case_bundle_sha256="bad"),
            _valid_manifest(evaluation_role=["not", "a", "role"]),
        ):
            with self.subTest(manifest=manifest):
                projection = _manifest_binding_projection(
                    manifest,
                    source="run_receipt",
                )
                self.assertEqual(projection["status"], "INVALID")
                self.assertEqual(projection["case_bundle_sha256"], "UNKNOWN")

    def test_queue_run_binding_mismatch_is_invalid(self) -> None:
        projection = _evaluation_binding(
            queue_manifest=_valid_manifest(),
            run_manifest=_valid_manifest(case_bundle_sha256="b" * 64),
            has_run=True,
        )
        self.assertEqual(projection["status"], "INVALID")
        self.assertEqual(projection["evaluation_role"], "UNKNOWN")
        self.assertEqual(projection["case_bundle_sha256"], "UNKNOWN")

    def test_malformed_v2_queue_cannot_be_masked_by_valid_run(self) -> None:
        projection = _evaluation_binding(
            queue_manifest=_valid_manifest(case_bundle_sha256="bad"),
            run_manifest=_valid_manifest(),
            has_run=True,
        )
        self.assertEqual(projection["status"], "INVALID")
        self.assertEqual(projection["evaluation_role"], "UNKNOWN")
        self.assertEqual(projection["case_bundle_sha256"], "UNKNOWN")

    def test_summary_preserves_mixed_unknown(self) -> None:
        bound = _manifest_binding_projection(
            _valid_manifest(),
            source="run_receipt",
        )
        unknown = _manifest_binding_projection({}, source="run_receipt")
        summary = _binding_summary(
            [
                {"evaluation_binding": bound},
                {"evaluation_binding": unknown},
            ]
        )
        self.assertEqual(summary["status"], "MIXED_UNKNOWN")
        self.assertEqual(summary["confirmatory_eligible"], "UNKNOWN")
        self.assertEqual(summary["case_bundle_sha256"], "a" * 64)

    def test_historical_model_card_explicitly_projects_unknown(self) -> None:
        card = model_card(
            base_label="historical",
            decision_repeats={"historical-r1": []},
            judge_repeats={},
            opus_judge_repeats={},
        )
        binding = card["evaluation_binding"]
        self.assertEqual(binding["status"], "UNKNOWN")
        self.assertEqual(binding["confirmatory_eligible"], "UNKNOWN")
        self.assertEqual(
            binding["repeats"]["historical-r1"]["evaluation_role_counts"],
            {"UNKNOWN": 1},
        )

    def test_public_contract_rejects_confirmatory_or_invalid_projection(self) -> None:
        errors = []
        _validate_evaluation_binding_summary(
            {
                "status": "V2_BOUND_NONCONFIRMATORY",
                "manifest_schema_counts": {"operant-run-manifest.v2": 1},
                "evaluation_role_counts": {"OPEN_DEVELOPMENT": 1},
                "case_bundle_count": 1,
                "case_bundle_sha256": "a" * 64,
                "confirmatory_eligible": True,
            },
            label="fixture",
            errors=errors,
        )
        self.assertIn(
            "fixture: bound evaluation must be non-confirmatory",
            errors,
        )
        forged_errors = []
        _validate_evaluation_binding_summary(
            {
                "status": "V2_BOUND_NONCONFIRMATORY",
                "manifest_schema_counts": {"operant-run-manifest.v2": 1},
                "evaluation_role_counts": {"CONFIRMATORY": 1},
                "case_bundle_count": 1,
                "case_bundle_sha256": "a" * 64,
                "confirmatory_eligible": False,
            },
            label="forged",
            errors=forged_errors,
        )
        self.assertIn(
            "forged: bound evaluation lacks explicit role counts",
            forged_errors,
        )

    def test_public_contract_rejects_missing_active_card_projection(self) -> None:
        errors = []
        _validate_model_card_evaluation_binding(
            {
                "run_family": "fixture",
                "decision": {"repeats": {"fixture-r1": {}}},
            },
            errors,
        )
        self.assertEqual(
            errors,
            ["model card fixture: missing evaluation_binding"],
        )

    def test_export_refuses_invalid_binding_before_write_phase(self) -> None:
        with self.assertRaisesRegex(
            RuntimeError,
            "refusing public export with invalid evaluation bindings: bad-r1",
        ):
            _require_publishable_evaluation_bindings(
                {
                    "runs": [
                        {
                            "run_label": "bad-r1",
                            "evaluation_binding": {"status": "INVALID"},
                        }
                    ]
                }
            )


if __name__ == "__main__":
    unittest.main()
