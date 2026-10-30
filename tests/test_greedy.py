import json
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from roster.case_io import Case, Shift, Student, load_case
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


def one_student_case(horizon, demand_days, config=None):
    """A tiny MPR case with one 20 h student and 4 h Morning shifts."""
    student = Student("S1", "Solo", "MPR", 20.0)
    shift = Shift("M", "Morning", "11:00", "15:00", 4.0, False)
    return Case(
        horizon_days=horizon,
        students={student.roll_no: student},
        by_group={"MPR": [student.roll_no]},
        shifts={shift.shift_id: shift},
        shift_order=[shift.shift_id],
        demand={(day, "M", "MPR"): 1 for day in demand_days},
        prefs={},
        config={"horizon_days": horizon, **(config or {})},
    )


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

    def test_hard_ceiling_blocks_a_candidate(self):
        case = one_student_case(
            2, (1, 2), {"hard_ceiling_hours_per_window": 4}
        )
        rules = Rules.from_case(case)
        roster = solve(case, 90.0, seed=1)
        self.assertEqual(len(roster), 1)
        report = evaluate(case, rules, roster)
        self.assertTrue(report.hard_pass, report.hard_breaches)
        self.assertEqual(report.unfilled_total, 1)

    def test_disabled_hard_ceiling_allows_both_days(self):
        case = one_student_case(2, (1, 2))
        rules = Rules.from_case(case)
        self.assertEqual(rules.hard_ceiling_hours_per_window, 0)
        roster = solve(case, 90.0, seed=1)
        self.assertEqual(len(roster), 2)
        self.assertTrue(evaluate(case, rules, roster).hard_pass)

    def test_overwork_is_priced_across_a_week_boundary(self):
        days = (3, 4, 5, 7, 8, 9)
        case = one_student_case(9, days, {"rolling_window_days": 7})
        rules = Rules.from_case(case)
        roster = solve(case, 90.0, seed=1)
        self.assertEqual(len(roster), 6)
        report = evaluate(case, rules, roster)
        self.assertEqual(report.cost_components["overwork"], 2)
        info = report.overwork_by_student["S1"]
        self.assertEqual(info["worst_rolling_extra_shifts"], 1)
        self.assertIn([3, 9], [window["days"] for window in info["charged_windows"]])

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
                self.assertLess(cost, 5.0)

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
