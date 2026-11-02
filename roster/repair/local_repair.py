"""Minimal-change sick-leave repair without re-solving the whole roster.

Scenario
--------
Student ``S`` reports sick shortly before the shift they hold on day ``D``.

Scope
-----
* Every assignment before ``D`` and from ``D + 3`` onward is frozen.
* Only days ``D`` and ``D + 1`` may change, and only for ``S``'s group.
* Frozen days still count in every rolling-window, streak and rest calculation
  because the objective is the real :func:`roster.evaluator.evaluate` report on
  the complete roster.

Fix
---
``S``'s day-``D`` assignment is always removed.  If ``S`` is also marked
unavailable for day ``D + 1``, that assignment is removed too.  The freed
slot(s) are then filled by the cheapest candidate in this preference order:

1. one free replacement from the same group,
2. a chain of at most two changes (move a worker into the slot, then backfill
   the cell they left with another free group member),
3. leave the slot unfilled.

The objective is::

    evaluator total cost + 0.5 * number of assignments changed from the original

so a repair never churns the roster for a negligible gain.  A candidate that
would create a hard-rule breach is discarded.  If no candidate beats leaving
the slot empty, the result keeps it unfilled and explains why in ``reason``.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Iterable

from ..evaluator import evaluate
from ..rules import Rules
from ..solvers.base import RepairResult, Roster

if TYPE_CHECKING:
    from ..case_io import Case

#: Cost charged for every original assignment that the repair changes.
CHANGE_COST = 0.5

#: A free replacement is one change; a chain is at most two.
MAX_EXTRA_CHANGES = 2

#: Days ``>= sick_day + FROZEN_TAIL_OFFSET`` are frozen.
FROZEN_TAIL_OFFSET = 3


def _diff(original: Roster, repaired: Roster) -> list[tuple[str, int, str | None, str | None]]:
    """Return one ``(roll_no, day, old_shift, new_shift)`` entry per changed cell."""
    entries = []
    for key in sorted(set(original) | set(repaired)):
        roll_no, day = key
        old = original.get(key)
        new = repaired.get(key)
        if old != new:
            entries.append((roll_no, day, old, new))
    return entries


def _members(case: "Case", group: str) -> list[str]:
    return list(case.by_group.get(group, ()))


def _assigned_count(
    case: "Case",
    roster: Roster,
    group: str,
    day: int,
    shift_id: str,
) -> int:
    return sum(
        1
        for (roll_no, assigned_day), assigned_shift in roster.items()
        if assigned_day == day
        and assigned_shift == shift_id
        and roll_no in case.students
        and case.students[roll_no].group == group
    )


def _is_free(roster: Roster, roll_no: str, day: int) -> bool:
    return (roll_no, day) not in roster


def _deficit(case: "Case", roster: Roster, group: str, day: int, shift_id: str) -> int:
    required = case.demand.get((day, shift_id, group), 0)
    return max(0, required - _assigned_count(case, roster, group, day, shift_id))


def _score(
    case: "Case",
    rules: Rules,
    original: Roster,
    candidate: Roster,
) -> tuple[float, list[tuple[str, int, str | None, str | None]]] | None:
    """Return ``(objective, diff)`` for a candidate, or None if it is hard-invalid."""
    report = evaluate(case, rules, candidate)
    if not report.hard_pass:
        return None
    diff = _diff(original, candidate)
    return report.total_cost + CHANGE_COST * len(diff), diff


def _direct_candidates(
    case: "Case",
    base: Roster,
    group: str,
    sick_roll_no: str,
    day: int,
    shift_id: str,
) -> Iterable[Roster]:
    """Roster variants that put one free same-group member into the freed cell."""
    for roll_no in _members(case, group):
        if roll_no == sick_roll_no or not _is_free(base, roll_no, day):
            continue
        candidate = dict(base)
        candidate[(roll_no, day)] = shift_id
        yield candidate


def _chain_candidates(
    case: "Case",
    base: Roster,
    group: str,
    sick_roll_no: str,
    day: int,
    shift_id: str,
    window_days: list[int],
) -> Iterable[Roster]:
    """Roster variants using two changes: move a worker in, backfill their cell.

    ``X`` currently works another cell on day ``d2`` in the window and is free on
    the sick day.  Moving ``X`` into the freed cell creates a deficit at their old
    cell; a free group member ``Y`` then fills that old cell.
    """
    for worker in _members(case, group):
        if worker == sick_roll_no or not _is_free(base, worker, day):
            continue
        for other_day in window_days:
            if other_day == day:
                continue
            key = (worker, other_day)
            other_shift = base.get(key)
            if other_shift is None:
                continue
            moved = dict(base)
            del moved[key]
            moved[(worker, day)] = shift_id
            if _deficit(case, moved, group, other_day, other_shift) == 0:
                # The vacated cell had slack, so no backfill is needed.
                yield moved
                continue
            for replacement in _members(case, group):
                if replacement in (sick_roll_no, worker):
                    continue
                if not _is_free(moved, replacement, other_day):
                    continue
                candidate = dict(moved)
                candidate[(replacement, other_day)] = other_shift
                yield candidate


def _direct_combinations(
    case: "Case",
    base: Roster,
    group: str,
    sick_roll_no: str,
    deficits: list[tuple[int, str]],
    index: int = 0,
) -> Iterable[Roster]:
    """Yield direct fills for several freed slots, including leaving some empty."""
    if index == len(deficits):
        yield base
        return
    day, shift_id = deficits[index]
    yield from _direct_combinations(case, base, group, sick_roll_no, deficits, index + 1)
    for roll_no in _members(case, group):
        if roll_no == sick_roll_no or not _is_free(base, roll_no, day):
            continue
        candidate = dict(base)
        candidate[(roll_no, day)] = shift_id
        yield from _direct_combinations(case, candidate, group, sick_roll_no, deficits, index + 1)


def _reason(
    case: "Case",
    rules: Rules,
    group: str,
    unfilled: list[tuple[int, str]],
) -> str:
    slot_text = ", ".join(f"day {day} {shift_id}" for day, shift_id in unfilled)
    return (
        f"left {slot_text} unfilled for {group}: no free same-group replacement or "
        f"chain of at most {MAX_EXTRA_CHANGES} changes beat the unfilled penalty "
        f"({rules.penalty_unfilled_slot:g} per slot) once the {CHANGE_COST:g} cost "
        f"per changed assignment is added"
    )


def repair(
    case: "Case",
    roster: Roster,
    sick_roll_no: str,
    sick_day: int,
    shift_id: str,
    time_limit_s: float = 90.0,
    seed: int = 0,
    unavailable_days: list[int] | None = None,
) -> RepairResult:
    """Return a minimal-change repair after ``sick_roll_no`` reports sick.

    Parameters match :data:`roster.solvers.base.repair`.  ``shift_id`` is the
    shift the student held on ``sick_day``.  ``unavailable_days`` may include
    ``sick_day + 1`` (or any other day); only days in the repair window are
    acted on.

    Raises ``ValueError`` if the student, day or original assignment is unknown,
    or if the sick student is not rostered on the reported day.
    """
    if sick_roll_no not in case.students:
        raise ValueError(f"unknown student {sick_roll_no!r}")
    horizon = case.horizon_days
    if not isinstance(sick_day, int) or not 1 <= sick_day <= horizon:
        raise ValueError(f"sick day {sick_day!r} outside 1..{horizon}")
    if (sick_roll_no, sick_day) not in roster:
        raise ValueError(
            f"{sick_roll_no} is not rostered on day {sick_day}; nothing to repair"
        )
    # ``shift_id`` names the reported shift; if it disagrees with the roster the
    # roster is authoritative for the cell that is actually freed.

    rules = Rules.from_case(case)
    group = case.students[sick_roll_no].group
    unavailable = set(unavailable_days or ())
    window_days = [sick_day]
    if sick_day + 1 <= horizon:
        window_days.append(sick_day + 1)

    # Freeze everything outside the window by starting from a copy and only
    # touching cells inside it.  Days before sick_day and >= sick_day+3 are
    # never written.
    base = dict(roster)
    freed: list[tuple[int, str]] = []
    held = base.pop((sick_roll_no, sick_day), None)
    if held is not None:
        freed.append((sick_day, held))
    if sick_day + 1 in unavailable:
        held_next = base.pop((sick_roll_no, sick_day + 1), None)
        if held_next is not None:
            freed.append((sick_day + 1, held_next))
    # De-duplicate in case both paths ever name the same cell.
    deficits = list(dict.fromkeys(freed))

    direct_candidates: list[Roster] = []
    chain_candidates: list[Roster] = []
    if len(deficits) == 1:
        day, slot_shift = deficits[0]
        direct_candidates = list(
            _direct_candidates(case, base, group, sick_roll_no, day, slot_shift)
        )
        chain_candidates = list(
            _chain_candidates(
                case, base, group, sick_roll_no, day, slot_shift, window_days
            )
        )
    elif len(deficits) > 1:
        direct_candidates = list(
            _direct_combinations(case, base, group, sick_roll_no, deficits)
        )

    started = time.monotonic()
    deadline = started + max(0.0, float(time_limit_s))

    def score_all(candidates: Iterable[Roster]):
        """Deduplicate and score candidates; keep only hard-valid rosters."""
        unique: dict[tuple[tuple[tuple[str, int], str], ...], Roster] = {}
        for candidate in candidates:
            key = tuple(sorted(candidate.items()))
            unique.setdefault(key, candidate)
        scored = []
        for index, candidate in enumerate(unique.values()):
            if index and time.monotonic() > deadline:
                break
            result = _score(case, rules, roster, candidate)
            if result is not None:
                score, diff = result
                scored.append((score, len(diff), diff, candidate))
        return scored

    # Preference order: a direct replacement first; only if no hard-valid direct
    # replacement exists do we try a chain of at most two changes.  Leaving the
    # slot unfilled is always a candidate and wins when it is cheaper.
    direct_scored = score_all(direct_candidates)
    base_scored = _score(case, rules, roster, base)
    if direct_scored:
        pool = direct_scored
        if base_scored is not None:
            pool.append((base_scored[0], len(base_scored[1]), base_scored[1], base))
    else:
        chain_scored = score_all(chain_candidates)
        pool = chain_scored
        if base_scored is not None:
            pool.append((base_scored[0], len(base_scored[1]), base_scored[1], base))
    if not pool:
        # No hard-valid candidate at all (for example the input roster already
        # breached a hard rule).  Return the forced removal and explain it.
        best_roster = base
        best_diff = _diff(roster, base)
    else:
        _score_value, _diff_len, best_diff, best_roster = min(
            pool, key=lambda item: (item[0], item[1])
        )

    unfilled = [
        (day, slot_shift)
        for day, slot_shift in deficits
        if _deficit(case, best_roster, group, day, slot_shift) > 0
    ]
    reason = _reason(case, rules, group, unfilled) if unfilled else None
    return RepairResult(roster=best_roster, diff=best_diff, reason=reason)
