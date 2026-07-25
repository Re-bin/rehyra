from __future__ import annotations

import hashlib
import json
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = ROOT / "benchmarks"
SOLUTIONS = BENCHMARKS / "solutions"
EXPECTED_TASKS = {
    "smallest_adder",
    "sums_diffs",
    "autocorrelation_first",
    "autocorrelation_second",
    "erdos_min_overlap",
    "qubit_routing",
    "sunspot_symbolic",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PublishedSolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.manifest = tomllib.loads(
            (SOLUTIONS / "manifest.toml").read_text(encoding="utf-8")
        )
        cls.results = cls.manifest["results"]

    def test_manifest_covers_the_complete_suite_once(self) -> None:
        tasks = [result["task"] for result in self.results]
        self.assertEqual(set(tasks), EXPECTED_TASKS)
        self.assertEqual(len(tasks), len(set(tasks)))

    def test_every_evidence_record_is_present_and_hashed(self) -> None:
        for result in self.results:
            with self.subTest(task=result["task"]):
                evidence = BENCHMARKS / result["evidence"]
                self.assertTrue(evidence.is_file())
                self.assertEqual(sha256(evidence), result["evidence_sha256"])

    def test_publication_audit_matches_this_manifest_and_suite(self) -> None:
        audit_path = BENCHMARKS / self.manifest["publication_audit"]
        audit = json.loads(audit_path.read_text(encoding="utf-8"))
        self.assertEqual(
            audit["manifest_sha256"],
            sha256(SOLUTIONS / "manifest.toml"),
        )
        checks = audit["checks"]
        self.assertEqual({check["task"] for check in checks}, EXPECTED_TASKS)
        self.assertTrue(all(check["passed"] for check in checks))
        self.assertTrue(audit["all_passed"])

    def test_published_bundles_are_complete_and_exact(self) -> None:
        declared_bundles: set[str] = set()
        for result in self.results:
            if result["status"] != "published":
                continue
            bundle_name = result["bundle"]
            declared_bundles.add(bundle_name)
            bundle = SOLUTIONS / bundle_name
            self.assertTrue(bundle.is_dir())
            self.assertFalse(bundle.is_symlink())
            declared_files = {item["path"] for item in result["files"]}
            actual_files = {
                path.relative_to(bundle).as_posix()
                for path in bundle.rglob("*")
                if path.is_file() and "__pycache__" not in path.parts
            }
            self.assertEqual(actual_files, declared_files)
            for item in result["files"]:
                path = bundle / item["path"]
                with self.subTest(task=result["task"], path=item["path"]):
                    self.assertTrue(path.is_file())
                    self.assertFalse(path.is_symlink())
                    self.assertEqual(sha256(path), item["sha256"])

        actual_bundles = {
            path.name
            for path in SOLUTIONS.iterdir()
            if path.is_dir() and path.name != "__pycache__"
        }
        self.assertEqual(actual_bundles, declared_bundles)

    def test_no_result_is_explicit_and_has_no_bundle(self) -> None:
        no_results = [
            result for result in self.results if result["status"] == "no_new_solution"
        ]
        self.assertEqual([result["task"] for result in no_results], [
            "autocorrelation_second"
        ])
        for result in no_results:
            self.assertNotIn("bundle", result)
            self.assertNotIn("files", result)
            self.assertTrue(result["reason"])

    def test_statuses_and_directions_are_closed_sets(self) -> None:
        for result in self.results:
            self.assertIn(result["status"], {"published", "no_new_solution"})
            self.assertIn(result["direction"], {"minimize", "maximize"})


if __name__ == "__main__":
    unittest.main()
