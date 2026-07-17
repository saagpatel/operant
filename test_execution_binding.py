#!/usr/bin/env python3
"""Adversarial tests for OPERANT execution and identity bindings."""

from __future__ import annotations

import ast
import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path

from operant_lab.artifacts import (
    RunManifest,
    RunReport,
    build_execution_binding,
    complete_execution_binding,
    execution_input_mismatches,
    filter_unblocked_index_rows,
    receipt_output_scoring_block_reason,
    receipt_scoring_block_reason,
    scoring_block_reason,
    validate_execution_binding,
)
from operant_lab.inventory import _manifest_binding_projection

HERE = Path(__file__).resolve().parent


def _binding(
    *,
    requested: str = "fixture-model",
    candidates: list[str] | None = None,
) -> dict:
    binding = build_execution_binding(
        root=HERE,
        exact_prompt="private fixture prompt",
        system_prompt="private fixture system",
        stdin_text=None,
        command=["fixture", "--model", requested],
        cwd_class="REPOSITORY_ROOT",
        tool_policy="none",
        timeout_seconds=1,
        output_mode="fixture-json",
        dispatch_settings={},
        harness_files=[Path(__file__)],
        requested_model_id=requested,
    )
    if candidates is not None:
        binding = complete_execution_binding(
            binding,
            provider_reported_candidates=candidates,
            evidence_source="provider_result_modelUsage",
            raw_result_envelope='{"fixture":true}',
            final_answer="fixture answer",
        )
    return binding


def _manifest(binding: dict) -> RunManifest:
    return RunManifest(
        run_label="fixture-r1",
        case_id="fixture.case",
        axis="decision",
        subject_shell="fixture",
        model_id="fixture-model",
        prompt_hash="a" * 64,
        prompt_contract="fixture",
        tool_policy="none",
        evaluation_role="OPEN_DEVELOPMENT",
        case_bundle_sha256="b" * 64,
        case_bundle_case_count=1,
        case_split="fixture",
        execution_binding=binding,
    )


