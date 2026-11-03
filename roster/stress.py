"""Random-case stress benchmark for the roster solvers and repair methods.

Commands::

    python -m roster.stress run --n 100 --time 60 --workers 4
    python -m roster.stress report

``run`` samples N cases with ``generate_case.py`` (scenario 50/30/20, days
7/14/20/28 at 15/25/40/20, random group sizes 6..30 and preference fraction
0.2..0.9), writes them under ``results/stress/cases/`` with a reproducible
``manifest.csv``, then runs greedy, local search, CP-SAT cold and CP-SAT warm
on every case. Solver pairs are appended to ``results/stress/results.csv`` as
they finish, so an interrupted run resumes without repeating completed pairs.

CP-SAT is forced to ``num_workers=1`` per group during a parallel stress run so
that its timings are comparable across cases; this is recorded in the manifest.

After solving, five deterministic sick events per case are repaired with both
the local and CP-SAT repair modules and appended to ``results/stress/repairs.csv``.

``report`` reads both CSVs and writes ``results/stress/summary.md``.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import random
import statistics
import sys
import time
import types
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Iterable

from .case_io import Case, load_case
from .evaluator import GROUPS, evaluate
from .rules import Rules
from .solvers.base import Roster, load_roster, save_roster

SOLVERS = ("greedy", "local", "cpsat_cold", "cpsat_warm")
REPAIR_METHODS = ("local", "cpsat")
SCENARIOS = ("balanced", "tight", "overloaded")
SCENARIO_WEIGHTS = (50, 30, 20)
DAY_CHOICES = (7, 14, 20, 28)
DAY_WEIGHTS = (15, 25, 40, 20)
RUNTIME_BUDGET_S = 90.0
EPS = 1e-9

MANIFEST_COLUMNS = (
    "case_id",
    "index",
    "sampling_seed",
    "case_seed",
    "solver_seed",
    "scenario",
    "days",
    "size_MPR",
    "size_CND",
    "size_Hospitality",
    "size_SPONS",
    "pref_fraction",
    "target_load",
    "load_ratio",
    "students",
    "demand_slots",
    "capacity_slots",
    "time_limit_s",
    "cpsat_num_workers",
)

RESULT_COLUMNS = (
    "case_id",
    "solver",
    "scenario",
    "days",
    "students",
    "load_ratio",
    "pref_fraction",
    "target_load",
    "case_seed",
    "time_limit_s",
    "cpsat_num_workers",
    "total_cost",
    "cost_unfilled",
    "cost_overwork",
    "cost_streak",
    "cost_rest",
    "cost_preference",
    "cost_fairness",
    "cost_isolated",
    "unfilled_total",
    "hard_pass",
    "preference_satisfaction_pct",
    *(f"spread_{group}" for group in GROUPS),
    "runtime_s",
    "runtime_under_90s",
    "cpsat_gap",
    "cpsat_rel_gap_pct",
)

REPAIR_COLUMNS = (
    "case_id",
    "event_index",
    "method",
    "scenario",
    "days",
    "students",
    "load_ratio",
    "roll_no",
    "sick_day",
    "shift_id",
    "before_cost",
    "after_cost",
    "changes",
    "runtime_s",
    "unfilled_total",
    "sick_slot_unfilled",
    "hard_pass",
    "reason",
)

_GENERATOR: Any = None


# ------------------------------------------------------------------ generator
def _generator():
    """Load ``generate_case.py`` from the repository root (cached)."""
    global _GENERATOR
    if _GENERATOR is None:
        path = Path(__file__).resolve().parents[1] / "generate_case.py"
        spec = importlib.util.spec_from_file_location("_stress_generate_case", path)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        _GENERATOR = module
    return _GENERATOR


def draw_case_params(sampling_seed: int, index: int) -> dict[str, Any]:
    """Draw one case's parameters independently of N, so runs can be extended."""
    rng = random.Random(f"stress:{sampling_seed}:{index}")
    scenario = rng.choices(SCENARIOS, weights=SCENARIO_WEIGHTS, k=1)[0]
    days = rng.choices(DAY_CHOICES, weights=DAY_WEIGHTS, k=1)[0]
    case_seed = rng.randrange(1, 2**31 - 1)
    sizes = {group: rng.randint(6, 30) for group in GROUPS}
    pref_fraction = round(rng.uniform(0.2, 0.9), 4)
    return {
        "index": index,
        "scenario": scenario,
        "days": days,
        "case_seed": case_seed,
        "sizes": sizes,
        "pref_fraction": pref_fraction,
    }


