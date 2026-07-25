from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from rehyra.execution import ExecutionRequest, LocalProcessBackend


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat_path = Path("/proc") / str(pid) / "stat"
    if stat_path.is_file():
        stat = stat_path.read_text(encoding="utf-8")
        closing_paren = stat.rfind(")")
        if closing_paren >= 0 and stat[closing_paren + 2 : closing_paren + 3] == "Z":
            return False
    return True


class ProcessTreeTests(unittest.TestCase):
    def test_cancellation_kills_descendant_in_a_new_session(self) -> None:
        async def scenario(root: Path) -> int:
            child_script = root / "child.py"
            child_script.write_text(
                "import os, time\n"
                "from pathlib import Path\n"
                "Path(os.environ['CHILD_PID_PATH']).write_text(str(os.getpid()))\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            parent_script = root / "parent.py"
            parent_script.write_text(
                "import subprocess, sys, time\n"
                f"subprocess.Popen([sys.executable, {str(child_script)!r}], "
                "start_new_session=True)\n"
                "time.sleep(60)\n",
                encoding="utf-8",
            )
            child_pid_path = root / "child.pid"
            task = asyncio.create_task(
                LocalProcessBackend().run(
                    ExecutionRequest(
                        argv=(sys.executable, str(parent_script)),
                        cwd=root,
                        timeout_seconds=120,
                        env={"CHILD_PID_PATH": str(child_pid_path)},
                    )
                )
            )
            for _ in range(200):
                if child_pid_path.is_file():
                    break
                await asyncio.sleep(0.01)
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            return child_pid

        with tempfile.TemporaryDirectory() as raw:
            child_pid = asyncio.run(scenario(Path(raw)))
            deadline = time.monotonic() + 5
            while process_is_running(child_pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(process_is_running(child_pid))


if __name__ == "__main__":
    unittest.main()
