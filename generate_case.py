#!/usr/bin/env python3
"""Random test-case generator for the festival shift-roster optimizer.

Each case is a folder of five CSV files (the input schema for the solver):

    config.csv        key,value,description          horizon, rules, penalty values, objective weights
    shifts.csv        shift_id,name,start,end,hours,crosses_midnight
    students.csv      roll_no,name,group,max_weekly_hours
    demand.csv        day,shift_id,group,required     long format, every (day, shift, group) present
    preferences.csv   roll_no,type,day,shift_id,weight
                      type = OFF_DAY (day set) | PREFER_SHIFT | AVOID_SHIFT (shift_id set, day blank = any day)

plus _summary.json (how the case was generated and its load ratio; not part of the schema).

Examples
    python3 generate_case.py                                  # balanced, 20 days, random seed
    python3 generate_case.py --scenario tight --days 28 --seed 7
    python3 generate_case.py --scenario overloaded --name stress_01
    python3 generate_case.py --sizes MPR=20,CND=15,Hospitality=24,SPONS=12
    python3 generate_case.py --validate test/some_case        # check an existing case folder

Scenarios set how much of the groups' weekly-hour capacity the demand asks for:
    balanced 0.85   tight 1.00   overloaded 1.15 (deliberately infeasible: exercises the audit report)
"""
import argparse
import csv
import json
import random
import re
import sys
from datetime import datetime
from pathlib import Path

GROUPS = ["MPR", "CND", "Hospitality", "SPONS"]
MAX_DAYS = 28
DEFAULT_DAYS = 20
DEFAULT_WEEKLY_CAP = 20

# weight = share of a group's daily demand that lands on this shift
SHIFTS = [
    {"shift_id": "M", "name": "Morning", "start": "11:00", "end": "15:00", "weight": 0.30},
    {"shift_id": "E", "name": "Evening", "start": "16:00", "end": "20:00", "weight": 0.32},
    {"shift_id": "N", "name": "Night", "start": "21:00", "end": "01:00", "weight": 0.38},
]

SCENARIOS = {"balanced": 0.85, "tight": 1.00, "overloaded": 1.15}

PREF_TYPES = ["OFF_DAY", "PREFER_SHIFT", "AVOID_SHIFT"]

CONFIG_HEADER = ["key", "value", "description"]
SHIFTS_HEADER = ["shift_id", "name", "start", "end", "hours", "crosses_midnight"]
STUDENTS_HEADER = ["roll_no", "name", "group", "max_weekly_hours"]
DEMAND_HEADER = ["day", "shift_id", "group", "required"]
PREFS_HEADER = ["roll_no", "type", "day", "shift_id", "weight"]

# (key, value, description). horizon_days is filled in per case.
CONFIG_DEFAULTS = [
    ("horizon_days", None, "Planning horizon in days (1 to 28)"),
    ("weekly_cap_default_hours", 20, "Default max hours in any rolling window; per-student value in students.csv wins"),
    ("rolling_window_days", 7, "Length of the rolling window for the weekly cap"),
    ("max_consecutive_days", 5, "Working days in a row allowed before the streak penalty applies"),
    ("min_rest_hours", 11, "Minimum gap between consecutive shifts; shorter gaps get the rest penalty"),
    ("hard_ceiling_hours_per_window", 0, "Absolute hard cap per rolling window; 0 = off"),
    ("penalty_unfilled_slot", 3, "Per missing headcount slot"),
    ("penalty_overwork_tier1", 2, "1st extra shift above the weekly cap in a window"),
    ("penalty_overwork_tier2", 4, "2nd extra shift above the cap"),
    ("penalty_overwork_tier3_plus", 6, "3rd and further extra shifts, each"),
    ("penalty_rest_violation", 2, "Base penalty per short-rest back-to-back pair"),
    ("penalty_rest_chain_increment", 1, "Added for each further consecutive rest violation in a run"),
    ("penalty_streak_day", 4, "Per working day beyond max_consecutive_days"),
    ("weight_preference", 0.05, "Soft term weight: shift and day-off preferences"),
    ("weight_fairness", 0.05, "Soft term weight: hours spread within a group"),
    ("weight_isolated_day_off", 0.05, "Soft term weight: Work-Off-Work pattern"),
]
CONFIG_KEYS = [k for k, _, _ in CONFIG_DEFAULTS]

