"""Smoke test executed against an installed wheel from outside the checkout."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import benchmarks

from rehyra.loop import copy_preregistration_inputs


def main() -> None:
    benchmark_package = Path(benchmarks.__file__).resolve().parent
    assert not (benchmark_package / "solutions").exists()
    assert not (benchmark_package / "evidence").exists()

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        config = root / "config.toml"
        task = root / "task.json"
        run_dir = root / "run"
        config.write_text("[run]\nname = \"wheel-smoke\"\n", encoding="utf-8")
        task.write_text('{"name": "wheel-smoke"}\n', encoding="utf-8")

        copy_preregistration_inputs(
            config_path=config,
            task_path=task,
            run_dir=run_dir,
        )

        preregistration = json.loads(
            (run_dir / "preregistration.json").read_text(encoding="utf-8")
        )
        assert preregistration["harness"]["source_kind"] == (
            "installed_distribution"
        )
        assert (
            run_dir / "harness_source" / "distribution-metadata.json"
        ).is_file()
        assert (
            run_dir
            / "harness_source"
            / "benchmarks"
            / "tasks"
            / "simpletes"
            / "verify_sums_exact.cpp"
        ).is_file()
        assert not (run_dir / "harness_source" / "pyproject.toml").exists()


if __name__ == "__main__":
    main()
