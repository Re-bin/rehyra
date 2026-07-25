from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .contracts import TaskSpec
from .util import atomic_write_json


_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class TaskBootstrapper(Protocol):
    """Optional task-package hook; the core never imports benchmark logic."""

    def bootstrap(self, destination: Path) -> Path: ...


@dataclass(frozen=True)
class TaskBundle:
    root: Path
    spec_path: Path
    spec: TaskSpec

    @classmethod
    def load(cls, path: Path) -> "TaskBundle":
        spec_path = path / "task.json" if path.is_dir() else path
        spec = TaskSpec.load(spec_path)
        bundle = cls(root=spec_path.parent, spec_path=spec_path, spec=spec)
        bundle.validate()
        return bundle

    def validate(self) -> None:
        relpath = self.spec.submission_relpath
        if relpath.is_absolute() or ".." in relpath.parts:
            raise ValueError("submission_relpath must stay inside the solution workspace")
        evaluator_relpath = self.spec.evaluator_relpath
        if evaluator_relpath.is_absolute() or ".." in evaluator_relpath.parts:
            raise ValueError("evaluator_relpath must stay inside its version workspace")
        if not self.spec.seed_workspace.is_dir():
            raise ValueError(f"seed workspace does not exist: {self.spec.seed_workspace}")
        submission = self.spec.seed_workspace / relpath
        if not submission.is_file():
            raise ValueError(f"seed submission does not exist: {submission}")
        if self.spec.evaluator_mode == "fixed" and not self.spec.evaluator_argv:
            raise ValueError("fixed evaluator command is required")
        if self.spec.mutation_policy not in {"workspace", "submission_only"}:
            raise ValueError("unsupported mutation_policy")
        if self.spec.editable_start_marker is not None:
            text = submission.read_text(encoding="utf-8")
            assert self.spec.editable_end_marker is not None
            for label, marker in (
                ("start", self.spec.editable_start_marker),
                ("end", self.spec.editable_end_marker),
            ):
                matches = sum(
                    line.rstrip("\r\n") == marker
                    for line in text.splitlines(keepends=True)
                )
                if matches != 1:
                    raise ValueError(
                        f"seed submission must contain exactly one editable {label} marker"
                    )
            if text.index(self.spec.editable_start_marker) >= text.index(
                self.spec.editable_end_marker
            ):
                raise ValueError("editable_region markers are out of order")
        for label, value in (
            ("runtime_state.capture_env", self.spec.state_capture_env),
            ("runtime_state.input_env", self.spec.state_input_env),
        ):
            if value is not None and not _ENV_NAME.fullmatch(value):
                raise ValueError(f"{label} is not a valid environment name")
        for name, value in self.spec.evaluator_env.items():
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(
                    f"evaluator_env.{name} is not a valid environment name"
                )
            if "\x00" in value:
                raise ValueError(f"evaluator_env.{name} contains a null byte")
        for path in self.spec.evaluator_path_prepend:
            if not path.is_dir():
                raise ValueError(
                    f"evaluator_path_prepend directory does not exist: {path}"
                )
        allowed = {"workspace", "submission", "run_dir", "evaluator"}
        for command_name, argv in (
            ("entrypoint_argv", self.spec.entrypoint_argv),
            ("evaluator_argv", self.spec.evaluator_argv),
        ):
            for item in argv:
                if not item or "\x00" in item:
                    raise ValueError(f"{command_name} contains an invalid argv item")
                unknown = set(re.findall(r"\{([^{}]+)\}", item)) - allowed
                if unknown:
                    names = ", ".join(sorted(unknown))
                    raise ValueError(
                        f"{command_name} contains unknown placeholders: {names}"
                    )


def write_task_spec(path: Path, value: dict[str, Any]) -> TaskBundle:
    """Task-author helper. Agent/provider details are deliberately absent."""
    atomic_write_json(path, value)
    return TaskBundle.load(path)


def describe_task(path: Path) -> dict[str, Any]:
    bundle = TaskBundle.load(path)
    return {
        "name": bundle.spec.name,
        "goal": bundle.spec.goal,
        "submission": bundle.spec.submission_relpath.as_posix(),
        "mutation_policy": bundle.spec.mutation_policy,
        "evaluator_mode": bundle.spec.evaluator_mode,
        "objective": {
            "key": bundle.spec.objective_key,
            "direction": bundle.spec.direction,
            "qualification_key": bundle.spec.qualification_key,
        },
        "runtime_state": {
            "enabled": bundle.spec.state_capture_env is not None,
            "description": bundle.spec.state_description,
            "max_bytes": bundle.spec.state_max_bytes,
        },
        "editable_region": {
            "enabled": bundle.spec.editable_start_marker is not None,
            "start_marker": bundle.spec.editable_start_marker,
            "end_marker": bundle.spec.editable_end_marker,
        },
        "evaluator_runtime": {
            "environment_names": sorted(bundle.spec.evaluator_env),
            "path_prepend_count": len(bundle.spec.evaluator_path_prepend),
        },
        "provenance": bundle.spec.provenance,
    }
