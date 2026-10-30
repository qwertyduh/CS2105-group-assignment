import contextlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from roster.case_io import Shift, load_case
from roster import rules as rules_module
from roster.rules import (
    HARD_ONE_SHIFT_PER_DAY,
    HARD_SKILL_MATCHING,
    Rules,
    RulesError,
    format_rules,
    overwork_penalty,
    rest_chain_penalty,
    rest_gap_hours,
    short_rest,
)


SHIFTS = {
    "M": Shift("M", "Morning", "11:00", "15:00", 4, False),
    "E": Shift("E", "Evening", "16:00", "20:00", 4, False),
    "N": Shift("N", "Night", "21:00", "01:00", 4, True),
}


class RestGapTests(unittest.TestCase):
    def test_night_ends_on_the_next_calendar_day(self):
        # Night starts day d at 21:00, ends day d+1 at 01:00.
        # Morning on day d+1 starts at 11:00: 10 hours after that end.
        self.assertEqual(rest_gap_hours(SHIFTS["N"], SHIFTS["M"], 1), 10)

    def test_evening_then_morning_is_fifteen_hours(self):
        self.assertEqual(rest_gap_hours(SHIFTS["E"], SHIFTS["M"], 1), 15)

    def test_night_then_evening_is_fifteen_hours(self):
        self.assertEqual(rest_gap_hours(SHIFTS["N"], SHIFTS["E"], 1), 15)

    def test_same_day_gap(self):
        self.assertEqual(rest_gap_hours(SHIFTS["M"], SHIFTS["N"], 0), 6)

    def test_multi_day_gap(self):
        self.assertEqual(rest_gap_hours(SHIFTS["N"], SHIFTS["M"], 2), 34)

    def test_short_rest_only_for_the_wraparound(self):
        self.assertTrue(short_rest(SHIFTS["N"], SHIFTS["M"]))
        self.assertFalse(short_rest(SHIFTS["E"], SHIFTS["M"]))
        self.assertFalse(short_rest(SHIFTS["N"], SHIFTS["E"]))

    def test_short_rest_honours_a_configured_minimum(self):
        self.assertFalse(short_rest(SHIFTS["N"], SHIFTS["M"], min_rest_hours=10))
        self.assertTrue(short_rest(SHIFTS["N"], SHIFTS["M"], min_rest_hours=10.5))


class OverworkPenaltyTests(unittest.TestCase):
    def test_cumulative_tiers(self):
        cases = {0: 0, 1: 2, 2: 6, 3: 12, 4: 18, 5: 24}
        for extra_shifts, expected in cases.items():
            with self.subTest(extra_shifts=extra_shifts):
                self.assertEqual(overwork_penalty(extra_shifts), expected)

    def test_negative_extra_shifts_cost_nothing(self):
        self.assertEqual(overwork_penalty(-1), 0)

    def test_custom_tiers(self):
        self.assertEqual(
            overwork_penalty(3, tier1=1, tier2=2, tier3_plus=3), 6
        )


class RestChainPenaltyTests(unittest.TestCase):
    def test_cumulative_chain(self):
        # kth violation costs 2 + 1*(k-1): totals 2, 5, 9, 14.
        cases = {0: 0, 1: 2, 2: 5, 3: 9, 4: 14}
        for run_length, expected in cases.items():
            with self.subTest(run_length=run_length):
                self.assertEqual(rest_chain_penalty(run_length), expected)

    def test_custom_base_and_increment(self):
        self.assertEqual(
            rest_chain_penalty(3, base=3, increment=2), 3 + 5 + 7
        )


