"""Stage-by-stage comparison of the greedy, local-search and CP-SAT solvers.

Every row is scored with :func:`roster.evaluator.evaluate`; a solver's own
objective value is never used as the reported cost. The only extra number taken
from a solver is CP-SAT's proven optimality gap, which the evaluator does not
produce.

Solvers compared, all with the same case, seed and time limit:

* ``greedy``      - A4 greedy builder
* ``local``       - A5 simulated-annealing local search
* ``cpsat_cold``  - B2 four-group CP-SAT, no hint
* ``cpsat_warm``  - B2 four-group CP-SAT, greedy warm-start hint

Outputs:

* ``results/compare.csv`` - one row per (case, solver)
* ``results/compare.md``  - a table per case (best total cost in bold), the
  read-out paragraph, and every sanity-ordering warning with its investigation
* ``results/compare.png`` - grouped bar chart when matplotlib is already
  installed; otherwise no chart is written

CLI::

    python -m roster.compare --time 60 --seed 1
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from .case_io import Case, CaseError, load_case
from .evaluator import GROUPS, evaluate
from .rules import Rules, RulesError
from .solvers.base import Roster

SOLVERS = ("greedy", "local", "cpsat_cold", "cpsat_warm")
COMPONENTS = ("unfilled", "overwork", "streak", "rest", "preference", "fairness", "isolated")
RUNTIME_BUDGET_S = 90.0
EPS = 1e-9

CSV_COLUMNS = (
    "case",
    "solver",
    "seed",
    "time_limit_s",
    "total_cost",
    *(f"cost_{name}" for name in COMPONENTS),
    "unfilled_total",
    "hard_pass",
    "preference_satisfaction_pct",
    *(f"spread_{group}" for group in GROUPS),
    "runtime_s",
    "runtime_under_90s",
    "cpsat_gap",
    "cpsat_rel_gap_pct",
    "cpsat_status",
    "cpsat_no_solution_groups",
)

# Filled in after a warning has been investigated. Key is "<case>|<relation>". Each
# entry records the costs it was written for; it is only shown while the current run
# reproduces those costs, so a stale explanation never sits under new numbers.
WARNING_EXPLANATIONS: dict[str, dict[str, Any]] = {
    "case_02_tight_28d|warm-vs-cold": {
        "costs": {"cpsat_warm": 62.016512, "cpsat_cold": 62.014478},
        "text": (
        "The two CP-SAT runs differ by only 0.0020 units (0.003%). Both "
        "have exactly the same hard-rule outcome and rule-breach components "
        "(unfilled=54, overwork=8, streak=0, rest=0); the difference is entirely in the "
        "soft preference term (warm 96.7% satisfied vs cold 99.2%). Both groups finished "
        "FEASIBLE at the 60 s limit (warm gap 0.0082, cold gap 0.0063), so neither run "
        "proved optimality and their costs differ by less than either proven gap. The "
        "greedy hint can steer the multi-threaded search onto a slightly worse "
        "near-optimal incumbent on a tight case; it is not a correctness or hard-rule "
        "problem, and on the other four cases warm is better than or tied with cold."
        ),
    },
}
EXPLANATION_COST_TOLERANCE = 1e-4


def warning_explanation(warning: dict[str, Any]) -> str | None:
    """The recorded investigation for ``warning``, if it was written for these costs."""
    entry = WARNING_EXPLANATIONS.get(warning["key"])
    if entry is None:
        return None
    costs = warning.get("costs", {})
    for solver, expected in entry["costs"].items():
        actual = costs.get(solver)
        if actual is None or abs(actual - expected) > EXPLANATION_COST_TOLERANCE:
            return None
    return entry["text"]


def _run_solver(
    solver: str,
    case: Case,
    time_limit_s: float,
    seed: int,
) -> tuple[Roster, dict[str, Any]]:
    """Run one solver and return (roster, side information)."""
    if solver == "greedy":
        from .solvers.greedy import solve

        return solve(case, time_limit_s, seed), {}
    if solver == "local":
        from .solvers.local_search import solve

        return solve(case, time_limit_s, seed), {}
    if solver in ("cpsat_cold", "cpsat_warm"):
        from .solvers.cpsat import solve

        side: dict[str, Any] = {}
        roster = solve(
            case,
            time_limit_s=time_limit_s,
            seed=seed,
            side=side,
            warm_start=(solver == "cpsat_warm"),
        )
        return roster, side
    raise ValueError(f"unknown solver {solver!r}")


Runner = Callable[[str, Case, float, int], tuple[Roster, dict[str, Any]]]


def collect_row(
    case: Case,
    rules: Rules,
    solver: str,
    time_limit_s: float,
    seed: int,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """Run ``solver`` once and score its roster with the evaluator."""
    runner = runner or _run_solver
    case_name = Path(case.folder).name
    started = time.perf_counter()
    roster, side = runner(solver, case, time_limit_s, seed)
    runtime = time.perf_counter() - started
    report = evaluate(case, rules, roster)

    gap = side.get("gap") if solver.startswith("cpsat") else None
    rel_gap = side.get("rel_gap") if solver.startswith("cpsat") else None
    status = None
    no_solution = None
    if solver.startswith("cpsat"):
        statuses = side.get("status") or {}
        if statuses:
            status = ",".join(f"{group}:{status}" for group, status in statuses.items())
        no_solution = ";".join(side.get("no_solution_groups") or [])

    row: dict[str, Any] = {
        "case": case_name,
        "solver": solver,
        "seed": seed,
        "time_limit_s": float(time_limit_s),
        "total_cost": float(report.total_cost),
        "unfilled_total": int(report.unfilled_total),
        "hard_pass": bool(report.hard_pass),
        "preference_satisfaction_pct": float(report.preference_satisfaction_pct),
        "runtime_s": float(runtime),
        "runtime_under_90s": bool(runtime < RUNTIME_BUDGET_S),
        "cpsat_gap": None if gap is None else float(gap),
        "cpsat_rel_gap_pct": None if rel_gap is None else float(rel_gap) * 100.0,
        "cpsat_status": status,
        "cpsat_no_solution_groups": no_solution,
    }
    for name in COMPONENTS:
        row[f"cost_{name}"] = float(report.cost_components.get(name, 0.0))
    for group in GROUPS:
        row[f"spread_{group}"] = float(report.workload_spread_by_group.get(group, 0.0))
    return row


def discover_cases(root: str | Path = "test") -> list[Path]:
    """Every case folder under ``root``, sorted by name."""
    root = Path(root)
    if not root.is_dir():
        return []
    return sorted(
        folder
        for folder in root.iterdir()
        if folder.is_dir() and (folder / "config.csv").is_file()
    )


def sanity_warnings(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return every broken cost-ordering expectation as a warning record."""
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case"], {})[row["solver"]] = row

    warnings: list[dict[str, str]] = []
    for case_name in by_case:
        costs = by_case[case_name]
        summary = (
            "costs: "
            + ", ".join(
                f"{solver}={costs[solver]['total_cost']:.4f}"
                for solver in SOLVERS
                if solver in costs
            )
        )

        def warn(relation: str, detail: str) -> None:
            warnings.append(
                {
                    "key": f"{case_name}|{relation}",
                    "text": f"WARNING: {case_name}: {detail}; {summary}",
                    "costs": {
                        solver: costs[solver]["total_cost"]
                        for solver in SOLVERS
                        if solver in costs
                    },
                }
            )

        warm = costs.get("cpsat_warm")
        cold = costs.get("cpsat_cold")
        local = costs.get("local")
        greedy = costs.get("greedy")

        if warm and cold and warm["total_cost"] > cold["total_cost"] + EPS:
            warn(
                "warm-vs-cold",
                f"cpsat_warm ({warm['total_cost']:.4f}) > cpsat_cold ({cold['total_cost']:.4f})",
            )
        if warm and local and warm["total_cost"] > local["total_cost"] + EPS:
            warn(
                "warm-vs-local",
                f"cpsat_warm ({warm['total_cost']:.4f}) > local ({local['total_cost']:.4f})",
            )
        if local and greedy and local["total_cost"] > greedy["total_cost"] + EPS:
            warn(
                "local-vs-greedy",
                f"local ({local['total_cost']:.4f}) > greedy ({greedy['total_cost']:.4f})",
            )
    return warnings


