from __future__ import annotations

import io
import json
import math
import os
import shlex
import sys
import tempfile
import time
import unittest
from collections import OrderedDict
from pathlib import Path
from unittest import mock

import score_my_agent
import score_operant
import score_variance
from operant_lab import selfserve
from operant_lab.agent_runners import (
    AgentRunner,
    HTTPEndpointRunner,
    PythonEntrypointRunner,
    RunnerResult,
    ShellCommandRunner,
)

ROOT = Path(__file__).resolve().parent


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class BenchmarkIntegrityAdversarialTests(unittest.TestCase):
    @staticmethod
    def _valid_summary() -> dict[str, object]:
        cases = score_operant.load_cases()
        answers = {
            case_id: RunnerResult(
                f"DECISION: {case['expected_decisions'][0]}\nJUSTIFICATION: fixture.",
                True,
            )
            for case_id, case in cases.items()
        }
        decision = selfserve.score_decision(cases, answers)

        class Runner:
            shell = "synthetic"
            descriptor = "synthetic:integrity"

        return selfserve.build_summary(
            label="integrity-fixture",
            runner=Runner(),
            contract_source="synthetic",
            decision=decision,
            orchestration={"status": "skipped", "reason": "fixture", "n": 0},
            n_decision_cases=len(cases),
            n_orch_cases=0,
            cases_glob=None,
            input_binding=selfserve.build_input_binding(
                contract="synthetic",
                decision_cases=cases,
                orchestration_cases={},
                runner_descriptor=Runner.descriptor,
            ),
        )

    def test_standard_selftest_has_no_machine_specific_result_dependency(self) -> None:
        source = (ROOT / "selftest.py").read_text(encoding="utf-8")
        self.assertNotIn("/Users/d/Projects/evals", source)

    def test_duplicate_case_ids_fail_instead_of_overwriting(self) -> None:
        def corpus(pair_id: str, case_id: str) -> dict[str, object]:
            return {
                "axis": "refusal-calibration",
                "pairs": [
                    {
                        "pair_id": pair_id,
                        "tier": 1,
                        "malign": {
                            "id": case_id,
                            "class": "guard_warranted",
                            "expected_decisions": ["REFUSE"],
                            "withholds_dangerous_op_expected": True,
                        },
                        "benign": {
                            "id": f"{pair_id}.benign",
                            "class": "benign_open",
                            "expected_decisions": ["PROCEED"],
                            "withholds_dangerous_op_expected": False,
                        },
                    }
                ],
            }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a_cases.json").write_text(
                json.dumps(corpus("a", "duplicate.malign")), encoding="utf-8"
            )
            (root / "b_cases.json").write_text(
                json.dumps(corpus("b", "duplicate.malign")), encoding="utf-8"
            )
            with mock.patch.dict(
                os.environ, {"OPERANT_CASES": str(root / "*_cases.json")}
            ):
                with self.assertRaisesRegex(ValueError, "duplicate case id"):
                    score_operant.load_cases()

    @unittest.skipUnless(os.name == "posix", "signal names are POSIX-specific")
    def test_signal_termination_is_distinct_and_never_an_answer(self) -> None:
        script = (
            "import os, signal; print('DECISION: REFUSE', flush=True); "
            "os.kill(os.getpid(), signal.SIGTERM)"
        )
        command = shlex.join([sys.executable, "-c", script, "{prompt}"])
        result = ShellCommandRunner(command).respond("synthetic")
        self.assertFalse(result.ok)
        self.assertEqual(result.text, "")
        self.assertEqual(result.error, "signal_SIGTERM")
        self.assertEqual(result.meta.get("signal"), "SIGTERM")

    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-specific")
    def test_timeout_keeps_partial_diagnostics_and_kills_descendants(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "survived.txt"
            child = (
                "import pathlib,time; time.sleep(5); "
                f"pathlib.Path({str(marker)!r}).write_text('survived')"
            )
            parent = (
                "import subprocess,sys,time; "
                "print('partial', flush=True); "
                "print('diagnostic', file=sys.stderr, flush=True); "
                f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                "time.sleep(5)"
            )
            command = shlex.join([sys.executable, "-c", parent, "{prompt}"])
            result = ShellCommandRunner(command, timeout=2.0).respond("synthetic")
            time.sleep(0.3)
            self.assertFalse(result.ok)
            self.assertEqual(result.error, "timeout")
            self.assertGreater(result.meta.get("stdout_bytes", 0), 0)
            self.assertGreater(result.meta.get("stderr_bytes", 0), 0)
            self.assertTrue(result.meta.get("process_group_terminated"))
            self.assertFalse(marker.exists())

    def test_python_adapter_timeout_is_enforced_out_of_process(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = Path(tmp) / "hanging_adapter.py"
            adapter.write_text(
                "import time\n"
                "def respond(_prompt):\n"
                "    time.sleep(5)\n"
                "    return 'DECISION: PROCEED'\n",
                encoding="utf-8",
            )
            started = time.monotonic()
            result = PythonEntrypointRunner(
                f"{adapter}:respond", timeout=0.3
            ).respond("synthetic")
            self.assertLess(time.monotonic() - started, 2.0)
            self.assertFalse(result.ok)
            self.assertEqual(result.error, "timeout")

    def test_python_adapter_isolation_survives_custom_working_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            adapter = root / "adapter.py"
            adapter.write_text(
                "def respond(prompt):\n    return 'DECISION: REFUSE'\n",
                encoding="utf-8",
            )
            cwd = root / "candidate-cwd"
            cwd.mkdir()
            result = PythonEntrypointRunner(
                f"{adapter}:respond", cwd=str(cwd), timeout=2
            ).respond("synthetic")
            self.assertTrue(result.ok, result.error)
            self.assertEqual(result.text, "DECISION: REFUSE")

    def test_http_response_ceiling_rejects_oversized_payload(self) -> None:
        def opener(_request, timeout=None):  # noqa: ARG001
            return _Response(b"0123456789abcdef")

        runner = HTTPEndpointRunner(
            "https://example.invalid/run",
            opener=opener,
            max_answer_bytes=8,
        )
        result = runner.respond("synthetic")
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "response_too_large")
        self.assertEqual(result.text, "")
        self.assertEqual(result.meta.get("response_bytes"), 9)
        self.assertTrue(result.meta.get("response_truncated"))

    def test_dispatch_result_order_is_input_order_not_completion_order(self) -> None:
        class DelayedRunner(AgentRunner):
            def respond(self, prompt: str) -> RunnerResult:
                if "slow" in prompt:
                    time.sleep(0.04)
                return RunnerResult("DECISION: PROCEED", True)

        cases = OrderedDict(
            [
                ("slow", {"task_prompt": "slow"}),
                ("fast-a", {"task_prompt": "fast"}),
                ("fast-b", {"task_prompt": "fast"}),
            ]
        )
        answers = selfserve.dispatch_cases(
            DelayedRunner(), cases, "contract", concurrency=3
        )
        self.assertEqual(list(answers), list(cases))

    def test_failed_dispatch_is_not_retried_or_duplicated(self) -> None:
        class CountingRunner(AgentRunner):
            def __init__(self) -> None:
                self.calls = 0

            def respond(self, _prompt: str) -> RunnerResult:
                self.calls += 1
                return RunnerResult("", False, "synthetic_failure")

        runner = CountingRunner()
        cases = {
            "a": {"task_prompt": "a"},
            "b": {"task_prompt": "b"},
            "c": {"task_prompt": "c"},
        }
        selfserve.dispatch_cases(runner, cases, "contract", concurrency=2)
        self.assertEqual(runner.calls, len(cases))

    def test_special_numbers_and_empty_cohorts_fail_closed(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    selfserve.classify_band(value)

        summary = {
            "ocs": 0.0,
            "decision": {
                "ocs": 0.0,
                "accuracy": 0.0,
                "tpr": 0.0,
                "fpr": 0.0,
                "n_scored": 0,
                "n_cases": 0,
                "unparseable": 0,
                "rate_limited": [],
                "errored": [],
            },
            "orchestration": {"status": "skipped", "missing": []},
        }
        self.assertIn("decision_empty_cohort", selfserve.incomplete_attempt_reasons(summary))

    def test_one_class_cohort_cannot_claim_perfect_ocs(self) -> None:
        summary = score_operant.aggregate(
            [
                {
                    "case_id": "guard-only",
                    "decision": "REFUSE",
                    "withheld": True,
                    "class": "guard_warranted",
                    "decision_accuracy": True,
                    "safe_and_correct": True,
                    "bypass_used": False,
                }
            ]
        )
        self.assertEqual(summary["metric_status"], "UNDEFINED_MISSING_CLASS")

    def test_variance_analysis_rejects_partial_repeat_metrics(self) -> None:
        cases = {
            "guard": {
                "id": "guard",
                "pair_id": "pair",
                "side": "malign",
                "tier": "T1",
                "axis": "fixture",
                "class": "guard_warranted",
                "expected_decisions": ["REFUSE"],
            },
            "benign": {
                "id": "benign",
                "pair_id": "pair",
                "side": "benign",
                "tier": "T1",
                "axis": "fixture",
                "class": "benign_open",
                "expected_decisions": ["PROCEED"],
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            report = Path(tmp) / "guard.txt"
            report.write_text("DECISION: REFUSE\n", encoding="utf-8")
            with mock.patch.object(
                score_variance,
                "receipt_output_scoring_block_reason",
                return_value=None,
            ):
                summary, missing = score_variance.score_repeat(
                    {"guard": report}, cases, run_label="fixture-r1"
                )
        self.assertEqual(missing, 1)
        self.assertEqual(summary, {})

    def test_input_binding_is_order_independent_and_content_sensitive(self) -> None:
        a = {
            "a": {"id": "a", "task_prompt": "first"},
            "b": {"id": "b", "task_prompt": "second"},
        }
        b = {"b": a["b"], "a": a["a"]}
        first = selfserve.build_input_binding(
            contract="contract",
            decision_cases=a,
            orchestration_cases={},
            runner_descriptor="synthetic",
        )
        reordered = selfserve.build_input_binding(
            contract="contract",
            decision_cases=b,
            orchestration_cases={},
            runner_descriptor="synthetic",
        )
        changed = selfserve.build_input_binding(
            contract="changed",
            decision_cases=a,
            orchestration_cases={},
            runner_descriptor="synthetic",
        )
        self.assertEqual(first, reordered)
        self.assertNotEqual(first["input_sha256"], changed["input_sha256"])
        with mock.patch.dict(os.environ, {"TZ": "Pacific/Kiritimati", "NOISE": "x"}):
            perturbed = selfserve.build_input_binding(
                contract="contract",
                decision_cases=a,
                orchestration_cases={},
                runner_descriptor="synthetic",
            )
        self.assertEqual(first, perturbed)

    def test_cli_rejects_nonpositive_concurrency_and_timeout(self) -> None:
        for flag in ("--concurrency", "--timeout"):
            with self.subTest(flag=flag):
                with self.assertRaises(SystemExit):
                    score_my_agent.build_parser().parse_args(
                        ["--adapter", "x:y", "--label", "x", flag, "0"]
                    )

    def test_partial_evaluator_crash_blocks_publication(self) -> None:
        summary = {
            "ocs": 1.0,
            "decision": {
                "ocs": 1.0,
                "accuracy": 1.0,
                "tpr": 1.0,
                "fpr": 0.0,
                "n_scored": 2,
                "n_cases": 2,
                "unparseable": 0,
                "rate_limited": [],
                "errored": [],
                "metric_status": "DEFINED",
            },
            "orchestration": {
                "status": "scored",
                "missing": [],
                "judge_errors": [{"case_id": "case-2", "error": "crash"}],
            },
        }
        self.assertIn(
            "orchestration_evaluator_incomplete",
            selfserve.incomplete_attempt_reasons(summary),
        )

    def test_output_lock_rejects_duplicate_same_label_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with selfserve.output_run_lock(out, "duplicate"):
                with self.assertRaises(RuntimeError):
                    with selfserve.output_run_lock(out, "duplicate"):
                        pass

    def test_output_lock_serializes_different_labels_sharing_singleton_badges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with selfserve.output_run_lock(out, "first"):
                with self.assertRaises(RuntimeError):
                    with selfserve.output_run_lock(out, "second"):
                        pass

    def test_sanitized_output_names_do_not_alias_distinct_labels(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = selfserve.output_paths(out, "a/b")["summary_json"]
            second = selfserve.output_paths(out, "a?b")["summary_json"]
            self.assertNotEqual(first, second)

    def test_summary_never_retains_raw_runner_descriptor(self) -> None:
        cases = score_operant.load_cases()
        answers = {
            case_id: RunnerResult(
                f"DECISION: {case['expected_decisions'][0]}\nJUSTIFICATION: fixture.",
                True,
            )
            for case_id, case in cases.items()
        }

        class Runner:
            shell = "synthetic"
            descriptor = "https://example.invalid/run?token=supersecret"

        summary = selfserve.build_summary(
            label="descriptor-fixture",
            runner=Runner(),
            contract_source="synthetic",
            decision=selfserve.score_decision(cases, answers),
            orchestration={"status": "skipped", "reason": "fixture", "n": 0},
            n_decision_cases=len(cases),
            n_orch_cases=0,
            cases_glob=None,
            input_binding=selfserve.build_input_binding(
                contract="synthetic",
                decision_cases=cases,
                orchestration_cases={},
                runner_descriptor=Runner.descriptor,
            ),
        )
        self.assertNotIn("supersecret", json.dumps(summary))

    def test_summary_rejects_mismatched_or_stale_input_binding(self) -> None:
        class Runner:
            shell = "synthetic"
            descriptor = "synthetic:current"

        binding = selfserve.build_input_binding(
            contract="contract",
            decision_cases={},
            orchestration_cases={},
            runner_descriptor="synthetic:other",
        )
        with self.assertRaisesRegex(ValueError, "runner descriptor"):
            selfserve.build_summary(
                label="binding-fixture",
                runner=Runner(),
                contract_source="synthetic",
                decision={"ocs": 0.0},
                orchestration={"status": "skipped"},
                n_decision_cases=0,
                n_orch_cases=0,
                cases_glob=None,
                input_binding=binding,
            )

    def test_empty_corpus_dry_run_fails_with_explicit_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing*_cases.json"
            with self.assertRaisesRegex(SystemExit, "no decision cases"):
                score_my_agent.main(
                    [
                        "--adapter",
                        "examples/heuristic_agent.py:respond",
                        "--label",
                        "empty-corpus",
                        "--axes",
                        "decision",
                        "--no-judge",
                        "--cases",
                        str(missing),
                        "--dry-run",
                    ]
                )

    def test_interrupted_output_write_leaves_no_partial_projection(self) -> None:
        summary = self._valid_summary()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            original = Path.write_text
            calls = 0

            def fail_second(path, data, *args, **kwargs):  # noqa: ANN001
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic interrupted write")
                return original(path, data, *args, **kwargs)

            with mock.patch.object(Path, "write_text", fail_second):
                with self.assertRaises(OSError):
                    selfserve.write_outputs(out, summary)
            self.assertFalse(
                any(
                    path.exists()
                    for path in selfserve.output_paths(out, "integrity-fixture").values()
                )
            )


if __name__ == "__main__":
    unittest.main()
