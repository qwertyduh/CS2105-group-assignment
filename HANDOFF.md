# HANDOFF.md

Running log for the festival shift-roster optimizer. Update it after every phase, before starting the next prompt.
Attach it with each prompt (see the "Context files" table in the prompt-sequence doc).

Last updated: 2026-10-30

## Project in one paragraph

Roster optimizer for a college festival with four student groups (MPR, CND, Hospitality, SPONS). It takes a case folder of five CSVs and produces a shift roster, an audit report, and a minimal-change repair when a student reports sick. Three solvers are built behind one interface and compared: a greedy builder, local search, and CP-SAT (OR-Tools). One evaluator scores all of them. Full background is in `CONTEXT.md`; the contract is in `INTERFACES.md`.

## Status

| Phase | Prompt | Status | Notes |
| --- | --- | --- | --- |
| Context doc, progress doc, prompt sequence | n/a | Done | Written as docs, no code |
| Case generator | n/a | Done | `generate_case.py`, 4 sample cases generated and validated |
| Load and verify CSVs | A1 | Done | `roster/case_io.py`, `tests/test_case_io.py`; 15 tests pass |
| Rules file | A2 | Not started | |
| Evaluator + base interface | A3 | Not started | Freeze `rules.py` and `evaluator.py` after this |
| Greedy solver | A4 | Not started | |
| Local search | A5 | Not started | |
| Local sick-leave repair | A6 | Not started | |
| CP-SAT group model | B1 | Not started | |
| CP-SAT parallel run | B2 | Not started | |
| CP-SAT sick-leave repair | B3 | Not started | |
| Comparison | C1 | Not started | |
| Stress test | D1 | Not started | |

## Decisions (agreed with the project owner)

