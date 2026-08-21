"""Executable-surface contract tests for bounded failure semantics."""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class CliContractTests(unittest.TestCase):
    def test_invalid_arguments_fail_closed_for_gate_scripts(self) -> None:
        for script in (
            "selftest.py",
            "selftest_selfserve.py",
            "verify_evaluation_split.py",
        ):
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, script, "--not-a-real-option"],
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertNotEqual(
                    result.returncode,
                    0,
                    f"{script} silently accepted an unsupported argument:\n"
                    f"stdout={result.stdout[-500:]}\n"
                    f"stderr={result.stderr[-500:]}",
                )


if __name__ == "__main__":
    unittest.main()
