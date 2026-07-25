from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any

from .execution import ExecutionBackend, ExecutionRequest, LocalProcessBackend
from .contracts import TaskSpec
from .util import render_argv, sha256_file, workspace_safety_violations


def validate_evaluation_result(
    task: TaskSpec,
    result: dict[str, Any],
) -> dict[str, Any]:
    qualification = result.get(task.qualification_key)
    if not isinstance(qualification, bool):
        result[task.qualification_key] = False
        result["error"] = (
            f"evaluator field {task.qualification_key!r} must be boolean"
        )
        return result
    if "error" in result:
        result[task.qualification_key] = False
        return result
    if not qualification:
        return result
    objective = result.get(task.objective_key)
    if (
        not isinstance(objective, (int, float))
        or isinstance(objective, bool)
        or not math.isfinite(float(objective))
    ):
        result[task.qualification_key] = False
        result["error"] = (
            f"qualified evaluator result requires finite numeric "
            f"{task.objective_key!r}"
        )
    return result


async def evaluate(
    task: TaskSpec,
    *,
    workspace: Path,
    run_dir: Path,
    output_dir: Path,
    timeout_seconds: int = 1800,
    execution_backend: ExecutionBackend | None = None,
    evaluator_path: Path | None = None,
    input_state_path: Path | None = None,
) -> dict[str, Any]:
    submission = workspace / task.submission_relpath
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_violations = workspace_safety_violations(workspace)
    if workspace_violations:
        return {
            task.qualification_key: False,
            "error": "unsafe solution workspace: "
            + "; ".join(workspace_violations),
        }
    backend = execution_backend or LocalProcessBackend()
    if task.entrypoint_argv:
        entrypoint_argv = render_argv(
            task.entrypoint_argv,
            workspace=workspace,
            submission=submission,
            run_dir=run_dir,
            evaluator=evaluator_path,
        )
        entrypoint = await backend.run(
            ExecutionRequest(
                argv=tuple(entrypoint_argv),
                cwd=workspace,
                timeout_seconds=timeout_seconds,
            )
        )
        (output_dir / "solution.stdout.log").write_bytes(entrypoint.stdout)
        (output_dir / "solution.stderr.log").write_bytes(entrypoint.stderr)
        if entrypoint.timed_out:
            return {
                task.qualification_key: False,
                "error": f"solution entrypoint timed out after {timeout_seconds}s",
                "duration_seconds": entrypoint.duration_seconds,
                "isolation": entrypoint.attestation.as_dict(),
            }
        if entrypoint.returncode != 0:
            return {
                task.qualification_key: False,
                "error": f"solution entrypoint exited {entrypoint.returncode}",
                "duration_seconds": entrypoint.duration_seconds,
                "isolation": entrypoint.attestation.as_dict(),
            }
    if not submission.exists():
        return {
            task.qualification_key: False,
            "error": f"submission missing: {task.submission_relpath}",
        }
    argv = render_argv(
        task.evaluator_argv,
        workspace=workspace,
        submission=submission,
        run_dir=run_dir,
        evaluator=evaluator_path,
    )
    evaluator_env = dict(task.evaluator_env)
    if task.evaluator_path_prepend:
        inherited_path = evaluator_env.get("PATH", os.environ.get("PATH", ""))
        path_parts = [str(path) for path in task.evaluator_path_prepend]
        if inherited_path:
            path_parts.append(inherited_path)
        evaluator_env["PATH"] = os.pathsep.join(path_parts)
    captured_state_path: Path | None = None
    if task.state_capture_env is not None:
        captured_state_path = output_dir / "captured_state.json"
        if captured_state_path.is_symlink() or captured_state_path.exists():
            captured_state_path.unlink()
        evaluator_env[task.state_capture_env] = str(captured_state_path)
        if input_state_path is not None:
            if not input_state_path.is_file() or input_state_path.is_symlink():
                return {
                    task.qualification_key: False,
                    "error": "runtime input state is missing or unsafe",
                }
            assert task.state_input_env is not None
            evaluator_env[task.state_input_env] = str(input_state_path)
    execution = await backend.run(
        ExecutionRequest(
            argv=tuple(argv),
            cwd=workspace,
            timeout_seconds=timeout_seconds,
            env=evaluator_env,
        )
    )
    if execution.timed_out:
        return {
            task.qualification_key: False,
            "error": f"evaluator timed out after {timeout_seconds}s",
            "duration_seconds": execution.duration_seconds,
            "isolation": execution.attestation.as_dict(),
        }
    stdout_text = execution.stdout.decode("utf-8", errors="replace")
    stderr_text = execution.stderr.decode("utf-8", errors="replace")
    (output_dir / "stdout.log").write_text(stdout_text, encoding="utf-8")
    (output_dir / "stderr.log").write_text(stderr_text, encoding="utf-8")
    duration = execution.duration_seconds
    if execution.returncode != 0:
        return {
            task.qualification_key: False,
            "error": f"evaluator exited {execution.returncode}",
            "duration_seconds": duration,
            "isolation": execution.attestation.as_dict(),
        }
    lines = [line.strip() for line in stdout_text.splitlines() if line.strip()]
    if not lines:
        return {
            task.qualification_key: False,
            "error": "evaluator produced no JSON",
            "duration_seconds": duration,
        }
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        return {
            task.qualification_key: False,
            "error": f"invalid evaluator JSON: {exc}",
            "duration_seconds": duration,
        }
    if not isinstance(result, dict):
        return {
            task.qualification_key: False,
            "error": "evaluator JSON must be an object",
            "duration_seconds": duration,
        }
    result.pop("captured_state", None)
    if captured_state_path is not None:
        if (
            not captured_state_path.is_file()
            or captured_state_path.is_symlink()
        ):
            result[task.qualification_key] = False
            result["error"] = "evaluator did not capture required runtime state"
        else:
            size = captured_state_path.stat().st_size
            if size > task.state_max_bytes:
                result[task.qualification_key] = False
                result["error"] = (
                    "captured runtime state exceeded "
                    f"{task.state_max_bytes} bytes"
                )
            else:
                try:
                    relative_path = captured_state_path.relative_to(run_dir)
                except ValueError:
                    result[task.qualification_key] = False
                    result["error"] = "captured runtime state escaped run directory"
                else:
                    result["captured_state"] = {
                        "path": relative_path.as_posix(),
                        "sha256": sha256_file(captured_state_path),
                        "size": size,
                    }
    result = validate_evaluation_result(task, result)
    result["duration_seconds"] = duration
    result["evaluator_argv"] = argv
    result["isolation"] = execution.attestation.as_dict()
    return result
