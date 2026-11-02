import unittest
from pathlib import Path

from roster.case_io import Case, Shift, Student, load_case
from roster.evaluator import GROUPS, evaluate
from roster.rules import Rules
from roster.solvers.cpsat import solve_group

ROOT = Path(__file__).resolve().parents[1]
TOL = 0.01


def group_cost(case, rules, group, roster):
    """The evaluator's cost restricted to one group, from a group-only roster."""
    rep = evaluate(case, rules, roster)
    comp = rep.cost_components
    members = set(case.by_group[group])
    total_w = sum(float(p.weight) for ps in case.prefs.values() for p in ps)
    worked = set(roster.values())
    unsat = 0.0
    for roll in members:
        days = {d: sh for (r, d), sh in roster.items() if r == roll}
        shifts = set(days.values())
        for p in case.prefs.get(roll, []):
            ok = {
                "OFF_DAY": p.day not in days,
                "PREFER_SHIFT": p.shift_id in shifts,
                "AVOID_SHIFT": p.shift_id not in shifts,
            }[p.type]
            if not ok:
                unsat += float(p.weight)
    n_groups = len([g for g in case.by_group if case.by_group[g]])
    longest = max(float(s.hours) for s in case.shifts.values())
    fairness = (
        rules.weight_fairness
        * (rep.workload_spread_by_group[group] / (case.horizon_days * longest))
        / n_groups
    )
    return (
        rep.unfilled_by_group[group] * rules.penalty_unfilled_slot
        + comp["overwork"] + comp["streak"] + comp["rest"] + comp["isolated"]
        + fairness
        + (rules.weight_preference * unsat / total_w if total_w else 0.0)
    ), rep


def one_student_case(horizon, demand_days, config=None, shifts=None, cap=20.0):
    student = Student("S1", "Solo", "MPR", cap)
    shifts = shifts or [Shift("M", "Morning", "11:00", "15:00", 4.0, False)]
    demand = {(d, sh.shift_id, "MPR"): 1 for d in demand_days for sh in shifts[:1]}
    return Case(
        horizon_days=horizon,
        students={student.roll_no: student},
        by_group={"MPR": [student.roll_no]},
        shifts={s.shift_id: s for s in shifts},
        shift_order=[s.shift_id for s in shifts],
        demand=demand,
        prefs={},
        config={"horizon_days": horizon, **(config or {})},
    )


class GroupCostMatchTests(unittest.TestCase):
    def check_case(self, folder, time_limit):
        case = load_case(str(ROOT / "test" / folder))
        rules = Rules.from_case(case)
        merged, total_obj = {}, 0.0
        for group in GROUPS:
            if not case.by_group.get(group):
                continue
            with self.subTest(case=folder, group=group):
                res = solve_group(case, rules, group, time_limit, seed=1)
                self.assertIn(res.status, ("OPTIMAL", "FEASIBLE"))
                cost, rep = group_cost(case, rules, group, res.roster)
                self.assertTrue(rep.hard_pass, rep.hard_breaches)
                self.assertAlmostEqual(res.objective, cost, delta=TOL)
                self.assertLessEqual(res.best_bound, res.objective + 1e-6)
                merged.update(res.roster)
                total_obj += res.objective
        full = evaluate(case, rules, merged)
        self.assertTrue(full.hard_pass)
        self.assertAlmostEqual(total_obj, full.total_cost, delta=TOL)

    def test_case_04_small_7d(self):
        self.check_case("case_04_small_7d", 15)

    def test_case_01_balanced_20d(self):
        self.check_case("case_01_balanced_20d", 20)


class TinyModelTests(unittest.TestCase):
    def solve(self, case):
        rules = Rules.from_case(case)
        res = solve_group(case, rules, "MPR", 10, seed=1)
        self.assertEqual(res.status, "OPTIMAL")
        return rules, res

    def test_overwork_tiers_three_extra(self):
        # cap 16 h = 4 shifts; 7 shifts in the single window = 3 extra = 2 + 4 + 6 = 12.
        # Unfilled is priced high so the solver prefers to overwork.
        case = one_student_case(7, range(1, 8), {"penalty_unfilled_slot": 50, "max_consecutive_days": 7}, cap=16)
        rules, res = self.solve(case)
        cost, rep = group_cost(case, rules, "MPR", res.roster)
        self.assertAlmostEqual(res.objective, cost, delta=TOL)
        self.assertAlmostEqual(res.objective, 12.0, delta=0.2)

    def test_streak_seven_day_run(self):
        # 7 worked days in a row = 2 days beyond the 5th = 8; unfilled priced above that.
        case = one_student_case(7, range(1, 8), {"penalty_unfilled_slot": 20}, cap=100)
        rules, res = self.solve(case)
        cost, rep = group_cost(case, rules, "MPR", res.roster)
        self.assertAlmostEqual(res.objective, cost, delta=TOL)
        self.assertAlmostEqual(res.objective, 8.0, delta=0.2)

    def test_rest_chain_of_three(self):
        shifts = [
            Shift("M", "Morning", "11:00", "15:00", 4.0, False),
            Shift("N", "Night", "21:00", "01:00", 4.0, True),
        ]
        case = one_student_case(6, [], None, shifts, cap=100)
        # N,M,N,M,N,M on days 1..6 forces three short pairs only if N->M: days 1-2, 3-4, 5-6
        demand = {}
        for d in range(1, 7):
            demand[(d, "N" if d % 2 else "M", "MPR")] = 1
        case = Case(**{**case.__dict__, "demand": demand, "config": {"horizon_days": 6, "max_consecutive_days": 6, "penalty_unfilled_slot": 50}})
        rules, res = self.solve(case)
        cost, rep = group_cost(case, rules, "MPR", res.roster)
        self.assertAlmostEqual(res.objective, cost, delta=TOL)
        self.assertEqual(rep.unfilled_total, 0)
        self.assertAlmostEqual(rep.cost_components["rest"], 9.0, delta=TOL)  # 2 + 3 + 4

    def test_hint_not_worse(self):
        case = load_case(str(ROOT / "test" / "case_04_small_7d"))
        rules = Rules.from_case(case)
        group = "MPR"
        cold = solve_group(case, rules, group, 10, seed=1)
        warm = solve_group(case, rules, group, 10, seed=1, hint=cold.roster)
        self.assertLessEqual(warm.objective, cold.objective + 1e-6)


if __name__ == "__main__":
    unittest.main()
