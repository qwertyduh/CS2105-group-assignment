"""CP-SAT sick-leave repair within the affected group's next two days.

The full-horizon group model keeps frozen days in every rule calculation.
The objective adds 0.5 per changed student/day to the regular roster cost.
"""

from __future__ import annotations

import time

from ortools.sat.python import cp_model

from ..case_io import Case
from ..evaluator import evaluate
from ..rules import Rules
from ..solvers.base import RepairResult, Roster
from ..solvers.cpsat import SCALE, model_for_group

CHANGE_COST = 0.5


def _diff(original: Roster, repaired: Roster) -> list[tuple[str, int, str | None, str | None]]:
    return [
        (s, d, original.get((s, d)), repaired.get((s, d)))
        for s, d in sorted(set(original) | set(repaired))
        if original.get((s, d)) != repaired.get((s, d))
    ]


def repair(
    case: Case,
    roster: Roster,
    sick_roll_no: str,
    sick_day: int,
    shift_id: str,
    time_limit_s: float = 90.0,
    seed: int = 0,
    unavailable_days: list[int] | None = None,
) -> RepairResult:
    """Repair days D/D+1, preserving all other groups and assignments.

    The roster determines the sick student's actual shift if ``shift_id``
    disagrees. Only unavailability within this two-day window is acted on.
    On timeout, retain the best audited incumbent and explain any shortage.
    """
    started = time.monotonic()
    if sick_roll_no not in case.students:
        raise ValueError(f"unknown student {sick_roll_no!r}")
    horizon = case.horizon_days
    if not isinstance(sick_day, int) or not 1 <= sick_day <= horizon:
        raise ValueError(f"sick day {sick_day!r} outside 1..{horizon}")
    if (sick_roll_no, sick_day) not in roster:
        raise ValueError(f"{sick_roll_no} is not rostered on day {sick_day}; nothing to repair")

    rules = Rules.from_case(case)
    group = case.students[sick_roll_no].group
    unavailable = {sick_day} | (set(unavailable_days or ()) & {sick_day + 1})
    window_days = range(sick_day, min(horizon, sick_day + 1) + 1)

    # This feasible incumbent is available even if CP-SAT finds no solution.
    best_roster = dict(roster)
    for day in unavailable:
        best_roster.pop((sick_roll_no, day), None)
    best_diff = _diff(roster, best_roster)
    best_report = evaluate(case, rules, best_roster)
    best_score = best_report.total_cost + CHANGE_COST * len(best_diff)

    model, gv = model_for_group(case, rules, group)
    for (s, d, sh), var in gv.x.items():
        if d not in window_days:
            model.add(var == int(roster.get((s, d)) == sh))
        model.add_hint(var, int(best_roster.get((s, d)) == sh))
    for day in unavailable:
        if day in window_days:
            model.add(gv.worked[(sick_roll_no, day)] == 0)

    objective = model.Proto().objective
    for s in gv.students:
        for day in window_days:
            changed = model.new_bool_var(f"diff_{s}_{day}")
            original_shift = roster.get((s, day))
            if original_shift is not None:
                model.add(changed == 1 - gv.x[(s, day, original_shift)])
            else:
                model.add(changed == gv.worked[(s, day)])
            objective.vars.append(changed.Index())
            objective.coeffs.append(int(CHANGE_COST * SCALE))

    solver = cp_model.CpSolver()
    remaining = float(time_limit_s) - (time.monotonic() - started)
    solver.parameters.max_time_in_seconds = max(0.0, remaining)
    solver.parameters.random_seed = int(seed)
    solver.parameters.num_workers = 1
    status = solver.solve(model)

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Rebuild the affected window; retain frozen cells verbatim.
        candidate = {
            (s, d): sh for (s, d), sh in roster.items()
            if d not in window_days or case.students[s].group != group
        }
        candidate.update({
            (s, d): sh for (s, d, sh), var in gv.x.items()
            if d in window_days and solver.boolean_value(var)
        })
        candidate_diff = _diff(roster, candidate)
        candidate_report = evaluate(case, rules, candidate)
        candidate_score = candidate_report.total_cost + CHANGE_COST * len(candidate_diff)
        if candidate_report.hard_pass and (
            not best_report.hard_pass
            or (candidate_score, len(candidate_diff)) <= (best_score, len(best_diff))
        ):
            best_roster, best_diff, best_report = candidate, candidate_diff, candidate_report

    # A repair may move a shortage to another cell. Report the whole window,
    # including on UNKNOWN, rather than only checking the original sick slot.
    unfilled = []
    for day in window_days:
        for sh in case.shift_order:
            assigned = sum(best_roster.get((s, day)) == sh for s in gv.students)
            if assigned < case.demand.get((day, sh, group), 0):
                unfilled.append((day, sh))
    reason = None
    if unfilled:
        slots = ", ".join(f"day {day} {sh}" for day, sh in unfilled)
        reason = (
            f"left {slots} unfilled for {group}: CP-SAT status {solver.status_name(status)}; "
            f"the best available repair includes the unfilled penalty "
            f"({rules.penalty_unfilled_slot:g} per slot) and {CHANGE_COST:g} per changed assignment"
        )
    return RepairResult(roster=best_roster, diff=best_diff, reason=reason)
