from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from rehyra.util import sha256_file
from rehyra.workspace import project_editable_region


class WorkspacePolicyTests(unittest.TestCase):
    def test_projects_only_editable_region_and_preserves_raw_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            parent = root / "parent.py"
            candidate = root / "candidate.py"
            evidence = root / "evidence" / "raw_submission"
            parent.write_text(
                "HEADER = 'fixed'\n"
                "# START\n"
                "VALUE = 1\n"
                "# END\n"
                "def run_code():\n"
                "    return VALUE\n",
                encoding="utf-8",
            )
            raw_candidate = (
                "# START\n"
                "VALUE = 2\n"
                "# END\n"
            )
            candidate.write_text(raw_candidate, encoding="utf-8")

            projection = project_editable_region(
                parent_submission=parent,
                candidate_submission=candidate,
                raw_evidence_path=evidence,
                submission_relpath=Path("candidate.py"),
                start_marker="# START",
                end_marker="# END",
            )

            expected = (
                "HEADER = 'fixed'\n"
                "# START\n"
                "VALUE = 2\n"
                "# END\n"
                "def run_code():\n"
                "    return VALUE\n"
            )
            self.assertEqual(candidate.read_text(encoding="utf-8"), expected)
            self.assertEqual(evidence.read_text(encoding="utf-8"), raw_candidate)
            self.assertEqual(projection["raw_sha256"], sha256_file(evidence))
            self.assertEqual(
                projection["projected_sha256"],
                sha256_file(candidate),
            )
            self.assertEqual(
                projection["submission_relpath"],
                "candidate.py",
            )

            second = project_editable_region(
                parent_submission=parent,
                candidate_submission=candidate,
                raw_evidence_path=evidence,
                submission_relpath=Path("candidate.py"),
                start_marker="# START",
                end_marker="# END",
            )
            self.assertEqual(second, projection)
            self.assertEqual(candidate.read_text(encoding="utf-8"), expected)

    def test_rejects_ambiguous_or_missing_markers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            parent = root / "parent.py"
            candidate = root / "candidate.py"
            parent.write_text("# START\nx = 1\n# END\n", encoding="utf-8")
            evidence = root / "raw_submission"
            for content in (
                "x = 2\n",
                "# START\n# START\nx = 2\n# END\n",
                "# END\nx = 2\n# START\n",
            ):
                candidate.write_text(content, encoding="utf-8")
                if evidence.exists():
                    evidence.unlink()
                with self.assertRaises(ValueError):
                    project_editable_region(
                        parent_submission=parent,
                        candidate_submission=candidate,
                        raw_evidence_path=evidence,
                        submission_relpath=Path("candidate.py"),
                        start_marker="# START",
                        end_marker="# END",
                    )


if __name__ == "__main__":
    unittest.main()
