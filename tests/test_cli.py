from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rehyra.cli import (
    _run,
    _task_with_frozen_harness_paths,
    _validate,
    build_parser,
)
from rehyra.contracts import TaskSpec
from rehyra.loop import copy_preregistration_inputs
from rehyra.util import (
    atomic_write_json,
    sha256_file,
    workspace_manifest,
    write_evidence_manifest,
)


class _FakeRunner:
    constructions: list[tuple[object, object]] = []

    def __init__(self, *, config, task, backend, run_dir) -> None:
        del backend, run_dir
        self.constructions.append((config, task))

    async def run(self) -> dict[str, object]:
        return {"ok": True}


class CliBrandingTests(unittest.TestCase):
    def test_parser_uses_rehyra_brand(self) -> None:
        parser = build_parser()

        self.assertEqual(parser.prog, "rehyra")
        self.assertIn("ReHyra", parser.description or "")


class FrozenRunInputTests(unittest.TestCase):
    def test_legacy_task_path_maps_to_consolidated_harness(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_root = root / "source"
            legacy_evaluator = source_root / "tasks" / "evaluate.py"
            run_dir = root / "run"
            frozen_evaluator = (
                run_dir
                / "harness_source"
                / "benchmarks"
                / "tasks"
                / "evaluate.py"
            )
            frozen_evaluator.parent.mkdir(parents=True)
            frozen_evaluator.write_text("pass\n", encoding="utf-8")
            task = TaskSpec(
                name="legacy",
                goal="minimize score",
                seed_workspace=root / "seed",
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(legacy_evaluator)),
                objective_key="score",
                direction="minimize",
            )

            rebased = _task_with_frozen_harness_paths(
                task,
                run_dir=run_dir,
                preregistration={
                    "harness": {
                        "source_root": str(source_root),
                        "snapshot": "harness_source",
                    }
                },
            )

            self.assertEqual(
                rebased.evaluator_argv[1],
                str(frozen_evaluator),
            )

    def test_resume_uses_frozen_config_and_task(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            (source / "seed").mkdir()
            (source / "seed" / "candidate.json").write_text(
                '{"score": 1}',
                encoding="utf-8",
            )
            task_path = source / "task.json"
            harness_adapter = (
                Path(__file__).resolve().parents[1]
                / "rehyra"
                / "task.py"
            )
            task_path.write_text(
                json.dumps(
                    {
                        "name": "original-task",
                        "goal": "goal",
                        "seed_workspace": "seed",
                        "submission_relpath": "candidate.json",
                        "evaluator_argv": [
                            sys.executable,
                            str(harness_adapter),
                            str(
                                Path(__file__).resolve().parents[1]
                                / "task-data"
                                / "external-input"
                            ),
                        ],
                        "objective_key": "score",
                        "direction": "minimize",
                    }
                ),
                encoding="utf-8",
            )
            config_path = source / "config.toml"
            config_path.write_text(
                "[run]\n"
                'name = "original-run"\n'
                'task_spec = "task.json"\n'
                "max_proposals = 1\n"
                "\n"
                "[agent]\n"
                'backend = "command"\n'
                'model = "original-model"\n'
                'command_argv = ["/usr/bin/true"]\n',
                encoding="utf-8",
            )
            run_dir = root / "run"
            args = argparse.Namespace(config=str(config_path), run_dir=str(run_dir))
            _FakeRunner.constructions.clear()
            with (
                patch("rehyra.cli.make_backend", return_value=object()),
                patch("rehyra.cli.ReHyraLoop", _FakeRunner),
            ):
                self.assertEqual(_run(args), 0)
                task_path.write_text(
                    task_path.read_text(encoding="utf-8").replace(
                        "original-task",
                        "mutated-task",
                    ),
                    encoding="utf-8",
                )
                config_path.write_text(
                    config_path.read_text(encoding="utf-8").replace(
                        "original-model",
                        "mutated-model",
                    ),
                    encoding="utf-8",
                )
                self.assertEqual(_run(args), 0)

            self.assertEqual(len(_FakeRunner.constructions), 2)
            preregistration = json.loads(
                (run_dir / "preregistration.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                preregistration["harness"]["snapshot"],
                "harness_source",
            )
            self.assertEqual(
                preregistration["harness"]["source_kind"],
                "checkout",
            )
            self.assertTrue(
                (run_dir / "harness_source" / "pyproject.toml").is_file()
            )
            self.assertTrue(
                preregistration["harness"]["manifest"]["tree_sha256"]
            )
            self.assertEqual(
                preregistration["harness"]["source_root"],
                str(Path(__file__).resolve().parents[1]),
            )
            for config, task in _FakeRunner.constructions:
                self.assertEqual(config.agent.model, "original-model")
                self.assertEqual(task.name, "original-task")
                self.assertEqual(task.seed_workspace, (source / "seed").resolve())
                self.assertEqual(
                    task.evaluator_argv[1],
                    str(
                        (
                            run_dir
                        / "harness_source"
                        / "rehyra"
                        / "task.py"
                        ).resolve()
                    ),
                )
                self.assertEqual(
                    task.evaluator_argv[2],
                    str(
                        Path(__file__).resolve().parents[1]
                        / "task-data"
                        / "external-input"
                    ),
                )

    def test_new_run_can_immutably_import_a_qualified_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            seed = source / "seed"
            seed.mkdir()
            (seed / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            task_path = source / "task.json"
            task_path.write_text(
                json.dumps(
                    {
                        "name": "toy",
                        "goal": "minimize score",
                        "seed_workspace": "seed",
                        "submission_relpath": "candidate.json",
                        "evaluator_argv": ["/usr/bin/true"],
                        "objective_key": "score",
                        "direction": "minimize",
                    }
                ),
                encoding="utf-8",
            )
            config_path = source / "config.toml"
            config_path.write_text(
                "[run]\n"
                'name = "toy"\n'
                'task_spec = "task.json"\n'
                "max_proposals = 1\n"
                "\n"
                "[agent]\n"
                'backend = "command"\n'
                'model = "fake"\n'
                'command_argv = ["/usr/bin/true"]\n',
                encoding="utf-8",
            )

            source_run = root / "source-run"
            copy_preregistration_inputs(
                config_path=config_path,
                task_path=task_path,
                run_dir=source_run,
            )
            source_workspace = (
                source_run / "attempts" / "attempt-0001" / "workspace"
            )
            source_workspace.mkdir(parents=True)
            (source_workspace / "candidate.json").write_text(
                '{"score": 3}',
                encoding="utf-8",
            )
            cache = source_workspace / ".pytest_cache" / "v" / "cache"
            cache.mkdir(parents=True)
            (cache / "nodeids").write_text('["test_candidate"]', encoding="utf-8")
            atomic_write_json(
                source_run / "attempts" / "attempt-0001" / "attempt.json",
                {
                    "attempt_id": "attempt-0001",
                    "status": "completed",
                    "evaluation": {"qualified": True, "score": 3},
                    "workspace_manifest": workspace_manifest(source_workspace),
                },
            )
            atomic_write_json(
                source_run / "run.json",
                {"status": "completed"},
            )
            atomic_write_json(
                source_run / "report.json",
                {"incumbent_attempt_id": "attempt-0001"},
            )

            target_run = root / "target-run"
            args = argparse.Namespace(
                config=str(config_path),
                run_dir=str(target_run),
                seed_from=str(source_run),
                seed_attempt=None,
            )
            _FakeRunner.constructions.clear()
            with (
                patch("rehyra.cli.make_backend", return_value=object()),
                patch("rehyra.cli.ReHyraLoop", _FakeRunner),
            ):
                self.assertEqual(_run(args), 0)
                (source_workspace / "candidate.json").write_text(
                    '{"score": -999}',
                    encoding="utf-8",
                )
                self.assertEqual(_run(args), 0)

            self.assertEqual(len(_FakeRunner.constructions), 2)
            for _, task in _FakeRunner.constructions:
                self.assertEqual(
                    task.seed_workspace,
                    target_run.resolve() / "imported_seed",
                )
                self.assertEqual(
                    (task.seed_workspace / "candidate.json").read_text(
                        encoding="utf-8"
                    ),
                    '{"score": 3}',
                )
                self.assertEqual(
                    (
                        task.seed_workspace
                        / ".pytest_cache"
                        / "v"
                        / "cache"
                        / "nodeids"
                    ).read_text(encoding="utf-8"),
                    '["test_candidate"]',
                )
            preregistration = json.loads(
                (target_run / "preregistration.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                preregistration["seed_import"]["source_attempt_id"],
                "attempt-0001",
            )
            self.assertTrue(
                preregistration["seed_import"]["manifest"]["tree_sha256"]
            )


class ValidationTests(unittest.TestCase):
    def _validate(
        self,
        run_dir: Path,
        expected_root: str | None = None,
    ) -> tuple[int, dict]:
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            returncode = _validate(
                argparse.Namespace(
                    run_dir=str(run_dir),
                    expected_root=expected_root,
                )
            )
        return returncode, json.loads(stdout.getvalue())

    def test_empty_directory_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            returncode, result = self._validate(Path(raw))

        self.assertEqual(returncode, 1)
        self.assertFalse(result["ok"])
        self.assertIn("run is missing preregistration.json", result["failures"])
        self.assertIn("run is missing run.json", result["failures"])
        self.assertIn("run contains no attempt evidence", result["failures"])

    def test_completed_manifest_detects_tampering_and_checks_external_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run_dir = Path(raw) / "run"
            run_dir.mkdir()
            frozen_config = run_dir / "config.frozen.toml"
            frozen_task = run_dir / "task.frozen.json"
            frozen_config.write_text("[run]\nname='test'\n", encoding="utf-8")
            frozen_task.write_text("{}\n", encoding="utf-8")
            atomic_write_json(
                run_dir / "preregistration.json",
                {
                    "config": {
                        "frozen": frozen_config.name,
                        "sha256": sha256_file(frozen_config),
                    },
                    "task": {
                        "frozen": frozen_task.name,
                        "sha256": sha256_file(frozen_task),
                    },
                },
            )
            atomic_write_json(
                run_dir / "run.json",
                {
                    "status": "completed",
                    "report": "report.json",
                    "evidence_manifest": "evidence_manifest.json",
                },
            )
            atomic_write_json(run_dir / "report.json", {"score": 1})
            workspace = run_dir / "attempts" / "attempt-0000" / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "solution.py").write_text("VALUE = 1\n", encoding="utf-8")
            atomic_write_json(
                workspace.parent / "attempt.json",
                {
                    "attempt_id": "attempt-0000",
                    "status": "completed",
                    "evaluation": {"qualified": True, "score": 1},
                    "workspace_manifest": workspace_manifest(workspace),
                },
            )
            manifest = write_evidence_manifest(run_dir)

            returncode, result = self._validate(
                run_dir,
                str(manifest["root_sha256"]),
            )
            self.assertEqual(returncode, 0, result)
            self.assertTrue(result["ok"])
            self.assertEqual(
                result["evidence_root_sha256"],
                manifest["root_sha256"],
            )

            atomic_write_json(run_dir / "report.json", {"score": 999})
            returncode, result = self._validate(run_dir)
            self.assertEqual(returncode, 1)
            self.assertIn(
                "evidence manifest does not match run contents",
                result["failures"],
            )
