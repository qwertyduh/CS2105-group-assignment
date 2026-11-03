import csv
import tempfile
import unittest
from pathlib import Path

from roster.compare import (
    CSV_COLUMNS,
    discover_cases,
    run_comparison,
    sanity_warnings,
)

ROOT = Path(__file__).resolve().parents[1]
CASE_04 = ROOT / "test" / "case_04_small_7d"
DETERMINISM_TIME_S = 5.0


def fake_row(case, solver, cost):
    return {"case": case, "solver": solver, "total_cost": cost}


class SanityWarningTests(unittest.TestCase):
    def test_clean_ordering_has_no_warnings(self):
        rows = [
            fake_row("case_x", "greedy", 100.0),
            fake_row("case_x", "local", 80.0),
            fake_row("case_x", "cpsat_cold", 70.0),
            fake_row("case_x", "cpsat_warm", 60.0),
        ]
        self.assertEqual(sanity_warnings(rows), [])

    def test_broken_ordering_is_reported_with_case_and_costs(self):
        rows = [
            fake_row("case_x", "greedy", 100.0),
            fake_row("case_x", "local", 105.0),
            fake_row("case_x", "cpsat_cold", 90.0),
            fake_row("case_x", "cpsat_warm", 110.0),
        ]
        warnings = sanity_warnings(rows)
        self.assertEqual(
            [warning["key"] for warning in warnings],
            [
                "case_x|warm-vs-cold",
                "case_x|warm-vs-local",
                "case_x|local-vs-greedy",
            ],
        )
        for warning in warnings:
            self.assertIn("WARNING", warning["text"])
            self.assertIn("case_x", warning["text"])
            self.assertIn("greedy=100.0000", warning["text"])

    def test_cpsat_warm_tied_with_local_is_not_a_warning(self):
        rows = [
            fake_row("case_x", "greedy", 100.0),
            fake_row("case_x", "local", 60.0),
            fake_row("case_x", "cpsat_cold", 62.0),
            fake_row("case_x", "cpsat_warm", 60.0),
        ]
        self.assertEqual(sanity_warnings(rows), [])


class CaseDiscoveryTests(unittest.TestCase):
    def test_all_four_sample_cases_are_found(self):
        names = {folder.name for folder in discover_cases(ROOT / "test")}
        for expected in (
            "case_01_balanced_20d",
            "case_02_tight_28d",
            "case_03_overloaded_20d",
            "case_04_small_7d",
        ):
            self.assertIn(expected, names)


class CompareReproducibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        base = Path(cls.temp.name)
        cls.rows, cls.warnings = run_comparison(
            [CASE_04],
            DETERMINISM_TIME_S,
            1,
            base / "first",
            progress=False,
        )
        cls.second_rows, _ = run_comparison(
            [CASE_04],
            DETERMINISM_TIME_S,
            1,
            base / "second",
            progress=False,
        )
        cls.first_dir = base / "first"
        cls.second_dir = base / "second"

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_one_row_per_solver(self):
        self.assertEqual(len(self.rows), 4)
        self.assertEqual(
            [row["solver"] for row in self.rows],
            ["greedy", "local", "cpsat_cold", "cpsat_warm"],
        )

    def test_csv_columns_include_evaluator_and_gap_fields(self):
        with (self.first_dir / "compare.csv").open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle))
        self.assertEqual(header, list(CSV_COLUMNS))
        for column in (
            "total_cost",
            "cost_unfilled",
            "cost_overwork",
            "cost_streak",
            "cost_rest",
            "cost_preference",
            "cost_fairness",
            "cost_isolated",
            "unfilled_total",
            "hard_pass",
            "preference_satisfaction_pct",
            "spread_MPR",
            "runtime_s",
            "runtime_under_90s",
            "cpsat_gap",
        ):
            self.assertIn(column, header)

    def test_rerun_same_seed_matches_except_runtime(self):
        def load(path):
            with path.open(newline="", encoding="utf-8") as handle:
                return list(csv.DictReader(handle))

        first = load(self.first_dir / "compare.csv")
        second = load(self.second_dir / "compare.csv")
        self.assertEqual(len(first), len(second))
        ignored = {"runtime_s", "runtime_under_90s"}
        for row_a, row_b in zip(first, second):
            for column in CSV_COLUMNS:
                if column in ignored:
                    continue
                self.assertEqual(
                    row_a[column],
                    row_b[column],
                    f"{row_a['solver']} column {column} changed on rerun",
                )

    def test_markdown_reports_tables_readout_and_sanity(self):
        text = (self.first_dir / "compare.md").read_text(encoding="utf-8")
        self.assertIn("## case_04_small_7d", text)
        self.assertIn("**", text)  # best total cost in bold
        self.assertIn("## Read-out", text)
        self.assertIn("## Sanity checks", text)
        if self.warnings:
            self.assertIn("WARNING", text)
        else:
            self.assertIn("No ordering warnings", text)


if __name__ == "__main__":
    unittest.main()
