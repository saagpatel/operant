from __future__ import annotations

import os
import shlex
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from operant_lab.agent_runners import ShellCommandRunner


class AgentRunnerInterruptTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix", "process-group cleanup is POSIX-specific")
    def test_interrupt_kills_parent_and_descendant_processes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "descendant.pid"
            descendant = "import time; time.sleep(30)"
            parent = (
                "import pathlib,subprocess,sys,time; "
                f"child=subprocess.Popen([sys.executable,'-c',{descendant!r}]); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid)); "
                "time.sleep(30)"
            )
            command = shlex.join([sys.executable, "-c", parent, "{prompt}"])

            def interrupt_after_child(*_args, **_kwargs):
                deadline = time.monotonic() + 5
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                if not pid_file.exists():
                    raise AssertionError("agent did not start its descendant")
                raise KeyboardInterrupt

            with mock.patch(
                "operant_lab.agent_runners.subprocess.Popen.communicate",
                side_effect=interrupt_after_child,
            ), self.assertRaises(KeyboardInterrupt):
                ShellCommandRunner(command, timeout=10).respond("synthetic")

            descendant_pid = int(pid_file.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                try:
                    os.kill(descendant_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail("descendant process survived interrupted runner")


if __name__ == "__main__":
    unittest.main()
