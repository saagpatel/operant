#!/usr/bin/env python3
"""Adversarial tests for OPERANT execution and identity bindings."""

from __future__ import annotations

import ast
import copy
import json
import subprocess
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest import mock

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
    validate_run_manifest_v3,
    validate_run_manifest_v4,
    validate_run_manifest_v5,
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
        prompt_hash=binding["input_binding"]["delivered_prompt_sha256"],
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
            expected_commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            harness.write_text("print('two')\n", encoding="utf-8")
            subprocess.run(["git", "add", "harness.py"], cwd=root, check=True)
            staged = capture()["source_state"]
            harness.write_text("print('three')\n", encoding="utf-8")
            dirty = capture()["source_state"]
            (root / "untracked.txt").write_text("evidence\n", encoding="utf-8")
            untracked = capture()["source_state"]
        self.assertEqual(clean["commit"], expected_commit)
        self.assertIs(clean["dirty"], False)
        self.assertEqual(clean["reconstruction"], "CLEAN_COMMIT")
        self.assertEqual(staged["reconstruction"], "DIRTY_DIGEST_ONLY")
        self.assertIs(dirty["dirty"], True)
        self.assertNotEqual(
            clean["dirty_state_sha256"],
            staged["dirty_state_sha256"],
        )
        self.assertNotEqual(
            staged["dirty_state_sha256"],
            dirty["dirty_state_sha256"],
        )
        self.assertNotEqual(
            clean["dirty_state_sha256"],
            dirty["dirty_state_sha256"],
        )
        self.assertNotEqual(
            dirty["dirty_state_sha256"],
            untracked["dirty_state_sha256"],
        )

    def test_dependency_lockfiles_are_present_but_not_claimed_active(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness.py"
            harness.write_text("# fixture\n", encoding="utf-8")
            lock = root / "pylock.toml"
            lock.write_text("[packages]\na = '1'\n", encoding="utf-8")

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
                )["dependency_lock"]

            first = capture()
            lock.write_text("[packages]\na = '2'\n", encoding="utf-8")
            second = capture()
        self.assertEqual(first["status"], "LOCKFILE_PRESENT_UNVERIFIED")
        self.assertEqual(first["files"], ["pylock.toml"])
        self.assertEqual(first["reason"], "ACTIVE_ENVIRONMENT_NOT_PROVEN")
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_subject_runtime_binds_bytes_without_invoking_executable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = root / "harness.py"
            harness.write_text("# fixture\n", encoding="utf-8")
            executable = root / "fixture-runtime"
            executable.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
            executable.chmod(0o755)

            def capture(command: list[str] | None) -> dict:
                return build_execution_binding(
                    root=root,
                    exact_prompt="p",
                    system_prompt="s",
                    stdin_text=None,
                    command=command,
                    cwd_class="REPOSITORY_ROOT",
                    tool_policy="none",
                    timeout_seconds=1,
                    output_mode="fixture",
                    dispatch_settings={},
                    harness_files=[harness],
                    requested_model_id="fixture-model",
                )["subject_runtime"]

            first = capture([str(executable)])
            executable.write_text("#!/bin/sh\nexit 98\n", encoding="utf-8")
            second = capture([str(executable)])
            relative = capture(["./fixture-runtime"])
            executable.chmod(0o644)
            non_executable = capture([str(executable)])
            missing = capture(["definitely-not-an-operant-runtime"])
            manual = capture(None)
            null_absolute = capture(["/tmp/\x00"])
            null_relative = capture(["./\x00"])
        self.assertEqual(
            first["status"],
            "PRE_DISPATCH_EXECUTABLE_BYTES_BOUND",
        )
        self.assertEqual(first["resolved_executable_name"], "fixture-runtime")
        self.assertRegex(first["executable_sha256"], r"^[0-9a-f]{64}$")
        self.assertNotEqual(first["executable_sha256"], second["executable_sha256"])
        self.assertEqual(relative["executable_sha256"], second["executable_sha256"])
        self.assertEqual(first["version"], "UNKNOWN")
        self.assertEqual(
            first["version_reason"],
            "NOT_QUERIED_TO_PRESERVE_NO_SIDE_EFFECT_BOUNDARY",
        )
        self.assertEqual(missing["reason"], "EXECUTABLE_NOT_FOUND")
        self.assertEqual(non_executable["reason"], "EXECUTABLE_CAPTURE_FAILED")
        self.assertEqual(manual["reason"], "NO_EXECUTABLE_DISPATCH")
        self.assertEqual(null_absolute["reason"], "EXECUTABLE_CAPTURE_FAILED")
        self.assertEqual(null_relative["reason"], "EXECUTABLE_CAPTURE_FAILED")
        self.assertNotIn(str(root), json.dumps(first, sort_keys=True))

    def test_untracked_symlink_binds_link_identity_not_external_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            external = Path(tmp) / "private.txt"
            external.write_text("first secret\n", encoding="utf-8")
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
            harness.write_text("# fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "harness.py"], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
            (root / "external-link").symlink_to(external)

            def source() -> dict:
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
                )["source_state"]

            before = source()
            external.write_text("changed secret\n", encoding="utf-8")
            after = source()
        self.assertEqual(before, after)
        self.assertEqual(before["reconstruction"], "DIRTY_DIGEST_ONLY")

    def test_unstable_source_capture_falls_back_to_unknown(self) -> None:
        import operant_lab.artifacts as artifacts

        first = {
            "commit": "a" * 40,
            "dirty": False,
            "dirty_state_sha256": "b" * 64,
            "reconstruction": "CLEAN_COMMIT",
        }
        second = {
            **first,
            "dirty": True,
            "reconstruction": "DIRTY_DIGEST_ONLY",
        }
        with mock.patch.object(
            artifacts,
            "_source_snapshot",
            side_effect=[first, second],
        ):
            captured = artifacts._source_state(Path("."))
        self.assertEqual(
            captured,
            {
                "commit": "UNKNOWN",
                "dirty": "UNKNOWN",
                "dirty_state_sha256": "UNKNOWN",
                "reconstruction": "UNKNOWN",
            },
        )
        with mock.patch.object(
            artifacts,
            "_source_snapshot",
            side_effect=FileNotFoundError("disappeared"),
        ):
            disappeared = artifacts._source_state(Path("."))
        self.assertEqual(disappeared["reconstruction"], "UNKNOWN")

    def test_manifest_core_and_schema_downgrade_are_fail_closed(self) -> None:
        manifest = asdict(_manifest(_binding(candidates=["fixture-model"])))
        self.assertEqual(validate_run_manifest_v5(manifest), [])
        for field_name, replacement in (
            ("subject_shell", "relabelled-shell"),
            ("evaluation_role", "SMOKE_ONLY"),
            ("case_split", "relabelled-split"),
            ("created_at", "2020-01-01T00:00:00Z"),
        ):
            changed = copy.deepcopy(manifest)
            changed[field_name] = replacement
            with self.subTest(field=field_name):
                self.assertIn(
                    "manifest core digest mismatch",
                    validate_run_manifest_v5(changed),
                )
                self.assertEqual(
                    scoring_block_reason(changed),
                    "invalid_execution_binding",
                )
        for schema in (
            None,
            "operant-run-manifest.v1",
            "operant-run-manifest.v2",
            "operant-run-manifest.v3",
            "operant-run-manifest.v4",
        ):
            downgraded = copy.deepcopy(manifest)
            downgraded["manifest_schema"] = schema
            with self.subTest(schema=schema):
                self.assertEqual(
                    scoring_block_reason(downgraded),
                    "invalid_execution_binding",
                )

    def test_genuine_historical_v3_v1_receipt_remains_interpretable(self) -> None:
        import operant_lab.artifacts as artifacts

        historical_binding = copy.deepcopy(_binding())
        historical_binding["schema"] = "operant-execution-binding.v1"
        historical_binding.pop("subject_runtime")
        historical_binding["source_state"].pop("reconstruction")
        pre_dispatch = {
            **{
                key: value
                for key, value in historical_binding.items()
                if key
                not in {
                    "model_observation",
                    "pre_dispatch_sha256",
                    "completion_sha256",
                }
            },
            "requested_model_id": historical_binding["model_observation"][
                "requested_model_id"
            ],
        }
        historical_binding["pre_dispatch_sha256"] = artifacts._canonical_hash(
            pre_dispatch
        )
        historical_manifest = asdict(_manifest(_binding()))
        historical_manifest.pop("manifest_core_sha256")
        historical_manifest["manifest_schema"] = "operant-run-manifest.v3"
        historical_manifest["execution_binding"] = historical_binding
        self.assertEqual(validate_run_manifest_v3(historical_manifest), [])
        self.assertEqual(
            scoring_block_reason(historical_manifest),
            "incomplete_execution_receipt",
        )

    def test_genuine_historical_v4_v2_receipt_remains_interpretable(self) -> None:
        import operant_lab.artifacts as artifacts

        historical_binding = copy.deepcopy(_binding())
        historical_binding["schema"] = "operant-execution-binding.v2"
        historical_binding.pop("subject_runtime")
        pre_dispatch = {
            **{
                key: value
                for key, value in historical_binding.items()
                if key
                not in {
                    "model_observation",
                    "pre_dispatch_sha256",
                    "completion_sha256",
                }
            },
            "requested_model_id": historical_binding["model_observation"][
                "requested_model_id"
            ],
        }
        historical_binding["pre_dispatch_sha256"] = artifacts._canonical_hash(
            pre_dispatch
        )
        historical_manifest = asdict(_manifest(_binding()))
        historical_manifest["manifest_schema"] = "operant-run-manifest.v4"
        historical_manifest["execution_binding"] = historical_binding
        historical_manifest["manifest_core_sha256"] = artifacts._canonical_hash(
            {
                key: value
                for key, value in historical_manifest.items()
                if key != "manifest_core_sha256"
            }
        )
        self.assertEqual(validate_run_manifest_v4(historical_manifest), [])
        self.assertEqual(
            scoring_block_reason(historical_manifest),
            "incomplete_execution_receipt",
        )

    def test_contradictory_capture_states_and_nonfinite_cost_are_invalid(self) -> None:
        manifest = asdict(_manifest(_binding()))
        source = manifest["execution_binding"]["source_state"]
        source.update(
            {
                "commit": "UNKNOWN",
                "dirty": False,
                "dirty_state_sha256": "a" * 64,
                "reconstruction": "CLEAN_COMMIT",
            }
        )
        source_errors = validate_execution_binding(manifest["execution_binding"])
        self.assertIn(
            "unknown source state carries contradictory evidence",
            source_errors,
        )

        dependency_binding = _binding()
        dependency_binding["dependency_lock"].update(
            {
                "status": "LOCKFILE_PRESENT_UNVERIFIED",
                "files": [],
                "sha256": "a" * 64,
                "reason": "ACTIVE_ENVIRONMENT_NOT_PROVEN",
            }
        )
        self.assertIn(
            "lockfile evidence is incomplete",
            validate_execution_binding(dependency_binding),
        )
        malformed_dependency = asdict(_manifest(_binding()))
        malformed_dependency["execution_binding"]["dependency_lock"]["files"] = [{}]
        self.assertEqual(
            scoring_block_reason(malformed_dependency),
            "invalid_execution_binding",
        )

        for persisted_cost in (float("nan"), float("inf"), float("-inf")):
            persisted = asdict(_manifest(_binding()))
            persisted["cost_usd"] = persisted_cost
            with self.subTest(persisted_cost=persisted_cost):
                self.assertEqual(
                    scoring_block_reason(persisted),
                    "invalid_execution_binding",
                )

        for cost in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(cost=cost):
                with self.assertRaisesRegex(ValueError, "cost_usd"):
                    RunManifest(
                        **{
                            key: value
                            for key, value in asdict(_manifest(_binding())).items()
                            if key
                            not in {
                                "manifest_schema",
                                "confirmatory_eligible",
                                "manifest_core_sha256",
                                "cost_usd",
                            }
                        },
                        cost_usd=cost,
                    )

    def test_validators_are_total_over_malformed_json_shapes(self) -> None:
        malformed_values = (None, True, 1, 1.5, "text", [], [1], {}, {"x": 1})
        for value in malformed_values:
            with self.subTest(root_value=value):
                self.assertTrue(validate_execution_binding(value))
                self.assertTrue(validate_run_manifest_v3(value))
                self.assertTrue(validate_run_manifest_v4(value))
                self.assertTrue(validate_run_manifest_v5(value))
                expected = (
                    None
                    if isinstance(value, dict)
                    and "execution_binding" not in value
                    else "invalid_execution_binding"
                )
                self.assertEqual(scoring_block_reason(value), expected)

        mutations = (
            ("manifest_schema", []),
            ("evaluation_role", {}),
            ("execution_binding.schema", []),
            ("execution_binding.dependency_lock.status", []),
            ("execution_binding.dependency_lock.reason", []),
            ("execution_binding.model_observation", 1),
            ("execution_binding.model_observation.comparison_status", []),
            ("execution_binding.source_state.dirty", []),
            ("execution_binding.subject_runtime", []),
            ("execution_binding.subject_runtime.status", []),
            ("execution_binding.subject_runtime.reason", {}),
        )
        baseline = asdict(_manifest(_binding()))
        for path, value in mutations:
            changed = copy.deepcopy(baseline)
            cursor = changed
            parts = path.split(".")
            for part in parts[:-1]:
                cursor = cursor[part]
            cursor[parts[-1]] = value
            with self.subTest(path=path):
                self.assertEqual(
                    scoring_block_reason(changed),
                    "invalid_execution_binding",
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

    def test_persisted_nonobject_receipt_fails_closed(self) -> None:
        import operant_lab.export as public_export

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "lab" / "runs" / "fixture-r1" / "fixture.case.json"
            path.parent.mkdir(parents=True)
            path.write_text("[]\n", encoding="utf-8")
            self.assertEqual(
                receipt_scoring_block_reason(
                    root,
                    run_label="fixture-r1",
                    case_id="fixture.case",
                    require_receipt=True,
                ),
                "invalid_execution_binding",
            )
            with (
                mock.patch.object(
                    public_export,
                    "_load_score_operant",
                    return_value=object(),
                ),
                mock.patch.object(
                    public_export,
                    "load_decision_cases",
                    return_value={},
                ),
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "receipt root is not an object",
                ):
                    public_export.load_lab_decision_rows(
                        root / "lab" / "runs",
                        {"fixture-r1"},
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
