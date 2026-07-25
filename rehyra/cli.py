from __future__ import annotations

import argparse
import asyncio
import json
import re
import secrets
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .providers import audit_agent_evidence
from .agents import make_backend
from .contracts import RunConfig, TaskSpec
from .loop import ReHyraLoop, copy_preregistration_inputs
from .task import TaskBundle, describe_task
from .util import (
    atomic_write_json,
    copy_workspace,
    evidence_manifest,
    sha256_file,
    workspace_manifest,
)


def _bootstrap_adderboard(args: argparse.Namespace) -> int:
    argv = [
        sys.executable,
        "-m",
        "benchmarks.tasks.adderboard.bootstrap",
        "--output",
        str(Path(args.output).resolve()),
        "--public-tests",
        str(args.public_tests),
        "--shadow-tests",
        str(args.shadow_tests),
        "--shadow-seeds",
        args.shadow_seeds,
    ]
    if args.python:
        argv.extend(["--python", args.python])
    return subprocess.run(argv, check=False).returncode


def _import_seed(
    *,
    source_run: Path,
    target_run: Path,
    target_task: TaskSpec,
    attempt_id: str | None,
) -> dict[str, object]:
    source_run = source_run.resolve()
    source_state_path = source_run / "run.json"
    source_preregistration_path = source_run / "preregistration.json"
    if not source_state_path.is_file() or not source_preregistration_path.is_file():
        raise RuntimeError("seed source is not a preregistered run")
    source_state = json.loads(source_state_path.read_text(encoding="utf-8"))
    if source_state.get("status") != "completed":
        raise RuntimeError("seed source run must be completed")
    if attempt_id is None:
        report_path = source_run / "report.json"
        if not report_path.is_file():
            raise RuntimeError("seed source run has no completed report")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        attempt_id = str(report.get("incumbent_attempt_id", ""))
    attempt_path = source_run / "attempts" / attempt_id / "attempt.json"
    workspace = source_run / "attempts" / attempt_id / "workspace"
    if (
        re.fullmatch(r"attempt-[0-9]+", attempt_id) is None
        or not attempt_path.is_file()
        or not workspace.is_dir()
        or workspace.is_symlink()
    ):
        raise RuntimeError("seed source attempt is missing or unsafe")
    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
    if (
        attempt.get("status") != "completed"
        or attempt.get("attempt_id") != attempt_id
    ):
        raise RuntimeError("seed source attempt is not completed")
    expected_manifest = attempt.get("workspace_manifest") or {}
    actual_manifest = workspace_manifest(workspace)
    if actual_manifest.get("tree_sha256") != expected_manifest.get("tree_sha256"):
        raise RuntimeError("seed source workspace hash mismatch")

    source_preregistration = json.loads(
        source_preregistration_path.read_text(encoding="utf-8")
    )
    source_task_item = source_preregistration.get("task") or {}
    source_frozen_task = source_run / str(source_task_item.get("frozen", ""))
    if (
        not source_frozen_task.is_file()
        or sha256_file(source_frozen_task) != source_task_item.get("sha256")
    ):
        raise RuntimeError("seed source frozen task is invalid")
    source_task_path = source_task_item.get("source")
    if not isinstance(source_task_path, str) or not source_task_path:
        raise RuntimeError("seed source task provenance is missing")
    source_task = TaskSpec.load(
        source_frozen_task,
        base_dir=Path(source_task_path).resolve().parent,
    )
    compatibility = (
        source_task.name,
        source_task.submission_relpath,
        source_task.objective_key,
        source_task.direction,
        source_task.qualification_key,
        source_task.evaluator_mode,
        source_task.evaluator_argv,
        source_task.evaluator_env,
        source_task.evaluator_path_prepend,
        source_task.evaluator_relpath,
        source_task.entrypoint_argv,
        source_task.state_capture_env,
        source_task.state_input_env,
        source_task.state_max_bytes,
        source_task.editable_start_marker,
        source_task.editable_end_marker,
        source_task.provenance,
        source_task.mutation_policy,
    )
    target_compatibility = (
        target_task.name,
        target_task.submission_relpath,
        target_task.objective_key,
        target_task.direction,
        target_task.qualification_key,
        target_task.evaluator_mode,
        target_task.evaluator_argv,
        target_task.evaluator_env,
        target_task.evaluator_path_prepend,
        target_task.evaluator_relpath,
        target_task.entrypoint_argv,
        target_task.state_capture_env,
        target_task.state_input_env,
        target_task.state_max_bytes,
        target_task.editable_start_marker,
        target_task.editable_end_marker,
        target_task.provenance,
        target_task.mutation_policy,
    )
    if compatibility != target_compatibility:
        raise RuntimeError("seed source task contract is incompatible")
    evaluation = attempt.get("evaluation") or {}
    if not evaluation.get(target_task.qualification_key, False):
        raise RuntimeError("seed source attempt is not qualified")
    if not (workspace / target_task.submission_relpath).is_file():
        raise RuntimeError("seed source submission is missing")

    snapshot = target_run / "imported_seed"
    if snapshot.exists():
        raise RuntimeError("seed import destination already exists")
    staging = target_run / f".imported-seed-{secrets.token_hex(8)}"
    try:
        copy_workspace(workspace, staging)
        snapshot_manifest = workspace_manifest(staging)
        if snapshot_manifest.get("tree_sha256") != expected_manifest.get(
            "tree_sha256"
        ):
            raise RuntimeError("seed import copy hash mismatch")
        staging.replace(snapshot)
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return {
        "source_run": str(source_run),
        "source_attempt_id": attempt_id,
        "source_preregistration_sha256": sha256_file(
            source_preregistration_path
        ),
        "source_attempt_sha256": sha256_file(attempt_path),
        "snapshot": "imported_seed",
        "manifest": snapshot_manifest,
    }


