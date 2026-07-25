from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from benchmarks.tasks.adderboard.bootstrap import load_hyra_final_submission
from benchmarks.tasks.adderboard.evaluate import (
    DirectInputAdditionVisitor,
    suspicious_fixed_buffers,
)


class AdderboardEvaluatorTests(unittest.TestCase):
    def test_loads_hyra_final_submission_from_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "solution.json"
            artifact.write_text(
                json.dumps({"submission_py": "def build_model():\n    pass\n"}),
                encoding="utf-8",
            )
            self.assertEqual(
                load_hyra_final_submission(artifact),
                "def build_model():\n    pass\n",
            )

    def test_rejects_missing_hyra_final_submission(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            artifact = Path(raw) / "solution.json"
            artifact.write_text("{}", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_hyra_final_submission(artifact)

    def test_detects_direct_input_addition(self) -> None:
        tree = ast.parse("def add(model, a, b):\n    return a + b\n")
        visitor = DirectInputAdditionVisitor()
        visitor.visit(tree)
        self.assertTrue(visitor.found)

    def test_allows_model_decode(self) -> None:
        tree = ast.parse(
            "def add(model, a, b):\n"
            "    encoded = encode(a, b)\n"
            "    return decode(model(encoded))\n"
        )
        visitor = DirectInputAdditionVisitor()
        visitor.visit(tree)
        self.assertFalse(visitor.found)

    def test_flags_nonstandard_floating_buffer(self) -> None:
        class FakeValues(list):
            def __getitem__(self, key):
                value = super().__getitem__(key)
                return FakeValues(value) if isinstance(value, list) else value

        class FakeTensor:
            def is_floating_point(self) -> bool:
                return True

            def detach(self):
                return self

            def cpu(self):
                return self

            def flatten(self):
                return FakeValues([1.25])

            def numel(self) -> int:
                return 1

        class FakeModel:
            def named_buffers(self):
                return [
                    ("block.attn.rope_cos", FakeTensor()),
                    ("fixed_geometry", FakeTensor()),
                ]

        self.assertEqual(
            suspicious_fixed_buffers(FakeModel()),
            [
                {
                    "name": "fixed_geometry",
                    "numel": 1,
                    "values_preview": [1.25],
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
