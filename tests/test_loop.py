from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from rehyra.contracts import AgentCall, AgentConfig, Inspiration, RunConfig, TaskSpec
from rehyra.loop import ReHyraLoop


EVALUATOR = """\
import json
from pathlib import Path

value = json.loads(Path("candidate.json").read_text())["score"]
print(json.dumps({"qualified": True, "score": value}))
"""

CONTEXT_CLEAN_EVALUATOR = """\
import json
from pathlib import Path

value = json.loads(Path("candidate.json").read_text())["score"]
context_present = Path(".hyra_context").exists()
print(json.dumps({
    "qualified": not context_present,
    "score": value,
    "context_present": context_present,
}))
"""

STATEFUL_EVALUATOR = """\
import json
import os
from pathlib import Path

value = json.loads(Path("candidate.json").read_text())["score"]
input_path = os.environ.get("INPUT_STATE")
seen = None
if input_path:
    seen = json.loads(Path(input_path).read_text())["best"]
Path(os.environ["CAPTURE_STATE"]).write_text(json.dumps({"best": value}))
print(json.dumps({
    "qualified": True,
    "score": value,
    "seen_parent_state": seen,
}))
"""

PYTHON_SUBMISSION_EVALUATOR = """\
import importlib.util
import json
import sys

spec = importlib.util.spec_from_file_location("candidate", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print(json.dumps({"qualified": True, "score": module.run_code()}))
"""


class ImprovingFakeBackend:
    def __init__(self) -> None:
        self.context_summaries: list[dict[str, Any]] = []
        self.proposal_contexts: list[dict[str, Any]] = []
        self.runtime_state_snapshots: list[bytes] = []
        self.context_runtime_state_snapshots: list[dict[str, bytes]] = []

    async def context(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        requested: int,
        remaining_proposals: int,
        work_dir: Path,
    ) -> AgentCall:
        self.context_summaries.append(bank_summary)
        self.context_runtime_state_snapshots.append(
            {
                attempt_id: (work_dir / relative).read_bytes()
                for attempt_id, relative in bank_summary.get(
                    "runtime_state_snapshots",
                    {},
                ).items()
            }
        )
        parent = bank_summary["incumbent_attempt_id"]
        output = {
            "inspirations": [
                {
                    "title": f"decrement {index}",
                    "hypothesis": "a smaller score is better",
                    "instructions": "decrement candidate score",
                    "risk": "none",
                    "parent_attempt_id": parent,
                    "reference_attempt_ids": [parent],
                }
                for index in range(requested)
            ]
        }
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={"input_tokens": 10, "output_tokens": 5},
            output=output,
        )

    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall:
        self.proposal_contexts.append(bank_context)
        state_snapshot = (bank_context.get("runtime_state") or {}).get(
            "snapshot"
        )
        if isinstance(state_snapshot, str):
            self.runtime_state_snapshots.append(
                (workspace / state_snapshot).read_bytes()
            )
        path = workspace / "candidate.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["score"] -= 1
        path.write_text(json.dumps(value), encoding="utf-8")
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={"input_tokens": 20, "output_tokens": 7},
            output={
                "diagnostic_summary": (
                    "Candidate improved the score; category audit is clean."
                )
            },
        )


