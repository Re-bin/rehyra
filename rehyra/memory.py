from __future__ import annotations

import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from .contracts import TaskSpec
from .util import append_jsonl, atomic_write_json, sha256_file, utc_now


_PRIVATE_EVALUATION_KEYS = {
    "argv",
    "command",
    "cwd",
    "evaluator_argv",
    "isolation",
    "submission",
    "workspace",
}
_ABSOLUTE_PATH = re.compile(
    r"(?<![A-Za-z0-9:/])/(?:[^\s,\"'\]\)]+)"
)
_SOURCE_MAX_FILES = 64
_SOURCE_MAX_FILE_BYTES = 1024 * 1024
_SOURCE_MAX_TOTAL_BYTES = 4 * 1024 * 1024
_STATE_MAX_FILES = 8
_STATE_MAX_TOTAL_BYTES = 64 * 1024 * 1024
_LOG_MAX_BYTES = 4096
_IGNORED_PARTS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".hyra_context",
}


def agent_visible_evaluation(value: Any, *, key: str = "") -> Any:
    normalized = key.lower()
    if (
        normalized in _PRIVATE_EVALUATION_KEYS
        or normalized.endswith("_argv")
        or normalized.endswith("_command")
        or normalized.endswith("_path")
    ):
        return None
    if isinstance(value, dict):
        return {
            child_key: visible
            for child_key, child_value in value.items()
            if (
                visible := agent_visible_evaluation(
                    child_value,
                    key=str(child_key),
                )
            )
            is not None
        }
    if isinstance(value, list):
        return [
            visible
            for item in value
            if (visible := agent_visible_evaluation(item)) is not None
        ]
    if isinstance(value, str):
        return agent_visible_text(value)
    return value


def agent_visible_text(value: str) -> str:
    return _ABSOLUTE_PATH.sub("<redacted-path>", value)


