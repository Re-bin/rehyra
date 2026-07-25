from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import signal
import subprocess
import sys
import tempfile
import types
from pathlib import Path
from typing import Any


TASKS: dict[str, dict[str, Any]] = {
    "autocorrelation_first": {
        "evaluator": "datasets/autocorrelation/autocorrelation_first/evaluator.py",
        "objective_source": "c1",
        "objective": "c1",
    },
    "autocorrelation_second": {
        "evaluator": "datasets/autocorrelation/autocorrelation_second/evaluator.py",
        "objective_source": "c2",
        "objective": "r",
    },
    "erdos_min_overlap": {
        "evaluator": "datasets/erdos/erdos_min_overlap/evaluator.py",
        "objective_source": "c5",
        "objective": "c5",
    },
    "sums_diffs": {
        "evaluator": "datasets/sums_diffs/sums_diffs/evaluator.py",
        "objective_source": "c_value",
        "objective": "c_a",
    },
    "qubit_routing": {
        "evaluator": "datasets/qubit_routing/swap_reduction/evaluator.py",
        "objective_source": "total_added_cnot_by_candidate",
        "objective": "cnot_added",
    },
}


def _load(path: Path, upstream_root: Path):
    sys.path.insert(0, str(upstream_root))
    # SimpleTES exposes lightweight evaluator helpers below its package, but its
    # top-level __init__ eagerly imports the full LLM engine and UI dependencies.
    # Evaluation should need only the modules explicitly imported by the pinned
    # evaluator. Registering the package path avoids executing that unrelated
    # top-level import surface while preserving normal submodule imports.
    package_root = upstream_root / "simpletes"
    package = types.ModuleType("simpletes")
    package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    package.__package__ = "simpletes"
    sys.modules["simpletes"] = package
    spec = importlib.util.spec_from_file_location(
        f"hyra_external_evaluator_{path.parent.name}",
        path,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load evaluator: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _install_simpletes_package(upstream_root: Path) -> None:
    sys.path.insert(0, str(upstream_root))
    package_root = upstream_root / "simpletes"
    package = types.ModuleType("simpletes")
    package.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    package.__package__ = "simpletes"
    sys.modules["simpletes"] = package


def _run_candidate(submission: Path, *, timeout_seconds: int) -> Any:
    wrapper = """\
import importlib.util
import builtins
import json
import os
import sys
import traceback


def decode_construction(value):
    if isinstance(value, list):
        return [decode_construction(item) for item in value]
    if not isinstance(value, dict):
        return value
    tag = value.get("__simpletes_type__", value.get("__simpleevolve_type__"))
    if tag == "ndarray":
        import numpy as np
        return np.asarray(
            value.get("data"),
            dtype=value.get("dtype"),
        )
    if tag == "tuple":
        return tuple(
            decode_construction(item)
            for item in value.get("items", [])
        )
    return {
        key: decode_construction(item)
        for key, item in value.items()
    }


def install_shared_construction():
    value = None
    path = os.environ.get("SIMPLETES_SHARED_CONSTRUCTION_PATH")
    if path:
        try:
            with open(path, encoding="utf-8") as handle:
                value = decode_construction(json.load(handle))
        except (OSError, ValueError, TypeError):
            value = None
    setattr(builtins, "GLOBAL_BEST_CONSTRUCTION", value)


def encode(value):
    if isinstance(value, tuple):
        return {
            "__hyra_wire_type__": "tuple",
            "items": [encode(item) for item in value],
        }
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if hasattr(value, "tolist"):
        return encode(value.tolist())
    if hasattr(value, "item"):
        return encode(value.item())
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported candidate output type: {type(value).__name__}")


submission, output = sys.argv[1:3]
try:
    install_shared_construction()
    spec = importlib.util.spec_from_file_location("candidate", submission)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load candidate: {submission}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "run_code") or not callable(module.run_code):
        raise RuntimeError("candidate must define run_code()")
    result = {"output": encode(module.run_code())}
except BaseException as exc:
    traceback.print_exc()
    result = {"error": f"{type(exc).__name__}: {exc}"}
flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
if hasattr(os, "O_NOFOLLOW"):
    flags |= os.O_NOFOLLOW
descriptor = os.open(output, flags, 0o600)
with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
    json.dump(result, handle, ensure_ascii=False, allow_nan=False)
"""
    with tempfile.TemporaryDirectory(prefix="hyra-public-metric-") as raw:
        output_path = Path(raw) / "candidate.json"
        process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                wrapper,
                str(submission),
                str(output_path),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
        )
        try:
            _, stderr = process.communicate(timeout=timeout_seconds)
        except subprocess.TimeoutExpired as exc:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.communicate()
            raise TimeoutError(
                f"candidate timed out after {timeout_seconds}s"
            ) from exc
        if process.returncode != 0 or not output_path.is_file():
            diagnostic = stderr.decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(
                f"candidate exited {process.returncode}: {diagnostic}"
            )
        max_bytes = int(
            os.environ.get(
                "HYRA_CANDIDATE_RESULT_MAX_BYTES",
                str(64 * 1024 * 1024),
            )
        )
        size = output_path.stat().st_size
        if size > max_bytes:
            raise RuntimeError(
                f"candidate output is {size} bytes; limit is {max_bytes}"
            )
        result = json.loads(output_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict):
            raise RuntimeError("candidate wrapper returned invalid data")
        if "error" in result:
            diagnostic = stderr.decode("utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"{result['error']}: {diagnostic}")
        return _decode_wire(result.get("output"))


def _decode_wire(value: Any) -> Any:
    if isinstance(value, list):
        return [_decode_wire(item) for item in value]
    if isinstance(value, dict):
        if value.get("__hyra_wire_type__") == "tuple":
            items = value.get("items")
            if not isinstance(items, list):
                raise RuntimeError("invalid tuple candidate output")
            return tuple(_decode_wire(item) for item in items)
        return {key: _decode_wire(item) for key, item in value.items()}
    return value


def _candidate_solution(output: Any) -> Any:
    if isinstance(output, tuple) and output:
        return output[0]
    return output


def _public_compatible_evaluate(task: str, submission: Path) -> dict[str, Any]:
    import numpy as np
    from scipy.signal import fftconvolve

    timeout_seconds = int(os.environ.get("EVALUATOR_TIMEOUT_SECONDS", "1100"))
    output = _run_candidate(submission, timeout_seconds=timeout_seconds)
    solution = _candidate_solution(output)

    if task in {"autocorrelation_first", "autocorrelation_second"}:
        values = np.asarray(solution, dtype=np.float64)
        valid = bool(
            values.ndim == 1
            and values.size
            and np.all(np.isfinite(values))
            and np.all(values >= 0)
            and values.sum() >= 0.01
        )
        if not valid:
            raise ValueError("invalid nonnegative autocorrelation sequence")
        if task == "autocorrelation_first" and np.any(values > 1000):
            raise ValueError("autocorrelation_first values exceed 1000")
        scale = (
            max(1.0, float(values.max()) / 1000.0)
            if task == "autocorrelation_second"
            else 1.0
        )
        normalized = values / scale
        convolution = np.maximum(
            fftconvolve(normalized, normalized, mode="full"),
            0.0,
        )
        if task == "autocorrelation_first":
            score = (
                2.0
                * values.size
                * float(convolution.max())
                / float(normalized.sum() ** 2)
            )
            result = {
                "qualified": True,
                "c1": score,
                "upstream_metrics": {
                    "validity": 1.0,
                    "c1": score,
                    "combined_score": 1.0 / (1e-8 + score),
                    "metric_mode": "public-compatible-fft",
                    "sequence_length": int(values.size),
                },
            }
        else:
            y = np.concatenate(([0.0], convolution, [0.0]))
            h = 1.0 / (convolution.size + 1)
            l2_squared = h / 3.0 * float(
                np.sum(y[:-1] ** 2 + y[:-1] * y[1:] + y[1:] ** 2)
            )
            norm_1 = float(np.sum(convolution)) / (convolution.size + 1)
            norm_inf = float(convolution.max())
            score = l2_squared / (norm_1 * norm_inf)
            result = {
                "qualified": True,
                "r": score,
                "upstream_metrics": {
                    "validity": 1.0,
                    "c2": score,
                    "combined_score": score,
                    "metric_mode": "public-compatible-fft",
                    "sequence_length": int(values.size),
                    "scale_normalization": scale,
                },
            }
    elif task == "sums_diffs":
        raw = np.asarray(solution).reshape(-1)
        if raw.size < 2:
            raise ValueError("sums_diffs candidate has fewer than two values")
        floats = raw.astype(np.float64)
        if not np.all(np.isfinite(floats)):
            raise ValueError("sums_diffs candidate contains non-finite values")
        rounded = np.rint(floats)
        if not np.all(np.abs(floats - rounded) <= 1e-9):
            raise ValueError("sums_diffs candidate contains non-integers")
        values = np.unique(rounded.astype(np.int64))
        if values.size < 2 or values[0] < -1_000_000 or values[-1] > 1_000_000:
            raise ValueError("sums_diffs candidate is outside the valid domain")
        shifted = values - values[0]
        indicator = np.zeros(int(shifted[-1]) + 1, dtype=np.float64)
        indicator[shifted] = 1.0
        sum_counts = fftconvolve(indicator, indicator, mode="full")
        diff_counts = fftconvolve(indicator, indicator[::-1], mode="full")
        sumset_size = int(np.sum(sum_counts > 0.5))
        diffset_size = int(np.sum(diff_counts > 0.5))
        n = int(values.size)
        sum_ratio = sumset_size / n
        diff_ratio = diffset_size / n
        if sum_ratio <= 1.0 or diff_ratio <= 1.0:
            raise ValueError("sums_diffs candidate has invalid ratios")
        score = math.log(sum_ratio) / math.log(diff_ratio)
        solution = values.tolist()
        result = {
            "qualified": True,
            "c_a": score,
            "upstream_metrics": {
                "validity": 1.0,
                "c_value": score,
                "combined_score": score,
                "metric_mode": "public-compatible-fft",
                "set_size": n,
                "sumset_size": sumset_size,
                "diffset_size": diffset_size,
                "sum_ratio": sum_ratio,
                "diff_ratio": diff_ratio,
            },
        }
    else:
        raise ValueError(f"public-compatible metric is unsupported for {task}")

    if os.environ.get("SIMPLETES_CAPTURE_CONSTRUCTION_PATH"):
        from simpletes.construction import capture_construction_if_requested

        capture_construction_if_requested(solution)
    return result


def normalize(task: str, raw: dict[str, Any]) -> dict[str, Any]:
    config = TASKS[task]
    source_key = str(config["objective_source"])
    objective_key = str(config["objective"])
    source_value = raw.get(source_key)
    finite = (
        isinstance(source_value, (int, float))
        and not isinstance(source_value, bool)
        and math.isfinite(float(source_value))
    )
    qualified = float(raw.get("validity", 0.0)) >= 1.0 and finite
    result = {
        "qualified": qualified,
        objective_key: float(source_value) if finite else None,
        "upstream_metrics": raw,
    }
    if raw.get("error"):
        result["review_flags"] = [f"upstream_error: {raw['error']}"]
    return result


def _prepare_candidate_environment(upstream_root: Path) -> None:
    current = os.environ.get("PYTHONPATH")
    entries = [str(upstream_root)]
    if current:
        entries.append(current)
    os.environ["PYTHONPATH"] = os.pathsep.join(entries)


def _state_summary() -> str | None:
    from simpletes.construction import (
        CAPTURE_CONSTRUCTION_ENV,
        read_payload,
        summarize_construction_payload,
    )

    raw_path = os.environ.get(CAPTURE_CONSTRUCTION_ENV)
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        return summarize_construction_payload(read_payload(path))
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--upstream-root", required=True)
    parser.add_argument("--task", choices=sorted(TASKS), required=True)
    parser.add_argument("--trial-mode", choices=["fast", "full"], default="fast")
    parser.add_argument(
        "--metric-mode",
        choices=["upstream", "public-compatible"],
        default="upstream",
    )
    parser.add_argument("submission")
    args = parser.parse_args()

    upstream_root = Path(args.upstream_root).resolve()
    _prepare_candidate_environment(upstream_root)
    if args.metric_mode == "public-compatible":
        _install_simpletes_package(upstream_root)
        result = _public_compatible_evaluate(
            args.task,
            Path(args.submission).resolve(),
        )
    else:
        config = TASKS[args.task]
        evaluator_path = upstream_root / str(config["evaluator"])
        module = _load(evaluator_path, upstream_root)
        kwargs: dict[str, Any] = {}
        if args.task == "qubit_routing":
            kwargs["trial_mode"] = args.trial_mode
        raw = module.evaluate(str(Path(args.submission).resolve()), **kwargs)
        result = normalize(args.task, raw)
    if summary := _state_summary():
        result["state_summary"] = summary
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