class RecombiningFakeBackend(ImprovingFakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.context_round = 0
        self.saw_reference_source = False

    async def context(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        requested: int,
        remaining_proposals: int,
        work_dir: Path,
    ) -> AgentCall:
        self.context_summaries.append(bank_summary)
        self.context_round += 1
        parent = "attempt-0000"
        references = (
            ["attempt-0000"]
            if self.context_round == 1
            else ["attempt-0001"]
        )
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={
                "inspirations": [
                    {
                        "title": "make and then reuse a technique",
                        "hypothesis": "reference source carries the technique",
                        "instructions": "combine the selected implementation",
                        "risk": "none",
                        "parent_attempt_id": parent,
                        "reference_attempt_ids": references,
                    }
                ]
            },
        )

    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall:
        self.proposal_contexts.append(bank_context)
        candidate_path = workspace / "candidate.json"
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
        if inspiration.attempt_id == "attempt-0001":
            candidate["score"] = 9
            (workspace / "technique.txt").write_text(
                "reusable implementation detail",
                encoding="utf-8",
            )
        else:
            reference = (
                workspace
                / ".hyra_context"
                / "references"
                / "attempt-0001"
                / "technique.txt"
            )
            listed = {
                path
                for paths in bank_context["source_snapshots"].values()
                for path in paths
            }
            self.saw_reference_source = (
                reference.read_text(encoding="utf-8")
                == "reusable implementation detail"
                and (
                    ".hyra_context/references/attempt-0001/technique.txt"
                    in listed
                )
            )
            candidate["score"] = 8 if self.saw_reference_source else 10
        candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={"diagnostic_summary": "combined selected reference source"},
        )


class WorseningFakeBackend(ImprovingFakeBackend):
    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall:
        self.proposal_contexts.append(bank_context)
        path = workspace / "candidate.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["score"] += 1
        path.write_text(json.dumps(value), encoding="utf-8")
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={"diagnostic_summary": "intentionally worse candidate"},
        )


class EvaluatorTamperingBackend(ImprovingFakeBackend):
    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall:
        del goal, inspiration, submission_relpath, output_dir
        self.proposal_contexts.append(bank_context)
        (workspace / "evaluator.py").write_text(
            'print(\'{"qualified": true, "score": 999}\')\n',
            encoding="utf-8",
        )
        (workspace / "forged-test.py").write_text(
            "assert True\n",
            encoding="utf-8",
        )
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={"diagnostic_summary": "attempted evaluator rewrite"},
        )


class BlockOnlyFakeBackend(ImprovingFakeBackend):
    async def proposal(
        self,
        *,
        goal: str,
        inspiration: Inspiration,
        bank_context: dict[str, Any],
        submission_relpath: Path,
        workspace: Path,
        output_dir: Path,
    ) -> AgentCall:
        self.proposal_contexts.append(bank_context)
        (workspace / submission_relpath).write_text(
            "# EVOLVE-BLOCK-START\n"
            "VALUE = 11\n"
            "# EVOLVE-BLOCK-END\n",
            encoding="utf-8",
        )
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={"diagnostic_summary": "replaced only the editable body"},
        )


class BilevelFakeBackend(ImprovingFakeBackend):
    def __init__(self) -> None:
        super().__init__()
        self.architect_calls = 0
        self.critic_calls = 0

    async def evaluator_architect(
        self,
        *,
        goal: str,
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
        initial: bool,
    ) -> AgentCall:
        self.architect_calls += 1
        evaluator = workspace / evaluator_relpath
        evaluator.parent.mkdir(parents=True, exist_ok=True)
        evaluator.write_text(EVALUATOR, encoding="utf-8")
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={
                "changed": True,
                "summary": "deterministic score evaluator",
                "objective_key": "score",
                "direction": "minimize",
                "qualification_key": "qualified",
                "evaluator_relpath": evaluator_relpath.as_posix(),
            },
        )

    async def evaluator_critic(
        self,
        *,
        goal: str,
        architect_output: dict[str, Any],
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
    ) -> AgentCall:
        self.critic_calls += 1
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={
                "approve": True,
                "blockers": [],
                "risks": [],
                "recommended_tests": [],
            },
        )


class RejectingEvolutionBackend(BilevelFakeBackend):
    async def evaluator_critic(
        self,
        *,
        goal: str,
        architect_output: dict[str, Any],
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
    ) -> AgentCall:
        approved = await super().evaluator_critic(
            goal=goal,
            architect_output=architect_output,
            bank_summary=bank_summary,
            workspace=workspace,
            output_dir=output_dir,
            evaluator_relpath=evaluator_relpath,
        )
        if self.critic_calls == 1:
            return approved
        return AgentCall(
            ok=True,
            returncode=0,
            duration_seconds=0.0,
            error=None,
            usage={},
            output={
                "approve": False,
                "blockers": ["no evaluator headroom"],
                "risks": [],
                "recommended_tests": [],
            },
        )


