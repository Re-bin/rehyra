from __future__ import annotations

import json
import math
import shutil
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol

from .providers import load_recorded_structured_call
from .evaluator import evaluate
from .execution import ExecutionBackend, LocalProcessBackend
from .contracts import AgentCall, TaskSpec
from .util import atomic_write_json, sha256_file, utc_now, workspace_manifest


class EvaluatorAgents(Protocol):
    async def evaluator_architect(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
        initial: bool,
    ) -> AgentCall: ...

    async def evaluator_critic(
        self,
        *,
        goal: str,
        architect_output: dict[str, Any],
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
    ) -> AgentCall: ...


@dataclass(frozen=True)
class EvaluatorVersion:
    version_id: str
    task: TaskSpec
    evaluator_path: Path | None
    metadata_path: Path | None


class EvaluatorEvolution:
    """Versioned evaluator lifecycle for the public Hyra bilevel loop."""

    INITIAL_REVISION_LIMIT = 3

    def __init__(
        self,
        *,
        task: TaskSpec,
        agents: EvaluatorAgents,
        run_dir: Path,
        timeout_seconds: int,
        execution_backend: ExecutionBackend | None = None,
    ) -> None:
        self.base_task = task
        self.agents = agents
        self.run_dir = run_dir
        self.timeout_seconds = timeout_seconds
        self.execution_backend = execution_backend or LocalProcessBackend()
        self.root = run_dir / "evaluators"
        self.root.mkdir(parents=True, exist_ok=True)
        self.current = EvaluatorVersion(
            version_id="fixed",
            task=task,
            evaluator_path=None,
            metadata_path=None,
        )

    def _task_for(self, evaluator_path: Path) -> TaskSpec:
        return replace(
            self.base_task,
            evaluator_argv=(
                sys.executable,
                str(evaluator_path),
                "{submission}",
            ),
        )

    async def initialize(self, bank_summary: dict[str, Any]) -> EvaluatorVersion:
        if self.base_task.evaluator_mode == "fixed":
            return self.current
        previous: EvaluatorVersion | None = None
        revision_summary = dict(bank_summary)
        blockers: list[str] = []
        for index in range(self.INITIAL_REVISION_LIMIT):
            candidate = await self._propose_version(
                index=index,
                bank_summary=revision_summary,
                initial=True,
                previous=previous,
                attempt_workspaces=None,
            )
            assert candidate.metadata_path is not None
            metadata = json.loads(
                candidate.metadata_path.read_text(encoding="utf-8")
            )
            if metadata.get("status") != "rejected_critic":
                self.current = candidate
                return self.current
            critic_output = (metadata.get("critic") or {}).get("output") or {}
            blockers = [
                str(item)
                for item in critic_output.get("blockers", [])
            ]
            revision_summary = {
                **bank_summary,
                "previous_evaluator_revision": {
                    "version_id": candidate.version_id,
                    "critic_audit": critic_output,
                    "instruction": (
                        "Revise the previous evaluator to resolve every blocker. "
                        "Preserve valid parts and keep the frozen objective contract."
                    ),
                },
            }
            previous = candidate
        raise RuntimeError(
            "evaluator critic blocked all initial revisions: "
            + "; ".join(blockers)
        )

    def load(self, version_id: str) -> EvaluatorVersion:
        if version_id == "fixed":
            if self.base_task.evaluator_mode != "fixed":
                raise RuntimeError("generated evaluator run cannot restore fixed version")
            return self.current
        if self.base_task.evaluator_mode != "generated":
            raise RuntimeError("fixed evaluator run cannot restore generated version")
        if not version_id.startswith("evaluator-"):
            raise RuntimeError(f"invalid evaluator version id: {version_id}")
        version_dir = self.root / version_id
        metadata_path = version_dir / "version.json"
        if not metadata_path.is_file():
            raise RuntimeError(f"missing evaluator metadata: {version_id}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("version_id") != version_id:
            raise RuntimeError(f"evaluator metadata id mismatch: {version_id}")
        if metadata.get("status") not in {"created", "promoted", "unchanged"}:
            raise RuntimeError(f"evaluator version is not restorable: {version_id}")
        evaluator_path = version_dir / "workspace" / self.base_task.evaluator_relpath
        if not evaluator_path.is_file():
            raise RuntimeError(f"missing evaluator program: {version_id}")
        if sha256_file(evaluator_path) != metadata.get("evaluator_sha256"):
            raise RuntimeError(f"evaluator checksum mismatch: {version_id}")
        self.current = EvaluatorVersion(
            version_id=version_id,
            task=self._task_for(evaluator_path),
            evaluator_path=evaluator_path,
            metadata_path=metadata_path,
        )
        return self.current

    async def improve(
        self,
        *,
        bank_summary: dict[str, Any],
        attempt_workspaces: dict[str, Path],
        attempt_input_states: dict[str, Path | None] | None = None,
    ) -> tuple[EvaluatorVersion, dict[str, dict[str, Any]]]:
        if self.base_task.evaluator_mode == "fixed":
            return self.current, {}
        next_index = int(self.current.version_id.split("-")[-1]) + 1
        candidate = await self._propose_version(
            index=next_index,
            bank_summary=bank_summary,
            initial=False,
            previous=self.current,
            attempt_workspaces=attempt_workspaces,
        )
        metadata = json.loads(candidate.metadata_path.read_text(encoding="utf-8"))
        if metadata["status"] == "rejected_critic":
            return self.current, {}
        if not metadata["architect"]["output"].get("changed", False):
            metadata["status"] = "unchanged"
            atomic_write_json(candidate.metadata_path, metadata)
            return self.current, {}

        replay: dict[str, dict[str, Any]] = {}
        for attempt_id, workspace in sorted(attempt_workspaces.items()):
            replay[attempt_id] = await evaluate(
                candidate.task,
                workspace=workspace,
                run_dir=self.run_dir,
                output_dir=(
                    candidate.metadata_path.parent / "replay" / attempt_id
                ),
                timeout_seconds=self.timeout_seconds,
                evaluator_path=candidate.evaluator_path,
                execution_backend=self.execution_backend,
                input_state_path=(attempt_input_states or {}).get(attempt_id),
            )
        seed_result = replay.get("attempt-0000") or {}
        valid = self._valid_result(seed_result, require_qualified=True)
        valid = valid and all(
            self._valid_result(result, require_qualified=False)
            for result in replay.values()
        )
        metadata["replay"] = replay
        metadata["status"] = "promoted" if valid else "rejected_replay"
        metadata["promoted_at"] = utc_now() if valid else None
        atomic_write_json(candidate.metadata_path, metadata)
        if valid:
            self.current = candidate
            return candidate, replay
        return self.current, {}

    def _valid_result(
        self,
        result: dict[str, Any],
        *,
        require_qualified: bool,
    ) -> bool:
        qualification = result.get(self.base_task.qualification_key)
        if not isinstance(qualification, bool):
            return False
        if "error" in result:
            return False
        if require_qualified and not qualification:
            return False
        if not qualification:
            return True
        objective = result.get(self.base_task.objective_key)
        return (
            isinstance(objective, (int, float))
            and not isinstance(objective, bool)
            and math.isfinite(float(objective))
        )

    async def _propose_version(
        self,
        *,
        index: int,
        bank_summary: dict[str, Any],
        initial: bool,
        previous: EvaluatorVersion | None,
        attempt_workspaces: dict[str, Path] | None,
    ) -> EvaluatorVersion:
        version_id = f"evaluator-{index:04d}"
        version_dir = self.root / version_id
        workspace = version_dir / "workspace"
        if version_dir.exists():
            metadata_path = version_dir / "version.json"
            if not metadata_path.is_file():
                evaluator_path = workspace / self.base_task.evaluator_relpath
                architect = load_recorded_structured_call(
                    version_dir / "architect"
                )
                critic = load_recorded_structured_call(version_dir / "critic")
                if (
                    not evaluator_path.is_file()
                    or architect is None
                    or not architect.ok
                    or architect.output is None
                    or critic is None
                    or not critic.ok
                    or critic.output is None
                ):
                    raise RuntimeError(
                        f"incomplete evaluator version cannot be recovered: {version_id}"
                    )
                output = architect.output
                if output.get("objective_key") != self.base_task.objective_key:
                    raise RuntimeError(
                        "recovered evaluator changed the frozen objective key"
                    )
                if output.get("direction") != self.base_task.direction:
                    raise RuntimeError(
                        "recovered evaluator changed the frozen direction"
                    )
                if (
                    output.get("qualification_key")
                    != self.base_task.qualification_key
                ):
                    raise RuntimeError(
                        "recovered evaluator changed the qualification key"
                    )
                approved = bool(critic.output.get("approve", False))
                metadata = {
                    "version_id": version_id,
                    "status": "created" if approved else "rejected_critic",
                    "created_at": utc_now(),
                    "recovered_from_completed_calls": True,
                    "parent_version_id": (
                        previous.version_id if previous is not None else None
                    ),
                    "architect": architect.as_dict(),
                    "critic": critic.as_dict(),
                    "evaluator_sha256": sha256_file(evaluator_path),
                    "workspace_manifest": workspace_manifest(workspace),
                }
                atomic_write_json(metadata_path, metadata)
            else:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("status") not in {
                "created",
                "promoted",
                "unchanged",
                "rejected_critic",
            }:
                raise RuntimeError(f"incomplete evaluator version: {version_id}")
            evaluator_path = workspace / self.base_task.evaluator_relpath
            return EvaluatorVersion(
                version_id=version_id,
                task=self._task_for(evaluator_path),
                evaluator_path=evaluator_path,
                metadata_path=metadata_path,
            )
        workspace.mkdir(parents=True)
        if previous is not None and previous.evaluator_path is not None:
            source_root = previous.evaluator_path.parent
            for item in source_root.iterdir():
                destination = workspace / item.name
                if item.is_dir():
                    shutil.copytree(item, destination)
                else:
                    shutil.copy2(item, destination)
        experience_root = workspace / "experience_sources"
        if experience_root.exists():
            shutil.rmtree(experience_root)
        source_snapshots: dict[str, list[str]] = {}
        for attempt_id, attempt_workspace in sorted(
            (attempt_workspaces or {}).items()
        ):
            copied: list[str] = []
            for relpath in (
                self.base_task.submission_relpath,
                Path("solve.sh"),
            ):
                source = attempt_workspace / relpath
                if not source.is_file():
                    continue
                target = experience_root / attempt_id / relpath
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                copied.append(target.relative_to(workspace).as_posix())
            source_snapshots[attempt_id] = copied
        bank_summary = {
            **bank_summary,
            "source_snapshots": source_snapshots,
        }
        atomic_write_json(version_dir / "bank_summary.json", bank_summary)
        architect_dir = version_dir / "architect"
        architect = await self.agents.evaluator_architect(
            goal=self.base_task.goal,
            bank_summary=bank_summary,
            workspace=workspace,
            output_dir=architect_dir,
            evaluator_relpath=self.base_task.evaluator_relpath,
            initial=initial,
        )
        if not architect.ok or architect.output is None:
            raise RuntimeError(f"evaluator architect failed: {architect.error}")
        output = architect.output
        if output.get("objective_key") != self.base_task.objective_key:
            raise RuntimeError("evaluator architect changed the frozen objective key")
        if output.get("direction") != self.base_task.direction:
            raise RuntimeError("evaluator architect changed the frozen direction")
        if output.get("qualification_key") != self.base_task.qualification_key:
            raise RuntimeError("evaluator architect changed the qualification key")
        evaluator_path = workspace / self.base_task.evaluator_relpath
        if not evaluator_path.is_file():
            raise RuntimeError(f"architect did not create {evaluator_path}")

        critic_dir = version_dir / "critic"
        critic = await self.agents.evaluator_critic(
            goal=self.base_task.goal,
            architect_output=output,
            bank_summary=bank_summary,
            workspace=workspace,
            output_dir=critic_dir,
            evaluator_relpath=self.base_task.evaluator_relpath,
        )
        if not critic.ok or critic.output is None:
            raise RuntimeError(f"evaluator critic failed: {critic.error}")
        metadata_path = version_dir / "version.json"
        approved = bool(critic.output.get("approve", False))
        metadata = {
            "version_id": version_id,
            "status": "created" if approved else "rejected_critic",
            "created_at": utc_now(),
            "parent_version_id": previous.version_id if previous else None,
            "architect": architect.as_dict(),
            "critic": critic.as_dict(),
            "evaluator_sha256": sha256_file(evaluator_path),
            "workspace_manifest": workspace_manifest(workspace),
        }
        atomic_write_json(metadata_path, metadata)
        return EvaluatorVersion(
            version_id=version_id,
            task=self._task_for(evaluator_path),
            evaluator_path=evaluator_path,
            metadata_path=metadata_path,
        )
