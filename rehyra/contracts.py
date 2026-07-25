from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TaskSpec:
    name: str
    goal: str
    seed_workspace: Path
    submission_relpath: Path
    evaluator_argv: tuple[str, ...]
    objective_key: str
    direction: str
    qualification_key: str = "qualified"
    provenance: dict[str, Any] = field(default_factory=dict)
    evaluator_mode: str = "fixed"
    evaluator_relpath: Path = Path("evaluator.py")
    entrypoint_argv: tuple[str, ...] = ()
    state_capture_env: str | None = None
    state_input_env: str | None = None
    state_description: str = ""
    state_max_bytes: int = 4 * 1024 * 1024
    editable_start_marker: str | None = None
    editable_end_marker: str | None = None
    evaluator_env: dict[str, str] = field(default_factory=dict)
    evaluator_path_prepend: tuple[Path, ...] = ()
    mutation_policy: str = "workspace"

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        base_dir: Path | None = None,
    ) -> "TaskSpec":
        raw = json.loads(path.read_text(encoding="utf-8"))
        base = base_dir or path.parent

        def resolved(value: str) -> Path:
            candidate = Path(value).expanduser()
            return candidate if candidate.is_absolute() else (base / candidate).resolve()

        direction = str(raw["direction"])
        if direction not in {"minimize", "maximize"}:
            raise ValueError("task direction must be 'minimize' or 'maximize'")
        evaluator_mode = str(raw.get("evaluator_mode", "fixed"))
        if evaluator_mode not in {"fixed", "generated"}:
            raise ValueError("evaluator_mode must be 'fixed' or 'generated'")
        mutation_policy = str(raw.get("mutation_policy", "workspace"))
        if mutation_policy not in {"workspace", "submission_only"}:
            raise ValueError(
                "mutation_policy must be 'workspace' or 'submission_only'"
            )
        runtime_state = raw.get("runtime_state") or {}
        if not isinstance(runtime_state, dict):
            raise ValueError("runtime_state must be an object")
        state_capture_env = runtime_state.get("capture_env")
        state_input_env = runtime_state.get("input_env")
        if (state_capture_env is None) != (state_input_env is None):
            raise ValueError(
                "runtime_state capture_env and input_env must be declared together"
            )
        state_max_bytes = int(
            runtime_state.get("max_bytes", 4 * 1024 * 1024)
        )
        if state_max_bytes <= 0:
            raise ValueError("runtime_state max_bytes must be positive")
        editable_region = raw.get("editable_region") or {}
        if not isinstance(editable_region, dict):
            raise ValueError("editable_region must be an object")
        editable_start_marker = editable_region.get("start_marker")
        editable_end_marker = editable_region.get("end_marker")
        if (editable_start_marker is None) != (editable_end_marker is None):
            raise ValueError(
                "editable_region start_marker and end_marker must be declared together"
            )
        if (
            editable_start_marker is not None
            and str(editable_start_marker) == str(editable_end_marker)
        ):
            raise ValueError("editable_region markers must be different")
        for label, marker in (
            ("start_marker", editable_start_marker),
            ("end_marker", editable_end_marker),
        ):
            if marker is not None and (
                not str(marker) or "\n" in str(marker) or "\r" in str(marker)
            ):
                raise ValueError(
                    f"editable_region {label} must be one non-empty line"
                )
        evaluator_env = raw.get("evaluator_env") or {}
        if not isinstance(evaluator_env, dict):
            raise ValueError("evaluator_env must be an object")
        if any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in evaluator_env.items()
        ):
            raise ValueError("evaluator_env names and values must be strings")
        evaluator_path_prepend = raw.get("evaluator_path_prepend") or []
        if not isinstance(evaluator_path_prepend, list) or any(
            not isinstance(item, str) for item in evaluator_path_prepend
        ):
            raise ValueError("evaluator_path_prepend must be an array of paths")
        argv = tuple(str(item) for item in raw.get("evaluator_argv", []))
        if not argv and evaluator_mode == "fixed":
            raise ValueError("fixed task evaluator_argv must not be empty")
        return cls(
            name=str(raw["name"]),
            goal=str(raw["goal"]),
            seed_workspace=resolved(str(raw["seed_workspace"])),
            submission_relpath=Path(str(raw["submission_relpath"])),
            evaluator_argv=argv,
            objective_key=str(raw["objective_key"]),
            direction=direction,
            qualification_key=str(raw.get("qualification_key", "qualified")),
            provenance=dict(raw.get("provenance", {})),
            evaluator_mode=evaluator_mode,
            evaluator_relpath=Path(str(raw.get("evaluator_relpath", "evaluator.py"))),
            entrypoint_argv=tuple(
                str(item) for item in raw.get("entrypoint_argv", [])
            ),
            state_capture_env=(
                str(state_capture_env) if state_capture_env is not None else None
            ),
            state_input_env=(
                str(state_input_env) if state_input_env is not None else None
            ),
            state_description=str(runtime_state.get("description", "")),
            state_max_bytes=state_max_bytes,
            editable_start_marker=(
                str(editable_start_marker)
                if editable_start_marker is not None
                else None
            ),
            editable_end_marker=(
                str(editable_end_marker)
                if editable_end_marker is not None
                else None
            ),
            evaluator_env=dict(evaluator_env),
            evaluator_path_prepend=tuple(
                resolved(item) for item in evaluator_path_prepend
            ),
            mutation_policy=mutation_policy,
        )


