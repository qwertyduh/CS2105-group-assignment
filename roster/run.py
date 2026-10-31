"""CLI to run a roster solver (or a sick-leave repair) on a case folder.

Examples::

    python -m roster.run greedy test/case_01_balanced_20d --seed 1
    python -m roster.run local test/case_01_balanced_20d --seed 1 --time 60
    python -m roster.run local test/case_04_small_7d --seed 1 --time 5 --debug

The roster is written to ``results/<case>_<solver>.roster.csv`` and the
evaluator report beside it as ``results/<case>_<solver>.report.json``. The
command prints the total cost and runtime. Greedy and local search are
implemented; ``cpsat`` is wired up so a later phase can drop in its module
without changing this CLI. ``--debug`` makes local search cross-check its
incremental cost against the evaluator every 1000 accepted moves and at the
end.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .case_io import Case, CaseError, load_case
from .evaluator import evaluate
from .rules import Rules, RulesError
from .solvers.base import Roster, save_roster


def _run_solver(
    name: str,
    case: Case,
    time_limit_s: float,
    seed: int,
    debug: bool = False,
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
    else:  # pragma: no cover - argparse already constrains this
        raise SystemExit(f"unknown solver {name!r}")
    return solve(case, time_limit_s, seed)


def _run_repair(
    case: Case,
    roster: Roster,
    sick_roll_no: str,
    sick_day: int,
    time_limit_s: float,
    seed: int,
    method: str,
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
    )
    print(json.dumps({"diff": result.diff, "reason": result.reason}, indent=2, default=str))


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
        )
        return 0

    started = time.perf_counter()
    roster = _run_solver(
        args.command,
        case,
        args.time_limit_s,
        args.seed,
        debug=getattr(args, "debug", False),
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
    print(f"  wrote: {roster_path}")
    print(f"  wrote: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