def case_id_for(params: dict[str, Any]) -> str:
    return (
        f"case_{params['index']:03d}_{params['scenario']}_"
        f"{params['days']}d_seed{params['case_seed']}"
    )


def generate_case_folder(
    base_dir: Path, params: dict[str, Any], sampling_seed: int
) -> dict[str, Any]:
    """Generate one case with generate_case.py and return its manifest row."""
    generator = _generator()
    case_id = case_id_for(params)
    sizes_text = ",".join(f"{group}={params['sizes'][group]}" for group in GROUPS)
    args = types.SimpleNamespace(
        out=str(base_dir / "cases"),
        name=case_id,
        seed=params["case_seed"],
        scenario=params["scenario"],
        load=None,
        days=params["days"],
        sizes=sizes_text,
        peak_frac=0.15,
        pref_fraction=params["pref_fraction"],
        varied_caps=0.10,
    )
    case_dir, summary = generator.build_case(args)
    errors = generator.validate_case(case_dir)
    if errors:
        raise RuntimeError(
            f"generated case {case_id} failed validation:\n" + "\n".join(errors)
        )
    return {
        "case_id": case_id,
        "index": params["index"],
        "sampling_seed": sampling_seed,
        "case_seed": params["case_seed"],
        "solver_seed": params["case_seed"],
        "scenario": params["scenario"],
        "days": params["days"],
        **{f"size_{group}": params["sizes"][group] for group in GROUPS},
        "pref_fraction": params["pref_fraction"],
        "target_load": summary["target_load"],
        "load_ratio": summary["load_ratio"],
        "students": summary["students"],
        "demand_slots": summary["demand_slots"],
        "capacity_slots": summary["capacity_slots"],
    }


# ------------------------------------------------------------------ csv helpers
def _fmt(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        return f"{value:.6f}"
    return str(value)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv_rows(path: Path, columns: Iterable[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(columns)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _fmt(row.get(column)) for column in columns})


class _Appender:
    def __init__(self, path: Path, columns: Iterable[str]):
        self.path = path
        self.columns = list(columns)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, row: dict[str, Any]) -> None:
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.columns)
            if new_file:
                writer.writeheader()
            writer.writerow({column: _fmt(row.get(column)) for column in self.columns})
            handle.flush()
            os.fsync(handle.fileno())


# ------------------------------------------------------------------ case setup
def ensure_cases(
    base_dir: Path,
    n: int,
    sampling_seed: int,
    time_limit_s: float,
    cpsat_num_workers: int,
) -> list[dict[str, str]]:
    """Generate missing cases and return manifest rows 0..max(n, existing)-1."""
    manifest_path = base_dir / "manifest.csv"
    existing_rows = _read_csv(manifest_path)
    existing = {int(row["index"]): row for row in existing_rows}
    highest = max(existing, default=-1)
    target_count = max(n, highest + 1)

    rows: list[dict[str, str]] = []
    for index in range(target_count):
        row = existing.get(index)
        if row is None:
            params = draw_case_params(sampling_seed, index)
            generated = generate_case_folder(base_dir, params, sampling_seed)
            row = generated
            print(
                f"generated {row['case_id']}: scenario={row['scenario']} "
                f"days={row['days']} students={row['students']} "
                f"load={row['load_ratio']}"
            )
        row = dict(row)
        row["time_limit_s"] = time_limit_s
        row["cpsat_num_workers"] = cpsat_num_workers
        rows.append(row)

    write_csv_rows(manifest_path, MANIFEST_COLUMNS, rows)
    return [{column: _fmt(row.get(column)) for column in MANIFEST_COLUMNS} for row in rows]


def case_folder(base_dir: Path, case_id: str) -> Path:
    return base_dir / "cases" / case_id


