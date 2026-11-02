"""CP-SAT model for ONE group (B1).

Groups never share students, so each group is an independent subproblem. The model
prices every rule exactly as ``roster.evaluator.evaluate`` charges it, so
``objective`` equals that group's share of ``Report.total_cost``:

  * unfilled     penalty_unfilled_slot per missing person per cell
  * overwork     every ROLLING window of rolling_window_days (the evaluator code charges
                 rolling windows; see evaluator.rolling_windows), extra = shifts - floor(cap/h),
                 priced 2 / 4 / 6-each through tier variables
  * streak       one unit per fully-worked window of max_consecutive_days + 1 days, which
                 equals the number of worked days beyond the limit in each run
  * rest         short-rest pairs numbered within an unbroken run; k-th costs base + inc*(k-1)
  * preference   w_pref * unsatisfied_weight / total_weight (all students in the case)
  * fairness     w_fair * (max_h - min_h) / (horizon * longest shift) / non-empty groups
  * isolated     w_iso * Work-Off-Work count / (all students * (horizon - 2))

Costs are multiplied by SCALE and rounded to integers. The brief suggested 100, but the
soft terms are far smaller than 0.01 per event (an isolated day off is ~3e-5), so x100
would round them to zero and the model could never match the evaluator.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Optional

from ortools.sat.python import cp_model

from ..case_io import Case
from ..evaluator import GROUPS, rest_gap, rolling_windows
from ..rules import Rules
from .base import Roster

SCALE = 1_000_000


@dataclass
class GroupVars:
    group: str
    students: list[str]
    x: dict[tuple[str, int, str], cp_model.IntVar] = field(default_factory=dict)
    unfilled: dict[tuple[int, str], cp_model.IntVar] = field(default_factory=dict)
    worked: dict[tuple[str, int], cp_model.IntVar] = field(default_factory=dict)
    scale: int = SCALE


@dataclass
class GroupResult:
    roster: Roster
    objective: Optional[float]
    best_bound: Optional[float]
    status: str


def _c(value: float) -> int:
    return int(round(value * SCALE))


def model_for_group(case: Case, rules: Rules, group: str) -> tuple[cp_model.CpModel, GroupVars]:
    H = case.horizon_days
    students = list(case.by_group.get(group, []))
    shift_ids = list(case.shift_order)
    hours = {sh: float(case.shifts[sh].hours) for sh in shift_ids}
    if len(set(hours.values())) > 1:
        raise ValueError("cpsat model needs all shifts to have the same hours")
    shift_h = next(iter(hours.values()))

    model = cp_model.CpModel()
    gv = GroupVars(group=group, students=students)
    terms: list[tuple[cp_model.IntVar, int]] = []
    const = 0

    def price(var, cost: float) -> None:
        coeff = _c(cost)
        if coeff:
            terms.append((var, coeff))

    # ---- variables, single shift per day, coverage -------------------------------------
    cells = {}
    for d in range(1, H + 1):
        for sh in shift_ids:
            req = case.demand.get((d, sh, group), 0)
            if req > 0:
                cells[(d, sh)] = req
    for (d, sh), req in cells.items():
        for s in students:
            gv.x[(s, d, sh)] = model.new_bool_var(f"x_{s}_{d}_{sh}")
        u = model.new_int_var(0, req, f"unf_{d}_{sh}")
        gv.unfilled[(d, sh)] = u
        model.add(sum(gv.x[(s, d, sh)] for s in students) + u == req)
        price(u, rules.penalty_unfilled_slot)

    for s in students:
        for d in range(1, H + 1):
            w = model.new_bool_var(f"w_{s}_{d}")
            gv.worked[(s, d)] = w
            model.add(w == sum(gv.x[(s, d, sh)] for sh in shift_ids if (s, d, sh) in gv.x))

    worked = gv.worked

    # ---- overwork: rolling windows, tier variables --------------------------------------
    for s in students:
        cap = float(case.students[s].max_weekly_hours or rules.weekly_cap_default_hours)
        cap_shifts = int(math.floor(cap / shift_h + 1e-9))
        for start, end in rolling_windows(H, rules.rolling_window_days):
            wl = end - start + 1
            max_extra = wl - cap_shifts
            if max_extra <= 0:
                continue
            e1 = model.new_bool_var(f"e1_{s}_{start}")
            e2 = model.new_bool_var(f"e2_{s}_{start}")
            ub3 = max(0, max_extra - 2)
            e3 = model.new_int_var(0, ub3, f"e3_{s}_{start}")
            model.add(e2 <= e1)
            model.add(e3 <= ub3 * e2)
            model.add(e1 + e2 + e3 >= sum(worked[(s, d)] for d in range(start, end + 1)) - cap_shifts)
            price(e1, rules.penalty_overwork_tier1)
            price(e2, rules.penalty_overwork_tier2)
            price(e3, rules.penalty_overwork_tier3_plus)

    # ---- optional hard ceiling ------------------------------------------------------------
    if rules.hard_ceiling_hours_per_window > 0:
        max_shifts = int(math.floor(rules.hard_ceiling_hours_per_window / shift_h + 1e-9))
        for s in students:
            for start, end in rolling_windows(H, rules.rolling_window_days):
                model.add(sum(worked[(s, d)] for d in range(start, end + 1)) <= max_shifts)

    # ---- streak ------------------------------------------------------------------------------
    M = rules.max_consecutive_days
    for s in students:
        for start in range(1, H - M + 1):
            v = model.new_bool_var(f"streak_{s}_{start}")
            model.add(v >= sum(worked[(s, d)] for d in range(start, start + M + 1)) - M)
            price(v, rules.penalty_streak_day)

    # ---- rest: short pairs + chain numbering ----------------------------------------------
    short_pairs = [
        (a, b)
        for a in shift_ids
        for b in shift_ids
        if rest_gap(case, a, b, 1) < rules.min_rest_hours - 1e-9
    ]
    if short_pairs:
        for s in students:
            short: dict[int, cp_model.IntVar] = {}
            for d in range(2, H + 1):
                pairs = [(a, b) for a, b in short_pairs if (s, d - 1, a) in gv.x and (s, d, b) in gv.x]
                if not pairs:
                    continue
                sv = model.new_bool_var(f"short_{s}_{d}")
                short[d] = sv
                for a, b in pairs:
                    model.add(sv >= gv.x[(s, d - 1, a)] + gv.x[(s, d, b)] - 1)
                price(sv, rules.penalty_rest_violation)
            if rules.penalty_rest_chain_increment and len(short) > 1:
                # c[d] >= number of short pairs so far in the current unbroken run of worked days
                c: dict[int, cp_model.IntVar] = {}
                for d in range(2, H + 1):
                    cv = model.new_int_var(0, H, f"c_{s}_{d}")
                    c[d] = cv
                    prev = c.get(d - 1)
                    expr = (prev if prev is not None else 0) + (short[d] if d in short else 0)
                    model.add(cv >= expr - H * (1 - worked[(s, d)]))
                for d in short:
                    if d - 1 in c:
                        z = model.new_int_var(0, H, f"z_{s}_{d}")
                        model.add(z >= c[d - 1] - H * (1 - short[d]))
                        price(z, rules.penalty_rest_chain_increment)

    # ---- isolated day off -----------------------------------------------------------------
    if H > 2:
        coeff = rules.weight_isolated_day_off / (len(case.students) * (H - 2))
        for s in students:
            for d in range(2, H):
                iso = model.new_bool_var(f"iso_{s}_{d}")
                model.add(iso >= worked[(s, d - 1)] + (1 - worked[(s, d)]) + worked[(s, d + 1)] - 2)
                price(iso, coeff)

    # ---- fairness -------------------------------------------------------------------------------
    n_groups = len([g for g in list(dict.fromkeys(list(GROUPS) + list(case.by_group))) if case.by_group.get(g)])
    longest = max(hours.values())
    if students and n_groups and H:
        unit = 0
        for sh in shift_ids:
            unit = math.gcd(unit, int(round(hours[sh] * 60)))
        per_shift = int(round(shift_h * 60)) // unit
        top = H * per_shift
        hmax = model.new_int_var(0, top, "hmax")
        hmin = model.new_int_var(0, top, "hmin")
        for s in students:
            hs = sum(per_shift * gv.x[(s, d, sh)] for d in range(1, H + 1) for sh in shift_ids if (s, d, sh) in gv.x)
            model.add(hmax >= hs)
            model.add(hmin <= hs)
        coeff = rules.weight_fairness * unit / 60 / (n_groups * H * longest)
        price(hmax, coeff)
        terms.append((hmin, -_c(coeff)))

    # ---- preferences ----------------------------------------------------------------------------
    total_w = 0.0
    for prefs in case.prefs.values():
        total_w += sum(float(p.weight) for p in prefs)
    if total_w:
        got: dict[tuple[str, str], cp_model.IntVar] = {}

        def got_var(s: str, sh: str):
            if (s, sh) not in got:
                days = [d for d in range(1, H + 1) if (s, d, sh) in gv.x]
                if not days:
                    got[(s, sh)] = None
                else:
                    g = model.new_bool_var(f"got_{s}_{sh}")
                    for d in days:
                        model.add(g >= gv.x[(s, d, sh)])
                    model.add(g <= sum(gv.x[(s, d, sh)] for d in days))
                    got[(s, sh)] = g
            return got[(s, sh)]

        for s in students:
            for p in case.prefs.get(s, []):
                coeff = rules.weight_preference * float(p.weight) / total_w
                kind = getattr(p, "type", None) or getattr(p, "kind", None)
                if kind == "OFF_DAY":
                    if p.day is not None and 1 <= p.day <= H:
                        price(worked[(s, p.day)], coeff)
                elif kind == "PREFER_SHIFT":
                    g = got_var(s, p.shift_id)
                    const += _c(coeff)
                    if g is not None:
                        terms.append((g, -_c(coeff)))
                elif kind == "AVOID_SHIFT":
                    g = got_var(s, p.shift_id)
                    if g is not None:
                        price(g, coeff)

    model.minimize(cp_model.LinearExpr.weighted_sum([v for v, _ in terms], [k for _, k in terms]) + const)
    return model, gv


def solve_group(
    case: Case,
    rules: Rules,
    group: str,
    time_limit_s: float,
    seed: int,
    hint: Optional[Roster] = None,
) -> GroupResult:
    model, gv = model_for_group(case, rules, group)
    if hint is not None:
        for (s, d, sh), var in gv.x.items():
            model.add_hint(var, 1 if hint.get((s, d)) == sh else 0)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = float(time_limit_s)
    solver.parameters.num_workers = os.cpu_count() or 1
    solver.parameters.random_seed = int(seed)
    if hint is not None:
        solver.parameters.repair_hint = True
    status = solver.solve(model)
    name = solver.status_name(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return GroupResult({}, None, None, name)
    roster: Roster = {
        (s, d): sh for (s, d, sh), var in gv.x.items() if solver.boolean_value(var)
    }
    return GroupResult(
        roster,
        solver.objective_value / SCALE,
        solver.best_objective_bound / SCALE,
        name,
    )
