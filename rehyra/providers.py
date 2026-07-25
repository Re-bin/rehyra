from __future__ import annotations

import asyncio
import contextlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Mapping, Protocol

from .contracts import AgentCall, AgentConfig
from .processes import terminate_process_tree
from .util import atomic_write_json


async def terminate_process(process: asyncio.subprocess.Process) -> None:
    await terminate_process_tree(process)


class AgentRole(StrEnum):
    CONTEXT = "context"
    PROPOSAL = "proposal"
    EVALUATOR_ARCHITECT = "evaluator_architect"
    EVALUATOR_CRITIC = "evaluator_critic"


class AgentAccess(StrEnum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


@dataclass(frozen=True)
class AgentRequest:
    role: AgentRole
    prompt: str
    workspace: Path
    output_dir: Path
    timeout_seconds: int
    access: AgentAccess
    output_schema: dict[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    allow_external_tools: bool = False


class AgentProvider(Protocol):
    """Provider-neutral ADK surface consumed by the research harness."""

    @property
    def descriptor(self) -> dict[str, Any]: ...

    async def run(self, request: AgentRequest) -> AgentCall: ...


def validate_schema_subset(
    value: Any,
    schema: Mapping[str, Any],
    *,
    path: str = "$",
) -> None:
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not one of the allowed values")
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": (
            isinstance(value, (int, float)) and not isinstance(value, bool)
        ),
        "null": value is None,
    }
    if isinstance(expected, str) and not matches.get(expected, False):
        raise ValueError(f"{path} must have type {expected}")
    if expected == "object":
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise ValueError(f"{path} is missing required key {key!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise ValueError(
                    f"{path} contains unknown keys: {', '.join(sorted(unknown))}"
                )
        for key, child in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                validate_schema_subset(
                    child,
                    child_schema,
                    path=f"{path}.{key}",
                )
    if expected == "array":
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            raise ValueError(f"{path} has fewer than {minimum} items")
        if isinstance(maximum, int) and len(value) > maximum:
            raise ValueError(f"{path} has more than {maximum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, child in enumerate(value):
                validate_schema_subset(
                    child,
                    item_schema,
                    path=f"{path}[{index}]",
                )


def record_request(request: AgentRequest) -> None:
    atomic_write_json(
        request.output_dir / "request.json",
        {
            "role": request.role.value,
            "access": request.access.value,
            "workspace": str(request.workspace),
            "timeout_seconds": request.timeout_seconds,
            "prompt": request.prompt,
            "output_schema": request.output_schema,
            "metadata": dict(request.metadata),
            "external_tools": (
                "allowed" if request.allow_external_tools else "disabled"
            ),
        },
    )


def merge_jsonl_usage(events_path: Path) -> dict[str, int]:
    totals: dict[str, int] = {}
    if not events_path.exists():
        return totals
    for line in events_path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage")
        if not isinstance(usage, dict):
            continue
        for key, value in usage.items():
            if isinstance(value, int):
                totals[key] = totals.get(key, 0) + value
    return totals


_EXTERNAL_CODEX_ITEM_TYPES = {
    "computer_use",
    "image_generation",
    "mcp_tool_call",
    "tool_search",
    "web_search",
}


def forbidden_external_events(events_path: Path) -> tuple[str, ...]:
    found: set[str] = set()
    if not events_path.is_file():
        return ()
    for line in events_path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        item = event.get("item")
        if isinstance(item, dict):
            item_type = item.get("type")
            if item_type in _EXTERNAL_CODEX_ITEM_TYPES:
                found.add(str(item_type))
        event_type = event.get("type")
        if event_type in _EXTERNAL_CODEX_ITEM_TYPES:
            found.add(str(event_type))
    return tuple(sorted(found))


def audit_agent_evidence(run_dir: Path) -> dict[str, Any]:
    requests = sorted(run_dir.rglob("request.json"))
    violations: list[str] = []
    audited_transcripts = 0
    forbidden_event_count = 0
    for request_path in requests:
        try:
            request = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            violations.append(
                f"{request_path.relative_to(run_dir)}: invalid request: {exc}"
            )
            continue
        if request.get("external_tools") != "disabled":
            violations.append(
                f"{request_path.relative_to(run_dir)}: external tools not disabled"
            )
        events_path = request_path.parent / "events.jsonl"
        if events_path.is_file():
            audited_transcripts += 1
            forbidden = forbidden_external_events(events_path)
            if forbidden:
                forbidden_event_count += len(forbidden)
                violations.append(
                    f"{events_path.relative_to(run_dir)}: forbidden events "
                    + ", ".join(forbidden)
                )
    return {
        "request_count": len(requests),
        "audited_transcript_count": audited_transcripts,
        "external_tools_policy": "disabled",
        "forbidden_event_count": forbidden_event_count,
        "violation_count": len(violations),
        "violations": violations,
        "passed": not violations,
    }


def record_agent_call(output_dir: Path, call: AgentCall) -> None:
    atomic_write_json(output_dir / "call.json", call.as_dict())


def load_recorded_structured_call(output_dir: Path) -> AgentCall | None:
    call_path = output_dir / "call.json"
    if call_path.is_file():
        raw = json.loads(call_path.read_text(encoding="utf-8"))
        return AgentCall(
            ok=bool(raw["ok"]),
            returncode=raw.get("returncode"),
            duration_seconds=float(raw["duration_seconds"]),
            error=raw.get("error"),
            usage=dict(raw.get("usage", {})),
            output=raw.get("output"),
        )
    last_message = output_dir / "last_message.json"
    if not last_message.is_file():
        return None
    try:
        output = json.loads(last_message.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(output, dict):
        return None
    return AgentCall(
        ok=True,
        returncode=0,
        duration_seconds=0.0,
        error=None,
        usage=merge_jsonl_usage(output_dir / "events.jsonl"),
        output=output,
    )


class CodexAgentProvider:
    """Codex implementation of the provider-neutral ADK protocol."""

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "provider": "codex",
            "model": self.config.model,
            "reasoning_effort": self.config.reasoning_effort,
        }

    def _argv(
        self,
        *,
        request: AgentRequest,
        last_message_path: Path,
        schema_path: Path | None,
    ) -> list[str]:
        argv = [
            self.config.codex_path,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--model",
            self.config.model,
            "--json",
            "--color",
            "never",
            "--cd",
            str(request.workspace),
        ]
        if request.allow_external_tools:
            sandbox = (
                "read-only"
                if request.access == AgentAccess.READ_ONLY
                else "workspace-write"
            )
            argv.extend(["--sandbox", sandbox])
        else:
            argv.extend(
                [
                    "--strict-config",
                    "--config",
                    'web_search="disabled"',
                    "--config",
                    'default_permissions="hyra_agent"',
                    "--config",
                    (
                        "permissions.hyra_agent.filesystem="
                        + self._permission_filesystem(request)
                    ),
                    "--config",
                    "permissions.hyra_agent.network={enabled=false}",
                ]
            )
            for feature in (
                "apps",
                "browser_use",
                "browser_use_external",
                "browser_use_full_cdp_access",
                "computer_use",
                "image_generation",
                "in_app_browser",
                "multi_agent",
                "plugins",
                "skill_search",
                "tool_suggest",
            ):
                argv.extend(["--disable", feature])
        if self.config.reasoning_effort:
            argv.extend(
                [
                    "--config",
                    f'model_reasoning_effort="{self.config.reasoning_effort}"',
                ]
            )
        argv.extend(self.config.extra_args)
        if schema_path is not None:
            argv.extend(["--output-schema", str(schema_path)])
        argv.extend(["--output-last-message", str(last_message_path), "-"])
        return argv

    @staticmethod
    def _permission_filesystem(request: AgentRequest) -> str:
        roots = {str(Path(sys.prefix).resolve())}
        homebrew = Path("/opt/homebrew")
        if homebrew.is_dir():
            roots.add(str(homebrew))
        workspace_access = (
            "read"
            if request.access == AgentAccess.READ_ONLY
            else "write"
        )
        items = [
            f'{json.dumps(":minimal")}={json.dumps("read")}',
            *(
                f"{json.dumps(root)}={json.dumps('read')}"
                for root in sorted(roots)
            ),
            (
                f"{json.dumps(str((request.output_dir / '.tmp').resolve()))}="
                f"{json.dumps('write')}"
            ),
            (
                f'{json.dumps(":workspace_roots")}='
                "{"
                f'{json.dumps(".")}={json.dumps(workspace_access)}'
                "}"
            ),
        ]
        return "{" + ",".join(items) + "}"

    async def run(self, request: AgentRequest) -> AgentCall:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        request.workspace.mkdir(parents=True, exist_ok=True)
        temporary_dir = request.output_dir / ".tmp"
        temporary_dir.mkdir(exist_ok=True)
        record_request(request)
        events_path = request.output_dir / "events.jsonl"
        stderr_path = request.output_dir / "stderr.log"
        schema_path: Path | None = None
        if request.output_schema is not None:
            schema_path = request.output_dir / "output.schema.json"
            schema_path.write_text(
                json.dumps(request.output_schema, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        suffix = ".json" if request.output_schema is not None else ".md"
        last_message_path = request.output_dir / f"last_message{suffix}"
        argv = self._argv(
            request=request,
            last_message_path=last_message_path,
            schema_path=schema_path,
        )
        started = time.monotonic()
        env = os.environ.copy()
        env["TMPDIR"] = str(temporary_dir)
        process = await asyncio.create_subprocess_exec(
            *argv,
            env=env,
            start_new_session=os.name == "posix",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def pump(
            stream: asyncio.StreamReader | None,
            destination: Path,
        ) -> None:
            if stream is None:
                return
            with destination.open("wb") as handle:
                while True:
                    chunk = await stream.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
                    handle.flush()

        stdout_task = asyncio.create_task(pump(process.stdout, events_path))
        stderr_task = asyncio.create_task(pump(process.stderr, stderr_path))
        if process.stdin is None:
            raise RuntimeError("agent subprocess has no stdin")
        process.stdin.write(request.prompt.encode("utf-8"))
        await process.stdin.drain()
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=request.timeout_seconds)
        except TimeoutError:
            await terminate_process(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            return AgentCall(
                ok=False,
                returncode=None,
                duration_seconds=time.monotonic() - started,
                error=f"agent timed out after {request.timeout_seconds}s",
                usage=merge_jsonl_usage(events_path),
            )
        except asyncio.CancelledError:
            await terminate_process(process)
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            raise
        await asyncio.gather(stdout_task, stderr_task)
        duration = time.monotonic() - started
        usage = merge_jsonl_usage(events_path)
        if process.returncode != 0:
            tail = stderr_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            return AgentCall(
                ok=False,
                returncode=process.returncode,
                duration_seconds=duration,
                error=f"agent exited {process.returncode}: {tail}",
                usage=usage,
            )
        if not request.allow_external_tools:
            forbidden = forbidden_external_events(events_path)
            if forbidden:
                return AgentCall(
                    ok=False,
                    returncode=process.returncode,
                    duration_seconds=duration,
                    error=(
                        "agent used forbidden external tools: "
                        + ", ".join(forbidden)
                    ),
                    usage=usage,
                )
        try:
            raw = last_message_path.read_text(encoding="utf-8", errors="replace")
            output: dict[str, Any]
            if request.output_schema is not None:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("structured agent output must be a JSON object")
                validate_schema_subset(parsed, request.output_schema)
                output = parsed
            else:
                output = {"message": raw.strip()}
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return AgentCall(
                ok=False,
                returncode=process.returncode,
                duration_seconds=duration,
                error=f"invalid agent output: {exc}",
                usage=usage,
            )
        return AgentCall(
            ok=True,
            returncode=process.returncode,
            duration_seconds=duration,
            error=None,
            usage=usage,
            output=output,
        )


class CommandAgentProvider:
    """Generic shell-free adapter for Nanobot, an ADK, or another agent CLI.

    The configured argv is executed directly. The prompt is delivered on stdin.
    The child writes its final response to the path in
    ``HYRA_AGENT_OUTPUT_PATH``. Structured roles also receive a JSON schema path
    in ``HYRA_AGENT_SCHEMA_PATH``.
    """

    def __init__(self, config: AgentConfig) -> None:
        if not config.command_argv:
            raise ValueError("command provider requires agent.command_argv")
        self.config = config

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "provider": "command",
            "model": self.config.model,
            "argv_program": self.config.command_argv[0],
        }

    async def run(self, request: AgentRequest) -> AgentCall:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        request.workspace.mkdir(parents=True, exist_ok=True)
        record_request(request)
        output_path = request.output_dir / (
            "last_message.json" if request.output_schema is not None else "last_message.md"
        )
        schema_path: Path | None = None
        if request.output_schema is not None:
            schema_path = request.output_dir / "output.schema.json"
            schema_path.write_text(
                json.dumps(request.output_schema, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        stdout_path = request.output_dir / "stdout.log"
        stderr_path = request.output_dir / "stderr.log"
        env = os.environ.copy()
        env.update(
            {
                "HYRA_AGENT_ROLE": request.role.value,
                "HYRA_AGENT_ACCESS": request.access.value,
                "HYRA_AGENT_OUTPUT_PATH": str(output_path),
                "HYRA_AGENT_WORKSPACE": str(request.workspace),
                "HYRA_AGENT_EXTERNAL_TOOLS": (
                    "allowed" if request.allow_external_tools else "disabled"
                ),
            }
        )
        if schema_path is not None:
            env["HYRA_AGENT_SCHEMA_PATH"] = str(schema_path)
        started = time.monotonic()
        process = await asyncio.create_subprocess_exec(
            *self.config.command_argv,
            cwd=request.workspace,
            env=env,
            start_new_session=os.name == "posix",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(request.prompt.encode("utf-8")),
                timeout=request.timeout_seconds,
            )
        except TimeoutError:
            await terminate_process(process)
            return AgentCall(
                ok=False,
                returncode=None,
                duration_seconds=time.monotonic() - started,
                error=f"agent timed out after {request.timeout_seconds}s",
                usage={},
            )
        except asyncio.CancelledError:
            await terminate_process(process)
            raise
        stdout_path.write_bytes(stdout)
        stderr_path.write_bytes(stderr)
        duration = time.monotonic() - started
        if process.returncode != 0:
            return AgentCall(
                ok=False,
                returncode=process.returncode,
                duration_seconds=duration,
                error=(
                    f"agent exited {process.returncode}: "
                    f"{stderr.decode('utf-8', errors='replace')[-2000:]}"
                ),
                usage={},
            )
        if not output_path.exists():
            output_path.write_bytes(stdout)
        try:
            raw = output_path.read_text(encoding="utf-8", errors="replace")
            if request.output_schema is None:
                output: dict[str, Any] = {"message": raw.strip()}
            else:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    raise ValueError("structured agent output must be a JSON object")
                validate_schema_subset(parsed, request.output_schema)
                output = parsed
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return AgentCall(
                ok=False,
                returncode=process.returncode,
                duration_seconds=duration,
                error=f"invalid agent output: {exc}",
                usage={},
            )
        return AgentCall(
            ok=True,
            returncode=process.returncode,
            duration_seconds=duration,
            error=None,
            usage={},
            output=output,
        )


class NanobotAgentProvider:
    """Optional Nanobot SDK adapter.

    Nanobot remains an optional dependency. Task packages and the scheduler see
    only the same ``AgentProvider`` protocol used by Codex and command agents.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    @property
    def descriptor(self) -> dict[str, Any]:
        return {
            "provider": "nanobot",
            "model": self.config.model,
        }

    async def run(self, request: AgentRequest) -> AgentCall:
        request.output_dir.mkdir(parents=True, exist_ok=True)
        request.workspace.mkdir(parents=True, exist_ok=True)
        record_request(request)
        try:
            from nanobot import Nanobot
        except ImportError as exc:
            return AgentCall(
                ok=False,
                returncode=None,
                duration_seconds=0.0,
                error=f"nanobot SDK is not installed: {exc}",
                usage={},
            )
        started = time.monotonic()
        bot = None
        try:
            bot = Nanobot.from_config(
                workspace=request.workspace,
                model=self.config.model,
            )
            message = request.prompt
            if request.output_schema is not None:
                message += (
                    "\n\nReturn one JSON object matching this exact schema:\n"
                    + json.dumps(
                        request.output_schema,
                        indent=2,
                        ensure_ascii=False,
                    )
                )
            result = await asyncio.wait_for(
                bot.run(
                    message,
                    session_key=(
                        f"hyra:{request.role.value}:{request.output_dir.name}"
                    ),
                    ephemeral=True,
                    model=self.config.model,
                ),
                timeout=request.timeout_seconds,
            )
            content = str(result.content or "").strip()
            usage = dict(result.usage or {})
            last_message = request.output_dir / (
                "last_message.json"
                if request.output_schema is not None
                else "last_message.md"
            )
            last_message.write_text(content, encoding="utf-8")
            if request.output_schema is None:
                output: dict[str, Any] = {"message": content}
            else:
                parsed = json.loads(content)
                if not isinstance(parsed, dict):
                    raise ValueError("structured agent output must be a JSON object")
                validate_schema_subset(parsed, request.output_schema)
                output = parsed
            return AgentCall(
                ok=True,
                returncode=0,
                duration_seconds=time.monotonic() - started,
                error=None,
                usage=usage,
                output=output,
            )
        except TimeoutError:
            return AgentCall(
                ok=False,
                returncode=None,
                duration_seconds=time.monotonic() - started,
                error=f"agent timed out after {request.timeout_seconds}s",
                usage={},
            )
        except (json.JSONDecodeError, ValueError) as exc:
            return AgentCall(
                ok=False,
                returncode=0,
                duration_seconds=time.monotonic() - started,
                error=f"invalid agent output: {exc}",
                usage={},
            )
        except Exception as exc:
            return AgentCall(
                ok=False,
                returncode=None,
                duration_seconds=time.monotonic() - started,
                error=f"nanobot agent failed: {type(exc).__name__}: {exc}",
                usage={},
            )
        finally:
            if bot is not None:
                with contextlib.suppress(Exception):
                    await bot.aclose()


def make_provider(config: AgentConfig) -> AgentProvider:
    provider = config.provider
    if provider == "codex":
        return CodexAgentProvider(config)
    if provider == "command":
        return CommandAgentProvider(config)
    if provider == "nanobot":
        return NanobotAgentProvider(config)
    raise ValueError(f"unsupported agent provider: {provider}")
