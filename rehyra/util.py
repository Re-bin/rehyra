from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def append_jsonl(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(value, sort_keys=True, ensure_ascii=False) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def workspace_manifest(root: Path) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if rel.startswith(".git/") or "/__pycache__/" in f"/{rel}/":
            continue
        if path.is_symlink():
            entries.append(
                {
                    "path": rel,
                    "unsafe": "symlink",
                    "target": os.readlink(path),
                }
            )
            continue
        if not path.is_file():
            continue
        stat = path.stat()
        entries.append(
            {
                "path": rel,
                "size": stat.st_size,
                "sha256": sha256_file(path),
            }
        )
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "algorithm": "sha256",
        "files": entries,
        "tree_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def evidence_manifest(root: Path) -> dict[str, Any]:
    if not root.is_dir() or root.is_symlink():
        raise ValueError("evidence root is missing or unsafe")
    entries: list[dict[str, Any]] = []
    ignored_parts = {"__pycache__", ".tmp"}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        rel = relative.as_posix()
        if rel == "evidence_manifest.json" or any(
            part in ignored_parts for part in relative.parts
        ):
            continue
        if path.is_symlink():
            raise ValueError(f"evidence contains symbolic link: {rel}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ValueError(f"evidence contains special file: {rel}")
        stat = path.stat()
        entries.append(
            {
                "path": rel,
                "size": stat.st_size,
                "sha256": sha256_file(path),
            }
        )
    encoded = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
    return {
        "algorithm": "sha256",
        "files": entries,
        "root_sha256": hashlib.sha256(encoded).hexdigest(),
    }


def write_evidence_manifest(root: Path) -> dict[str, Any]:
    manifest = evidence_manifest(root)
    atomic_write_json(root / "evidence_manifest.json", manifest)
    return manifest


def workspace_safety_violations(root: Path) -> list[str]:
    violations: list[str] = []
    if not root.is_dir() or root.is_symlink():
        return ["workspace root is missing or unsafe"]
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            violations.append(f"{relative}: symbolic links are forbidden")
        elif not path.is_file() and not path.is_dir():
            violations.append(f"{relative}: special filesystem entry is forbidden")
    return violations


def copy_workspace(source: Path, destination: Path) -> None:
    if destination.exists():
        raise FileExistsError(destination)
    violations = workspace_safety_violations(source)
    if violations:
        raise ValueError("unsafe source workspace: " + "; ".join(violations))

    def ignored(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name in {".git", "__pycache__"}
        }

    shutil.copytree(source, destination, ignore=ignored)


def render_argv(
    argv: Iterable[str],
    *,
    workspace: Path,
    submission: Path,
    run_dir: Path,
    evaluator: Path | None = None,
) -> list[str]:
    replacements = {
        "{workspace}": str(workspace),
        "{submission}": str(submission),
        "{run_dir}": str(run_dir),
        "{evaluator}": str(evaluator) if evaluator is not None else "",
    }
    rendered: list[str] = []
    for item in argv:
        value = item
        for placeholder, replacement in replacements.items():
            value = value.replace(placeholder, replacement)
        rendered.append(value)
    return rendered