def _num(value: float | None, digits: int = 6) -> str:
    return "" if value is None else f"{value:.{digits}f}"


def _csv_row(row: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for column in CSV_COLUMNS:
        value = row.get(column)
        if column in ("case", "solver", "cpsat_status", "cpsat_no_solution_groups"):
            out[column] = "" if value is None else str(value)
        elif column in ("seed",):
            out[column] = str(value)
        elif column in ("unfilled_total",):
            out[column] = str(value)
        elif column in ("hard_pass", "runtime_under_90s"):
            out[column] = "True" if value else "False"
        elif column in ("time_limit_s", "runtime_s"):
            out[column] = _num(value, 3)
        elif column == "cpsat_rel_gap_pct":
            out[column] = _num(value, 4)
        else:
            out[column] = _num(value, 6)
    return out


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(CSV_COLUMNS))
        writer.writeheader()
        for row in rows:
            writer.writerow(_csv_row(row))


def _winner_text(
    case_name: str,
    winner_text: str,
    tied_count: int,
    best_cost: float,
    runner_up: dict[str, Any],
) -> str:
    gap_pct = (
        (runner_up["total_cost"] - best_cost) / runner_up["total_cost"] * 100.0
        if runner_up["total_cost"] > EPS
        else 0.0
    )
    verb = "tied" if tied_count > 1 else "won"
    return (
        f"{case_name}: {winner_text} {verb} at {best_cost:.4f}, "
        f"{gap_pct:.1f}% below {runner_up['solver']} ({runner_up['total_cost']:.4f})"
    )


