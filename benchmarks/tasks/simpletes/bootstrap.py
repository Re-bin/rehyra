from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rehyra.task import write_task_spec
from rehyra.util import sha256_file


SIMPLETES_URL = "https://github.com/wq-will/SimpleTES.git"
SIMPLETES_COMMIT = "b7e0367b5ab19554fb40859e8069e091bb8c4ca2"
TTT_DISCOVER_URL = "https://github.com/test-time-training/discover.git"
TTT_DISCOVER_COMMIT = "6c40e82dab9d5de7416ac873ad5cd3106084aaed"
DEFAULT_QUBIT_ROUTING_TOTAL_BUDGET_SECONDS = 1200
HYRA_RESULTS_URL = "https://github.com/Tencent-Hunyuan/hyra-results.git"
HYRA_RESULTS_COMMIT = "bcda2e29200fbeaeb1620acca0670cc445e8688f"
HYRA_FINAL_ARTIFACTS = {
    "autocorrelation_first": "AI4Science/autocorrelation_first/solution.json",
    "autocorrelation_second": "AI4Science/autocorrelation_second/solution.json",
    "erdos_min_overlap": "AI4Science/erdos_min_overlap/solution.json",
    "sums_diffs": "AI4Science/sums_diffs/solution.json",
}

TASKS = {
    "autocorrelation_first": {
        "source": "datasets/autocorrelation/autocorrelation_first/init_program.py",
        "predecessor_source": None,
        "predecessor_construction_repository": "ttt-discover",
        "predecessor_construction": "results/mathematics/ttt_ac1_sequence.json",
        "instruction": "datasets/autocorrelation/autocorrelation_first/autocorrelation_first.txt",
        "submission": "solution.py",
        "objective": "c1",
        "direction": "minimize",
        "goal": "Minimize the first autocorrelation constant C1 with a valid executable construction.",
    },
    "autocorrelation_second": {
        "source": "datasets/autocorrelation/autocorrelation_second/init_program.py",
        "predecessor_source": (
            "best_results/mathematics_extremal_analysis/"
            "second_autocorrelation_inequality/"
            "second_autocorrelation_inequality_best.py"
        ),
        "predecessor_construction": (
            "best_results/mathematics_extremal_analysis/"
            "second_autocorrelation_inequality/"
            "second_autocorrelation_inequality_best_construction.json"
        ),
        "instruction": "datasets/autocorrelation/autocorrelation_second/autocorrelation_second.txt",
        "submission": "solution.py",
        "objective": "r",
        "direction": "maximize",
        "goal": "Maximize the second autocorrelation ratio R with a valid executable construction.",
    },
    "erdos_min_overlap": {
        "source": "datasets/erdos/erdos_min_overlap/init_program.py",
        "predecessor_source": (
            "best_results/mathematics_extremal_analysis/"
            "erdos_minimum_overlap/erdos_minimum_overlap_best.py"
        ),
        "predecessor_construction": (
            "best_results/mathematics_extremal_analysis/"
            "erdos_minimum_overlap/"
            "erdos_minimum_overlap_best_construction.json"
        ),
        "instruction": "datasets/erdos/erdos_min_overlap/erdos_min_overlap.txt",
        "submission": "solution.py",
        "objective": "c5",
        "direction": "minimize",
        "goal": "Minimize the Erdos overlap constant C5 with a valid executable construction.",
    },
    "sums_diffs": {
        "source": "datasets/sums_diffs/sums_diffs/init_program.py",
        "predecessor_source": (
            "best_results/combinatorial_construction/sum_difference_problem/"
            "sum_difference_problem_best.py"
        ),
        "predecessor_construction": (
            "best_results/combinatorial_construction/sum_difference_problem/"
            "sum_difference_problem_best_construction.json"
        ),
        "instruction": "datasets/sums_diffs/sums_diffs/sums_diffs.txt",
        "submission": "solution.py",
        "objective": "c_a",
        "direction": "maximize",
        "goal": "Maximize the sum-versus-difference exponent C(A) for a valid finite integer set.",
    },
    "qubit_routing": {
        "source": "datasets/qubit_routing/swap_reduction/init_program.rs",
        "predecessor_source": (
            "best_results/quantum_circuit_compilation/"
            "qubit_routing_on_superconducting_quantum_computer/"
            "qubit_routing_on_superconducting_quantum_computer_best.rs"
        ),
        "instruction": "datasets/qubit_routing/swap_reduction/swap_reduction.txt",
        "submission": "solution.rs",
        "objective": "cnot_added",
        "direction": "minimize",
        "goal": "Minimize added CNOT gates across the frozen qubit-routing suite with a valid general routing policy.",
    },
}

