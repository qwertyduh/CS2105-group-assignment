# Festival Shift Roster Optimizer Progress

Updated: 31 October 2026

## Completed

### Synthetic case generator

- Generates CSV inputs for balanced, tight and overloaded scenarios with configurable horizon, group sizes, preferences and weekly caps.
- Supports repeatable generation with a seed and validation of existing case folders.

```sh
python3 generate_case.py --scenario tight --days 28 --seed 7
```

### Case loading and validation — Soumya

- Added `roster/case_io.py` with `Case`, `Student`, `Shift` and `Pref` dataclasses, plus indexed students, groups, shifts, demand and preferences.
- Added `load_case`, `validate_case` and aggregated validation errors through `CaseError`.
- Checks missing files, CSV headers, duplicate identifiers, unknown references, horizon bounds, demand-grid completeness, demand values, preferences, shift durations and midnight crossings.
- Added a CLI summary showing horizon, student counts, demand, estimated capacity and load ratio.
- Added four sample cases: balanced 20-day, tight 28-day, overloaded 20-day and small 7-day.
- Added 15 unit tests; all pass on the current implementation.

```sh
python3 -m unittest -v
python3 -m roster.case_io test/case_01_balanced_20d
```

### Rules and penalty helpers — A2

- Added `roster/rules.py` with a frozen `Rules` dataclass typed to the config keys, the documented defaults, and `Rules.from_case` for per-case overrides.
- Unknown config keys, non-numeric values and fractional integer fields raise `RulesError`; they are never silently ignored.
- Recorded the two hard rules as module docstring text and as `HARD_ONE_SHIFT_PER_DAY`, `HARD_SKILL_MATCHING` and `HARD_RULES` constants, each with its reason.
- Added pure helpers: `rest_gap_hours` (including Night-to-next-day wraparound), `short_rest`, `overwork_penalty` and `rest_chain_penalty`. The last three accept the configured rule values as optional arguments.
- Added `python3 -m roster.rules`, which prints every rule, its default value and description, followed by the hard rules.
- Added 22 unit tests in `tests/test_rules.py`; the full suite now passes 37 tests.

```sh
python3 -m roster.rules
python3 -m unittest -v
```

### Greedy solver and run CLI — A4

- Added `roster/solvers/greedy.py`: hard-first slot ordering (required / group size, peak day, Night, seeded random tie-break) and one-person-at-a-time filling.
- Candidates must fit every affected rolling window under an enabled hard ceiling, then are ranked by marginal overwork, streak and rest cost using the same rolling-window pricing as the evaluator, with sub-unit fairness and preference tie-breakers.
- A slot is left unfilled only when the cheapest candidate costs more than `penalty_unfilled_slot`; rosters are never overstaffed and use only own-group members.
- Added `roster/run.py` (`python3 -m roster.run greedy <case> --seed N`) which writes `results/<case>_greedy.roster.csv` and `.report.json` and prints cost, hard pass, unfilled count and runtime.
- Added 11 tests in `tests/test_greedy.py`; the full suite passes 72 tests.
- Review correction (PR #3, approved and merged): overwork is now priced on every rolling window in both the evaluator and solver, and an enabled hard ceiling is enforced before accepting a candidate. Regression tests cover the ceiling and a cross-boundary overwork case.
- Baseline costs (`results/greedy_baseline.txt`, seed 1): case_01 2.0311, case_02 299.0456, case_03 571.0477, case_04 0.0287. All four pass the hard rules.

```sh
python3 -m roster.run greedy test/case_01_balanced_20d --seed 1
python3 -m unittest discover -s tests -v
```

### Local search solver — A5

- Added `roster/solvers/local_search.py`: simulated annealing over the greedy roster with reassign, swap, fill, drop and shift-change moves.
- Maintains per-student worked days, rule costs, preferences, isolated days and hours, plus per-cell coverage and group spreads, so each move's delta only recomputes the affected students, days, cells and group.
- Starting temperature is calibrated on sampled worsening moves to accept about 30% of them; cooling is geometric, with one reheat after a long stagnation. The best roster seen is always returned.
- `--debug` (or `ROSTER_LOCAL_DEBUG=1`) cross-checks the incremental total against `evaluator.evaluate` every 1000 accepted moves and at the end.
- Added 6 tests in `tests/test_local_search.py`; the full suite passes 78 tests.
- Baseline costs (`results/local_baseline.txt`, seed 1, 60 s): case_01 0.0366 (−98.20%), case_02 127.0555 (−57.51%), case_03 427.0491 (−25.22%), case_04 0.0113 (−60.70%). All four pass the hard rules.

```sh
python3 -m roster.run local test/case_01_balanced_20d --seed 1 --time 60
python3 -m unittest -v tests.test_local_search
```

## Validation follow-ups

- Reject invalid numeric inputs consistently: negative weekly caps and rolling-window lengths currently pass validation, as do `NaN` shift hours.
- Report non-finite horizon values through `CaseError`; `horizon_days=nan` currently raises an uncaught `ValueError`.
- Allow demand above group size so the solver can represent the shortage with unfilled-slot slack and the auditor can report it. The loader currently rejects these inputs, and its corresponding test expects rejection.

## Remaining components

- CP-SAT scheduling solver with configurable penalties and a full-horizon solve target below 90 seconds.
- Independent feasibility and conflict auditor that recomputes results from the final schedule.
- Sick-leave re-optimization with minimal assignment changes and frozen-day constraints.
- Interactive dashboard for schedules, workload, coverage and sick-leave changes.
