# INTERFACES.md

The contract every module and every prompt in the build sequence relies on.
If something here conflicts with a prompt, this file wins. Change it only deliberately, and record the change in HANDOFF.md.

## 1. Conventions

- Python 3.10 or later. Parts A and C use the standard library only. Only the CP-SAT part (B) may install `ortools`.
- Days are **1-based** (`1..horizon_days`, horizon 1 to 28, default 20).
- Shift ids are short strings. The default set is `M` (Morning 11:00-15:00), `E` (Evening 16:00-20:00), `N` (Night 21:00-01:00). All are 4 h.
- A shift that crosses midnight **belongs to the day it starts**. Night on day d counts toward day d for the one-shift-per-day rule and for weekly hours. It ends on calendar day d+1.
- Groups are exactly `MPR`, `CND`, `Hospitality`, `SPONS`.
- Roll numbers are unique strings (for example `25CE001`). All joins use `roll_no`.
- Penalty costs are plain numbers in "units" (see section 5). CP-SAT scales them by 100 internally and divides back when reporting.
- Determinism: every solver takes a `seed`. Same case + same seed + same time limit gives the same roster, except where multi-threaded CP-SAT is involved.

## 2. Project layout

```text
roster/
  case_io.py          load and validate a case folder            (A1)
  rules.py            typed rules, penalty values, helpers        (A2)
  evaluator.py        the single scoring function + audit report  (A3)
  run.py              CLI to run any solver or repair             (A4 onward)
  solvers/
    base.py           Roster type + solve() signature             (A3)
    greedy.py                                                     (A4)
    local_search.py                                               (A5)
    cpsat.py                                                      (B1, B2)
  repair/
    local_repair.py                                               (A6)
    cpsat_repair.py                                               (B3)
  compare.py          stage-by-stage comparison                   (C1)
  stress.py           random-case benchmark                       (D1)
tests/
  test_case_io.py     one test file per prompt                    (A1)
generate_case.py      random case generator (already built)
test/                 generated cases (already built)
results/              outputs of run.py, compare.py, stress.py
```

## 3. Case folder (input) schema

A case is a folder of five CSV files. Header rows are exact and in this order. UTF-8, comma separated.

### config.csv
`key,value,description`

Required keys (all numeric). Unknown keys are an error.

| key | default | meaning |
| --- | --- | --- |
| horizon_days | 20 | planning horizon, 1 to 28 |
| weekly_cap_default_hours | 20 | default cap per rolling window; per-student value in students.csv wins |
| rolling_window_days | 7 | rolling window length |
| max_consecutive_days | 5 | working days in a row before the streak penalty applies |
| min_rest_hours | 11 | gaps shorter than this between shifts get the rest penalty |
| hard_ceiling_hours_per_window | 0 | absolute hard cap per window, 0 = off |
| penalty_unfilled_slot | 3 | per missing headcount slot |
| penalty_overwork_tier1 | 2 | 1st extra shift above the cap in a window |
| penalty_overwork_tier2 | 4 | 2nd extra shift above the cap |
| penalty_overwork_tier3_plus | 6 | 3rd and further extra shifts, each |
| penalty_rest_violation | 2 | base penalty per short-rest back-to-back pair |
| penalty_rest_chain_increment | 1 | added for each further consecutive rest violation in a run |
| penalty_streak_day | 4 | per working day beyond max_consecutive_days |
| weight_preference | 0.05 | soft term: preferences |
| weight_fairness | 0.05 | soft term: hours spread within a group |
| weight_isolated_day_off | 0.05 | soft term: Work-Off-Work pattern |

### shifts.csv
`shift_id,name,start,end,hours,crosses_midnight`

`start` and `end` are `HH:MM` 24-hour. `hours` is derived (end minus start, adding 24 h if the shift crosses midnight). `crosses_midnight` is `yes` or `no`.

### students.csv
`roll_no,name,group,max_weekly_hours`

`max_weekly_hours` is the cap for any rolling window of `rolling_window_days` days. Default 20; the generator gives about 10% of students 12 or 16.

### demand.csv
`day,shift_id,group,required`

Long format. **Complete grid**: every (day, shift_id, group) appears exactly once. `required` is an integer from 0 to the group's size. Demand is met exactly, never exceeded.

### preferences.csv
`roll_no,type,day,shift_id,weight`

| type | day | shift_id | meaning |
| --- | --- | --- | --- |
| OFF_DAY | required | blank | student asks to be off that day |
| PREFER_SHIFT | blank (any day) | required | student likes that shift |
| AVOID_SHIFT | blank (any day) | required | student dislikes that shift |

`weight` is an integer 1 to 5 (the generator uses 1 to 3). A student may have zero rows.

### _summary.json
Written by the generator only. Not part of the schema; the solver must ignore it. Holds seed, scenario, load ratio and per-group capacity versus demand.

## 4. Python interfaces

