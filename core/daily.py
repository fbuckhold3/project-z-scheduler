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

# Per-group composition: (n_seniors, n_interns)  index 0 = Mario/A, 1 = Luigi/B …
# Mario at SLUH carries 2 seniors only; every other team is 1 sr + 2 int.
# VA teams are 1 sr + 1 int each.
MK_GROUP_COMPOSITION: dict[str, list[tuple[int, int]]] = {
    "SLUH": [(2, 0), (1, 2), (1, 2), (1, 2), (1, 2)],
    "VA":   [(1, 1), (1, 1), (1, 1), (1, 1), (1, 1)],
}

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

    Uses a stint-based approach: a team's floor advances exactly once each
    time they return from an off period.  This gives 3 consistent floor
    stints within a typical 3-week (21-day) block.

    During an off period the value returned is the PREVIOUS stint's floor
    (caller should check mk_is_working before using this value).
    """
    cycle = n_teams * days_off_per_turn          # e.g. 10
    off_start = group_idx * days_off_per_turn    # when this group's off window starts in cycle
    complete_cycles = abs_day // cycle
    pos_in_cycle    = abs_day % cycle
    # Count completed off-periods for this group up to (not including) abs_day.
    # An off-period is "completed" once we're past its end inside the current cycle.
    if pos_in_cycle >= off_start + days_off_per_turn:
        n_completed_off = complete_cycles + 1
    else:
        n_completed_off = complete_cycles
    # Add group_idx as an initial phase offset so all 5 groups start on DIFFERENT
    # floors from day 1.  Without this every group converges on the same floor
    # after cycling through their first off-period (all within the first 10 days).
    return floors[(n_completed_off + group_idx) % len(floors)]


def mk_off_group(abs_day: int, n_teams: int = 5,
                 days_off_per_turn: int = 2) -> int:
    """Return which group index is off on abs_day."""
    return (abs_day // days_off_per_turn) % n_teams


# ---------------------------------------------------------------------------
# NF helpers
# ---------------------------------------------------------------------------

def _rotation_chunks(n_units: int, n_days: int, areas: list,
                     chunk_size: int, off_nights: int = 1) -> list:
    """
    Build a chunk-based rotation schedule where each unit (resident/intern)
    cycles through ALL areas in multi-day stints.

    Pattern per unit:
        chunk_size nights on area[0] → off_nights off →
        chunk_size nights on area[1] → off_nights off → … repeat

    Units are staggered evenly in phase so that every area is staffed
    every night.

    Math (verified for standard NF staffing):
        chunk_period = chunk_size + off_nights
        cycle        = n_areas * chunk_period
        stagger      = cycle // n_units    (must be integer for perfect coverage)

      Senior NF : n=5, areas=4, chunk=4, off=1  → cycle=20, stagger=4  ✓
      Intern NF : n=4, areas=3, chunk=3, off=1  → cycle=12, stagger=3  ✓

    Returns list[list[dict{resident_idx, assignment}]], length = n_days.
    """
    n_areas      = len(areas)
    chunk_period = chunk_size + off_nights
    cycle        = n_areas * chunk_period
    stagger      = cycle // n_units if (n_units > 0 and cycle % n_units == 0) \
                   else max(1, round(cycle / max(n_units, 1)))

    def _assign(phase: int) -> str:
        p   = phase % cycle
        pos = p % chunk_period
        if pos >= chunk_size:
            return "Off"
        return areas[(p // chunk_period) % n_areas]

    schedule = []
    for d in range(n_days):
        row = []
        for u in range(n_units):
            phase = (d + u * stagger) % cycle
            row.append({"resident_idx": u, "assignment": _assign(phase)})
        schedule.append(row)
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

    # ---------- Level-aware MK group assignment ----------------------------------
    # Seniors and interns are distributed across groups following MK_GROUP_COMPOSITION.
    # For SLUH: Mario (g=0) gets 2 seniors, each other group gets 1 senior + 2 interns.
    # For VA  : every group gets 1 senior + 1 intern.

    def _mk_group_assign(rot_id_: str, rids_: list) -> dict[str, int]:
        composition = MK_GROUP_COMPOSITION.get(rot_id_)
        n = DEFAULT_MK_N_TEAMS
        if not composition:
            return {rid: i % n for i, rid in enumerate(sorted(rids_))}
        srs  = sorted([r for r in rids_ if res_map.get(r) and res_map[r].is_senior])
        ints = sorted([r for r in rids_ if res_map.get(r) and not res_map[r].is_senior])
        sr_per  = [c[0] for c in composition]
        int_per = [c[1] for c in composition]
        sr_cyc  = sum(sr_per)
        int_cyc = sum(int_per)
        gmap: dict[str, int] = {}
        for i, rid in enumerate(srs):
            pos, cs = (i % sr_cyc if sr_cyc else 0), 0
            for g, cnt in enumerate(sr_per):
                cs += cnt
                if pos < cs:
                    gmap[rid] = g; break
        for i, rid in enumerate(ints):
            if not int_cyc:
                gmap[rid] = 0; continue
            pos, cs = i % int_cyc, 0
            for g, cnt in enumerate(int_per):
                cs += cnt
                if pos < cs:
                    gmap[rid] = g; break
        for rid in rids_:
            if rid not in gmap:
                gmap[rid] = len(gmap) % n
        return gmap

    mk_group_map: dict[str, dict[str, int]] = {}   # rot_id -> {res_id -> group_idx}
    mk_groups_out: dict[str, list[MKGroup]] = {}

    for rot_id in mk_rot_ids:
        rids = mk_all_residents.get(rot_id, [])
        n_teams = DEFAULT_MK_N_TEAMS
        team_names = MK_TEAM_NAMES.get(rot_id, [f"T{i}" for i in range(n_teams)])
        floors     = MK_FLOORS.get(rot_id,     [f"F{i}" for i in range(n_teams - 1)])

        group_map: dict[str, int] = _mk_group_assign(rot_id, rids)
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

    # chunk sizes matched to the stagger math: senior 4-night chunks, intern 3-night
    NF_CHUNK: dict[str, int] = {"senior": 4, "intern": 3}

    def _process_nf(groups_dict: dict, level: str, covers: list):
        chunk_size = NF_CHUNK.get(level, 3)
        for (rot_id, sw, ew), rids in groups_dict.items():
            n_weeks = ew - sw + 1
            n_days  = n_weeks * 7
            day0    = (sw - 1) * 7
            sorted_rids = sorted(rids)
            n_res = len(sorted_rids)
            if n_res == 0:
                continue
            daily = _rotation_chunks(n_res, n_days, covers, chunk_size=chunk_size)
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
