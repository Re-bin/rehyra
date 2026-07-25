from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol

from .processes import terminate_process_tree


@dataclass(frozen=True)
class IsolationAttestation:
    backend: str
    level: str
    filesystem_isolated: bool
    network_isolated: bool
    details: str

    def as_dict(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "level": self.level,
            "filesystem_isolated": self.filesystem_isolated,
            "network_isolated": self.network_isolated,
            "details": self.details,
        }


@dataclass(frozen=True)
class ExecutionRequest:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    env: Mapping[str, str] = field(default_factory=dict)
    stdin: bytes | None = None


@dataclass(frozen=True)
class ExecutionResult:
    returncode: int | None
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    timed_out: bool
    attestation: IsolationAttestation


class ExecutionBackend(Protocol):
    @property
    def attestation(self) -> IsolationAttestation: ...

    async def run(self, request: ExecutionRequest) -> ExecutionResult: ...


class LocalProcessBackend:
    """Portable shell-free backend.

    It is intentionally honest: a local subprocess is not a security boundary.
    A container or remote sandbox can implement the same protocol later without
    changing tasks, evaluators, or the research loop.
    """

    @property
    def attestation(self) -> IsolationAttestation:
        return IsolationAttestation(
            backend="local_process",
            level="advisory",
            filesystem_isolated=False,
            network_isolated=False,
            details="Fresh cwd and process; no attested filesystem or network boundary.",
        )

    async def run(self, request: ExecutionRequest) -> ExecutionResult:
        started = time.monotonic()
        env = os.environ.copy()
        env.update(request.env)
        process = await asyncio.create_subprocess_exec(
            *request.argv,
            cwd=request.cwd,
            env=env,
            start_new_session=os.name == "posix",
            stdin=asyncio.subprocess.PIPE if request.stdin is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request.stdin),
                timeout=request.timeout_seconds,
            )
        except TimeoutError:
            await terminate_process_tree(process)
            stdout, stderr = await process.communicate()
            return ExecutionResult(
                returncode=None,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=time.monotonic() - started,
                timed_out=True,
                attestation=self.attestation,
            )
        except asyncio.CancelledError:
            await terminate_process_tree(process)
            raise
        return ExecutionResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=time.monotonic() - started,
            timed_out=False,
            attestation=self.attestation,
        )
