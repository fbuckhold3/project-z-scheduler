"""
core/daily.py
Day-level schedule expansion.

Converts the week-level Schedule (from solver) into a per-resident,
per-day assignment grid, accounting for:

  - MarioKart (MK)  : 5 groups, 4 working each day, continuous year-wide cycle
  - Night Float (NF): 5sr/4int per 2-week block, 1 off each night rotating
  - Standard IP     : 7 consecutive working days (MICU, Bronze, Gold, Cards)
  - OP / Clinic     : Mon–Fri only
  - Jeopardy        : available all 7 days
"""
from __future__ import annotations
from dataclasses import dataclass, field
from collections import defaultdict
from .models import (
    Schedule, Assignment, Resident, Rotation,
    AcademicYear, RotationPattern, RotationType,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

MK_TEAM_NAMES: dict[str, list[str]] = {
    "SLUH": ["Mario", "Luigi", "Peach", "Yoshi", "Bowser"],
    "VA":   ["A", "B", "C", "D", "E"],
}
MK_FLOORS: dict[str, list[str]] = {
    "SLUH": ["Red", "Green", "White", "Yellow"],
    "VA":   ["A", "B", "C", "D"],
}
DEFAULT_MK_N_TEAMS = 5

NF_SR_COVERS  = ["Admits", "MICU", "VA", "Cards/Bronze"]
NF_INT_COVERS = ["NF-MedA", "NF-MedB", "NF-MICUi"]


# ---------------------------------------------------------------------------
# Result dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DayEntry:
    rotation_id: str    # e.g. "SLUH", "NF", "OP", "" (unassigned)
    assignment: str     # floor name, NF area, rotation abbrev, or "Off"
    working: bool       # True = physically present and working
    team: str = ""      # MK team name ("Mario", "A", …) or ""


@dataclass
class MKGroup:
    """One of the 5 MK groups for a single MK rotation (year-wide)."""
    rotation_id: str
    group_idx: int
    team_name: str
    resident_ids: list          # all residents who spend ANY time in this group
    floors: list                # floor rotation for this rotation


@dataclass
class NfBlock:
    rotation_id: str
    start_week: int             # 1-indexed
    end_week: int
    resident_ids: list          # sorted list (seniors or interns separately)
    level: str                  # "senior" or "intern"
    covers: list                # coverage area names
    daily: list                 # list[list[dict{resident_idx, assignment}]]


@dataclass
class ResidentStats:
    days_on: int = 0
    days_off_rot: int = 0       # days marked Off within a rotation block
    max_consecutive: int = 0
    weekends_on: int = 0


@dataclass
class DailySchedule:
    resident_daily: dict        # resident_id -> list[DayEntry]  (total_days long)
    mk_groups: dict             # rot_id -> list[MKGroup]  (5 groups per MK rotation)
    nf_blocks: dict             # (rot_id, start_week, level) -> NfBlock
    coverage: dict              # rot_id -> list[int]  (total_days long)
    stats: dict                 # resident_id -> ResidentStats
    total_days: int
    mk_days_off: int            # stored for UI display
    # Quick lookup: resident_id -> group_idx for each MK rotation
    mk_group_map: dict = field(default_factory=dict)  # (rot_id, res_id) -> group_idx


# ---------------------------------------------------------------------------
# MK day-off helpers
# ---------------------------------------------------------------------------

def mk_is_working(group_idx: int, abs_day: int, n_teams: int = 5,
                  days_off_per_turn: int = 2) -> bool:
    """Return True if the group is working on abs_day (0-indexed)."""
    cycle = n_teams * days_off_per_turn
    off_group = (abs_day // days_off_per_turn) % n_teams
    return group_idx != off_group


def mk_floor(group_idx: int, abs_day: int, floors: list,
             n_teams: int = 5, days_off_per_turn: int = 2) -> str:
    """Return the floor name for a working group on a given day.

    Floor assignment is fixed to the group's position among active teams
    on week boundaries (abs_day // 7) so that a resident stays on the
    same floor for a full week before any rotation occurs.
    """
    off_group = (abs_day // days_off_per_turn) % n_teams
    active = [g for g in range(n_teams) if g != off_group]
    slot = active.index(group_idx)
    # Rotate floor assignment weekly, not every 2 days
    floor_idx = (slot + abs_day // 7) % len(floors)
    return floors[floor_idx]


def mk_off_group(abs_day: int, n_teams: int = 5,
                 days_off_per_turn: int = 2) -> int:
    """Return which group index is off on abs_day."""
    return (abs_day // days_off_per_turn) % n_teams


# ---------------------------------------------------------------------------
# NF helpers
# ---------------------------------------------------------------------------

def _nf_pattern(n_residents: int, n_days: int, covers: list,
                days_off_per_turn: int = 1) -> list:
    """
    Build NF daily rotation with FIXED coverage positions.

    Each resident is permanently assigned to one coverage area for the
    entire block.  Residents 0..len(covers)-1 map to covers[0..n-1].
    Any extra residents (index >= len(covers)) are labeled "Float" —
    they fill in wherever needed when the pinned resident is off.

    One resident is off each day, cycling through the group.  When they
    ARE working they always show the same area label.

    Returns list[list[dict{resident_idx, assignment}]], length = n_days.
    """
    n_covers = len(covers)
    fixed = [covers[i] if i < n_covers else "Float" for i in range(n_residents)]

    cycle = n_residents * days_off_per_turn
    schedule = []
    for d in range(n_days):
        off_idx = (d // days_off_per_turn) % n_residents
        assignments = []
        for r in range(n_residents):
            if r == off_idx:
                assignments.append({"resident_idx": r, "assignment": "Off"})
            else:
                assignments.append({"resident_idx": r, "assignment": fixed[r]})
        schedule.append(assignments)
    return schedule


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_daily_schedule(
    schedule: Schedule,
    residents: list[Resident],
    rotations: list[Rotation],
    academic_year: AcademicYear,
    mk_days_off: int = 2,
    max_consecutive: int = 8,
) -> DailySchedule:
    """
    Expand a week-level Schedule into a per-resident per-day grid.

    Weeks are 1-indexed.  Days are 0-indexed from the start of week 1
    (day 0 = Monday of week 1).
    """
    total_weeks = academic_year.total_weeks
    total_days  = total_weeks * 7

    rot_map = {r.rotation_id: r for r in rotations}
    res_map = {r.resident_id: r for r in residents}

    # --- Initialise empty day entries ---
    resident_daily: dict[str, list[DayEntry]] = {
        r.resident_id: [DayEntry("", "", False) for _ in range(total_days)]
        for r in residents
    }

    # =========================================================================
    # 1. MK ROTATIONS — year-wide group assignment
    # =========================================================================
    mk_rot_ids = [
        r.rotation_id for r in rotations
        if r.pattern == RotationPattern.MK and r.active
    ]

    # For each MK rotation, find every resident who touches it during the year
    mk_all_residents: dict[str, list[str]] = {}  # rot_id -> sorted list of res_ids
    for rot_id in mk_rot_ids:
        rids = sorted({a.resident_id for a in schedule.assignments
                       if a.rotation_id == rot_id})
        mk_all_residents[rot_id] = rids

    # Assign groups (0–4) round-robin by sorted resident_id
    mk_group_map: dict[str, dict[str, int]] = {}   # rot_id -> {res_id -> group_idx}
    mk_groups_out: dict[str, list[MKGroup]] = {}

    for rot_id in mk_rot_ids:
        rids = mk_all_residents.get(rot_id, [])
        n_teams = DEFAULT_MK_N_TEAMS
        team_names = MK_TEAM_NAMES.get(rot_id, [f"T{i}" for i in range(n_teams)])
        floors     = MK_FLOORS.get(rot_id,     [f"F{i}" for i in range(n_teams - 1)])

        group_map: dict[str, int] = {rid: i % n_teams for i, rid in enumerate(rids)}
        mk_group_map[rot_id] = group_map

        # Build MKGroup objects (one per group index)
        groups: list[MKGroup] = []
        for g in range(n_teams):
            g_rids = [rid for rid, gi in group_map.items() if gi == g]
            groups.append(MKGroup(
                rotation_id=rot_id,
                group_idx=g,
                team_name=team_names[g],
                resident_ids=g_rids,
                floors=floors,
            ))
        mk_groups_out[rot_id] = groups

    # Write MK day entries for every assignment
    for a in schedule.assignments:
        rot = rot_map.get(a.rotation_id)
        if rot is None or rot.pattern != RotationPattern.MK:
            continue
        group_map = mk_group_map.get(a.rotation_id, {})
        team_names = MK_TEAM_NAMES.get(a.rotation_id, [f"T{i}" for i in range(5)])
        floors     = MK_FLOORS.get(a.rotation_id,     [f"F{i}" for i in range(4)])
        n_teams    = DEFAULT_MK_N_TEAMS

        group_idx = group_map.get(a.resident_id, 0)
        tname     = team_names[group_idx] if group_idx < len(team_names) else f"T{group_idx}"

        for w in range(a.start_week, a.end_week + 1):
            w_day0 = (w - 1) * 7
            for d in range(7):
                abs_d = w_day0 + d
                if abs_d >= total_days:
                    break
                working = mk_is_working(group_idx, abs_d, n_teams, mk_days_off)
                if working:
                    floor = mk_floor(group_idx, abs_d, floors, n_teams, mk_days_off)
                    assign = floor
                else:
                    assign = "Off"
                resident_daily[a.resident_id][abs_d] = DayEntry(
                    rotation_id=a.rotation_id,
                    assignment=assign,
                    working=working,
                    team=tname,
                )

    # =========================================================================
    # 2. NF ROTATIONS — block-level rotation, split by level
    # =========================================================================
    # Group NF assignments by (start_week, end_week) and level
    nf_blocks_senior: dict[tuple, list[str]] = defaultdict(list)
    nf_blocks_intern: dict[tuple, list[str]] = defaultdict(list)

    for a in schedule.assignments:
        rot = rot_map.get(a.rotation_id)
        if rot is None or rot.pattern != RotationPattern.NF:
            continue
        res = res_map.get(a.resident_id)
        if res is None:
            continue
        key = (a.rotation_id, a.start_week, a.end_week)
        if res.is_senior:
            nf_blocks_senior[key].append(a.resident_id)
        else:
            nf_blocks_intern[key].append(a.resident_id)

    nf_blocks_out: dict = {}

    def _process_nf(groups_dict: dict, level: str, covers: list):
        for (rot_id, sw, ew), rids in groups_dict.items():
            n_weeks = ew - sw + 1
            n_days  = n_weeks * 7
            day0    = (sw - 1) * 7
            sorted_rids = sorted(rids)
            n_res = len(sorted_rids)
            if n_res == 0:
                continue
            daily = _nf_pattern(n_res, n_days, covers)
            block = NfBlock(
                rotation_id=rot_id,
                start_week=sw,
                end_week=ew,
                resident_ids=sorted_rids,
                level=level,
                covers=covers,
                daily=daily,
            )
            nf_blocks_out[(rot_id, sw, level)] = block

            for d in range(n_days):
                abs_d = day0 + d
                if abs_d >= total_days:
                    break
                for entry in daily[d]:
                    ridx = entry["resident_idx"]
                    if ridx < len(sorted_rids):
                        rid     = sorted_rids[ridx]
                        assign  = entry["assignment"]
                        working = assign != "Off"
                        resident_daily[rid][abs_d] = DayEntry(
                            rotation_id=rot_id,
                            assignment=assign,
                            working=working,
                            team="",
                        )

    _process_nf(nf_blocks_senior, "senior", NF_SR_COVERS)
    _process_nf(nf_blocks_intern, "intern", NF_INT_COVERS)

    # =========================================================================
    # 3. ALL OTHER ROTATION PATTERNS
    # =========================================================================
    for a in schedule.assignments:
        rot = rot_map.get(a.rotation_id)
        if rot is None:
            continue
        if rot.pattern in (RotationPattern.MK, RotationPattern.NF):
            continue  # already handled

        for w in range(a.start_week, a.end_week + 1):
            w_day0 = (w - 1) * 7
            for d in range(7):
                abs_d = w_day0 + d
                if abs_d >= total_days:
                    break
                dow = d  # 0=Mon … 6=Sun

                if rot.rot_type == RotationType.OP or rot.pattern == RotationPattern.CLINIC:
                    # Mon–Fri only
                    working = dow < 5
                    assign  = rot.abbrev if working else "Off"
                elif a.rotation_id == "Jeopardy":
                    working = True
                    assign  = "Jeopardy"
                else:
                    # Standard IP (Gold, Cards, MICU, Bronze, ABABA IP weeks)
                    # Apply MICU stagger: stagger_day=0 → Sun start, =1 → Mon start
                    if a.stagger_day == 1 and d == 0:
                        # This resident starts Monday; skip Sunday (d=6 of prev week handled elsewhere)
                        working = False
                        assign  = "Pre-start"
                    else:
                        working = True
                        assign  = rot.abbrev

                resident_daily[a.resident_id][abs_d] = DayEntry(
                    rotation_id=a.rotation_id,
                    assignment=assign,
                    working=working,
                    team="",
                )

    # =========================================================================
    # 4. COVERAGE COUNTS
    # =========================================================================
    coverage: dict[str, list[int]] = defaultdict(lambda: [0] * total_days)
    for rid, day_list in resident_daily.items():
        for d, entry in enumerate(day_list):
            if entry.working and entry.rotation_id:
                coverage[entry.rotation_id][d] += 1
    coverage = dict(coverage)

    # =========================================================================
    # 5. PER-RESIDENT STATS
    # =========================================================================
    stats: dict[str, ResidentStats] = {}
    for rid, day_list in resident_daily.items():
        s = ResidentStats()
        consec = 0
        for d, entry in enumerate(day_list):
            dow = d % 7
            if entry.working and entry.rotation_id:
                s.days_on += 1
                consec    += 1
                s.max_consecutive = max(s.max_consecutive, consec)
                if dow >= 5:
                    s.weekends_on += 1
            else:
                if entry.rotation_id:
                    s.days_off_rot += 1
                consec = 0
        stats[rid] = s

    # Flatten mk_group_map for DailySchedule
    flat_group_map: dict[tuple, int] = {}
    for rot_id, gmap in mk_group_map.items():
        for res_id, gi in gmap.items():
            flat_group_map[(rot_id, res_id)] = gi

    return DailySchedule(
        resident_daily=resident_daily,
        mk_groups=mk_groups_out,
        nf_blocks=nf_blocks_out,
        coverage=coverage,
        stats=stats,
        total_days=total_days,
        mk_days_off=mk_days_off,
        mk_group_map=flat_group_map,
    )


# ---------------------------------------------------------------------------
# Convenience: build per-week team roster for a MK rotation
# ---------------------------------------------------------------------------

def mk_week_roster(
    daily_sched: DailySchedule,
    rot_id: str,
    week: int,           # 1-indexed
    residents: list[Resident],
    n_teams: int = 5,
) -> dict[str, list]:
    """
    Return {team_name: [resident_ids_working_that_week_on_that_team]}.
    Used by the UI to show "who is on which team this week".
    """
    team_names = MK_TEAM_NAMES.get(rot_id, [f"T{i}" for i in range(n_teams)])
    res_map    = {r.resident_id: r for r in residents}
    day0       = (week - 1) * 7

    roster: dict[str, list] = {t: [] for t in team_names}

    for r in residents:
        rid = r.resident_id
        # Check if resident has any working day on this rotation this week
        for d in range(7):
            abs_d = day0 + d
            if abs_d >= daily_sched.total_days:
                break
            entry = daily_sched.resident_daily[rid][abs_d]
            if entry.rotation_id == rot_id and entry.working:
                gi = daily_sched.mk_group_map.get((rot_id, rid), 0)
                tname = team_names[gi] if gi < len(team_names) else f"T{gi}"
                if rid not in roster[tname]:
                    roster[tname].append(rid)
                break  # counted once per week

    return roster