FIRST_NAMES = [
    "Aarav", "Aditi", "Aditya", "Akash", "Ananya", "Anjali", "Arjun", "Ayesha", "Bhavya", "Chirag",
    "Deepak", "Diya", "Farhan", "Gauri", "Harsh", "Isha", "Ishaan", "Jatin", "Kavya", "Kunal",
    "Lakshya", "Meera", "Mohit", "Naina", "Neha", "Nikhil", "Pooja", "Pranav", "Priya", "Rahul",
    "Riya", "Rohan", "Sakshi", "Sameer", "Sanya", "Shreya", "Siddharth", "Tanvi", "Utkarsh", "Vaishnavi",
]
LAST_NAMES = [
    "Agarwal", "Bansal", "Chauhan", "Das", "Dubey", "Gupta", "Iyer", "Jain", "Kapoor", "Khan",
    "Kumar", "Mehta", "Mishra", "Nair", "Pandey", "Patel", "Rao", "Reddy", "Roy", "Saxena",
    "Sharma", "Singh", "Sinha", "Thakur", "Tiwari", "Verma", "Yadav", "Zaidi", "Menon", "Joshi",
]
DEPTS = ["CS", "EE", "ME", "CE", "EC", "CB"]


# ---------------------------------------------------------------- helpers
def to_minutes(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def shift_hours(start, end):
    s, e = to_minutes(start), to_minutes(end)
    if e <= s:
        e += 24 * 60
    return (e - s) / 60


def split_total(total, weights):
    """Split an integer total across weights using largest remainder."""
    wsum = sum(weights)
    raw = [total * w / wsum for w in weights]
    base = [int(x) for x in raw]
    rest = total - sum(base)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - base[i], reverse=True)
    for i in order[:rest]:
        base[i] += 1
    return base


def normalize_mean(values):
    mean = sum(values) / len(values)
    return [v / mean for v in values]


def fmt_num(x):
    return str(int(x)) if float(x) == int(x) else str(x)


# ---------------------------------------------------------------- generation
def make_shifts():
    rows = []
    for s in SHIFTS:
        hours = shift_hours(s["start"], s["end"])
        rows.append({
            "shift_id": s["shift_id"], "name": s["name"], "start": s["start"], "end": s["end"],
            "hours": fmt_num(hours),
            "crosses_midnight": "yes" if to_minutes(s["end"]) <= to_minutes(s["start"]) else "no",
        })
    return rows


def make_students(rng, sizes, varied_caps):
    students, used_names, seq = [], set(), 1
    for group in GROUPS:
        for _ in range(sizes[group]):
            while True:
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                if name not in used_names:
                    used_names.add(name)
                    break
            cap = rng.choice([12, 16]) if rng.random() < varied_caps else DEFAULT_WEEKLY_CAP
            roll = f"{rng.choice([22, 23, 24, 25])}{rng.choice(DEPTS)}{seq:02d}"
            students.append({"roll_no": roll, "name": name, "group": group, "max_weekly_hours": cap})
            seq += 1
    return students


def make_demand(rng, days, students, shifts, load, peak_frac):
    mean_hours = sum(float(s["hours"]) for s in shifts) / len(shifts)
    weights = [s["weight"] for s in SHIFTS]
    shift_ids = [s["shift_id"] for s in shifts]

    # shared festival rhythm: a few peak days, scaled so the average stays 1
    peak = [1.0] * days
    n_peak = round(peak_frac * days)
    for d in rng.sample(range(days), min(n_peak, days)):
        peak[d] = 1.25

    rows = []
    for group in GROUPS:
        members = [s for s in students if s["group"] == group]
        size = len(members)
        cap_per_day = sum(m["max_weekly_hours"] / mean_hours for m in members) / 7
        factors = normalize_mean([peak[d] * rng.uniform(0.9, 1.1) for d in range(days)])

        cum, prev = 0.0, 0
        for d in range(days):
            cum += load * cap_per_day * factors[d]
            cur = round(cum)
            total, prev = cur - prev, cur
            day_w = [w * rng.uniform(0.85, 1.15) for w in weights]
            counts = split_total(total, day_w)
            for sid, n in zip(shift_ids, counts):
                rows.append({"day": d + 1, "shift_id": sid, "group": group, "required": min(n, size)})
    return rows


