from __future__ import annotations

import argparse
import importlib.util
import json
import math
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from scipy.signal import fftconvolve

from rehyra.util import sha256_file


TARGETS = {
    "smallest_adder": 15,
    "autocorrelation_first": 1.502850,
    "autocorrelation_second": 0.962901,
    "erdos_min_overlap": 0.380859,
    "sums_diffs": 1.15971,
    "qubit_routing": 258369,
    "sunspot_symbolic": 0.78,
}

TOLERANCES = {
    "autocorrelation_first": 0.5e-6,
    "autocorrelation_second": 0.5e-6,
    "erdos_min_overlap": 0.5e-6,
    "sums_diffs": 0.5e-5,
    "qubit_routing": 0.0,
    "sunspot_symbolic": 0.5e-2,
    "smallest_adder": 0.0,
}


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _matches(task: str, value: float, *, direction: str) -> bool:
    target = float(TARGETS[task])
    tolerance = TOLERANCES[task]
    if direction == "minimize":
        return value <= target + tolerance
    return value >= target - tolerance


def verify_ac1(path: Path) -> dict[str, Any]:
    values = np.asarray(_load(path)["values"], dtype=np.float64)
    valid = bool(
        values.ndim == 1
        and values.size
        and np.all(np.isfinite(values))
        and np.all(values >= 0)
        and np.all(values <= 1000)
        and values.sum() >= 0.01
    )
    convolution = np.maximum(fftconvolve(values, values, mode="full"), 0.0)
    score = 2.0 * values.size * float(convolution.max()) / float(values.sum() ** 2)
    return {
        "qualified": valid,
        "c1": score,
        "target": TARGETS["autocorrelation_first"],
        "matches_published": _matches(
            "autocorrelation_first", score, direction="minimize"
        ),
    }


def verify_ac2(path: Path) -> dict[str, Any]:
    raw = np.asarray(_load(path)["values"], dtype=np.float64)
    scale = max(1.0, float(raw.max()) / 1000.0)
    values = raw / scale
    convolution = np.maximum(fftconvolve(values, values, mode="full"), 0.0)
    y = np.concatenate(([0.0], convolution, [0.0]))
    h = 1.0 / (convolution.size + 1)
    l2_squared = h / 3.0 * float(
        np.sum(y[:-1] ** 2 + y[:-1] * y[1:] + y[1:] ** 2)
    )
    norm_1 = float(np.sum(convolution)) / (convolution.size + 1)
    norm_inf = float(convolution.max())
    score = l2_squared / (norm_1 * norm_inf)
    return {
        "qualified": bool(
            raw.ndim == 1
            and raw.size
            and np.all(np.isfinite(raw))
            and np.all(raw >= 0)
        ),
        "r": score,
        "target": TARGETS["autocorrelation_second"],
        "matches_published": _matches(
            "autocorrelation_second", score, direction="maximize"
        ),
        "scale_normalization": scale,
        "note": (
            "The published array exceeds the upstream evaluator's 1000 cap. "
            "A global scale normalization is required; the objective is scale invariant."
        ),
    }


def verify_erdos(path: Path) -> dict[str, Any]:
    artifact = _load(path)
    values = np.asarray(artifact["h_values"], dtype=np.float64)
    n = int(artifact["n_points"])
    if float(values.sum()) != n / 2.0:
        values = values * ((n / 2.0) / float(values.sum()))
    correlation = np.correlate(values, 1.0 - values, mode="full") * (2.0 / n)
    score = float(correlation.max())
    return {
        "qualified": bool(
            values.shape == (n,)
            and np.all(np.isfinite(values))
            and np.all(values >= 0)
            and np.all(values <= 1)
        ),
        "c5": score,
        "target": TARGETS["erdos_min_overlap"],
        "matches_published": _matches(
            "erdos_min_overlap", score, direction="minimize"
        ),
    }


