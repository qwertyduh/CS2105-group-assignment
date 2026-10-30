import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from roster.case_io import load_case
from roster.evaluator import evaluate
from roster.rules import Rules
from roster.solvers.greedy import solve

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "case_01": ROOT / "test" / "case_01_balanced_20d",
    "case_02": ROOT / "test" / "case_02_tight_28d",
    "case_03": ROOT / "test" / "case_03_overloaded_20d",
    "case_04": ROOT / "test" / "case_04_small_7d",
}


class GreedySolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {name: load_case(str(path)) for name, path in CASES.items()}
        cls.rules = {name: Rules.from_case(case) for name, case in cls.cases.items()}
        cls.rosters = {
            name: solve(case, time_limit_s=90.0, seed=1)
            for name, case in cls.cases.items()
        }
        cls.reports = {
            name: evaluate(case, cls.rules[name], cls.rosters[name])
            for name, case in cls.cases.items()
        }

    def test_deterministic_for_same_seed(self):
        case = self.cases["case_04"]
        first = solve(case, 90.0, seed=7)
        second = solve(case, 90.0, seed=7)
        self.assertEqual(first, second)

    def test_hard_rules_pass_on_all_cases(self):
        for name, report in self.reports.items():
            with self.subTest(case=name):
                self.assertTrue(report.hard_pass, report.hard_breaches)

    def test_no_demand_exceeded_and_only_own_group(self):
        for name, roster in self.rosters.items():
            case = self.cases[name]
            counts = {}
            for (roll_no, day), shift_id in roster.items():
                group = case.students[roll_no].group
                self.assertGreater(case.demand.get((day, shift_id, group), 0), 0)
                counts[(day, shift_id, group)] = counts.get((day, shift_id, group), 0) + 1
            for cell, assigned in counts.items():
                self.assertLessEqual(assigned, case.demand[cell])

    def test_one_shift_per_day_by_construction(self):
        roster = self.rosters["case_01"]
        seen = set()
        for roll_no, day in roster:
            self.assertNotIn((roll_no, day), seen)
            seen.add((roll_no, day))

    def test_balanced_cases_have_finite_cost(self):
        for name in ("case_01", "case_04"):
            with self.subTest(case=name):
                cost = self.reports[name].total_cost
                self.assertTrue(cost == cost)  # not NaN
                self.assertLess(cost, 1.0)

    def test_overloaded_case_leaves_gaps_instead_of_failing(self):
        report = self.reports["case_03"]
        self.assertTrue(report.hard_pass)
        self.assertGreater(report.unfilled_total, 0)

    def test_case_02_runtime_under_five_seconds(self):
        case = self.cases["case_02"]
        started = time.perf_counter()
        solve(case, time_limit_s=90.0, seed=1)
        self.assertLess(time.perf_counter() - started, 5.0)

    def test_run_cli_writes_roster_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "roster.run",
                    "greedy",
                    str(CASES["case_04"]),
                    "--seed",
                    "1",
                    "--out-dir",
                    tmp,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("total_cost=", completed.stdout)
            self.assertIn("hard_pass=True", completed.stdout)
            roster_path = Path(tmp) / "case_04_small_7d_greedy.roster.csv"
            report_path = Path(tmp) / "case_04_small_7d_greedy.report.json"
            self.assertTrue(roster_path.is_file())
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["hard_pass"])
            self.assertIn("total_cost", report)


if __name__ == "__main__":
    unittest.main()