def make_prefs(rng, days, students, frac):
    shift_ids = [s["shift_id"] for s in SHIFTS]
    rows = []
    for st in students:
        if rng.random() >= frac:
            continue
        local = []
        for d in rng.sample(range(1, days + 1), min(rng.choice([0, 1, 1, 2]), days)):
            local.append({"roll_no": st["roll_no"], "type": "OFF_DAY", "day": d, "shift_id": "", "weight": rng.randint(1, 3)})
        liked = None
        if rng.random() < 0.6:
            liked = rng.choice(shift_ids)
            local.append({"roll_no": st["roll_no"], "type": "PREFER_SHIFT", "day": "", "shift_id": liked, "weight": rng.randint(1, 3)})
        if rng.random() < 0.5:
            disliked = rng.choice([s for s in shift_ids if s != liked])
            local.append({"roll_no": st["roll_no"], "type": "AVOID_SHIFT", "day": "", "shift_id": disliked, "weight": rng.randint(1, 3)})
        if not local:
            local.append({"roll_no": st["roll_no"], "type": "PREFER_SHIFT", "day": "", "shift_id": rng.choice(shift_ids), "weight": rng.randint(1, 3)})
        rows.extend(local)
    return rows


# ---------------------------------------------------------------- io
def write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        w.writerows(rows)


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        return r.fieldnames, list(r)