def verify_sums_diffs(path: Path) -> dict[str, Any]:
    values = sorted(set(int(item) for item in _load(path)["A"]))
    lo = values[0]
    shifted = np.asarray(values, dtype=np.int64) - lo
    indicator = np.zeros(int(shifted[-1]) + 1, dtype=np.float64)
    indicator[shifted] = 1.0
    sum_counts = fftconvolve(indicator, indicator, mode="full")
    diff_counts = fftconvolve(indicator, indicator[::-1], mode="full")
    sumset_size = int(np.sum(sum_counts > 0.5))
    diffset_size = int(np.sum(diff_counts > 0.5))
    n = len(values)
    score = math.log(sumset_size / n) / math.log(diffset_size / n)
    return {
        "qualified": 2 <= n and all(-1_000_000 <= item <= 1_000_000 for item in values),
        "c_a": score,
        "set_size": n,
        "sumset_size": sumset_size,
        "diffset_size": diffset_size,
        "target": TARGETS["sums_diffs"],
        "matches_published": _matches(
            "sums_diffs", score, direction="maximize"
        ),
    }


def _topology_name(case_id: str) -> str:
    if case_id.endswith("_heron_fez"):
        return "heron_fez"
    if case_id.endswith("_willow"):
        return "willow"
    if case_id.endswith("_q20"):
        return "q20"
    raise ValueError(f"unknown topology suffix: {case_id}")


