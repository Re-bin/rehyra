from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

import rehyra
from rehyra.util import write_evidence_manifest


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "codex-plugin" / "rehyra_plugin.py"


class PluginBundleTests(unittest.TestCase):
    def run_bridge(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(BRIDGE), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_manifest_and_marketplace_expose_rehyra(self) -> None:
        manifest = json.loads(
            (ROOT / ".codex-plugin" / "plugin.json").read_text()
        )
        marketplace = json.loads(
            (
                ROOT
                / ".agents"
                / "plugins"
                / "marketplace.json"
            ).read_text()
        )
        with (ROOT / "pyproject.toml").open("rb") as handle:
            project = tomllib.load(handle)["project"]

        self.assertEqual(manifest["name"], "rehyra")
        self.assertEqual(manifest["version"], project["version"])
        self.assertEqual(rehyra.__version__, project["version"])
        self.assertEqual(manifest["skills"], "./codex-plugin/skills/")
        skill = ROOT / manifest["skills"] / "rehyra" / "SKILL.md"
        self.assertTrue(skill.is_file())
        self.assertTrue(BRIDGE.is_file())
        self.assertFalse((ROOT / "scripts" / "rehyra_plugin.py").exists())
        self.assertFalse((ROOT / "skills" / "rehyra" / "SKILL.md").exists())
        self.assertEqual(marketplace["name"], "rehyra")
        self.assertEqual(
            marketplace["plugins"][0]["source"]["path"],
            "./",
        )

    def test_prepare_freezes_workspace_outside_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "solution.py").write_text("x = 1\n")
            (workspace / "evaluate.py").write_text(
                "print('{\"qualified\": true, \"score\": 1}')\n"
            )
            ignored = workspace / ".rehyra"
            ignored.mkdir()
            (ignored / "private.txt").write_text("do not copy")
            output = root / "experiment"

            result = self.run_bridge(
                "prepare",
                "--workspace",
                str(workspace),
                "--output",
                str(output),
                "--name",
                "Speed Trial",
                "--goal",
                "maximize score while preserving behavior",
                "--submission",
                "solution.py",
                "--objective-key",
                "score",
                "--direction",
                "maximize",
                "--evaluator-program",
                sys.executable,
                "--evaluator-arg",
                "{workspace}/evaluate.py",
                "--evaluator-arg",
                "{submission}",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertTrue((output / "seed" / "solution.py").is_file())
            self.assertTrue((output / "seed" / "evaluate.py").is_file())
            self.assertFalse((output / "seed" / ".rehyra").exists())
            task = json.loads((output / "task.json").read_text())
            self.assertEqual(task["name"], "speed-trial")
            self.assertEqual(task["seed_workspace"], "seed")
            self.assertEqual(task["submission_relpath"], "solution.py")
            self.assertEqual(task["mutation_policy"], "submission_only")
            self.assertIn("{workspace}/evaluate.py", task["evaluator_argv"])
            with (output / "run.toml").open("rb") as handle:
                config = tomllib.load(handle)
            self.assertEqual(config["run"]["max_proposals"], 4)
            self.assertEqual(config["agent"]["backend"], "codex")

            described = self.run_bridge(
                "cli",
                "describe-task",
                str(output / "task.json"),
            )
            self.assertEqual(described.returncode, 0, described.stderr)
            self.assertEqual(
                json.loads(described.stdout)["name"],
                "speed-trial",
            )

    def test_prepare_rejects_output_inside_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            (workspace / "solution.py").write_text("x = 1\n")
            result = self.run_bridge(
                "prepare",
                "--workspace",
                str(workspace),
                "--output",
                str(workspace / ".rehyra" / "experiment"),
                "--name",
                "unsafe",
                "--goal",
                "maximize score",
                "--submission",
                "solution.py",
                "--objective-key",
                "score",
                "--direction",
                "maximize",
                "--evaluator-program",
                sys.executable,
                "--evaluator-arg",
                "{submission}",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "outside the source workspace",
                json.loads(result.stderr)["error"],
            )

    def test_plugin_campaign_cannot_promote_rewritten_evaluator(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            workspace = root / "project"
            workspace.mkdir()
            (workspace / "solution.json").write_text(
                '{"score": 10}\n',
                encoding="utf-8",
            )
            evaluator_source = (
                "import json\n"
                "from pathlib import Path\n"
                "score = json.loads(Path('solution.json').read_text())['score']\n"
                "print(json.dumps({'qualified': True, 'score': score}))\n"
            )
            (workspace / "evaluate.py").write_text(
                evaluator_source,
                encoding="utf-8",
            )
            fake_agent = root / "fake_agent.py"
            fake_agent.write_text(
                "import json, os\n"
                "from pathlib import Path\n"
                "role = os.environ['HYRA_AGENT_ROLE']\n"
                "output = Path(os.environ['HYRA_AGENT_OUTPUT_PATH'])\n"
                "workspace = Path(os.environ['HYRA_AGENT_WORKSPACE'])\n"
                "if role == 'context':\n"
                "    payload = {'stop': False, 'stop_reason': '', 'inspirations': [{\n"
                "        'title': 'attempt evaluator rewrite',\n"
                "        'hypothesis': 'inflate score',\n"
                "        'instructions': 'rewrite evaluator and improve solution',\n"
                "        'risk': 'evaluator tampering',\n"
                "        'parent_attempt_id': 'attempt-0000',\n"
                "        'reference_attempt_ids': []}]}\n"
                "    output.write_text(json.dumps(payload))\n"
                "elif role == 'proposal':\n"
                "    (workspace / 'evaluate.py').write_text(\n"
                "        'print(\"{\\\\\"qualified\\\\\": true, \\\\\"score\\\\\": 999}\")\\n')\n"
                "    (workspace / 'solution.json').write_text('{\"score\": 9}\\n')\n"
                "    output.write_text('candidate complete')\n"
                "else:\n"
                "    raise RuntimeError(role)\n",
                encoding="utf-8",
            )
            experiment = root / "experiment"
            prepared = self.run_bridge(
                "prepare",
                "--workspace",
                str(workspace),
                "--output",
                str(experiment),
                "--name",
                "sealed plugin",
                "--goal",
                "minimize score",
                "--submission",
                "solution.json",
                "--objective-key",
                "score",
                "--direction",
                "minimize",
                "--evaluator-program",
                sys.executable,
                "--evaluator-arg",
                "{workspace}/evaluate.py",
                "--max-proposals",
                "1",
                "--max-concurrency",
                "1",
            )
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            run_toml = experiment / "run.toml"
            run_toml.write_text(
                run_toml.read_text(encoding="utf-8").replace(
                    'backend = "codex"\n',
                    'backend = "command"\n'
                    f"command_argv = [{json.dumps(sys.executable)}, "
                    f"{json.dumps(str(fake_agent))}]\n",
                ),
                encoding="utf-8",
            )
            run_dir = experiment / "run"

            completed = self.run_bridge(
                "cli",
                "run",
                str(run_toml),
                "--run-dir",
                str(run_dir),
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            report = json.loads(completed.stdout)
            self.assertEqual(report["incumbent_evaluation"]["score"], 9)
            winner = run_dir / "attempts" / "attempt-0001" / "workspace"
            self.assertEqual(
                (winner / "evaluate.py").read_text(encoding="utf-8"),
                evaluator_source,
            )

            validated = self.run_bridge("cli", "validate", str(run_dir))
            self.assertEqual(validated.returncode, 0, validated.stderr)
            validation = json.loads(validated.stdout)
            self.assertTrue(validation["ok"])
            self.assertRegex(
                validation["evidence_root_sha256"],
                r"^[0-9a-f]{64}$",
            )
            best = self.run_bridge("best", str(run_dir))
            self.assertEqual(best.returncode, 0, best.stderr)
            self.assertEqual(json.loads(best.stdout)["evaluation"]["score"], 9)

    def test_best_resolves_frozen_incumbent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            run = Path(raw)
            workspace = (
                run / "attempts" / "attempt-0002" / "workspace"
            )
            workspace.mkdir(parents=True)
            submission = workspace / "solution.py"
            submission.write_text("x = 2\n")
            (run / "report.json").write_text(
                json.dumps(
                    {
                        "incumbent_attempt_id": "attempt-0002",
                        "incumbent_evaluation": {
                            "qualified": True,
                            "score": 2,
                        },
                        "proposal_count": 2,
                        "terminal_reason": "proposal_budget_exhausted",
                    }
                )
            )
            (run / "task.frozen.json").write_text(
                json.dumps({"submission_relpath": "solution.py"})
            )
            write_evidence_manifest(run)

            result = self.run_bridge("best", str(run))

            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["attempt_id"], "attempt-0002")
            self.assertEqual(payload["submission"], str(submission.resolve()))
            self.assertRegex(payload["evidence_root_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