def _validated_imported_seed(
    *,
    run_dir: Path,
    preregistration: dict[str, object],
) -> Path | None:
    raw = preregistration.get("seed_import")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError("invalid seed import preregistration")
    relative = Path(str(raw.get("snapshot", "")))
    if relative.is_absolute() or ".." in relative.parts:
        raise RuntimeError("imported seed snapshot path is unsafe")
    snapshot = run_dir / relative
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise RuntimeError("imported seed snapshot is missing or unsafe")
    expected = raw.get("manifest") or {}
    actual = workspace_manifest(snapshot)
    if not isinstance(expected, dict) or actual.get("tree_sha256") != expected.get(
        "tree_sha256"
    ):
        raise RuntimeError("imported seed snapshot hash mismatch")
    return snapshot


def _task_with_frozen_harness_paths(
    task: TaskSpec,
    *,
    run_dir: Path,
    preregistration: dict[str, object],
) -> TaskSpec:
    harness = preregistration.get("harness")
    if not isinstance(harness, dict):
        return task
    raw_source_root = harness.get("source_root")
    raw_snapshot = harness.get("snapshot")
    if not isinstance(raw_source_root, str) or not isinstance(raw_snapshot, str):
        return task
    source_root = Path(raw_source_root).resolve()
    snapshot_relpath = Path(raw_snapshot)
    if snapshot_relpath.is_absolute() or ".." in snapshot_relpath.parts:
        raise RuntimeError("harness snapshot path is unsafe")
    snapshot = run_dir / snapshot_relpath
    if not snapshot.is_dir() or snapshot.is_symlink():
        raise RuntimeError("harness snapshot is missing or unsafe")

    def rebased(value: str) -> str:
        candidate = Path(value)
        if not candidate.is_absolute():
            return value
        try:
            relative = candidate.resolve().relative_to(source_root)
        except ValueError:
            return value
        if not relative.parts or relative.parts[0] not in {
            "rehyra",
            "benchmarks",
            "tasks",
        }:
            return value
        candidates = [snapshot / relative]
        if relative.parts[0] == "tasks":
            # v0.3.0 task bundles used the former top-level tasks/ layout.
            candidates.append(snapshot / "benchmarks" / relative)
        for frozen in candidates:
            if frozen.exists() and not frozen.is_symlink():
                return str(frozen)
        raise RuntimeError(
            f"harness-owned runtime path is absent from snapshot: {relative}"
        )

    return replace(
        task,
        evaluator_argv=tuple(rebased(item) for item in task.evaluator_argv),
        entrypoint_argv=tuple(rebased(item) for item in task.entrypoint_argv),
        evaluator_path_prepend=tuple(
            Path(rebased(str(item))) for item in task.evaluator_path_prepend
        ),
    )


