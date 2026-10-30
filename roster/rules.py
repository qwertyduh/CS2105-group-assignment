"""Typed roster rules, penalty values and pure rule helpers.

Hard rules (never relaxed, never priced in the penalty):

* **One shift per day** — a student works at most one shift in a calendar day.
  This prevents overlapping assignments and keeps hours unambiguous.
* **Skill matching** — a group's demand is filled only by members of that same
  group. A student cannot cover another group's skill requirement.

Everything else is a soft, priced rule. The constants below name the hard
rules and record why they are hard.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Mapping

# Hard rules. These are constraints, not penalty terms.
HARD_ONE_SHIFT_PER_DAY = "one shift per day"
HARD_SKILL_MATCHING = "skill matching"

HARD_RULES = (
    (
        HARD_ONE_SHIFT_PER_DAY,
        "A student works at most one shift per calendar day; this prevents "
        "overlapping assignments and double-counted hours.",
    ),
    (
        HARD_SKILL_MATCHING,
        "A group's demand is filled only by its own members; students cannot "
        "cover another group's skill requirement.",
    ),
)

# Default values, also used by the pure helper functions.
DEFAULT_HORIZON_DAYS = 20
DEFAULT_WEEKLY_CAP_HOURS = 20.0
DEFAULT_ROLLING_WINDOW_DAYS = 7
DEFAULT_MAX_CONSECUTIVE_DAYS = 5
DEFAULT_MIN_REST_HOURS = 11.0
DEFAULT_HARD_CEILING_HOURS = 0.0
DEFAULT_PENALTY_UNFILLED_SLOT = 3.0
DEFAULT_PENALTY_OVERWORK_TIER1 = 2.0
DEFAULT_PENALTY_OVERWORK_TIER2 = 4.0
DEFAULT_PENALTY_OVERWORK_TIER3_PLUS = 6.0
DEFAULT_PENALTY_REST_VIOLATION = 2.0
DEFAULT_PENALTY_REST_CHAIN_INCREMENT = 1.0
DEFAULT_PENALTY_STREAK_DAY = 4.0
DEFAULT_WEIGHT_PREFERENCE = 0.05
DEFAULT_WEIGHT_FAIRNESS = 0.05
DEFAULT_WEIGHT_ISOLATED_DAY_OFF = 0.05


class RulesError(ValueError):
    """Raised when config values cannot be turned into Rules."""


@dataclass(frozen=True)
class Rules:
    """Validated rule and penalty values for one case.

    Field names match the keys in ``config.csv`` so a config value can
    override its field directly.
    """

    horizon_days: int = DEFAULT_HORIZON_DAYS
    weekly_cap_default_hours: float = DEFAULT_WEEKLY_CAP_HOURS
    rolling_window_days: int = DEFAULT_ROLLING_WINDOW_DAYS
    max_consecutive_days: int = DEFAULT_MAX_CONSECUTIVE_DAYS
    min_rest_hours: float = DEFAULT_MIN_REST_HOURS
    hard_ceiling_hours_per_window: float = DEFAULT_HARD_CEILING_HOURS
    penalty_unfilled_slot: float = DEFAULT_PENALTY_UNFILLED_SLOT
    penalty_overwork_tier1: float = DEFAULT_PENALTY_OVERWORK_TIER1
    penalty_overwork_tier2: float = DEFAULT_PENALTY_OVERWORK_TIER2
    penalty_overwork_tier3_plus: float = DEFAULT_PENALTY_OVERWORK_TIER3_PLUS
    penalty_rest_violation: float = DEFAULT_PENALTY_REST_VIOLATION
    penalty_rest_chain_increment: float = DEFAULT_PENALTY_REST_CHAIN_INCREMENT
    penalty_streak_day: float = DEFAULT_PENALTY_STREAK_DAY
    weight_preference: float = DEFAULT_WEIGHT_PREFERENCE
    weight_fairness: float = DEFAULT_WEIGHT_FAIRNESS
    weight_isolated_day_off: float = DEFAULT_WEIGHT_ISOLATED_DAY_OFF

    @classmethod
    def from_case(cls, case) -> "Rules":
        """Build Rules from ``case.config``, overriding the defaults.

        Unknown config keys are an error rather than being silently ignored.
        Integer fields must contain whole numbers.
        """
        config: Mapping[str, object] = getattr(case, "config", {}) or {}
        field_names = {field.name for field in fields(cls)}
        unknown = sorted(set(config) - field_names)
        if unknown:
            keys = ", ".join(repr(key) for key in unknown)
            raise RulesError(f"unknown rule config key(s): {keys}")

        int_fields = {"horizon_days", "rolling_window_days", "max_consecutive_days"}
        values: dict[str, object] = {}
        for name, raw_value in config.items():
            try:
                number = float(raw_value)
            except (TypeError, ValueError) as exc:
                raise RulesError(
                    f"rule config {name!r} is not numeric: {raw_value!r}"
                ) from exc
            if name in int_fields:
                if not number.is_integer():
                    raise RulesError(
                        f"rule config {name!r} must be a whole number, got {raw_value!r}"
                    )
                values[name] = int(number)
            else:
                values[name] = number
        return cls(**values)


def _minutes(value: str) -> int:
    """Parse an ``HH:MM`` time into minutes after midnight."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def rest_gap_hours(shift_a, shift_b, day_gap: int) -> float:
    """Hours from the end of ``shift_a`` on day d to the start of ``shift_b``.

    ``shift_b`` starts ``day_gap`` calendar days after ``shift_a`` starts.
    A shift that crosses midnight, such as Night 21:00-01:00, ends on the
    following calendar day even though it belongs to the day it starts.
    """
    start_a = _minutes(shift_a.start)
    end_a = _minutes(shift_a.end)
    if getattr(shift_a, "crosses_midnight", False):
        end_a += 24 * 60
    elif end_a <= start_a:
        # Defensive fallback for a Shift whose crossing flag is inconsistent.
        end_a += 24 * 60

    start_b = _minutes(shift_b.start) + day_gap * 24 * 60
    return (start_b - end_a) / 60