STATEFUL_TASKS = {
    "autocorrelation_first",
    "autocorrelation_second",
    "erdos_min_overlap",
    "sums_diffs",
}
PUBLIC_COMPATIBLE_TASKS = {
    "autocorrelation_first",
    "autocorrelation_second",
    "sums_diffs",
}
PUBLIC_SUMS_DIFFS_INSTRUCTION = """\
In additive combinatorics, for a finite set of integers A, define

- Sumset: A + A = {a + b : a, b in A}
- Difference set: A - A = {a - b : a, b in A}

Maximize

C(A) = log(|A + A| / |A|) / log(|A - A| / |A|).

Public-comparison output requirements:
1. A must be a finite set of distinct integers.
2. |A| must be at least 2. The mathematical comparison contract imposes no
   additional cardinality cap.
3. Every element must be in [-1_000_000, 1_000_000].

Program interface:
- Implement run_code().
- run_code() may return either (A, claimed_c) or A alone.

The evaluator deduplicates A and recomputes the metric with FFT support
counting. Reported values cannot be faked. Program execution is limited to
1100 seconds.

Do this by evolving the code between # EVOLVE-BLOCK-START and
# EVOLVE-BLOCK-END.
"""


_PREDECESSOR_LOADER = """\
import json
from pathlib import Path

import numpy as np


def _decode_predecessor(value):
    if isinstance(value, list):
        return [_decode_predecessor(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__simpletes_type__", value.get("__simpleevolve_type__"))
    if tag == "ndarray":
        return np.asarray(value["data"], dtype=value.get("dtype", "float64"))
    if tag == "tuple":
        return tuple(_decode_predecessor(item) for item in value["items"])
    if "construction" in value:
        return _decode_predecessor(value["construction"])
    if "sequence" in value and len(value) == 1:
        return _decode_predecessor(value["sequence"])
    return {key: _decode_predecessor(item) for key, item in value.items()}


def _load_predecessor():
    path = Path(__file__).with_name("predecessor_construction.json")
    return _decode_predecessor(json.loads(path.read_text(encoding="utf-8")))
"""

_HYRA_FINAL_LOADER = """\
import json
from pathlib import Path

import numpy as np


def _load_hyra_final():
    path = Path(__file__).with_name("hyra_final_artifact.json")
    return json.loads(path.read_text(encoding="utf-8"))
"""


def _replace_evolve_block(text: str, body: str, *, rust: bool) -> str:
    start = "// EVOLVE-BLOCK-START" if rust else "# EVOLVE-BLOCK-START"
    end = "// EVOLVE-BLOCK-END" if rust else "# EVOLVE-BLOCK-END"
    if text.count(start) != 1 or text.count(end) != 1:
        raise RuntimeError("upstream seed must contain one EVOLVE-BLOCK pair")
    prefix, remainder = text.split(start, 1)
    _, suffix = remainder.split(end, 1)
    return f"{prefix}{start}\n{body.rstrip()}\n{end}{suffix}"


def _predecessor_seed(task_name: str, init_text: str) -> str:
    if task_name == "autocorrelation_first":
        body = (
            _PREDECESSOR_LOADER
            + "\n\ndef propose_candidate(seed=42, budget_s=1000, **kwargs):\n"
            "    del seed, budget_s, kwargs\n"
            "    return np.asarray(_load_predecessor(), dtype=float).tolist()\n"
        )
        return (
            "# EVOLVE-BLOCK-START\n"
            + body.rstrip()
            + "\n# EVOLVE-BLOCK-END\n\n"
            "def run_code():\n"
            "    return propose_candidate(), None\n"
        )
    if task_name == "autocorrelation_second":
        body = (
            _PREDECESSOR_LOADER
            + "\n\ndef construct_function():\n"
            "    return np.asarray(_load_predecessor(), dtype=float).tolist()\n"
        )
        return (
            "# EVOLVE-BLOCK-START\n"
            + body.rstrip()
            + "\n# EVOLVE-BLOCK-END\n\n"
            "def run_code():\n"
            "    return construct_function(), None\n"
        )
    if task_name == "sums_diffs":
        body = (
            _PREDECESSOR_LOADER
            + "\n\ndef construct_set():\n"
            "    return sorted(set(int(item) for item in _load_predecessor()))\n"
        )
        return (
            "# EVOLVE-BLOCK-START\n"
            + body.rstrip()
            + "\n# EVOLVE-BLOCK-END\n\n"
            "def run_code():\n"
            "    return construct_set(), None\n"
        )
    if task_name == "erdos_min_overlap":
        body = (
            _PREDECESSOR_LOADER
            + "\n\ndef construct_h():\n"
            "    construction = _load_predecessor()\n"
            "    return np.asarray(construction[0], dtype=float), int(construction[2])\n"
        )
        return _replace_evolve_block(init_text, body, rust=False)
    raise ValueError(f"no predecessor seed scaffold for {task_name}")


