from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from benchmarks.tasks.sunspot.bootstrap import HYRA_RESULTS_COMMIT, bootstrap


class SunspotBootstrapTests(unittest.TestCase):
    def test_hyra_final_seed_is_copied_and_labeled(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            hyra = root / "hyra-results"
            source = hyra / "AI4Science" / "sunspot_symbolic" / "solution"
            source.mkdir(parents=True)
            (source / "train.csv").write_text(
                "time,ssn\n1932.0,1.0\n",
                encoding="utf-8",
            )
            (source / "srfit.py").write_text(
                "def placeholder():\n    return None\n",
                encoding="utf-8",
            )
            artifact = source.parent / "solution.json"
            payload = {
                "expression": "a+b*x1",
                "params": {"a": 1.0, "b": 0.5},
            }
            artifact.write_text(
                json.dumps(payload),
                encoding="utf-8",
            )
            data = root / "data.csv"
            data.write_text("time,ssn\n1932.0,1.0\n", encoding="utf-8")
            output = root / "task"

            with patch(
                "benchmarks.tasks.sunspot.bootstrap.subprocess.check_output",
                return_value=HYRA_RESULTS_COMMIT + "\n",
            ):
                bootstrap(
                    output,
                    hyra_root=hyra,
                    data_path=data,
                    evaluator_python=sys.executable,
                    seed_source="hyra-final",
                )

            self.assertEqual(
                json.loads((output / "seed" / "solution.json").read_text()),
                payload,
            )
            task = json.loads((output / "task.json").read_text())
            self.assertEqual(
                task["provenance"]["seed_source"],
                "hyra-final",
            )
            self.assertIn("not autonomous rediscovery", task["goal"])


if __name__ == "__main__":
    unittest.main()