def verify_qubit(path: Path, simpletes_root: Path) -> dict[str, Any]:
    cases = _load(path)["cases"]
    topology_root = (
        simpletes_root
        / "datasets/qubit_routing/swap_reduction/python/benchmarks/topologies"
    )
    topologies: dict[str, set[tuple[int, int]]] = {}
    for name in ("q20", "willow", "heron_fez"):
        raw = _load(topology_root / f"{name}.json")
        topologies[name] = {
            tuple(sorted((int(left), int(right)))) for left, right in raw["edges"]
        }
    violations: list[str] = []
    by_topology = {"q20": 0, "willow": 0, "heron_fez": 0}
    for case in cases:
        name = _topology_name(str(case["id"]))
        qasm = str(case["output_circuit"])
        swaps = re.findall(r"(?mi)^\s*swap\s+q\[(\d+)\]\s*,\s*q\[(\d+)\]", qasm)
        two_qubit = [
            *swaps,
            *re.findall(r"(?mi)^\s*cx\s+q\[(\d+)\]\s*,\s*q\[(\d+)\]", qasm),
        ]
        for left, right in two_qubit:
            edge = tuple(sorted((int(left), int(right))))
            if edge not in topologies[name]:
                violations.append(f"{case['id']}: non-edge {edge}")
        by_topology[name] += 3 * len(swaps)
    suite_path = (
        simpletes_root
        / "datasets/qubit_routing/swap_reduction/python/benchmarks/sabre_suite.json"
    )
    suite_cases = _load(suite_path)["cases"]
    artifact_ids = {str(case["id"]) for case in cases}
    missing_cases = [
        case for case in suite_cases if str(case["id"]) not in artifact_ids
    ]
    omitted_baseline_cnot = sum(
        int(case["original_cnot_added"]) for case in missing_cases
    )
    explicit = sum(by_topology.values())
    reproduced = explicit + omitted_baseline_cnot

    semantic_status = "skipped_qiskit_unavailable"
    semantic_errors: list[str] = []
    try:
        import qiskit  # noqa: F401

        evaluator_path = (
            simpletes_root
            / "datasets/qubit_routing/swap_reduction/evaluator.py"
        )
        sys.path.insert(0, str(simpletes_root))
        spec = importlib.util.spec_from_file_location(
            "hyra_qubit_semantic_evaluator",
            evaluator_path,
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot load {evaluator_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        suite_metadata = module._load_suite_metadata(suite_path)
        for case in cases:
            error = module._validate_case_routing(
                case,
                suite_metadata[str(case["id"])],
            )
            if error is not None:
                semantic_errors.append(f"{case['id']}: {error}")
        semantic_status = "passed" if not semantic_errors else "failed"
    except ModuleNotFoundError:
        pass

    return {
        "qualified": (
            len(cases) == 61
            and not violations
            and semantic_status != "failed"
            and reproduced == TARGETS["qubit_routing"]
        ),
        "cnot_added_from_explicit_swaps": explicit,
        "omitted_baseline_cnot": omitted_baseline_cnot,
        "reproduced_total_cnot_added": reproduced,
        "by_topology": by_topology,
        "target": TARGETS["qubit_routing"],
        "matches_published": _matches(
            "qubit_routing", float(reproduced), direction="minimize"
        ),
        "difference_from_published": reproduced - TARGETS["qubit_routing"],
        "omitted_case_ids": [str(case["id"]) for case in missing_cases],
        "adjacency_violations": violations,
        "semantic_equivalence": semantic_status,
        "semantic_errors": semantic_errors,
        "note": (
            "The artifact contains 61 changed routed circuits. The frozen suite has "
            "72 cases; its 11 omitted baseline-pass-through cases contribute three "
            "CNOTs, explaining 258366 + 3 = the published 258369."
        ),
    }


def _load_sunspot_series(path: Path) -> tuple[np.ndarray, list[float]]:
    values: list[float] = []
    years: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(";")
        if len(fields) < 4:
            continue
        years.append(float(fields[2]))
        values.append(float(fields[3]))
    return np.asarray(values, dtype=np.float64), years


def verify_sunspot(path: Path, artifact_root: Path, data_path: Path) -> dict[str, Any]:
    srfit_path = artifact_root / "sunspot_symbolic/solution/srfit.py"
    spec = importlib.util.spec_from_file_location("hyra_public_srfit", srfit_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {srfit_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["hyra_public_srfit"] = module
    spec.loader.exec_module(module)
    artifact = _load(path)
    series, years = _load_sunspot_series(data_path)
    era_lo = next(index for index, year in enumerate(years) if year >= 1979.0)
    era_hi = next(
        (index for index, year in enumerate(years) if year >= 2026.0),
        len(series),
    )
    scored = module.score_law(
        artifact["expression"],
        artifact["params"],
        series,
        era_lo,
        era_hi,
    )
    return {
        "qualified": True,
        "rolling_origin_r2": float(scored.r2),
        "penalty": float(scored.penalty),
        "penalized_score": float(scored.score),
        "era": [years[era_lo], years[era_hi - 1]],
        "target": TARGETS["sunspot_symbolic"],
        "matches_published": _matches(
            "sunspot_symbolic", float(scored.r2), direction="maximize"
        ),
        "contaminated_holdout": True,
    }


def verify_adder(
    path: Path,
    verifier_path: Path,
    *,
    public_tests: int,
    shadow_tests: int,
    shadow_seeds: tuple[int, ...],
) -> dict[str, Any]:
    from benchmarks.tasks.adderboard.evaluate import (
        actual_parameter_count,
        direct_input_addition,
        load_module,
        one_run,
        suspicious_fixed_buffers,
    )

    artifact = _load(path)
    source = artifact.get("submission_py")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("smallest_adder artifact has no submission_py")
    with tempfile.TemporaryDirectory(prefix="hyra-adder-replay-") as raw:
        submission_path = Path(raw) / "submission.py"
        submission_path.write_text(source, encoding="utf-8")
        verifier = load_module(verifier_path, "hyra_adderboard_verifier")
        submission = verifier.load_submission(str(submission_path))
        model, metadata = submission.build_model()
        actual = actual_parameter_count(model)
        declared = int(metadata.get("params", -1))
        public, _ = one_run(
            verifier,
            submission,
            tests=public_tests,
            seed=2025,
        )
        shadows = []
        for seed in shadow_seeds:
            result, _ = one_run(
                verifier,
                submission,
                tests=shadow_tests,
                seed=seed,
            )
            shadows.append(
                {
                    "seed": seed,
                    "accuracy": float(result["accuracy"]),
                    "qualified": bool(result["qualified"]),
                    "passed": int(result["passed"]),
                    "total": int(result["total"]),
                }
            )
        direct = direct_input_addition(submission.add)
        buffers = suspicious_fixed_buffers(model)
        qualified = bool(
            public["qualified"]
            and all(item["qualified"] for item in shadows)
            and actual == declared
            and not direct
        )
        return {
            "qualified": qualified,
            "trainable_parameters": actual,
            "declared_parameters": declared,
            "target": TARGETS["smallest_adder"],
            "matches_published": _matches(
                "smallest_adder", float(actual), direction="minimize"
            ),
            "public_seed": 2025,
            "public_accuracy": float(public["accuracy"]),
            "public_passed": int(public["passed"]),
            "public_total": int(public["total"]),
            "shadow_results": shadows,
            "direct_input_addition_detected": direct,
            "nonstandard_fixed_buffers": buffers,
            "trained_category_review": "manual_final_review_required",
        }


def verify_all(
    hyra_root: Path,
    simpletes_root: Path,
    sunspot_data: Path,
    *,
    adder_verifier: Path | None = None,
    adder_python: str | None = None,
    adder_public_tests: int = 10_000,
    adder_shadow_tests: int = 10_000,
    adder_shadow_seeds: tuple[int, ...] = (100003, 100019),
) -> dict[str, Any]:
    artifact_root = hyra_root / "AI4Science"
    started = time.monotonic()
    checks = {
        "autocorrelation_first": verify_ac1(
            artifact_root / "autocorrelation_first/solution.json"
        ),
        "autocorrelation_second": verify_ac2(
            artifact_root / "autocorrelation_second/solution.json"
        ),
        "erdos_min_overlap": verify_erdos(
            artifact_root / "erdos_min_overlap/solution.json"
        ),
        "sums_diffs": verify_sums_diffs(
            artifact_root / "sums_diffs/solution.json"
        ),
        "qubit_routing": verify_qubit(
            artifact_root / "qubit_routing/solution.json",
            simpletes_root,
        ),
        "sunspot_symbolic": verify_sunspot(
            artifact_root / "sunspot_symbolic/solution.json",
            artifact_root,
            sunspot_data,
        ),
    }
    if adder_verifier is not None:
        artifact_path = artifact_root / "smallest_adder/solution.json"
        if (
            adder_python
            and Path(adder_python).absolute() != Path(sys.executable).absolute()
        ):
            completed = subprocess.run(
                [
                    adder_python,
                    "-m",
                    "benchmarks.tasks.hyra_artifacts.verify_adder",
                    "--artifact",
                    str(artifact_path),
                    "--verifier",
                    str(adder_verifier),
                    "--public-tests",
                    str(adder_public_tests),
                    "--shadow-tests",
                    str(adder_shadow_tests),
                    "--shadow-seeds",
                    ",".join(str(item) for item in adder_shadow_seeds),
                ],
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            )
            checks["smallest_adder"] = json.loads(completed.stdout)
        else:
            checks["smallest_adder"] = verify_adder(
                artifact_path,
                adder_verifier,
                public_tests=adder_public_tests,
                shadow_tests=adder_shadow_tests,
                shadow_seeds=adder_shadow_seeds,
            )
    return {
        "schema_version": 2,
        "duration_seconds": time.monotonic() - started,
        "hyra_commit": "bcda2e29200fbeaeb1620acca0670cc445e8688f",
        "simpletes_commit": "b7e0367b5ab19554fb40859e8069e091bb8c4ca2",
        "sunspot_data_sha256": sha256_file(sunspot_data),
        "checks": checks,
        "all_qualified": all(bool(item["qualified"]) for item in checks.values()),
        "all_match_published": all(
            bool(item["matches_published"]) for item in checks.values()
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--hyra-root", required=True)
    parser.add_argument("--simpletes-root", required=True)
    parser.add_argument("--sunspot-data", required=True)
    parser.add_argument("--adder-verifier")
    parser.add_argument("--adder-python")
    parser.add_argument("--adder-public-tests", type=int, default=10_000)
    parser.add_argument("--adder-shadow-tests", type=int, default=10_000)
    parser.add_argument("--adder-shadow-seeds", default="100003,100019")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = verify_all(
        Path(args.hyra_root).resolve(),
        Path(args.simpletes_root).resolve(),
        Path(args.sunspot_data).resolve(),
        adder_verifier=(
            Path(args.adder_verifier).resolve() if args.adder_verifier else None
        ),
        adder_python=args.adder_python,
        adder_public_tests=args.adder_public_tests,
        adder_shadow_tests=args.adder_shadow_tests,
        adder_shadow_seeds=tuple(
            int(item.strip())
            for item in args.adder_shadow_seeds.split(",")
            if item.strip()
        ),
    )
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        Path(args.output).resolve().write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
