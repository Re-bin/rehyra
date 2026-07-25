from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rehyra.contracts import RunConfig, TaskSpec
from rehyra.task import TaskBundle


class ModelTests(unittest.TestCase):
    def test_loads_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "seed").mkdir()
            task_path = root / "task.json"
            task_path.write_text(
                json.dumps(
                    {
                        "name": "toy",
                        "goal": "win",
                        "seed_workspace": "seed",
                        "submission_relpath": "answer.txt",
                        "evaluator_argv": ["python", "eval.py"],
                        "objective_key": "score",
                        "direction": "maximize",
                        "runtime_state": {
                            "capture_env": "CAPTURE_STATE",
                            "input_env": "INPUT_STATE",
                            "description": "validated parent state",
                            "max_bytes": 1234,
                        },
                        "editable_region": {
                            "start_marker": "# START",
                            "end_marker": "# END",
                        },
                        "evaluator_env": {"MAGIC": "yes"},
                        "evaluator_path_prepend": ["seed"],
                    }
                ),
                encoding="utf-8",
            )
            config_path = root / "config.toml"
            config_path.write_text(
                """\
[run]
name = "toy"
task_spec = "task.json"
max_proposals = 2

[agent]
model = "fake"
""",
                encoding="utf-8",
            )
            task = TaskSpec.load(task_path)
            config = RunConfig.load(config_path)
            self.assertEqual(task.seed_workspace, (root / "seed").resolve())
            self.assertEqual(config.task_spec, task_path.resolve())
            self.assertEqual(config.queue_target, 1)
            self.assertEqual(task.state_capture_env, "CAPTURE_STATE")
            self.assertEqual(task.state_input_env, "INPUT_STATE")
            self.assertEqual(task.state_description, "validated parent state")
            self.assertEqual(task.state_max_bytes, 1234)
            self.assertEqual(task.editable_start_marker, "# START")
            self.assertEqual(task.editable_end_marker, "# END")
            self.assertEqual(task.evaluator_env, {"MAGIC": "yes"})
            self.assertEqual(
                task.evaluator_path_prepend,
                ((root / "seed").resolve(),),
            )

    def test_rejects_invalid_direction(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "task.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "toy",
                        "goal": "win",
                        "seed_workspace": ".",
                        "submission_relpath": "answer",
                        "evaluator_argv": ["true"],
                        "objective_key": "score",
                        "direction": "sideways",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                TaskSpec.load(path)

    def test_rejects_invalid_editable_region(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root / "task.json"
            base = {
                "name": "toy",
                "goal": "win",
                "seed_workspace": ".",
                "submission_relpath": "answer",
                "evaluator_argv": ["true"],
                "objective_key": "score",
                "direction": "maximize",
            }
            for editable_region in (
                {"start_marker": "# START"},
                {"start_marker": "# SAME", "end_marker": "# SAME"},
                {"start_marker": "# START\nBAD", "end_marker": "# END"},
            ):
                path.write_text(
                    json.dumps(
                        {
                            **base,
                            "editable_region": editable_region,
                        }
                    ),
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    TaskSpec.load(path)

    def test_task_bundle_rejects_unknown_command_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "answer").write_text("ok", encoding="utf-8")
            path = root / "task.json"
            path.write_text(
                json.dumps(
                    {
                        "name": "toy",
                        "goal": "win",
                        "seed_workspace": "seed",
                        "submission_relpath": "answer",
                        "evaluator_argv": ["true", "{private_future}"],
                        "objective_key": "score",
                        "direction": "maximize",
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "unknown placeholders"):
                TaskBundle.load(path)

    def test_runtime_state_requires_valid_paired_environment_names(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "answer").write_text("ok", encoding="utf-8")
            path = root / "task.json"
            base = {
                "name": "toy",
                "goal": "win",
                "seed_workspace": "seed",
                "submission_relpath": "answer",
                "evaluator_argv": ["true"],
                "objective_key": "score",
                "direction": "maximize",
            }
            path.write_text(
                json.dumps(
                    {
                        **base,
                        "runtime_state": {"capture_env": "CAPTURE_ONLY"},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "declared together"):
                TaskSpec.load(path)

            path.write_text(
                json.dumps(
                    {
                        **base,
                        "runtime_state": {
                            "capture_env": "BAD-NAME",
                            "input_env": "INPUT_STATE",
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "valid environment name"):
                TaskBundle.load(path)

    def test_rejects_invalid_evaluator_environment(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            (seed / "answer").write_text("ok", encoding="utf-8")
            path = root / "task.json"
            base = {
                "name": "toy",
                "goal": "win",
                "seed_workspace": "seed",
                "submission_relpath": "answer",
                "evaluator_argv": ["true"],
                "objective_key": "score",
                "direction": "maximize",
            }
            for extra, message in (
                ({"evaluator_env": {"BAD-NAME": "x"}}, "valid environment name"),
                ({"evaluator_env": {"GOOD": "bad\x00value"}}, "null byte"),
                (
                    {"evaluator_path_prepend": ["missing-bin"]},
                    "directory does not exist",
                ),
            ):
                path.write_text(
                    json.dumps({**base, **extra}),
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, message):
                    TaskBundle.load(path)


if __name__ == "__main__":
    unittest.main()
