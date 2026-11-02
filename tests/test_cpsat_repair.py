import csv
from itertools import product
from unittest.mock import patch
import random
import time
import unittest
from pathlib import Path

from ortools.sat.python import cp_model

from roster.case_io import Case, Shift, Student, load_case
from roster.evaluator import evaluate
from roster.rules import Rules
from roster.solvers.base import load_roster
from roster.solvers.greedy import solve as greedy_solve
from roster.repair.local_repair import repair as local_repair
from roster.repair.cpsat_repair import repair as cpsat_repair


class TestCPSATRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case_01 = load_case("test/case_01_balanced_20d")
        cls.case_03 = load_case("test/case_03_overloaded_20d")
        
        cls.roster_01 = load_roster("results/case_01_balanced_20d_cpsat.roster.csv")
        cls.roster_03 = load_roster("results/case_03_overloaded_20d_cpsat.roster.csv")

    def test_balanced_repair(self):
        sick_roll, sick_day, sick_sh = None, None, None
        for (r, d), sh in self.roster_01.items():
            if d == 5:
                sick_roll, sick_day, sick_sh = r, d, sh
                break
                
        res = cpsat_repair(
            self.case_01, self.roster_01, sick_roll, sick_day, sick_sh, time_limit_s=5, seed=1
        )
        
        group = self.case_01.students[sick_roll].group
        self.assertLessEqual(len(res.diff), 4)
        
        for r, d, old, new in res.diff:
            self.assertEqual(self.case_01.students[r].group, group)
            self.assertIn(d, [sick_day, sick_day + 1])
            
        frozen_orig = {k: v for k, v in self.roster_01.items() if k[1] not in [sick_day, sick_day + 1]}
        frozen_new = {k: v for k, v in res.roster.items() if k[1] not in [sick_day, sick_day + 1]}
        self.assertEqual(frozen_orig, frozen_new)

    def test_overloaded_unfilled_reason(self):
        sick_roll, sick_day, sick_sh = None, None, None
        for (r, d), sh in self.roster_03.items():
            if d == 5:
                sick_roll, sick_day, sick_sh = r, d, sh
                break

        res = cpsat_repair(
            self.case_03, self.roster_03, sick_roll, sick_day, sick_sh, time_limit_s=5, seed=1
        )
        
        self.assertIsNotNone(res.reason)
        self.assertIn("unfilled penalty", res.reason)
        group = self.case_03.students[sick_roll].group
        shortages = []
        for day in (sick_day, sick_day + 1):
            for sh in self.case_03.shift_order:
                assigned = sum(res.roster.get((s, day)) == sh
                               for s in self.case_03.by_group[group])
                if assigned < self.case_03.demand.get((day, sh, group), 0):
                    shortages.append((day, sh))
        self.assertTrue(shortages)
        for day, sh in shortages:
            self.assertIn(f"day {day} {sh}", res.reason)

    def test_unavailable_D1(self):
        sick_roll, sick_day, sick_sh = None, None, None
        for (r, d), sh in self.roster_01.items():
            if d == 5 and (r, d + 1) in self.roster_01:
                sick_roll, sick_day, sick_sh = r, d, sh
                break

        res = cpsat_repair(
            self.case_01, self.roster_01, sick_roll, sick_day, sick_sh, 
            time_limit_s=5, seed=1, unavailable_days=[sick_day + 1]
        )
        
        self.assertNotIn((sick_roll, sick_day), res.roster)
        self.assertNotIn((sick_roll, sick_day + 1), res.roster)
        
    def test_20_event_comparison(self):
        rules = Rules.from_case(self.case_01)
        assignments = [a for a in self.roster_01.items() if a[0][1] < self.case_01.horizon_days]
        
        rng = random.Random(42)
        sampled = rng.sample(assignments, 20)
        
        results = []
        for idx, ((roll_no, day), sh) in enumerate(sampled):
            unavailable = [day + 1] if rng.random() > 0.5 else None
            
            t0 = time.perf_counter()
            loc_res = local_repair(
                self.case_01, self.roster_01, roll_no, day, sh, 
                time_limit_s=5, seed=1, unavailable_days=unavailable
            )
            loc_t = time.perf_counter() - t0
            
            t0 = time.perf_counter()
            cp_res = cpsat_repair(
                self.case_01, self.roster_01, roll_no, day, sh, 
                time_limit_s=5, seed=1, unavailable_days=unavailable
            )
            cp_t = time.perf_counter() - t0
            
            loc_rep = evaluate(self.case_01, rules, loc_res.roster)
            loc_cost = loc_rep.total_cost + 0.5 * len(loc_res.diff)
            
            cp_rep = evaluate(self.case_01, rules, cp_res.roster)
            self.assertTrue(loc_rep.hard_pass, loc_rep.hard_breaches)
            self.assertTrue(cp_rep.hard_pass, cp_rep.hard_breaches)
            for result in (loc_res, cp_res):
                self.assertNotIn((roll_no, day), result.roster)
                if unavailable:
                    self.assertNotIn((roll_no, day + 1), result.roster)
                expected_diff = [
                    (r, d, self.roster_01.get((r, d)), result.roster.get((r, d)))
                    for r, d in sorted(set(self.roster_01) | set(result.roster))
                    if self.roster_01.get((r, d)) != result.roster.get((r, d))
                ]
                self.assertEqual(result.diff, expected_diff)
                for r, d, _old, _new in result.diff:
                    self.assertIn(d, (day, day + 1))
                    self.assertEqual(self.case_01.students[r].group,
                                     self.case_01.students[roll_no].group)
            cp_cost = cp_rep.total_cost + 0.5 * len(cp_res.diff)
            
            cost_winner = 'tie'
            if loc_cost < cp_cost - 1e-6:
                cost_winner = 'local'
            elif cp_cost < loc_cost - 1e-6:
                cost_winner = 'cpsat'
                
            results.append({
                'event_idx': idx + 1,
                'roll_no': roll_no,
                'day': day,
                'unavailable_D1': bool(unavailable),
                'local_diff_len': len(loc_res.diff),
                'cpsat_diff_len': len(cp_res.diff),
                'local_cost': round(loc_cost, 6),
                'cpsat_cost': round(cp_cost, 6),
                'local_time': round(loc_t, 3),
                'cpsat_time': round(cp_t, 3),
                'cost_winner': cost_winner
            })
            
        Path("results").mkdir(exist_ok=True)
        with open("results/repair_compare.csv", "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=results[0].keys())
            writer.writeheader()
            writer.writerows(results)

        for r in results:
            self.assertNotEqual(
                r['cost_winner'], 
                'local', 
                "CP-SAT found a worse optimal solution than local search"
            )


def tiny_case(horizon=1, **config):
    students = {s: Student(s, s, "MPR", 8) for s in ("a", "b", "c")}
    students["other"] = Student("other", "other", "CND", 20)
    return Case(
        horizon_days=horizon,
        students=students,
        by_group={"MPR": ["a", "b", "c"], "CND": ["other"]},
        shifts={
            "M": Shift("M", "Morning", "08:00", "12:00", 4, False),
            "N": Shift("N", "Night", "23:00", "03:00", 4, True),
        },
        shift_order=["M", "N"],
        demand={(d, sh, group): 1 for d in range(1, horizon + 1)
                for sh in ("M", "N") for group in ("MPR", "CND")},
        prefs={},
        config={"weight_preference": 0, "weight_fairness": 0,
                "weight_isolated_day_off": 0, **config},
    )


def repair_cost(case, result):
    return evaluate(case, Rules.from_case(case), result.roster).total_cost + 0.5 * len(result.diff)


class CPSATRepairRegressions(unittest.TestCase):
    def test_unknown_reports_shortage_and_both_forced_removals(self):
        case = tiny_case(2)
        original = {("a", 1): "M", ("a", 2): "N", ("other", 1): "M"}
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.UNKNOWN):
            result = cpsat_repair(case, original, "a", 1, "M", unavailable_days=[2])
        self.assertEqual(result.roster, {("other", 1): "M"})
        self.assertEqual(result.diff, [("a", 1, "M", None), ("a", 2, "N", None)])
        self.assertIn("UNKNOWN", result.reason)
        self.assertIn("day 1 M", result.reason)
        self.assertIn("day 2 N", result.reason)
        self.assertEqual(original[("a", 1)], "M")
        self.assertTrue(evaluate(case, Rules.from_case(case), result.roster).hard_pass)

    def test_feasible_solution_cannot_worsen_forced_removal_incumbent(self):
        case = tiny_case()
        original = {("a", 1): "M", ("b", 1): "N"}
        # An empty group roster is feasible (coverage is soft), but needlessly
        # drops b's shift. A time-limited first incumbent need not be optimal.
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.FEASIBLE), \
             patch.object(cp_model.CpSolver, "boolean_value", return_value=False):
            result = cpsat_repair(case, original, "a", 1, "M")
        self.assertEqual(result.roster, {("b", 1): "N"})
        self.assertEqual(result.diff, [("a", 1, "M", None)])

    def test_reason_reports_shortage_moved_to_another_shift(self):
        case = tiny_case()

        def assignment(var):
            return var.Name() == "x_b_1_M"
        with patch.object(cp_model.CpSolver, "solve", return_value=cp_model.FEASIBLE), \
             patch.object(cp_model.CpSolver, "boolean_value", side_effect=assignment):
            # With an empty incumbent, this feasible repair is an improvement.
            result = cpsat_repair(case, {("a", 1): "M"}, "a", 1, "M")
        self.assertEqual(result.roster, {("b", 1): "M"})
        self.assertIn("day 1 N", result.reason)
        self.assertNotIn("day 1 M", result.reason)
        self.assertIn("FEASIBLE", result.reason)

    def test_final_day_and_unavailability_outside_window(self):
        case = tiny_case(3)
        original = {("a", 1): "N", ("a", 3): "M", ("other", 3): "M"}
        result = cpsat_repair(case, original, "a", 3, "N", 2, 1, unavailable_days=[1, 4])
        self.assertNotIn(("a", 3), result.roster)
        self.assertEqual(result.roster[("a", 1)], "N")
        self.assertEqual(result.roster[("other", 3)], "M")
        self.assertEqual(original[("a", 3)], "M")
        self.assertTrue(evaluate(case, Rules.from_case(case), result.roster).hard_pass)

    def test_matches_exhaustive_window_optimum_with_frozen_rules(self):
        # Every allowed two-day schedule is independently scored, including
        # rolling caps/ceilings, short rest, streaks and frozen boundary days.
        for ceiling, unavailable_next in product((0, 8), (False, True)):
            with self.subTest(ceiling=ceiling, unavailable_next=unavailable_next):
                case = tiny_case(4, rolling_window_days=3, max_consecutive_days=2,
                                 hard_ceiling_hours_per_window=ceiling)
                original = greedy_solve(case, 2, 1)
                sick = next(s for s in case.by_group["MPR"] if (s, 2) in original)
                result = cpsat_repair(case, original, sick, 2, original[(sick, 2)],
                                      2, 1, unavailable_days=[3] if unavailable_next else None)
                frozen = {(s, d): sh for (s, d), sh in original.items()
                          if d not in (2, 3) or case.students[s].group != "MPR"}
                self.assertEqual(frozen, {(s, d): sh for (s, d), sh in result.roster.items()
                                         if d not in (2, 3) or case.students[s].group != "MPR"})
                keys = [(s, d) for s in case.by_group["MPR"] for d in (2, 3)]
                optimum = float("inf")
                for values in product((None, "M", "N"), repeat=len(keys)):
                    candidate = dict(frozen)
                    candidate.update({k: v for k, v in zip(keys, values) if v is not None})
                    if (sick, 2) in candidate or (unavailable_next and (sick, 3) in candidate):
                        continue
                    report = evaluate(case, Rules.from_case(case), candidate)
                    if report.hard_pass:
                        changes = sum(original.get(k) != candidate.get(k)
                                      for k in set(original) | set(candidate))
                        optimum = min(optimum, report.total_cost + 0.5 * changes)
                self.assertTrue(evaluate(case, Rules.from_case(case), result.roster).hard_pass)
                self.assertAlmostEqual(repair_cost(case, result), optimum)


if __name__ == "__main__":
    unittest.main()
