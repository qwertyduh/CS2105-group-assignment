import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from roster.case_io import load_case
from roster.evaluator import evaluate
from roster.repair.local_repair import repair
from roster.rules import Rules
from roster.solvers.base import save_roster
from roster.solvers.greedy import solve as greedy_solve

ROOT = Path(__file__).resolve().parents[1]
BALANCED = ROOT / "test" / "case_01_balanced_20d"
OVERLOADED = ROOT / "test" / "case_03_overloaded_20d"
SMALL = ROOT / "test" / "case_04_small_7d"


def _frozen_lines(path: Path, sick_day: int) -> list[str]:
    """Raw roster.csv lines for days before D and from D+3 onward."""
    lines = path.read_text(encoding="utf-8").splitlines()
    frozen = [lines[0]]
    for line in lines[1:]:
        day = int(line.split(",")[1])
        if day < sick_day or day >= sick_day + 3:
            frozen.append(line)
    return frozen


def _assigned(case, roster, group, day, shift_id):
    return sum(
        1
        for (roll_no, assigned_day), assigned_shift in roster.items()
        if assigned_day == day
        and assigned_shift == shift_id
        and case.students[roll_no].group == group
    )


class LocalRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = {
            "balanced": load_case(str(BALANCED)),
            "overloaded": load_case(str(OVERLOADED)),
            "small": load_case(str(SMALL)),
        }
        cls.rules = {name: Rules.from_case(case) for name, case in cls.cases.items()}
        cls.rosters = {
            name: greedy_solve(case, 90.0, seed=1)
            for name, case in cls.cases.items()
        }
        cls.reports = {
            name: evaluate(case, cls.rules[name], cls.rosters[name])
            for name, case in cls.cases.items()
        }

    def test_balanced_repair_is_minimal_and_group_local(self):
        case = self.cases["balanced"]
        roster = self.rosters["balanced"]
        report = self.reports["balanced"]
        self.assertTrue(report.hard_pass, report.hard_breaches)
        self.assertEqual(report.unfilled_total, 0)

        # A direct same-group replacement is available for this assignment.
        sick_roll_no, sick_day, shift_id = "22CB03", 1, "N"
        self.assertEqual(roster[(sick_roll_no, sick_day)], shift_id)
        group = case.students[sick_roll_no].group

        original = dict(roster)
        result = repair(case, roster, sick_roll_no, sick_day, shift_id, 30.0, 1)

        self.assertEqual(roster, original, "repair must not mutate the input roster")
        self.assertIsNone(result.reason)
        self.assertLessEqual(len(result.diff), 2)
        self.assertNotIn((sick_roll_no, sick_day), result.roster)

        changed_rolls = {roll_no for roll_no, _day, _old, _new in result.diff}
        changed_days = {day for _roll_no, day, _old, _new in result.diff}
        for roll_no in changed_rolls:
            self.assertEqual(case.students[roll_no].group, group)
        for day in changed_days:
            self.assertIn(day, (sick_day, sick_day + 1))

        repaired_report = evaluate(case, self.rules["balanced"], result.roster)
        self.assertTrue(repaired_report.hard_pass, repaired_report.hard_breaches)

    def test_balanced_repair_keeps_frozen_days_byte_for_byte(self):
        case = self.cases["balanced"]
        roster = self.rosters["balanced"]
        sick_roll_no, sick_day, shift_id = "22CB03", 1, "N"
        result = repair(case, roster, sick_roll_no, sick_day, shift_id, 30.0, 1)

        with tempfile.TemporaryDirectory() as tmp:
            before = Path(tmp) / "before.csv"
            after = Path(tmp) / "after.csv"
            save_roster(roster, str(before))
            save_roster(result.roster, str(after))
            self.assertEqual(
                _frozen_lines(before, sick_day),
                _frozen_lines(after, sick_day),
                "frozen days must be identical byte-for-byte",
            )

    def test_overloaded_repair_leaves_slot_unfilled_with_reason(self):
        case = self.cases["overloaded"]
        roster = self.rosters["overloaded"]
        group = case.students["22CB15"].group
        sick_roll_no, sick_day, shift_id = "22CB15", 4, "E"
        self.assertEqual(roster[(sick_roll_no, sick_day)], shift_id)
        required = case.demand[(sick_day, shift_id, group)]

        result = repair(case, roster, sick_roll_no, sick_day, shift_id, 30.0, 1)

        self.assertIsNotNone(result.reason)
        self.assertIn("unfilled", result.reason)
        self.assertNotIn((sick_roll_no, sick_day), result.roster)
        self.assertLess(
            _assigned(case, result.roster, group, sick_day, shift_id),
            required,
            "the overloaded repair must leave the sick slot short",
        )
        repaired_report = evaluate(case, self.rules["overloaded"], result.roster)
        self.assertTrue(repaired_report.hard_pass, repaired_report.hard_breaches)

    def test_unavailable_next_day_is_also_removed(self):
        case = self.cases["small"]
        roster = self.rosters["small"]
        # 22CS01 works day 2 and day 3; mark both as unavailable.
        self.assertIn(("22CS01", 2), roster)
        self.assertIn(("22CS01", 3), roster)
        shift_id = roster[("22CS01", 2)]
        result = repair(
            case,
            roster,
            "22CS01",
            2,
            shift_id,
            30.0,
            1,
            unavailable_days=[3],
        )
        self.assertNotIn(("22CS01", 2), result.roster)
        self.assertNotIn(("22CS01", 3), result.roster)

    def test_run_cli_repair_prints_diff_and_writes_roster(self):
        with tempfile.TemporaryDirectory() as tmp:
            roster_path = Path(tmp) / "input.roster.csv"
            save_roster(self.rosters["small"], str(roster_path))
            completed = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "roster.run",
                    "repair",
                    str(SMALL),
                    str(roster_path),
                    "--sick",
                    "22CS01",
                    "--day",
                    "3",
                    "--out-dir",
                    tmp,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("total_cost=", completed.stdout)
            self.assertIn('"diff"', completed.stdout)
            self.assertIn('"reason"', completed.stdout)
            written = Path(tmp) / "case_04_small_7d_repair.roster.csv"
            report_path = Path(tmp) / "case_04_small_7d_repair.report.json"
            self.assertTrue(written.is_file())
            self.assertTrue(report_path.is_file())


if __name__ == "__main__":
    unittest.main()
