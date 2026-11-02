"""CLI to run a roster solver (or a sick-leave repair) on a case folder.

Examples::

    python -m roster.run greedy test/case_01_balanced_20d --seed 1
    python -m roster.run local test/case_01_balanced_20d --seed 1 --time 60
    python -m roster.run cpsat test/case_01_balanced_20d --seed 1 --time 60
    python -m roster.run local test/case_04_small_7d --seed 1 --time 5 --debug
    python -m roster.run repair test/case_04_small_7d \
        results/case_04_small_7d_greedy.roster.csv --sick 22CS01 --day 5

The roster is written to ``results/<case>_<solver>.roster.csv`` and the
evaluator report beside it as ``results/<case>_<solver>.report.json``. The
command prints the total cost and runtime. When running ``cpsat``, it also
prints whether the CP-SAT objective beat the greedy warm start and the
per-group status and optimality gap.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from .case_io import Case, CaseError, load_case
from .evaluator import evaluate
from .rules import Rules, RulesError
from .solvers.base import Roster, save_roster


def _fmt_opt(value: Any, spec: str = ".4f", scale: float = 1.0, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value * scale:{spec}}{suffix}"


def _run_solver(
    name: str,
    case: Case,
    time_limit_s: float,
    seed: int,
    debug: bool = False,
    side: dict[str, Any] | None = None,
) -> Roster:
    if name == "greedy":
        from .solvers.greedy import solve
    elif name == "local":
        from .solvers.local_search import solve

        return solve(case, time_limit_s, seed, debug=debug)
    elif name == "cpsat":
        try:
            from .solvers.cpsat import solve
        except ImportError as exc:
            raise SystemExit(f"solver 'cpsat' is not implemented yet ({exc})") from exc
        return solve(case, time_limit_s, seed, side=side)
    else:  # pragma: no cover - argparse already constrains this
        raise SystemExit(f"unknown solver {name!r}")
    return solve(case, time_limit_s, seed)


def _parse_days(value: str | None) -> list[int] | None:
    if not value:
        return None
    days: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            days.append(int(part))
        except ValueError as exc:
            raise SystemExit(f"--unavailable-days expects integers, got {part!r}") from exc
    return days or None


def _run_repair(
    case: Case,
    roster: Roster,
    sick_roll_no: str,
    sick_day: int,
    time_limit_s: float,
    seed: int,
    method: str,
    unavailable_days: list[int] | None = None,
    out_dir: str = "results",
) -> None:
    shift_id = roster.get((sick_roll_no, sick_day))
    if shift_id is None:
        raise SystemExit(
            f"{sick_roll_no} is not rostered on day {sick_day}; nothing to repair"
        )
    module_name = "roster.repair.cpsat_repair" if method == "cpsat" else "roster.repair.local_repair"
    try:
        module = __import__(module_name, fromlist=["repair"])
    except ImportError as exc:
        raise SystemExit(f"repair method {method!r} is not implemented yet ({exc})") from exc
    result = module.repair(
        case,
        roster,
        sick_roll_no,
        sick_day,
        shift_id,
        time_limit_s,
        seed,
        unavailable_days=unavailable_days,
    )

    rules = Rules.from_case(case)
    report = evaluate(case, rules, result.roster)
    case_name = Path(case.folder).name
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    roster_path = out_path / f"{case_name}_repair.roster.csv"
    save_roster(result.roster, str(roster_path))
    report_path = out_path / f"{case_name}_repair.report.json"
    report_path.write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")

    print(
        f"{case_name} repair: total_cost={report.total_cost:.4f} "
        f"hard_pass={report.hard_pass} unfilled={report.unfilled_total} "
        f"changed={len(result.diff)}"
    )
    print(json.dumps({"diff": result.diff, "reason": result.reason}, indent=2, default=str))
    print(f"  wrote: {roster_path}")
    print(f"  wrote: {report_path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m roster.run",
        description="Run a roster solver or a sick-leave repair on a case folder.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    for solver in ("greedy", "local", "cpsat"):
        run_parser = subparsers.add_parser(solver, help=f"build a roster with the {solver} solver")
        run_parser.add_argument("case_folder")
        run_parser.add_argument("--seed", type=int, default=1)
        run_parser.add_argument("--time", dest="time_limit_s", type=float, default=90.0)
        run_parser.add_argument("--out-dir", default="results")
        run_parser.add_argument(
            "--debug",
            action="store_true",
            help="cross-check local search's incremental cost against the evaluator",
        )

    repair_parser = subparsers.add_parser("repair", help="re-optimize after a sick call")
    repair_parser.add_argument("case_folder")
    repair_parser.add_argument("roster_csv")
    repair_parser.add_argument("--sick", required=True)
    repair_parser.add_argument("--day", type=int, required=True)
    repair_parser.add_argument(
        "--unavailable-days",
        default=None,
        help="extra days the sick student is unavailable, comma separated (e.g. 5,6)",
    )
    repair_parser.add_argument("--method", choices=("local", "cpsat"), default="local")
    repair_parser.add_argument("--seed", type=int, default=1)
    repair_parser.add_argument("--time", dest="time_limit_s", type=float, default=90.0)
    repair_parser.add_argument("--out-dir", default="results")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        case = load_case(args.case_folder)
        rules = Rules.from_case(case)
    except (CaseError, RulesError) as exc:
        print(f"invalid case: {exc}", file=sys.stderr)
        return 1

    if args.command == "repair":
        from .solvers.base import load_roster, RosterError

        try:
            roster = load_roster(args.roster_csv)
        except RosterError as exc:
            print(f"invalid roster: {exc}", file=sys.stderr)
            return 1
        _run_repair(
            case,
            roster,
            args.sick,
            args.day,
            args.time_limit_s,
            args.seed,
            args.method,
            unavailable_days=_parse_days(args.unavailable_days),
            out_dir=args.out_dir,
        )
        return 0

    side: dict[str, Any] = {}
    started = time.perf_counter()
    roster = _run_solver(
        args.command,
        case,
        args.time_limit_s,
        args.seed,
        debug=getattr(args, "debug", False),
        side=side,
    )
    runtime = time.perf_counter() - started

    report = evaluate(case, rules, roster)
    case_name = Path(case.folder).name
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    roster_path = out_dir / f"{case_name}_{args.command}.roster.csv"
    report_path = out_dir / f"{case_name}_{args.command}.report.json"
    save_roster(roster, str(roster_path))
    report_path.write_text(json.dumps(report.to_json(), indent=2), encoding="utf-8")

    components = ", ".join(
        f"{name}={value:g}" for name, value in report.cost_components.items()
    )
    print(
        f"{case_name} {args.command}: total_cost={report.total_cost:.4f} "
        f"hard_pass={report.hard_pass} unfilled={report.unfilled_total} "
        f"runtime={runtime:.3f}s"
    )
    print(f"  components: {components}")
    if args.command == "cpsat" and side:
        greedy_cost = side.get("greedy_cost")
        beat_greedy = side.get("beat_greedy")
        if greedy_cost is not None:
            print(
                f"  warm_start: greedy_cost={greedy_cost:.4f} "
                f"cpsat_cost={report.total_cost:.4f} "
                f"beat_greedy={beat_greedy}"
            )
        for group_name, ginfo in side.get("groups", {}).items():
            status = ginfo.get("status", "UNKNOWN")
            gap_text = _fmt_opt(ginfo.get("gap"))
            rel_gap_text = _fmt_opt(ginfo.get("rel_gap"), ".2f", 100.0, "%")
            obj = ginfo.get("objective")
            bound = ginfo.get("best_bound")
            obj_text = f"{obj:.4f}" if obj is not None else "n/a"
            bound_text = f"{bound:.4f}" if bound is not None else "n/a"
            print(
                f"  group {group_name}: status={status} gap={gap_text} "
                f"({rel_gap_text}) objective={obj_text} bound={bound_text} "
                f"time_limit={ginfo.get('time_limit_s', 0.0):.2f}s "
                f"workers={ginfo.get('workers', 1)}"
            )
    print(f"  wrote: {roster_path}")
    print(f"  wrote: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
