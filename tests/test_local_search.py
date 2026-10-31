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
from roster.solvers.greedy import solve as greedy_solve
from roster.solvers.local_search import solve

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    "case_01": ROOT / "test" / "case_01_balanced_20d",
    "case_02": ROOT / "test" / "case_02_tight_28d",
    "case_03": ROOT / "test" / "case_03_overloaded_20d",
    "case_04": ROOT / "test" / "case_04_small_7d",
}
TIME_LIMIT = 1.5


class LocalSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {name: load_case(str(path)) for name, path in CASES.items()}
        cls.rules = {name: Rules.from_case(case) for name, case in cls.cases.items()}
        cls.greedy = {
            name: greedy_solve(case, 90.0, seed=1)
            for name, case in cls.cases.items()
        }
        cls.greedy_reports = {
            name: evaluate(case, cls.rules[name], cls.greedy[name])
            for name, case in cls.cases.items()
        }
        cls.local = {
            name: solve(case, TIME_LIMIT, seed=1)
            for name, case in cls.cases.items()
        }
        cls.local_reports = {
            name: evaluate(case, cls.rules[name], cls.local[name])
            for name, case in cls.cases.items()
        }

    def test_cost_never_above_greedy_for_same_seed(self):
        for name, report in self.local_reports.items():
            with self.subTest(case=name):
                self.assertLessEqual(
                    report.total_cost,
                    self.greedy_reports[name].total_cost + 1e-9,
                )

    def test_hard_rules_pass_on_all_cases(self):
        for name, report in self.local_reports.items():
            with self.subTest(case=name):
                self.assertTrue(report.hard_pass, report.hard_breaches)

    def test_debug_flag_matches_incremental_and_full_cost(self):
        case = self.cases["case_04"]
        roster = solve(case, 1.0, seed=3, debug=True)
        report = evaluate(case, self.rules["case_04"], roster)
        self.assertTrue(report.hard_pass, report.hard_breaches)
        self.assertLessEqual(
            report.total_cost,
            self.greedy_reports["case_04"].total_cost + 1e-9,
        )

    def test_deterministic_for_same_seed_and_time(self):
        case = self.cases["case_04"]
        first = solve(case, 1.5, seed=7)
        second = solve(case, 1.5, seed=7)
        self.assertEqual(first, second)

    def test_runtime_within_limit_plus_two_seconds(self):
        case = self.cases["case_04"]
        started = time.perf_counter()
        solve(case, 0.5, seed=1)
        self.assertLess(time.perf_counter() - started, 2.5)

    def test_run_cli_writes_local_roster_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "roster.run",
                    "local",
                    str(CASES["case_04"]),
                    "--seed",
                    "1",
                    "--time",
                    "1",
                    "--debug",
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
            roster_path = Path(tmp) / "case_04_small_7d_local.roster.csv"
            report_path = Path(tmp) / "case_04_small_7d_local.report.json"
            self.assertTrue(roster_path.is_file())
            self.assertTrue(report_path.is_file())
            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertTrue(report["hard_pass"])
            self.assertLessEqual(
                report["total_cost"],
                self.greedy_reports["case_04"].total_cost + 1e-9,
            )


if __name__ == "__main__":
    unittest.main()
