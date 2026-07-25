from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class ArchitectureBoundaryTests(unittest.TestCase):
    def test_benchmarks_do_not_import_agent_or_scheduler_implementation(self) -> None:
        forbidden = {
            "rehyra.providers",
            "rehyra.agents",
            "rehyra.loop",
            "rehyra.memory",
            "rehyra.evaluator_evolution",
        }
        violations: list[str] = []
        for path in sorted((ROOT / "benchmarks" / "tasks").rglob("*.py")):
            overlap = imported_modules(path) & forbidden
            if overlap:
                violations.append(
                    f"{path.relative_to(ROOT)}: {', '.join(sorted(overlap))}"
                )
        self.assertEqual(violations, [])

    def test_core_does_not_import_benchmark_packages(self) -> None:
        violations: list[str] = []
        for path in sorted((ROOT / "rehyra").glob("*.py")):
            if path.name == "cli.py":
                continue
            overlap = {
                name
                for name in imported_modules(path)
                if name == "benchmarks" or name.startswith("benchmarks.")
            }
            if overlap:
                violations.append(
                    f"{path.relative_to(ROOT)}: {', '.join(sorted(overlap))}"
                )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
