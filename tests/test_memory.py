from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from rehyra.contracts import TaskSpec
from rehyra.memory import ExperienceBank, agent_visible_evaluation
from rehyra.util import workspace_manifest


class AgentVisibleEvaluationTests(unittest.TestCase):
    def test_redacts_execution_details_and_absolute_paths(self) -> None:
        visible = agent_visible_evaluation(
            {
                "qualified": False,
                "score": -1.0,
                "evaluator_argv": ["python", "/private/future/evaluator.py"],
                "isolation": {"backend": "local"},
                "error": "failed while reading /private/future/data.csv",
                "metrics": {"validation_score": 0.5},
            }
        )

        self.assertEqual(visible["score"], -1.0)
        self.assertNotIn("evaluator_argv", visible)
        self.assertNotIn("isolation", visible)
        self.assertEqual(
            visible["error"],
            "failed while reading <redacted-path>",
        )
        self.assertEqual(visible["metrics"]["validation_score"], 0.5)
        markdown = agent_visible_evaluation(
            {"diagnostic": "edited [x](/private/run/candidate.py)"}
        )
        self.assertEqual(
            markdown["diagnostic"],
            "edited [x](<redacted-path>)",
        )

    def test_experience_bank_exposes_safe_logs_and_solution_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            seed = root / "seed"
            seed.mkdir()
            task = TaskSpec(
                name="toy",
                goal="improve",
                seed_workspace=seed,
                submission_relpath=Path("solution.py"),
                evaluator_argv=("true",),
                objective_key="score",
                direction="maximize",
            )
            bank = ExperienceBank(root / "run", task)
            attempt_dir = bank.attempt_dir("attempt-0000")
            workspace = attempt_dir / "workspace"
            workspace.mkdir(parents=True)
            (workspace / "solution.py").write_text("VALUE = 1\n")
            (workspace / "helper.txt").write_text("artifact\n")
            evaluation_dir = attempt_dir / "evaluation"
            evaluation_dir.mkdir()
            (evaluation_dir / "stderr.log").write_text(
                "failed at /private/evaluator.py\n",
                encoding="utf-8",
            )
            bank.write_attempt(
                "attempt-0000",
                {
                    "status": "completed",
                    "parent_attempt_id": None,
                    "evaluation": {"qualified": True, "score": 1},
                    "workspace_manifest": workspace_manifest(workspace),
                },
            )

            summary = bank.summary()
            self.assertEqual(
                summary["attempts"][0]["logs"]["evaluator_stderr"],
                "failed at <redacted-path>\n",
            )
            destination = root / "context" / "source_snapshots"
            snapshots = bank.materialize_context_sources(destination)
            self.assertIn(
                "source_snapshots/attempt-0000/solution.py",
                snapshots["attempt-0000"],
            )
            self.assertIn(
                "source_snapshots/attempt-0000/helper.txt",
                snapshots["attempt-0000"],
            )
            self.assertEqual(
                json.loads(
                    json.dumps(summary["attempts"][0]["evaluation"])
                )["score"],
                1,
            )


if __name__ == "__main__":
    unittest.main()