class RepairingInitialEvaluatorBackend(BilevelFakeBackend):
    async def evaluator_critic(
        self,
        *,
        goal: str,
        architect_output: dict[str, Any],
        bank_summary: dict[str, Any],
        workspace: Path,
        output_dir: Path,
        evaluator_relpath: Path,
    ) -> AgentCall:
        approved = await super().evaluator_critic(
            goal=goal,
            architect_output=architect_output,
            bank_summary=bank_summary,
            workspace=workspace,
            output_dir=output_dir,
            evaluator_relpath=evaluator_relpath,
        )
        if self.critic_calls == 1:
            return AgentCall(
                ok=True,
                returncode=0,
                duration_seconds=0.0,
                error=None,
                usage={},
                output={
                    "approve": False,
                    "blockers": ["malformed input crashes evaluator"],
                    "risks": [],
                    "recommended_tests": ["nested invalid JSON"],
                },
            )
        return approved


class RunnerTests(unittest.TestCase):
    def test_submission_only_policy_restores_evaluator_and_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            (seed / "evaluator.py").write_text(EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="sealed-evaluator",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(
                    sys.executable,
                    "{workspace}/evaluator.py",
                ),
                objective_key="score",
                direction="minimize",
                mutation_policy="submission_only",
            )
            config = RunConfig(
                name="sealed-evaluator",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"

            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=EvaluatorTamperingBackend(),
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(report["incumbent_attempt_id"], "attempt-0000")
            attempt_dir = run_dir / "attempts" / "attempt-0001"
            self.assertEqual(
                json.loads(
                    (attempt_dir / "workspace" / "candidate.json").read_text(
                        encoding="utf-8"
                    )
                )["score"],
                10,
            )
            self.assertEqual(
                (attempt_dir / "workspace" / "evaluator.py").read_text(
                    encoding="utf-8"
                ),
                EVALUATOR,
            )
            self.assertFalse(
                (attempt_dir / "workspace" / "forged-test.py").exists()
            )
            metadata = json.loads(
                (attempt_dir / "attempt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["evaluation"]["score"], 10)
            self.assertEqual(
                metadata["submission_projection"]["policy"],
                "submission_only",
            )

    def test_initial_evaluator_blocker_triggers_architect_revision(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 5}',
                encoding="utf-8",
            )
            task = TaskSpec(
                name="repair-evaluator",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(),
                objective_key="score",
                direction="minimize",
                evaluator_mode="generated",
            )
            config = RunConfig(
                name="repair-evaluator",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            backend = RepairingInitialEvaluatorBackend()
            run_dir = root / "run"

            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=backend,
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(report["evaluator_version"], "evaluator-0001")
            self.assertEqual(backend.architect_calls, 2)
            self.assertEqual(backend.critic_calls, 2)
            first = json.loads(
                (
                    run_dir
                    / "evaluators"
                    / "evaluator-0000"
                    / "version.json"
                ).read_text(encoding="utf-8")
            )
            second_summary = json.loads(
                (
                    run_dir
                    / "evaluators"
                    / "evaluator-0001"
                    / "bank_summary.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(first["status"], "rejected_critic")
            self.assertEqual(
                second_summary["previous_evaluator_revision"]["critic_audit"][
                    "blockers"
                ],
                ["malformed input crashes evaluator"],
            )

    def test_loop_preserves_protected_submission_scaffolding(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "solution.py").write_text(
                "HEADER = 'fixed'\n"
                "# EVOLVE-BLOCK-START\n"
                "VALUE = 10\n"
                "# EVOLVE-BLOCK-END\n"
                "def run_code():\n"
                "    return VALUE\n",
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(
                PYTHON_SUBMISSION_EVALUATOR,
                encoding="utf-8",
            )
            task = TaskSpec(
                name="editable",
                goal="maximize score",
                seed_workspace=seed,
                submission_relpath=Path("solution.py"),
                evaluator_argv=(
                    sys.executable,
                    str(evaluator),
                    "{submission}",
                ),
                objective_key="score",
                direction="maximize",
                editable_start_marker="# EVOLVE-BLOCK-START",
                editable_end_marker="# EVOLVE-BLOCK-END",
            )
            config = RunConfig(
                name="editable",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=BlockOnlyFakeBackend(),
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(report["incumbent_evaluation"]["score"], 11)
            attempt_dir = run_dir / "attempts" / "attempt-0001"
            submission = (
                attempt_dir / "workspace" / "solution.py"
            ).read_text(encoding="utf-8")
            self.assertIn("HEADER = 'fixed'", submission)
            self.assertIn("def run_code():", submission)
            metadata = json.loads(
                (attempt_dir / "attempt.json").read_text(encoding="utf-8")
            )
            projection = metadata["submission_projection"]
            self.assertEqual(projection["policy"], "editable_region")
            self.assertTrue((attempt_dir / "agent" / "raw_submission").is_file())

    def test_async_loop_uses_completed_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text('{"score": 10}', encoding="utf-8")
            evaluator = root / "evaluator.py"
            evaluator.write_text(EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="toy",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(
                    sys.executable,
                    str(evaluator),
                ),
                objective_key="score",
                direction="minimize",
            )
            config = RunConfig(
                name="toy-run",
                task_spec=root / "task.json",
                max_proposals=4,
                max_concurrency=2,
                queue_target=2,
                context_batch_size=2,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            backend = ImprovingFakeBackend()
            runner = ReHyraLoop(
                config=config,
                task=task,
                backend=backend,
                run_dir=root / "run",
            )
            report = asyncio.run(runner.run())

            self.assertEqual(report["proposal_count"], 4)
            self.assertEqual(report["incumbent_evaluation"]["score"], 8)
            self.assertEqual(report["qualified_count"], 5)
            self.assertEqual(len(report["best_so_far"]), 5)
            attempts = sorted((root / "run" / "attempts").glob("attempt-*/attempt.json"))
            self.assertEqual(len(attempts), 5)
            self.assertGreaterEqual(len(backend.context_summaries), 2)
            later_summary = backend.context_summaries[-1]
            diagnostics = [
                item.get("proposal_diagnostic", "")
                for item in later_summary["attempts"]
            ]
            self.assertTrue(
                any("category audit is clean" in item for item in diagnostics)
            )
            self.assertNotIn(
                "evaluator_argv",
                later_summary["incumbent_evaluation"],
            )
            self.assertNotIn(
                "isolation",
                later_summary["incumbent_evaluation"],
            )
            for context in backend.proposal_contexts:
                for attempt in context["attempts"]:
                    self.assertNotIn(
                        "evaluator_argv",
                        attempt["evaluation"],
                    )
                    self.assertNotIn(
                        "isolation",
                        attempt["evaluation"],
                    )
            snapshots = sorted(
                (root / "run" / "context_calls").glob(
                    "context-*/source_snapshots/attempt-*/candidate.json"
                )
            )
            self.assertTrue(snapshots)

    def test_completed_run_resume_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 2}',
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="completed-resume",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
            )
            config = RunConfig(
                name="completed-resume",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            first = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=ImprovingFakeBackend(),
                    run_dir=run_dir,
                ).run()
            )
            sealed_before = {
                path.name: path.read_bytes()
                for path in (
                    run_dir / "report.json",
                    run_dir / "run.json",
                    run_dir / "events.jsonl",
                    run_dir / "evidence_manifest.json",
                )
            }

            second = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=ImprovingFakeBackend(),
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(second, first)
            self.assertEqual(
                {
                    path.name: path.read_bytes()
                    for path in (
                        run_dir / "report.json",
                        run_dir / "run.json",
                        run_dir / "events.jsonl",
                        run_dir / "evidence_manifest.json",
                    )
                },
                sealed_before,
            )
            (run_dir / "report.json").write_text(
                '{"incumbent_attempt_id": "forged"}\n',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                RuntimeError,
                "evidence manifest mismatch",
            ):
                asyncio.run(
                    ReHyraLoop(
                        config=config,
                        task=task,
                        backend=ImprovingFakeBackend(),
                        run_dir=run_dir,
                    ).run()
                )

    def test_proposal_can_recombine_selected_reference_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(CONTEXT_CLEAN_EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="reference-recombination",
                goal="minimize score by combining prior implementations",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
            )
            config = RunConfig(
                name="reference-recombination",
                task_spec=root / "task.json",
                max_proposals=2,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            backend = RecombiningFakeBackend()
            run_dir = root / "run"
            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=backend,
                    run_dir=run_dir,
                ).run()
            )

            self.assertTrue(backend.saw_reference_source)
            self.assertEqual(report["incumbent_evaluation"]["score"], 8)
            second_dir = run_dir / "attempts" / "attempt-0002"
            self.assertFalse((second_dir / "workspace" / ".hyra_context").exists())
            metadata = json.loads(
                (second_dir / "attempt.json").read_text(encoding="utf-8")
            )
            self.assertFalse(metadata["evaluation"]["context_present"])
            self.assertFalse(
                any(
                    item["path"].startswith(".hyra_context/")
                    for item in metadata["workspace_manifest"]["files"]
                )
            )
            bundle = json.loads(
                (second_dir / "reference_bundle.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                bundle["reference_attempt_ids"],
                ["attempt-0001"],
            )
            self.assertTrue(
                any(
                    item["path"]
                    == "references/attempt-0001/technique.txt"
                    for item in bundle["bundle_manifest"]["files"]
                )
            )

    def test_reserved_proposal_context_path_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            reserved = seed / ".hyra_context"
            reserved.mkdir()
            (reserved / "user.txt").write_text("collision", encoding="utf-8")
            evaluator = root / "evaluator.py"
            evaluator.write_text(EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="reserved-path",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
            )
            config = RunConfig(
                name="reserved-path",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            with self.assertRaisesRegex(
                RuntimeError,
                r"\.hyra_context is reserved",
            ):
                asyncio.run(
                    ReHyraLoop(
                        config=config,
                        task=task,
                        backend=ImprovingFakeBackend(),
                        run_dir=root / "run",
                    ).run()
                )

    def test_workspace_hash_detects_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text('{"score": 2}', encoding="utf-8")
            evaluator = root / "evaluator.py"
            evaluator.write_text(EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="toy",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
            )
            config = RunConfig(
                name="one",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            runner = ReHyraLoop(
                config=config,
                task=task,
                backend=ImprovingFakeBackend(),
                run_dir=run_dir,
            )
            asyncio.run(runner.run())
            metadata = json.loads(
                (run_dir / "attempts" / "attempt-0001" / "attempt.json").read_text()
            )
            before = metadata["workspace_manifest"]["tree_sha256"]
            (run_dir / "attempts" / "attempt-0001" / "workspace" / "candidate.json").write_text(
                '{"score": 999}',
                encoding="utf-8",
            )
            from rehyra.util import workspace_manifest

            after = workspace_manifest(
                run_dir / "attempts" / "attempt-0001" / "workspace"
            )["tree_sha256"]
            self.assertNotEqual(before, after)

    def test_runtime_state_follows_best_parent_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(STATEFUL_EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="stateful",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
                state_capture_env="CAPTURE_STATE",
                state_input_env="INPUT_STATE",
                state_description="validated parent best",
            )
            config = RunConfig(
                name="stateful",
                task_spec=root / "task.json",
                max_proposals=2,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            backend = ImprovingFakeBackend()
            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=backend,
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(report["incumbent_evaluation"]["score"], 8)
            seed_attempt = json.loads(
                (run_dir / "attempts" / "attempt-0000" / "attempt.json").read_text()
            )
            first = json.loads(
                (run_dir / "attempts" / "attempt-0001" / "attempt.json").read_text()
            )
            second = json.loads(
                (run_dir / "attempts" / "attempt-0002" / "attempt.json").read_text()
            )
            self.assertEqual(
                seed_attempt["effective_state"]["source_attempt_id"],
                "attempt-0000",
            )
            self.assertEqual(first["evaluation"]["seen_parent_state"], 10)
            self.assertEqual(
                first["effective_state"]["source_attempt_id"],
                "attempt-0001",
            )
            self.assertEqual(second["evaluation"]["seen_parent_state"], 9)
            self.assertEqual(
                second["effective_state"]["source_attempt_id"],
                "attempt-0002",
            )
            runtime_context = backend.proposal_contexts[-1]["runtime_state"]
            self.assertTrue(runtime_context["available"])
            self.assertEqual(
                runtime_context["source_attempt_id"],
                "attempt-0001",
            )
            self.assertEqual(
                json.loads(backend.runtime_state_snapshots[-1]),
                {"best": 9},
            )
            self.assertEqual(
                json.loads(
                    backend.context_runtime_state_snapshots[-1][
                        "attempt-0001"
                    ]
                ),
                {"best": 9},
            )
            self.assertFalse(
                (
                    run_dir
                    / "attempts"
                    / "attempt-0002"
                    / "workspace"
                    / ".hyra_context"
                ).exists()
            )

    def test_worse_candidate_retains_parent_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(STATEFUL_EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="stateful",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
                state_capture_env="CAPTURE_STATE",
                state_input_env="INPUT_STATE",
            )
            config = RunConfig(
                name="stateful-worse",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=WorseningFakeBackend(),
                    run_dir=run_dir,
                ).run()
            )
            attempt = json.loads(
                (run_dir / "attempts" / "attempt-0001" / "attempt.json").read_text()
            )
            self.assertEqual(attempt["evaluation"]["score"], 11)
            self.assertEqual(
                attempt["effective_state"]["source_attempt_id"],
                "attempt-0000",
            )

    def test_generated_evaluator_runs_bilevel_loop_and_replay(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text('{"score": 5}', encoding="utf-8")
            task = TaskSpec(
                name="toy-generated",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(),
                objective_key="score",
                direction="minimize",
                evaluator_mode="generated",
            )
            config = RunConfig(
                name="bilevel",
                task_spec=root / "task.json",
                max_proposals=2,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
                evaluator_cycles=2,
            )
            backend = BilevelFakeBackend()
            run_dir = root / "run"
            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=backend,
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(report["proposal_count"], 2)
            self.assertEqual(report["incumbent_evaluation"]["score"], 3)
            self.assertEqual(report["evaluator_version"], "evaluator-0001")
            self.assertEqual(backend.architect_calls, 2)
            self.assertEqual(backend.critic_calls, 2)
            replay = run_dir / "evaluator_replays" / "evaluator-0001"
            self.assertTrue((replay / "attempt-0000.json").is_file())
            self.assertTrue((replay / "attempt-0001.json").is_file())

            resumed_backend = BilevelFakeBackend()
            resumed_report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=resumed_backend,
                    run_dir=run_dir,
                ).run()
            )
            self.assertEqual(resumed_report["incumbent_evaluation"]["score"], 3)
            self.assertEqual(resumed_report["evaluator_version"], "evaluator-0001")
            self.assertEqual(resumed_backend.architect_calls, 0)
            self.assertEqual(resumed_backend.critic_calls, 0)

    def test_generated_evaluator_restore_detects_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text('{"score": 5}', encoding="utf-8")
            task = TaskSpec(
                name="toy-generated",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(),
                objective_key="score",
                direction="minimize",
                evaluator_mode="generated",
            )
            config = RunConfig(
                name="tamper",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=BilevelFakeBackend(),
                    run_dir=run_dir,
                ).run()
            )
            evaluator = (
                run_dir
                / "evaluators"
                / "evaluator-0000"
                / "workspace"
                / "evaluator.py"
            )
            evaluator.write_text("raise SystemExit(1)\n", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "checksum mismatch"):
                asyncio.run(
                    ReHyraLoop(
                        config=config,
                        task=task,
                        backend=BilevelFakeBackend(),
                        run_dir=run_dir,
                    ).initialize()
                )

    def test_resume_evaluates_durably_completed_agent_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text('{"score": 2}', encoding="utf-8")
            evaluator = root / "evaluator.py"
            evaluator.write_text(EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="recover",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
            )
            config = RunConfig(
                name="recover",
                task_spec=root / "task.json",
                max_proposals=1,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
            )
            run_dir = root / "run"
            first = ReHyraLoop(
                config=config,
                task=task,
                backend=ImprovingFakeBackend(),
                run_dir=run_dir,
            )
            asyncio.run(first.initialize())

            from rehyra.util import copy_workspace

            attempt_dir = run_dir / "attempts" / "attempt-0001"
            workspace = attempt_dir / "workspace"
            copy_workspace(run_dir / "attempts" / "attempt-0000" / "workspace", workspace)
            (workspace / "candidate.json").write_text(
                '{"score": 1}',
                encoding="utf-8",
            )
            context_dir = workspace / ".hyra_context" / "references"
            context_dir.mkdir(parents=True)
            (context_dir / "temporary.txt").write_text(
                "must not reach evaluator",
                encoding="utf-8",
            )
            first.bank.write_attempt(
                "attempt-0001",
                {
                    "status": "running",
                    "kind": "proposal",
                    "parent_attempt_id": "attempt-0000",
                    "created_at": "2026-01-01T00:00:00+00:00",
                },
            )
            agent_dir = attempt_dir / "agent"
            agent_dir.mkdir()
            (agent_dir / "call.json").write_text(
                json.dumps(
                    {
                        "ok": True,
                        "returncode": 0,
                        "duration_seconds": 1.0,
                        "error": None,
                        "usage": {},
                        "output": {"message": "durably completed"},
                    }
                ),
                encoding="utf-8",
            )

            resumed = ReHyraLoop(
                config=config,
                task=task,
                backend=ImprovingFakeBackend(),
                run_dir=run_dir,
            )
            asyncio.run(resumed.initialize())
            recovered = resumed.bank.read_attempt("attempt-0001")
            self.assertEqual(recovered["evaluation"]["score"], 1)
            self.assertFalse((workspace / ".hyra_context").exists())
            self.assertEqual(
                recovered["proposal_call"]["output"]["diagnostic_summary"],
                "durably completed",
            )
            self.assertEqual(resumed.completed_proposals, 1)

    def test_rejected_evaluator_evolution_keeps_last_good_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text('{"score": 5}', encoding="utf-8")
            task = TaskSpec(
                name="toy-generated",
                goal="minimize score",
                seed_workspace=seed,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(),
                objective_key="score",
                direction="minimize",
                evaluator_mode="generated",
            )
            config = RunConfig(
                name="bilevel-rejection",
                task_spec=root / "task.json",
                max_proposals=2,
                max_concurrency=1,
                queue_target=1,
                context_batch_size=1,
                agent=AgentConfig(backend="fake", model="fake"),
                evaluator_cycles=2,
            )
            run_dir = root / "run"
            report = asyncio.run(
                ReHyraLoop(
                    config=config,
                    task=task,
                    backend=RejectingEvolutionBackend(),
                    run_dir=run_dir,
                ).run()
            )

            self.assertEqual(report["proposal_count"], 2)
            self.assertEqual(report["incumbent_evaluation"]["score"], 3)
            self.assertEqual(report["evaluator_version"], "evaluator-0000")
            metadata = json.loads(
                (
                    run_dir
                    / "evaluators"
                    / "evaluator-0001"
                    / "version.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(metadata["status"], "rejected_critic")


if __name__ == "__main__":
    unittest.main()
