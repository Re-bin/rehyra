from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _region_bounds(text: str, start_marker: str, end_marker: str) -> tuple[int, int]:
    starts: list[tuple[int, int]] = []
    ends: list[tuple[int, int]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        content = line.rstrip("\r\n")
        bounds = (offset, offset + len(line))
        if content == start_marker:
            starts.append(bounds)
        if content == end_marker:
            ends.append(bounds)
        offset += len(line)
    if len(starts) != 1 or len(ends) != 1:
        raise ValueError(
            "submission must contain exactly one editable start and end marker"
        )
    start = starts[0][0]
    end = ends[0][1]
    if start >= ends[0][0]:
        raise ValueError("editable region markers are out of order")
    return start, end


def project_editable_region(
    *,
    parent_submission: Path,
    candidate_submission: Path,
    raw_evidence_path: Path,
    submission_relpath: Path,
    start_marker: str,
    end_marker: str,
) -> dict[str, Any]:
    for label, path in (
        ("parent", parent_submission),
        ("candidate", candidate_submission),
    ):
        if not path.is_file() or path.is_symlink():
            raise ValueError(f"{label} submission is missing or unsafe")
    parent_text = parent_submission.read_text(encoding="utf-8")
    parent_start, parent_end = _region_bounds(
        parent_text,
        start_marker,
        end_marker,
    )

    raw_evidence_path.parent.mkdir(parents=True, exist_ok=True)
    if raw_evidence_path.exists() or raw_evidence_path.is_symlink():
        if not raw_evidence_path.is_file() or raw_evidence_path.is_symlink():
            raise ValueError("raw submission evidence is unsafe")
        candidate_text = raw_evidence_path.read_text(encoding="utf-8")
    else:
        candidate_text = candidate_submission.read_text(encoding="utf-8")
        raw_tmp = raw_evidence_path.with_suffix(
            raw_evidence_path.suffix + ".tmp"
        )
        raw_tmp.write_text(candidate_text, encoding="utf-8")
        os.replace(raw_tmp, raw_evidence_path)
    candidate_start, candidate_end = _region_bounds(
        candidate_text,
        start_marker,
        end_marker,
    )
    region = candidate_text[candidate_start:candidate_end]
    projected = parent_text[:parent_start] + region + parent_text[parent_end:]
    projected_tmp = candidate_submission.with_suffix(
        candidate_submission.suffix + ".projected.tmp"
    )
    projected_tmp.write_text(projected, encoding="utf-8")
    os.replace(projected_tmp, candidate_submission)

    raw_bytes = candidate_text.encode("utf-8")
    region_bytes = region.encode("utf-8")
    projected_bytes = projected.encode("utf-8")
    parent_bytes = parent_text.encode("utf-8")
    return {
        "policy": "editable_region",
        "start_marker": start_marker,
        "end_marker": end_marker,
        "submission_relpath": submission_relpath.as_posix(),
        "raw_submission": raw_evidence_path.name,
        "raw_sha256": _sha256(raw_bytes),
        "region_sha256": _sha256(region_bytes),
        "parent_sha256": _sha256(parent_bytes),
        "projected_sha256": _sha256(projected_bytes),
        "restored_prefix_bytes": len(parent_text[:parent_start].encode("utf-8")),
        "restored_suffix_bytes": len(parent_text[parent_end:].encode("utf-8")),
    }
