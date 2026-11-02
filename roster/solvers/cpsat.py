"""CP-SAT solver: single-group model (B1) and parallel four-group solver (B2).

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

Costs are multiplied by SCALE and rounded to integers.

In B2, ``solve(case, time_limit_s, seed)`` builds the greedy roster first as a warm-start
hint, splits ``time_limit_s`` across the four groups by their share of model variables
(minimum 10% each), divides CPU workers across the four groups so the total never exceeds
``os.cpu_count()``, solves the four groups in parallel with ``ProcessPoolExecutor``, and
merges the four group rosters into one ``Roster``.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Optional

from ortools.sat.python import cp_model

from ..case_io import Case, load_case
from ..evaluator import GROUPS, evaluate, rest_gap, rolling_windows
from ..rules import Rules
from .base import Roster, save_roster
from . import greedy as greedy_solver

SCALE = 1_000_000
MIN_GROUP_TIME_SHARE = 0.10

# Side dict from the most recent solve() call.
LAST_INFO: dict[str, Any] = {}
LAST_SIDE: dict[str, Any] = LAST_INFO


class SolvedRoster(dict):
    """A dict (Roster) that also carries the CP-SAT side dict on .info / .side."""

    def __init__(self, mapping: Roster | None = None, info: dict[str, Any] | None = None):
        super().__init__(mapping or {})
        self.info: dict[str, Any] = info if info is not None else {}
        self.side: dict[str, Any] = self.info
        self.stats: dict[str, Any] = self.info
        self.groups: dict[str, Any] = self.info.get("groups", {})
        self.beat_greedy: bool = bool(self.info.get("beat_greedy", False))
        self.greedy_cost: Optional[float] = self.info.get("greedy_cost")


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

    @property
    def gap(self) -> float:
        if self.objective is None or self.best_bound is None:
            return 0.0
        return max(0.0, float(self.objective - self.best_bound))

    @property
    def rel_gap(self) -> float:
        if self.objective is None or self.best_bound is None:
            return 0.0
        denom = max(1e-9, abs(float(self.objective)))
        return max(0.0, float(self.objective - self.best_bound)) / denom


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
    num_workers: Optional[int] = None,
) -> GroupResult:
    model, gv = model_for_group(case, rules, group)
    if hint is not None:
        for (s, d, sh), var in gv.x.items():
            model.add_hint(var, 1 if hint.get((s, d)) == sh else 0)
        for (s, d), var in gv.worked.items():
            model.add_hint(var, 1 if (s, d) in hint else 0)
        for (d, sh), var in gv.unfilled.items():
            req = case.demand.get((d, sh, group), 0)
            assigned = sum(1 for s in gv.students if hint.get((s, d)) == sh)
            model.add_hint(var, max(0, req - assigned))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max(0.01, float(time_limit_s))
    workers = int(num_workers) if num_workers is not None else (os.cpu_count() or 1)
    solver.parameters.num_workers = max(1, workers)
    solver.parameters.random_seed = int(seed)
    if hint is not None:
        solver.parameters.repair_hint = True

    status = solver.solve(model)
    name = solver.status_name(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        fallback = dict(hint) if hint is not None else {}
        return GroupResult(fallback, None, None, name)

    roster: Roster = {
        (s, d): sh for (s, d, sh), var in gv.x.items() if solver.boolean_value(var)
    }
    return GroupResult(
        roster,
        solver.objective_value / SCALE,
        solver.best_objective_bound / SCALE,
        name,
    )


def count_group_variables(case: Case, rules: Rules, groups: list[str] | tuple[str, ...] = GROUPS) -> dict[str, int]:
    """Return the number of CP-SAT model variables for each group."""
    counts: dict[str, int] = {}
    for group in groups:
        model, gv = model_for_group(case, rules, group)
        n_vars = len(model.proto.variables)
        if n_vars == 0:
            n_vars = len(gv.x) + len(gv.unfilled) + len(gv.worked)
        counts[group] = n_vars
    return counts


def split_time_limits(
    var_counts: dict[str, int],
    time_limit_s: float,
    min_share: float = MIN_GROUP_TIME_SHARE,
) -> dict[str, float]:
    """Split ``time_limit_s`` across groups by variable share, minimum ``min_share`` (10%) each."""
    groups = list(var_counts.keys())
    if not groups:
        return {}
    total_time = max(0.0, float(time_limit_s))
    n = len(groups)
    floor_share = min(float(min_share), 1.0 / n)

    shares: dict[str, float] = {}
    remaining_groups = list(groups)
    remaining_share = 1.0

    while remaining_groups:
        rem_vars = sum(max(0, var_counts[g]) for g in remaining_groups)
        if rem_vars <= 0:
            equal = remaining_share / len(remaining_groups)
            for g in remaining_groups:
                shares[g] = equal
            break

        provisional = {
            g: remaining_share * (max(0, var_counts[g]) / rem_vars)
            for g in remaining_groups
        }
        below = [g for g in remaining_groups if provisional[g] < floor_share - 1e-12]
        if not below:
            shares.update(provisional)
            break

        for g in below:
            shares[g] = floor_share
            remaining_share -= floor_share
            remaining_groups.remove(g)
        remaining_share = max(0.0, remaining_share)

    return {g: total_time * shares[g] for g in groups}


def split_workers(
    var_counts: dict[str, int],
    cpu_count: Optional[int] = None,
) -> dict[str, int]:
    """Divide CPU workers across groups by variable share so the sum never exceeds ``os.cpu_count()``."""
    groups = list(var_counts.keys())
    if not groups:
        return {}
    total_cpus = max(1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1)))
    n = len(groups)
    if total_cpus <= n:
        return {g: 1 for g in groups}

    workers = {g: 1 for g in groups}
    extra = total_cpus - n
    total_vars = sum(max(0, v) for v in var_counts.values())
    if total_vars <= 0:
        for i in range(extra):
            workers[groups[i % n]] += 1
        return workers

    exact = {g: extra * (max(0, var_counts[g]) / total_vars) for g in groups}
    for g in groups:
        workers[g] += int(math.floor(exact[g]))
    remainder = total_cpus - sum(workers.values())
    order = sorted(
        groups,
        key=lambda g: (exact[g] - math.floor(exact[g]), var_counts[g]),
        reverse=True,
    )
    for g in order[:remainder]:
        workers[g] += 1
    return workers


def _solve_group_task(
    case: Case,
    rules: Rules,
    group: str,
    time_limit_s: float,
    seed: int,
    hint: Optional[Roster],
    num_workers: int,
) -> tuple[str, GroupResult, float]:
    t0 = time.perf_counter()
    res = solve_group(
        case,
        rules,
        group,
        time_limit_s=time_limit_s,
        seed=seed,
        hint=hint,
        num_workers=num_workers,
    )
    elapsed = time.perf_counter() - t0
    return group, res, elapsed


def solve(
    case: Case,
    time_limit_s: float = 90.0,
    seed: int = 0,
    side: Optional[dict[str, Any]] = None,
    *,
    return_info: bool = False,
) -> Roster | tuple[Roster, dict[str, Any]]:
    """Solve all four groups in parallel with greedy warm start and merge into one Roster.

    Per-group status, gap, objective, bound, allocated time/workers, and warm-start
    comparison (``greedy_cost``, ``cpsat_cost``, ``beat_greedy``) are written into:
      * ``side`` if a dict is passed in,
      * ``cpsat.LAST_INFO`` / ``cpsat.LAST_SIDE`` at module level, and
      * ``roster.info`` / ``roster.side`` on the returned ``SolvedRoster`` (a ``dict`` subclass).
    """
    started = time.perf_counter()
    rules = Rules.from_case(case)
    groups = [g for g in GROUPS if g in case.by_group] or list(GROUPS)

    # 1. Warm start: build greedy roster first and partition by group.
    greedy_budget = min(max(0.5, float(time_limit_s) * 0.1), 5.0)
    greedy_roster = greedy_solver.solve(case, time_limit_s=greedy_budget, seed=seed)
    greedy_report = evaluate(case, rules, greedy_roster)
    greedy_cost = greedy_report.total_cost

    hints: dict[str, Roster] = {}
    for g in groups:
        members = set(case.by_group.get(g, []))
        hints[g] = {(r, d): sh for (r, d), sh in greedy_roster.items() if r in members}

    # 2. Split time_limit_s by share of variables (min 10% each) and divide CPU workers.
    var_counts = count_group_variables(case, rules, groups)
    group_times = split_time_limits(var_counts, time_limit_s, min_share=MIN_GROUP_TIME_SHARE)
    total_cpus = max(1, os.cpu_count() or 1)
    group_workers = split_workers(var_counts, cpu_count=total_cpus)

    # 3. Run the four group models in parallel with ProcessPoolExecutor.
    max_pool_workers = min(len(groups), total_cpus)
    results: dict[str, GroupResult] = {}
    wall_times: dict[str, float] = {}

    def _run_with_executor(executor_cls):
        with executor_cls(max_workers=max_pool_workers) as pool:
            futures = {
                pool.submit(
                    _solve_group_task,
                    case,
                    rules,
                    g,
                    group_times[g],
                    int(seed),
                    hints[g],
                    group_workers[g],
                ): g
                for g in groups
            }
            for fut in futures:
                g, res, elapsed = fut.result()
                results[g] = res
                wall_times[g] = elapsed

    try:
        _run_with_executor(ProcessPoolExecutor)
    except (PermissionError, NotImplementedError, OSError):
        _run_with_executor(ThreadPoolExecutor)

    # 4. Merge group rosters, keeping the best per-group roster found (never worse than hint).
    merged: Roster = {}
    per_group_info: dict[str, dict[str, Any]] = {}
    for g in groups:
        res = results[g]
        chosen_roster = res.roster
        if hints[g]:
            if not chosen_roster:
                chosen_roster = dict(hints[g])
            else:
                cand_rep = evaluate(case, rules, chosen_roster)
                hint_rep = evaluate(case, rules, hints[g])
                if (not cand_rep.hard_pass) or (cand_rep.total_cost > hint_rep.total_cost + 1e-9):
                    chosen_roster = dict(hints[g])

        merged.update(chosen_roster)
        per_group_info[g] = {
            "status": res.status,
            "objective": res.objective,
            "best_bound": res.best_bound,
            "gap": res.gap,
            "rel_gap": res.rel_gap,
            "time_limit_s": group_times[g],
            "workers": group_workers[g],
            "variables": var_counts[g],
            "wall_time_s": wall_times.get(g, 0.0),
        }

    final_report = evaluate(case, rules, merged)
    if (not final_report.hard_pass) or (final_report.total_cost > greedy_cost + 1e-9):
        merged = dict(greedy_roster)
        final_report = greedy_report

    final_cost = final_report.total_cost
    beat_greedy = bool(final_cost < greedy_cost - 1e-9)
    total_wall = time.perf_counter() - started

    all_objs = [info["objective"] for info in per_group_info.values() if info["objective"] is not None]
    all_bounds = [info["best_bound"] for info in per_group_info.values() if info["best_bound"] is not None]
    total_obj = sum(all_objs) if len(all_objs) == len(groups) else final_cost
    total_bound = sum(all_bounds) if len(all_bounds) == len(groups) else final_cost
    total_gap = max(0.0, float(total_obj - total_bound))
    total_rel_gap = total_gap / max(1e-9, abs(float(total_obj)))

    info_dict: dict[str, Any] = {
        **per_group_info,
        "groups": per_group_info,
        "per_group": per_group_info,
        "status": {g: per_group_info[g]["status"] for g in groups},
        "gaps": {g: per_group_info[g]["gap"] for g in groups},
        "greedy_cost": greedy_cost,
        "cpsat_cost": final_cost,
        "beat_greedy": beat_greedy,
        "objective": total_obj,
        "best_bound": total_bound,
        "gap": total_gap,
        "rel_gap": total_rel_gap,
        "wall_time_s": total_wall,
        "total_workers": sum(group_workers.values()),
    }

    LAST_INFO.clear()
    LAST_INFO.update(info_dict)
    if side is not None:
        side.clear()
        side.update(info_dict)

    solved = SolvedRoster(merged, info=info_dict)
    if return_info:
        return solved, info_dict
    return solved


def solve_with_info(
    case: Case,
    time_limit_s: float = 90.0,
    seed: int = 0,
) -> tuple[Roster, dict[str, Any]]:
    """Convenience wrapper returning ``(roster, side_dict)``."""
    side: dict[str, Any] = {}
    roster = solve(case, time_limit_s=time_limit_s, seed=seed, side=side)
    return roster, side


def write_baseline(
    case_folders: list[str] | None = None,
    time_limit_s: float = 90.0,
    seed: int = 1,
    out_dir: str = "results",
) -> Path:
    """Run CP-SAT on all four sample cases and write ``results/cpsat_baseline.txt``."""
    if case_folders is None:
        case_folders = [
            "test/case_01_balanced_20d",
            "test/case_02_tight_28d",
            "test/case_03_overloaded_20d",
            "test/case_04_small_7d",
        ]
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [
        f"CP-SAT baseline (seed={seed}, time_limit_s={time_limit_s:g})",
        "=" * 88,
    ]
    for folder in case_folders:
        case = load_case(folder)
        rules = Rules.from_case(case)
        case_name = Path(case.folder).name
        side: dict[str, Any] = {}
        t0 = time.perf_counter()
        roster = solve(case, time_limit_s=time_limit_s, seed=seed, side=side)
        wall_s = time.perf_counter() - t0
        report = evaluate(case, rules, roster)

        save_roster(roster, str(out_path / f"{case_name}_cpsat.roster.csv"))
        (out_path / f"{case_name}_cpsat.report.json").write_text(
            json.dumps(report.to_json(), indent=2), encoding="utf-8"
        )

        lines.append(
            f"{case_name}: cost={report.total_cost:.4f} greedy_cost={side['greedy_cost']:.4f} "
            f"beat_greedy={side['beat_greedy']} hard_pass={report.hard_pass} "
            f"unfilled={report.unfilled_total} wall_time={wall_s:.2f}s "
            f"gap={side['gap']:.4f} ({side['rel_gap'] * 100.0:.2f}%)"
        )
        for g, ginfo in side["groups"].items():
            lines.append(
                f"  {g:<12} status={ginfo['status']:<9} "
                f"obj={ginfo['objective']:.4f} bound={ginfo['best_bound']:.4f} "
                f"gap={ginfo['gap']:.4f} ({ginfo['rel_gap'] * 100.0:.2f}%) "
                f"time_limit={ginfo['time_limit_s']:.2f}s workers={ginfo['workers']} "
                f"wall={ginfo['wall_time_s']:.2f}s"
            )
    baseline_file = out_path / "cpsat_baseline.txt"
    baseline_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return baseline_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m roster.solvers.cpsat",
        description="Run CP-SAT baseline across sample cases and write results/cpsat_baseline.txt.",
    )
    parser.add_argument("--time", dest="time_limit_s", type=float, default=90.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out-dir", default="results")
    args = parser.parse_args(argv)
    path = write_baseline(time_limit_s=args.time_limit_s, seed=args.seed, out_dir=args.out_dir)
    print(path.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