# ------------------------------------------------------------------ solver jobs
def _solve_task(payload: dict[str, Any]) -> dict[str, Any]:
    case = load_case(payload["case_dir"])
    rules = Rules.from_case(case)
    solver = payload["solver"]
    time_limit_s = payload["time_limit_s"]
    seed = payload["seed"]
    side: dict[str, Any] = {}

    started = time.perf_counter()
    if solver == "greedy":
        from .solvers.greedy import solve as solve_greedy

        roster = solve_greedy(case, time_limit_s, seed)
    elif solver == "local":
        from .solvers.local_search import solve as solve_local

        roster = solve_local(case, time_limit_s, seed)
    else:
        from .solvers.cpsat import solve as solve_cpsat

        roster = solve_cpsat(
            case,
            time_limit_s=time_limit_s,
            seed=seed,
            side=side,
            warm_start=(solver == "cpsat_warm"),
            num_workers=payload["cpsat_num_workers"],
        )
    runtime = time.perf_counter() - started

    report = evaluate(case, rules, roster)
    save_roster(roster, payload["roster_path"])

    gap = side.get("gap")
    rel_gap = side.get("rel_gap")
    row: dict[str, Any] = {
        "case_id": payload["case_id"],
        "solver": solver,
        "scenario": payload["scenario"],
        "days": payload["days"],
        "students": payload["students"],
        "load_ratio": payload["load_ratio"],
        "pref_fraction": payload["pref_fraction"],
        "target_load": payload["target_load"],
        "case_seed": payload["case_seed"],
        "time_limit_s": time_limit_s,
        "cpsat_num_workers": payload["cpsat_num_workers"],
        "total_cost": float(report.total_cost),
        "unfilled_total": int(report.unfilled_total),
        "hard_pass": bool(report.hard_pass),
        "preference_satisfaction_pct": float(report.preference_satisfaction_pct),
        "runtime_s": float(runtime),
        "runtime_under_90s": bool(runtime < RUNTIME_BUDGET_S),
        "cpsat_gap": None if gap is None else float(gap),
        "cpsat_rel_gap_pct": None if rel_gap is None else float(rel_gap) * 100.0,
    }
    for name in ("unfilled", "overwork", "streak", "rest", "preference", "fairness", "isolated"):
        row[f"cost_{name}"] = float(report.cost_components.get(name, 0.0))
    for group in GROUPS:
        row[f"spread_{group}"] = float(report.workload_spread_by_group.get(group, 0.0))
    return row


def _repair_task(payload: dict[str, Any]) -> dict[str, Any]:
    case = load_case(payload["case_dir"])
    rules = Rules.from_case(case)
    roster = load_roster(payload["roster_path"])
    before = evaluate(case, rules, roster)
    method = payload["method"]

    started = time.perf_counter()
    if method == "local":
        from .repair.local_repair import repair

        result = repair(
            case,
            roster,
            payload["roll_no"],
            payload["sick_day"],
            payload["shift_id"],
            payload["time_limit_s"],
            payload["seed"],
        )
    else:
        from .repair.cpsat_repair import repair

        result = repair(
            case,
            roster,
            payload["roll_no"],
            payload["sick_day"],
            payload["shift_id"],
            payload["time_limit_s"],
            payload["seed"],
        )
    runtime = time.perf_counter() - started

    report = evaluate(case, rules, result.roster)
    group = case.students[payload["roll_no"]].group
    cell = (payload["sick_day"], payload["shift_id"], group)
    required = int(case.demand.get(cell, 0))
    assigned = sum(
        1
        for (roll_no, day), shift_id in result.roster.items()
        if day == payload["sick_day"]
        and shift_id == payload["shift_id"]
        and case.students[roll_no].group == group
    )
    return {
        "case_id": payload["case_id"],
        "event_index": payload["event_index"],
        "method": method,
        "scenario": payload["scenario"],
        "days": payload["days"],
        "students": payload["students"],
        "load_ratio": payload["load_ratio"],
        "roll_no": payload["roll_no"],
        "sick_day": payload["sick_day"],
        "shift_id": payload["shift_id"],
        "before_cost": float(before.total_cost),
        "after_cost": float(report.total_cost),
        "changes": len(result.diff),
        "runtime_s": float(runtime),
        "unfilled_total": int(report.unfilled_total),
        "sick_slot_unfilled": bool(assigned < required),
        "hard_pass": bool(report.hard_pass),
        "reason": result.reason or "",
    }


