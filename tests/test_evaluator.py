from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from rehyra.evaluator import evaluate
from rehyra.contracts import TaskSpec


STATE_EVALUATOR = """\
import json
import os
from pathlib import Path

candidate = json.loads(Path("candidate.json").read_text())
input_path = os.environ.get("INPUT_STATE")
seen = None
if input_path:
    seen = json.loads(Path(input_path).read_text())["best"]
Path(os.environ["CAPTURE_STATE"]).write_text(
    json.dumps({"best": candidate["score"]})
)
print(json.dumps({
    "qualified": True,
    "score": candidate["score"],
    "seen_parent_state": seen,
}))
"""


class EvaluatorRuntimeStateTests(unittest.TestCase):
    def test_injects_task_declared_evaluator_environment(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.json").write_text(
                '{"score": 1}',
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            helper = bin_dir / "task-helper"
            helper.write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            helper.chmod(0o755)
            evaluator = root / "evaluator.py"
            evaluator.write_text(
                "import json\n"
                "import os\n"
                "import shutil\n"
                "print(json.dumps({"
                "'qualified': True, "
                "'score': 1, "
                "'magic': os.environ.get('TASK_MAGIC'), "
                "'helper': shutil.which('task-helper')"
                "}))\n",
                encoding="utf-8",
            )
            task = TaskSpec(
                name="declared-environment",
                goal="minimize score",
                seed_workspace=workspace,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
                evaluator_env={"TASK_MAGIC": "present"},
                evaluator_path_prepend=(bin_dir,),
            )

            result = asyncio.run(
                evaluate(
                    task,
                    workspace=workspace,
                    run_dir=root / "run",
                    output_dir=root / "run" / "evaluation",
                )
            )

            self.assertTrue(result["qualified"])
            self.assertEqual(result["magic"], "present")
            self.assertEqual(result["helper"], str(helper))

    def test_workspace_symlink_fails_before_evaluator_execution(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.json").write_text(
                '{"score": 1}',
                encoding="utf-8",
            )
            (workspace / "outside.txt").symlink_to(root / "private.txt")
            marker = root / "evaluator-ran"
            evaluator = root / "evaluator.py"
            evaluator.write_text(
                "from pathlib import Path\n"
                f"Path({str(marker)!r}).write_text('yes')\n"
                "print('{\"qualified\": true, \"score\": 1}')\n",
                encoding="utf-8",
            )
            task = TaskSpec(
                name="unsafe",
                goal="minimize score",
                seed_workspace=workspace,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
            )

            result = asyncio.run(
                evaluate(
                    task,
                    workspace=workspace,
                    run_dir=root / "run",
                    output_dir=root / "run" / "evaluation",
                )
            )

            self.assertFalse(result["qualified"])
            self.assertIn("symbolic links are forbidden", result["error"])
            self.assertFalse(marker.exists())

    def test_captures_and_injects_opaque_runtime_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / "run"
            evaluator = root / "evaluator.py"
            evaluator.write_text(STATE_EVALUATOR, encoding="utf-8")
            first_workspace = root / "first"
            first_workspace.mkdir()
            (first_workspace / "candidate.json").write_text(
                '{"score": 10}',
                encoding="utf-8",
            )
            task = TaskSpec(
                name="stateful",
                goal="minimize score",
                seed_workspace=first_workspace,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
                state_capture_env="CAPTURE_STATE",
                state_input_env="INPUT_STATE",
                state_description="validated best score",
            )

            first = asyncio.run(
                evaluate(
                    task,
                    workspace=first_workspace,
                    run_dir=run_dir,
                    output_dir=run_dir / "attempts" / "attempt-0000" / "evaluation",
                )
            )
            self.assertTrue(first["qualified"])
            self.assertIsNone(first["seen_parent_state"])
            first_state = run_dir / first["captured_state"]["path"]
            self.assertTrue(first_state.is_file())

            second_workspace = root / "second"
            second_workspace.mkdir()
            (second_workspace / "candidate.json").write_text(
                '{"score": 8}',
                encoding="utf-8",
            )
            second = asyncio.run(
                evaluate(
                    task,
                    workspace=second_workspace,
                    run_dir=run_dir,
                    output_dir=run_dir / "attempts" / "attempt-0001" / "evaluation",
                    input_state_path=first_state,
                )
            )
            self.assertTrue(second["qualified"])
            self.assertEqual(second["seen_parent_state"], 10)
            second_state = run_dir / second["captured_state"]["path"]
            self.assertEqual(
                json.loads(second_state.read_text(encoding="utf-8")),
                {"best": 8},
            )

    def test_missing_capture_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / "run"
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.json").write_text(
                '{"score": 1}',
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(
                'print(\'{"qualified": true, "score": 1}\')\n',
                encoding="utf-8",
            )
            task = TaskSpec(
                name="stateful",
                goal="minimize score",
                seed_workspace=workspace,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
                state_capture_env="CAPTURE_STATE",
                state_input_env="INPUT_STATE",
            )
            result = asyncio.run(
                evaluate(
                    task,
                    workspace=workspace,
                    run_dir=run_dir,
                    output_dir=run_dir / "evaluation",
                )
            )
            self.assertFalse(result["qualified"])
            self.assertIn("did not capture", result["error"])

    def test_retry_removes_stale_state_and_ignores_spoofed_descriptor(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            run_dir = root / "run"
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.json").write_text(
                '{"score": 1}',
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(STATE_EVALUATOR, encoding="utf-8")
            task = TaskSpec(
                name="stateful",
                goal="minimize score",
                seed_workspace=workspace,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(sys.executable, str(evaluator)),
                objective_key="score",
                direction="minimize",
                state_capture_env="CAPTURE_STATE",
                state_input_env="INPUT_STATE",
            )
            output_dir = run_dir / "evaluation"
            first = asyncio.run(
                evaluate(
                    task,
                    workspace=workspace,
                    run_dir=run_dir,
                    output_dir=output_dir,
                )
            )
            self.assertIn("captured_state", first)

            evaluator.write_text(
                "import json\n"
                "print(json.dumps({"
                "'qualified': True, 'score': 1, "
                "'captured_state': {'path': '../../spoof'}}))\n",
                encoding="utf-8",
            )
            retried = asyncio.run(
                evaluate(
                    task,
                    workspace=workspace,
                    run_dir=run_dir,
                    output_dir=output_dir,
                )
            )
            self.assertFalse(retried["qualified"])
            self.assertNotIn("captured_state", retried)
            self.assertIn("did not capture", retried["error"])

    def test_rejects_non_finite_or_non_numeric_qualified_objective(self) -> None:
        payloads = (
            '{"qualified": true, "score": Infinity}',
            '{"qualified": true, "score": "999"}',
            '{"qualified": true}',
        )
        for payload in payloads:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                workspace = root / "workspace"
                workspace.mkdir()
                (workspace / "candidate.json").write_text("{}", encoding="utf-8")
                task = TaskSpec(
                    name="strict-result",
                    goal="maximize score",
                    seed_workspace=workspace,
                    submission_relpath=Path("candidate.json"),
                    evaluator_argv=(
                        sys.executable,
                        "-c",
                        f"print({payload!r})",
                    ),
                    objective_key="score",
                    direction="maximize",
                )

                result = asyncio.run(
                    evaluate(
                        task,
                        workspace=workspace,
                        run_dir=root / "run",
                        output_dir=root / "run" / "evaluation",
                    )
                )

                self.assertFalse(result["qualified"])
                self.assertIn("finite numeric", result["error"])

    def test_error_field_cannot_remain_qualified(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "candidate.json").write_text("{}", encoding="utf-8")
            task = TaskSpec(
                name="strict-error",
                goal="maximize score",
                seed_workspace=workspace,
                submission_relpath=Path("candidate.json"),
                evaluator_argv=(
                    sys.executable,
                    "-c",
                    "print('{\"qualified\": true, \"score\": 99, \"error\": \"bad\"}')",
                ),
                objective_key="score",
                direction="maximize",
            )

            result = asyncio.run(
                evaluate(
                    task,
                    workspace=workspace,
                    run_dir=root / "run",
                    output_dir=root / "run" / "evaluation",
                )
            )

            self.assertFalse(result["qualified"])
            self.assertEqual(result["error"], "bad")


if __name__ == "__main__":
    unittest.main()