def _hyra_final_seed(task_name: str, init_text: str) -> str:
    if task_name == "autocorrelation_first":
        body = (
            _HYRA_FINAL_LOADER
            + "\n\ndef propose_candidate(seed=42, budget_s=1000, **kwargs):\n"
            "    del seed, budget_s, kwargs\n"
            "    return np.asarray(_load_hyra_final()['values'], dtype=float).tolist()\n"
        )
        return (
            "# EVOLVE-BLOCK-START\n"
            + body.rstrip()
            + "\n# EVOLVE-BLOCK-END\n\n"
            "def run_code():\n"
            "    return propose_candidate(), None\n"
        )
    if task_name == "autocorrelation_second":
        body = (
            _HYRA_FINAL_LOADER
            + "\n\ndef construct_function():\n"
            "    return np.asarray(_load_hyra_final()['values'], dtype=float).tolist()\n"
        )
        return (
            "# EVOLVE-BLOCK-START\n"
            + body.rstrip()
            + "\n# EVOLVE-BLOCK-END\n\n"
            "def run_code():\n"
            "    return construct_function(), None\n"
        )
    if task_name == "sums_diffs":
        body = (
            _HYRA_FINAL_LOADER
            + "\n\ndef construct_set():\n"
            "    return sorted(set(int(item) for item in _load_hyra_final()['A']))\n"
        )
        return (
            "# EVOLVE-BLOCK-START\n"
            + body.rstrip()
            + "\n# EVOLVE-BLOCK-END\n\n"
            "def run_code():\n"
            "    return construct_set(), None\n"
        )
    if task_name == "erdos_min_overlap":
        body = (
            _HYRA_FINAL_LOADER
            + "\n\ndef construct_h():\n"
            "    artifact = _load_hyra_final()\n"
            "    return np.asarray(artifact['h_values'], dtype=float), int(artifact['n_points'])\n"
        )
        return _replace_evolve_block(init_text, body, rust=False)
    raise ValueError(f"no Hyra final-artifact seed scaffold for {task_name}")


def _instruction_for_contract(
    task_name: str,
    instruction: str,
    metric_mode: str,
) -> str:
    if task_name == "sums_diffs" and metric_mode == "public-compatible":
        return PUBLIC_SUMS_DIFFS_INSTRUCTION
    return instruction


def _git(*argv: str, cwd: Path | None = None) -> None:
    subprocess.run(["git", *argv], cwd=cwd, check=True)


def _checkout_repository(
    destination: Path,
    *,
    directory: str,
    url: str,
    commit: str,
    label: str,
) -> Path:
    checkout = destination / "upstream" / directory
    if not checkout.exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        _git("clone", "--no-checkout", url, str(checkout))
    _git("fetch", "--depth", "1", "origin", commit, cwd=checkout)
    _git("checkout", "--detach", commit, cwd=checkout)
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=checkout,
        text=True,
    ).strip()
    if actual != commit:
        raise RuntimeError(f"{label} pin mismatch: {actual}")
    return checkout


def _checkout_simpletes(destination: Path) -> Path:
    return _checkout_repository(
        destination,
        directory="simpletes",
        url=SIMPLETES_URL,
        commit=SIMPLETES_COMMIT,
        label="SimpleTES",
    )


def _checkout_ttt_discover(destination: Path) -> Path:
    return _checkout_repository(
        destination,
        directory="ttt-discover",
        url=TTT_DISCOVER_URL,
        commit=TTT_DISCOVER_COMMIT,
        label="TTT-Discover",
    )


