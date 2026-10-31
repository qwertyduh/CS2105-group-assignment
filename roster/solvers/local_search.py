"""Simulated-annealing local search over the greedy roster.

The search keeps the roster plus per-student worked-day maps, per-cell coverage
counts and the soft-term totals up to date.  A move only touches one or two
students and the days of their assignments, so its cost delta is computed from
those affected students, days, cells and (for fairness) their group only.  The
whole roster is never re-evaluated inside the search loop.

Moves
-----
* reassign   move an assigned slot to a free student of the same group/day
* swap       exchange the shifts of two assigned students of a group on a day
* fill       assign a free student to a short demand cell
* drop       remove one assignment
* shift-change  move a student to another shift on the same day if it has room

Acceptance is simulated annealing with geometric cooling.  The start
temperature is calibrated on sampled worsening moves so about 30% of them are
accepted initially.  The temperature is reheated once after a long stretch
without a new best.  The function always returns the best roster seen.

Set ``debug=True`` (or ``ROSTER_LOCAL_DEBUG=1``) to cross-check the incremental
total against ``evaluator.evaluate`` every 1000 accepted moves and at the end.
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..evaluator import evaluate, extra_shifts, rest_gap, rolling_windows
from ..rules import Rules, overwork_penalty, rest_chain_penalty
from .base import Roster
from .greedy import solve as _greedy_solve

if TYPE_CHECKING:
    from ..case_io import Case

GROUPS = ("MPR", "CND", "Hospitality", "SPONS")

DEBUG = os.environ.get("ROSTER_LOCAL_DEBUG", "").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}

_EPS = 1e-12
_ACCEPT_TARGET = 0.30
_T_END_RATIO = 1e-4
_ITERS_PER_SECOND = 6000


@dataclass(frozen=True)
class _Move:
    kind: str
    removes: tuple[tuple[str, int, str], ...] = ()
    adds: tuple[tuple[str, int, str], ...] = ()


def _preference_satisfied(prefs, worked: dict[int, str]) -> float:
    worked_shifts = set(worked.values())
    satisfied = 0.0
    for pref in prefs:
        weight = float(pref.weight)
        kind = getattr(pref, "type", None) or getattr(pref, "kind", None)
        if kind == "OFF_DAY":
            ok = pref.day not in worked
        elif kind == "PREFER_SHIFT":
            ok = pref.shift_id in worked_shifts
        elif kind == "AVOID_SHIFT":
            ok = pref.shift_id not in worked_shifts
        else:
            ok = True
        if ok:
            satisfied += weight
    return satisfied


def _isolated_count(worked: dict[int, str], horizon: int) -> int:
    return sum(
        1
        for day in range(2, horizon)
        if day - 1 in worked and day not in worked and day + 1 in worked
    )


def _window_overwork(hours_on, start, end, cap, rules: Rules) -> float:
    window_hours = [hours_on[day] for day in range(start, end + 1) if day in hours_on]
    extra = extra_shifts(window_hours, cap)
    if not extra:
        return 0.0
    return overwork_penalty(
        extra,
        tier1=rules.penalty_overwork_tier1,
        tier2=rules.penalty_overwork_tier2,
        tier3_plus=rules.penalty_overwork_tier3_plus,
    )


def _streak_rest_cost(case: "Case", rules: Rules, worked: dict[int, str]):
    horizon = case.horizon_days
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
    return streak, rest


def _student_rule_cost(
    case: "Case", rules: Rules, worked: dict[int, str], cap: float
) -> float:
    hours_on = {
        day: float(case.shifts[shift_id].hours)
        for day, shift_id in worked.items()
    }
    overwork = 0.0
    for start, end in rolling_windows(case.horizon_days, rules.rolling_window_days):
        overwork += _window_overwork(hours_on, start, end, cap, rules)
    streak, rest = _streak_rest_cost(case, rules, worked)
    return overwork + streak + rest


def _student_rule_delta(
    case: "Case",
    rules: Rules,
    old: dict[int, str],
    new: dict[int, str],
    cap: float,
) -> float:
    """Rule-cost delta; overwork only scans windows containing changed days."""
    affected = {
        day for day in set(old) | set(new) if old.get(day) != new.get(day)
    }
    if not affected:
        return 0.0

    old_hours = {d: float(case.shifts[s].hours) for d, s in old.items()}
    new_hours = {d: float(case.shifts[s].hours) for d, s in new.items()}
    overwork = 0.0
    for start, end in rolling_windows(case.horizon_days, rules.rolling_window_days):
        if any(start <= day <= end for day in affected):
            overwork += _window_overwork(new_hours, start, end, cap, rules)
            overwork -= _window_overwork(old_hours, start, end, cap, rules)

    old_streak, old_rest = _streak_rest_cost(case, rules, old)
    new_streak, new_rest = _streak_rest_cost(case, rules, new)
    return overwork + (new_streak - old_streak) + (new_rest - old_rest)


class _State:
    """Roster plus all quantities needed to price a move locally."""

    def __init__(self, case: "Case", rules: Rules, roster: Roster):
        self.case = case
        self.rules = rules
        self.roster: Roster = dict(roster)
        self.groups = list(dict.fromkeys(list(GROUPS) + list(case.by_group)))
        self.horizon = case.horizon_days
        self.windows = rolling_windows(self.horizon, rules.rolling_window_days)
        self.ceiling = float(rules.hard_ceiling_hours_per_window)
        self.caps = {
            roll: float(student.max_weekly_hours or rules.weekly_cap_default_hours)
            for roll, student in case.students.items()
        }

        self.worked: dict[str, dict[int, str]] = {
            roll: {} for roll in case.students
        }
        for (roll, day), shift_id in self.roster.items():
            self.worked[roll][day] = shift_id

        self.assigned_keys: list[tuple[str, int]] = []
        self.assigned_index: dict[tuple[str, int], int] = {}
        self.group_day: dict[tuple[str, int], list[str]] = {}
        self.group_day_index: dict[tuple[str, int, str], int] = {}

        self.cell_counts = {cell: 0 for cell in case.demand}
        for key in sorted(self.roster):
            roll, day = key
            shift_id = self.roster[key]
            group = case.students[roll].group
            self._list_add(self.assigned_keys, self.assigned_index, key)
            cell = (day, shift_id, group)
            self.cell_counts[cell] = self.cell_counts.get(cell, 0) + 1
            self._group_day_add(group, day, roll)

        self.deficient_keys: list[tuple[int, str, str]] = []
        self.deficient_index: dict[tuple[int, str, str], int] = {}
        self.unfilled_total = 0
        for cell in sorted(case.demand):
            required = case.demand[cell]
            count = self.cell_counts.get(cell, 0)
            if count < required:
                self._list_add(self.deficient_keys, self.deficient_index, cell)
                self.unfilled_total += required - count

        self.student_hours: dict[str, float] = {}
        self.student_rule_cost: dict[str, float] = {}
        self.student_isolated: dict[str, int] = {}
        self.student_pref_sat: dict[str, float] = {}
        for roll in case.students:
            worked = self.worked[roll]
            self.student_hours[roll] = sum(
                float(case.shifts[s].hours) for s in worked.values()
            )
            self.student_rule_cost[roll] = _student_rule_cost(
                case, rules, worked, self.caps[roll]
            )
            self.student_isolated[roll] = _isolated_count(worked, self.horizon)
            self.student_pref_sat[roll] = _preference_satisfied(
                case.prefs.get(roll, ()), worked
            )

        self.total_pref_weight = sum(
            float(pref.weight)
            for prefs in case.prefs.values()
            for pref in prefs
        )
        self.total_pref_sat = 0.0
        for roll, prefs in case.prefs.items():
            if roll in self.student_pref_sat:
                self.total_pref_sat += self.student_pref_sat[roll]
            else:
                # Unknown roll numbers never work, matching the evaluator.
                self.total_pref_sat += _preference_satisfied(prefs, {})

        self.max_possible = self.horizon * max(
            (float(shift.hours) for shift in case.shifts.values()), default=0.0
        )
        self.group_spread: dict[str, float] = {}
        self.fairness_sum = 0.0
        self.fairness_groups = [g for g in self.groups if case.by_group.get(g)]
        for group in self.groups:
            members = case.by_group.get(group, [])
            if not members:
                self.group_spread[group] = 0.0
                continue
            hours = [self.student_hours[r] for r in members]
            spread = max(hours) - min(hours)
            self.group_spread[group] = spread
            if self.max_possible:
                self.fairness_sum += spread / self.max_possible

        self.rule_total = sum(self.student_rule_cost.values())
        self.isolated_total = sum(self.student_isolated.values())
        self.iso_slots = len(case.students) * max(0, self.horizon - 2)
        self.cost = self._compute_cost()

    @staticmethod
    def _list_add(items, index, key) -> None:
        index[key] = len(items)
        items.append(key)

    @staticmethod
    def _list_remove(items, index, key) -> None:
        position = index.pop(key)
        last = items.pop()
        if position < len(items):
            items[position] = last
            index[last] = position

    def _group_day_add(self, group: str, day: int, roll: str) -> None:
        rolls = self.group_day.setdefault((group, day), [])
        self.group_day_index[(group, day, roll)] = len(rolls)
        rolls.append(roll)

    def _group_day_remove(self, group: str, day: int, roll: str) -> None:
        rolls = self.group_day[(group, day)]
        key = (group, day, roll)
        position = self.group_day_index.pop(key)
        last = rolls.pop()
        if position < len(rolls):
            rolls[position] = last
            self.group_day_index[(group, day, last)] = position

    def _compute_cost(self) -> float:
        cost = (
            self.unfilled_total * float(self.rules.penalty_unfilled_slot)
            + self.rule_total
        )
        if self.total_pref_weight:
            cost += self.rules.weight_preference * (
                1.0 - self.total_pref_sat / self.total_pref_weight
            )
        if self.fairness_groups and self.max_possible:
            cost += self.rules.weight_fairness * (
                self.fairness_sum / len(self.fairness_groups)
            )
        if self.iso_slots:
            cost += self.rules.weight_isolated_day_off * (
                self.isolated_total / self.iso_slots
            )
        return cost

    def _adjust_cell(self, cell, change: int) -> None:
        old_count = self.cell_counts.get(cell, 0)
        new_count = old_count + change
        required = int(self.case.demand.get(cell, 0))
        old_missing = max(0, required - old_count)
        new_missing = max(0, required - new_count)
        self.unfilled_total += new_missing - old_missing

        was_deficient = cell in self.deficient_index
        now_deficient = new_count < required
        if now_deficient and not was_deficient:
            self._list_add(self.deficient_keys, self.deficient_index, cell)
        elif was_deficient and not now_deficient:
            self._list_remove(self.deficient_keys, self.deficient_index, cell)
        self.cell_counts[cell] = new_count

    def _add_assignment(self, roll: str, day: int, shift_id: str) -> None:
        key = (roll, day)
        self.roster[key] = shift_id
        self._list_add(self.assigned_keys, self.assigned_index, key)
        group = self.case.students[roll].group
        self._adjust_cell((day, shift_id, group), 1)
        self._group_day_add(group, day, roll)

    def _remove_assignment(self, roll: str, day: int) -> None:
        key = (roll, day)
        shift_id = self.roster.pop(key)
        self._list_remove(self.assigned_keys, self.assigned_index, key)
        group = self.case.students[roll].group
        self._adjust_cell((day, shift_id, group), -1)
        self._group_day_remove(group, day, roll)

    def _ceiling_ok(self, worked: dict[int, str]) -> bool:
        if self.ceiling <= 0:
            return True
        hours_on = {
            day: float(self.case.shifts[s].hours)
            for day, s in worked.items()
        }
        for start, end in self.windows:
            total = sum(
                hours_on[day] for day in range(start, end + 1) if day in hours_on
            )
            if total > self.ceiling + 1e-9:
                return False
        return True

    def delta(self, move: _Move):
        """Return the cost change for ``move`` or None if it breaks a hard rule."""
        affected_rolls = sorted(
            {roll for roll, _day, _shift in move.removes}
            | {roll for roll, _day, _shift in move.adds}
        )
        new_worked: dict[str, dict[int, str]] = {}
        for roll in affected_rolls:
            worked = dict(self.worked[roll])
            for r, day, shift_id in move.removes:
                if r != roll:
                    continue
                if worked.get(day) != shift_id:
                    return None
                del worked[day]
            for r, day, shift_id in move.adds:
                if r != roll:
                    continue
                if day in worked:
                    return None
                worked[day] = shift_id
            if not self._ceiling_ok(worked):
                return None
            new_worked[roll] = worked

        rule_delta = 0.0
        isolated_delta = 0
        pref_delta = 0.0
        hour_delta: dict[str, float] = {}
        for roll in affected_rolls:
            old = self.worked[roll]
            new = new_worked[roll]
            rule_delta += _student_rule_delta(
                self.case, self.rules, old, new, self.caps[roll]
            )
            isolated_delta += (
                _isolated_count(new, self.horizon) - self.student_isolated[roll]
            )
            pref_delta += _preference_satisfied(
                self.case.prefs.get(roll, ()), new
            ) - self.student_pref_sat[roll]
            old_hours = sum(
                float(self.case.shifts[s].hours) for s in old.values()
            )
            new_hours = sum(
                float(self.case.shifts[s].hours) for s in new.values()
            )
            hour_delta[roll] = new_hours - old_hours

        cell_change: dict[tuple[int, str, str], int] = {}
        for roll, day, shift_id in move.removes:
            group = self.case.students[roll].group
            cell = (day, shift_id, group)
            cell_change[cell] = cell_change.get(cell, 0) - 1
        for roll, day, shift_id in move.adds:
            group = self.case.students[roll].group
            cell = (day, shift_id, group)
            cell_change[cell] = cell_change.get(cell, 0) + 1

        unfilled_delta = 0
        for cell in sorted(cell_change):
            change = cell_change[cell]
            if not change:
                continue
            required = int(self.case.demand.get(cell, 0))
            old_count = self.cell_counts.get(cell, 0)
            old_missing = max(0, required - old_count)
            new_missing = max(0, required - (old_count + change))
            unfilled_delta += new_missing - old_missing

        fairness_delta = 0.0
        affected_groups = sorted(
            {self.case.students[roll].group for roll in affected_rolls}
        )
        if self.max_possible:
            for group in affected_groups:
                members = self.case.by_group.get(group, [])
                if not members:
                    continue
                new_hours = [
                    self.student_hours[r] + hour_delta.get(r, 0.0)
                    for r in members
                ]
                new_spread = max(new_hours) - min(new_hours)
                fairness_delta += (
                    new_spread - self.group_spread.get(group, 0.0)
                ) / self.max_possible

        delta = (
            unfilled_delta * float(self.rules.penalty_unfilled_slot)
            + rule_delta
        )
        if self.total_pref_weight:
            delta += self.rules.weight_preference * (
                -pref_delta / self.total_pref_weight
            )
        if self.fairness_groups and self.max_possible:
            delta += self.rules.weight_fairness * (
                fairness_delta / len(self.fairness_groups)
            )
        if self.iso_slots:
            delta += self.rules.weight_isolated_day_off * (
                isolated_delta / self.iso_slots
            )
        return delta

    def apply(self, move: _Move) -> None:
        affected_rolls = sorted(
            {roll for roll, _day, _shift in move.removes}
            | {roll for roll, _day, _shift in move.adds}
        )
        affected_groups = sorted(
            {self.case.students[roll].group for roll in affected_rolls}
        )

        for roll, day, _shift in move.removes:
            self._remove_assignment(roll, day)
            del self.worked[roll][day]
        for roll, day, shift_id in move.adds:
            self.worked[roll][day] = shift_id
            self._add_assignment(roll, day, shift_id)

        for roll in affected_rolls:
            worked = self.worked[roll]
            new_rule = _student_rule_cost(
                self.case, self.rules, worked, self.caps[roll]
            )
            new_isolated = _isolated_count(worked, self.horizon)
            new_pref = _preference_satisfied(
                self.case.prefs.get(roll, ()), worked
            )
            new_hours = sum(
                float(self.case.shifts[s].hours) for s in worked.values()
            )
            self.rule_total += new_rule - self.student_rule_cost[roll]
            self.isolated_total += new_isolated - self.student_isolated[roll]
            self.total_pref_sat += new_pref - self.student_pref_sat[roll]
            self.student_rule_cost[roll] = new_rule
            self.student_isolated[roll] = new_isolated
            self.student_pref_sat[roll] = new_pref
            self.student_hours[roll] = new_hours

        if self.max_possible:
            for group in affected_groups:
                members = self.case.by_group.get(group, [])
                if not members:
                    continue
                hours = [self.student_hours[r] for r in members]
                new_spread = max(hours) - min(hours)
                self.fairness_sum += (
                    new_spread - self.group_spread.get(group, 0.0)
                ) / self.max_possible
                self.group_spread[group] = new_spread

        self.cost = self._compute_cost()

    def _random_assignment(self, rng: random.Random):
        if not self.assigned_keys:
            return None
        return self.assigned_keys[rng.randrange(len(self.assigned_keys))]

    def _propose_reassign(self, rng: random.Random):
        key = self._random_assignment(rng)
        if key is None:
            return None
        roll, day = key
        shift_id = self.roster[key]
        group = self.case.students[roll].group
        members = self.case.by_group.get(group, ())
        if len(members) < 2:
            return None
        start = rng.randrange(len(members))
        for offset in range(len(members)):
            other = members[(start + offset) % len(members)]
            if other != roll and day not in self.worked[other]:
                return _Move(
                    "reassign",
                    removes=((roll, day, shift_id),),
                    adds=((other, day, shift_id),),
                )
        return None

    def _propose_swap(self, rng: random.Random):
        key = self._random_assignment(rng)
        if key is None:
            return None
        roll, day = key
        group = self.case.students[roll].group
        gd_key = (group, day)
        members = self.group_day.get(gd_key, [])
        if len(members) < 2:
            return None
        position = self.group_day_index[(group, day, roll)]
        other_position = rng.randrange(len(members) - 1)
        if other_position >= position:
            other_position += 1
        other = members[other_position]
        shift_id = self.roster[key]
        other_shift = self.roster[(other, day)]
        if shift_id == other_shift:
            return None
        return _Move(
            "swap",
            removes=((roll, day, shift_id), (other, day, other_shift)),
            adds=((roll, day, other_shift), (other, day, shift_id)),
        )

    def _propose_fill(self, rng: random.Random):
        if not self.deficient_keys:
            return None
        start = rng.randrange(len(self.deficient_keys))
        for offset in range(len(self.deficient_keys)):
            cell = self.deficient_keys[
                (start + offset) % len(self.deficient_keys)
            ]
            day, shift_id, group = cell
            if self.cell_counts.get(cell, 0) >= self.case.demand.get(cell, 0):
                continue
            members = self.case.by_group.get(group, ())
            if not members:
                continue
            member_start = rng.randrange(len(members))
            for member_offset in range(len(members)):
                roll = members[(member_start + member_offset) % len(members)]
                if day not in self.worked[roll]:
                    return _Move(
                        "fill",
                        adds=((roll, day, shift_id),),
                    )
        return None

    def _propose_drop(self, rng: random.Random):
        key = self._random_assignment(rng)
        if key is None:
            return None
        roll, day = key
        return _Move(
            "drop",
            removes=((roll, day, self.roster[key]),),
        )

    def _propose_shift_change(self, rng: random.Random):
        key = self._random_assignment(rng)
        if key is None:
            return None
        roll, day = key
        group = self.case.students[roll].group
        old_shift = self.roster[key]
        candidates = []
        for shift_id in self.case.shift_order:
            if shift_id == old_shift:
                continue
            cell = (day, shift_id, group)
            required = self.case.demand.get(cell, 0)
            if self.cell_counts.get(cell, 0) < required:
                candidates.append(shift_id)
        if not candidates:
            return None
        new_shift = candidates[rng.randrange(len(candidates))]
        return _Move(
            "shift-change",
            removes=((roll, day, old_shift),),
            adds=((roll, day, new_shift),),
        )

    def propose(self, rng: random.Random):
        draw = rng.random()
        if draw < 0.30:
            return self._propose_reassign(rng)
        if draw < 0.55:
            return self._propose_swap(rng)
        if draw < 0.75:
            return self._propose_fill(rng)
        if draw < 0.85:
            return self._propose_drop(rng)
        return self._propose_shift_change(rng)


def _calibrate_temperature(
    state: _State,
    rng: random.Random,
    deadline: float,
    samples: int = 250,
) -> tuple[float, float]:
    worsening: list[float] = []
    attempts = 0
    while attempts < samples and time.monotonic() < deadline:
        attempts += 1
        move = state.propose(rng)
        if move is None:
            continue
        delta = state.delta(move)
        if delta is not None and delta > _EPS:
            worsening.append(delta)

    if not worsening:
        return 1.0, 1.0

    low, high = 0.0, max(worsening) * 10.0
    for _ in range(60):
        mid = (low + high) / 2.0
        if mid <= 0:
            low = 1e-12
            continue
        average = sum(math.exp(-d / mid) for d in worsening) / len(worsening)
        if average < _ACCEPT_TARGET:
            low = mid
        else:
            high = mid
    temperature = max((low + high) / 2.0, 1e-12)
    accepted = sum(
        math.exp(-d / temperature) for d in worsening
    ) / len(worsening)
    return temperature, accepted


def _check_against_evaluator(
    case: "Case",
    rules: Rules,
    roster: Roster,
    incremental: float,
    label: str,
) -> None:
    report = evaluate(case, rules, roster)
    if not report.hard_pass:
        raise AssertionError(
            f"local search hard breach ({label}): {report.hard_breaches[:3]}"
        )
    if abs(report.total_cost - incremental) > 1e-6:
        raise AssertionError(
            f"incremental/full cost mismatch ({label}): "
            f"incremental={incremental:.12f} full={report.total_cost:.12f} "
            f"components={report.cost_components}"
        )


def solve(
    case: "Case",
    time_limit_s: float = 90.0,
    seed: int = 0,
    debug: bool = False,
) -> Roster:
    """Improve the greedy roster with simulated annealing; return the best seen."""
    started = time.monotonic()
    limit = max(0.0, float(time_limit_s))
    deadline = started + limit
    check = bool(debug or DEBUG)
    rules = Rules.from_case(case)
    rng = random.Random(seed)

    # Start from the greedy roster for the same seed.  Greedy is fast; the
    # local search stops at its own deadline regardless.
    roster = _greedy_solve(case, limit, seed)
    if time.monotonic() >= deadline:
        if check:
            _check_against_evaluator(
                case,
                rules,
                roster,
                evaluate(case, rules, roster).total_cost,
                "greedy-only/timeout",
            )
        return roster

    state = _State(case, rules, roster)
    best_cost = state.cost
    best_roster = dict(state.roster)
    if check:
        _check_against_evaluator(
            case, rules, state.roster, state.cost, "initial"
        )

    start_temperature, _ = _calibrate_temperature(state, rng, deadline)
    temperature = start_temperature

    # Deterministic iteration budget (wall-clock is still a hard safety stop),
    # so the same seed and time limit reproduce the same search on a machine
    # fast enough to reach the budget.
    max_iterations = max(1000, int(limit * _ITERS_PER_SECOND))
    alpha = (
        _T_END_RATIO ** (1.0 / max_iterations)
        if temperature > 0 and max_iterations > 0
        else 1.0
    )

    reheat_after = max(2000, int(max_iterations * 0.12))
    stop_after = min(
        max_iterations,
        max(4 * reheat_after, int(max_iterations * 0.85)),
    )
    last_improvement = 0
    reheated = False
    accepted = 0
    accepted_since_check = 0
    iterations = 0
    no_move_streak = 0

    while iterations < max_iterations:
        if time.monotonic() >= deadline:
            break
        iterations += 1

        move = None
        for _ in range(8):
            move = state.propose(rng)
            if move is not None:
                break
        if move is None:
            no_move_streak += 1
            if no_move_streak >= 100:
                break
            continue
        no_move_streak = 0

        delta = state.delta(move)
        if delta is None:
            continue

        take = delta <= _EPS
        if not take:
            take = rng.random() < math.exp(-delta / temperature)
        if take:
            state.apply(move)
            accepted += 1
            accepted_since_check += 1
            if state.cost < best_cost - 1e-12:
                best_cost = state.cost
                best_roster = dict(state.roster)
                last_improvement = iterations

            if check and accepted_since_check >= 1000:
                _check_against_evaluator(
                    case,
                    rules,
                    state.roster,
                    state.cost,
                    f"after {accepted} accepted moves",
                )
                accepted_since_check = 0

        temperature *= alpha

        stuck = iterations - last_improvement
        if not reheated and stuck >= reheat_after:
            # A single reheat back toward the calibrated starting temperature.
            temperature = max(temperature, start_temperature)
            reheated = True
        if stuck >= stop_after:
            break

    if check:
        _check_against_evaluator(
            case, rules, best_roster, best_cost, "final/best"
        )
        if state.roster != best_roster:
            _check_against_evaluator(
                case, rules, state.roster, state.cost, "final/current"
            )
    return best_roster
