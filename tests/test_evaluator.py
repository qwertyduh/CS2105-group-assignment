import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from roster.case_io import load_case
from roster.evaluator import evaluate
from roster.rules import Rules
from roster.solvers.base import load_roster, read_roster, save_roster, RosterError

ROOT = Path(__file__).resolve().parents[1]
CASE_04 = ROOT / "test" / "case_04_small_7d"
CASE_03 = ROOT / "test" / "case_03_overloaded_20d"


def no_soft(rules):
    return dataclasses.replace(rules, weight_preference=0, weight_fairness=0, weight_isolated_day_off=0)


class EvaluatorCase04(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = load_case(str(CASE_04))
        cls.rules = Rules.from_case(cls.case)
        cls.total_demand = sum(cls.case.demand.values())
        cls.unit = float(cls.rules.penalty_unfilled_slot)

    def pick(self, plan):
        """Find a student whose group has demand for every (day, shift) in plan and whose cap is 20 h."""
        for group, members in self.case.by_group.items():
            if all(self.case.demand.get((d, s, group), 0) > 0 for d, s in plan):
                for roll in members:
                    if float(self.case.students[roll].max_weekly_hours) == 20:
                        return roll
        self.skipTest(f"no 20 h student with demand for {plan}")

    def quiet_shift(self):
        for s in self.case.shift_order:
            if s != "M":
                return s
        return self.case.shift_order[0]

    def run_eval(self, roster, rules=None):
        return evaluate(self.case, rules or no_soft(self.rules), roster)

    def test_empty_roster_all_demand_unfilled(self):
        report = self.run_eval({})
        self.assertTrue(report.hard_pass)
        self.assertEqual(report.unfilled_total, self.total_demand)
        by_group = {}
        for (_, _, g), req in self.case.demand.items():
            by_group[g] = by_group.get(g, 0) + req
        for g, req in by_group.items():
            self.assertEqual(report.unfilled_by_group[g], req)
        self.assertEqual(report.cost_components["overwork"], 0)
        self.assertEqual(report.cost_components["streak"], 0)
        self.assertEqual(report.cost_components["rest"], 0)
        self.assertAlmostEqual(report.total_cost, self.total_demand * self.unit)
        self.assertTrue(all(h == 0 for h in report.per_student_hours.values()))
        self.assertEqual(report.isolated_dayoffs, 0)

    def test_night_then_morning(self):
        plan = [(3, "N"), (4, "M")]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): s for d, s in plan})
        self.assertTrue(report.hard_pass)
        self.assertEqual(report.cost_components["rest"], 2)
        self.assertEqual(report.cost_components["overwork"], 0)
        self.assertEqual(report.cost_components["streak"], 0)
        self.assertEqual(report.unfilled_total, self.total_demand - 2)
        self.assertAlmostEqual(report.total_cost, (self.total_demand - 2) * self.unit + 2)

    def test_evening_then_morning_is_fine(self):
        plan = [(3, "E"), (4, "M")]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): s for d, s in plan})
        self.assertEqual(report.cost_components["rest"], 0)

    def test_rest_chain_in_one_run(self):
        plan = [(1, "N"), (2, "M"), (3, "N"), (4, "M")]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): s for d, s in plan})
        self.assertEqual(report.cost_components["rest"], 2 + 3)
        self.assertEqual([v["chain_index"] for v in report.rest_violations], [1, 2])

    def test_rest_chain_resets_after_day_off(self):
        plan = [(1, "N"), (2, "M"), (4, "N"), (5, "M")]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): s for d, s in plan})
        self.assertEqual(report.cost_components["rest"], 2 + 2)

    def test_six_day_run(self):
        s = self.quiet_shift()
        plan = [(d, s) for d in range(1, 7)]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): sh for d, sh in plan})
        self.assertTrue(report.hard_pass)
        self.assertEqual(report.cost_components["streak"], 4)
        self.assertEqual(report.cost_components["overwork"], 2)
        self.assertEqual(report.cost_components["rest"], 0)
        self.assertEqual(report.streak_by_student, {roll: 1})
        self.assertAlmostEqual(report.total_cost, (self.total_demand - 6) * self.unit + 4 + 2)

    def test_seven_day_run(self):
        s = self.quiet_shift()
        plan = [(d, s) for d in range(1, 8)]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): sh for d, sh in plan})
        self.assertEqual(report.cost_components["streak"], 8)
        self.assertEqual(report.cost_components["overwork"], 2 + 4)

    def test_six_shift_week_without_streak(self):
        s = self.quiet_shift()
        plan = [(d, s) for d in (1, 2, 3, 5, 6, 7)]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): sh for d, sh in plan})
        self.assertTrue(report.hard_pass)
        self.assertEqual(report.cost_components["overwork"], 2)
        self.assertEqual(report.cost_components["streak"], 0)
        self.assertEqual(report.cost_components["rest"], 0)
        self.assertEqual(report.isolated_dayoffs, 1)
        self.assertEqual(report.per_student_hours[roll], 6 * self.case.shifts[s].hours)
        self.assertAlmostEqual(report.total_cost, (self.total_demand - 6) * self.unit + 2)
        info = report.overwork_by_student[roll]
        self.assertEqual(info["charged_windows"][0]["extra_shifts"], 1)
        self.assertEqual(info["worst_rolling_extra_shifts"], 1)

    def test_five_shift_week_no_overwork(self):
        s = self.quiet_shift()
        plan = [(d, s) for d in (1, 2, 3, 4, 5)]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): sh for d, sh in plan})
        self.assertEqual(report.cost_components["overwork"], 0)
        self.assertEqual(report.cost_components["streak"], 0)

    def test_penalties_stack(self):
        plan = [(1, "E"), (2, "E"), (3, "E"), (4, "E"), (5, "N"), (6, "M")]
        roll = self.pick(plan)
        report = self.run_eval({(roll, d): s for d, s in plan})
        self.assertEqual(report.cost_components["overwork"], 2)
        self.assertEqual(report.cost_components["streak"], 4)
        self.assertEqual(report.cost_components["rest"], 2)

    def test_exceeding_demand_is_hard(self):
        for (day, shift_id, group), req in sorted(self.case.demand.items()):
            if 0 < req < len(self.case.by_group[group]):
                break
        members = self.case.by_group[group][: req + 1]
        report = self.run_eval({(r, day): shift_id for r in members})
        self.assertFalse(report.hard_pass)
        self.assertTrue(any("demand exceeded" in b for b in report.hard_breaches))
        self.assertEqual(report.unfilled_total, self.total_demand - req)

    def test_skill_matching_zero_demand_cell(self):
        case = self.case
        roll = case.by_group[next(iter(case.by_group))][0]
        group = case.students[roll].group
        demand = dict(case.demand)
        demand[(2, "E", group)] = 0
        patched = dataclasses.replace(case, demand=demand)
        report = evaluate(patched, no_soft(self.rules), {(roll, 2): "E"})
        self.assertFalse(report.hard_pass)
        self.assertTrue(any("skill matching" in b for b in report.hard_breaches))

    def test_outside_case_is_hard(self):
        roll = next(iter(self.case.students))
        horizon = self.case.horizon_days
        roster = {(roll, 0): "M", (roll, horizon + 1): "M", ("NOBODY", 1): "M", (roll, 2): "X"}
        report = self.run_eval(roster)
        self.assertFalse(report.hard_pass)
        self.assertEqual(len(report.hard_breaches), 4)
        self.assertEqual(report.unfilled_total, self.total_demand)

    def test_hard_ceiling(self):
        s = self.quiet_shift()
        plan = [(d, s) for d in range(1, 7)]
        roll = self.pick(plan)
        rules = dataclasses.replace(no_soft(self.rules), hard_ceiling_hours_per_window=20)
        report = evaluate(self.case, rules, {(roll, d): sh for d, sh in plan})
        self.assertFalse(report.hard_pass)
        self.assertTrue(any("hard ceiling" in b for b in report.hard_breaches))

    def test_preferences(self):
        roll = self.pick([(1, "M")])
        prefs = {
            roll: [
                SimplePref("OFF_DAY", 1, None, 2),
                SimplePref("PREFER_SHIFT", None, "M", 1),
                SimplePref("AVOID_SHIFT", None, "N", 1),
            ]
        }
        case = dataclasses.replace(self.case, prefs=prefs)
        empty = evaluate(case, self.rules, {})
        self.assertAlmostEqual(empty.preference_satisfaction_pct, 75.0)
        self.assertAlmostEqual(empty.cost_components["preference"], self.rules.weight_preference * 0.25)
        worked = evaluate(case, self.rules, {(roll, 1): "M"})
        self.assertAlmostEqual(worked.preference_satisfaction_pct, 50.0)

    def test_fairness_and_isolated(self):
        s = self.quiet_shift()
        plan = [(d, s) for d in (1, 3, 5)]
        roll = self.pick(plan)
        group = self.case.students[roll].group
        report = evaluate(self.case, self.rules, {(roll, d): sh for d, sh in plan})
        hours = 3 * self.case.shifts[s].hours
        self.assertEqual(report.workload_spread_by_group[group], hours)
        self.assertEqual(report.isolated_dayoffs, 2)
        longest = max(sh.hours for sh in self.case.shifts.values())
        groups_with_members = [g for g, m in self.case.by_group.items() if m]
        expected_fair = self.rules.weight_fairness * (hours / (self.case.horizon_days * longest)) / len(groups_with_members)
        self.assertAlmostEqual(report.cost_components["fairness"], expected_fair)
        expected_iso = self.rules.weight_isolated_day_off * 2 / (len(self.case.students) * (self.case.horizon_days - 2))
        self.assertAlmostEqual(report.cost_components["isolated"], expected_iso)

    def test_soft_terms_stay_below_one_unit(self):
        report = evaluate(self.case, self.rules, {})
        soft = sum(report.cost_components[k] for k in ("preference", "fairness", "isolated"))
        self.assertLess(soft, 1)

    def test_to_json_keys(self):
        data = evaluate(self.case, self.rules, {}).to_json()
        for key in (
            "hard_pass", "hard_breaches", "unfilled_total", "unfilled_by_group", "cost_components",
            "total_cost", "preference_satisfaction_pct", "workload_spread_by_group",
            "isolated_dayoffs", "per_student_hours",
        ):
            self.assertIn(key, data)
        self.assertEqual(
            set(data["cost_components"]),
            {"unfilled", "overwork", "streak", "rest", "preference", "fairness", "isolated"},
        )
        self.assertAlmostEqual(data["total_cost"], sum(data["cost_components"].values()), places=5)
        self.assertIn("non-overlapping", data["overwork_accounting"])
        json.dumps(data)


