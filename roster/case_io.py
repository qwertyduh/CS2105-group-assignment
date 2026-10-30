"""Loading and validation for festival shift-roster case folders."""

from __future__ import annotations

import csv
import re
import sys
from dataclasses import dataclass
from pathlib import Path


GROUPS = ("MPR", "CND", "Hospitality", "SPONS")
MAX_DAYS = 28

CONFIG_HEADER = ["key", "value", "description"]
SHIFTS_HEADER = ["shift_id", "name", "start", "end", "hours", "crosses_midnight"]
STUDENTS_HEADER = ["roll_no", "name", "group", "max_weekly_hours"]
DEMAND_HEADER = ["day", "shift_id", "group", "required"]
PREFS_HEADER = ["roll_no", "type", "day", "shift_id", "weight"]

CONFIG_KEYS = (
    "horizon_days",
    "weekly_cap_default_hours",
    "rolling_window_days",
    "max_consecutive_days",
    "min_rest_hours",
    "hard_ceiling_hours_per_window",
    "penalty_unfilled_slot",
    "penalty_overwork_tier1",
    "penalty_overwork_tier2",
    "penalty_overwork_tier3_plus",
    "penalty_rest_violation",
    "penalty_rest_chain_increment",
    "penalty_streak_day",
    "weight_preference",
    "weight_fairness",
    "weight_isolated_day_off",
)
PREF_TYPES = ("OFF_DAY", "PREFER_SHIFT", "AVOID_SHIFT")

CASE_FILES = (
    "config.csv",
    "shifts.csv",
    "students.csv",
    "demand.csv",
    "preferences.csv",
)

_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CaseError(Exception):
    """Raised when a case folder cannot be loaded without errors."""

    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


@dataclass(frozen=True)
class Student:
    roll_no: str
    name: str
    group: str
    max_weekly_hours: float


@dataclass(frozen=True)
class Shift:
    shift_id: str
    name: str
    start: str
    end: str
    hours: float
    crosses_midnight: bool


@dataclass(frozen=True)
class Pref:
    type: str
    day: int | None
    shift_id: str | None
    weight: int


@dataclass(frozen=True)
class Case:
    horizon_days: int
    students: dict[str, Student]
    by_group: dict[str, list[str]]
    shifts: dict[str, Shift]
    shift_order: list[str]
    demand: dict[tuple[int, str, str], int]
    prefs: dict[str, list[Pref]]
    config: dict[str, float]
    folder: str = ""
    load_errors: tuple[str, ...] = ()
    structural_ok: bool = True
    available: frozenset[str] = frozenset(CASE_FILES)