# ---------------------------------------------------------------- validation
def validate_case(case_dir):
    """Re-read the CSVs from disk and check them against the schema. Returns a list of error strings."""
    case_dir = Path(case_dir)
    errors = []

    def load(name, header):
        p = case_dir / name
        if not p.exists():
            errors.append(f"{name}: file missing")
            return []
        fields, rows = read_csv(p)
        if fields != header:
            errors.append(f"{name}: header is {fields}, expected {header}")
            return []
        return rows

    config = load("config.csv", CONFIG_HEADER)
    shifts = load("shifts.csv", SHIFTS_HEADER)
    students = load("students.csv", STUDENTS_HEADER)
    demand = load("demand.csv", DEMAND_HEADER)
    prefs = load("preferences.csv", PREFS_HEADER)
    if errors:
        return errors

    # config
    cfg = {r["key"]: r["value"] for r in config}
    for k in CONFIG_KEYS:
        if k not in cfg:
            errors.append(f"config.csv: missing key {k}")
        else:
            try:
                float(cfg[k])
            except ValueError:
                errors.append(f"config.csv: {k} is not numeric ({cfg[k]!r})")
    if errors:
        return errors
    horizon = int(float(cfg["horizon_days"]))
    if not 1 <= horizon <= MAX_DAYS:
        errors.append(f"config.csv: horizon_days {horizon} outside 1..{MAX_DAYS}")

    # shifts
    shift_ids = set()
    time_re = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    for r in shifts:
        if r["shift_id"] in shift_ids:
            errors.append(f"shifts.csv: duplicate shift_id {r['shift_id']}")
        shift_ids.add(r["shift_id"])
        if not (time_re.match(r["start"]) and time_re.match(r["end"])):
            errors.append(f"shifts.csv: bad time for {r['shift_id']}")
            continue
        if abs(shift_hours(r["start"], r["end"]) - float(r["hours"])) > 1e-9:
            errors.append(f"shifts.csv: hours mismatch for {r['shift_id']}")
        crosses = to_minutes(r["end"]) <= to_minutes(r["start"])
        if (r["crosses_midnight"] == "yes") != crosses:
            errors.append(f"shifts.csv: crosses_midnight wrong for {r['shift_id']}")
    if not shift_ids:
        errors.append("shifts.csv: no shifts")

    # students
    rolls, group_size = set(), {g: 0 for g in GROUPS}
    for r in students:
        if r["roll_no"] in rolls:
            errors.append(f"students.csv: duplicate roll_no {r['roll_no']}")
        rolls.add(r["roll_no"])
        if r["group"] not in GROUPS:
            errors.append(f"students.csv: {r['roll_no']} has unknown group {r['group']!r}")
        else:
            group_size[r["group"]] += 1
        try:
            if float(r["max_weekly_hours"]) <= 0:
                raise ValueError
        except ValueError:
            errors.append(f"students.csv: {r['roll_no']} has bad max_weekly_hours {r['max_weekly_hours']!r}")
        if not r["name"].strip():
            errors.append(f"students.csv: {r['roll_no']} has empty name")

    # demand: complete grid, no duplicates, within group size
    seen = set()
    for r in demand:
        try:
            day, req = int(r["day"]), int(r["required"])
        except ValueError:
            errors.append(f"demand.csv: non-integer value in row {r}")
            continue
        key = (day, r["shift_id"], r["group"])
        if key in seen:
            errors.append(f"demand.csv: duplicate row {key}")
        seen.add(key)
        if not 1 <= day <= horizon:
            errors.append(f"demand.csv: day {day} outside 1..{horizon}")
        if r["shift_id"] not in shift_ids:
            errors.append(f"demand.csv: unknown shift_id {r['shift_id']!r}")
        if r["group"] not in GROUPS:
            errors.append(f"demand.csv: unknown group {r['group']!r}")
        elif req < 0:
            errors.append(f"demand.csv: negative required at {key}")
        elif req > group_size[r["group"]]:
            errors.append(f"demand.csv: {key} requires {req}, group has only {group_size[r['group']]} students")
    expected = {(d, s, g) for d in range(1, horizon + 1) for s in shift_ids for g in GROUPS}
    missing = expected - seen
    if missing:
        errors.append(f"demand.csv: {len(missing)} (day, shift, group) combinations missing")

    # preferences
    for r in prefs:
        if r["roll_no"] not in rolls:
            errors.append(f"preferences.csv: unknown roll_no {r['roll_no']!r}")
        if r["type"] not in PREF_TYPES:
            errors.append(f"preferences.csv: unknown type {r['type']!r}")
            continue
        if r["type"] == "OFF_DAY":
            try:
                if not 1 <= int(r["day"]) <= horizon:
                    errors.append(f"preferences.csv: OFF_DAY day {r['day']} outside 1..{horizon}")
            except ValueError:
                errors.append(f"preferences.csv: OFF_DAY for {r['roll_no']} needs an integer day")
        else:
            if r["shift_id"] not in shift_ids:
                errors.append(f"preferences.csv: {r['type']} for {r['roll_no']} has unknown shift {r['shift_id']!r}")
        try:
            if not 1 <= int(r["weight"]) <= 5:
                errors.append(f"preferences.csv: weight {r['weight']} outside 1..5")
        except ValueError:
            errors.append(f"preferences.csv: non-integer weight {r['weight']!r}")
    return errors


# ---------------------------------------------------------------- main
def parse_sizes(text, rng):
    sizes = {g: rng.randint(12, 24) for g in GROUPS}
    if text:
        for part in text.split(","):
            g, n = part.split("=")
            g = g.strip()
            if g not in GROUPS:
                raise SystemExit(f"--sizes: unknown group {g!r}; use {GROUPS}")
            sizes[g] = int(n)
    return sizes