class SimplePref:
    def __init__(self, type, day, shift_id, weight):
        self.type = type
        self.day = day
        self.shift_id = shift_id
        self.weight = weight


class RosterFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "roster.csv")

    def tearDown(self):
        self.tmp.cleanup()

    def test_round_trip(self):
        roster = {("25CE001", 1): "M", ("25CE001", 3): "N", ("25CE002", 2): "E"}
        save_roster(roster, self.path)
        self.assertEqual(load_roster(self.path), roster)

    def test_two_shifts_one_day_caught_on_load(self):
        with open(self.path, "w") as fh:
            fh.write("roll_no,day,shift_id\n25CE001,1,M\n25CE001,1,E\n")
        roster, problems = read_roster(self.path)
        self.assertEqual(roster, {("25CE001", 1): "M"})
        self.assertEqual(len(problems), 1)
        self.assertIn("one shift per day", problems[0])
        with self.assertRaises(RosterError):
            load_roster(self.path)

    def test_bad_header_and_rows(self):
        with open(self.path, "w") as fh:
            fh.write("roll,day,shift\n")
        self.assertEqual(len(read_roster(self.path)[1]), 1)
        with open(self.path, "w") as fh:
            fh.write("roll_no,day,shift_id\n25CE001,x,M\n25CE001,2\n")
        self.assertEqual(len(read_roster(self.path)[1]), 2)

    def test_load_breaches_fail_hard_pass(self):
        case = load_case(str(CASE_04))
        roll = next(iter(case.students))
        with open(self.path, "w") as fh:
            fh.write(f"roll_no,day,shift_id\n{roll},1,M\n{roll},1,E\n")
        roster, problems = read_roster(self.path)
        report = evaluate(case, Rules.from_case(case), roster, load_breaches=problems)
        self.assertFalse(report.hard_pass)


