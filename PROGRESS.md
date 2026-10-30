# Festival Shift Roster Optimizer Progress

Updated: 30 October 2026

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

## Validation follow-ups

- Reject invalid numeric inputs consistently: negative weekly caps and rolling-window lengths currently pass validation, as do `NaN` shift hours.
- Report non-finite horizon values through `CaseError`; `horizon_days=nan` currently raises an uncaught `ValueError`.
- Allow demand above group size so the solver can represent the shortage with unfilled-slot slack and the auditor can report it. The loader currently rejects these inputs, and its corresponding test expects rejection.

## Remaining components

- CP-SAT scheduling solver with configurable penalties and a full-horizon solve target below 90 seconds.
- Independent feasibility and conflict auditor that recomputes results from the final schedule.
- Sick-leave re-optimization with minimal assignment changes and frozen-day constraints.
- Interactive dashboard for schedules, workload, coverage and sick-leave changes.