def agent_visible_log(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        return ""
    raw = path.read_bytes()
    if len(raw) > _LOG_MAX_BYTES:
        half = _LOG_MAX_BYTES // 2
        raw = raw[:half] + b"\n... <truncated> ...\n" + raw[-half:]
    text = raw.decode("utf-8", errors="replace")
    return agent_visible_text(text)


class ExperienceBank:
    def __init__(self, run_dir: Path, task: TaskSpec) -> None:
        self.run_dir = run_dir
        self.task = task
        self.attempts_dir = run_dir / "attempts"
        self.events_path = run_dir / "events.jsonl"
        self.context_calls_dir = run_dir / "context_calls"
        self.evaluator_replays_dir = run_dir / "evaluator_replays"
        self.current_evaluator_version = "fixed"
        self.evaluation_overrides: dict[str, dict[str, Any]] = {}
        self.attempts_dir.mkdir(parents=True, exist_ok=True)
        self.context_calls_dir.mkdir(parents=True, exist_ok=True)
        self.evaluator_replays_dir.mkdir(parents=True, exist_ok=True)

    def event(self, event_type: str, **payload: Any) -> None:
        append_jsonl(
            self.events_path,
            {"at": utc_now(), "type": event_type, **payload},
        )

    def attempt_dir(self, attempt_id: str) -> Path:
        return self.attempts_dir / attempt_id

    def workspace(self, attempt_id: str) -> Path:
        return self.attempt_dir(attempt_id) / "workspace"

    def metadata_path(self, attempt_id: str) -> Path:
        return self.attempt_dir(attempt_id) / "attempt.json"

    def read_attempt(self, attempt_id: str) -> dict[str, Any]:
        return json.loads(self.metadata_path(attempt_id).read_text(encoding="utf-8"))

    def write_attempt(self, attempt_id: str, value: dict[str, Any]) -> None:
        value = {**value, "attempt_id": attempt_id}
        atomic_write_json(self.metadata_path(attempt_id), value)

    def completed_attempts(self) -> list[dict[str, Any]]:
        attempts: list[dict[str, Any]] = []
        for path in sorted(self.attempts_dir.glob("attempt-*/attempt.json")):
            value = json.loads(path.read_text(encoding="utf-8"))
            if value.get("status") == "completed":
                value = {
                    **value,
                    "evaluation": self.evaluation_for(value),
                    "evaluation_version": self.current_evaluator_version,
                }
                attempts.append(value)
        return attempts

    def evaluation_for(self, attempt: dict[str, Any]) -> dict[str, Any]:
        attempt_id = str(attempt["attempt_id"])
        if attempt_id in self.evaluation_overrides:
            return self.evaluation_overrides[attempt_id]
        return dict(attempt.get("evaluation") or {})

    def set_evaluator_version(
        self,
        version_id: str,
        replay: dict[str, dict[str, Any]] | None,
        *,
        emit_event: bool = True,
    ) -> None:
        version_dir = self.evaluator_replays_dir / version_id
        version_dir.mkdir(parents=True, exist_ok=True)
        if replay is not None:
            for attempt_id, result in sorted(replay.items()):
                atomic_write_json(version_dir / f"{attempt_id}.json", result)
        restored: dict[str, dict[str, Any]] = {}
        for path in sorted(version_dir.glob("attempt-*.json")):
            restored[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        self.current_evaluator_version = version_id
        self.evaluation_overrides = restored
        if emit_event:
            self.event(
                "evaluator.activated",
                evaluator_version=version_id,
                replayed_attempts=len(restored),
            )

    def has_attempt(self, attempt_id: str) -> bool:
        return self.metadata_path(attempt_id).exists()

    def is_better(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        left_eval = left.get("evaluation") or {}
        right_eval = right.get("evaluation") or {}
        key = self.task.objective_key
        left_qualified = (
            left_eval.get(self.task.qualification_key) is True
            and "error" not in left_eval
        )
        right_qualified = (
            right_eval.get(self.task.qualification_key) is True
            and "error" not in right_eval
        )
        if not left_qualified:
            return False
        left_value = left_eval.get(key)
        if (
            not isinstance(left_value, (int, float))
            or isinstance(left_value, bool)
            or not math.isfinite(float(left_value))
        ):
            return False
        if not right_qualified:
            return True
        right_value = right_eval.get(key)
        if (
            not isinstance(right_value, (int, float))
            or isinstance(right_value, bool)
            or not math.isfinite(float(right_value))
        ):
            return True
        if self.task.direction == "minimize":
            return float(left_value) < float(right_value)
        return float(left_value) > float(right_value)

    def incumbent(self) -> dict[str, Any]:
        completed = self.completed_attempts()
        if not completed:
            raise RuntimeError("experience bank has no completed attempts")
        best = completed[0]
        for attempt in completed[1:]:
            if self.is_better(attempt, best):
                best = attempt
        return best

    def summary(self, *, limit: int = 40) -> dict[str, Any]:
        attempts = self.completed_attempts()
        compact: list[dict[str, Any]] = []
        for attempt in attempts[-limit:]:
            visible_evaluation = agent_visible_evaluation(
                attempt.get("evaluation") or {}
            )
            proposal_output = (attempt.get("proposal_call") or {}).get("output") or {}
            diagnostic = agent_visible_text(
                str(proposal_output.get("diagnostic_summary", ""))
            )
            raw_proposal_error = (attempt.get("proposal_call") or {}).get(
                "error"
            )
            effective_state = attempt.get("effective_state")
            attempt_dir = self.attempt_dir(str(attempt["attempt_id"]))
            logs = {
                label: excerpt
                for label, relpath in (
                    ("solution_stdout", Path("evaluation/solution.stdout.log")),
                    ("solution_stderr", Path("evaluation/solution.stderr.log")),
                    ("evaluator_stdout", Path("evaluation/stdout.log")),
                    ("evaluator_stderr", Path("evaluation/stderr.log")),
                )
                if (
                    excerpt := agent_visible_log(attempt_dir / relpath)
                )
            }
            compact.append(
                {
                    "attempt_id": attempt["attempt_id"],
                    "parent_attempt_id": attempt.get("parent_attempt_id"),
                    "title": (attempt.get("inspiration") or {}).get("title", "seed"),
                    "hypothesis": agent_visible_text(
                        str(
                            (attempt.get("inspiration") or {}).get(
                                "hypothesis",
                                "",
                            )
                        )
                    ),
                    "status": attempt.get("status"),
                    "evaluation": visible_evaluation,
                    "proposal_error": (
                        agent_visible_text(str(raw_proposal_error))
                        if raw_proposal_error is not None
                        else None
                    ),
                    "proposal_diagnostic": diagnostic[:2000],
                    "logs": logs,
                    "workspace_tree_sha256": (
                        attempt.get("workspace_manifest") or {}
                    ).get("tree_sha256"),
                    "runtime_state": (
                        {
                            "source_attempt_id": effective_state.get(
                                "source_attempt_id"
                            ),
                            "sha256": effective_state.get("sha256"),
                        }
                        if isinstance(effective_state, dict)
                        else None
                    ),
                }
            )
        incumbent = self.incumbent()
        incumbent_state = incumbent.get("effective_state")
        return {
            "task": self.task.name,
            "objective_key": self.task.objective_key,
            "direction": self.task.direction,
            "qualification_key": self.task.qualification_key,
            "attempt_count": len(attempts),
            "incumbent_attempt_id": incumbent["attempt_id"],
            "incumbent_evaluation": agent_visible_evaluation(
                incumbent.get("evaluation") or {}
            ),
            "runtime_state": {
                "enabled": self.task.state_capture_env is not None,
                "description": self.task.state_description,
                "incumbent_source_attempt_id": (
                    incumbent_state.get("source_attempt_id")
                    if isinstance(incumbent_state, dict)
                    else None
                ),
            },
            "attempts": compact,
        }

    def materialize_context_sources(
        self,
        destination: Path,
        *,
        limit: int = 40,
    ) -> dict[str, list[str]]:
        attempt_ids = [
            str(attempt["attempt_id"])
            for attempt in self.completed_attempts()[-limit:]
        ]
        return self.materialize_attempt_sources(
            attempt_ids,
            destination,
            display_root=destination.parent,
        )

    def materialize_context_states(
        self,
        destination: Path,
        *,
        limit: int = 40,
    ) -> dict[str, str]:
        completed = self.completed_attempts()
        ordered = [self.incumbent(), *reversed(completed[-limit:])]
        snapshots: dict[str, str] = {}
        total_bytes = 0
        for attempt in ordered:
            state = attempt.get("effective_state")
            if not isinstance(state, dict):
                continue
            source_attempt_id = state.get("source_attempt_id")
            raw_path = state.get("path")
            expected_hash = state.get("sha256")
            if (
                not isinstance(source_attempt_id, str)
                or not isinstance(raw_path, str)
                or not isinstance(expected_hash, str)
                or source_attempt_id in snapshots
            ):
                continue
            relative = Path(raw_path)
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError("runtime-state path escapes run directory")
            source = self.run_dir / relative
            if not source.is_file() or source.is_symlink():
                raise RuntimeError("runtime-state artifact is missing or unsafe")
            if sha256_file(source) != expected_hash:
                raise RuntimeError("runtime-state artifact checksum mismatch")
            size = source.stat().st_size
            if (
                len(snapshots) >= _STATE_MAX_FILES
                or total_bytes + size > _STATE_MAX_TOTAL_BYTES
            ):
                continue
            target = destination / source_attempt_id / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            snapshots[source_attempt_id] = target.relative_to(
                destination.parent
            ).as_posix()
            total_bytes += size
        return snapshots

    def materialize_attempt_sources(
        self,
        attempt_ids: list[str] | tuple[str, ...],
        destination: Path,
        *,
        display_root: Path | None = None,
    ) -> dict[str, list[str]]:
        snapshots: dict[str, list[str]] = {}
        shown_from = display_root or destination
        for attempt_id in attempt_ids:
            workspace = self.workspace(attempt_id)
            copied: list[str] = []
            total_bytes = 0
            file_count = 0
            for source in sorted(workspace.rglob("*")):
                if source.is_symlink() or not source.is_file():
                    continue
                relpath = source.relative_to(workspace)
                if any(part in _IGNORED_PARTS for part in relpath.parts):
                    continue
                size = source.stat().st_size
                if (
                    size > _SOURCE_MAX_FILE_BYTES
                    or total_bytes + size > _SOURCE_MAX_TOTAL_BYTES
                    or file_count >= _SOURCE_MAX_FILES
                ):
                    continue
                target = destination / attempt_id / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(target.relative_to(shown_from).as_posix())
                total_bytes += size
                file_count += 1
            snapshots[attempt_id] = copied
        return snapshots