class Case03AndCli(unittest.TestCase):
    def test_empty_roster_matches_summary(self):
        case = load_case(str(CASE_03))
        report = evaluate(case, Rules.from_case(case), {})
        total = sum(case.demand.values())
        self.assertEqual(report.unfilled_total, total)
        summary_total = summary_demand(CASE_03 / "_summary.json")
        if summary_total is not None:
            self.assertEqual(report.unfilled_total, summary_total)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "roster.csv")
            save_roster({}, path)
            out = subprocess.run(
                [sys.executable, "-m", "roster.evaluator", str(CASE_04), path],
                cwd=ROOT, capture_output=True, text=True, check=True,
            )
        data = json.loads(out.stdout)
        case = load_case(str(CASE_04))
        self.assertTrue(data["hard_pass"])
        self.assertEqual(data["unfilled_total"], sum(case.demand.values()))


def summary_demand(path):
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    totals = []
    per_group = []

    def walk(node, parent_key=""):
        if isinstance(node, dict):
            for key, value in node.items():
                k = str(key).lower()
                if (
                    isinstance(value, int)
                    and not isinstance(value, bool)
                    and "demand" in k
                    and not any(word in k for word in ("ratio", "capacity", "load", "share", "peak"))
                ):
                    if "total" in k or parent_key == "":
                        totals.append(value)
                    elif parent_key in GROUP_KEYS:
                        per_group.append(value)
                walk(value, str(key))
        elif isinstance(node, list):
            for item in node:
                walk(item, parent_key)

    walk(data)
    if totals:
        return int(totals[0])
    if per_group:
        return int(sum(per_group))
    return None


GROUP_KEYS = {"MPR", "CND", "Hospitality", "SPONS"}


if __name__ == "__main__":
    unittest.main()