- **Horizon:** configurable up to 28 days, default 20.
- **Utilization goal:** meet every shift's headcount exactly, never exceed it, and use as much of each student's weekly cap as demand allows. Using the cap is a tie-breaker, not a reason to overstaff.
- **Hard rules (only two):** one shift per day, and skill matching (a group's demand is filled only by its own members).
- **Everything else is soft and priced** so the solver never fails or returns an empty roster:
  - unfilled headcount slot = 3
  - weekly-cap overwork = 2 for the 1st extra shift, 4 for the 2nd, 6 each for the 3rd onward
  - rest violation = 2, plus 1 for each further consecutive occurrence in the same run (includes the Night-to-next-day-Morning wraparound)
  - each working day beyond 5 in a row = 4
  - preferences, fairness, isolated day off = well below 1 unit, tie-breakers only
- **Penalties stack.** One extra shift can trigger overwork, streak and rest penalties together; that is intended.
- **Rest rule:** default minimum gap 11 h, configurable. With the sample shifts only Night then next-day Morning (10 h) is short; Evening then Morning (15 h) is fine.
- **Night shift ownership:** belongs to the day it starts.
- **Overwork unit:** counted per extra shift above the cap. With uniform 4 h shifts this equals per-hour counting.
- **Optional hard ceiling** on hours per window exists in config, off by default (0).
- **Group independence:** groups never compete for the same students, so each group is an independent subproblem and can be solved in parallel.
- **Sick-leave repair:** freeze days before the sick day D and from D+3 onward. Only D and D+1 may change, only for the sick student's group. Frozen days still count in rolling-window, streak and rest calculations. Objective is coverage first, then fewest changed assignments. If nobody can cover without a worse breach, leave the slot unfilled with a reason.
- **Method:** CP-SAT is the exact engine; a dependency-free greedy plus local search solver is built first as a baseline and fallback. All are compared by the same evaluator.

## Open decisions (settle before the phase that needs them)

| Decision | Needed by | Current default | Notes |
| --- | --- | --- | --- |
| Weekly-cap accounting: charge non-overlapping windows from day 1, or every rolling 7-day window | **A3** | Non-overlapping from day 1 | Stricter and harder to model if every rolling window. The CP-SAT model (B1) must match whichever is chosen |
| Sick-leave freeze boundary: D+3 or D+2 | A6 | D+3 | |
| Hard ceiling hours per window | A3 | Off (0) | Say if you want a hard guarantee, for example 28 h |
| Whether students need hard unavailability (exams) or an on-call flag | A6 | Not modelled | Repair accepts an optional `unavailable_days` list |
| Preference file format | done | CSV (see `INTERFACES.md`) | The schema was designed by the assistant, not given in the brief; change it if the organizers have a different one |
| Dashboard stack | later | FastAPI (Streamlit if speed matters) | Not part of the current prompt sequence |

## What exists now

**`roster/case_io.py`** (tested)
- Loads the five CSVs into a frozen `Case` with typed `Student`, `Shift` and `Pref` records, indexed by roll number, group, shift id and `(day, shift_id, group)`.
- `load_case` collects structural and semantic problems and raises one `CaseError` listing all of them; `CaseError.errors` holds the list.
- Validation covers missing files and wrong headers, duplicate roll numbers and shift ids, unknown groups/shifts/roll numbers, horizon range, complete demand grid, negative or over-capacity demand, preference days and weights, shift hours and midnight crossing.
- CLI `python -m roster.case_io <case_folder>` prints case name, horizon, students per group, total demand slots, capacity slots, load ratio and validation status.
- `tests/test_case_io.py` has 15 unittest cases: all four named sample cases load clean, and each validation rule is exercised by corrupting a copied case.

**`generate_case.py`** (tested)
- Writes a case folder of five CSVs plus `_summary.json`, then re-reads and validates it from disk.
- Scenarios set demand as a share of weekly-hour capacity: balanced 0.85, tight 1.00, overloaded 1.15 (overloaded is deliberately infeasible, to exercise the audit report). `--load` overrides.
- Demand is sized from each group's capacity, with about 15% of days as 25% busier peak days, split across shifts (about 30 / 32 / 38%), and capped at group size.
- About 10% of students get a 12 h or 16 h weekly cap; about 60% have preferences.
- Same seed gives identical files except the timestamp in `_summary.json`.
- `--validate <folder>` checks an existing case. Verified to reject: missing demand rows, unknown group, demand above group size.

**Sample cases in `test/`**

| Case | Days | Students | Demand / capacity | Purpose |
| --- | --- | --- | --- | --- |
| case_01_balanced_20d | 20 | 83 | 0.85 | Normal default case |
| case_02_tight_28d | 28 | 55 | 1.00 | Max horizon, hours fully used |
| case_03_overloaded_20d | 20 | 70 | 1.15 | Not enough capacity; tests the audit report |
| case_04_small_7d | 7 | 67 | 0.851 | Quick horizon debugging case |

Seeds used: 1, 2, 3, 4 respectively. Regenerate with `python3 generate_case.py --seed N --name <name>`.
`test/` also contains `case_tight_28d_seed7` from an earlier generator run; it loads clean too.

## Current signatures

A1 is implemented as specified in `INTERFACES.md` section 4:
- `load_case(folder: str) -> Case`
- `validate_case(case: Case) -> list[str]`
- `CaseError(Exception)` with `.errors: list[str]`

The loaded `Case` also carries loader diagnostics (`folder`, `load_errors`, `structural_ok`, `available`) so validation can report file-level problems. Solver code should use only the documented data fields.

## Phases passed

- Generator: validated, deterministic per seed.
- A1: case loading and validation complete; `python -m unittest` passes 15 tests.

## Known issues and notes

- The prompt sequence charges overwork on non-overlapping windows in the evaluator (A3) but the CP-SAT model must reproduce that exactly. If the choice changes after A3, `evaluator.py` is re-frozen and B1 is rewritten to match.
- The generator's demand uses a fixed share per shift, so it cannot produce shifts with different required hours; the overwork penalty counts shifts, which equals hours only while all shifts are 4 h.
- Preference weights are 1 to 3 in generated data but the validator accepts 1 to 5.
- The A1 loader rejects missing or unknown config keys, matching `INTERFACES.md`; all generated cases contain the full key set.

## Baselines (fill in as phases finish)

| Case | Greedy | Local search | CP-SAT cold | CP-SAT warm |
| --- | --- | --- | --- | --- |
| case_01_balanced_20d | | | | |
| case_02_tight_28d | | | | |
| case_03_overloaded_20d | | | | |
| case_04_small_7d | | | | |

## How to update this file

After each prompt passes: tick the phase in Status, add any decision made, record any signature that changed, paste the cost lines into Baselines, and note anything that surprised you under Known issues. Keep entries short and dated.

## Change log

| Date | Change |
| --- | --- |
| 2026-10-30 | File created. Generator and four sample cases done; all solver phases not started. |
| 2026-10-30 | A1 complete: `roster/case_io.py` loader/validator, CLI summary, `tests/test_case_io.py` (15 tests), four named sample cases generated. |
