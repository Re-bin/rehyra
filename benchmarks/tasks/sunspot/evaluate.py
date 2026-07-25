from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np


def _load_srfit(path: Path):
    spec = importlib.util.spec_from_file_location("hyra_sunspot_srfit", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_series(path: Path) -> tuple[np.ndarray, list[float]]:
    years: list[float] = []
    values: list[float] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(";")
        if len(fields) < 4:
            continue
        years.append(float(fields[2]))
        values.append(float(fields[3]))
    return np.asarray(values, dtype=np.float64), years


def _first_at_or_after(years: list[float], target: float) -> int:
    return next(index for index, year in enumerate(years) if year >= target)


def evaluate(
    submission: Path,
    *,
    srfit_path: Path,
    data_path: Path,
    mode: str,
) -> dict[str, object]:
    try:
        artifact = json.loads(submission.read_text(encoding="utf-8"))
        expression = artifact["expression"]
        params = artifact["params"]
        module = _load_srfit(srfit_path)
        series, years = _load_series(data_path)
        train_hi = _first_at_or_after(years, 1932.042)
        valid_hi = _first_at_or_after(years, 1979.0)
        test_hi = _first_at_or_after(years, 2026.0)
        if mode == "valid":
            scored = module.blended_forecast_score(
                expression,
                params,
                series,
                0,
                train_hi,
                valid_hi,
            )
            return {
                "qualified": True,
                "score": float(scored.score),
                "train_r2": float(scored.train_r2),
                "valid_r2": float(scored.valid_r2),
                "penalty": float(scored.penalty),
                "visible_era": [years[train_hi], years[valid_hi - 1]],
            }
        scored = module.score_law(
            expression,
            params,
            series,
            valid_hi,
            test_hi,
        )
        return {
            "qualified": True,
            "score": float(scored.r2),
            "test_r2": float(scored.r2),
            "penalty": float(scored.penalty),
            "penalized_test_score": float(scored.score),
            "sealed_era": [years[valid_hi], years[test_hi - 1]],
        }
    except Exception as exc:
        return {
            "qualified": False,
            "score": -5.0,
            "error": f"{type(exc).__name__}: {exc}",
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--srfit", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--mode", choices=["valid", "test"], default="valid")
    parser.add_argument("submission")
    args = parser.parse_args()
    result = evaluate(
        Path(args.submission).resolve(),
        srfit_path=Path(args.srfit).resolve(),
        data_path=Path(args.data).resolve(),
        mode=args.mode,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
