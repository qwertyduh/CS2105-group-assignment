from __future__ import annotations

import contextlib
import io
import os
import tempfile
import time
import unittest

from roster import run as roster_run
from roster.case_io import load_case
from roster.evaluator import GROUPS, evaluate
from roster.rules import Rules
from roster.solvers import cpsat, greedy


class TestCpSatSolver(unittest.TestCase):
    CASE_FOLDERS = (
        "test/case_01_balanced_20d",
        "test/case_02_tight_28d",
        "test/case_03_overloaded_20d",
        "test/case_04_small_7d",
    )

    def test_b1_group_objectives_match_evaluator(self) -> None:
        """B1 contract: sum of four group objectives matches evaluator total_cost within 0.01."""
        for folder in ("test/case_04_small_7d", "test/case_01_balanced_20d"):
            with self.subTest(case=folder):
                case = load_case(folder)
                rules = Rules.from_case(case)
                merged = {}
                obj_sum = 0.0
                for group in GROUPS:
                    res = cpsat.solve_group(case, rules, group, time_limit_s=5.0, seed=1)
                    self.assertIn(res.status, ("OPTIMAL", "FEASIBLE"))
                    self.assertIsNotNone(res.objective)
                    obj_sum += float(res.objective)
                    merged.update(res.roster)
                report = evaluate(case, rules, merged)
                self.assertTrue(report.hard_pass, msg=report.hard_breaches)
                self.assertAlmostEqual(obj_sum, report.total_cost, delta=0.01)

    def test_split_time_limits_and_workers(self) -> None:
        """Time split respects variable shares with >= 10% floor; worker sum <= os.cpu_count()."""
        var_counts = {"MPR": 10, "CND": 300, "Hospitality": 300, "SPONS": 390}
        times = cpsat.split_time_limits(var_counts, time_limit_s=60.0)
        self.assertAlmostEqual(sum(times.values()), 60.0, places=6)
        for group, t_s in times.items():
            self.assertGreaterEqual(t_s, 6.0 - 1e-9, msg=f"{group} below 10% floor")
        self.assertGreater(times["SPONS"], times["CND"])
        self.assertAlmostEqual(times["MPR"], 6.0, places=6)

        cpu_budget = max(4, os.cpu_count() or 4)
        workers = cpsat.split_workers(var_counts, cpu_count=cpu_budget)
        self.assertLessEqual(sum(workers.values()), cpu_budget)
        for w in workers.values():
            self.assertGreaterEqual(w, 1)

    def test_all_four_cases_hard_pass_wall_time_and_beat_or_match_greedy(self) -> None:
        """Merged roster passes hard rules, finishes within time_limit_s + 5s, and <= greedy cost."""
        seed = 1
        time_limit_s = 12.0
        for folder in self.CASE_FOLDERS:
            with self.subTest(case=folder):
                case = load_case(folder)
                rules = Rules.from_case(case)
                greedy_roster = greedy.solve(case, time_limit_s=time_limit_s, seed=seed)
                greedy_rep = evaluate(case, rules, greedy_roster)

                side: dict = {}
                t0 = time.perf_counter()
                roster = cpsat.solve(case, time_limit_s=time_limit_s, seed=seed, side=side)
                wall = time.perf_counter() - t0

                report = evaluate(case, rules, roster)
                self.assertTrue(report.hard_pass, msg=f"{folder}: {report.hard_breaches}")
                self.assertLessEqual(
                    wall,
                    time_limit_s + 5.0,
                    msg=f"{folder}: wall={wall:.2f}s exceeded {time_limit_s + 5.0:.2f}s",
                )
                self.assertLessEqual(
                    report.total_cost,
                    greedy_rep.total_cost + 1e-6,
                    msg=f"{folder}: cpsat={report.total_cost} > greedy={greedy_rep.total_cost}",
                )

                # Side dict has per-group status and gap for all 4 groups
                self.assertIn("groups", side)
                self.assertIn("beat_greedy", side)
                for group in GROUPS:
                    self.assertIn(group, side["groups"])
                    ginfo = side["groups"][group]
                    self.assertIn(ginfo["status"], ("OPTIMAL", "FEASIBLE"))
                    self.assertGreaterEqual(ginfo["gap"], 0.0)

                if folder.endswith("case_03_overloaded_20d"):
                    self.assertGreater(report.unfilled_total, 0)
                    self.assertGreater(len(report.unfilled_slots), 0)

    def test_run_cli_prints_per_group_status_and_gap(self) -> None:
        """`python -m roster.run cpsat` prints per-group status, gap, and warm-start status."""
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = roster_run.main(
                    [
                        "cpsat",
                        "test/case_04_small_7d",
                        "--seed",
                        "1",
                        "--time",
                        "8",
                        "--out-dir",
                        tmp,
                    ]
                )
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            self.assertIn("warm_start:", out)
            self.assertIn("beat_greedy=", out)
            for group in GROUPS:
                self.assertIn(f"group {group}:", out)
                self.assertIn("status=", out)
                self.assertIn("gap=", out)

    def test_timeout_keeps_bounds_and_gaps_unknown(self) -> None:
        """UNKNOWN groups must not fabricate a bound or a zero gap."""
        case = load_case("test/case_01_balanced_20d")
        side: dict = {}
        cpsat.solve(case, time_limit_s=0.001, seed=1, side=side)
        unknown = [g for g, i in side["groups"].items() if i["status"] == "UNKNOWN"]
        self.assertTrue(unknown)
        for g in unknown:
            info = side["groups"][g]
            self.assertIsNone(info["objective"])
            self.assertIsNone(info["best_bound"])
            self.assertIsNone(info["gap"])
            self.assertIsNone(info["rel_gap"])
        self.assertIsNone(side["best_bound"])
        self.assertIsNone(side["gap"])
        self.assertIsNone(side["rel_gap"])

    def test_concurrent_groups_get_the_whole_budget(self) -> None:
        """Groups solved side by side each get the remaining budget, not a share of it."""
        if (os.cpu_count() or 1) < len(GROUPS):
            self.skipTest("fewer CPUs than groups; the budget is split")
        case = load_case("test/case_04_small_7d")
        side: dict = {}
        cpsat.solve(case, time_limit_s=4.0, seed=1, side=side)
        for g in GROUPS:
            self.assertGreater(side["groups"][g]["time_limit_s"], 3.0)
            self.assertLessEqual(side["groups"][g]["time_limit_s"], 4.0)

    def test_cold_groups_without_solution_are_flagged(self) -> None:
        """A cold group with no solution is listed and contributes no assignments."""
        case = load_case("test/case_01_balanced_20d")
        side: dict = {}
        roster = cpsat.solve(case, time_limit_s=0.001, seed=1, side=side, warm_start=False)
        expected = [g for g in GROUPS if side["groups"][g]["objective"] is None]
        self.assertEqual(side["no_solution_groups"], expected)
        for g in side["no_solution_groups"]:
            members = set(case.by_group[g])
            self.assertFalse([key for key in roster if key[0] in members])

    def test_write_baseline_survives_timeout(self) -> None:
        """Baseline writer renders missing objectives/bounds as n/a instead of raising."""
        with tempfile.TemporaryDirectory() as tmp:
            path = cpsat.write_baseline(
                ["test/case_01_balanced_20d"], time_limit_s=0.001, seed=1, out_dir=tmp
            )
            text = path.read_text(encoding="utf-8")
        self.assertIn("n/a", text)
        self.assertIn("status=UNKNOWN", text)

    def test_run_cli_prints_na_for_unknown_gap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = roster_run.main(
                    ["cpsat", "test/case_01_balanced_20d", "--seed", "1",
                     "--time", "0.001", "--out-dir", tmp]
                )
            self.assertEqual(rc, 0)
            self.assertIn("gap=n/a", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