def _run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    run_dir = Path(args.run_dir).resolve()
    preregistration = run_dir / "preregistration.json"
    seed_from = getattr(args, "seed_from", None)
    seed_attempt = getattr(args, "seed_attempt", None)
    if not preregistration.exists():
        source_config = RunConfig.load(config_path)
        copy_preregistration_inputs(
            config_path=config_path,
            task_path=source_config.task_spec,
            run_dir=run_dir,
        )
        if seed_from:
            source_task = TaskSpec.load(source_config.task_spec)
            frozen = json.loads(preregistration.read_text(encoding="utf-8"))
            frozen["seed_import_request"] = {
                "source_run": str(Path(seed_from).resolve()),
                "source_attempt_id": seed_attempt,
            }
            atomic_write_json(preregistration, frozen)
            frozen["seed_import"] = _import_seed(
                source_run=Path(seed_from),
                target_run=run_dir,
                target_task=source_task,
                attempt_id=seed_attempt,
            )
            frozen.pop("seed_import_request", None)
            atomic_write_json(preregistration, frozen)
    elif seed_from:
        frozen = json.loads(preregistration.read_text(encoding="utf-8"))
        existing_import = frozen.get("seed_import")
        pending_import = frozen.get("seed_import_request")
        import_record = existing_import or pending_import or {}
        if not isinstance(import_record, dict) or Path(
            str(import_record.get("source_run", ""))
        ).resolve() != Path(seed_from).resolve():
            raise RuntimeError("cannot change seed source after preregistration")
        existing_attempt = import_record.get("source_attempt_id")
        if seed_attempt and existing_attempt and existing_attempt != seed_attempt:
            raise RuntimeError("cannot change seed attempt after preregistration")
        if existing_import is None:
            frozen_task_path = run_dir / "task.frozen.json"
            task_item = frozen.get("task") or {}
            task_source = task_item.get("source")
            if not isinstance(task_source, str) or not task_source:
                raise RuntimeError("run preregistration has no task source")
            source_task = TaskSpec.load(
                frozen_task_path,
                base_dir=Path(task_source).resolve().parent,
            )
            frozen["seed_import"] = _import_seed(
                source_run=Path(seed_from),
                target_run=run_dir,
                target_task=source_task,
                attempt_id=seed_attempt or existing_attempt,
            )
            frozen.pop("seed_import_request", None)
            atomic_write_json(preregistration, frozen)
    else:
        frozen = json.loads(preregistration.read_text(encoding="utf-8"))
        if frozen.get("seed_import_request") is not None:
            raise RuntimeError(
                "seed import is incomplete; retry with the original --seed-from"
            )
    frozen_config = run_dir / "config.frozen.toml"
    frozen_task = run_dir / "task.frozen.json"
    if not frozen_config.is_file() or not frozen_task.is_file():
        raise RuntimeError("run preregistration is incomplete")
    frozen = json.loads(preregistration.read_text(encoding="utf-8"))
    for kind, path in (("config", frozen_config), ("task", frozen_task)):
        expected = (frozen.get(kind) or {}).get("sha256")
        if sha256_file(path) != expected:
            raise RuntimeError(f"frozen {kind} checksum mismatch")
    config = replace(
        RunConfig.load(frozen_config),
        task_spec=frozen_task.resolve(),
    )
    task_source = (frozen.get("task") or {}).get("source")
    if not isinstance(task_source, str) or not task_source:
        raise RuntimeError("run preregistration has no task source")
    task = TaskSpec.load(
        frozen_task,
        base_dir=Path(task_source).resolve().parent,
    )
    task = _task_with_frozen_harness_paths(
        task,
        run_dir=run_dir,
        preregistration=frozen,
    )
    imported_seed = _validated_imported_seed(
        run_dir=run_dir,
        preregistration=frozen,
    )
    if imported_seed is not None:
        task = replace(task, seed_workspace=imported_seed)
    TaskBundle(root=frozen_task.parent, spec_path=frozen_task, spec=task).validate()
    backend = make_backend(config.agent)
    runner = ReHyraLoop(
        config=config,
        task=task,
        backend=backend,
        run_dir=run_dir,
    )
    report = asyncio.run(runner.run())
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def _bootstrap_science(args: argparse.Namespace) -> int:
    argv = [
        sys.executable,
        "-m",
        "benchmarks.tasks.simpletes.bootstrap",
        "--output",
        str(Path(args.output).resolve()),
        "--tasks",
        args.tasks,
        "--trial-mode",
        args.trial_mode,
        "--seed-source",
        args.seed_source,
        "--metric-mode",
        args.metric_mode,
        "--python",
        args.python,
        "--qubit-total-budget-seconds",
        str(args.qubit_total_budget_seconds),
    ]
    return subprocess.run(argv, check=False).returncode


