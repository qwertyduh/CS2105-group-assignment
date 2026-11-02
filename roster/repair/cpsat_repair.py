"""CP-SAT minimal-change sick-leave repair."""

import time
from typing import Optional

from ortools.sat.python import cp_model

from ..evaluator import evaluate
from ..rules import Rules
from ..solvers.base import RepairResult, Roster
from ..solvers.cpsat import model_for_group, SCALE

CHANGE_COST = 0.5

def repair(
    case,
    roster: Roster,
    sick_roll_no: str,
    sick_day: int,
    shift_id: str,
    time_limit_s: float = 90.0,
    seed: int = 0,
    unavailable_days: Optional[list[int]] = None,
) -> RepairResult:
    if sick_roll_no not in case.students:
        raise ValueError(f"unknown student {sick_roll_no!r}")
    horizon = case.horizon_days
    if not isinstance(sick_day, int) or not 1 <= sick_day <= horizon:
        raise ValueError(f"sick day {sick_day!r} outside 1..{horizon}")
    if (sick_roll_no, sick_day) not in roster:
        raise ValueError(f"{sick_roll_no} is not rostered on day {sick_day}; nothing to repair")

    rules = Rules.from_case(case)
    group = case.students[sick_roll_no].group
    unavailable = set(unavailable_days or ())
    
    window_days = [sick_day]
    if sick_day + 1 <= horizon:
        window_days.append(sick_day + 1)

    model, gv = model_for_group(case, rules, group)

    # 1. Freeze non-window days
    for (s, d, sh), var in gv.x.items():
        if d not in window_days:
            val = 1 if roster.get((s, d)) == sh else 0
            model.add(var == val)

    # 2. Fix sick student unavailable
    model.add(gv.worked[(sick_roll_no, sick_day)] == 0)
    if (sick_day + 1) in unavailable and (sick_day + 1) in window_days:
        model.add(gv.worked[(sick_roll_no, sick_day + 1)] == 0)

    # 3. Add change costs to objective
    obj_proto = model.Proto().objective
    change_cost_scaled = int(CHANGE_COST * SCALE)
    diff_vars = []
    
    for s in gv.students:
        for d in window_days:
            diff_var = model.new_bool_var(f"diff_{s}_{d}")
            orig_sh = roster.get((s, d))
            if orig_sh is not None:
                # If they had a shift, diff is 1 if they DON'T have that exact shift anymore.
                model.add(diff_var == 1 - gv.x[(s, d, orig_sh)])
            else:
                # If they were free, diff is 1 if they work ANY shift now.
                model.add(diff_var == gv.worked[(s, d)])
            
            obj_proto.vars.append(diff_var.Index())
            obj_proto.coeffs.append(change_cost_scaled)
            diff_vars.append(diff_var)

    # Solve
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, float(time_limit_s))
    solver.parameters.random_seed = int(seed)
    solver.parameters.num_workers = 1

    status = solver.solve(model)
    
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        # Fallback to simple removal
        best_roster = dict(roster)
        if (sick_roll_no, sick_day) in best_roster:
            del best_roster[(sick_roll_no, sick_day)]
        if (sick_day + 1) in unavailable and (sick_roll_no, sick_day + 1) in best_roster:
            del best_roster[(sick_roll_no, sick_day + 1)]
        
        diff = []
        for k in set(roster) | set(best_roster):
            if roster.get(k) != best_roster.get(k):
                diff.append((k[0], k[1], roster.get(k), best_roster.get(k)))
        return RepairResult(roster=best_roster, diff=diff, reason=None)

    # Extract best roster
    best_roster = dict(roster)
    for (s, d, sh), var in gv.x.items():
        if solver.boolean_value(var):
            best_roster[(s, d)] = sh
        elif best_roster.get((s, d)) == sh:
            del best_roster[(s, d)]
    
    # Build diff
    diff = []
    for k in sorted(set(roster) | set(best_roster)):
        if roster.get(k) != best_roster.get(k):
            diff.append((k[0], k[1], roster.get(k), best_roster.get(k)))

    # Identify if left unfilled
    freed = []
    if (sick_roll_no, sick_day) in roster:
        freed.append((sick_day, roster[(sick_roll_no, sick_day)]))
    if (sick_day + 1) in unavailable and (sick_roll_no, sick_day + 1) in roster:
        freed.append((sick_day + 1, roster[(sick_roll_no, sick_day + 1)]))

    unfilled_list = []
    for d, sh in dict.fromkeys(freed):
        req = case.demand.get((d, sh, group), 0)
        assigned = sum(1 for (r, day), assigned_sh in best_roster.items() if day == d and assigned_sh == sh and case.students[r].group == group)
        if assigned < req:
            unfilled_list.append((d, sh))

    reason = None
    if unfilled_list:
        slot_text = ", ".join(f"day {d} {sh}" for d, sh in unfilled_list)
        reason = (
            f"left {slot_text} unfilled for {group}: no free same-group replacement or "
            f"chain of at most 2 changes beat the unfilled penalty "
            f"({rules.penalty_unfilled_slot:g} per slot) once the {CHANGE_COST:g} cost "
            f"per changed assignment is added"
        )

    return RepairResult(roster=best_roster, diff=diff, reason=reason)