def short_rest(
    shift_a,
    shift_b,
    min_rest_hours: float = DEFAULT_MIN_REST_HOURS,
) -> bool:
    """True if ``shift_a`` on day d and ``shift_b`` on day d+1 leave too little rest."""
    return rest_gap_hours(shift_a, shift_b, 1) < min_rest_hours


def overwork_penalty(
    extra_shifts: int,
    tier1: float = DEFAULT_PENALTY_OVERWORK_TIER1,
    tier2: float = DEFAULT_PENALTY_OVERWORK_TIER2,
    tier3_plus: float = DEFAULT_PENALTY_OVERWORK_TIER3_PLUS,
) -> float:
    """Cumulative penalty for extra shifts above the cap in one window.

    The 1st extra shift costs ``tier1``, the 2nd costs ``tier2`` and every
    further extra shift costs ``tier3_plus``. With defaults the totals are
    0, 2, 6, 12, 18 for 0..4 extra shifts.
    """
    if extra_shifts <= 0:
        return 0.0
    total = tier1
    if extra_shifts >= 2:
        total += tier2
    if extra_shifts >= 3:
        total += (extra_shifts - 2) * tier3_plus
    return float(total)


def rest_chain_penalty(
    run_length: int,
    base: float = DEFAULT_PENALTY_REST_VIOLATION,
    increment: float = DEFAULT_PENALTY_REST_CHAIN_INCREMENT,
) -> float:
    """Cumulative penalty for a run of consecutive short-rest violations.

    The kth violation in a run costs ``base + increment * (k - 1)``. With
    defaults a run of length 1, 2, 3 costs 2, 5, 9 in total.
    """
    if run_length <= 0:
        return 0.0
    return float(
        sum(base + increment * (k - 1) for k in range(1, run_length + 1))
    )


_DESCRIPTIONS = {
    "horizon_days": "planning horizon (days); 1 to 28",
    "weekly_cap_default_hours": "default cap per rolling window",
    "rolling_window_days": "rolling window length (days)",
    "max_consecutive_days": "working days in a row before the streak penalty",
    "min_rest_hours": "minimum rest gap between shifts (hours)",
    "hard_ceiling_hours_per_window": "absolute hard cap per window; 0 = off",
    "penalty_unfilled_slot": "per missing headcount slot",
    "penalty_overwork_tier1": "1st extra shift above the cap in a window",
    "penalty_overwork_tier2": "2nd extra shift above the cap",
    "penalty_overwork_tier3_plus": "3rd and further extra shifts, each",
    "penalty_rest_violation": "base penalty per short-rest back-to-back pair",
    "penalty_rest_chain_increment": "added for each further violation in a run",
    "penalty_streak_day": "per working day beyond max_consecutive_days",
    "weight_preference": "soft term weight: preferences",
    "weight_fairness": "soft term weight: hours spread within a group",
    "weight_isolated_day_off": "soft term weight: Work-Off-Work pattern",
}


def _format_value(name: str, value) -> str:
    if name == "horizon_days" or name in {
        "rolling_window_days",
        "max_consecutive_days",
    }:
        return str(int(value))
    return f"{float(value):g}"


def format_rules(rules: Rules | None = None) -> str:
    """Return the human-readable rules table."""
    rules = rules or Rules()
    rows = [
        (field.name, _format_value(field.name, getattr(rules, field.name)))
        for field in fields(Rules)
    ]
    name_width = max(len("Rule"), *(len(name) for name, _ in rows))
    value_width = max(len("Value"), *(len(value) for _, value in rows))

    lines = [
        "Roster rules and penalties",
        "",
        f"{'Rule':<{name_width}}  {'Value':>{value_width}}  Description",
        f"{'-' * name_width}  {'-' * value_width}  {'-' * 45}",
    ]
    for name, value in rows:
        lines.append(
            f"{name:<{name_width}}  {value:>{value_width}}  {_DESCRIPTIONS[name]}"
        )

    lines.extend(["", "Hard rules (never relaxed, never priced):"])
    for name, reason in HARD_RULES:
        lines.append(f"  - {name}: {reason}")
    return "\n".join(lines)


def main(argv=None) -> int:
    """CLI: print the rules table with default values."""
    del argv  # The rule table takes no arguments.
    print(format_rules())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