@dataclass(frozen=True)
class AgentConfig:
    backend: str
    model: str
    codex_path: str = "codex"
    reasoning_effort: str = ""
    context_timeout_seconds: int = 900
    proposal_timeout_seconds: int = 1800
    extra_args: tuple[str, ...] = ()
    command_argv: tuple[str, ...] = ()

    @property
    def provider(self) -> str:
        """Provider name; ``backend`` remains for v0.1 config compatibility."""
        return self.backend


@dataclass(frozen=True)
class RunConfig:
    name: str
    task_spec: Path
    max_proposals: int
    max_concurrency: int
    queue_target: int
    context_batch_size: int
    agent: AgentConfig
    evaluator_cycles: int = 1

    @classmethod
    def load(cls, path: Path) -> "RunConfig":
        with path.open("rb") as handle:
            raw = tomllib.load(handle)
        run = raw["run"]
        agent = raw["agent"]
        base = path.parent
        task_path = Path(str(run["task_spec"])).expanduser()
        if not task_path.is_absolute():
            task_path = (base / task_path).resolve()

        max_proposals = int(run["max_proposals"])
        max_concurrency = int(run.get("max_concurrency", 1))
        queue_target = int(run.get("queue_target", max_concurrency))
        context_batch_size = int(run.get("context_batch_size", max_concurrency))
        evaluator_cycles = int(run.get("evaluator_cycles", 1))
        for name, value in (
            ("max_proposals", max_proposals),
            ("max_concurrency", max_concurrency),
            ("queue_target", queue_target),
            ("context_batch_size", context_batch_size),
            ("evaluator_cycles", evaluator_cycles),
        ):
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        if evaluator_cycles > max_proposals:
            raise ValueError("evaluator_cycles cannot exceed max_proposals")
        if max_concurrency > max_proposals:
            max_concurrency = max_proposals

        extra_args = tuple(str(item) for item in agent.get("extra_args", []))
        command_argv = tuple(str(item) for item in agent.get("command_argv", []))
        return cls(
            name=str(run["name"]),
            task_spec=task_path,
            max_proposals=max_proposals,
            max_concurrency=max_concurrency,
            queue_target=queue_target,
            context_batch_size=context_batch_size,
            evaluator_cycles=evaluator_cycles,
            agent=AgentConfig(
                backend=str(agent.get("backend", "codex")),
                model=str(agent["model"]),
                codex_path=str(agent.get("codex_path", "codex")),
                reasoning_effort=str(agent.get("reasoning_effort", "")),
                context_timeout_seconds=int(agent.get("context_timeout_seconds", 900)),
                proposal_timeout_seconds=int(agent.get("proposal_timeout_seconds", 1800)),
                extra_args=extra_args,
                command_argv=command_argv,
            ),
        )


@dataclass(frozen=True)
class Inspiration:
    attempt_id: str
    title: str
    hypothesis: str
    instructions: str
    risk: str
    parent_attempt_id: str
    reference_attempt_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempt_id": self.attempt_id,
            "title": self.title,
            "hypothesis": self.hypothesis,
            "instructions": self.instructions,
            "risk": self.risk,
            "parent_attempt_id": self.parent_attempt_id,
            "reference_attempt_ids": list(self.reference_attempt_ids),
        }


@dataclass(frozen=True)
class AgentCall:
    ok: bool
    returncode: int | None
    duration_seconds: float
    error: str | None
    usage: dict[str, Any]
    output: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "returncode": self.returncode,
            "duration_seconds": self.duration_seconds,
            "error": self.error,
            "usage": self.usage,
            "output": self.output,
        }
