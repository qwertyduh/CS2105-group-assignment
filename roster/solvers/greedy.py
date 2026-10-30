"""Dependency-free greedy roster builder.

Algorithm
---------
1. Build every (day, shift_id, group) slot that needs at least one person.
2. Order slots hardest first: highest ``required / group size``, then the
   busiest peak days, then Night shifts. Ties use a seeded RNG so a run is
   reproducible for a given seed.
3. Fill each slot one headcount at a time. A candidate is any member of the
   slot's group who is not already working that day and who would not push any
   rolling window above the enabled hard ceiling. Candidates are ranked by the
   *marginal* rule cost they would add (overwork, streak and rest, priced
   exactly like ``evaluator.py``), with small tie-breakers for fairness and
   preferences. The cheapest candidate is taken.
4. If the cheapest candidate still adds more rule cost than leaving the slot
   unfilled (``penalty_unfilled_slot``), the slot is left short. It is never
   overstaffed and only the group's own members are used, so both hard rules
   always hold.

The solver never looks at wall-clock time for its result unless the optional
``time_limit_s`` deadline is reached, in which case it returns the roster it
has built so far.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from ..evaluator import extra_shifts, rest_gap, rolling_windows
from ..rules import Rules, overwork_penalty, rest_chain_penalty
from .base import Roster

if TYPE_CHECKING:
    from ..case_io import Case

# Tie-breaker scales. Both stay far below one rule unit, so a tie-break can
# never outweigh a priced rule breach (the cheapest breach is 2 units).
_FAIRNESS_SCALE = 0.01
_PREFERENCE_SCALE = 0.002


def _rule_totals(
    case: "Case",
    rules: Rules,
    worked: dict[int, str],
    cap: float,
) -> tuple[float, float, float]:
    """Return (overwork, streak, rest) cost for one student's worked days.

    Mirrors the pricing in ``roster.evaluator``: every rolling window for
    overwork, runs of worked days for streak days, and numbered short-rest pairs
    within one unbroken run for rest.
    """
    horizon = case.horizon_days
    hours_on = {day: float(case.shifts[shift_id].hours) for day, shift_id in worked.items()}

    overwork = 0.0
    for start, end in rolling_windows(horizon, rules.rolling_window_days):
        extra = extra_shifts(
            [hours_on[day] for day in range(start, end + 1) if day in hours_on], cap
        )
        if extra:
            overwork += overwork_penalty(
                extra,
                tier1=rules.penalty_overwork_tier1,
                tier2=rules.penalty_overwork_tier2,
                tier3_plus=rules.penalty_overwork_tier3_plus,
            )

    streak = 0.0
    rest = 0.0
    run = 0
    rest_in_run = 0
    for day in range(1, horizon + 1):
        if day not in worked:
            run = 0
            rest_in_run = 0
            continue
        run += 1
        if run > rules.max_consecutive_days:
            streak += rules.penalty_streak_day
        if day - 1 in worked:
            gap = rest_gap(case, worked[day - 1], worked[day], 1)
            if gap < rules.min_rest_hours - 1e-9:
                rest_in_run += 1
                rest += rest_chain_penalty(
                    rest_in_run,
                    base=rules.penalty_rest_violation,
                    increment=rules.penalty_rest_chain_increment,
                ) - rest_chain_penalty(
                    rest_in_run - 1,
                    base=rules.penalty_rest_violation,
                    increment=rules.penalty_rest_chain_increment,
                )
    return overwork, streak, rest


def _exceeds_hard_ceiling(
    case: "Case",
    rules: Rules,
    worked: dict[int, str],
    ceiling: float,
) -> bool:
    """True if any rolling window in ``worked`` is above the enabled ceiling."""
    if ceiling <= 0:
        return False
    hours_on = {day: float(case.shifts[shift_id].hours) for day, shift_id in worked.items()}
    for start, end in rolling_windows(case.horizon_days, rules.rolling_window_days):
        total = sum(hours_on[day] for day in range(start, end + 1) if day in hours_on)
        if total > ceiling + 1e-9:
            return True
    return False


def _preference_term(case: "Case", roll_no: str, day: int, shift_id: str) -> float:
    """A tiny signed preference score for placing this student on this shift.

    Negative is desirable (preferred shift), positive is undesirable (a
    disliked shift or a requested day off). Magnitudes stay far below 1 unit.
    """
    term = 0.0
    for pref in case.prefs.get(roll_no, ()):
        weight = float(pref.weight)
        kind = getattr(pref, "type", None) or getattr(pref, "kind", None)
        if kind == "OFF_DAY":
            if pref.day == day:
                term += weight
        elif kind == "PREFER_SHIFT":
            if pref.shift_id == shift_id:
                term -= weight
        elif kind == "AVOID_SHIFT":
            if pref.shift_id == shift_id:
                term += weight
    return _PREFERENCE_SCALE * term


def _is_night(shift) -> bool:
    name = (getattr(shift, "name", "") or "").strip().lower()
    return bool(getattr(shift, "crosses_midnight", False)) or name == "night"


def _ordered_slots(case: "Case", seed: int) -> list[tuple[int, str, str, int]]:
    """Hardest-first slot order with a seeded random tie-break."""
    rng = random.Random(seed)
    day_totals: dict[int, int] = {}
    for (day, _shift_id, _group), required in case.demand.items():
        day_totals[day] = day_totals.get(day, 0) + required

    decorated = []
    for (day, shift_id, group), required in case.demand.items():
        if required <= 0:
            continue
        group_size = len(case.by_group.get(group, ()))
        load = required / group_size if group_size else 1.0
        night_rank = 0 if _is_night(case.shifts[shift_id]) else 1
        key = (-load, -day_totals.get(day, 0), night_rank, rng.random())
        decorated.append((key, (day, shift_id, group, required)))

    decorated.sort(key=lambda item: item[0])
    return [slot for _key, slot in decorated]


def solve(case: "Case", time_limit_s: float = 90.0, seed: int = 0) -> Roster:
    """Build a roster greedily; hard rules always hold and gaps are priced."""
    rules = Rules.from_case(case)
    horizon = case.horizon_days
    roster: Roster = {}
    worked: dict[str, dict[int, str]] = {roll: {} for roll in case.students}
    group_hours: dict[str, float] = {group: 0.0 for group in case.by_group}
    rule_cost: dict[str, float] = {}

    caps = {
        roll: float(student.max_weekly_hours or rules.weekly_cap_default_hours)
        for roll, student in case.students.items()
    }
    ceiling = float(rules.hard_ceiling_hours_per_window)
    deadline = time.monotonic() + max(0.0, float(time_limit_s))

    for day, shift_id, group, required in _ordered_slots(case, seed):
        if time.monotonic() > deadline:
            break
        members = case.by_group.get(group, [])
        if not members:
            continue
        group_size = len(members)
        shift_hours = float(case.shifts[shift_id].hours)
        filled = 0

        while filled < required:
            if time.monotonic() > deadline:
                break
            mean_hours = group_hours[group] / group_size
            best = None
            for index, roll_no in enumerate(members):
                if day in worked[roll_no]:
                    continue
                old_days = worked[roll_no]
                new_days = dict(old_days)
                new_days[day] = shift_id
                if _exceeds_hard_ceiling(case, rules, new_days, ceiling):
                    continue

                base = rule_cost.get(roll_no)
                if base is None:
                    base = sum(_rule_totals(case, rules, old_days, caps[roll_no]))
                    rule_cost[roll_no] = base
                new_total = sum(_rule_totals(case, rules, new_days, caps[roll_no]))
                incremental = new_total - base

                hours_after = sum(
                    float(case.shifts[s].hours) for s in new_days.values()
                )
                fairness = _FAIRNESS_SCALE * (hours_after - mean_hours)
                preference = _preference_term(case, roll_no, day, shift_id)
                score = incremental + fairness + preference
                key = (round(score, 9), incremental, len(old_days), index)
                if best is None or key < best[0]:
                    best = (key, roll_no, incremental)

            if best is None:
                break  # nobody in the group is free that day

            _key, roll_no, incremental = best
            if incremental > float(rules.penalty_unfilled_slot) + 1e-9:
                break  # a gap is cheaper than this breach

            roster[(roll_no, day)] = shift_id
            worked[roll_no][day] = shift_id
            rule_cost.pop(roll_no, None)
            group_hours[group] += shift_hours
            filled += 1

    return roster
