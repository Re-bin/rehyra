from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

from rehyra.task import write_task_spec
from rehyra.util import sha256_file


ADDBOARD_URL = "https://github.com/anadim/AdderBoard.git"
ADDBOARD_COMMIT = "07061de8d833d04e9800a009721731586e102aef"
M10S_URL = "https://github.com/tbukic/M10S-Transformer.git"
M10S_COMMIT = "a05e46ed449e064081ea9f40a3a132b798ac965d"
HYRA_RESULTS_URL = "https://github.com/Tencent-Hunyuan/hyra-results.git"
HYRA_RESULTS_COMMIT = "bcda2e29200fbeaeb1620acca0670cc445e8688f"
HYRA_ADDER_ARTIFACT = "AI4Science/smallest_adder/solution.json"


def run(argv: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(argv, cwd=cwd, check=True)


def clone_at(url: str, commit: str, destination: Path) -> None:
    if destination.exists():
        actual = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        if actual != commit:
            raise RuntimeError(
                f"{destination} exists at {actual}, expected pinned {commit}"
            )
        return
    run(["git", "clone", "--quiet", url, str(destination)])
    run(["git", "-C", str(destination), "checkout", "--quiet", commit])


def find_torch_python(explicit: str) -> str:
    candidates = [
        explicit,
        sys.executable,
        shutil.which("python3") or "",
    ]
    seen: set[str] = set()
    for value in candidates:
        if not value:
            continue
        path = str(Path(value).expanduser())
        if path in seen:
            continue
        seen.add(path)
        completed = subprocess.run(
            [path, "-c", "import torch; print(torch.__version__)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if completed.returncode == 0:
            return path
    raise RuntimeError("no Python interpreter with torch is available")


def copy_seed(source: Path, destination: Path) -> None:
    if destination.exists():
        shutil.rmtree(destination)

    def ignored(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", "__pycache__", ".pytest_cache"}
        }

    shutil.copytree(source, destination, ignore=ignored)
    shutil.copy2(
        destination / "submissions" / "submission_36p.py",
        destination / "submissions" / "submission.py",
    )


def load_hyra_final_submission(artifact: Path) -> str:
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    submission = payload.get("submission_py")
    if not isinstance(submission, str) or not submission.strip():
        raise ValueError("Hyra adder artifact must contain non-empty submission_py")
    return submission


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--python", default="")
    parser.add_argument("--public-tests", type=int, default=500)
    parser.add_argument("--shadow-tests", type=int, default=500)
    parser.add_argument("--shadow-seeds", default="100003,100019")
    parser.add_argument(
        "--seed-source",
        choices=["predecessor", "hyra-final"],
        default="predecessor",
    )
    parser.add_argument(
        "--hyra-root",
        help="path to the pinned hyra-results checkout for --seed-source hyra-final",
    )
    args = parser.parse_args()

    output = Path(args.output).resolve()
    sources = output / "sources"
    sources.mkdir(parents=True, exist_ok=True)
    adderboard = sources / "AdderBoard"
    m10s = sources / "M10S-Transformer"
    clone_at(ADDBOARD_URL, ADDBOARD_COMMIT, adderboard)
    clone_at(M10S_URL, M10S_COMMIT, m10s)
    seed_workspace = output / "seed-workspace"
    copy_seed(m10s, seed_workspace)
    hyra_artifact = None
    if args.seed_source == "hyra-final":
        if args.hyra_root is None:
            raise ValueError("--hyra-root is required for a Hyra final seed")
        hyra_root = Path(args.hyra_root).expanduser().resolve()
        actual_hyra_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=hyra_root,
            text=True,
        ).strip()
        if actual_hyra_commit != HYRA_RESULTS_COMMIT:
            raise RuntimeError(
                f"Hyra results pin mismatch: {actual_hyra_commit}"
            )
        hyra_artifact = hyra_root / HYRA_ADDER_ARTIFACT
        submission = seed_workspace / "submissions" / "submission.py"
        submission.write_text(
            load_hyra_final_submission(hyra_artifact),
            encoding="utf-8",
        )
        shutil.copy2(
            hyra_artifact,
            seed_workspace / "hyra_final_artifact.json",
        )
    solve_sh = seed_workspace / "solve.sh"
    solve_sh.write_text(
        "#!/bin/sh\nset -eu\ntest -f \"$1\"\n",
        encoding="utf-8",
    )
    solve_sh.chmod(0o755)
    python = find_torch_python(args.python)
    evaluator = Path(__file__).resolve().parent / "evaluate.py"
    task = {
        "name": "adderboard-trained",
        "goal": (
            "Produce the smallest genuinely autoregressive trained transformer that "
            "adds two integers in [0, 9,999,999,999] with at least 99% accuracy. "
            "The training algorithm must remain generic; metadata must match the "
            "actual unique trainable parameter count; direct Python arithmetic on "
            "the input integers is forbidden."
            + (
                "\n\nThe frozen seed is Tencent's published Hyra final artifact. "
                "This is a final-artifact warm-start experiment: preserve or "
                "improve the validated incumbent. It is not autonomous rediscovery."
                if args.seed_source == "hyra-final"
                else ""
            )
        ),
        "seed_workspace": "seed-workspace",
        "submission_relpath": "submissions/submission.py",
        "entrypoint_argv": [
            "/bin/sh",
            "{workspace}/solve.sh",
            "{submission}",
        ],
        "evaluator_argv": [
            python,
            str(evaluator),
            "--verifier",
            str(adderboard / "verify.py"),
            "--submission",
            "{submission}",
            "--public-tests",
            str(args.public_tests),
            "--shadow-tests",
            str(args.shadow_tests),
            "--shadow-seeds",
            args.shadow_seeds,
        ],
        "objective_key": "actual_parameters",
        "direction": "minimize",
        "qualification_key": "qualified",
        "provenance": {
            "adderboard_url": ADDBOARD_URL,
            "adderboard_commit": ADDBOARD_COMMIT,
            "m10s_url": M10S_URL,
            "m10s_commit": M10S_COMMIT,
            "seed_submission": "submissions/submission_36p.py",
            "seed_source": args.seed_source,
            "category": "trained",
            "hyra_final_artifact": (
                HYRA_ADDER_ARTIFACT
                if hyra_artifact is not None
                else None
            ),
            "hyra_final_artifact_repository": (
                HYRA_RESULTS_URL
                if hyra_artifact is not None
                else None
            ),
            "hyra_final_artifact_commit": (
                HYRA_RESULTS_COMMIT
                if hyra_artifact is not None
                else None
            ),
            "hyra_final_artifact_sha256": (
                sha256_file(hyra_artifact)
                if hyra_artifact is not None
                else None
            ),
            "upstream_verifier_license_note": (
                "AdderBoard has no repository LICENSE; fetched at bootstrap and "
                "not redistributed by this repository."
            ),
            "seed_license": "MIT",
        },
    }
    task_path = output / "task.json"
    write_task_spec(task_path, task)
    provenance_path = output / "PROVENANCE.json"
    provenance_path.write_text(
        json.dumps(task["provenance"], indent=2) + "\n",
        encoding="utf-8",
    )
    print(task_path)


if __name__ == "__main__":
    main()
