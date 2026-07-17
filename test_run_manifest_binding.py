#!/usr/bin/env python3
"""Adversarial tests for OPERANT run-level evidence bindings."""

from __future__ import annotations

import argparse
import ast
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
    case_bundle_binding,
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


class RunManifestBindingTests(unittest.TestCase):
    def test_every_manifest_writer_supplies_v2_binding_fields(self) -> None:
        required = {
            "evaluation_role",
            "case_bundle_sha256",
            "case_bundle_case_count",
            "case_split",
        }
        writers = (
            "run_codex_app.py",
            "run_codex_cli.py",
            "run_operant.py",
            "run_orchestration.py",
            "run_suite.py",
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

    def test_written_receipt_carries_v2_nonconfirmatory_contract(self) -> None:
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
        self.assertEqual(written["manifest_schema"], "operant-run-manifest.v2")
        self.assertEqual(written["evaluation_role"], "OPEN_DEVELOPMENT")
        self.assertEqual(
            written["case_bundle_sha256"],
            binding["case_bundle_sha256"],
        )
        self.assertFalse(written["confirmatory_eligible"])

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
                    run_codex_cli,
                    "_canonical_queue_prompt",
                    return_value="canonical prompt",
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
                    run_codex_cli,
                    "_canonical_queue_prompt",
                    return_value=prompt,
                ),
                mock.patch.object(run_codex_cli, "write_run_report", capture),
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
                    run_codex_cli,
                    "_canonical_queue_prompt",
                    return_value=prompt,
                ),
                mock.patch.object(run_codex_cli, "write_run_report", capture),
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
            self.assertEqual(meta["error"], "empty_result")
            self.assertEqual(captured[0].final_answer, "")

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
