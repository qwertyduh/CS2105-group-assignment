"""Shared solver contract: the Roster type, solve()/repair() signatures, roster.csv I/O.

A roster maps (roll_no, day) to the shift_id worked that day. Only worked days are
stored. Because the key is (roll_no, day), a Roster can never hold two shifts for one
student on one day; that hard rule can only be broken in a roster.csv file, so
read_roster() reports such rows instead of silently dropping them.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from ..case_io import Case

Roster = dict[tuple[str, int], str]

ROSTER_HEADER = ["roll_no", "day", "shift_id"]


@dataclass
class RepairResult:
    roster: Roster
    diff: list[tuple[str, int, Optional[str], Optional[str]]] = field(default_factory=list)
    reason: Optional[str] = None


class RosterError(ValueError):
    def __init__(self, errors: list[str]):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def solve(case: "Case", time_limit_s: float, seed: int) -> Roster:
    raise NotImplementedError


def repair(
    case: "Case",
    roster: Roster,
    sick_roll_no: str,
    sick_day: int,
    shift_id: str,
    time_limit_s: float,
    seed: int,
    unavailable_days: list[int] | None = None,
) -> RepairResult:
    raise NotImplementedError


def save_roster(roster: Roster, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(ROSTER_HEADER)
        for (roll_no, day), shift_id in sorted(roster.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            writer.writerow([roll_no, day, shift_id])


def read_roster(path: str) -> tuple[Roster, list[str]]:
    """Read roster.csv and return (roster, problems).

    Problems are hard breaches found while reading: a wrong header, malformed rows,
    and any student listed more than once on the same day. For a repeated
    (roll_no, day) the first row is kept and every later row is reported.
    """
    roster: Roster = {}
    problems: list[str] = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            return roster, problems
        if [h.strip() for h in header] != ROSTER_HEADER:
            problems.append(f"roster.csv header must be {','.join(ROSTER_HEADER)}, got {','.join(header)}")
            return roster, problems
        for line_no, row in enumerate(reader, start=2):
            if not row or all(not c.strip() for c in row):
                continue
            if len(row) != 3:
                problems.append(f"line {line_no}: expected 3 columns, got {len(row)}")
                continue
            roll_no, day_text, shift_id = (c.strip() for c in row)
            try:
                day = int(day_text)
            except ValueError:
                problems.append(f"line {line_no}: day '{day_text}' is not an integer")
                continue
            if not roll_no or not shift_id:
                problems.append(f"line {line_no}: empty roll_no or shift_id")
                continue
            key = (roll_no, day)
            if key in roster:
                problems.append(
                    f"one shift per day: {roll_no} on day {day} has {roster[key]} and {shift_id} (line {line_no})"
                )
                continue
            roster[key] = shift_id
    return roster, problems


def load_roster(path: str) -> Roster:
    roster, problems = read_roster(path)
    if problems:
        raise RosterError(problems)
    return roster