class RulesFromCaseTests(unittest.TestCase):
    def test_defaults(self):
        rules = Rules()
        self.assertEqual(rules.horizon_days, 20)
        self.assertEqual(rules.weekly_cap_default_hours, 20)
        self.assertEqual(rules.rolling_window_days, 7)
        self.assertEqual(rules.max_consecutive_days, 5)
        self.assertEqual(rules.min_rest_hours, 11)
        self.assertEqual(rules.hard_ceiling_hours_per_window, 0)
        self.assertEqual(rules.penalty_unfilled_slot, 3)
        self.assertEqual(rules.penalty_overwork_tier1, 2)
        self.assertEqual(rules.penalty_overwork_tier2, 4)
        self.assertEqual(rules.penalty_overwork_tier3_plus, 6)
        self.assertEqual(rules.penalty_rest_violation, 2)
        self.assertEqual(rules.penalty_rest_chain_increment, 1)
        self.assertEqual(rules.penalty_streak_day, 4)
        self.assertEqual(rules.weight_preference, 0.05)
        self.assertEqual(rules.weight_fairness, 0.05)
        self.assertEqual(rules.weight_isolated_day_off, 0.05)

    def test_sample_case_config_overrides_defaults(self):
        case = load_case(str(ROOT / "test" / "case_04_small_7d"))
        rules = Rules.from_case(case)
        self.assertEqual(rules.horizon_days, 7)
        self.assertEqual(rules.penalty_unfilled_slot, 3)
        self.assertEqual(rules.weight_fairness, 0.05)

    def test_partial_config_keeps_defaults(self):
        case = SimpleNamespace(
            config={"horizon_days": 14, "penalty_unfilled_slot": 9}
        )
        rules = Rules.from_case(case)
        self.assertEqual(rules.horizon_days, 14)
        self.assertEqual(rules.penalty_unfilled_slot, 9)
        self.assertEqual(rules.weekly_cap_default_hours, 20)

    def test_unknown_config_key_is_an_error(self):
        case = SimpleNamespace(config={"horizon_days": 20, "mystery": 1})
        with self.assertRaises(RulesError) as caught:
            Rules.from_case(case)
        self.assertIn("mystery", str(caught.exception))

    def test_integer_field_rejects_fraction(self):
        case = SimpleNamespace(config={"horizon_days": 7.5})
        with self.assertRaises(RulesError):
            Rules.from_case(case)

    def test_non_numeric_value_is_an_error(self):
        case = SimpleNamespace(config={"penalty_unfilled_slot": "lots"})
        with self.assertRaises(RulesError):
            Rules.from_case(case)

    def test_rules_are_frozen(self):
        with self.assertRaises(Exception):
            Rules().horizon_days = 1


class RulesTableTests(unittest.TestCase):
    def test_table_has_every_rule_and_the_documented_penalties(self):
        table = format_rules()
        expected = {
            "horizon_days": "20",
            "weekly_cap_default_hours": "20",
            "rolling_window_days": "7",
            "max_consecutive_days": "5",
            "min_rest_hours": "11",
            "hard_ceiling_hours_per_window": "0",
            "penalty_unfilled_slot": "3",
            "penalty_overwork_tier1": "2",
            "penalty_overwork_tier2": "4",
            "penalty_overwork_tier3_plus": "6",
            "penalty_rest_violation": "2",
            "penalty_rest_chain_increment": "1",
            "penalty_streak_day": "4",
            "weight_preference": "0.05",
            "weight_fairness": "0.05",
            "weight_isolated_day_off": "0.05",
        }
        for name, value in expected.items():
            with self.subTest(rule=name):
                row = next(line for line in table.splitlines() if line.startswith(name))
                self.assertIn(f" {value} ", row)

    def test_table_lists_both_hard_rules(self):
        table = format_rules()
        self.assertIn(HARD_ONE_SHIFT_PER_DAY, table)
        self.assertIn(HARD_SKILL_MATCHING, table)

    def test_module_cli_prints_table(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            status = rules_module.main([])
        self.assertEqual(status, 0)
        self.assertIn("penalty_unfilled_slot", output.getvalue())
        self.assertIn("Hard rules", output.getvalue())


if __name__ == "__main__":
    unittest.main()