def bootstrap(
    destination: Path,
    selected: list[str],
    trial_mode: str,
    evaluator_python: str,
    seed_source: str,
    metric_mode: str,
    qubit_total_budget_seconds: int,
    hyra_root: Path | None = None,
) -> None:
    if qubit_total_budget_seconds < 1:
        raise ValueError("qubit total budget must be positive")
    destination.mkdir(parents=True, exist_ok=True)
    if seed_source == "hyra-final":
        if hyra_root is None:
            raise ValueError("hyra_root is required for Hyra final-artifact seeds")
        hyra_root = hyra_root.resolve()
        actual_hyra_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=hyra_root,
            text=True,
        ).strip()
        if actual_hyra_commit != HYRA_RESULTS_COMMIT:
            raise RuntimeError(
                f"Hyra results pin mismatch: {actual_hyra_commit}"
            )
        unsupported = sorted(set(selected) - set(HYRA_FINAL_ARTIFACTS))
        if unsupported:
            raise ValueError(
                "Hyra final-artifact seed is unavailable for: "
                + ", ".join(unsupported)
            )
    upstream = _checkout_simpletes(destination)
    ttt_discover = (
        _checkout_ttt_discover(destination)
        if seed_source == "predecessor"
        and "autocorrelation_first" in selected
        else None
    )
    adapter = Path(__file__).resolve().parent / "evaluate.py"
    index: dict[str, str] = {}
    for task_name in selected:
        config = TASKS[task_name]
        task_root = destination / task_name
        seed = task_root / "seed"
        if seed.exists():
            shutil.rmtree(seed)
        seed.mkdir(parents=True)
        source = upstream / str(config["source"])
        instruction = (upstream / str(config["instruction"])).read_text(
            encoding="utf-8"
        )
        submission = seed / str(config["submission"])
        predecessor_source_raw = config.get("predecessor_source")
        predecessor_source = (
            upstream / str(predecessor_source_raw)
            if predecessor_source_raw is not None
            else None
        )
        predecessor_construction_raw = config.get("predecessor_construction")
        construction_repository = str(
            config.get("predecessor_construction_repository", "simpletes")
        )
        construction_root = (
            ttt_discover
            if construction_repository == "ttt-discover"
            else upstream
        )
        if (
            construction_repository == "ttt-discover"
            and construction_root is None
            and seed_source == "predecessor"
        ):
            raise RuntimeError("TTT-Discover predecessor was not checked out")
        predecessor_construction = (
            construction_root / str(predecessor_construction_raw)
            if predecessor_construction_raw is not None
            and construction_root is not None
            else None
        )
        hyra_final_artifact = (
            hyra_root / HYRA_FINAL_ARTIFACTS[task_name]
            if seed_source == "hyra-final" and hyra_root is not None
            else None
        )
        if hyra_final_artifact is not None and not hyra_final_artifact.is_file():
            raise FileNotFoundError(hyra_final_artifact)
        if seed_source == "hyra-final":
            assert hyra_final_artifact is not None
            submission.write_text(
                _hyra_final_seed(
                    task_name,
                    source.read_text(encoding="utf-8"),
                ),
                encoding="utf-8",
            )
            shutil.copy2(
                hyra_final_artifact,
                seed / "hyra_final_artifact.json",
            )
        elif seed_source == "predecessor" and task_name == "qubit_routing":
            shutil.copy2(predecessor_source, submission)
        elif seed_source == "predecessor":
            assert predecessor_construction is not None
            submission.write_text(
                _predecessor_seed(
                    task_name,
                    source.read_text(encoding="utf-8"),
                ),
                encoding="utf-8",
            )
            shutil.copy2(
                predecessor_construction,
                seed / "predecessor_construction.json",
            )
            if predecessor_source is not None:
                references = seed / "references"
                references.mkdir()
                shutil.copy2(
                    predecessor_source,
                    references / predecessor_source.name,
                )
        else:
            shutil.copy2(source, submission)
        seed_normalization = None
        if task_name == "erdos_min_overlap" and seed_source == "init":
            text = submission.read_text(encoding="utf-8")
            marker = "np.random.default_rng()"
            if marker not in text:
                raise RuntimeError("Erdos seed RNG marker changed upstream")
            submission.write_text(
                text.replace(marker, "np.random.default_rng(0)", 1),
                encoding="utf-8",
            )
            seed_normalization = "fixed numpy seed 0 for deterministic replay"
        solve_sh = seed / "solve.sh"
        solve_sh.write_text(
            "#!/bin/sh\nset -eu\ntest -f \"$1\"\n",
            encoding="utf-8",
        )
        solve_sh.chmod(0o755)
        evaluator_argv = [
            evaluator_python,
            str(adapter),
            "--upstream-root",
            str(upstream),
            "--task",
            task_name,
        ]
        resolved_metric_mode = (
            (
                "public-compatible"
                if task_name in PUBLIC_COMPATIBLE_TASKS
                else "upstream"
            )
            if metric_mode == "auto"
            else metric_mode
        )
        if (
            resolved_metric_mode == "public-compatible"
            and task_name not in PUBLIC_COMPATIBLE_TASKS
        ):
            raise ValueError(
                f"public-compatible metric is unavailable for {task_name}"
            )
        contract_instruction = _instruction_for_contract(
            task_name,
            instruction,
            resolved_metric_mode,
        )
        evaluator_argv.extend(["--metric-mode", resolved_metric_mode])
        if task_name == "qubit_routing":
            evaluator_argv.extend(["--trial-mode", trial_mode])
        evaluator_argv.append("{submission}")
        task_path = task_root / "task.json"
        task_spec = {
            "name": task_name,
            "goal": (
                f"{config['goal']}\n\n"
                + (
                    "The frozen seed is the pinned published predecessor, not the "
                    "weak init program. Improve this validated incumbent without "
                    "hard-coding the target score. When present, `references/` "
                    "contains its public search program for inspection.\n\n"
                    if seed_source == "predecessor"
                    else ""
                )
                + (
                    "The frozen seed is Tencent's published Hyra final artifact. "
                    "This is a final-artifact warm-start experiment: preserve or "
                    "improve the validated incumbent. It is not an autonomous "
                    "rediscovery experiment, and the target score is not exposed "
                    "to the Agent.\n\n"
                    if seed_source == "hyra-final"
                    else ""
                )
                + contract_instruction
            ),
            "seed_workspace": "seed",
            "submission_relpath": config["submission"],
            "entrypoint_argv": [
                "/bin/sh",
                "{workspace}/solve.sh",
                "{submission}",
            ],
            "evaluator_argv": evaluator_argv,
            "objective_key": config["objective"],
            "direction": config["direction"],
            "qualification_key": "qualified",
            "editable_region": {
                "start_marker": (
                    "// EVOLVE-BLOCK-START"
                    if Path(str(config["submission"])).suffix == ".rs"
                    else "# EVOLVE-BLOCK-START"
                ),
                "end_marker": (
                    "// EVOLVE-BLOCK-END"
                    if Path(str(config["submission"])).suffix == ".rs"
                    else "# EVOLVE-BLOCK-END"
                ),
            },
            "provenance": {
                "upstream": SIMPLETES_URL,
                "commit": SIMPLETES_COMMIT,
                "source": config["source"],
                "seed_source": seed_source,
                "predecessor_source": predecessor_source_raw,
                "predecessor_source_sha256": (
                    sha256_file(predecessor_source)
                    if predecessor_source is not None
                    else None
                ),
                "predecessor_construction_repository": construction_repository,
                "predecessor_construction_upstream": (
                    TTT_DISCOVER_URL
                    if construction_repository == "ttt-discover"
                    else SIMPLETES_URL
                ),
                "predecessor_construction_commit": (
                    TTT_DISCOVER_COMMIT
                    if construction_repository == "ttt-discover"
                    else SIMPLETES_COMMIT
                ),
                "predecessor_construction": predecessor_construction_raw,
                "predecessor_construction_sha256": (
                    sha256_file(predecessor_construction)
                    if predecessor_construction is not None
                    else None
                ),
                "instruction": config["instruction"],
                "instruction_contract": (
                    "public-compatible"
                    if contract_instruction != instruction
                    else "upstream"
                ),
                "adapter": "benchmarks/tasks/simpletes/evaluate.py",
                "metric_mode": resolved_metric_mode,
                "trial_mode": (
                    trial_mode if task_name == "qubit_routing" else None
                ),
                "seed_normalization": seed_normalization,
                "hyra_final_artifact": (
                    HYRA_FINAL_ARTIFACTS[task_name]
                    if hyra_final_artifact is not None
                    else None
                ),
                "hyra_final_artifact_repository": (
                    HYRA_RESULTS_URL
                    if hyra_final_artifact is not None
                    else None
                ),
                "hyra_final_artifact_commit": (
                    HYRA_RESULTS_COMMIT
                    if hyra_final_artifact is not None
                    else None
                ),
                "hyra_final_artifact_sha256": (
                    sha256_file(hyra_final_artifact)
                    if hyra_final_artifact is not None
                    else None
                ),
            },
        }
        if task_name == "qubit_routing":
            toolchain_root = destination.parent / "toolchains"
            cargo_home = Path(
                os.environ.get("CARGO_HOME", toolchain_root / "cargo")
            ).expanduser().resolve()
            rustup_home = Path(
                os.environ.get("RUSTUP_HOME", toolchain_root / "rustup")
            ).expanduser().resolve()
            cargo_bin = cargo_home / "bin"
            if cargo_bin.is_dir() and rustup_home.is_dir():
                task_spec["evaluator_env"] = {
                    "CARGO_HOME": str(cargo_home),
                    "RUSTUP_HOME": str(rustup_home),
                    "QUBIT_ROUTING_TOTAL_BUDGET_SECONDS": str(
                        qubit_total_budget_seconds
                    ),
                }
                task_spec["evaluator_path_prepend"] = [str(cargo_bin)]
                task_spec["provenance"]["rust_toolchain"] = (
                    "task-declared CARGO_HOME and RUSTUP_HOME"
                )
                task_spec["provenance"]["local_evaluator_budget_seconds"] = (
                    qubit_total_budget_seconds
                )
        if task_name in STATEFUL_TASKS:
            construction_bytes = (
                hyra_final_artifact.stat().st_size
                if hyra_final_artifact is not None
                else (
                    predecessor_construction.stat().st_size
                    if seed_source == "predecessor"
                    and predecessor_construction is not None
                    else 0
                )
            )
            state_max_bytes = max(
                4 * 1024 * 1024,
                construction_bytes * 4,
            )
            if (
                task_name == "sums_diffs"
                and resolved_metric_mode == "public-compatible"
            ):
                state_max_bytes = max(state_max_bytes, 64 * 1024 * 1024)
            task_spec["runtime_state"] = {
                "capture_env": "SIMPLETES_CAPTURE_CONSTRUCTION_PATH",
                "input_env": "SIMPLETES_SHARED_CONSTRUCTION_PATH",
                "description": (
                    "At evaluation time GLOBAL_BEST_CONSTRUCTION contains the "
                    "validated construction from the best state on the selected "
                    "parent lineage. It is optional and may be used as a warm start."
                ),
                "max_bytes": state_max_bytes,
            }
            task_spec.setdefault("evaluator_env", {})[
                "SIMPLETES_SHARED_CONSTRUCTION_MAX_BYTES"
            ] = str(state_max_bytes)
        write_task_spec(task_path, task_spec)
        index[task_name] = str(task_path)
    (destination / "tasks.json").write_text(
        json.dumps(index, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--tasks",
        default=",".join(TASKS),
        help="comma-separated task ids",
    )
    parser.add_argument("--trial-mode", choices=["fast", "full"], default="fast")
    parser.add_argument(
        "--seed-source",
        choices=["predecessor", "init", "hyra-final"],
        default="predecessor",
    )
    parser.add_argument(
        "--hyra-root",
        help="path to the pinned hyra-results checkout for --seed-source hyra-final",
    )
    parser.add_argument(
        "--metric-mode",
        choices=["auto", "upstream", "public-compatible"],
        default="auto",
    )
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--qubit-total-budget-seconds",
        type=int,
        default=DEFAULT_QUBIT_ROUTING_TOTAL_BUDGET_SECONDS,
        help="hardware-adjustable wall-time budget; full trial counts stay fixed",
    )
    args = parser.parse_args()
    selected = [item.strip() for item in args.tasks.split(",") if item.strip()]
    unknown = sorted(set(selected) - set(TASKS))
    if unknown:
        raise SystemExit(f"unknown tasks: {', '.join(unknown)}")
    bootstrap(
        Path(args.output).resolve(),
        selected,
        args.trial_mode,
        str(Path(args.python).expanduser().absolute()),
        args.seed_source,
        args.metric_mode,
        args.qubit_total_budget_seconds,
        (
            Path(args.hyra_root).expanduser().resolve()
            if args.hyra_root is not None
            else None
        ),
    )


if __name__ == "__main__":
    main()