def _readout(rows: list[dict[str, Any]]) -> str:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case"], []).append(row)

    parts: list[str] = []
    local_within_five: list[str] = []
    for case_name, case_rows in by_case.items():
        ranked = sorted(case_rows, key=lambda row: row["total_cost"])
        best = ranked[0]
        best_cost = best["total_cost"]
        tied = [
            row["solver"]
            for row in ranked
            if abs(row["total_cost"] - best_cost) <= EPS
        ]
        winner_text = " and ".join(tied)
        if len(ranked) == 1:
            parts.append(f"{case_name}: only {winner_text} ran, at {best_cost:.4f}")
            continue
        runner_up = next(
            (row for row in ranked if row["total_cost"] > best_cost + EPS), None
        )
        if runner_up is None:
            parts.append(f"{case_name}: {winner_text} all tied at {best_cost:.4f}")
        else:
            parts.append(_winner_text(case_name, winner_text, len(tied), best_cost, runner_up))

        local = next((row for row in case_rows if row["solver"] == "local"), None)
        cpsat_costs = [
            row["total_cost"]
            for row in case_rows
            if row["solver"].startswith("cpsat")
        ]
        if local is not None and cpsat_costs:
            best_cpsat = min(cpsat_costs)
            if local["total_cost"] <= best_cpsat * 1.05 + EPS:
                local_within_five.append(
                    f"{case_name} (local {local['total_cost']:.4f} vs CP-SAT {best_cpsat:.4f})"
                )

    readout = "; ".join(parts) + "."
    if local_within_five:
        readout += (
            " Local search came within 5% of CP-SAT on: "
            + ", ".join(local_within_five)
            + "."
        )
    else:
        readout += " Local search did not come within 5% of CP-SAT on any case."
    return readout


