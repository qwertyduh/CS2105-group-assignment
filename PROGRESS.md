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

## Validation follow-ups

- Reject invalid numeric inputs consistently: negative weekly caps and rolling-window lengths currently pass validation, as do `NaN` shift hours.
- Report non-finite horizon values through `CaseError`; `horizon_days=nan` currently raises an uncaught `ValueError`.
- Allow demand above group size so the solver can represent the shortage with unfilled-slot slack and the auditor can report it. The loader currently rejects these inputs, and its corresponding test expects rejection.

## Remaining components

- CP-SAT scheduling solver with configurable penalties and a full-horizon solve target below 90 seconds.
- Independent feasibility and conflict auditor that recomputes results from the final schedule.
- Sick-leave re-optimization with minimal assignment changes and frozen-day constraints.
- Interactive dashboard for schedules, workload, coverage and sick-leave changes.
