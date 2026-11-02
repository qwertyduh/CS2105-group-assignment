import csv
import random
import time
import unittest
from pathlib import Path

from roster.case_io import load_case
from roster.evaluator import evaluate
from roster.rules import Rules
from roster.solvers.cpsat import solve
from roster.repair.local_repair import repair as local_repair
from roster.repair.cpsat_repair import repair as cpsat_repair


class TestCPSATRepair(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case_01 = load_case("test/case_01_balanced_20d")
        cls.case_03 = load_case("test/case_03_overloaded_20d")
        
        cls.roster_01 = solve(cls.case_01, time_limit_s=10, seed=1)
        cls.roster_03 = solve(cls.case_03, time_limit_s=10, seed=1)

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
        self.assertIn("left day 5", res.reason)
        self.assertIn("unfilled penalty", res.reason)

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
        
        random.seed(42)
        sampled = random.sample(assignments, 20)
        
        results = []
        for idx, ((roll_no, day), sh) in enumerate(sampled):
            unavailable = [day + 1] if random.random() > 0.5 else None
            
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


if __name__ == "__main__":
    unittest.main()