def write_markdown(
    rows: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    path: Path,
    seed: int,
    time_limit_s: float,
) -> None:
    by_case: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_case.setdefault(row["case"], []).append(row)

    lines = [
        "# Solver comparison",
        "",
        f"Seed **{seed}**, time limit **{time_limit_s:g} s** per solver per case. "
        "Every cost below comes from `roster.evaluator.evaluate`, never from a solver's "
        "own objective. CP-SAT gap is the solver's proven bound relative to its own "
        "objective; it is reported for information only.",
        "",
    ]

    for case_name, case_rows in by_case.items():
        best = min(row["total_cost"] for row in case_rows)
        lines.append(f"## {case_name}")
        lines.append("")
        lines.append(
            "| Solver | Total cost | Unfilled | Hard pass | Pref % | Overwork | Streak | Rest | "
            "Spread MPR / CND / Hosp / SPONS | Runtime (s) | CP-SAT gap | Under 90 s |"
        )
        lines.append(
            "| --- | ---: | ---: | :---: | ---: | ---: | ---: | ---: | --- | ---: | --- | :---: |"
        )
        for row in case_rows:
            cost_text = f"{row['total_cost']:.4f}"
            if abs(row["total_cost"] - best) <= EPS:
                cost_text = f"**{cost_text}**"
            spreads = " / ".join(
                f"{row[f'spread_{group}']:.1f}" for group in GROUPS
            )
            gap_text = "-"
            if row["cpsat_gap"] is not None:
                rel = row["cpsat_rel_gap_pct"]
                gap_text = f"{row['cpsat_gap']:.4f}"
                if rel is not None:
                    gap_text += f" ({rel:.2f}%)"
            lines.append(
                f"| {row['solver']} | {cost_text} | {row['unfilled_total']} | "
                f"{'yes' if row['hard_pass'] else 'NO'} | "
                f"{row['preference_satisfaction_pct']:.1f} | "
                f"{row['cost_overwork']:.2f} | {row['cost_streak']:.2f} | "
                f"{row['cost_rest']:.2f} | {spreads} | {row['runtime_s']:.2f} | "
                f"{gap_text} | {'yes' if row['runtime_under_90s'] else 'NO'} |"
            )
        lines.append("")
        for row in case_rows:
            if row.get("cpsat_no_solution_groups"):
                lines.append(
                    f"{row['solver']} found no solution in time for "
                    f"{row['cpsat_no_solution_groups'].replace(';', ', ')}; those groups "
                    "have no assignments, so its cost is not a like-for-like result."
                )
                lines.append("")

    lines.append("## Read-out")
    lines.append("")
    lines.append(_readout(rows))
    lines.append("")
    lines.append("## Sanity checks")
    lines.append("")
    if warnings:
        for warning in warnings:
            lines.append(f"- {warning['text']}")
            explanation = warning_explanation(warning)
            if explanation:
                lines.append(f"  - Investigation: {explanation}")
            else:
                lines.append(
                    "  - Investigation: **not yet recorded** - explain this warning "
                    "before closing the phase."
                )
    else:
        lines.append(
            "No ordering warnings. Every case satisfied "
            "`cpsat_warm <= cpsat_cold`, `cpsat_warm <= local` and `local <= greedy`."
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def write_chart(rows: list[dict[str, Any]], path: Path) -> bool:
    """Write a grouped bar chart when matplotlib is available; return success."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return False

    by_case: dict[str, dict[str, float]] = {}
    for row in rows:
        by_case.setdefault(row["case"], {})[row["solver"]] = row["total_cost"]

    cases = list(by_case)
    width = 0.2
    fig, ax = plt.subplots(figsize=(max(8, 2.2 * len(cases)), 5))
    for index, solver in enumerate(SOLVERS):
        values = [by_case[case].get(solver, 0.0) for case in cases]
        offsets = [position + (index - 1.5) * width for position in range(len(cases))]
        ax.bar(offsets, values, width, label=solver)
    ax.set_ylabel("Evaluator total cost (lower is better)")
    ax.set_xticks(range(len(cases)))
    ax.set_xticklabels(cases, rotation=20, ha="right")
    ax.legend()
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)
    return True


def run_comparison(
    case_folders: list[Path],
    time_limit_s: float,
    seed: int,
    out_dir: str | Path = "results",
    runner: Runner | None = None,
    progress: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Run every solver on every case, write CSV/Markdown, and return the rows."""
    out = Path(out_dir)
    rows: list[dict[str, Any]] = []
    for folder in case_folders:
        case = load_case(str(folder))
        rules = Rules.from_case(case)
        case_name = Path(case.folder).name
        for solver in SOLVERS:
            row = collect_row(case, rules, solver, time_limit_s, seed, runner=runner)
            rows.append(row)
            if progress:
                print(
                    f"{case_name:<28} {solver:<10} cost={row['total_cost']:.4f} "
                    f"hard_pass={row['hard_pass']} unfilled={row['unfilled_total']} "
                    f"runtime={row['runtime_s']:.2f}s"
                )

    warnings = sanity_warnings(rows)
    write_csv(rows, out / "compare.csv")
    write_markdown(rows, warnings, out / "compare.md", seed, time_limit_s)
    chart_written = write_chart(rows, out / "compare.png")
    if progress:
        for warning in warnings:
            print(warning["text"])
        print(f"wrote {out / 'compare.csv'}")
        print(f"wrote {out / 'compare.md'}")
        if chart_written:
            print(f"wrote {out / 'compare.png'}")
        else:
            print("matplotlib not installed; skipped compare.png")
    return rows, warnings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roster.compare",
        description="Compare greedy, local search and CP-SAT on every case in test/.",
    )
    parser.add_argument("--time", dest="time_limit_s", type=float, default=60.0)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--cases", nargs="*", default=None)
    parser.add_argument("--out-dir", default="results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cases:
        folders = [Path(folder) for folder in args.cases]
    else:
        folders = discover_cases("test")
    if not folders:
        print("no case folders found", file=sys.stderr)
        return 1

    try:
        run_comparison(folders, args.time_limit_s, args.seed, args.out_dir)
    except (CaseError, RulesError) as exc:
        print(f"invalid case: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