```python
# case_io.py
def load_case(folder: str) -> "Case"            # raises CaseError listing every error
def validate_case(case: "Case") -> list[str]    # human-readable errors, empty = valid
class CaseError(Exception):
    errors: list[str]                           # every message; str(exc) joins them

# Case is a frozen dataclass:
#   horizon_days: int
#   students: dict[str, Student]                # by roll_no (name, group, max_weekly_hours)
#   by_group: dict[str, list[str]]              # group -> roll_nos, stable order
#   shifts: dict[str, Shift]                    # by shift_id (name, start, end, hours, crosses_midnight)
#   shift_order: list[str]                      # as in shifts.csv
#   demand: dict[tuple[int, str, str], int]     # (day, shift_id, group) -> required
#   prefs: dict[str, list[Pref]]                # by roll_no (type, day|None, shift_id|None, weight)
#   config: dict[str, float]
#   folder: str                                 # loader diagnostics only
#   load_errors: tuple[str, ...]                # loader diagnostics only
#   structural_ok: bool                         # loader diagnostics only
#   available: frozenset[str]                   # files that loaded cleanly
# Solver/evaluator code should use only the documented data fields above.

# rules.py
@dataclass(frozen=True)
class Rules: ...                                # fields = config keys above
Rules.from_case(case) -> Rules                  # config overrides defaults; unknown keys are an error
def rest_gap_hours(shift_a, shift_b, day_gap) -> float
def short_rest(shift_a, shift_b) -> bool        # a on day d, b on day d+1, gap < min_rest_hours
def overwork_penalty(extra_shifts: int) -> float          # cumulative: 1->2, 2->6, 3->12, 4->18
def rest_chain_penalty(run_length: int) -> float          # cumulative: base + increment*(k-1) per kth

# solvers/base.py
Roster = dict[tuple[str, int], str]             # (roll_no, day) -> shift_id, worked days only

def solve(case: Case, time_limit_s: float, seed: int) -> Roster: ...

# repair/*.py
def repair(case: Case, roster: Roster, sick_roll_no: str, sick_day: int,
           shift_id: str, time_limit_s: float, seed: int,
           unavailable_days: list[int] | None = None) -> RepairResult
# RepairResult: roster: Roster, diff: list[(roll_no, day, old_shift|None, new_shift|None)],
#               reason: str | None   (set when a slot stays unfilled)

# evaluator.py
def evaluate(case: Case, rules: Rules, roster: Roster) -> Report
Report.to_json() -> dict                        # keys in section 5
```

A roster file is `roster.csv` with columns `roll_no,day,shift_id`, one row per worked day.

## 5. Scoring (evaluator is the only scorer)

No solver computes its own cost. Every comparison uses `evaluator.evaluate`.

**Hard (never relaxed):** one shift per day (guaranteed by the Roster type), skill matching (a student only fills their own group's demand), demand never exceeded in a (day, shift, group) cell. Any breach makes `hard_pass` false.

**Priced rule breaches (units):**

| Event | Cost |
| --- | --- |
| Unfilled headcount slot | 3 |
| 1st extra shift above weekly cap in a window | 2 |
| 2nd extra shift | 4 |
| 3rd and further extra shifts, each | 6 |
| Rest violation (base) | 2, plus 1 for each further consecutive occurrence in the same run |
| Each working day beyond 5 in a row | 4 |

**Soft quality terms:** preference satisfaction, hours spread within a group (max minus min), isolated day off (Work-Off-Work). Each is normalized and weighted so that its total effect stays well under 1 unit and can only break ties between rosters with equal rule breaches.

**Report keys (`to_json`):**

```text
hard_pass                       bool
hard_breaches                   list of strings
unfilled_total                  int
unfilled_by_group               {group: int}
cost_components                 {unfilled, overwork, streak, rest, preference, fairness, isolated}
total_cost                      float
preference_satisfaction_pct     float
workload_spread_by_group        {group: float}   (max minus min hours)
isolated_dayoffs                int
per_student_hours               {roll_no: float}
```

The weekly-overwork accounting rule (charge non-overlapping windows from day 1, or every rolling window) is defined in `HANDOFF.md` under Decisions. The evaluator and the CP-SAT model must use the same rule.

## 6. CLI

```text
python -m roster.case_io <case_folder>                           # summary + validation
python -m roster.rules                                           # print rule table
python -m roster.evaluator <case_folder> <roster.csv>            # audit JSON
python -m roster.run greedy|local|cpsat <case_folder> --seed N [--time S]
python -m roster.run repair <case_folder> <roster.csv> --sick <roll_no> --day D
python -m roster.compare --time 60 --seed 1
python -m roster.stress run --n 100 --time 60 --workers 4
python -m roster.stress report
python generate_case.py [--scenario balanced|tight|overloaded] [--days N] [--seed N]
                        [--sizes MPR=..,CND=..,Hospitality=..,SPONS=..] [--validate <folder>]
```

`python -m roster.case_io <case_folder>` prints the case name, horizon, student counts per group, total demand slots, capacity slots, load ratio and validation status. For an invalid case it prints every error and exits non-zero.

## 7. Output file names

```text
results/<case>_<solver>.roster.csv       roster
results/<case>_<solver>.report.json      evaluator output
results/greedy_baseline.txt              A4
results/local_baseline.txt               A5
results/cpsat_baseline.txt               B2
results/repair_compare.csv               B3
results/compare.csv, compare.md          C1
results/stress/{cases/, manifest.csv, results.csv, repairs.csv, summary.md}   D1
```

## 8. Rules for changing this file

- `rules.py` and `evaluator.py` are frozen after A3. Any change is made by hand, logged in `HANDOFF.md`, and all earlier tests are re-run.
- A change to a signature in section 4 means updating this file first, then every module that uses it.
