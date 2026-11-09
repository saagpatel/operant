"""Process-level local Open Sittings acceptance path."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class LocalE2ETests(unittest.TestCase):
    def test_full_local_sitting_flow(self):
        repo = Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory() as temporary:
            result = subprocess.run(
                [sys.executable, "-m", "sittings.e2e_local", "--workdir", temporary],
                cwd=repo,
                text=True,
                capture_output=True,
                timeout=240,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('"published_rows": 2', result.stdout)
        self.assertIn('"tampered_receipt_rejected": true', result.stdout)
        self.assertIn("local OpenAI-compatible fake provider", result.stdout)


if __name__ == "__main__":
    unittest.main()
