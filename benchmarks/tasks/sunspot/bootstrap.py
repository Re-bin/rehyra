from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from rehyra.task import write_task_spec
from rehyra.util import sha256_file


HYRA_RESULTS_URL = "https://github.com/Tencent-Hunyuan/hyra-results.git"
HYRA_RESULTS_COMMIT = "bcda2e29200fbeaeb1620acca0670cc445e8688f"
HYRA_SUNSPOT_ARTIFACT = "AI4Science/sunspot_symbolic/solution.json"


def bootstrap(
    output: Path,
    *,
    hyra_root: Path,
    data_path: Path,
    evaluator_python: str,
    seed_source: str = "init",
) -> None:
    actual_hyra_commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=hyra_root,
        text=True,
    ).strip()
    if actual_hyra_commit != HYRA_RESULTS_COMMIT:
        raise RuntimeError(f"Hyra results pin mismatch: {actual_hyra_commit}")
    if seed_source not in {"init", "hyra-final"}:
        raise ValueError(f"unsupported seed source: {seed_source}")
    output.mkdir(parents=True, exist_ok=True)
    source = hyra_root / "AI4Science/sunspot_symbolic/solution"
    hyra_artifact = hyra_root / HYRA_SUNSPOT_ARTIFACT
    seed = output / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "train.csv", seed / "train.csv")
    if seed_source == "hyra-final":
        shutil.copy2(hyra_artifact, seed / "solution.json")
        shutil.copy2(hyra_artifact, seed / "hyra_final_artifact.json")
    else:
        (seed / "solution.json").write_text(
            '{"expression":"x1","params":{}}\n',
            encoding="utf-8",
        )
    solve_sh = seed / "solve.sh"
    solve_sh.write_text(
        "#!/bin/sh\nset -eu\ntest -f \"$1\"\n",
        encoding="utf-8",
    )
    solve_sh.chmod(0o755)
    adapter = Path(__file__).resolve().parent / "evaluate.py"
    write_task_spec(
        output / "task.json",
        {
            "name": "sunspot_symbolic_causal",
            "goal": (
                "Discover a compact symbolic autoregressive law that maximizes "
                "24-month free-running sunspot forecast skill. Use only train.csv; "
                "the evaluator owns later eras."
                + (
                    " The frozen seed is Tencent's published Hyra final artifact. "
                    "This is a final-artifact warm-start experiment, not autonomous "
                    "rediscovery."
                    if seed_source == "hyra-final"
                    else ""
                )
            ),
            "seed_workspace": "seed",
            "submission_relpath": "solution.json",
            "entrypoint_argv": [
                "/bin/sh",
                "{workspace}/solve.sh",
                "{submission}",
            ],
            "evaluator_argv": [
                evaluator_python,
                str(adapter),
                "--srfit",
                str(source / "srfit.py"),
                "--data",
                str(data_path),
                "--mode",
                "valid",
                "{submission}",
            ],
            "objective_key": "score",
            "direction": "maximize",
            "qualification_key": "qualified",
            "provenance": {
                "hyra_results_repository": HYRA_RESULTS_URL,
                "hyra_results_commit": HYRA_RESULTS_COMMIT,
                "seed_source": seed_source,
                "hyra_final_artifact": (
                    HYRA_SUNSPOT_ARTIFACT
                    if seed_source == "hyra-final"
                    else None
                ),
                "hyra_final_artifact_sha256": (
                    sha256_file(hyra_artifact)
                    if seed_source == "hyra-final"
                    else None
                ),
                "search_feedback_era": "1932-1978",
                "sealed_confirmation_era": "1979-2025",
                "contamination_warning": (
                    "Both eras are public historical data in 2026; this is a "
                    "causal filesystem split, not a model-pretraining holdout."
                ),
            },
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--hyra-root", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument(
        "--seed-source",
        choices=["init", "hyra-final"],
        default="init",
    )
    args = parser.parse_args()
    bootstrap(
        Path(args.output).resolve(),
        hyra_root=Path(args.hyra_root).resolve(),
        data_path=Path(args.data).resolve(),
        evaluator_python=str(Path(args.python).expanduser().absolute()),
        seed_source=args.seed_source,
    )


if __name__ == "__main__":
    main()
