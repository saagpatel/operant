#!/usr/bin/env python3
"""Zero-cost fixture burn-in for every current OPERANT producer path."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
import unittest.mock

import operant_lab.artifacts as artifacts


def _case() -> dict:
    return {
        "id": "fixture.case",
        "axis": "decision",
        "task_prompt": "Choose the safe operating action.",
        "class": "benign_open",
        "expected_decisions": ["PROCEED"],
    }


ANSWER = "DECISION: PROCEED\nJUSTIFICATION: Fixture evidence is sufficient.\n"
ORCHESTRATION_ANSWER = (
    "TIER: standard\nMODEL: fixture-model\nISOLATION: read-only\n"
    "SAFEGUARD: stop on any mismatch\n"
)


class V3FixtureBurnInTests(unittest.TestCase):
    def test_native_producers_complete_and_bind_successful_output(self) -> None:
        import run_operant
        import run_orchestration

        for runner in (run_operant, run_orchestration):
            with self.subTest(runner=runner.__name__), tempfile.TemporaryDirectory() as tmp:
                project = Path(tmp) / "operant-public"
                project.mkdir()
                harness = project / "fixture-harness.py"
                harness.write_text("# fixture harness\n", encoding="utf-8")
                reports = project / "results" / "reports"
                model = "fixture-model"
                answer = (
                    ANSWER
                    if runner is run_operant
                    else ORCHESTRATION_ANSWER
                )
                envelope = json.dumps(
                    {
                        "type": "result",
                        "result": answer,
                        "modelUsage": {model: {}},
                        "total_cost_usd": 0,
                    }
                )
                dispatch = unittest.mock.Mock(
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=envelope,
                        stderr="",
                    )
                )
                fake_subprocess = SimpleNamespace(
                    run=dispatch,
                    DEVNULL=subprocess.DEVNULL,
                    TimeoutExpired=subprocess.TimeoutExpired,
                )
                with (
                    unittest.mock.patch.object(runner, "HERE", project),
                    unittest.mock.patch.object(runner, "REPORTS", reports),
                    unittest.mock.patch.object(runner, "HARNESS_FILES", [harness]),
                    unittest.mock.patch.object(
                        runner.ADAPTER,
                        "command",
                        return_value=["/bin/sh"],
                    ),
                    unittest.mock.patch.object(runner, "subprocess", fake_subprocess),
                ):
                    meta = runner.run_case(
                        _case(),
                        model=model,
                        label=f"{runner.__name__}-fixture-r1",
                        system_prompt="Fixture system contract.",
                        evaluation_role="OPEN_DEVELOPMENT",
                        case_split="fixture",
                    )
                    with self.assertRaises(FileExistsError):
                        runner.run_case(
                            _case(),
                            model=model,
                            label=f"{runner.__name__}-fixture-r1",
                            system_prompt="Fixture system contract.",
                            evaluation_role="OPEN_DEVELOPMENT",
                            case_split="fixture",
                        )
                    prefix = (
                        "operant"
                        if runner is run_operant
                        else "orchestration"
                    )
                    stale_label = f"{runner.__name__}-stale-r1"
                    stale_report = (
                        reports / f"{prefix}__{stale_label}__fixture.case.txt"
                    )
                    stale_report.parent.mkdir(parents=True, exist_ok=True)
                    stale_report.write_text("prior attempt\n", encoding="utf-8")
                    with self.assertRaises(FileExistsError):
                        runner.run_case(
                            _case(),
                            model=model,
                            label=stale_label,
                            system_prompt="Fixture system contract.",
                            evaluation_role="OPEN_DEVELOPMENT",
                            case_split="fixture",
                        )
                self.assertEqual(dispatch.call_count, 1)
                receipt_path = project / meta["lab_report"]
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
                observation = receipt["manifest"]["execution_binding"][
                    "model_observation"
                ]
                self.assertEqual(observation["comparison_status"], "MATCHED")
                self.assertEqual(
                    receipt["manifest"]["execution_binding"][
                        "post_dispatch_runtime"
                    ]["comparison"],
                    "MATCHED",
                )
                self.assertEqual(
                    receipt["manifest"]["execution_binding"][
                        "process_image_identity"
                    ]["reason"],
                    "KERNEL_EXEC_ATTESTATION_NOT_CONFIGURED",
                )
                self.assertIn(
                    receipt["manifest"]["execution_binding"][
                        "post_dispatch_harness_python_environment"
                    ]["comparison"],
                    {"MATCHED", "UNKNOWN"},
                )
                self.assertEqual(
                    receipt["manifest"]["execution_binding"][
                        "subject_environment_linkage"
                    ]["reason"],
                    "SUBPROCESS_ENVIRONMENT_NOT_OBSERVED",
                )
                self.assertEqual(observation["final_answer_sha256"], artifacts.stable_hash(answer))
                self.assertEqual(receipt["final_answer"], answer)
                self.assertEqual(receipt["parse_status"], "ok")
                self.assertIsNone(
                    artifacts.receipt_output_scoring_block_reason(
                        project,
                        run_label=f"{runner.__name__}-fixture-r1",
                        case_id="fixture.case",
                        final_answer=answer,
                        require_receipt=True,
                    )
                )

    def test_native_nonzero_exit_preserves_receipt_but_publishes_no_report(self) -> None:
        import run_operant

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            project.mkdir()
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            reports = project / "results" / "reports"
            model = "fixture-model"
            envelope = json.dumps(
                {
                    "type": "result",
                    "result": ANSWER,
                    "modelUsage": {model: {}},
                }
            )
            fake_subprocess = SimpleNamespace(
                run=unittest.mock.Mock(
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout=envelope,
                        stderr="provider failed",
                    )
                ),
                DEVNULL=subprocess.DEVNULL,
                TimeoutExpired=subprocess.TimeoutExpired,
            )
            with (
                unittest.mock.patch.object(run_operant, "HERE", project),
                unittest.mock.patch.object(run_operant, "REPORTS", reports),
                unittest.mock.patch.object(run_operant, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_operant.ADAPTER,
                    "command",
                    return_value=["fixture-provider"],
                ),
                unittest.mock.patch.object(run_operant, "subprocess", fake_subprocess),
            ):
                meta = run_operant.run_case(
                    _case(),
                    model=model,
                    label="native-nonzero-r1",
                    system_prompt="Fixture system contract.",
                    evaluation_role="OPEN_DEVELOPMENT",
                    case_split="fixture",
                )
            receipt = json.loads(
                (project / meta["lab_report"]).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["parse_status"], "process_exit_nonzero")
            self.assertEqual(receipt["process_exit_code"], 1)
            self.assertEqual(receipt["final_answer"], ANSWER)
            self.assertIsNone(receipt["source_report"])
            self.assertFalse(
                (reports / "operant__native-nonzero-r1__fixture.case.txt").exists()
            )

    def test_native_runtime_candidate_drift_blocks_success_projection(self) -> None:
        import run_operant

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            project.mkdir()
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            executable = project / "fixture-provider"
            executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            executable.chmod(0o755)
            reports = project / "results" / "reports"
            model = "fixture-model"
            envelope = json.dumps(
                {
                    "type": "result",
                    "result": ANSWER,
                    "modelUsage": {model: {}},
                }
            )

            def dispatch(*_args, **_kwargs):
                executable.write_text("#!/bin/sh\nexit 9\n", encoding="utf-8")
                return SimpleNamespace(
                    returncode=0,
                    stdout=envelope,
                    stderr="",
                )

            fake_subprocess = SimpleNamespace(
                run=dispatch,
                DEVNULL=subprocess.DEVNULL,
                TimeoutExpired=subprocess.TimeoutExpired,
            )
            with (
                unittest.mock.patch.object(run_operant, "HERE", project),
                unittest.mock.patch.object(run_operant, "REPORTS", reports),
                unittest.mock.patch.object(run_operant, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_operant.ADAPTER,
                    "command",
                    return_value=[str(executable)],
                ),
                unittest.mock.patch.object(run_operant, "subprocess", fake_subprocess),
            ):
                meta = run_operant.run_case(
                    _case(),
                    model=model,
                    label="native-runtime-drift-r1",
                    system_prompt="Fixture system contract.",
                    evaluation_role="OPEN_DEVELOPMENT",
                    case_split="fixture",
                )
            receipt = json.loads(
                (project / meta["lab_report"]).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["parse_status"], "runtime_candidate_drift")
            self.assertEqual(
                receipt["manifest"]["execution_binding"][
                    "post_dispatch_runtime"
                ]["comparison"],
                "DRIFTED",
            )
            self.assertIsNone(receipt["source_report"])
            self.assertIsNone(receipt["score_row"])
            self.assertFalse(
                (
                    reports
                    / "operant__native-runtime-drift-r1__fixture.case.txt"
                ).exists()
            )

    def test_native_provider_error_envelope_is_not_scoreable(self) -> None:
        import run_operant

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            project.mkdir()
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            reports = project / "results" / "reports"
            model = "fixture-model"
            envelope = json.dumps(
                {
                    "type": "result",
                    "result": ANSWER,
                    "modelUsage": {model: {}},
                    "is_error": True,
                }
            )
            fake_subprocess = SimpleNamespace(
                run=unittest.mock.Mock(
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=envelope,
                        stderr="",
                    )
                ),
                DEVNULL=subprocess.DEVNULL,
                TimeoutExpired=subprocess.TimeoutExpired,
            )
            with (
                unittest.mock.patch.object(run_operant, "HERE", project),
                unittest.mock.patch.object(run_operant, "REPORTS", reports),
                unittest.mock.patch.object(run_operant, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_operant.ADAPTER,
                    "command",
                    return_value=["fixture-provider"],
                ),
                unittest.mock.patch.object(run_operant, "subprocess", fake_subprocess),
            ):
                meta = run_operant.run_case(
                    _case(),
                    model=model,
                    label="native-provider-error-r1",
                    system_prompt="Fixture system contract.",
                    evaluation_role="OPEN_DEVELOPMENT",
                    case_split="fixture",
                )
            receipt = json.loads(
                (project / meta["lab_report"]).read_text(encoding="utf-8")
            )
            self.assertEqual(receipt["parse_status"], "provider_result_error")
            self.assertEqual(receipt["process_exit_code"], 0)
            self.assertIsNone(receipt["source_report"])
            self.assertFalse(
                (
                    reports
                    / "operant__native-provider-error-r1__fixture.case.txt"
                ).exists()
            )

    def test_receipt_failure_cannot_leave_an_orphan_report(self) -> None:
        import run_operant

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            project.mkdir()
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            reports = project / "results" / "reports"
            model = "fixture-model"
            envelope = json.dumps(
                {
                    "type": "result",
                    "result": ANSWER,
                    "modelUsage": {model: {}},
                }
            )
            fake_subprocess = SimpleNamespace(
                run=unittest.mock.Mock(
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=envelope,
                        stderr="",
                    )
                ),
                DEVNULL=subprocess.DEVNULL,
                TimeoutExpired=subprocess.TimeoutExpired,
            )
            with (
                unittest.mock.patch.object(run_operant, "HERE", project),
                unittest.mock.patch.object(run_operant, "REPORTS", reports),
                unittest.mock.patch.object(run_operant, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_operant.ADAPTER,
                    "command",
                    return_value=["fixture-provider"],
                ),
                unittest.mock.patch.object(run_operant, "subprocess", fake_subprocess),
                unittest.mock.patch.object(
                    run_operant,
                    "write_run_report",
                    side_effect=RuntimeError("fixture receipt failure"),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, "receipt failure"):
                    run_operant.run_case(
                        _case(),
                        model=model,
                        label="receipt-failure-r1",
                        system_prompt="Fixture system contract.",
                        evaluation_role="OPEN_DEVELOPMENT",
                        case_split="fixture",
                    )
            self.assertFalse(
                (reports / "operant__receipt-failure-r1__fixture.case.txt").exists()
            )

    def test_codex_cli_success_path_writes_completed_scored_receipt(self) -> None:
        import operant_lab.export as public_export
        import run_codex_cli

        case = _case()
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            queue_path = project / "lab" / "codex-app-queue" / "fixture.json"
            queue_path.parent.mkdir(parents=True)
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            system_prompt = "Fixture system contract."
            prompt = run_codex_cli.ADAPTER.build_prompt(
                case,
                system_prompt,
                "decision",
            ).full_prompt
            queue_path.write_text(
                json.dumps(
                    {
                        "case_id": case["id"],
                        "axis": "decision",
                        "prompt": prompt,
                        "manifest": {
                            "case_id": case["id"],
                            "axis": "decision",
                            "prompt_hash": artifacts.stable_hash(prompt),
                            "prompt_contract": (
                                "codex_app_prompt_embeds_operator_contract"
                            ),
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = argparse.Namespace(
                label="codex-cli-fixture-r1",
                model="fixture-model",
                thinking="medium",
                repeat=1,
                timeout=1,
                dry_run=False,
                resolved_evaluation_role="OPEN_DEVELOPMENT",
                case_bundle=artifacts.case_bundle_binding([case], case_split="fixture"),
            )

            def dispatch(cmd, **_kwargs):
                answer_path = Path(cmd[cmd.index("--output-last-message") + 1])
                answer_path.write_text(ANSWER, encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            scorer = SimpleNamespace(
                score_one=lambda *_args: {
                    "case_id": case["id"],
                    "decision_accuracy": True,
                }
            )
            fake_subprocess = SimpleNamespace(
                run=dispatch,
                TimeoutExpired=subprocess.TimeoutExpired,
            )
            with (
                unittest.mock.patch.object(run_codex_cli, "HERE", project),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "ANSWERS",
                    project / "lab" / "codex-cli-answers",
                ),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "REPORTS",
                    project / "results" / "reports",
                ),
                unittest.mock.patch.object(run_codex_cli, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "_system_prompt",
                    return_value=system_prompt,
                ),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "_load_score_operant",
                    return_value=scorer,
                ),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "codex_command",
                    side_effect=lambda _args, answer_path: [
                        "/bin/sh",
                        "--output-last-message",
                        str(answer_path),
                    ],
                ),
                unittest.mock.patch.object(run_codex_cli, "subprocess", fake_subprocess),
            ):
                meta = run_codex_cli.run_queue_file(
                    queue_path,
                    args,
                    {case["id"]: case},
                )
            receipt = json.loads(
                (project / meta["lab_report"]).read_text(encoding="utf-8")
            )
            observation = receipt["manifest"]["execution_binding"][
                "model_observation"
            ]
            self.assertEqual(observation["comparison_status"], "UNKNOWN")
            self.assertEqual(
                receipt["manifest"]["execution_binding"][
                    "post_dispatch_runtime"
                ]["comparison"],
                "MATCHED",
            )
            self.assertEqual(
                receipt["manifest"]["execution_binding"][
                    "process_image_identity"
                ]["reason"],
                "KERNEL_EXEC_ATTESTATION_NOT_CONFIGURED",
            )
            self.assertIn(
                receipt["manifest"]["execution_binding"][
                    "post_dispatch_harness_python_environment"
                ]["comparison"],
                {"MATCHED", "UNKNOWN"},
            )
            self.assertEqual(
                receipt["manifest"]["execution_binding"][
                    "subject_environment_linkage"
                ]["reason"],
                "SUBPROCESS_ENVIRONMENT_NOT_OBSERVED",
            )
            self.assertNotEqual(
                receipt["manifest"]["execution_binding"]["completion_sha256"],
                "UNKNOWN",
            )
            self.assertEqual(observation["final_answer_sha256"], artifacts.stable_hash(ANSWER))
            self.assertTrue(receipt["score_row"]["decision_accuracy"])
            self.assertEqual(
                receipt["manifest"]["source_queue_sha256"],
                hashlib.sha256(queue_path.read_bytes()).hexdigest(),
            )
            self.assertIsNone(
                artifacts.receipt_output_scoring_block_reason(
                    project,
                    run_label=args.label,
                    case_id=case["id"],
                    final_answer=ANSWER,
                    require_receipt=True,
                )
            )

            failed_args = argparse.Namespace(**vars(args))
            failed_args.label = "codex-cli-nonzero-r1"

            def failed_dispatch(cmd, **_kwargs):
                answer_path = Path(cmd[cmd.index("--output-last-message") + 1])
                answer_path.write_text(ANSWER, encoding="utf-8")
                return SimpleNamespace(
                    returncode=1,
                    stdout="",
                    stderr="provider failed",
                )

            forbidden_scorer = unittest.mock.Mock()
            with (
                unittest.mock.patch.object(run_codex_cli, "HERE", project),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "ANSWERS",
                    project / "lab" / "codex-cli-answers",
                ),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "REPORTS",
                    project / "results" / "reports",
                ),
                unittest.mock.patch.object(run_codex_cli, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "_system_prompt",
                    return_value=system_prompt,
                ),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "_load_score_operant",
                    return_value=SimpleNamespace(score_one=forbidden_scorer),
                ),
                unittest.mock.patch.object(
                    run_codex_cli,
                    "subprocess",
                    SimpleNamespace(
                        run=failed_dispatch,
                        TimeoutExpired=subprocess.TimeoutExpired,
                    ),
                ),
            ):
                failed_meta = run_codex_cli.run_queue_file(
                    queue_path,
                    failed_args,
                    {case["id"]: case},
                )
            failed_receipt = json.loads(
                (project / failed_meta["lab_report"]).read_text(encoding="utf-8")
            )
            forbidden_scorer.assert_not_called()
            self.assertEqual(failed_receipt["parse_status"], "process_exit_nonzero")
            self.assertEqual(failed_receipt["process_exit_code"], 1)
            self.assertIsNone(failed_receipt["score_row"])
            self.assertIsNone(failed_receipt["source_report"])
            self.assertFalse(
                (
                    project
                    / "results"
                    / "reports"
                    / "operant__codex-cli-nonzero-r1__fixture.case.txt"
                ).exists()
            )
            export_scorer = SimpleNamespace(
                score_one=lambda *_args: {
                    "case_id": case["id"],
                    "decision_accuracy": True,
                }
            )
            with (
                unittest.mock.patch.object(
                    public_export,
                    "_load_score_operant",
                    return_value=export_scorer,
                ),
                unittest.mock.patch.object(
                    public_export,
                    "load_decision_cases",
                    return_value={case["id"]: case},
                ),
            ):
                exported, _metadata = public_export.load_lab_decision_rows(
                    project / "lab" / "runs",
                    {args.label, failed_args.label},
                )
            self.assertEqual(len(exported), 1)
            self.assertEqual(
                exported[0]["source_queue_sha256"],
                receipt["manifest"]["source_queue_sha256"],
            )

    def test_codex_app_prepare_record_round_trip_is_bound(self) -> None:
        import run_codex_app

        case = _case()
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            project.mkdir()
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            queue_dir = project / "lab" / "codex-app-queue"
            reports = project / "results" / "reports"
            system_prompt = "Fixture system contract."
            prepare_args = argparse.Namespace(
                axis="decision",
                cases=[case["id"]],
                limit=1,
                model="fixture-model",
                thinking="medium",
                label="codex-app-fixture-r1",
                repeat=1,
                thread_container="projectless:fixture",
                evaluation_role="OPEN_DEVELOPMENT",
                case_split="fixture",
                write_queue=True,
            )
            answer_path = project / "fixture-answer.txt"
            answer_path.write_text(ANSWER, encoding="utf-8")
            queue_path = queue_dir / prepare_args.label / f"{case['id']}.json"
            record_args = argparse.Namespace(
                answer_file=answer_path,
                axis="decision",
                case_id=case["id"],
                queue_file=queue_path,
                model=prepare_args.model,
                evaluation_role="OPEN_DEVELOPMENT",
                label=prepare_args.label,
                thinking=prepare_args.thinking,
                thread_container=prepare_args.thread_container,
                repeat=1,
                case_split="fixture",
                thread_id="fixture-thread",
            )
            with (
                unittest.mock.patch.object(run_codex_app, "HERE", project),
                unittest.mock.patch.object(run_codex_app, "QUEUE_DIR", queue_dir),
                unittest.mock.patch.object(run_codex_app, "REPORTS", reports),
                unittest.mock.patch.object(run_codex_app, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_codex_app,
                    "_load_cases",
                    return_value={case["id"]: case},
                ),
                unittest.mock.patch.object(
                    run_codex_app,
                    "_system_prompt",
                    return_value=system_prompt,
                ),
                unittest.mock.patch("builtins.print"),
            ):
                run_codex_app.prepare(prepare_args)
                clean_queue = json.loads(queue_path.read_text(encoding="utf-8"))
                for field in ("prompt_hash", "prompt_contract", "tool_policy"):
                    corrupted = json.loads(json.dumps(clean_queue))
                    corrupted["manifest"][field] = "tampered"
                    queue_path.write_text(
                        json.dumps(corrupted),
                        encoding="utf-8",
                    )
                    with self.subTest(corrupted_field=field):
                        with self.assertRaises(SystemExit):
                            run_codex_app.record(record_args)
                queue_path.write_text(
                    json.dumps(clean_queue),
                    encoding="utf-8",
                )
                run_codex_app.record(record_args)
            receipt_path = (
                project
                / "lab"
                / "runs"
                / prepare_args.label
                / f"{case['id']}.json"
            )
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            binding = receipt["manifest"]["execution_binding"]
            self.assertNotEqual(binding["completion_sha256"], "UNKNOWN")
            self.assertEqual(binding["post_dispatch_runtime"]["status"], "UNKNOWN")
            self.assertEqual(
                binding["post_dispatch_runtime"]["reason"],
                "NO_EXECUTABLE_DISPATCH",
            )
            self.assertEqual(
                binding["process_image_identity"]["reason"],
                "NO_LOCAL_PROCESS_DISPATCH",
            )
            self.assertIn(
                binding["post_dispatch_harness_python_environment"][
                    "comparison"
                ],
                {"MATCHED", "UNKNOWN"},
            )
            self.assertEqual(
                binding["subject_environment_linkage"]["reason"],
                "NO_LOCAL_PROCESS_DISPATCH",
            )
            self.assertEqual(
                binding["model_observation"]["final_answer_sha256"],
                artifacts.stable_hash(ANSWER),
            )
            self.assertEqual(
                receipt["manifest"]["source_queue_file"],
                queue_path.relative_to(project).as_posix(),
            )
            self.assertEqual(
                receipt["manifest"]["source_queue_sha256"],
                hashlib.sha256(queue_path.read_bytes()).hexdigest(),
            )
            self.assertEqual(receipt["final_answer"], ANSWER)
            self.assertIsNone(
                artifacts.receipt_output_scoring_block_reason(
                    project,
                    run_label=prepare_args.label,
                    case_id=case["id"],
                    final_answer=ANSWER,
                    require_receipt=True,
                )
            )

    def test_native_receipt_flows_through_suite_and_export_consumers(self) -> None:
        import operant_lab.export as public_export
        import run_operant
        import run_suite

        case = _case()
        model = "fixture-model"
        label = "consumer-fixture-r1"
        envelope = json.dumps(
            {
                "type": "result",
                "result": ANSWER,
                "modelUsage": {model: {}},
                "total_cost_usd": 0,
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            project.mkdir()
            harness = project / "fixture-harness.py"
            harness.write_text("# fixture harness\n", encoding="utf-8")
            reports = project / "results" / "reports"
            index = project / "results" / "fixture-index.jsonl"
            fake_subprocess = SimpleNamespace(
                run=unittest.mock.Mock(
                    return_value=SimpleNamespace(
                        returncode=0,
                        stdout=envelope,
                        stderr="",
                    )
                ),
                DEVNULL=subprocess.DEVNULL,
                TimeoutExpired=subprocess.TimeoutExpired,
            )

            class Runner:
                @staticmethod
                def run_case(*args, **kwargs):
                    return run_operant.run_case(*args, **kwargs)

            score_calls: list[str] = []

            class Scorer:
                @staticmethod
                def score_one(_case_value, answer):
                    score_calls.append(answer)
                    return {
                        "case_id": case["id"],
                        "decision_accuracy": True,
                    }

                @staticmethod
                def aggregate(rows):
                    return {"n": len(rows)}

            with (
                unittest.mock.patch.object(run_operant, "HERE", project),
                unittest.mock.patch.object(run_operant, "REPORTS", reports),
                unittest.mock.patch.object(run_operant, "HARNESS_FILES", [harness]),
                unittest.mock.patch.object(
                    run_operant.ADAPTER,
                    "command",
                    return_value=["fixture-provider"],
                ),
                unittest.mock.patch.object(run_operant, "subprocess", fake_subprocess),
                unittest.mock.patch.object(run_suite, "HERE", project),
                unittest.mock.patch.object(run_suite, "REPORTS", reports),
                unittest.mock.patch("builtins.print"),
            ):
                aggregate = run_suite.run_axis(
                    runner=Runner,
                    scorer=Scorer,
                    cases={case["id"]: case},
                    prefix="operant",
                    model=model,
                    label=label,
                    system_prompt="Fixture system contract.",
                    index_path=index,
                    concurrency=1,
                    dry_run=False,
                    evaluation_role="OPEN_DEVELOPMENT",
                    case_split="fixture",
                )
            self.assertEqual(aggregate["n"], 1)
            self.assertEqual(score_calls, [ANSWER])
            self.assertEqual(len(index.read_text(encoding="utf-8").splitlines()), 1)

            export_scorer = SimpleNamespace(
                score_one=lambda *_args: {
                    "case_id": case["id"],
                    "decision_accuracy": True,
                }
            )
            with (
                unittest.mock.patch.object(
                    public_export,
                    "_load_score_operant",
                    return_value=export_scorer,
                ),
                unittest.mock.patch.object(
                    public_export,
                    "load_decision_cases",
                    return_value={case["id"]: case},
                ),
            ):
                rows, metadata = public_export.load_lab_decision_rows(
                    project / "lab" / "runs",
                    {label},
                )
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["parse_status"], "ok")
            self.assertEqual(rows[0]["run_label"], label)
            self.assertIn("consumer-fixture", metadata)


if __name__ == "__main__":
    unittest.main()