def _to_minutes(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _shift_hours(start: str, end: str) -> tuple[float, bool]:
    start_minutes = _to_minutes(start)
    end_minutes = _to_minutes(end)
    crosses_midnight = end_minutes <= start_minutes
    if crosses_midnight:
        end_minutes += 24 * 60
    return (end_minutes - start_minutes) / 60, crosses_midnight


def _read_csv(path: Path, expected_header: list[str], errors: list[str]):
    filename = path.name
    if not path.is_file():
        errors.append(f"{filename}: file missing")
        return [], False
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.reader(handle)
            try:
                actual_header = next(reader)
            except StopIteration:
                actual_header = []
            if actual_header != expected_header:
                errors.append(
                    f"{filename}: wrong header; header is {actual_header}, "
                    f"expected {expected_header}"
                )
                return [], False
            rows = [dict(zip(expected_header, row)) for row in reader]
    except OSError as exc:
        errors.append(f"{filename}: cannot read file ({exc})")
        return [], False
    return rows, True


def _parse_float(value, label: str, errors: list[str]):
    try:
        return float(value)
    except (TypeError, ValueError):
        errors.append(f"{label}: not numeric ({value!r})")
        return None


def _parse_int(value, label: str, errors: list[str]):
    try:
        return int(value)
    except (TypeError, ValueError):
        errors.append(f"{label}: not an integer ({value!r})")
        return None


def _load_case(folder: Path):
    errors: list[str] = []

    config_rows, config_ok = _read_csv(folder / "config.csv", CONFIG_HEADER, errors)
    shift_rows, shifts_ok = _read_csv(folder / "shifts.csv", SHIFTS_HEADER, errors)
    student_rows, students_ok = _read_csv(folder / "students.csv", STUDENTS_HEADER, errors)
    demand_rows, demand_ok = _read_csv(folder / "demand.csv", DEMAND_HEADER, errors)
    pref_rows, prefs_ok = _read_csv(folder / "preferences.csv", PREFS_HEADER, errors)
    structural_ok = all((config_ok, shifts_ok, students_ok, demand_ok, prefs_ok))
    available = frozenset(
        filename
        for filename, ok in zip(
            CASE_FILES, (config_ok, shifts_ok, students_ok, demand_ok, prefs_ok)
        )
        if ok
    )

    config: dict[str, float] = {}
    seen_config: set[str] = set()
    for row in config_rows:
        key = (row.get("key") or "").strip()
        if key in seen_config:
            errors.append(f"config.csv: duplicate key {key!r}")
            continue
        seen_config.add(key)
        if key not in CONFIG_KEYS:
            errors.append(f"config.csv: unknown key {key!r}")
            continue
        number = _parse_float(row.get("value"), f"config.csv: {key}", errors)
        if number is not None:
            config[key] = number
    if config_ok:
        for key in CONFIG_KEYS:
            if key not in seen_config:
                errors.append(f"config.csv: missing key {key}")

    horizon_days = int(config["horizon_days"]) if "horizon_days" in config else 0

    shifts: dict[str, Shift] = {}
    shift_order: list[str] = []
    for row in shift_rows:
        shift_id = (row.get("shift_id") or "").strip()
        if shift_id in shifts:
            errors.append(f"shifts.csv: duplicate shift_id {shift_id!r}")
            continue
        hours = _parse_float(row.get("hours"), f"shifts.csv: {shift_id} hours", errors)
        crosses = (row.get("crosses_midnight") or "").strip().lower() == "yes"
        shifts[shift_id] = Shift(
            shift_id=shift_id,
            name=(row.get("name") or "").strip(),
            start=(row.get("start") or "").strip(),
            end=(row.get("end") or "").strip(),
            hours=hours if hours is not None else 0.0,
            crosses_midnight=crosses,
        )
        shift_order.append(shift_id)

    students: dict[str, Student] = {}
    by_group: dict[str, list[str]] = {group: [] for group in GROUPS}
    for row in student_rows:
        roll_no = (row.get("roll_no") or "").strip()
        if roll_no in students:
            errors.append(f"students.csv: duplicate roll_no {roll_no!r}")
            continue
        max_hours = _parse_float(
            row.get("max_weekly_hours"),
            f"students.csv: {roll_no} max_weekly_hours",
            errors,
        )
        group = (row.get("group") or "").strip()
        students[roll_no] = Student(
            roll_no=roll_no,
            name=(row.get("name") or "").strip(),
            group=group,
            max_weekly_hours=max_hours if max_hours is not None else 0.0,
        )
        by_group.setdefault(group, []).append(roll_no)

    demand: dict[tuple[int, str, str], int] = {}
    for row in demand_rows:
        day = _parse_int(row.get("day"), "demand.csv: day", errors)
        required = _parse_int(row.get("required"), "demand.csv: required", errors)
        if day is None or required is None:
            continue
        shift_id = (row.get("shift_id") or "").strip()
        group = (row.get("group") or "").strip()
        key = (day, shift_id, group)
        if key in demand:
            errors.append(
                f"demand.csv: duplicate row (day={day}, shift_id={shift_id!r}, group={group!r})"
            )
            continue
        demand[key] = required

    prefs: dict[str, list[Pref]] = {roll_no: [] for roll_no in students}
    for row in pref_rows:
        roll_no = (row.get("roll_no") or "").strip()
        pref_type = (row.get("type") or "").strip()
        weight = _parse_int(
            row.get("weight"), f"preferences.csv: {roll_no} weight", errors
        )
        if weight is None:
            continue
        day_text = (row.get("day") or "").strip()
        shift_text = (row.get("shift_id") or "").strip()
        day = None
        shift_id = None
        if pref_type == "OFF_DAY":
            if not day_text:
                errors.append(f"preferences.csv: OFF_DAY for {roll_no!r} needs a day")
                continue
            day = _parse_int(
                day_text, f"preferences.csv: {roll_no} OFF_DAY day", errors
            )
            if day is None:
                continue
        else:
            if not shift_text:
                errors.append(
                    f"preferences.csv: {pref_type or 'preference'} for {roll_no!r} needs a shift_id"
                )
                continue
            shift_id = shift_text
        prefs.setdefault(roll_no, []).append(
            Pref(type=pref_type, day=day, shift_id=shift_id, weight=weight)
        )

    case = Case(
        horizon_days=horizon_days,
        students=students,
        by_group=by_group,
        shifts=shifts,
        shift_order=shift_order,
        demand=demand,
        prefs=prefs,
        config=config,
        folder=str(folder),
        load_errors=tuple(errors),
        structural_ok=structural_ok,
        available=available,
    )
    return case


def validate_case(case: Case) -> list[str]:
    """Return human-readable validation errors for a loaded case."""
    errors = list(case.load_errors)
    shifts_available = "shifts.csv" in case.available
    students_available = "students.csv" in case.available
    demand_available = "demand.csv" in case.available

    horizon = case.horizon_days
    horizon_known = "horizon_days" in case.config
    horizon_ok = horizon_known and 1 <= horizon <= MAX_DAYS
    if horizon_known and not horizon_ok:
        errors.append(
            f"config.csv: horizon_days {horizon} outside 1..{MAX_DAYS}"
        )

    for shift_id in case.shift_order:
        shift = case.shifts[shift_id]
        if not (_TIME_RE.match(shift.start) and _TIME_RE.match(shift.end)):
            errors.append(
                f"shifts.csv: shift {shift_id!r} has invalid start/end time "
                f"({shift.start!r}, {shift.end!r})"
            )
            continue
        expected_hours, crosses_midnight = _shift_hours(shift.start, shift.end)
        if abs(expected_hours - shift.hours) > 1e-9:
            errors.append(
                f"shifts.csv: shift {shift_id!r} hours mismatch: {shift.hours:g} "
                f"do not match start/end ({expected_hours:g})"
            )
        if shift.crosses_midnight != crosses_midnight:
            errors.append(
                f"shifts.csv: crosses_midnight wrong for {shift_id!r}: "
                f"expected {'yes' if crosses_midnight else 'no'}"
            )

    for roll_no, student in case.students.items():
        if student.group not in GROUPS:
            errors.append(
                f"students.csv: {roll_no!r} has unknown group {student.group!r}"
            )

    for (day, shift_id, group), required in case.demand.items():
        location = f"(day={day}, shift_id={shift_id!r}, group={group!r})"
        if horizon_ok and not 1 <= day <= horizon:
            errors.append(f"demand.csv: day {day} outside 1..{horizon} at {location}")
        if shifts_available and shift_id not in case.shifts:
            errors.append(f"demand.csv: unknown shift_id {shift_id!r} at {location}")
        if group not in GROUPS:
            errors.append(f"demand.csv: unknown group {group!r} at {location}")
        if required < 0:
            errors.append(f"demand.csv: negative required {required} at {location}")
        elif (
            students_available
            and group in GROUPS
            and required > len(case.by_group.get(group, []))
        ):
            size = len(case.by_group.get(group, []))
            errors.append(
                f"demand.csv: demand {required} at {location} is above group size "
                f"(exceeds group; group {group} has only {size} students)"
            )

    if horizon_ok and shifts_available and demand_available:
        expected_grid = {
            (day, shift_id, group)
            for day in range(1, horizon + 1)
            for shift_id in case.shift_order
            for group in GROUPS
        }
        missing = expected_grid - set(case.demand)
        if missing:
            errors.append(
                f"demand.csv: demand grid incomplete; {len(missing)} "
                f"(day, shift_id, group) combinations missing"
            )

    for roll_no, student_prefs in case.prefs.items():
        if students_available and roll_no not in case.students:
            errors.append(f"preferences.csv: unknown roll_no {roll_no!r}")
        for pref in student_prefs:
            if pref.type not in PREF_TYPES:
                errors.append(
                    f"preferences.csv: unknown type {pref.type!r} for {roll_no!r}"
                )
                continue
            if (
                pref.type == "OFF_DAY"
                and pref.day is not None
                and horizon_ok
                and not 1 <= pref.day <= horizon
            ):
                errors.append(
                    f"preferences.csv: OFF_DAY day {pref.day} outside 1..{horizon} "
                    f"for {roll_no!r}"
                )
            if pref.type in ("PREFER_SHIFT", "AVOID_SHIFT"):
                if shifts_available and pref.shift_id not in case.shifts:
                    errors.append(
                        f"preferences.csv: {pref.type} for {roll_no!r} has unknown "
                        f"shift_id {pref.shift_id!r}"
                    )
            if not 1 <= pref.weight <= 5:
                errors.append(
                    f"preferences.csv: weight {pref.weight} outside 1..5 for {roll_no!r}"
                )

    return errors


def load_case(folder: str) -> Case:
    """Load a case folder, raising CaseError with every validation error."""
    path = Path(folder)
    case = _load_case(path)
    errors = validate_case(case)
    if errors:
        raise CaseError(errors)
    return case


def _summary(case: Case) -> str:
    horizon = case.horizon_days
    student_counts = {group: len(case.by_group.get(group, [])) for group in GROUPS}
    total_students = len(case.students)
    total_demand = sum(case.demand.values())

    window = case.config.get("rolling_window_days", 7)
    mean_shift_hours = (
        sum(shift.hours for shift in case.shifts.values()) / len(case.shifts)
        if case.shifts
        else 0.0
    )
    if mean_shift_hours > 0 and window > 0:
        capacity_slots = sum(
            student.max_weekly_hours / mean_shift_hours * horizon / window
            for student in case.students.values()
        )
    else:
        capacity_slots = 0.0
    load_ratio = total_demand / capacity_slots if capacity_slots else 0.0

    counts = ", ".join(f"{group}: {student_counts[group]}" for group in GROUPS)
    name = Path(case.folder).name if case.folder else ""
    return "\n".join(
        [
            f"Case: {name}",
            f"Horizon: {horizon} days",
            f"Students: {total_students} ({counts})",
            f"Total demand slots: {total_demand}",
            f"Capacity slots: {capacity_slots:.1f}",
            f"Load ratio: {load_ratio:.3f}",
            "Validation: OK",
        ]
    )


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m roster.case_io <case_folder>")
        return 2
    try:
        case = load_case(args[0])
    except CaseError as exc:
        print("INVALID case:")
        for error in exc.errors:
            print(f"  - {error}")
        return 1
    print(_summary(case))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
