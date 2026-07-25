#!/usr/bin/env python3
"""Thin Codex Adapter for the ReHyra runtime."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Iterable


ADAPTER_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = ADAPTER_ROOT.parent
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from rehyra.util import evidence_manifest, workspace_manifest  # noqa: E402


PLUGIN_VERSION = "0.3.0"
DEFAULT_EXCLUDED_PARTS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".rehyra",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_BYTES = 256 * 1024 * 1024


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    if not normalized:
        raise ValueError("name must contain at least one letter or digit")
    return normalized[:64]


def _safe_relative(raw: str, *, label: str) -> Path:
    path = Path(raw)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError(f"{label} must be a safe relative path")
    return path


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _git_output(workspace: Path, *args: str) -> str | None:
    if shutil.which("git") is None:
        return None
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=30,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _git_workspace_files(workspace: Path) -> list[Path] | None:
    if shutil.which("git") is None:
        return None
    result = subprocess.run(
        [
            "git",
            "-C",
            str(workspace),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    if result.returncode != 0:
        return None
    return [
        Path(os.fsdecode(item))
        for item in result.stdout.split(b"\0")
        if item
    ]


def _excluded(path: Path, extra: tuple[Path, ...]) -> bool:
    if any(part in DEFAULT_EXCLUDED_PARTS for part in path.parts):
        return True
    return any(path == item or item in path.parents for item in extra)


def _workspace_files(
    workspace: Path,
    *,
    excludes: tuple[Path, ...],
) -> list[Path]:
    tracked = _git_workspace_files(workspace)
    if tracked is not None:
        candidates = tracked
    else:
        candidates = [
            path.relative_to(workspace)
            for path in workspace.rglob("*")
            if path.is_file() or path.is_symlink()
        ]

    files: list[Path] = []
    for relative in sorted(set(candidates)):
        if _excluded(relative, excludes):
            continue
        source = workspace / relative
        if source.is_symlink():
            raise ValueError(
                f"source workspace contains unsupported symlink: {relative}"
            )
        if source.is_file():
            files.append(relative)
    return files


def _snapshot_workspace(
    workspace: Path,
    destination: Path,
    *,
    excludes: tuple[Path, ...],
    max_files: int,
    max_bytes: int,
) -> dict[str, object]:
    files = _workspace_files(workspace, excludes=excludes)
    if not files:
        raise ValueError("source workspace contains no eligible files")
    if len(files) > max_files:
        raise ValueError(
            f"source workspace has {len(files)} files; limit is {max_files}"
        )

    total_bytes = 0
    for relative in files:
        total_bytes += (workspace / relative).stat().st_size
        if total_bytes > max_bytes:
            raise ValueError(
                f"source workspace exceeds the {max_bytes}-byte snapshot limit"
            )

    destination.mkdir(parents=True)
    for relative in files:
        source = workspace / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    manifest = workspace_manifest(destination)
    manifest["source_file_count"] = len(files)
    manifest["source_total_bytes"] = total_bytes
    return manifest


def _run_toml(
    *,
    name: str,
    max_proposals: int,
    max_concurrency: int,
    model: str,
    reasoning_effort: str,
    context_timeout_seconds: int,
    proposal_timeout_seconds: int,
) -> str:
    concurrency = min(max_concurrency, max_proposals)
    return "\n".join(
        [
            "[run]",
            f"name = {_json_string(name)}",
            'task_spec = "task.json"',
            f"max_proposals = {max_proposals}",
            f"max_concurrency = {concurrency}",
            f"queue_target = {concurrency}",
            f"context_batch_size = {concurrency}",
            "evaluator_cycles = 1",
            "",
            "[agent]",
            'backend = "codex"',
            f"model = {_json_string(model)}",
            f"reasoning_effort = {_json_string(reasoning_effort)}",
            f"context_timeout_seconds = {context_timeout_seconds}",
            f"proposal_timeout_seconds = {proposal_timeout_seconds}",
            "",
        ]
    )


def _command_doctor(_args: argparse.Namespace) -> int:
    checks = {
        "python_3_11_or_newer": sys.version_info >= (3, 11),
        "plugin_manifest": (
            PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
        ).is_file(),
        "rehyra_runtime": (PLUGIN_ROOT / "rehyra" / "cli.py").is_file(),
        "codex_cli": shutil.which("codex") is not None,
    }
    codex_version = None
    if checks["codex_cli"]:
        result = subprocess.run(
            ["codex", "--version"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
        codex_version = result.stdout.strip()
        checks["codex_cli"] = result.returncode == 0
    result = {
        "ok": all(checks.values()),
        "plugin_root": str(PLUGIN_ROOT),
        "adapter_root": str(ADAPTER_ROOT),
        "plugin_version": PLUGIN_VERSION,
        "python": sys.executable,
        "codex_version": codex_version,
        "checks": checks,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result["ok"] else 1


def _command_prepare(args: argparse.Namespace) -> int:
    workspace = Path(args.workspace).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    if not workspace.is_dir() or workspace.is_symlink():
        raise ValueError("workspace must be a real directory")
    if _is_relative_to(output, workspace):
        raise ValueError("experiment output must be outside the source workspace")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"experiment output already exists: {output}")
    if args.max_proposals <= 0 or args.max_concurrency <= 0:
        raise ValueError("proposal budget and concurrency must be positive")
    if args.max_files <= 0 or args.max_bytes <= 0:
        raise ValueError("snapshot limits must be positive")

    submission = _safe_relative(args.submission, label="submission")
    if not (workspace / submission).is_file():
        raise ValueError(f"submission does not exist: {submission}")
    excludes = tuple(
        _safe_relative(value, label="exclude")
        for value in args.exclude
    )

    evaluator_argv: list[str] = []
    if args.evaluator_mode == "fixed":
        if not args.evaluator_program:
            raise ValueError("fixed evaluator requires --evaluator-program")
        evaluator_argv = [
            args.evaluator_program,
            *args.evaluator_arg,
        ]
        if not any(
            "{submission}" in item or "{workspace}" in item
            for item in evaluator_argv
        ):
            raise ValueError(
                "fixed evaluator must reference {submission} or {workspace}"
            )
    elif args.evaluator_program or args.evaluator_arg:
        raise ValueError(
            "generated evaluator cannot use fixed evaluator arguments"
        )

    entrypoint_argv: list[str] = []
    if args.entrypoint_program:
        entrypoint_argv = [
            args.entrypoint_program,
            *args.entrypoint_arg,
        ]
    elif args.entrypoint_arg:
        raise ValueError("entrypoint arguments require --entrypoint-program")

    name = _slug(args.name)
    temporary = output.with_name(
        f".{output.name}.tmp-{uuid.uuid4().hex}"
    )
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshot = _snapshot_workspace(
            workspace,
            temporary / "seed",
            excludes=excludes,
            max_files=args.max_files,
            max_bytes=args.max_bytes,
        )
        task: dict[str, object] = {
            "name": name,
            "goal": args.goal,
            "seed_workspace": "seed",
            "submission_relpath": submission.as_posix(),
            "mutation_policy": "submission_only",
            "evaluator_argv": evaluator_argv,
            "evaluator_mode": args.evaluator_mode,
            "objective_key": args.objective_key,
            "direction": args.direction,
            "qualification_key": args.qualification_key,
            "provenance": {
                "created_by": "rehyra-codex-plugin",
                "plugin_version": PLUGIN_VERSION,
                "source_workspace": str(workspace),
                "source_git_commit": _git_output(
                    workspace,
                    "rev-parse",
                    "HEAD",
                ),
                "source_git_dirty": bool(
                    _git_output(
                        workspace,
                        "status",
                        "--porcelain",
                        "--untracked-files=normal",
                    )
                ),
                "snapshot_tree_sha256": snapshot["tree_sha256"],
            },
        }
        if entrypoint_argv:
            task["entrypoint_argv"] = entrypoint_argv
        if args.editable_start_marker or args.editable_end_marker:
            if not (
                args.editable_start_marker and args.editable_end_marker
            ):
                raise ValueError(
                    "both editable-region markers must be provided"
                )
            task["editable_region"] = {
                "start_marker": args.editable_start_marker,
                "end_marker": args.editable_end_marker,
            }

        (temporary / "task.json").write_text(
            json.dumps(
                task,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        (temporary / "run.toml").write_text(
            _run_toml(
                name=name,
                max_proposals=args.max_proposals,
                max_concurrency=args.max_concurrency,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                context_timeout_seconds=args.context_timeout_seconds,
                proposal_timeout_seconds=args.proposal_timeout_seconds,
            ),
            encoding="utf-8",
        )
        (temporary / "snapshot.json").write_text(
            json.dumps(
                snapshot,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, output)
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise

    result = {
        "ok": True,
        "experiment": str(output),
        "task": str(output / "task.json"),
        "config": str(output / "run.toml"),
        "run_dir": str(output / "run"),
        "snapshot": {
            "files": snapshot["source_file_count"],
            "bytes": snapshot["source_total_bytes"],
            "tree_sha256": snapshot["tree_sha256"],
        },
        "commands": {
            "describe": [
                sys.executable,
                str(Path(__file__).resolve()),
                "cli",
                "describe-task",
                str(output / "task.json"),
            ],
            "run": [
                sys.executable,
                str(Path(__file__).resolve()),
                "cli",
                "run",
                str(output / "run.toml"),
                "--run-dir",
                str(output / "run"),
            ],
        },
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _command_cli(args: argparse.Namespace) -> int:
    if not args.rehyra_args:
        raise ValueError("cli requires ReHyra arguments")
    from rehyra.cli import main

    sys.argv = ["rehyra", *args.rehyra_args]
    main()
    return 0


def _command_best(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    report_path = run_dir / "report.json"
    task_path = run_dir / "task.frozen.json"
    if not report_path.is_file():
        raise ValueError("run has no completed report.json")
    if not task_path.is_file():
        raise ValueError("run has no frozen task contract")
    manifest_path = run_dir / "evidence_manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        raise ValueError("run has no sealed evidence manifest")
    expected_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual_manifest = evidence_manifest(run_dir)
    if expected_manifest != actual_manifest:
        raise ValueError("run evidence manifest does not match its contents")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    task = json.loads(task_path.read_text(encoding="utf-8"))
    attempt_id = str(report.get("incumbent_attempt_id", ""))
    if not re.fullmatch(r"attempt-\d{4}", attempt_id):
        raise ValueError("report has no valid incumbent attempt")
    workspace = run_dir / "attempts" / attempt_id / "workspace"
    submission_relpath = _safe_relative(
        str(task["submission_relpath"]),
        label="frozen submission",
    )
    submission = workspace / submission_relpath
    if not workspace.is_dir() or not submission.is_file():
        raise ValueError("winning workspace or submission is missing")
    result = {
        "ok": True,
        "attempt_id": attempt_id,
        "workspace": str(workspace),
        "submission": str(submission),
        "evaluation": report.get("incumbent_evaluation"),
        "proposal_count": report.get("proposal_count"),
        "terminal_reason": report.get("terminal_reason"),
        "evidence_root_sha256": expected_manifest.get("root_sha256"),
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ReHyra Codex plugin bridge",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser(
        "doctor",
        help="check the bundled runtime and local Codex CLI",
    )
    doctor.set_defaults(func=_command_doctor)

    prepare = subparsers.add_parser(
        "prepare",
        help="freeze a source workspace and create a ReHyra experiment",
    )
    prepare.add_argument("--workspace", default=".")
    prepare.add_argument("--output", required=True)
    prepare.add_argument("--name", required=True)
    prepare.add_argument("--goal", required=True)
    prepare.add_argument("--submission", required=True)
    prepare.add_argument("--objective-key", required=True)
    prepare.add_argument(
        "--direction",
        choices=["minimize", "maximize"],
        required=True,
    )
    prepare.add_argument("--qualification-key", default="qualified")
    prepare.add_argument(
        "--evaluator-mode",
        choices=["fixed", "generated"],
        default="fixed",
    )
    prepare.add_argument("--evaluator-program")
    prepare.add_argument("--evaluator-arg", action="append", default=[])
    prepare.add_argument("--entrypoint-program")
    prepare.add_argument("--entrypoint-arg", action="append", default=[])
    prepare.add_argument("--editable-start-marker")
    prepare.add_argument("--editable-end-marker")
    prepare.add_argument("--max-proposals", type=int, default=4)
    prepare.add_argument("--max-concurrency", type=int, default=2)
    prepare.add_argument("--model", default="gpt-5.6-sol")
    prepare.add_argument("--reasoning-effort", default="high")
    prepare.add_argument("--context-timeout-seconds", type=int, default=900)
    prepare.add_argument("--proposal-timeout-seconds", type=int, default=1800)
    prepare.add_argument("--exclude", action="append", default=[])
    prepare.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    prepare.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    prepare.set_defaults(func=_command_prepare)

    cli = subparsers.add_parser(
        "cli",
        help="run the bundled ReHyra CLI",
    )
    cli.add_argument("rehyra_args", nargs=argparse.REMAINDER)
    cli.set_defaults(func=_command_cli)

    best = subparsers.add_parser(
        "best",
        help="resolve the winning workspace and submission",
    )
    best.add_argument("run_dir")
    best.set_defaults(func=_command_best)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (FileExistsError, KeyError, OSError, ValueError) as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                },
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
