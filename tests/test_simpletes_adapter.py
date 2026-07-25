from __future__ import annotations

import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from benchmarks.tasks.simpletes.bootstrap import (
    DEFAULT_QUBIT_ROUTING_TOTAL_BUDGET_SECONDS,
    _hyra_final_seed,
    _instruction_for_contract,
    _predecessor_seed,
)
from benchmarks.tasks.simpletes.evaluate import (
    _load,
    _prepare_candidate_environment,
    _public_compatible_evaluate,
    _run_candidate,
)

HAS_NUMPY = importlib.util.find_spec("numpy") is not None


class ExactSumsVerifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        compiler = shutil.which("clang++") or shutil.which("g++")
        if compiler is None:
            raise unittest.SkipTest("a C++17 compiler is not installed")
        cls._temporary_directory = tempfile.TemporaryDirectory()
        cls._executable = Path(cls._temporary_directory.name) / "verify"
        source = (
            Path(__file__).parents[1]
            / "benchmarks"
            / "tasks"
            / "simpletes"
            / "verify_sums_exact.cpp"
        )
        subprocess.run(
            [
                compiler,
                "-O2",
                "-std=c++17",
                str(source),
                "-o",
                str(cls._executable),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        temporary_directory = getattr(cls, "_temporary_directory", None)
        if temporary_directory is not None:
            temporary_directory.cleanup()

    def test_exact_ntt_support_matches_small_brute_force_result(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state.json"
            state.write_text(json.dumps({"A": [0, 1, 3]}), encoding="utf-8")
            completed = subprocess.run(
                [str(self._executable), str(state)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        result = json.loads(completed.stdout)
        self.assertEqual(result["sumset_size"], 6)
        self.assertEqual(result["diffset_size"], 7)
        self.assertAlmostEqual(
            result["c_a"],
            math.log(2) / math.log(7 / 3),
        )

    def test_exact_ntt_support_rejects_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            state = Path(raw) / "state.json"
            state.write_text(json.dumps([0, 1, 1]), encoding="utf-8")
            completed = subprocess.run(
                [str(self._executable), str(state)],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("duplicate", completed.stderr)


class SimpleTESAdapterTests(unittest.TestCase):
    def test_qubit_default_budget_matches_upstream(self) -> None:
        self.assertEqual(DEFAULT_QUBIT_ROUTING_TOTAL_BUDGET_SECONDS, 1200)

    def test_evaluator_import_does_not_load_full_package_init(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / "simpletes"
            package.mkdir()
            (package / "__init__.py").write_text(
                "raise RuntimeError('full package imported')\n",
                encoding="utf-8",
            )
            (package / "construction.py").write_text(
                "VALUE = 137\n",
                encoding="utf-8",
            )
            evaluator = root / "evaluator.py"
            evaluator.write_text(
                "from simpletes.construction import VALUE\n"
                "def evaluate(_path):\n"
                "    return {'qualified': True, 'score': VALUE}\n",
                encoding="utf-8",
            )

            previous = {
                name: module
                for name, module in sys.modules.items()
                if name == "simpletes" or name.startswith("simpletes.")
            }
            try:
                for name in list(sys.modules):
                    if name == "simpletes" or name.startswith("simpletes."):
                        del sys.modules[name]
                module = _load(evaluator, root)
                self.assertEqual(module.evaluate("unused")["score"], 137)
            finally:
                for name in list(sys.modules):
                    if name == "simpletes" or name.startswith("simpletes."):
                        del sys.modules[name]
                sys.modules.update(previous)

    def test_candidate_subprocess_loads_task_sitecustomize(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "sitecustomize.py").write_text(
                "import builtins\nbuiltins.RUNTIME_STATE_READY = 137\n",
                encoding="utf-8",
            )
            previous = os.environ.get("PYTHONPATH")
            try:
                _prepare_candidate_environment(root)
                completed = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        (
                            "import builtins; "
                            "print(getattr(builtins, 'RUNTIME_STATE_READY', -1))"
                        ),
                    ],
                    check=True,
                    stdout=subprocess.PIPE,
                    text=True,
                    env=os.environ.copy(),
                )
                self.assertEqual(completed.stdout.strip(), "137")
            finally:
                if previous is None:
                    os.environ.pop("PYTHONPATH", None)
                else:
                    os.environ["PYTHONPATH"] = previous

    def test_candidate_wrapper_injects_shared_construction_without_upstream_imports(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state = root / "state.json"
            state.write_text(
                json.dumps([3, 7, 11, 19]),
                encoding="utf-8",
            )
            candidate = root / "candidate.py"
            candidate.write_text(
                "def run_code():\n"
                "    return GLOBAL_BEST_CONSTRUCTION, None\n",
                encoding="utf-8",
            )
            previous_state = os.environ.get(
                "SIMPLETES_SHARED_CONSTRUCTION_PATH"
            )
            previous_pythonpath = os.environ.pop("PYTHONPATH", None)
            os.environ["SIMPLETES_SHARED_CONSTRUCTION_PATH"] = str(state)
            try:
                self.assertEqual(
                    _run_candidate(candidate, timeout_seconds=10),
                    ([3, 7, 11, 19], None),
                )
            finally:
                if previous_state is None:
                    os.environ.pop(
                        "SIMPLETES_SHARED_CONSTRUCTION_PATH",
                        None,
                    )
                else:
                    os.environ[
                        "SIMPLETES_SHARED_CONSTRUCTION_PATH"
                    ] = previous_state
                if previous_pythonpath is not None:
                    os.environ["PYTHONPATH"] = previous_pythonpath


class PublicCompatibleMetricTests(unittest.TestCase):
    def test_sums_public_contract_removes_upstream_cardinality_cap(self) -> None:
        upstream = "Output requirements: 2 <= |A| <= 512."
        public = _instruction_for_contract(
            "sums_diffs",
            upstream,
            "public-compatible",
        )
        self.assertNotIn("<= 512", public)
        self.assertIn("no\n   additional cardinality cap", public)
        self.assertEqual(
            _instruction_for_contract("sums_diffs", upstream, "upstream"),
            upstream,
        )

    def test_fft_metrics_accept_large_contract_shapes(self) -> None:
        try:
            import scipy  # noqa: F401
        except ImportError:
            self.skipTest("science extras are not installed")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            ac2 = root / "ac2.py"
            ac2.write_text(
                "def run_code():\n"
                "    return [0.0, 1.0, 2.0, 1.0, 0.0], None\n",
                encoding="utf-8",
            )
            ac2_result = _public_compatible_evaluate(
                "autocorrelation_second",
                ac2,
            )
            self.assertTrue(ac2_result["qualified"])
            self.assertGreater(ac2_result["r"], 0)
            self.assertEqual(
                ac2_result["upstream_metrics"]["metric_mode"],
                "public-compatible-fft",
            )

            sums = root / "sums.py"
            sums.write_text(
                "def run_code():\n"
                "    return list(range(0, 2048, 3)), None\n",
                encoding="utf-8",
            )
            sums_result = _public_compatible_evaluate("sums_diffs", sums)
            self.assertTrue(sums_result["qualified"])
            self.assertEqual(
                sums_result["upstream_metrics"]["set_size"],
                683,
            )
            self.assertGreater(sums_result["c_a"], 0)


@unittest.skipUnless(HAS_NUMPY, "requires the science extra")
class PredecessorSeedTests(unittest.TestCase):
    def test_seed_loads_legacy_construction_without_running_optimizer(self) -> None:
        init = """\
# EVOLVE-BLOCK-START
def construct_function():
    return [0.0]
# EVOLVE-BLOCK-END

def run_code():
    return construct_function(), 0.0
"""
        generated = _predecessor_seed("autocorrelation_second", init)
        self.assertIn("def _load_predecessor()", generated)
        self.assertIn("return construct_function(), None", generated)
        self.assertNotIn("return [0.0]", generated)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            solution = root / "solution.py"
            solution.write_text(generated, encoding="utf-8")
            (root / "predecessor_construction.json").write_text(
                json.dumps(
                    {
                        "score": 0.9,
                        "construction": [0.0, 1.0, 0.0],
                    }
                ),
                encoding="utf-8",
            )
            namespace: dict[str, object] = {"__file__": str(solution)}
            exec(compile(generated, str(solution), "exec"), namespace)
            output = namespace["run_code"]()  # type: ignore[operator]
            self.assertEqual(output, ([0.0, 1.0, 0.0], None))

    def test_seed_loads_ttt_sequence_wrapper(self) -> None:
        generated = _predecessor_seed("autocorrelation_first", "")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            solution = root / "solution.py"
            solution.write_text(generated, encoding="utf-8")
            (root / "predecessor_construction.json").write_text(
                json.dumps({"sequence": [0.25, 0.5, 0.25]}),
                encoding="utf-8",
            )
            namespace: dict[str, object] = {"__file__": str(solution)}
            exec(compile(generated, str(solution), "exec"), namespace)
            output = namespace["run_code"]()  # type: ignore[operator]
            self.assertEqual(output, ([0.25, 0.5, 0.25], None))


@unittest.skipUnless(HAS_NUMPY, "requires the science extra")
class HyraFinalSeedTests(unittest.TestCase):
    def _run_seed(
        self,
        task_name: str,
        artifact: dict[str, object],
        init: str = "",
    ) -> object:
        generated = _hyra_final_seed(task_name, init)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            solution = root / "solution.py"
            solution.write_text(generated, encoding="utf-8")
            (root / "hyra_final_artifact.json").write_text(
                json.dumps(artifact),
                encoding="utf-8",
            )
            namespace: dict[str, object] = {"__file__": str(solution)}
            exec(compile(generated, str(solution), "exec"), namespace)
            return namespace["run_code"]()  # type: ignore[operator]

    def test_python_artifact_seeds_load_without_target_scores(self) -> None:
        self.assertEqual(
            self._run_seed(
                "autocorrelation_first",
                {"values": [0.25, 0.5, 0.25]},
            ),
            ([0.25, 0.5, 0.25], None),
        )
        self.assertEqual(
            self._run_seed(
                "autocorrelation_second",
                {"values": [0.0, 1.0, 0.0]},
            ),
            ([0.0, 1.0, 0.0], None),
        )
        self.assertEqual(
            self._run_seed(
                "sums_diffs",
                {"A": [3, 1, 3, 2]},
            ),
            ([1, 2, 3], None),
        )

    def test_erdos_artifact_seed_preserves_fixed_postprocessing(self) -> None:
        init = """\
# EVOLVE-BLOCK-START
def construct_h():
    return [0.5, 0.5], 2
# EVOLVE-BLOCK-END

def run_code():
    h_values, n_points = construct_h()
    return h_values, None, n_points
"""
        values, score, n_points = self._run_seed(
            "erdos_min_overlap",
            {"h_values": [0.25, 0.75], "n_points": 2},
            init,
        )
        self.assertEqual(values.tolist(), [0.25, 0.75])
        self.assertIsNone(score)
        self.assertEqual(n_points, 2)


if __name__ == "__main__":
    unittest.main()
