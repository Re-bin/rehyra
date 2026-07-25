from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess


def _descendant_pids(root_pid: int) -> tuple[int, ...]:
    if os.name != "posix":
        return ()
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid="],
        check=False,
        capture_output=True,
        text=True,
    )
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent = (int(item) for item in fields)
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)

    ordered: list[int] = []

    def visit(parent: int) -> None:
        for child in children.get(parent, []):
            visit(child)
            ordered.append(child)

    visit(root_pid)
    return tuple(ordered)


async def terminate_process_tree(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    if os.name == "posix":
        descendants = _descendant_pids(process.pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(process.pid, signal.SIGKILL)
        for pid in descendants:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
    else:
        process.kill()
    await process.wait()