def build_case(args):
    seed = args.seed if args.seed is not None else random.SystemRandom().randrange(1, 10**6)
    rng = random.Random(seed)
    load = args.load if args.load is not None else SCENARIOS[args.scenario]
    scenario = args.scenario if args.load is None else f"custom{load}"
    name = args.name or f"case_{scenario}_{args.days}d_seed{seed}"
    case_dir = Path(args.out) / name
    case_dir.mkdir(parents=True, exist_ok=True)

    sizes = parse_sizes(args.sizes, rng)
    shifts = make_shifts()
    students = make_students(rng, sizes, args.varied_caps)
    demand = make_demand(rng, args.days, students, shifts, load, args.peak_frac)
    prefs = make_prefs(rng, args.days, students, args.pref_fraction)

    config = [{"key": k, "value": args.days if k == "horizon_days" else v, "description": d}
              for k, v, d in CONFIG_DEFAULTS]
    write_csv(case_dir / "config.csv", CONFIG_HEADER, config)
    write_csv(case_dir / "shifts.csv", SHIFTS_HEADER, shifts)
    write_csv(case_dir / "students.csv", STUDENTS_HEADER, students)
    write_csv(case_dir / "demand.csv", DEMAND_HEADER, demand)
    write_csv(case_dir / "preferences.csv", PREFS_HEADER, prefs)

    mean_hours = sum(float(s["hours"]) for s in shifts) / len(shifts)
    per_group = {}
    for g in GROUPS:
        members = [s for s in students if s["group"] == g]
        cap_slots = sum(m["max_weekly_hours"] / mean_hours for m in members) / 7 * args.days
        need = sum(r["required"] for r in demand if r["group"] == g)
        per_group[g] = {"students": len(members), "demand_slots": need,
                        "capacity_slots": round(cap_slots, 1), "load_ratio": round(need / cap_slots, 3)}
    total_need = sum(v["demand_slots"] for v in per_group.values())
    total_cap = sum(v["capacity_slots"] for v in per_group.values())
    summary = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "seed": seed, "scenario": scenario, "target_load": load, "horizon_days": args.days,
        "students": len(students), "preferences": len(prefs),
        "demand_slots": total_need, "capacity_slots": round(total_cap, 1),
        "load_ratio": round(total_need / total_cap, 3), "per_group": per_group,
    }
    (case_dir / "_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return case_dir, summary


def main():
    p = argparse.ArgumentParser(description="Random test-case generator for the shift-roster optimizer.")
    p.add_argument("--out", default="test", help="base output folder (default: test)")
    p.add_argument("--name", help="case folder name (default: derived from scenario, days, seed)")
    p.add_argument("--seed", type=int, help="random seed (default: random, recorded in _summary.json)")
    p.add_argument("--days", type=int, default=DEFAULT_DAYS, help=f"horizon in days, 1 to {MAX_DAYS} (default {DEFAULT_DAYS})")
    p.add_argument("--scenario", choices=SCENARIOS, default="balanced", help="demand level preset")
    p.add_argument("--load", type=float, help="demand as a fraction of weekly-hour capacity; overrides --scenario")
    p.add_argument("--sizes", help="group sizes, e.g. MPR=20,CND=15,Hospitality=24,SPONS=12 (default: random 12 to 24 each)")
    p.add_argument("--peak-frac", type=float, default=0.15, help="share of days that are 25%% busier peak days (default 0.15)")
    p.add_argument("--pref-fraction", type=float, default=0.6, help="share of students with preferences (default 0.6)")
    p.add_argument("--varied-caps", type=float, default=0.10, help="share of students with a 12 h or 16 h cap instead of 20 h (default 0.10)")
    p.add_argument("--validate", metavar="DIR", help="only validate an existing case folder")
    args = p.parse_args()

    if args.validate:
        errs = validate_case(args.validate)
        print("VALID" if not errs else "\n".join(errs[:50]))
        sys.exit(1 if errs else 0)

    if not 1 <= args.days <= MAX_DAYS:
        p.error(f"--days must be between 1 and {MAX_DAYS}")
    if args.load is not None and args.load <= 0:
        p.error("--load must be positive")

    case_dir, summary = build_case(args)
    errs = validate_case(case_dir)
    if errs:
        print("Generated case failed validation:")
        print("\n".join(errs[:50]))
        sys.exit(1)
    print(f"{case_dir}  seed={summary['seed']}  {summary['horizon_days']} days  "
          f"{summary['students']} students  demand/capacity={summary['load_ratio']}  validated OK")


if __name__ == "__main__":
    main()