class ExecutionBindingTests(unittest.TestCase):
    def test_binding_is_sanitized_and_conservatively_not_replayable(self) -> None:
        binding = _binding()
        serialized = json.dumps(binding, sort_keys=True)
        self.assertEqual(validate_execution_binding(binding), [])
        self.assertEqual(binding["replay_class"], "INPUT_BOUND_NOT_REPLAYABLE")
        self.assertEqual(binding["dependency_lock"]["status"], "UNKNOWN")
        self.assertEqual(
            binding["model_observation"]["served_model_identity"],
            "UNKNOWN",
        )
        self.assertNotIn("private fixture prompt", serialized)
        self.assertNotIn("private fixture system", serialized)
        self.assertNotIn(str(HERE), serialized)

    def test_reported_candidate_cardinality_is_exact_and_unaliased(self) -> None:
        cases = (
            ([], "UNKNOWN"),
            (["fixture-model"], "MATCHED"),
            (["fixture-model-2026"], "MISMATCH"),
            (["fixture-model", "other"], "AMBIGUOUS"),
        )
        for candidates, expected in cases:
            with self.subTest(candidates=candidates):
                binding = _binding(candidates=candidates)
                observation = binding["model_observation"]
                self.assertEqual(observation["comparison_status"], expected)
                self.assertEqual(observation["served_model_identity"], "UNKNOWN")

    def test_raw_result_envelope_is_bound_without_content(self) -> None:
        binding = _binding(candidates=["fixture-model"])
        observation = binding["model_observation"]
        self.assertRegex(
            observation["raw_result_envelope_sha256"],
            r"^[0-9a-f]{64}$",
        )
        self.assertNotIn("fixture", observation["raw_result_envelope_sha256"])

    def test_input_drift_is_detected_field_by_field(self) -> None:
        binding = _binding()
        mismatches = execution_input_mismatches(
            binding,
            exact_prompt="changed",
            system_prompt="private fixture system",
            stdin_text=None,
            command=["fixture", "--model", "fixture-model"],
            cwd_class="REPOSITORY_ROOT",
            tool_policy="none",
            timeout_seconds=1,
            output_mode="fixture-json",
            dispatch_settings={},
        )
        self.assertEqual(mismatches, ["delivered_prompt_sha256"])

    def test_dirty_and_untracked_content_change_source_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(
                ["git", "config", "user.email", "fixture@example.invalid"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Fixture"],
                cwd=root,
                check=True,
            )
            harness = root / "harness.py"
            harness.write_text("print('one')\n", encoding="utf-8")
            subprocess.run(["git", "add", "harness.py"], cwd=root, check=True)
            subprocess.run(
                ["git", "commit", "-qm", "fixture"],
                cwd=root,
                check=True,
            )

            def capture() -> dict:
                return build_execution_binding(
                    root=root,
                    exact_prompt="p",
                    system_prompt="s",
                    stdin_text=None,
                    command=["fixture"],
                    cwd_class="REPOSITORY_ROOT",
                    tool_policy="none",
                    timeout_seconds=1,
                    output_mode="fixture",
                    dispatch_settings={},
                    harness_files=[harness],
                    requested_model_id="fixture-model",
                )

            clean = capture()["source_state"]
            harness.write_text("print('two')\n", encoding="utf-8")
            dirty = capture()["source_state"]
            (root / "untracked.txt").write_text("evidence\n", encoding="utf-8")
            untracked = capture()["source_state"]
        self.assertIs(clean["dirty"], False)
        self.assertIs(dirty["dirty"], True)
        self.assertNotEqual(
            clean["dirty_state_sha256"],
            dirty["dirty_state_sha256"],
        )
        self.assertNotEqual(
            dirty["dirty_state_sha256"],
            untracked["dirty_state_sha256"],
        )

    def test_identity_mismatch_blocks_scores_but_preserves_failure_receipt(self) -> None:
        binding = _binding(candidates=["different-model"])
        manifest = _manifest(binding)
        reason = scoring_block_reason(asdict(manifest))
        self.assertEqual(reason, "identity_blocked:mismatch")
        preserved = RunReport(
            manifest=manifest,
            parse_status=reason,
            final_answer="fixture answer",
            failure_class=reason,
        )
        self.assertEqual(preserved.final_answer, "fixture answer")
        with self.assertRaisesRegex(ValueError, "blocked receipt cannot carry scores"):
            RunReport(
                manifest=manifest,
                parse_status=reason,
                final_answer="fixture answer",
                failure_class=reason,
                score_row={"decision_accuracy": True},
            )

    def test_manifest_model_cannot_swap_a_matched_execution_binding(self) -> None:
        binding = _binding(candidates=["fixture-model"])
        manifest = asdict(_manifest(binding))
        manifest["model_id"] = "different-model"
        self.assertEqual(
            scoring_block_reason(manifest),
            "invalid_execution_binding",
        )

    def test_binding_rejects_injected_private_fields(self) -> None:
        binding = _binding()
        binding["input_binding"]["private_prompt"] = "must not survive"
        self.assertIn(
            "input binding fields are not exact",
            validate_execution_binding(binding),
        )

    def test_completed_observation_is_integrity_bound(self) -> None:
        binding = _binding(candidates=["fixture-model"])
        binding["model_observation"]["comparison_status"] = "MISMATCH"
        self.assertIn(
            "completed execution binding digest mismatch",
            validate_execution_binding(binding),
        )

    def test_model_candidates_reject_whitespace_normalization(self) -> None:
        with self.assertRaisesRegex(ValueError, "exact nonempty strings"):
            complete_execution_binding(
                _binding(),
                provider_reported_candidates=[" fixture-model "],
                evidence_source="provider_result_modelUsage",
                raw_result_envelope=b"{}",
                final_answer="fixture answer",
            )

    def test_persisted_index_rows_exclude_later_blocked_receipts(self) -> None:
        binding = _binding(candidates=["different-model"])
        receipt = {
            "manifest": asdict(_manifest(binding)),
            "parse_status": "identity_blocked:mismatch",
            "final_answer": "preserved",
        }
        row = {"run_label": "fixture-r1", "case_id": "fixture.case", "score": 1}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "lab" / "runs" / "fixture-r1" / "fixture.case.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(filter_unblocked_index_rows(root, [row]), [])

    def test_receipt_cannot_authorize_substituted_output(self) -> None:
        receipt = {
            "manifest": asdict(_manifest(_binding(candidates=["fixture-model"]))),
            "parse_status": "ok",
            "final_answer": "fixture answer",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "lab" / "runs" / "fixture-r1" / "fixture.case.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertEqual(
                receipt_output_scoring_block_reason(
                    root,
                    run_label="fixture-r1",
                    case_id="fixture.case",
                    final_answer="substituted answer",
                    require_receipt=True,
                ),
                "receipt_output_mismatch",
            )

    def test_score_recording_requires_receipt_when_requested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                receipt_scoring_block_reason(
                    Path(tmp),
                    run_label="new-label",
                    case_id="fixture.case",
                    require_receipt=True,
                ),
                "missing_execution_receipt",
            )

    def test_successful_output_rejects_pre_dispatch_only_binding(self) -> None:
        manifest = _manifest(_binding())
        self.assertEqual(
            scoring_block_reason(asdict(manifest)),
            "incomplete_execution_receipt",
        )
        with self.assertRaisesRegex(
            ValueError,
            "successful output requires completed execution binding",
        ):
            RunReport(
                manifest=manifest,
                parse_status="ok",
                final_answer="answer",
            )

    def test_every_persisted_index_consumer_filters_blocked_receipts(self) -> None:
        expected_calls = {
            "score_operant.py": 1,
            "score_orchestration.py": 1,
            "score_orchestration_judge.py": 2,
            "score_suite.py": 1,
            "rescore_orchestration.py": 1,
            "run_suite.py": 1,
            "operant_lab/export.py": 3,
        }
        for filename, minimum in expected_calls.items():
            tree = ast.parse((HERE / filename).read_text(encoding="utf-8"))
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "filter_unblocked_index_rows"
            ]
            self.assertGreaterEqual(len(calls), minimum, filename)

    def test_v2_absence_is_unknown_but_malformed_v3_is_invalid(self) -> None:
        v2 = {
            "manifest_schema": "operant-run-manifest.v2",
            "evaluation_role": "OPEN_DEVELOPMENT",
            "case_bundle_sha256": "b" * 64,
            "case_bundle_case_count": 1,
            "case_split": "fixture",
            "confirmatory_eligible": False,
        }
        self.assertEqual(
            _manifest_binding_projection(v2, source="run_receipt")["status"],
            "V2_BOUND_NONCONFIRMATORY",
        )
        malformed_v3 = {**v2, "manifest_schema": "operant-run-manifest.v3"}
        self.assertEqual(
            _manifest_binding_projection(
                malformed_v3,
                source="run_receipt",
            )["status"],
            "INVALID",
        )


if __name__ == "__main__":
    unittest.main()