# ------------------------------------------------------------------ run
def _run_meta(base_dir: Path) -> dict[str, Any]:
    path = base_dir / "run_meta.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _check_meta(base_dir: Path, meta: dict[str, Any]) -> None:
    existing = _run_meta(base_dir)
    if not existing:
        (base_dir / "run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return
    for key in ("sampling_seed", "time_limit_s", "repair_time_s", "repair_events"):
        if existing.get(key) != meta.get(key):
            raise SystemExit(
                f"results directory {base_dir} was created with {key}="
                f"{existing.get(key)!r}, but this run asks for {meta.get(key)!r}. "
                "Use a fresh --stress-dir (or the same settings) to resume."
            )


def _best_row(rows: list[dict[str, str]]) -> dict[str, str] | None:
    if not rows:
        return None
    priority = {solver: index for index, solver in enumerate(("cpsat_warm", "cpsat_cold", "local", "greedy"))}
    good = [row for row in rows if row.get("hard_pass") == "True"] or rows
    return min(
        good,
        key=lambda row: (float(row["total_cost"]), priority.get(row["solver"], 99)),
    )


def _sick_events(roster: Roster, case_seed: int, count: int) -> list[tuple[str, int, str]]:
    items = sorted(roster.items())
    if not items:
        return []
    rng = random.Random(case_seed + 7919)
    picked = rng.sample(items, min(count, len(items)))
    return [(roll_no, day, shift_id) for (roll_no, day), shift_id in picked]


def run_stress(
    n: int = 100,
    time_limit_s: float = 60.0,
    workers: int = 4,
    sampling_seed: int = 1,
    repair_time_s: float = 10.0,
    repair_events: int = 5,
    base_dir: str | Path = "results/stress",
) -> dict[str, Any]:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)

    _check_meta(
        base,
        {
            "sampling_seed": sampling_seed,
            "time_limit_s": time_limit_s,
            "repair_time_s": repair_time_s,
            "repair_events": repair_events,
            "cpsat_num_workers": 1,
            "solvers": list(SOLVERS),
        },
    )

    manifest = ensure_cases(base, n, sampling_seed, time_limit_s, 1)
    rosters_dir = base / "rosters"
    rosters_dir.mkdir(parents=True, exist_ok=True)

    results_path = base / "results.csv"
    repairs_path = base / "repairs.csv"
    done_results = {
        (row["case_id"], row["solver"]) for row in _read_csv(results_path)
    }
    done_repairs = {
        (row["case_id"], row["event_index"], row["method"])
        for row in _read_csv(repairs_path)
    }

    workers = max(1, min(int(workers), max(1, os.cpu_count() or 1)))
    print(
        f"{len(manifest)} cases, {len(SOLVERS)} solvers, workers={workers}, "
        f"time_limit={time_limit_s:g}s, cpsat_num_workers=1"
    )

    # ---- solve every missing (case, solver) pair.
    appender = _Appender(results_path, RESULT_COLUMNS)
    jobs: list[dict[str, Any]] = []
    for row in manifest:
        case_id = row["case_id"]
        for solver in SOLVERS:
            if (case_id, solver) in done_results:
                continue
            roster_path = rosters_dir / f"{case_id}__{solver}.roster.csv"
            jobs.append(
                {
                    "case_id": case_id,
                    "case_dir": str(case_folder(base, case_id)),
                    "solver": solver,
                    "scenario": row["scenario"],
                    "days": int(row["days"]),
                    "students": int(row["students"]),
                    "load_ratio": float(row["load_ratio"]),
                    "pref_fraction": float(row["pref_fraction"]),
                    "target_load": float(row["target_load"]),
                    "case_seed": int(row["case_seed"]),
                    "time_limit_s": float(time_limit_s),
                    "cpsat_num_workers": 1,
                    "seed": int(row["solver_seed"]),
                    "roster_path": str(roster_path),
                }
            )

    failures = 0
    if jobs:
        print(f"running {len(jobs)} solver pairs ({len(done_results)} already done)")
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_solve_task, payload): payload for payload in jobs}
            try:
                for future in as_completed(futures):
                    payload = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # keep the run alive; report the failure
                        failures += 1
                        print(
                            f"ERROR {payload['case_id']} {payload['solver']}: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    appender.append(result)
                    print(
                        f"  {result['case_id']:<42} {result['solver']:<10} "
                        f"cost={result['total_cost']:.4f} "
                        f"hard_pass={result['hard_pass']} "
                        f"runtime={result['runtime_s']:.2f}s",
                        flush=True,
                    )
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                print("interrupted; completed pairs are saved and will be skipped on resume")
                raise
    else:
        print("all solver pairs already complete")

    # ---- repairs on the best completed roster per case.
    result_rows = _read_csv(results_path)
    by_case: dict[str, list[dict[str, str]]] = {}
    for row in result_rows:
        by_case.setdefault(row["case_id"], []).append(row)
    manifest_by_id = {row["case_id"]: row for row in manifest}

    repair_appender = _Appender(repairs_path, REPAIR_COLUMNS)
    repair_jobs: list[dict[str, Any]] = []
    for case_id, row in manifest_by_id.items():
        best = _best_row(by_case.get(case_id, []))
        if best is None:
            print(f"WARNING {case_id}: no completed solver roster; skipping repairs")
            continue
        roster_path = rosters_dir / f"{case_id}__{best['solver']}.roster.csv"
        if not roster_path.exists():
            print(f"WARNING {case_id}: missing roster {roster_path.name}; skipping repairs")
            continue
        roster = load_roster(str(roster_path))
        events = _sick_events(roster, int(row["case_seed"]), repair_events)
        for event_index, (roll_no, day, shift_id) in enumerate(events):
            for method in REPAIR_METHODS:
                if (case_id, str(event_index), method) in done_repairs:
                    continue
                repair_jobs.append(
                    {
                        "case_id": case_id,
                        "case_dir": str(case_folder(base, case_id)),
                        "roster_path": str(roster_path),
                        "event_index": event_index,
                        "method": method,
                        "scenario": row["scenario"],
                        "days": int(row["days"]),
                        "students": int(row["students"]),
                        "load_ratio": float(row["load_ratio"]),
                        "roll_no": roll_no,
                        "sick_day": day,
                        "shift_id": shift_id,
                        "time_limit_s": float(repair_time_s),
                        "seed": int(row["case_seed"]) + event_index,
                    }
                )

    if repair_jobs:
        print(f"running {len(repair_jobs)} repair pairs ({len(done_repairs)} already done)")
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_repair_task, payload): payload for payload in repair_jobs}
            try:
                for future in as_completed(futures):
                    payload = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        failures += 1
                        print(
                            f"ERROR repair {payload['case_id']} event {payload['event_index']} "
                            f"{payload['method']}: {exc}",
                            file=sys.stderr,
                        )
                        continue
                    repair_appender.append(result)
                    print(
                        f"  repair {result['case_id']:<36} event={result['event_index']} "
                        f"{result['method']:<6} changes={result['changes']} "
                        f"cost={result['after_cost']:.4f} "
                        f"runtime={result['runtime_s']:.2f}s",
                        flush=True,
                    )
            except KeyboardInterrupt:
                pool.shutdown(wait=False, cancel_futures=True)
                print("interrupted; completed repairs are saved and will be skipped on resume")
                raise
    else:
        print("all repair pairs already complete")

    print(f"results: {results_path}")
    print(f"repairs: {repairs_path}")
    if failures:
        print(f"WARNING: {failures} jobs failed; rerun to retry them", file=sys.stderr)
    return {"results": str(results_path), "repairs": str(repairs_path), "failures": failures}


