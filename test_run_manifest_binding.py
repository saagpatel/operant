#!/usr/bin/env python3
"""Adversarial tests for OPERANT run-level evidence bindings."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures as cf
import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from operant_lab.artifacts import (
    RunManifest,
    RunReport,
    build_execution_binding,
    case_bundle_binding,
    complete_execution_binding,
    resolve_evaluation_role,
    stable_hash,
    write_run_report,
)


def _case(case_id: str, prompt: str) -> dict:
    return {
        "id": case_id,
        "axis": "refusal-calibration",
        "task_prompt": prompt,
        "class": "benign_open",
        "expected_decisions": ["PROCEED"],
    }


def _execution_binding(requested_model_id: str = "fixture") -> dict:
    root = Path(__file__).resolve().parent
    return build_execution_binding(
        root=root,
        exact_prompt="fixture prompt",
        system_prompt="fixture system",
        stdin_text=None,
        command=["fixture"],
        cwd_class="REPOSITORY_ROOT",
        tool_policy="none",
        timeout_seconds=1,
        output_mode="fixture",
        dispatch_settings={},
        harness_files=[Path(__file__)],
        requested_model_id=requested_model_id,
    )


class RunManifestBindingTests(unittest.TestCase):
    def test_every_manifest_writer_supplies_binding_fields(self) -> None:
        required = {
            "evaluation_role",
            "case_bundle_sha256",
            "case_bundle_case_count",
            "case_split",
            "execution_binding",
        }
        writers = (
            "run_codex_app.py",
            "run_codex_cli.py",
            "run_operant.py",
            "run_orchestration.py",
        )
        for filename in writers:
            tree = ast.parse(
                (Path(__file__).resolve().parent / filename).read_text(
                    encoding="utf-8"
                )
            )
            calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RunManifest"
            ]
            self.assertTrue(calls, filename)
            for call in calls:
                supplied = {keyword.arg for keyword in call.keywords}
                self.assertTrue(required.issubset(supplied), filename)

    def test_case_bundle_is_order_independent(self) -> None:
        first = _case("a", "alpha")
        second = _case("b", "beta")
        forward = case_bundle_binding(
            [first, second],
            case_split="development",
        )
        reverse = case_bundle_binding(
            [second, first],
            case_split="development",
        )
        self.assertEqual(forward, reverse)
        self.assertEqual(forward["case_bundle_case_count"], 2)

    def test_case_or_split_drift_changes_bundle_digest(self) -> None:
        baseline = case_bundle_binding(
            [_case("a", "alpha")],
            case_split="development",
        )
        changed_case = case_bundle_binding(
            [_case("a", "alpha!")],
            case_split="development",
        )
        changed_split = case_bundle_binding(
            [_case("a", "alpha")],
            case_split="surface-holdout",
        )
        self.assertNotEqual(
            baseline["case_bundle_sha256"],
            changed_case["case_bundle_sha256"],
        )
        self.assertNotEqual(
            baseline["case_bundle_sha256"],
            changed_split["case_bundle_sha256"],
        )

    def test_empty_duplicate_and_unidentified_cases_fail(self) -> None:
        with self.assertRaises(ValueError):
            case_bundle_binding([], case_split="development")
        with self.assertRaises(ValueError):
            case_bundle_binding(
                [_case("a", "one"), _case("a", "two")],
                case_split="development",
            )
        with self.assertRaises(ValueError):
            case_bundle_binding(
                [{"task_prompt": "missing id"}],
                case_split="development",
            )

    def test_known_adaptive_family_is_classified(self) -> None:
        self.assertEqual(
            resolve_evaluation_role(
                None,
                run_label="codex-gpt55-refusal-calibration-followup-r2",
            ),
            "ADAPTIVE_DIAGNOSTIC",
        )

    def test_unknown_family_defaults_nonconfirmatory(self) -> None:
        self.assertEqual(
            resolve_evaluation_role(None, run_label="new-experiment-r1"),
            "UNREGISTERED_EXPERIMENTAL_NONCONFIRMATORY",
        )

    def test_confirmatory_role_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            resolve_evaluation_role(
                "CONFIRMATORY",
                run_label="new-experiment",
            )

    def test_manifest_rejects_invalid_bundle_fields(self) -> None:
        common = {
            "run_label": "demo",
            "case_id": "a",
            "axis": "decision",
            "subject_shell": "fixture",
            "model_id": "fixture",
            "prompt_hash": "1" * 64,
            "prompt_contract": "fixture",
            "tool_policy": "none",
            "evaluation_role": "OPEN_DEVELOPMENT",
            "execution_binding": _execution_binding(),
        }
        with self.assertRaises(ValueError):
            RunManifest(
                **common,
                case_bundle_sha256="not-a-digest",
                case_bundle_case_count=1,
            )
        with self.assertRaises(ValueError):
            RunManifest(
                **common,
                case_bundle_sha256="2" * 64,
                case_bundle_case_count=0,
            )

    def test_written_receipt_carries_v3_execution_contract(self) -> None:
        binding = case_bundle_binding(
            [_case("a", "alpha")],
            case_split="development",
        )
        manifest = RunManifest(
            run_label="demo",
            case_id="a",
            axis="decision",
            subject_shell="fixture",
            model_id="fixture",
            prompt_hash="1" * 64,
            prompt_contract="fixture",
            tool_policy="none",
            evaluation_role="OPEN_DEVELOPMENT",
            case_bundle_sha256=str(binding["case_bundle_sha256"]),
            case_bundle_case_count=int(binding["case_bundle_case_count"]),
            case_split=str(binding["case_split"]),
            execution_binding=complete_execution_binding(
                _execution_binding(),
                provider_reported_candidates=[],
                evidence_source="NOT_EXPOSED",
                raw_result_envelope="fixture",
                final_answer="fixture",
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = write_run_report(
                Path(tmp),
                RunReport(
                    manifest=manifest,
                    parse_status="ok",
                    final_answer="fixture",
                ),
            )
            written = json.loads(path.read_text(encoding="utf-8"))["manifest"]
        self.assertEqual(written["manifest_schema"], "operant-run-manifest.v3")
        self.assertEqual(written["evaluation_role"], "OPEN_DEVELOPMENT")
        self.assertEqual(
            written["case_bundle_sha256"],
            binding["case_bundle_sha256"],
        )
        self.assertFalse(written["confirmatory_eligible"])

    def test_run_receipt_is_no_clobber(self) -> None:
        binding = case_bundle_binding(
            [_case("a", "alpha")],
            case_split="development",
        )
        execution = complete_execution_binding(
            _execution_binding(),
            provider_reported_candidates=[],
            evidence_source="NOT_EXPOSED",
            raw_result_envelope="fixture",
            final_answer="fixture",
        )
        report = RunReport(
            manifest=RunManifest(
                run_label="demo",
                case_id="a",
                axis="decision",
                subject_shell="fixture",
                model_id="fixture",
                prompt_hash="1" * 64,
                prompt_contract="fixture",
                tool_policy="none",
                evaluation_role="OPEN_DEVELOPMENT",
                case_bundle_sha256=str(binding["case_bundle_sha256"]),
                case_bundle_case_count=int(binding["case_bundle_case_count"]),
                case_split=str(binding["case_split"]),
                execution_binding=execution,
            ),
            parse_status="ok",
            final_answer="fixture",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def attempt() -> str:
                try:
                    write_run_report(root, report)
                    return "created"
                except FileExistsError:
                    return "blocked"

            with cf.ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(lambda _index: attempt(), range(8)))
            self.assertEqual(outcomes.count("created"), 1)
            self.assertEqual(outcomes.count("blocked"), 7)

    def test_app_prepare_serializes_v3_schema_and_pre_dispatch_binding(self) -> None:
        import run_codex_app

        args = argparse.Namespace(
            axis="decision",
            cases=["a"],
            limit=1,
            model="fixture",
            thinking="medium",
            label="fixture-r1",
            repeat=1,
            thread_container=None,
            evaluation_role="OPEN_DEVELOPMENT",
            case_split="development",
            write_queue=False,
        )
        record = {
            "case_id": "a",
            "axis": "decision",
            "prompt": "exact prepared prompt",
        }
        output = io.StringIO()
        with (
            mock.patch.object(
                run_codex_app,
                "_load_cases",
                return_value={"a": _case("a", "alpha")},
            ),
            mock.patch.object(run_codex_app, "_system_prompt", return_value="fixture system"),
            mock.patch.object(run_codex_app.ADAPTER, "queue_record", return_value=record),
            mock.patch.object(
                run_codex_app,
                "build_execution_binding",
                return_value=_execution_binding(),
            ),
            contextlib.redirect_stdout(output),
        ):
            run_codex_app.prepare(args)
        queued = json.loads(output.getvalue())
        self.assertEqual(
            queued["manifest"]["manifest_schema"],
            "operant-run-manifest.v3",
        )
        self.assertEqual(
            queued["manifest"]["execution_binding"]["pre_dispatch_sha256"],
            _execution_binding()["pre_dispatch_sha256"],
        )

    def test_app_prepare_queue_is_no_clobber(self) -> None:
        import run_codex_app

        args = argparse.Namespace(
            axis="decision",
            cases=["a"],
            limit=1,
            model="fixture",
            thinking="medium",
            label="fixture-r1",
            repeat=1,
            thread_container=None,
            evaluation_role="OPEN_DEVELOPMENT",
            case_split="development",
            write_queue=True,
        )
        record = {
            "case_id": "a",
            "axis": "decision",
            "prompt": "exact prepared prompt",
        }
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "project"
            queue_dir = project / "queue"
            with (
                mock.patch.object(
                    run_codex_app,
                    "_load_cases",
                    return_value={"a": _case("a", "alpha")},
                ),
                mock.patch.object(
                    run_codex_app,
                    "_system_prompt",
                    return_value="fixture system",
                ),
                mock.patch.object(
                    run_codex_app.ADAPTER,
                    "queue_record",
                    return_value=record,
                ),
                mock.patch.object(
                    run_codex_app,
                    "build_execution_binding",
                    return_value=_execution_binding(),
                ),
                mock.patch.object(run_codex_app, "HERE", project),
                mock.patch.object(run_codex_app, "QUEUE_DIR", queue_dir),
                mock.patch("builtins.print"),
            ):
                run_codex_app.prepare(args)
                with self.assertRaises(FileExistsError):
                    run_codex_app.prepare(args)

    def test_app_record_refuses_queue_less_historical_backfill(self) -> None:
        import run_codex_app

        with tempfile.TemporaryDirectory() as tmp:
            answer = Path(tmp) / "answer.txt"
            answer.write_text(
                "DECISION: PROCEED\nJUSTIFICATION: fixture\n",
                encoding="utf-8",
            )
            args = argparse.Namespace(
                answer_file=answer,
                axis="decision",
                case_id="a",
                queue_file=None,
                model="fixture",
                evaluation_role="OPEN_DEVELOPMENT",
                label="fixture-r1",
                thinking="medium",
                thread_container=None,
            )
            with (
                mock.patch.object(
                    run_codex_app,
                    "_load_cases",
                    return_value={"a": _case("a", "alpha")},
                ),
                mock.patch.object(
                    run_codex_app,
                    "_system_prompt",
                    return_value="fixture system",
                ),
                mock.patch.object(
                    run_codex_app.ADAPTER,
                    "build_prompt",
                    return_value=SimpleNamespace(full_prompt="fixture prompt"),
                ),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "requires the exact v3 --queue-file",
                ):
                    run_codex_app.record(args)

    def test_cli_rejects_queue_prompt_hash_drift_before_dispatch(self) -> None:
        import run_codex_cli

        case = _case("a", "alpha")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue" / "a.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps(
                    {
                        "case_id": "a",
                        "axis": "decision",
                        "prompt": "tampered",
                        "manifest": {
                            "case_id": "a",
                            "axis": "decision",
                            "prompt_hash": stable_hash("original"),
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = self._cli_args()
            with (
                mock.patch.object(run_codex_cli, "HERE", root),
                mock.patch.object(run_codex_cli.subprocess, "run") as dispatch,
            ):
                with self.assertRaisesRegex(ValueError, "prompt hash mismatch"):
                    run_codex_cli.run_queue_file(queue_path, args, {"a": case})
            dispatch.assert_not_called()

    def test_cli_rejects_coordinated_prompt_and_hash_case_drift(self) -> None:
        import run_codex_cli

        case = _case("a", "alpha")
        tampered_prompt = "coordinated tamper"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue" / "a.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps(
                    {
                        "case_id": "a",
                        "axis": "decision",
                        "prompt": tampered_prompt,
                        "manifest": {
                            "case_id": "a",
                            "axis": "decision",
                            "prompt_hash": stable_hash(tampered_prompt),
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = self._cli_args()
            with (
                mock.patch.object(run_codex_cli, "HERE", root),
                mock.patch.object(
                    run_codex_cli, "_system_prompt", return_value="fixture system"
                ),
                mock.patch.object(
                    run_codex_cli.ADAPTER,
                    "build_prompt",
                    return_value=SimpleNamespace(full_prompt="canonical prompt"),
                ),
                mock.patch.object(run_codex_cli.subprocess, "run") as dispatch,
            ):
                with self.assertRaisesRegex(ValueError, "canonical case"):
                    run_codex_cli.run_queue_file(queue_path, args, {"a": case})
            dispatch.assert_not_called()

    def test_cli_timeout_writes_bound_failure_receipt(self) -> None:
        import run_codex_cli

        case = _case("a", "alpha")
        prompt = "exact queued prompt"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue" / "a.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps(
                    {
                        "case_id": "a",
                        "axis": "decision",
                        "prompt": prompt,
                        "manifest": {
                            "case_id": "a",
                            "axis": "decision",
                            "prompt_hash": stable_hash(prompt),
                        },
                    }
                ),
                encoding="utf-8",
            )
            args = self._cli_args()
            captured = []

            def capture(_root, report):
                captured.append(report)
                out = root / "lab" / "failure.json"
                out.parent.mkdir()
                out.write_text("{}\n", encoding="utf-8")
                return out

            with (
                mock.patch.object(run_codex_cli, "HERE", root),
                mock.patch.object(run_codex_cli, "ANSWERS", root / "answers"),
                mock.patch.object(run_codex_cli, "REPORTS", root / "reports"),
                mock.patch.object(
                    run_codex_cli, "_system_prompt", return_value="fixture system"
                ),
                mock.patch.object(
                    run_codex_cli.ADAPTER,
                    "build_prompt",
                    return_value=SimpleNamespace(
                        full_prompt=prompt,
                        prompt_contract="codex_app_prompt_embeds_operator_contract",
                    ),
                ),
                mock.patch.object(run_codex_cli, "write_run_report", capture),
                mock.patch.object(
                    run_codex_cli,
                    "build_execution_binding",
                    return_value=_execution_binding(),
                ),
                mock.patch.object(
                    run_codex_cli.subprocess,
                    "run",
                    side_effect=subprocess.TimeoutExpired(["codex"], 1),
                ),
            ):
                meta = run_codex_cli.run_queue_file(queue_path, args, {"a": case})
            self.assertEqual(meta["parse_status"], "timeout")
            self.assertEqual(captured[0].parse_status, "timeout")
            self.assertEqual(captured[0].final_answer, "")
            self.assertEqual(
                captured[0].manifest.case_bundle_sha256,
                args.case_bundle["case_bundle_sha256"],
            )

    def test_cli_does_not_reuse_a_prior_answer_file(self) -> None:
        import run_codex_cli

        case = _case("a", "alpha")
        prompt = "exact queued prompt"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            queue_path = root / "queue" / "a.json"
            queue_path.parent.mkdir()
            queue_path.write_text(
                json.dumps(
                    {
                        "case_id": "a",
                        "axis": "decision",
                        "prompt": prompt,
                        "manifest": {
                            "case_id": "a",
                            "axis": "decision",
                            "prompt_hash": stable_hash(prompt),
                        },
                    }
                ),
                encoding="utf-8",
            )
            old_dir = root / "answers" / "target"
            old_dir.mkdir(parents=True)
            (old_dir / "a__old.txt").write_text(
                "DECISION: PROCEED\nJUSTIFICATION: stale\n",
                encoding="utf-8",
            )
            args = self._cli_args()
            captured = []

            def capture(_root, report):
                captured.append(report)
                out = root / "lab" / "empty.json"
                out.parent.mkdir()
                out.write_text("{}\n", encoding="utf-8")
                return out

            with (
                mock.patch.object(run_codex_cli, "HERE", root),
                mock.patch.object(run_codex_cli, "ANSWERS", root / "answers"),
                mock.patch.object(run_codex_cli, "REPORTS", root / "reports"),
                mock.patch.object(
                    run_codex_cli, "_system_prompt", return_value="fixture system"
                ),
                mock.patch.object(
                    run_codex_cli.ADAPTER,
                    "build_prompt",
                    return_value=SimpleNamespace(
                        full_prompt=prompt,
                        prompt_contract="codex_app_prompt_embeds_operator_contract",
                    ),
                ),
                mock.patch.object(run_codex_cli, "write_run_report", capture),
                mock.patch.object(
                    run_codex_cli,
                    "build_execution_binding",
                    return_value=_execution_binding(),
                ),
                mock.patch.object(
                    run_codex_cli,
                    "_load_score_operant",
                    return_value=SimpleNamespace(
                        score_one=lambda *_args: {"decision_accuracy": 0}
                    ),
                ),
                mock.patch.object(
                    run_codex_cli.subprocess,
                    "run",
                    return_value=SimpleNamespace(
                        returncode=1,
                        stdout="",
                        stderr="current failure",
                    ),
                ),
            ):
                meta = run_codex_cli.run_queue_file(queue_path, args, {"a": case})
            self.assertEqual(meta["error"], "process_exit_nonzero")
            self.assertEqual(captured[0].final_answer, "")

    def test_native_runners_preserve_but_never_publish_identity_mismatch(self) -> None:
        import run_operant
        import run_orchestration

        for runner in (run_operant, run_orchestration):
            with self.subTest(runner=runner.__name__), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                reports = root / "reports"
                captured = []

                def capture(_root, report):
                    captured.append(report)
                    out = root / "lab" / "mismatch.json"
                    out.parent.mkdir()
                    out.write_text("{}\n", encoding="utf-8")
                    return out

                result = json.dumps(
                    {
                        "type": "result",
                        "result": "private preserved answer",
                        "modelUsage": {"different-model": {}},
                    }
                )
                with (
                    mock.patch.object(runner, "HERE", root),
                    mock.patch.object(runner, "REPORTS", reports),
                    mock.patch.object(
                        runner.ADAPTER,
                        "build_prompt",
                        return_value=SimpleNamespace(
                            full_prompt="fixture prompt",
                            prompt_contract="fixture",
                            tool_policy="none",
                        ),
                    ),
                    mock.patch.object(
                        runner.ADAPTER,
                        "command",
                        return_value=["fixture"],
                    ),
                    mock.patch.object(
                        runner,
                        "build_execution_binding",
                        return_value=_execution_binding(),
                    ),
                    mock.patch.object(runner, "write_run_report", capture),
                    mock.patch.object(
                        runner.subprocess,
                        "run",
                        return_value=SimpleNamespace(
                            returncode=1,
                            stdout=result,
                            stderr="",
                        ),
                    ),
                ):
                    meta = runner.run_case(
                        _case("a", "alpha"),
                        model="fixture",
                        label="fixture-r1",
                        system_prompt="fixture system",
                        evaluation_role="OPEN_DEVELOPMENT",
                        case_split="development",
                    )
                self.assertEqual(meta["error"], "identity_blocked:mismatch")
                self.assertFalse(reports.exists())
                self.assertEqual(
                    captured[0].parse_status,
                    "identity_blocked:mismatch",
                )
                self.assertEqual(
                    captured[0].final_answer,
                    "private preserved answer",
                )
                self.assertIsNone(captured[0].score_row)
                self.assertEqual(captured[0].process_exit_code, 1)

    def test_suite_ignores_stale_report_not_returned_by_attempt(self) -> None:
        import run_suite

        case = _case("a", "alpha")

        class Runner:
            @staticmethod
            def run_case(*_args, **_kwargs):
                return {"case_id": "a", "error": "TimeoutExpired"}

        class Scorer:
            @staticmethod
            def score_one(*_args, **_kwargs):
                raise AssertionError("stale report must not be scored")

            @staticmethod
            def aggregate(rows):
                return {"n": len(rows)}

        with tempfile.TemporaryDirectory() as tmp:
            reports = Path(tmp) / "reports"
            reports.mkdir()
            (reports / "operant__target__a.txt").write_text(
                "DECISION: PROCEED\nJUSTIFICATION: stale\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(run_suite, "REPORTS", reports),
                mock.patch("builtins.print"),
            ):
                result = run_suite.run_axis(
                    runner=Runner,
                    scorer=Scorer,
                    cases={"a": case},
                    prefix="operant",
                    model="fixture",
                    label="target",
                    system_prompt="fixture",
                    index_path=Path(tmp) / "index.jsonl",
                    concurrency=1,
                    dry_run=False,
                    evaluation_role="OPEN_DEVELOPMENT",
                    case_split="development",
                )
        self.assertEqual(result["n"], 0)
        self.assertEqual(result["missing"], ["a"])

    def test_suite_never_scores_identity_blocked_attempt(self) -> None:
        import run_suite

        case = _case("a", "alpha")
        blocked_binding = complete_execution_binding(
            _execution_binding(),
            provider_reported_candidates=["different-model"],
            evidence_source="provider_result_modelUsage",
            raw_result_envelope=b'{"model":"different-model"}',
            final_answer="blocked",
        )

        class Runner:
            @staticmethod
            def run_case(*_args, **_kwargs):
                return {
                    "case_id": "a",
                    "report": "operant-public/reports/operant__target__a.txt",
                    "execution_binding": blocked_binding,
                }

        class Scorer:
            @staticmethod
            def score_one(*_args, **_kwargs):
                raise AssertionError("identity-blocked output must not be scored")

            @staticmethod
            def aggregate(rows):
                return {"n": len(rows)}

        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "operant-public"
            reports = project / "reports"
            reports.mkdir(parents=True)
            (reports / "operant__target__a.txt").write_text(
                "DECISION: PROCEED\nJUSTIFICATION: blocked\n",
                encoding="utf-8",
            )
            with (
                mock.patch.object(run_suite, "HERE", project),
                mock.patch.object(run_suite, "REPORTS", reports),
                mock.patch("builtins.print"),
            ):
                result = run_suite.run_axis(
                    runner=Runner,
                    scorer=Scorer,
                    cases={"a": case},
                    prefix="operant",
                    model="fixture",
                    label="target",
                    system_prompt="fixture",
                    index_path=project / "index.jsonl",
                    concurrency=1,
                    dry_run=False,
                    evaluation_role="OPEN_DEVELOPMENT",
                    case_split="development",
                )
        self.assertEqual(result["n"], 0)
        self.assertEqual(result["missing"], ["a"])

    @staticmethod
    def _cli_args() -> argparse.Namespace:
        binding = case_bundle_binding(
            [_case("a", "alpha")],
            case_split="development",
        )
        return argparse.Namespace(
            label="target",
            model="fixture",
            thinking="medium",
            repeat=1,
            timeout=1,
            dry_run=False,
            resolved_evaluation_role="OPEN_DEVELOPMENT",
            case_bundle=binding,
        )


if __name__ == "__main__":
    unittest.main()
