from __future__ import annotations

import argparse
import ast
import contextlib
import importlib.util
import inspect
import io
import json
import sys
import traceback
from pathlib import Path
from typing import Any


def load_module(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def actual_parameter_count(model: Any) -> int:
    if not hasattr(model, "parameters"):
        raise TypeError("model does not expose parameters()")
    return sum(int(parameter.numel()) for parameter in model.parameters())


def suspicious_fixed_buffers(model: Any) -> list[dict[str, Any]]:
    """Return fixed floating tensors that require trained-category review."""
    if not hasattr(model, "named_buffers"):
        return []
    allowed_suffixes = ("causal_mask", "rope_cos", "rope_sin")
    suspicious: list[dict[str, Any]] = []
    for name, buffer in model.named_buffers():
        if name.endswith(allowed_suffixes):
            continue
        if not getattr(buffer, "is_floating_point", lambda: False)():
            continue
        flattened = buffer.detach().cpu().flatten()
        suspicious.append(
            {
                "name": name,
                "numel": int(buffer.numel()),
                "values_preview": [
                    float(value) for value in flattened[:8]
                ],
            }
        )
    return suspicious


class DirectInputAdditionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_BinOp(self, node: ast.BinOp) -> None:
        if isinstance(node.op, ast.Add):
            names = {
                child.id for child in ast.walk(node) if isinstance(child, ast.Name)
            }
            if {"a", "b"}.issubset(names):
                self.found = True
        self.generic_visit(node)


def direct_input_addition(add_function: Any) -> bool:
    try:
        source = inspect.getsource(add_function)
    except (OSError, TypeError):
        return True
    tree = ast.parse(source)
    visitor = DirectInputAdditionVisitor()
    visitor.visit(tree)
    return visitor.found


def one_run(
    verifier: Any,
    submission: Any,
    *,
    tests: int,
    seed: int,
) -> tuple[dict[str, Any], str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = verifier.run_test(submission, num_tests=tests, seed=seed)
    return result, buffer.getvalue()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verifier", required=True)
    parser.add_argument("--submission", required=True)
    parser.add_argument("--public-tests", type=int, default=500)
    parser.add_argument("--shadow-tests", type=int, default=500)
    parser.add_argument("--shadow-seeds", default="100003,100019")
    args = parser.parse_args()

    try:
        verifier = load_module(Path(args.verifier), "adderboard_verifier")
        submission = verifier.load_submission(str(Path(args.submission)))
        model, metadata = submission.build_model()
        actual = actual_parameter_count(model)
        declared = int(metadata.get("params", -1))
        metadata_matches = actual == declared
        direct_addition = direct_input_addition(submission.add)
        review_buffers = suspicious_fixed_buffers(model)

        public, public_log = one_run(
            verifier,
            submission,
            tests=args.public_tests,
            seed=2025,
        )
        shadow_results: list[dict[str, Any]] = []
        shadow_logs: list[str] = []
        for raw_seed in args.shadow_seeds.split(","):
            seed = int(raw_seed.strip())
            result, log = one_run(
                verifier,
                submission,
                tests=args.shadow_tests,
                seed=seed,
            )
            shadow_results.append(
                {
                    "seed": seed,
                    "accuracy": result["accuracy"],
                    "qualified": result["qualified"],
                    "passed": result["passed"],
                    "total": result["total"],
                }
            )
            shadow_logs.append(log)
        shadow_min_accuracy = min(
            (item["accuracy"] for item in shadow_results),
            default=0.0,
        )
        qualified = bool(
            public["qualified"]
            and all(item["qualified"] for item in shadow_results)
            and metadata_matches
            and not direct_addition
        )
        print(public_log, file=sys.stderr)
        for log in shadow_logs:
            print(log, file=sys.stderr)
        result = {
            "qualified": qualified,
            "actual_parameters": actual,
            "declared_parameters": declared,
            "metadata_matches_actual": metadata_matches,
            "direct_input_addition_detected": direct_addition,
            "trained_category_review": {
                "status": (
                    "manual_review_required"
                    if review_buffers
                    else "no_nonstandard_fixed_buffers_detected"
                ),
                "nonstandard_fixed_buffers": review_buffers,
            },
            "public_seed": 2025,
            "public_accuracy": public["accuracy"],
            "public_passed": public["passed"],
            "public_total": public["total"],
            "shadow_min_accuracy": shadow_min_accuracy,
            "shadow_results": shadow_results,
            "rule_audit": "automatic_partial_manual_final_required",
        }
    except BaseException as exc:
        traceback.print_exc(file=sys.stderr)
        result = {
            "qualified": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
