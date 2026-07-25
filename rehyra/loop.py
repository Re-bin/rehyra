from __future__ import annotations

import asyncio
from importlib import metadata as importlib_metadata
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .providers import audit_agent_evidence
from .agents import AgentBackend
from .evaluator import evaluate
from .evaluator_evolution import EvaluatorEvolution, EvaluatorVersion
from .execution import ExecutionBackend, LocalProcessBackend
from .contracts import Inspiration, RunConfig, TaskSpec
from .memory import (
    ExperienceBank,
    agent_visible_evaluation,
    agent_visible_text,
)
from .util import (
    atomic_write_json,
    copy_workspace,
    evidence_manifest,
    write_evidence_manifest,
    sha256_file,
    utc_now,
    workspace_manifest,
)
from .workspace import project_editable_region


_PROPOSAL_CONTEXT_DIR = Path(".hyra_context")


class ReHyraLoop:
    def __init__(
        self,
        *,
        config: RunConfig,
        task: TaskSpec,
        backend: AgentBackend,
        run_dir: Path,
        execution_backend: ExecutionBackend | None = None,
    ) -> None:
        self.config = config
        self.task = task
        self.backend = backend
        self.execution_backend = execution_backend or LocalProcessBackend()
        self.run_dir = run_dir
        self.bank = ExperienceBank(run_dir, task)
        self.completion_event = asyncio.Event()
        self.completed_proposals = 0
        self.scheduled_proposals = 0
        self.context_call_count = 0
        self.voluntary_stop = False
        self.stop_reason = ""
        self.next_cycle_index = 0
        self.evaluator_evolution = EvaluatorEvolution(
            task=task,
            agents=backend,  # type: ignore[arg-type]
            run_dir=run_dir,
            timeout_seconds=config.agent.proposal_timeout_seconds,
            execution_backend=self.execution_backend,
        )
        self.evaluator_version = EvaluatorVersion(
            version_id="fixed",
            task=task,
            evaluator_path=None,
            metadata_path=None,
        )

    def _state_path(
        self,
        descriptor: dict[str, Any] | None,
    ) -> Path | None:
        if not descriptor:
            return None
        raw_path = descriptor.get("path")
        expected_hash = descriptor.get("sha256")
        if not isinstance(raw_path, str) or not isinstance(expected_hash, str):
            raise RuntimeError("invalid runtime-state descriptor")
        relative = Path(raw_path)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("runtime-state path escapes run directory")
        path = self.run_dir / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError("runtime-state artifact is missing or unsafe")
        if sha256_file(path) != expected_hash:
            raise RuntimeError("runtime-state artifact checksum mismatch")
        return path

    def _project_submission(
        self,
        *,
        parent_attempt_id: str,
        workspace: Path,
        agent_dir: Path,
    ) -> tuple[dict[str, Any] | None, str | None]:
        start_marker = self.task.editable_start_marker
        end_marker = self.task.editable_end_marker
        if start_marker is None:
            return None, None
        assert end_marker is not None
        try:
            projection = project_editable_region(
                parent_submission=(
                    self.bank.workspace(parent_attempt_id)
                    / self.task.submission_relpath
                ),
                candidate_submission=workspace / self.task.submission_relpath,
                raw_evidence_path=agent_dir / "raw_submission",
                submission_relpath=self.task.submission_relpath,
                start_marker=start_marker,
                end_marker=end_marker,
            )
        except (OSError, UnicodeError, ValueError) as exc:
            return None, str(exc)
        return projection, None

    def _project_workspace_policy(
        self,
        *,
        parent_attempt_id: str,
        workspace: Path,
        agent_dir: Path,
    ) -> tuple[dict[str, Any] | None, str | None]:
        if self.task.mutation_policy == "workspace":
            return None, None
        if self.task.mutation_policy != "submission_only":
            return None, f"unsupported mutation policy: {self.task.mutation_policy}"

        raw_submission = agent_dir / "raw_submission"
        candidate_submission = workspace / self.task.submission_relpath
        try:
            agent_dir.mkdir(parents=True, exist_ok=True)
            if raw_submission.exists() or raw_submission.is_symlink():
                if not raw_submission.is_file() or raw_submission.is_symlink():
                    raise ValueError("raw submission evidence is unsafe")
            else:
                if (
                    not candidate_submission.is_file()
                    or candidate_submission.is_symlink()
                ):
                    raise ValueError("candidate submission is missing or unsafe")
                raw_tmp = raw_submission.with_suffix(".tmp")
                shutil.copy2(candidate_submission, raw_tmp)
                raw_tmp.replace(raw_submission)

            if workspace.is_symlink() or (
                workspace.exists() and not workspace.is_dir()
            ):
                raise ValueError("candidate workspace is unsafe")
            if workspace.exists():
                shutil.rmtree(workspace)
            parent_workspace = self.bank.workspace(parent_attempt_id)
            copy_workspace(parent_workspace, workspace)
            projected_submission = workspace / self.task.submission_relpath
            projected_submission.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(raw_submission, projected_submission)
            return {
                "policy": "submission_only",
                "submission_relpath": self.task.submission_relpath.as_posix(),
                "raw_submission": raw_submission.name,
                "raw_sha256": sha256_file(raw_submission),
                "projected_sha256": sha256_file(projected_submission),
                "parent_tree_sha256": workspace_manifest(parent_workspace)[
                    "tree_sha256"
                ],
            }, None
        except (OSError, ValueError) as exc:
            return None, str(exc)

    def _freeze_candidate(
        self,
        *,
        parent_attempt_id: str,
        workspace: Path,
        agent_dir: Path,
    ) -> tuple[dict[str, Any] | None, str | None]:
        workspace_projection, error = self._project_workspace_policy(
            parent_attempt_id=parent_attempt_id,
            workspace=workspace,
            agent_dir=agent_dir,
        )
        if error is not None:
            return None, error
        region_projection, error = self._project_submission(
            parent_attempt_id=parent_attempt_id,
            workspace=workspace,
            agent_dir=agent_dir,
        )
        if error is not None:
            return None, error
        if region_projection is not None and workspace_projection is not None:
            region_projection["workspace_policy"] = workspace_projection["policy"]
            region_projection["parent_tree_sha256"] = workspace_projection[
                "parent_tree_sha256"
            ]
        return region_projection or workspace_projection, None

    def _effective_state(
        self,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        attempt = self.bank.read_attempt(attempt_id)
        state = attempt.get("effective_state")
        if state is None:
            return None
        if not isinstance(state, dict):
            raise RuntimeError(f"{attempt_id} has invalid effective runtime state")
        self._state_path(state)
        return dict(state)

    def _select_effective_state(
        self,
        *,
        attempt_id: str,
        parent_attempt_id: str | None,
        evaluation: dict[str, Any],
    ) -> dict[str, Any] | None:
        captured = evaluation.get("captured_state")
        captured_state = dict(captured) if isinstance(captured, dict) else None
        parent_state = (
            self._effective_state(parent_attempt_id)
            if parent_attempt_id is not None
            else None
        )
        if (
            captured_state is None
            or evaluation.get(self.task.qualification_key) is not True
        ):
            return parent_state
        captured_state["source_attempt_id"] = attempt_id
        if parent_state is None:
            return captured_state
        source_id = str(parent_state.get("source_attempt_id", ""))
        if not self.bank.has_attempt(source_id):
            raise RuntimeError("runtime-state source attempt is missing")
        source = self.bank.read_attempt(source_id)
        source["evaluation"] = self.bank.evaluation_for(source)
        candidate = {
            "attempt_id": attempt_id,
            "evaluation": evaluation,
        }
        if self.bank.is_better(candidate, source):
            return captured_state
        return parent_state

    async def initialize(self) -> None:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        state_path = self.run_dir / "run.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.evaluator_version = self.evaluator_evolution.load(
                str(state.get("evaluator_version", "fixed"))
            )
            self.next_cycle_index = int(state.get("next_cycle_index", 0))
            self.voluntary_stop = bool(state.get("voluntary_stop", False))
            self.stop_reason = str(state.get("stop_reason", ""))
        else:
            self.evaluator_version = await self.evaluator_evolution.initialize(
                {
                    "task": self.task.name,
                    "objective_key": self.task.objective_key,
                    "direction": self.task.direction,
                    "qualification_key": self.task.qualification_key,
                    "attempt_count": 0,
                    "attempts": [],
                }
            )
        self.task = self.evaluator_version.task
        self.bank.task = self.task
        self.bank.set_evaluator_version(
            self.evaluator_version.version_id,
            None if state_path.exists() else {},
            emit_event=not state_path.exists(),
        )
        if not state_path.exists():
            atomic_write_json(
                state_path,
                {
                    "name": self.config.name,
                    "status": "initializing",
                    "created_at": utc_now(),
                    "task": {
                        "name": self.task.name,
                        "goal": self.task.goal,
                        "provenance": self.task.provenance,
                    },
                    "budget": {
                        "max_proposals": self.config.max_proposals,
                        "max_concurrency": self.config.max_concurrency,
                        "queue_target": self.config.queue_target,
                        "context_batch_size": self.config.context_batch_size,
                        "evaluator_cycles": self.config.evaluator_cycles,
                    },
                    "agent": {
                        "backend": self.config.agent.backend,
                        "model": self.config.agent.model,
                        "reasoning_effort": self.config.agent.reasoning_effort,
                    },
                    "isolation": {
                        **self.execution_backend.attestation.as_dict(),
                        "agent_note": (
                            "Agent-provider isolation is separately recorded by "
                            "the provider transcript."
                        ),
                    },
                    "evaluator_mode": self.task.evaluator_mode,
                    "evaluator_version": self.evaluator_version.version_id,
                    "next_cycle_index": 0,
                    "voluntary_stop": False,
                    "stop_reason": "",
                },
            )
        seed_id = "attempt-0000"
        if not self.bank.has_attempt(seed_id):
            attempt_dir = self.bank.attempt_dir(seed_id)
            workspace = attempt_dir / "workspace"
            attempt_dir.mkdir(parents=True, exist_ok=False)
            copy_workspace(self.task.seed_workspace, workspace)
            self.bank.event("seed.copied", attempt_id=seed_id)
            evaluation = await evaluate(
                self.task,
                workspace=workspace,
                run_dir=self.run_dir,
                output_dir=attempt_dir / "evaluation",
                timeout_seconds=self.config.agent.proposal_timeout_seconds,
                evaluator_path=self.evaluator_version.evaluator_path,
                execution_backend=self.execution_backend,
            )
            effective_state = self._select_effective_state(
                attempt_id=seed_id,
                parent_attempt_id=None,
                evaluation=evaluation,
            )
            metadata = {
                "status": "completed",
                "kind": "seed",
                "parent_attempt_id": None,
                "created_at": utc_now(),
                "completed_at": utc_now(),
                "evaluation": evaluation,
                "input_state": None,
                "effective_state": effective_state,
                "evaluator_version": self.evaluator_version.version_id,
                "workspace_manifest": workspace_manifest(workspace),
            }
            self.bank.write_attempt(seed_id, metadata)
            self.bank.event(
                "seed.evaluated",
                attempt_id=seed_id,
                evaluation=evaluation,
            )
        seed = self.bank.read_attempt(seed_id)
        if (
            (seed.get("evaluation") or {}).get(self.task.qualification_key)
            is not True
        ):
            raise RuntimeError(f"seed failed qualification: {seed.get('evaluation')}")

        for path in sorted(self.bank.attempts_dir.glob("attempt-*/attempt.json")):
            interrupted = json.loads(path.read_text(encoding="utf-8"))
            if interrupted.get("status") != "running":
                continue
            self._remove_proposal_context(path.parent / "workspace")
            call_path = path.parent / "agent" / "call.json"
            call_record = (
                json.loads(call_path.read_text(encoding="utf-8"))
                if call_path.is_file()
                else None
            )
            interrupted["status"] = "completed"
            interrupted["completed_at"] = utc_now()
            if call_record is not None and call_record.get("ok", False):
                output = dict(call_record.get("output") or {})
                if "diagnostic_summary" not in output and "message" in output:
                    output = {
                        "diagnostic_summary": str(output.get("message", "")).strip()
                    }
                call_record = {**call_record, "output": output}
                interrupted["proposal_call"] = call_record
                parent_attempt_id = interrupted.get("parent_attempt_id")
                input_state = (
                    self._effective_state(str(parent_attempt_id))
                    if parent_attempt_id is not None
                    else None
                )
                submission_projection, projection_error = (
                    self._freeze_candidate(
                        parent_attempt_id=str(parent_attempt_id),
                        workspace=path.parent / "workspace",
                        agent_dir=path.parent / "agent",
                    )
                )
                interrupted["submission_projection"] = submission_projection
                if projection_error is None:
                    interrupted["evaluation"] = await evaluate(
                        self.task,
                        workspace=path.parent / "workspace",
                        run_dir=self.run_dir,
                        output_dir=path.parent / "evaluation",
                        timeout_seconds=self.config.agent.proposal_timeout_seconds,
                        evaluator_path=self.evaluator_version.evaluator_path,
                        execution_backend=self.execution_backend,
                        input_state_path=self._state_path(input_state),
                    )
                else:
                    interrupted["evaluation"] = {
                        self.task.qualification_key: False,
                        "error": (
                            "editable-region projection failed: "
                            f"{projection_error}"
                        ),
                    }
                interrupted["input_state"] = input_state
                interrupted["effective_state"] = self._select_effective_state(
                    attempt_id=str(interrupted["attempt_id"]),
                    parent_attempt_id=(
                        str(parent_attempt_id)
                        if parent_attempt_id is not None
                        else None
                    ),
                    evaluation=interrupted["evaluation"],
                )
                event_type = "proposal.recovered"
            else:
                interrupted["proposal_call"] = call_record
                interrupted["evaluation"] = {
                    self.task.qualification_key: False,
                    "error": "proposal interrupted before durable agent completion",
                }
                parent_attempt_id = interrupted.get("parent_attempt_id")
                input_state = (
                    self._effective_state(str(parent_attempt_id))
                    if parent_attempt_id is not None
                    else None
                )
                interrupted["input_state"] = input_state
                interrupted["effective_state"] = input_state
                interrupted["submission_projection"] = None
                event_type = "proposal.interrupted"
            interrupted["evaluator_version"] = self.evaluator_version.version_id
            interrupted["workspace_manifest"] = workspace_manifest(
                path.parent / "workspace"
            )
            self.bank.write_attempt(str(interrupted["attempt_id"]), interrupted)
            self.bank.event(
                event_type,
                attempt_id=interrupted["attempt_id"],
            )
        completed = [
            item
            for item in self.bank.completed_attempts()
            if item["attempt_id"] != seed_id
        ]
        self.completed_proposals = len(completed)
        self.scheduled_proposals = len(completed)
        self.context_call_count = len(
            list(self.bank.context_calls_dir.glob("context-*/call.json"))
        )
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "running"
        state["started_at"] = state.get("started_at", utc_now())
        atomic_write_json(state_path, state)

    def _resolve_inspirations(
        self,
        output: dict[str, Any],
        *,
        requested: int,
    ) -> list[Inspiration]:
        raw_items = output.get("inspirations")
        if not isinstance(raw_items, list):
            raise ValueError("context output has no inspirations array")
        resolved: list[Inspiration] = []
        incumbent_id = self.bank.incumbent()["attempt_id"]
        completed = self.bank.completed_attempts()
        completed_ids = {item["attempt_id"] for item in completed}
        qualified_parent_ids = {
            item["attempt_id"]
            for item in completed
            if (item.get("evaluation") or {}).get(
                self.task.qualification_key
            )
            is True
        }
        for raw in raw_items[:requested]:
            if not isinstance(raw, dict):
                continue
            parent = str(raw.get("parent_attempt_id", ""))
            if parent not in qualified_parent_ids:
                parent = incumbent_id
            refs = tuple(
                item
                for item in (str(value) for value in raw.get("reference_attempt_ids", []))
                if item in completed_ids
            )
            attempt_number = self.scheduled_proposals + len(resolved) + 1
            resolved.append(
                Inspiration(
                    attempt_id=f"attempt-{attempt_number:04d}",
                    title=str(raw.get("title", "continue incumbent"))[:200],
                    hypothesis=str(raw.get("hypothesis", ""))[:4000],
                    instructions=str(raw.get("instructions", ""))[:8000],
                    risk=str(raw.get("risk", ""))[:4000],
                    parent_attempt_id=parent,
                    reference_attempt_ids=refs,
                )
            )
        if not resolved:
            raise ValueError("context output contained no usable inspirations")
        return resolved

    async def _produce(
        self,
        queue: asyncio.Queue[Inspiration | None],
        *,
        target_proposals: int,
    ) -> None:
        while self.scheduled_proposals < target_proposals:
            outstanding = self.scheduled_proposals - self.completed_proposals
            if outstanding >= self.config.queue_target:
                await self.completion_event.wait()
                self.completion_event.clear()
                continue
            capacity = self.config.queue_target - outstanding
            remaining = target_proposals - self.scheduled_proposals
            requested = min(
                capacity,
                remaining,
                self.config.context_batch_size,
            )
            self.context_call_count += 1
            call_id = f"context-{self.context_call_count:04d}"
            work_dir = self.bank.context_calls_dir / call_id
            summary = self.bank.summary()
            summary["source_snapshots"] = self.bank.materialize_context_sources(
                work_dir / "source_snapshots"
            )
            summary["runtime_state_snapshots"] = (
                self.bank.materialize_context_states(
                    work_dir / "runtime_state_snapshots"
                )
            )
            atomic_write_json(work_dir / "bank_summary.json", summary)
            self.bank.event(
                "context.started",
                context_call_id=call_id,
                requested=requested,
                attempt_count=summary["attempt_count"],
            )
            call = await self.backend.context(
                goal=self.task.goal,
                bank_summary=summary,
                requested=requested,
                remaining_proposals=remaining,
                work_dir=work_dir,
            )
            atomic_write_json(work_dir / "call.json", call.as_dict())
            self.bank.event(
                "context.completed",
                context_call_id=call_id,
                ok=call.ok,
                usage=call.usage,
                error=call.error,
            )
            if not call.ok or call.output is None:
                raise RuntimeError(f"context agent failed: {call.error}")
            if bool(call.output.get("stop", False)):
                self.voluntary_stop = True
                self.stop_reason = str(call.output.get("stop_reason", "")).strip()
                state_path = self.run_dir / "run.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["voluntary_stop"] = True
                state["stop_reason"] = self.stop_reason
                atomic_write_json(state_path, state)
                self.bank.event(
                    "context.stopped",
                    context_call_id=call_id,
                    reason=self.stop_reason,
                )
                break
            inspirations = self._resolve_inspirations(
                call.output,
                requested=requested,
            )
            for inspiration in inspirations:
                await queue.put(inspiration)
                self.bank.event(
                    "proposal.queued",
                    attempt_id=inspiration.attempt_id,
                    parent_attempt_id=inspiration.parent_attempt_id,
                )
            self.scheduled_proposals += len(inspirations)

        for _ in range(self.config.max_concurrency):
            await queue.put(None)

    def _reference_context(self, inspiration: Inspiration) -> dict[str, Any]:
        return self._reference_context_with_sources(inspiration, {})

    def _reference_context_with_sources(
        self,
        inspiration: Inspiration,
        source_snapshots: dict[str, list[str]],
        runtime_state_snapshot: str | None = None,
    ) -> dict[str, Any]:
        ids = {
            inspiration.parent_attempt_id,
            *inspiration.reference_attempt_ids,
        }
        attempts: list[dict[str, Any]] = []
        for attempt_id in sorted(ids):
            attempt = self.bank.read_attempt(attempt_id)
            proposal_output = (attempt.get("proposal_call") or {}).get("output") or {}
            raw_proposal_error = (attempt.get("proposal_call") or {}).get(
                "error"
            )
            attempts.append(
                {
                    "attempt_id": attempt_id,
                    "evaluation": agent_visible_evaluation(
                        attempt.get("evaluation") or {}
                    ),
                    "inspiration": attempt.get("inspiration"),
                    "proposal_error": (
                        agent_visible_text(str(raw_proposal_error))
                        if raw_proposal_error is not None
                        else None
                    ),
                    "proposal_diagnostic": agent_visible_text(
                        str(proposal_output.get("diagnostic_summary", ""))
                    )[:4000],
                }
            )
        parent_state = self._effective_state(inspiration.parent_attempt_id)
        return {
            "attempts": attempts,
            "source_snapshots": source_snapshots,
            "runtime_state": {
                "available": parent_state is not None,
                "description": self.task.state_description,
                "source_attempt_id": (
                    parent_state.get("source_attempt_id")
                    if parent_state is not None
                    else None
                ),
                "sha256": (
                    parent_state.get("sha256")
                    if parent_state is not None
                    else None
                ),
                "snapshot": runtime_state_snapshot,
            },
        }

    def _materialize_proposal_context(
        self,
        inspiration: Inspiration,
        *,
        workspace: Path,
        attempt_dir: Path,
    ) -> tuple[dict[str, list[str]], str | None]:
        bundle_root = workspace / _PROPOSAL_CONTEXT_DIR
        if bundle_root.exists() or bundle_root.is_symlink():
            raise RuntimeError(
                f"{_PROPOSAL_CONTEXT_DIR.as_posix()} is reserved by the harness"
            )
        reference_ids = sorted(
            set(inspiration.reference_attempt_ids)
            - {inspiration.parent_attempt_id}
        )
        source_snapshots = self.bank.materialize_attempt_sources(
            reference_ids,
            workspace / _PROPOSAL_CONTEXT_DIR / "references",
            display_root=workspace,
        )
        runtime_state_snapshot: str | None = None
        parent_state = self._effective_state(inspiration.parent_attempt_id)
        state_path = self._state_path(parent_state)
        if state_path is not None:
            target = (
                workspace
                / _PROPOSAL_CONTEXT_DIR
                / "runtime_state"
                / state_path.name
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(state_path, target)
            if sha256_file(target) != parent_state["sha256"]:
                raise RuntimeError("runtime-state evidence copy checksum mismatch")
            runtime_state_snapshot = target.relative_to(workspace).as_posix()
        bundle_manifest = workspace_manifest(bundle_root)
        atomic_write_json(
            attempt_dir / "reference_bundle.json",
            {
                "parent_attempt_id": inspiration.parent_attempt_id,
                "reference_attempt_ids": reference_ids,
                "source_snapshots": source_snapshots,
                "runtime_state_snapshot": runtime_state_snapshot,
                "bundle_manifest": bundle_manifest,
                "lifecycle": "removed before candidate projection and evaluation",
            },
        )
        return source_snapshots, runtime_state_snapshot

    @staticmethod
    def _remove_proposal_context(workspace: Path) -> None:
        context_dir = workspace / _PROPOSAL_CONTEXT_DIR
        if context_dir.is_symlink() or context_dir.is_file():
            context_dir.unlink()
        elif context_dir.is_dir():
            shutil.rmtree(context_dir)

    async def _execute(
        self,
        inspiration: Inspiration,
        *,
        worker_id: int,
    ) -> None:
        attempt_dir = self.bank.attempt_dir(inspiration.attempt_id)
        workspace = attempt_dir / "workspace"
        attempt_dir.mkdir(parents=True, exist_ok=False)
        copy_workspace(self.bank.workspace(inspiration.parent_attempt_id), workspace)
        source_snapshots, runtime_state_snapshot = (
            self._materialize_proposal_context(
                inspiration,
                workspace=workspace,
                attempt_dir=attempt_dir,
            )
        )
        reference_context = self._reference_context_with_sources(
            inspiration,
            source_snapshots,
            runtime_state_snapshot,
        )
        atomic_write_json(attempt_dir / "inspiration.json", inspiration.as_dict())
        atomic_write_json(attempt_dir / "bank_context.json", reference_context)
        self.bank.write_attempt(
            inspiration.attempt_id,
            {
                "status": "running",
                "kind": "proposal",
                "parent_attempt_id": inspiration.parent_attempt_id,
                "created_at": utc_now(),
                "inspiration": inspiration.as_dict(),
            },
        )
        self.bank.event(
            "proposal.started",
            attempt_id=inspiration.attempt_id,
            parent_attempt_id=inspiration.parent_attempt_id,
            title=inspiration.title,
            worker_id=worker_id,
        )
        agent_dir = attempt_dir / "agent"
        try:
            call = await self.backend.proposal(
                goal=self.task.goal,
                inspiration=inspiration,
                bank_context=reference_context,
                submission_relpath=self.task.submission_relpath,
                workspace=workspace,
                output_dir=agent_dir,
            )
        finally:
            self._remove_proposal_context(workspace)
        input_state = self._effective_state(inspiration.parent_attempt_id)
        submission_projection = None
        if call.ok:
            submission_projection, projection_error = self._freeze_candidate(
                parent_attempt_id=inspiration.parent_attempt_id,
                workspace=workspace,
                agent_dir=agent_dir,
            )
            if projection_error is None:
                evaluation = await evaluate(
                    self.task,
                    workspace=workspace,
                    run_dir=self.run_dir,
                    output_dir=attempt_dir / "evaluation",
                    timeout_seconds=self.config.agent.proposal_timeout_seconds,
                    evaluator_path=self.evaluator_version.evaluator_path,
                    execution_backend=self.execution_backend,
                    input_state_path=self._state_path(input_state),
                )
            else:
                evaluation = {
                    self.task.qualification_key: False,
                    "error": (
                        "editable-region projection failed: "
                        f"{projection_error}"
                    ),
                }
        else:
            evaluation = {
                self.task.qualification_key: False,
                "error": f"proposal agent failed: {call.error}",
            }
        effective_state = self._select_effective_state(
            attempt_id=inspiration.attempt_id,
            parent_attempt_id=inspiration.parent_attempt_id,
            evaluation=evaluation,
        )
        metadata = {
            "status": "completed",
            "kind": "proposal",
            "parent_attempt_id": inspiration.parent_attempt_id,
            "created_at": self.bank.read_attempt(inspiration.attempt_id)["created_at"],
            "completed_at": utc_now(),
            "inspiration": inspiration.as_dict(),
            "proposal_call": call.as_dict(),
            "submission_projection": submission_projection,
            "evaluation": evaluation,
            "input_state": input_state,
            "effective_state": effective_state,
            "evaluator_version": self.evaluator_version.version_id,
            "workspace_manifest": workspace_manifest(workspace),
        }
        self.bank.write_attempt(inspiration.attempt_id, metadata)
        self.bank.event(
            "proposal.completed",
            attempt_id=inspiration.attempt_id,
            parent_attempt_id=inspiration.parent_attempt_id,
            ok=call.ok,
            evaluation=evaluation,
            usage=call.usage,
        )
        self.completed_proposals += 1
        self.completion_event.set()

    async def _worker(
        self,
        queue: asyncio.Queue[Inspiration | None],
        worker_id: int,
    ) -> None:
        while True:
            inspiration = await queue.get()
            try:
                if inspiration is None:
                    return
                self.bank.event(
                    "proposal.dequeued",
                    attempt_id=inspiration.attempt_id,
                    worker_id=worker_id,
                )
                await self._execute(inspiration, worker_id=worker_id)
            finally:
                queue.task_done()

    def build_report(self) -> dict[str, Any]:
        attempts = self.bank.completed_attempts()
        incumbent = self.bank.incumbent()
        total_usage: dict[str, int] = {}
        best_so_far: list[dict[str, Any]] = []
        best = attempts[0]
        for attempt in attempts:
            if self.bank.is_better(attempt, best):
                best = attempt
            usage = (attempt.get("proposal_call") or {}).get("usage") or {}
            for key, value in usage.items():
                if isinstance(value, int):
                    total_usage[key] = total_usage.get(key, 0) + value
            best_so_far.append(
                {
                    "attempt_id": attempt["attempt_id"],
                    "best_attempt_id": best["attempt_id"],
                    "best_evaluation": best.get("evaluation"),
                }
            )
        context_usage: dict[str, int] = {}
        for path in sorted(self.bank.context_calls_dir.glob("context-*/call.json")):
            call = json.loads(path.read_text(encoding="utf-8"))
            for key, value in (call.get("usage") or {}).items():
                if isinstance(value, int):
                    context_usage[key] = context_usage.get(key, 0) + value
        evaluator_history: list[dict[str, Any]] = []
        for path in sorted(
            (self.run_dir / "evaluators").glob("evaluator-*/version.json")
        ):
            value = json.loads(path.read_text(encoding="utf-8"))
            evaluator_history.append(
                {
                    "version_id": value.get("version_id"),
                    "parent_version_id": value.get("parent_version_id"),
                    "status": value.get("status"),
                    "evaluator_sha256": value.get("evaluator_sha256"),
                }
            )
        return {
            "name": self.config.name,
            "task": self.task.name,
            "generated_at": utc_now(),
            "attempt_count": len(attempts),
            "proposal_count": len(attempts) - 1,
            "context_call_count": len(
                list(self.bank.context_calls_dir.glob("context-*/call.json"))
            ),
            "qualified_count": sum(
                (item.get("evaluation") or {}).get(self.task.qualification_key)
                is True
                for item in attempts
            ),
            "incumbent_attempt_id": incumbent["attempt_id"],
            "incumbent_evaluation": incumbent.get("evaluation"),
            "proposal_usage": total_usage,
            "context_usage": context_usage,
            "best_so_far": best_so_far,
            "isolation": self.execution_backend.attestation.as_dict(),
            "terminal_reason": (
                "agent_stop" if self.voluntary_stop else "proposal_budget_exhausted"
            ),
            "stop_reason": self.stop_reason,
            "evaluator_version": self.evaluator_version.version_id,
            "evaluator_cycles": self.config.evaluator_cycles,
            "evaluator_history": evaluator_history,
            "agent_evidence": audit_agent_evidence(self.run_dir),
        }

    async def run(self) -> dict[str, Any]:
        state_path = self.run_dir / "run.json"
        if state_path.is_file():
            existing_state = json.loads(state_path.read_text(encoding="utf-8"))
            if existing_state.get("status") == "completed":
                report_path = self.run_dir / "report.json"
                manifest_path = self.run_dir / "evidence_manifest.json"
                if not report_path.is_file() or not manifest_path.is_file():
                    raise RuntimeError("completed run is missing sealed evidence")
                expected_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                if expected_manifest != evidence_manifest(self.run_dir):
                    raise RuntimeError("completed run evidence manifest mismatch")
                return json.loads(report_path.read_text(encoding="utf-8"))
        await self.initialize()
        try:
            for cycle_index in range(
                self.next_cycle_index,
                self.config.evaluator_cycles,
            ):
                target = (
                    self.config.max_proposals * (cycle_index + 1)
                    + self.config.evaluator_cycles
                    - 1
                ) // self.config.evaluator_cycles
                if target > self.scheduled_proposals:
                    queue: asyncio.Queue[Inspiration | None] = asyncio.Queue()
                    workers = [
                        asyncio.create_task(self._worker(queue, worker_id))
                        for worker_id in range(self.config.max_concurrency)
                    ]
                    producer = asyncio.create_task(
                        self._produce(queue, target_proposals=target)
                    )
                    active_tasks = [producer, *workers]
                    await asyncio.gather(*active_tasks)
                    await queue.join()
                if self.voluntary_stop:
                    break
                if (
                    cycle_index + 1 < self.config.evaluator_cycles
                    and self.task.evaluator_mode == "generated"
                ):
                    attempt_workspaces = {
                        item["attempt_id"]: self.bank.workspace(item["attempt_id"])
                        for item in self.bank.completed_attempts()
                    }
                    attempt_input_states = {
                        item["attempt_id"]: self._state_path(
                            item.get("input_state")
                        )
                        for item in self.bank.completed_attempts()
                    }
                    version, replay = await self.evaluator_evolution.improve(
                        bank_summary=self.bank.summary(),
                        attempt_workspaces=attempt_workspaces,
                        attempt_input_states=attempt_input_states,
                    )
                    if version.version_id != self.evaluator_version.version_id:
                        self.evaluator_version = version
                        self.task = version.task
                        self.bank.task = self.task
                        self.bank.set_evaluator_version(version.version_id, replay)
                self.next_cycle_index = cycle_index + 1
                state_path = self.run_dir / "run.json"
                state = json.loads(state_path.read_text(encoding="utf-8"))
                state["next_cycle_index"] = self.next_cycle_index
                state["evaluator_version"] = self.evaluator_version.version_id
                atomic_write_json(state_path, state)
        except BaseException:
            active_tasks = locals().get("active_tasks", [])
            for task in active_tasks:
                task.cancel()
            await asyncio.gather(*active_tasks, return_exceptions=True)
            state_path = self.run_dir / "run.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "failed"
            state["failed_at"] = utc_now()
            atomic_write_json(state_path, state)
            raise

        report = self.build_report()
        atomic_write_json(self.run_dir / "report.json", report)
        state_path = self.run_dir / "run.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["status"] = "completed"
        state["completed_at"] = utc_now()
        state["report"] = "report.json"
        state["evidence_manifest"] = "evidence_manifest.json"
        atomic_write_json(state_path, state)
        self.bank.event(
            "run.completed",
            incumbent_attempt_id=report["incumbent_attempt_id"],
            incumbent_evaluation=report["incumbent_evaluation"],
        )
        write_evidence_manifest(self.run_dir)
        return report


def copy_preregistration_inputs(
    *,
    config_path: Path,
    task_path: Path,
    run_dir: Path,
) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    source_root = Path(__file__).resolve().parent.parent
    harness_snapshot = run_dir / "harness_source"
    source_kind = (
        "checkout"
        if (source_root / "pyproject.toml").is_file()
        else "installed_distribution"
    )
    if not harness_snapshot.exists():
        harness_snapshot.mkdir()
        copy_workspace(
            source_root / "rehyra",
            harness_snapshot / "rehyra",
        )
        benchmark_snapshot = harness_snapshot / "benchmarks"
        benchmark_snapshot.mkdir()
        shutil.copy2(
            source_root / "benchmarks" / "__init__.py",
            benchmark_snapshot / "__init__.py",
        )
        copy_workspace(
            source_root / "benchmarks" / "tasks",
            benchmark_snapshot / "tasks",
        )
        if source_kind == "checkout":
            shutil.copy2(
                source_root / "pyproject.toml",
                harness_snapshot / "pyproject.toml",
            )
        else:
            distribution = importlib_metadata.distribution("rehyra")
            atomic_write_json(
                harness_snapshot / "distribution-metadata.json",
                {
                    "name": distribution.metadata["Name"],
                    "version": distribution.version,
                    "requires_python": distribution.metadata.get(
                        "Requires-Python"
                    ),
                },
            )
    for source, name in (
        (config_path, "config.frozen.toml"),
        (task_path, "task.frozen.json"),
    ):
        destination = run_dir / name
        if not destination.exists():
            shutil.copy2(source, destination)
    manifest_path = run_dir / "preregistration.json"
    if not manifest_path.exists():
        try:
            git_commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=source_root,
                stderr=subprocess.DEVNULL,
                text=True,
            ).strip()
        except (OSError, subprocess.CalledProcessError):
            git_commit = None
        atomic_write_json(
            manifest_path,
            {
                "frozen_at": utc_now(),
                "config": {
                    "source": str(config_path.resolve()),
                    "frozen": "config.frozen.toml",
                    "sha256": sha256_file(run_dir / "config.frozen.toml"),
                },
                "task": {
                    "source": str(task_path.resolve()),
                    "frozen": "task.frozen.json",
                    "sha256": sha256_file(run_dir / "task.frozen.json"),
                },
                "harness": {
                    "git_commit": git_commit,
                    "source_root": str(source_root),
                    "source_kind": source_kind,
                    "snapshot": "harness_source",
                    "manifest": workspace_manifest(harness_snapshot),
                },
            },
        )
