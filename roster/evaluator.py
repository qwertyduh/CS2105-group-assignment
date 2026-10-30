"""The single scorer. Every solver and repair is judged by evaluate() and nothing else.

The report is recomputed from the roster alone, never from solver internals, so a
modelling bug in a solver shows up here as a breach or a cost it did not expect.

Hard rules (any breach sets hard_pass to False):
  * one shift per day: guaranteed by the Roster type; a roster.csv that lists a
    student twice on a day is reported by read_roster() and passed in as load_breaches
  * skill matching: a student only fills a cell of their own group that has demand
  * demand is met exactly, never exceeded, in every (day, shift_id, group) cell
  * every assignment names a known student, a day in 1..horizon_days and a known shift
  * if hard_ceiling_hours_per_window > 0: no rolling window may exceed it

Priced rule breaches (units):
  * unfilled:  penalty_unfilled_slot per missing person per cell
  * overwork:  see OVERWORK_ACCOUNTING below
  * streak:    penalty_streak_day for every worked day beyond max_consecutive_days in a run
  * rest:      a short-rest pair is two shifts on adjacent days with a gap below
               min_rest_hours (Night then next-day Morning with the default shifts).
               Pairs are numbered within one unbroken run of worked days; the k-th
               pair in a run costs penalty_rest_violation + penalty_rest_chain_increment*(k-1).
               A day off ends the run and restarts the numbering.

Soft terms, each scaled into [0, its weight] so together they stay well below 1 unit:
  * preference = weight_preference * (1 - satisfied_weight / total_weight)
      OFF_DAY honoured if the student does not work that day, PREFER_SHIFT honoured if
      the student gets that shift at least once, AVOID_SHIFT honoured if never.
  * fairness   = weight_fairness * mean over groups of spread / max_possible_hours,
      spread = max minus min hours among the group's students (zeros included),
      max_possible_hours = horizon_days * longest shift.
  * isolated   = weight_isolated_day_off * isolated / (students * (horizon_days - 2)),
      isolated = number of Work-Off-Work patterns (worked d-1, off d, worked d+1).
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from .case_io import Case, load_case
from .rules import Rules, overwork_penalty, rest_chain_penalty
from .solvers.base import Roster, read_roster

GROUPS = ("MPR", "CND", "Hospitality", "SPONS")

OVERWORK_ACCOUNTING = (
    "Overwork is charged on every rolling window of rolling_window_days (days 1-7, 2-8, "
    "3-9, ... by default; the last window ends at the horizon). In each window, shifts are "
    "taken in day order and every shift that takes the student's hours above their cap is "
    "one extra shift (with 4 h shifts and a 20 h cap: extra = shifts - floor(20/4)). Extra "
    "shifts in a window are priced by the tier table (2, 4, then 6 each) and the student is "
    "charged the sum over all their windows. The worst rolling window per student is also "
    "reported for information."
)


def _hours_of(value: Any) -> float:
    if isinstance(value, _dt.time):
        return value.hour + value.minute / 60
    if isinstance(value, str):
        hh, _, mm = value.strip().partition(":")
        return int(hh) + (int(mm) if mm else 0) / 60
    number = float(value)
    return number / 60 if number > 24 else number


def rest_gap(case: Case, shift_a: str, shift_b: str, day_gap: int = 1) -> float:
    a = case.shifts[shift_a]
    b = case.shifts[shift_b]
    end_a = _hours_of(a.start) + float(a.hours)
    start_b = 24 * day_gap + _hours_of(b.start)
    return start_b - end_a


def rolling_windows(horizon: int, length: int) -> list[tuple[int, int]]:
    if horizon <= length:
        return [(1, horizon)]
    return [(start, start + length - 1) for start in range(1, horizon - length + 2)]


def extra_shifts(shift_hours: list[float], cap: float) -> int:
    total = 0.0
    extra = 0
    for h in shift_hours:
        total += h
        if total > cap + 1e-9:
            extra += 1
    return extra


@dataclass
class Report:
    hard_breaches: list[str] = field(default_factory=list)
    unfilled_total: int = 0
    unfilled_by_group: dict[str, int] = field(default_factory=dict)
    unfilled_slots: list[dict[str, Any]] = field(default_factory=list)
    cost_components: dict[str, float] = field(default_factory=dict)
    preference_satisfaction_pct: float = 100.0
    workload_spread_by_group: dict[str, float] = field(default_factory=dict)
    isolated_dayoffs: int = 0
    per_student_hours: dict[str, float] = field(default_factory=dict)
    overwork_by_student: dict[str, dict[str, Any]] = field(default_factory=dict)
    streak_by_student: dict[str, int] = field(default_factory=dict)
    rest_violations: list[dict[str, Any]] = field(default_factory=list)

    @property
    def hard_pass(self) -> bool:
        return not self.hard_breaches

    @property
    def total_cost(self) -> float:
        return sum(self.cost_components.values())

    def to_json(self) -> dict[str, Any]:
        return {
            "hard_pass": self.hard_pass,
            "hard_breaches": list(self.hard_breaches),
            "unfilled_total": self.unfilled_total,
            "unfilled_by_group": dict(self.unfilled_by_group),
            "cost_components": {k: round(v, 6) for k, v in self.cost_components.items()},
            "total_cost": round(self.total_cost, 6),
            "preference_satisfaction_pct": round(self.preference_satisfaction_pct, 4),
            "workload_spread_by_group": dict(self.workload_spread_by_group),
            "isolated_dayoffs": self.isolated_dayoffs,
            "per_student_hours": dict(self.per_student_hours),
            "overwork_accounting": OVERWORK_ACCOUNTING,
            "overwork_by_student": {k: dict(v) for k, v in self.overwork_by_student.items()},
            "streak_by_student": dict(self.streak_by_student),
            "rest_violations": [dict(v) for v in self.rest_violations],
            "unfilled_slots": [dict(s) for s in self.unfilled_slots],
        }


def evaluate(case: Case, rules: Rules, roster: Roster, load_breaches: list[str] | None = None) -> Report:
    report = Report()
    report.hard_breaches.extend(load_breaches or [])
    horizon = case.horizon_days
    groups = list(dict.fromkeys(list(GROUPS) + list(case.by_group)))

    valid: dict[str, dict[int, str]] = {roll: {} for roll in case.students}
    for (roll_no, day), shift_id in sorted(roster.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[1])):
        problems = []
        if roll_no not in case.students:
            problems.append(f"unknown student {roll_no}")
        if not isinstance(day, int) or not 1 <= day <= horizon:
            problems.append(f"day {day} outside 1..{horizon}")
        if shift_id not in case.shifts:
            problems.append(f"unknown shift {shift_id}")
        if problems:
            report.hard_breaches.append(f"invalid assignment ({roll_no}, day {day}, {shift_id}): " + "; ".join(problems))
            continue
        valid[roll_no][day] = shift_id

    assigned: dict[tuple[int, str, str], list[str]] = {}
    for roll_no, days in valid.items():
        group = case.students[roll_no].group
        for day, shift_id in days.items():
            assigned.setdefault((day, shift_id, group), []).append(roll_no)

    for cell in sorted(assigned):
        day, shift_id, group = cell
        required = case.demand.get(cell, 0)
        people = sorted(assigned[cell])
        if required == 0:
            report.hard_breaches.append(
                f"skill matching: day {day} {shift_id} has no {group} demand but has {', '.join(people)}"
            )
        elif len(people) > required:
            report.hard_breaches.append(
                f"demand exceeded: day {day} {shift_id} {group} needs {required}, has {len(people)}"
            )

    unfilled_by_group = {g: 0 for g in groups}
    for cell in sorted(case.demand):
        required = case.demand[cell]
        day, shift_id, group = cell
        have = len(assigned.get(cell, []))
        missing = max(0, required - have)
        if missing:
            unfilled_by_group[group] = unfilled_by_group.get(group, 0) + missing
            report.unfilled_slots.append(
                {"day": day, "shift_id": shift_id, "group": group, "required": required, "assigned": have, "missing": missing}
            )
    report.unfilled_by_group = unfilled_by_group
    report.unfilled_total = sum(unfilled_by_group.values())

    window = rules.rolling_window_days
    roll_windows = rolling_windows(horizon, window)
    ceiling = rules.hard_ceiling_hours_per_window

    overwork_cost = 0.0
    streak_cost = 0.0
    rest_cost = 0.0
    for roll_no in sorted(valid):
        days = valid[roll_no]
        student = case.students[roll_no]
        cap = float(student.max_weekly_hours or rules.weekly_cap_default_hours)
        hours_on = {d: float(case.shifts[s].hours) for d, s in days.items()}
        report.per_student_hours[roll_no] = sum(hours_on.values())

        charged = []
        cost = 0.0
        worst_extra, worst_window = 0, None
        for start, end in roll_windows:
            win_hours = [hours_on[d] for d in range(start, end + 1) if d in hours_on]
            extra = extra_shifts(win_hours, cap)
            if extra:
                penalty = overwork_penalty(
                    extra,
                    tier1=rules.penalty_overwork_tier1,
                    tier2=rules.penalty_overwork_tier2,
                    tier3_plus=rules.penalty_overwork_tier3_plus,
                )
                charged.append({"days": [start, end], "extra_shifts": extra, "cost": penalty})
                cost += penalty
            if extra > worst_extra:
                worst_extra, worst_window = extra, [start, end]
            if ceiling and sum(win_hours) > ceiling + 1e-9:
                report.hard_breaches.append(
                    f"hard ceiling: {roll_no} works {sum(win_hours):g} h in days {start}-{end} (ceiling {ceiling:g} h)"
                )
        if charged or worst_extra:
            report.overwork_by_student[roll_no] = {
                "cap_hours": cap,
                "charged_windows": charged,
                "cost": cost,
                "worst_rolling_window": worst_window,
                "worst_rolling_extra_shifts": worst_extra,
            }
        overwork_cost += cost

        beyond = 0
        run = 0
        rest_in_run = 0
        for day in range(1, horizon + 1):
            if day not in days:
                run = 0
                rest_in_run = 0
                continue
            run += 1
            if run > rules.max_consecutive_days:
                beyond += 1
            if day - 1 in days:
                prev, cur = days[day - 1], days[day]
                gap = rest_gap(case, prev, cur, 1)
                if gap < rules.min_rest_hours - 1e-9:
                    rest_in_run += 1
                    step = rest_chain_penalty(
                        rest_in_run, base=rules.penalty_rest_violation, increment=rules.penalty_rest_chain_increment
                    ) - rest_chain_penalty(
                        rest_in_run - 1, base=rules.penalty_rest_violation, increment=rules.penalty_rest_chain_increment
                    )
                    rest_cost += step
                    report.rest_violations.append(
                        {"roll_no": roll_no, "days": [day - 1, day], "shifts": [prev, cur],
                         "gap_hours": gap, "chain_index": rest_in_run, "cost": step}
                    )
        if beyond:
            report.streak_by_student[roll_no] = beyond
            streak_cost += beyond * rules.penalty_streak_day

    satisfied = 0.0
    total_weight = 0.0
    for roll_no, prefs in case.prefs.items():
        days = valid.get(roll_no, {})
        worked_shifts = set(days.values())
        for pref in prefs:
            weight = float(pref.weight)
            total_weight += weight
            kind = getattr(pref, "type", None) or getattr(pref, "kind", None)
            if kind == "OFF_DAY":
                ok = pref.day not in days
            elif kind == "PREFER_SHIFT":
                ok = pref.shift_id in worked_shifts
            elif kind == "AVOID_SHIFT":
                ok = pref.shift_id not in worked_shifts
            else:
                ok = True
            if ok:
                satisfied += weight
    pref_ratio = satisfied / total_weight if total_weight else 1.0
    report.preference_satisfaction_pct = 100.0 * pref_ratio

    longest = max((float(s.hours) for s in case.shifts.values()), default=0.0)
    max_possible = horizon * longest
    spreads = []
    for group in groups:
        members = case.by_group.get(group, [])
        if not members:
            report.workload_spread_by_group[group] = 0.0
            continue
        hours = [report.per_student_hours[r] for r in members]
        spread = max(hours) - min(hours)
        report.workload_spread_by_group[group] = spread
        spreads.append(spread / max_possible if max_possible else 0.0)
    fairness_ratio = sum(spreads) / len(spreads) if spreads else 0.0

    isolated = 0
    for roll_no, days in valid.items():
        for day in range(2, horizon):
            if day - 1 in days and day not in days and day + 1 in days:
                isolated += 1
    report.isolated_dayoffs = isolated
    iso_slots = len(case.students) * max(0, horizon - 2)
    iso_ratio = isolated / iso_slots if iso_slots else 0.0

    report.cost_components = {
        "unfilled": report.unfilled_total * float(rules.penalty_unfilled_slot),
        "overwork": float(overwork_cost),
        "streak": float(streak_cost),
        "rest": float(rest_cost),
        "preference": rules.weight_preference * (1.0 - pref_ratio),
        "fairness": rules.weight_fairness * fairness_ratio,
        "isolated": rules.weight_isolated_day_off * iso_ratio,
    }
    return report


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("usage: python -m roster.evaluator <case_folder> <roster.csv>", file=sys.stderr)
        return 2
    case = load_case(args[0])
    rules = Rules.from_case(case)
    roster, problems = read_roster(args[1])
    report = evaluate(case, rules, roster, load_breaches=problems)
    print(json.dumps(report.to_json(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
