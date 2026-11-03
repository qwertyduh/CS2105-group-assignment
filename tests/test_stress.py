import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from roster.stress import (
    DAY_CHOICES,
    SCENARIOS,
    build_report,
    draw_case_params,
    ensure_cases,
)

ROOT = Path(__file__).resolve().parents[1]
RUN_TIME_S = 1.0
REPAIR_TIME_S = 1.0
EVENTS = 1


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def run_cli(stress_dir, n=2):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "roster.stress",
            "run",
            "--n",
            str(n),
            "--time",
            str(RUN_TIME_S),
            "--workers",
            "2",
            "--seed",
            "11",
            "--repair-time",
            str(REPAIR_TIME_S),
            "--repair-events",
            str(EVENTS),
            "--stress-dir",
            str(stress_dir),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


class SamplingTests(unittest.TestCase):
    def test_params_are_reproducible_and_within_spec(self):
        for index in range(50):
            first = draw_case_params(11, index)
            second = draw_case_params(11, index)
            self.assertEqual(first, second)
            self.assertIn(first["scenario"], SCENARIOS)
            self.assertIn(first["days"], DAY_CHOICES)
            self.assertTrue(0.2 <= first["pref_fraction"] <= 0.9)
            for group in ("MPR", "CND", "Hospitality", "SPONS"):
                self.assertTrue(6 <= first["sizes"][group] <= 30)

    def test_case_does_not_depend_on_n(self):
        self.assertEqual(draw_case_params(11, 3), draw_case_params(11, 3))

    def test_manifest_and_case_files_reproduce(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_dir = Path(first)
            second_dir = Path(second)
            rows_a = ensure_cases(first_dir, 2, 11, 5.0, 1)
            rows_b = ensure_cases(second_dir, 2, 11, 5.0, 1)
            self.assertEqual(rows_a, rows_b)
            for row in rows_a:
                case_id = row["case_id"]
                for name in ("config.csv", "shifts.csv", "students.csv", "demand.csv", "preferences.csv"):
                    self.assertEqual(
                        (first_dir / "cases" / case_id / name).read_bytes(),
                        (second_dir / "cases" / case_id / name).read_bytes(),
                    )
                self.assertEqual(row["cpsat_num_workers"], "1")


class StressRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.first = run_cli(cls.base)
        if cls.first.returncode != 0:
            raise AssertionError(cls.first.stderr + cls.first.stdout)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_run_writes_manifest_results_and_repairs(self):
        self.assertEqual(self.first.returncode, 0, self.first.stderr)
        manifest = read_rows(self.base / "manifest.csv")
        results = read_rows(self.base / "results.csv")
        repairs = read_rows(self.base / "repairs.csv")
        self.assertEqual(len(manifest), 2)
        self.assertEqual(len(results), 8)  # 2 cases x 4 solvers
        self.assertEqual(len(repairs), 4)  # 2 cases x 1 event x 2 methods
        self.assertEqual(
            {row["solver"] for row in results},
            {"greedy", "local", "cpsat_cold", "cpsat_warm"},
        )
        for row in manifest:
            self.assertEqual(row["cpsat_num_workers"], "1")
            self.assertTrue(Path(row["case_id"]).name)
        for row in results:
            self.assertIn(row["hard_pass"], {"True", "False"})
            self.assertTrue(row["total_cost"])
            self.assertTrue(row["runtime_s"])

    def test_resume_skips_completed_pairs_and_restores_missing_ones(self):
        results_path = self.base / "results.csv"
        repairs_path = self.base / "repairs.csv"
        original_results = read_rows(results_path)
        original_repairs = read_rows(repairs_path)

        kept = original_results[:2]
        deleted = original_results[2:]
        self.assertTrue(deleted)
        with results_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(kept[0]))
            writer.writeheader()
            writer.writerows(kept)

        kept_repair = original_repairs[:1]
        with repairs_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(kept_repair[0]))
            writer.writeheader()
            writer.writerows(kept_repair)

        second = run_cli(self.base)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
        self.assertIn("already done", second.stdout)

        resumed_results = read_rows(results_path)
        resumed_repairs = read_rows(repairs_path)
        self.assertEqual(len(resumed_results), len(original_results))
        self.assertEqual(len(resumed_repairs), len(original_repairs))
        keys = {(row["case_id"], row["solver"]) for row in resumed_results}
        self.assertEqual(len(keys), len(resumed_results))
        # Rows that were already present must not have been recomputed.
        for old in kept:
            new = next(
                row
                for row in resumed_results
                if row["case_id"] == old["case_id"] and row["solver"] == old["solver"]
            )
            self.assertEqual(new["runtime_s"], old["runtime_s"])

    def test_report_has_required_sections_and_thin_evidence_note(self):
        path = build_report(self.base)
        text = path.read_text(encoding="utf-8")
        for section in (
            "## Win rate",
            "## Cost gap to the best solver",
            "## Reliability",
            "## Runtime",
            "## Local search vs CP-SAT by load",
            "## Sick-leave repair",
            "## Recommendation",
        ):
            self.assertIn(section, text)
        self.assertIn("evidence is thin", text)
        self.assertIn("Mean changes", text)
        self.assertIn("Missed 90 s", text)


if __name__ == "__main__":
    unittest.main()