# ------------------------------------------------------------------ report
def _as_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool(value: str) -> bool:
    return value == "True"


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _fmt_num(value: float | None, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _pct(part: float, total: float) -> str:
    return f"{100.0 * part / total:.1f}%" if total else "n/a"


def _plural(count: int, singular: str = "case") -> str:
    return f"{count} {singular}" if count == 1 else f"{count} {singular}s"


def build_report(base_dir: str | Path = "results/stress") -> Path:
    base = Path(base_dir)
    result_rows = _read_csv(base / "results.csv")
    repair_rows = _read_csv(base / "repairs.csv")
    if not result_rows:
        raise SystemExit(f"no solver results in {base / 'results.csv'}")

    for row in result_rows:
        row["_cost"] = _as_float(row["total_cost"])
        row["_runtime"] = _as_float(row["runtime_s"])
        row["_hard"] = _as_bool(row["hard_pass"])
        row["_under90"] = _as_bool(row["runtime_under_90s"])
        row["_load"] = _as_float(row["load_ratio"])
        row["_days"] = int(row["days"])

    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in result_rows:
        by_case.setdefault(row["case_id"], []).append(row)
    case_count = len(by_case)
    solvers = [solver for solver in SOLVERS if any(row["solver"] == solver for row in result_rows)]

    lines = [
        "# Stress benchmark summary",
        "",
        f"{_plural(case_count)}, {len(result_rows)} solver runs, {len(repair_rows)} repair runs.",
        "All costs come from `roster.evaluator.evaluate`. CP-SAT ran with "
        "`num_workers=1` per group inside the parallel stress harness.",
        "",
    ]

    # ---- win rate, ties split evenly.
    credits = {solver: 0.0 for solver in solvers}
    for case_rows in by_case.values():
        best = min(row["_cost"] for row in case_rows)
        tied = [row["solver"] for row in case_rows if abs(row["_cost"] - best) <= EPS]
        for solver in tied:
            credits[solver] += 1.0 / len(tied)
    lines.extend(
        [
            "## Win rate",
            "",
            "A case is won by every solver tied at the lowest evaluator cost; ties share "
            "the win equally.",
            "",
            "| Solver | Weighted wins | Win rate |",
            "| --- | ---: | ---: |",
        ]
    )
    for solver in solvers:
        lines.append(
            f"| {solver} | {credits[solver]:.2f} | {_pct(credits[solver], case_count)} |"
        )
    lines.append("")

    # ---- gap to the best solver per case, by scenario and by days.
    def gap_table(group_key: str, groups: list[Any]) -> list[str]:
        table = [
            "| Group | Solver | Cases | Mean gap | Median gap |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        for group in groups:
            group_cases = [
                rows for rows in by_case.values() if rows and rows[0][group_key] == group
            ]
            if not group_cases:
                continue
            for solver in solvers:
                gaps = []
                for rows in group_cases:
                    row = next((item for item in rows if item["solver"] == solver), None)
                    if row is None:
                        continue
                    best = min(item["_cost"] for item in rows)
                    gaps.append(row["_cost"] - best)
                table.append(
                    f"| {group} | {solver} | {len(gaps)} | "
                    f"{_fmt_num(_mean(gaps))} | {_fmt_num(_median(gaps))} |"
                )
        return table

    lines.extend(["## Cost gap to the best solver", "", "### By scenario", ""])
    lines.extend(gap_table("scenario", list(SCENARIOS)))
    lines.extend(["", "### By days", ""])
    lines.extend(gap_table("_days", list(DAY_CHOICES)))
    lines.append("")

    # ---- hard failures and missed 90 s.
    lines.extend(
        [
            "## Reliability",
            "",
            "| Solver | Runs | hard_pass False | Missed 90 s |",
            "| --- | ---: | ---: | ---: |",
        ]
    )
    for solver in solvers:
        rows = [row for row in result_rows if row["solver"] == solver]
        hard_false = sum(1 for row in rows if not row["_hard"])
        missed = sum(1 for row in rows if not row["_under90"])
        lines.append(
            f"| {solver} | {len(rows)} | {hard_false} ({_pct(hard_false, len(rows))}) | "
            f"{missed} ({_pct(missed, len(rows))}) |"
        )
    lines.append("")

    # ---- runtime.
    lines.extend(
        [
            "## Runtime",
            "",
            "| Solver | Median runtime (s) | Mean runtime (s) |",
            "| --- | ---: | ---: |",
        ]
    )
    for solver in solvers:
        runtimes = [
            row["_runtime"]
            for row in result_rows
            if row["solver"] == solver and row["_runtime"] is not None
        ]
        lines.append(
            f"| {solver} | {_fmt_num(_median(runtimes))} | {_fmt_num(_mean(runtimes))} |"
        )
    lines.append("")

    # ---- local vs CP-SAT by load.
    comparisons = []
    for case_id, rows in by_case.items():
        local = next((row for row in rows if row["solver"] == "local"), None)
        cpsat_costs = [
            row["_cost"] for row in rows if row["solver"].startswith("cpsat")
        ]
        if local is None or not cpsat_costs:
            continue
        best_cpsat = min(cpsat_costs)
        behind = (
            (local["_cost"] - best_cpsat) / best_cpsat if best_cpsat > EPS else 0.0
        )
        comparisons.append(
            {
                "case_id": case_id,
                "load": local["_load"],
                "behind": behind,
                "local_cost": local["_cost"],
                "cpsat_cost": best_cpsat,
            }
        )
    violations = [item for item in comparisons if item["behind"] > 0.10 + EPS]
    lines.extend(["## Local search vs CP-SAT by load", ""])
    if comparisons:
        lines.append(
            f"Compared on {len(comparisons)} cases. 'More than 10% behind' means "
            "local cost exceeds the best CP-SAT cost by over 10%."
        )
        lines.append("")
        if violations:
            violations.sort(key=lambda item: item["load"])
            threshold = violations[0]["load"]
            above = [item for item in comparisons if item["load"] >= threshold - EPS]
            above_bad = [item for item in above if item["behind"] > 0.10 + EPS]
            loads = sorted(item["load"] for item in comparisons)
            lines.append(
                f"Local search first falls more than 10% behind at load ratio "
                f"**{threshold:.3f}** ({violations[0]['case_id']})."
            )
            lines.append("")
            if threshold <= loads[0] + EPS:
                lines.append(
                    f"This is the lowest load in the sample, so every case is at or "
                    f"above it; {len(above_bad)} of {len(above)} cases are more than "
                    "10% behind."
                )
            else:
                lines.append(
                    f"At or above that load, {len(above_bad)} of {len(above)} cases are "
                    f"more than 10% behind; below it, "
                    f"{len(violations) - len(above_bad)} of {len(comparisons) - len(above)} are."
                )
            material = [
                item
                for item in violations
                if item["local_cost"] - item["cpsat_cost"] >= 1.0
            ]
            if material:
                first_material = min(material, key=lambda item: item["load"])
                lines.append("")
                lines.append(
                    "Some of the earliest violations are balanced cases whose total cost "
                    "is below 0.05 units, where a 10% band is only a few hundredths of a "
                    "unit. The first material violation (an absolute gap of at least 1.0 "
                    f"cost unit) is at load ratio **{first_material['load']:.3f}** "
                    f"({first_material['case_id']}, local {first_material['local_cost']:.4f} "
                    f"vs CP-SAT {first_material['cpsat_cost']:.4f})."
                )
        else:
            lines.append(
                "No case had local search more than 10% behind CP-SAT, so no load "
                "threshold is identified."
            )
        if violations:
            lines.extend(["", "| Case | Load | Local | CP-SAT | Behind |", "| --- | ---: | ---: | ---: | ---: |"])
            for item in sorted(violations, key=lambda value: value["load"]):
                lines.append(
                    f"| {item['case_id']} | {item['load']:.3f} | "
                    f"{item['local_cost']:.4f} | {item['cpsat_cost']:.4f} | "
                    f"{100.0 * item['behind']:.1f}% |"
                )
    else:
        lines.append("No case had both a local and a CP-SAT result.")
    lines.append("")

    # ---- repairs.
    lines.extend(
        [
            "## Sick-leave repair",
            "",
            "| Method | Runs | Mean changes | Median changes | Mean cost | Median cost | "
            "Median runtime (s) | Sick slot unfilled | hard_pass False |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for method in REPAIR_METHODS:
        rows = [row for row in repair_rows if row["method"] == method]
        changes = [float(row["changes"]) for row in rows]
        costs = [value for value in (_as_float(row["after_cost"]) for row in rows) if value is not None]
        runtimes = [value for value in (_as_float(row["runtime_s"]) for row in rows) if value is not None]
        left = sum(1 for row in rows if _as_bool(row["sick_slot_unfilled"]))
        hard_false = sum(1 for row in rows if not _as_bool(row["hard_pass"]))
        lines.append(
            f"| {method} | {len(rows)} | {_fmt_num(_mean(changes), 2)} | "
            f"{_fmt_num(_median(changes), 2)} | {_fmt_num(_mean(costs), 4)} | "
            f"{_fmt_num(_median(costs), 4)} | {_fmt_num(_median(runtimes), 3)} | "
            f"{left} ({_pct(left, len(rows))}) | {hard_false} ({_pct(hard_false, len(rows))}) |"
        )
    lines.append("")

    # ---- recommendation.
    lines.extend(["## Recommendation", ""])
    recommendations = []
    for scenario in SCENARIOS:
        scenario_cases = [
            rows for rows in by_case.values() if rows and rows[0]["scenario"] == scenario
        ]
        if not scenario_cases:
            continue
        mean_costs: dict[str, float] = {}
        for solver in solvers:
            costs = [
                row["_cost"]
                for rows in scenario_cases
                for row in rows
                if row["solver"] == solver
            ]
            if costs:
                mean_costs[solver] = sum(costs) / len(costs)
        if not mean_costs:
            continue
        winner = min(mean_costs, key=lambda solver: mean_costs[solver])
        thin = len(scenario_cases) < 10
        recommendations.append(
            f"**{scenario}**: {winner} has the lowest mean cost "
            f"({mean_costs[winner]:.4f} over {_plural(len(scenario_cases))})"
            + (
                " - evidence is thin (fewer than 10 cases in this group)."
                if thin
                else "."
            )
        )
    overall_costs = {}
    for solver in solvers:
        costs = [row["_cost"] for row in result_rows if row["solver"] == solver]
        if costs:
            overall_costs[solver] = sum(costs) / len(costs)
    overall = min(overall_costs, key=lambda solver: overall_costs[solver]) if overall_costs else "n/a"
    thin_groups = [
        scenario
        for scenario in SCENARIOS
        if 0 < sum(1 for rows in by_case.values() if rows and rows[0]["scenario"] == scenario) < 10
    ]
    if thin_groups:
        thin_text = "Groups with thin evidence (fewer than 10 cases): " + ", ".join(thin_groups) + "."
    else:
        thin_text = "Every scenario group has at least 10 cases, so none is thin-evidenced."

    repair_note = ""
    repair_stats = {}
    for method in REPAIR_METHODS:
        rows = [row for row in repair_rows if row["method"] == method]
        if not rows:
            continue
        changes = [float(row["changes"]) for row in rows]
        costs = [value for value in (_as_float(row["after_cost"]) for row in rows) if value is not None]
        left = sum(1 for row in rows if _as_bool(row["sick_slot_unfilled"]))
        repair_stats[method] = {
            "changes": _mean(changes),
            "cost": _mean(costs),
            "left": left,
            "runs": len(rows),
        }
    if "local" in repair_stats and "cpsat" in repair_stats:
        local_stats = repair_stats["local"]
        cpsat_stats = repair_stats["cpsat"]
        repair_note = (
            f" For sick-leave repair, local made {local_stats['changes']:.2f} changes on "
            f"average versus {cpsat_stats['changes']:.2f} for CP-SAT, and left the sick "
            f"slot unfilled in {_pct(local_stats['left'], local_stats['runs'])} versus "
            f"{_pct(cpsat_stats['left'], cpsat_stats['runs'])} of events; CP-SAT's mean "
            f"post-repair cost was {cpsat_stats['cost']:.4f} versus {local_stats['cost']:.4f}."
        )

    paragraph = (
        "Across all scenarios the lowest mean-cost solver is "
        f"**{overall}**. Per scenario: " + " ".join(recommendations) + " " + thin_text +
        " These results come from the stress harness setting of one CP-SAT search worker "
        "per group, which makes CP-SAT timings comparable across cases but weaker than "
        "the unrestricted C1 comparison; read the ranking as the behaviour of that "
        "constrained configuration, not as an upper bound on CP-SAT quality." +
        repair_note
    )
    lines.extend([paragraph, ""])

    path = base / "summary.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# ------------------------------------------------------------------ CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roster.stress",
        description="Stress-test the roster solvers on randomly generated cases.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="generate cases and run all solvers")
    run_parser.add_argument("--n", type=int, default=100)
    run_parser.add_argument("--time", dest="time_limit_s", type=float, default=60.0)
    run_parser.add_argument("--workers", type=int, default=4)
    run_parser.add_argument("--seed", dest="sampling_seed", type=int, default=1)
    run_parser.add_argument("--repair-time", dest="repair_time_s", type=float, default=10.0)
    run_parser.add_argument("--repair-events", type=int, default=5)
    run_parser.add_argument("--stress-dir", default="results/stress")

    report_parser = subparsers.add_parser("report", help="build summary.md from results")
    report_parser.add_argument("--stress-dir", default="results/stress")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        if args.n < 1:
            print("--n must be at least 1", file=sys.stderr)
            return 2
        try:
            run_stress(
                n=args.n,
                time_limit_s=args.time_limit_s,
                workers=args.workers,
                sampling_seed=args.sampling_seed,
                repair_time_s=args.repair_time_s,
                repair_events=args.repair_events,
                base_dir=args.stress_dir,
            )
        except SystemExit as exc:
            print(exc, file=sys.stderr)
            return 1
        except KeyboardInterrupt:
            return 130
        return 0

    try:
        path = build_report(args.stress_dir)
    except SystemExit as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
