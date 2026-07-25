from __future__ import annotations

import unittest
from pathlib import Path

from rehyra.prompts import evaluator_architect_prompt


class EvaluatorPromptTests(unittest.TestCase):
    def test_architect_receives_exact_frozen_output_contract(self) -> None:
        prompt = evaluator_architect_prompt(
            goal="maximize a toy score",
            bank_summary={
                "objective_key": "utility",
                "direction": "minimize",
                "qualification_key": "accepted",
            },
            evaluator_relpath=Path("evaluator.py"),
            initial=True,
        )

        self.assertIn("'accepted'", prompt)
        self.assertIn("'utility'", prompt)
        self.assertIn("'minimize'", prompt)


if __name__ == "__main__":
    unittest.main()