def _bootstrap_public_artifacts(args: argparse.Namespace) -> int:
    argv = [
        sys.executable,
        "-m",
        "benchmarks.tasks.hyra_artifacts.bootstrap",
        "--output",
        str(Path(args.output).resolve()),
    ]
    return subprocess.run(argv, check=False).returncode


def _replay_public_artifacts(args: argparse.Namespace) -> int:
    argv = [
        sys.executable,
        "-m",
        "benchmarks.tasks.hyra_artifacts.verify",
        "--hyra-root",
        str(Path(args.hyra_root).resolve()),
        "--simpletes-root",
        str(Path(args.simpletes_root).resolve()),
        "--sunspot-data",
        str(Path(args.sunspot_data).resolve()),
        "--adder-public-tests",
        str(args.adder_public_tests),
        "--adder-shadow-tests",
        str(args.adder_shadow_tests),
        "--adder-shadow-seeds",
        args.adder_shadow_seeds,
    ]
    if args.adder_verifier:
        argv.extend(
            ["--adder-verifier", str(Path(args.adder_verifier).resolve())]
        )
    if args.adder_python:
        argv.extend(["--adder-python", str(Path(args.adder_python).resolve())])
    if args.output:
        argv.extend(["--output", str(Path(args.output).resolve())])
    return subprocess.run(argv, check=False).returncode


def _describe_task(args: argparse.Namespace) -> int:
    print(
        json.dumps(
            describe_task(Path(args.task).resolve()),
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _status(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    state_path = run_dir / "run.json"
    if not state_path.exists():
        print(f"run does not exist: {run_dir}", file=sys.stderr)
        return 2
    state = json.loads(state_path.read_text(encoding="utf-8"))
    report_path = run_dir / "report.json"
    output = {"run": state}
    if report_path.exists():
        output["report"] = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        attempts = sorted((run_dir / "attempts").glob("attempt-*/attempt.json"))
        output["attempts_recorded"] = len(attempts)
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def _validate(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    failures: list[str] = []
    checked = 0
    evidence_root_sha256: str | None = None
    run_state: dict[str, object] = {}
    if not run_dir.is_dir() or run_dir.is_symlink():
        failures.append("run directory is missing or unsafe")
    preregistration_path = run_dir / "preregistration.json"
    if not preregistration_path.is_file():
        failures.append("run is missing preregistration.json")
    else:
        preregistration = json.loads(
            preregistration_path.read_text(encoding="utf-8")
        )
        for kind in ("config", "task"):
            item = preregistration.get(kind) or {}
            frozen = run_dir / str(item.get("frozen", ""))
            checked += 1
            if not frozen.is_file():
                failures.append(f"preregistration: missing frozen {kind}")
            elif sha256_file(frozen) != item.get("sha256"):
                failures.append(f"preregistration: {kind} hash mismatch")
        harness = preregistration.get("harness")
        if isinstance(harness, dict):
            checked += 1
            snapshot = run_dir / str(harness.get("snapshot", ""))
            expected_manifest = harness.get("manifest") or {}
            if not snapshot.is_dir() or snapshot.is_symlink():
                failures.append("preregistration: harness snapshot missing or unsafe")
            else:
                actual_manifest = workspace_manifest(snapshot)
                if actual_manifest.get("tree_sha256") != expected_manifest.get(
                    "tree_sha256"
                ):
                    failures.append(
                        "preregistration: harness source hash mismatch"
                    )
        try:
            imported_seed = _validated_imported_seed(
                run_dir=run_dir,
                preregistration=preregistration,
            )
        except RuntimeError as exc:
            failures.append(f"preregistration: {exc}")
        else:
            if imported_seed is not None:
                checked += 1
    state_path = run_dir / "run.json"
    if not state_path.is_file():
        failures.append("run is missing run.json")
    else:
        loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(loaded_state, dict):
            failures.append("run.json must contain an object")
        else:
            run_state = loaded_state
    attempt_metadata = sorted(
        (run_dir / "attempts").glob("attempt-*/attempt.json")
    )
    if not attempt_metadata:
        failures.append("run contains no attempt evidence")
    if run_state.get("status") == "completed":
        if not (run_dir / "report.json").is_file():
            failures.append("completed run is missing report.json")
        manifest_path = run_dir / "evidence_manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            failures.append("completed run is missing evidence_manifest.json")
        else:
            try:
                expected_manifest = json.loads(
                    manifest_path.read_text(encoding="utf-8")
                )
                actual_manifest = evidence_manifest(run_dir)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                failures.append(f"invalid evidence manifest: {exc}")
            else:
                checked += len(actual_manifest["files"])
                evidence_root_sha256 = str(
                    expected_manifest.get("root_sha256", "")
                )
                if expected_manifest != actual_manifest:
                    failures.append("evidence manifest does not match run contents")
                expected_root = getattr(args, "expected_root", None)
                if (
                    expected_root is not None
                    and evidence_root_sha256 != expected_root
                ):
                    failures.append(
                        "evidence root mismatch "
                        f"{expected_root} != {evidence_root_sha256}"
                    )
    agent_audit = audit_agent_evidence(run_dir)
    checked += int(agent_audit["request_count"])
    failures.extend(str(item) for item in agent_audit["violations"])
    for metadata_path in sorted((run_dir / "attempts").glob("attempt-*/attempt.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        expected = metadata.get("workspace_manifest")
        if not expected:
            failures.append(f"{metadata_path.parent.name}: no workspace manifest")
            continue
        actual = workspace_manifest(metadata_path.parent / "workspace")
        checked += 1
        if actual.get("tree_sha256") != expected.get("tree_sha256"):
            failures.append(
                f"{metadata_path.parent.name}: workspace hash mismatch "
                f"{expected.get('tree_sha256')} != {actual.get('tree_sha256')}"
            )
        projection = metadata.get("submission_projection")
        if projection is not None:
            checked += 2
            raw_name = Path(str(projection.get("raw_submission", "")))
            if (
                raw_name.is_absolute()
                or ".." in raw_name.parts
                or len(raw_name.parts) != 1
            ):
                failures.append(
                    f"{metadata_path.parent.name}: unsafe raw submission path"
                )
            else:
                raw_submission = metadata_path.parent / "agent" / raw_name
                if (
                    not raw_submission.is_file()
                    or raw_submission.is_symlink()
                ):
                    failures.append(
                        f"{metadata_path.parent.name}: raw submission missing or unsafe"
                    )
                elif sha256_file(raw_submission) != projection.get("raw_sha256"):
                    failures.append(
                        f"{metadata_path.parent.name}: raw submission hash mismatch"
                    )
            submission_relpath = Path(
                str(projection.get("submission_relpath", ""))
            )
            projected_submission = metadata_path.parent / "workspace" / submission_relpath
            if (
                submission_relpath.is_absolute()
                or ".." in submission_relpath.parts
                or not projected_submission.is_file()
                or projected_submission.is_symlink()
            ):
                failures.append(
                    f"{metadata_path.parent.name}: projected submission missing or unsafe"
                )
            elif sha256_file(projected_submission) != projection.get(
                "projected_sha256"
            ):
                failures.append(
                    f"{metadata_path.parent.name}: projected submission hash mismatch"
                )
        state_descriptors = {
            "captured_state": (metadata.get("evaluation") or {}).get(
                "captured_state"
            ),
            "input_state": metadata.get("input_state"),
            "effective_state": metadata.get("effective_state"),
        }
        for label, descriptor in state_descriptors.items():
            if descriptor is None:
                continue
            checked += 1
            if not isinstance(descriptor, dict):
                failures.append(
                    f"{metadata_path.parent.name}: invalid {label} descriptor"
                )
                continue
            relative = Path(str(descriptor.get("path", "")))
            if relative.is_absolute() or ".." in relative.parts:
                failures.append(
                    f"{metadata_path.parent.name}: unsafe {label} path"
                )
                continue
            artifact = run_dir / relative
            if not artifact.is_file() or artifact.is_symlink():
                failures.append(
                    f"{metadata_path.parent.name}: missing or unsafe {label}"
                )
                continue
            if sha256_file(artifact) != descriptor.get("sha256"):
                failures.append(
                    f"{metadata_path.parent.name}: {label} hash mismatch"
                )
            if artifact.stat().st_size != descriptor.get("size"):
                failures.append(
                    f"{metadata_path.parent.name}: {label} size mismatch"
                )
    for metadata_path in sorted(
        (run_dir / "evaluators").glob("evaluator-*/version.json")
    ):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        evaluator_relpath = str(
            ((metadata.get("architect") or {}).get("output") or {}).get(
                "evaluator_relpath",
                "evaluator.py",
            )
        )
        evaluator = (
            metadata_path.parent
            / "workspace"
            / evaluator_relpath
        )
        checked += 1
        if not evaluator.is_file():
            failures.append(f"{metadata_path.parent.name}: evaluator missing")
            continue
        actual_hash = sha256_file(evaluator)
        expected_hash = metadata.get("evaluator_sha256")
        if actual_hash != expected_hash:
            failures.append(
                f"{metadata_path.parent.name}: evaluator hash mismatch "
                f"{expected_hash} != {actual_hash}"
            )
        expected_workspace = metadata.get("workspace_manifest") or {}
        actual_workspace = workspace_manifest(metadata_path.parent / "workspace")
        if actual_workspace.get("tree_sha256") != expected_workspace.get(
            "tree_sha256"
        ):
            failures.append(
                f"{metadata_path.parent.name}: evaluator workspace hash mismatch"
            )
        replay = metadata.get("replay")
        if isinstance(replay, dict) and metadata.get("status") == "promoted":
            replay_root = run_dir / "evaluator_replays" / metadata_path.parent.name
            for attempt_id, expected_result in replay.items():
                replay_path = replay_root / f"{attempt_id}.json"
                if not replay_path.is_file():
                    failures.append(
                        f"{metadata_path.parent.name}: replay missing {attempt_id}"
                    )
                    continue
                actual_result = json.loads(replay_path.read_text(encoding="utf-8"))
                if actual_result != expected_result:
                    failures.append(
                        f"{metadata_path.parent.name}: replay mismatch {attempt_id}"
                    )
    result = {
        "ok": not failures,
        "checked_artifacts": checked,
        "evidence_root_sha256": evidence_root_sha256,
        "agent_evidence": agent_audit,
        "failures": failures,
    }
    print(json.dumps(result, indent=2))
    return 0 if not failures else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rehyra",
        description=(
            "ReHyra: auditable public-spec implementation "
            "of the Hyra research loop"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    bootstrap = subparsers.add_parser(
        "bootstrap-adderboard",
        help="fetch and freeze the low-cost AdderBoard task",
    )
    bootstrap.add_argument("--output", required=True)
    bootstrap.add_argument("--python", default="")
    bootstrap.add_argument("--public-tests", type=int, default=500)
    bootstrap.add_argument("--shadow-tests", type=int, default=500)
    bootstrap.add_argument("--shadow-seeds", default="100003,100019")
    bootstrap.set_defaults(func=_bootstrap_adderboard)

    science = subparsers.add_parser(
        "bootstrap-science",
        help="fetch and freeze the low-cost SimpleTES science tasks",
    )
    science.add_argument("--output", required=True)
    science.add_argument(
        "--tasks",
        default=(
            "autocorrelation_first,autocorrelation_second,"
            "erdos_min_overlap,sums_diffs,qubit_routing"
        ),
    )
    science.add_argument("--trial-mode", choices=["fast", "full"], default="fast")
    science.add_argument(
        "--seed-source",
        choices=["predecessor", "init"],
        default="predecessor",
    )
    science.add_argument(
        "--metric-mode",
        choices=["auto", "upstream", "public-compatible"],
        default="auto",
    )
    science.add_argument("--python", default=sys.executable)
    science.add_argument(
        "--qubit-total-budget-seconds",
        type=int,
        default=1200,
        help="hardware-adjustable wall-time budget; full trial counts stay fixed",
    )
    science.set_defaults(func=_bootstrap_science)

    artifacts = subparsers.add_parser(
        "bootstrap-public-artifacts",
        help="fetch the pinned public Hyra result artifacts and Sunspot data",
    )
    artifacts.add_argument("--output", required=True)
    artifacts.set_defaults(func=_bootstrap_public_artifacts)

    replay = subparsers.add_parser(
        "replay-public-artifacts",
        help="independently replay the frozen low-cost public artifacts",
    )
    replay.add_argument("--hyra-root", required=True)
    replay.add_argument("--simpletes-root", required=True)
    replay.add_argument("--sunspot-data", required=True)
    replay.add_argument("--adder-verifier")
    replay.add_argument("--adder-python")
    replay.add_argument("--adder-public-tests", type=int, default=10_000)
    replay.add_argument("--adder-shadow-tests", type=int, default=10_000)
    replay.add_argument("--adder-shadow-seeds", default="100003,100019")
    replay.add_argument("--output")
    replay.set_defaults(func=_replay_public_artifacts)

    describe = subparsers.add_parser(
        "describe-task",
        help="validate and show a provider-independent task bundle",
    )
    describe.add_argument("task")
    describe.set_defaults(func=_describe_task)

    run = subparsers.add_parser("run", help="run an asynchronous ReHyra campaign")
    run.add_argument("config")
    run.add_argument("--run-dir", required=True)
    run.add_argument(
        "--seed-from",
        help="immutable continuation from a completed compatible run",
    )
    run.add_argument(
        "--seed-attempt",
        help="completed source attempt (defaults to the source incumbent)",
    )
    run.set_defaults(func=_run)

    status = subparsers.add_parser("status", help="show run state and result")
    status.add_argument("run_dir")
    status.set_defaults(func=_status)

    validate = subparsers.add_parser(
        "validate",
        help="verify a run and its sealed evidence manifest",
    )
    validate.add_argument("run_dir")
    validate.add_argument(
        "--expected-root",
        help="previously published evidence root SHA-256",
    )
    validate.set_defaults(func=_validate)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    raise SystemExit(args.func(args))
