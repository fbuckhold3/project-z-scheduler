"""
core/defaults.py
Pre-loaded default data derived from schedule_explorer.xlsx.
These are the starting values shown in Configuration; everything is editable in the UI.
"""
from __future__ import annotations
from .models import (
    Rotation, RotationType, RotationPattern,
    RotatorProgram, Resident, ResidentLevel, AcademicYear, Assignment,
)


# ---------------------------------------------------------------------------
# Default Academic Year
# ---------------------------------------------------------------------------

def default_academic_year() -> AcademicYear:
    """
    48-week academic year with blackout (vacation) weeks.

    Blackout weeks:
      • Week  1 — July 4 ramp-up / holiday; no regular scheduling
      • Weeks 25-26 — Dec 23 – Jan 2 winter break
    """
    return AcademicYear(
        label="2025-2026",
        total_weeks=48,
        start_date="2025-07-07",
        blackout_weeks=[1, 25, 26],
    )


# ---------------------------------------------------------------------------
# Default Rotations
# ---------------------------------------------------------------------------

def default_rotations() -> list[Rotation]:
    return [
        Rotation(
            rotation_id="SLUH",
            name="SLUH Inpatient",
            abbrev="SLUH",
            rot_type=RotationType.IP,
            pattern=RotationPattern.MK,
            senior_capacity=6,   # 5 groups: 1×(2sr) + 4×(1sr+2i); 4 active/day
            intern_capacity=8,   # 4 teams × 2 interns each
            min_block_weeks=2,
            max_block_weeks=4,
            eligible_levels=["senior", "intern"],
            required=True,
            color="#3B82F6",
            notes=(
                "MarioKart pattern: 4 patient teams (Yellow, Green, Red, White), "
                "5 resident groups rotating in 5-7 day stints. 4 groups active / 1 off "
                "on any given day. sluh_mario has 2 seniors; others have 1 senior + 2 interns. "
                "2 consecutive days off guaranteed in each 21-day block."
            ),
        ),
        Rotation(
            rotation_id="VA",
            name="VA Inpatient",
            abbrev="VA",
            rot_type=RotationType.IP,
            pattern=RotationPattern.MK,
            senior_capacity=5,   # 5 groups, 1 senior each
            intern_capacity=5,   # 5 groups, 1 intern each
            min_block_weeks=2,
            max_block_weeks=4,
            eligible_levels=["senior", "intern"],
            required=True,
            color="#10B981",
            notes=(
                "MarioKart pattern: 4 teams (A, B, C, D), 5 resident groups. "
                "Same structure as SLUH but different staffing mix."
            ),
        ),
        Rotation(
            rotation_id="MICU",
            name="Medical ICU",
            abbrev="MICU",
            rot_type=RotationType.IP,
            pattern=RotationPattern.ABABA,
            senior_capacity=4,   # 2 residents × 2 MICU teams; staggered Sun/Mon starts
            intern_capacity=2,   # 1 intern per team; staggered Sun/Mon
            min_block_weeks=1,
            max_block_weeks=1,   # each A-block is exactly 7 days
            eligible_levels=["senior", "intern"],
            required=True,
            color="#EF4444",
            notes=(
                "ABABA pattern: 7 days on MICU (A), then outpatient/off (B), repeat 3×. "
                "Staggered starts: 2 seniors start Sunday, 2 start Monday. "
                "1 intern Sunday, 1 intern Monday. "
                "Rotators (EM, Anesthesia, Neurology) fill some slots."
            ),
        ),
        Rotation(
            rotation_id="NF",
            name="Night Float",
            abbrev="NF",
            rot_type=RotationType.IP,
            pattern=RotationPattern.NF,
            senior_capacity=5,   # 5 seniors assigned → 4 nightly slots (1 off per night)
            intern_capacity=4,   # 4 interns assigned → 3 nightly slots (1 off per night)
            min_block_weeks=2,
            max_block_weeks=2,   # always 2-week blocks
            eligible_levels=["senior", "intern"],
            required=True,
            color="#8B5CF6",
            notes=(
                "14-day block. 5 seniors assigned → 4 nightly slots (1 off per night). "
                "4 interns assigned → 3 nightly slots (1 off per night). "
                "Max 4 consecutive days worked. "
                "Must have OP/Clinic week on BOTH sides — no IP directly before or after."
            ),
        ),
        Rotation(
            rotation_id="Bronze",
            name="Bronze Service",
            abbrev="Brnz",
            rot_type=RotationType.IP,
            pattern=RotationPattern.ABABA,
            senior_capacity=2,   # staggered Sun/Mon starts
            intern_capacity=0,
            min_block_weeks=1,
            max_block_weeks=1,
            eligible_levels=["senior"],
            required=True,
            color="#F59E0B",
            notes=(
                "ABABA pattern (same as MICU). 2 seniors with staggered Sun/Mon starts. "
                "Senior-only service."
            ),
        ),
        Rotation(
            rotation_id="Gold",
            name="Gold Service",
            abbrev="Gold",
            rot_type=RotationType.IP,
            pattern=RotationPattern.STANDARD,
            senior_capacity=1,
            intern_capacity=0,
            min_block_weeks=1,
            max_block_weeks=1,
            eligible_levels=["senior"],
            required=True,
            color="#FBBF24",
            notes="7 days straight, senior only. In staffing plan.",
        ),
        Rotation(
            rotation_id="Cards",
            name="Cardiology",
            abbrev="Cards",
            rot_type=RotationType.IP,
            pattern=RotationPattern.STANDARD,
            senior_capacity=2,
            intern_capacity=1,
            min_block_weeks=1,
            max_block_weeks=4,
            eligible_levels=["senior", "intern"],
            required=False,     # soft constraint — fill when possible
            color="#EC4899",
            notes="2 seniors + 1 intern. Similar structure to Bronze. Soft fill — preferred but not required.",
        ),
        Rotation(
            rotation_id="Diamond",
            name="Diamond Service",
            abbrev="Diam",
            rot_type=RotationType.IP,
            pattern=RotationPattern.STANDARD,
            senior_capacity=0,
            intern_capacity=0,
            min_block_weeks=1,
            max_block_weeks=1,
            eligible_levels=[],
            required=False,
            active=False,       # placeholder — not in current staffing plan
            color="#6B7280",
            notes="Placeholder. Not in current staffing plan. Set capacity to activate.",
        ),
        Rotation(
            rotation_id="Clinic",
            name="Continuity Clinic",
            abbrev="Clin",
            rot_type=RotationType.OP,
            pattern=RotationPattern.CLINIC,
            senior_capacity=0,  # dynamic — 1/6 of resident pool each week
            intern_capacity=0,
            min_block_weeks=1,
            max_block_weeks=1,
            eligible_levels=["senior", "intern"],
            required=True,
            color="#14B8A6",
            notes=(
                "Continuity Clinic: each resident attends 1 out of every 6 weeks. "
                "Distribution target: 14-14-13-14-14-13 residents per clinic week "
                "within each 6-week cycle. Clinic weeks should be evenly spread "
                "across PGY1, PGY2, PGY3."
            ),
        ),
        Rotation(
            rotation_id="OP",
            name="Outpatient / Elective",
            abbrev="OP",
            rot_type=RotationType.OP,
            pattern=RotationPattern.STANDARD,
            senior_capacity=0,  # remainder after IP + clinic
            intern_capacity=0,
            min_block_weeks=1,
            max_block_weeks=4,
            eligible_levels=["senior", "intern"],
            required=False,
            color="#86EFAC",
            notes="Placeholder for all outpatient/elective rotations. Fills remaining weeks.",
        ),
        Rotation(
            rotation_id="Jeopardy",
            name="Jeopardy (Backup)",
            abbrev="Jeop",
            rot_type=RotationType.BACKUP,
            pattern=RotationPattern.BACKUP,
            senior_capacity=1,
            intern_capacity=1,
            min_block_weeks=1,
            max_block_weeks=1,
            eligible_levels=["senior", "intern"],
            required=False,
            color="#D97706",
            notes=(
                "Backup/call role. Can be pulled from IP or OP blocks. "
                "NEVER pulled from Clinic. 1 senior + 1 intern."
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Default Rotator Programs (aggregate config, kept for reference/UI)
# ---------------------------------------------------------------------------

def default_rotator_programs() -> list[RotatorProgram]:
    return [
        RotatorProgram(
            specialty="Neurology",
            total_rotators=6,
            months_inpatient=4,
            eligible_rotation_ids=["SLUH", "VA", "MICU"],
            slot_level="intern",
            max_simultaneous=1,
            blackout_months=[6],
            notes="6 neurology residents. 4 months IP each across SLUH/VA/MICU. Block June.",
        ),
        RotatorProgram(
            specialty="Emergency Medicine",
            total_rotators=8,
            months_inpatient=1,
            eligible_rotation_ids=["MICU"],
            slot_level="intern",
            max_simultaneous=1,
            blackout_months=[],
            notes="8 EM residents, 1 month MICU each. Only 1 at a time.",
        ),
        RotatorProgram(
            specialty="Anesthesia",
            total_rotators=10,
            months_inpatient=2,
            eligible_rotation_ids=["MICU", "SLUH"],
            slot_level="intern",
            max_simultaneous=1,
            blackout_months=[],
            notes="10 anesthesia residents, 2 months each (1 MICU + 1 SLUH). Max 1 per rotation.",
        ),
        RotatorProgram(
            specialty="Psychiatry",
            total_rotators=8,
            months_inpatient=1,
            eligible_rotation_ids=["VA"],
            slot_level="intern",
            max_simultaneous=1,
            blackout_months=[6],
            notes="8 psychiatry residents, 1 month VA each. Only 1 at a time. Block June.",
        ),
    ]


# ---------------------------------------------------------------------------
# Named rotator residents
# ---------------------------------------------------------------------------

def default_rotator_residents() -> list[Resident]:
    """
    Named residents from external training programs that rotate through
    our IP services.  Uses pgy_year=1 so they fill intern-level slots
    on MK teams (Luigi/Peach/Yoshi/Bowser) and MICU intern positions.

    Naming convention matches the reference spreadsheet:
      neuro1–neuro6, em1–em8, anes1–anes10, psy1–psy8
    """
    rotators = []
    specs = [
        # (prefix, count, long_name)
        ("neuro", 6,  "Neurology"),
        ("em",    8,  "Emergency Medicine"),
        ("anes",  10, "Anesthesia"),
        ("psy",   8,  "Psychiatry"),
    ]
    for prefix, count, long_name in specs:
        for i in range(1, count + 1):
            rotators.append(Resident(
                resident_id=f"{prefix}{i}",
                name=f"{long_name} {i}",
                pgy_year=1,          # fills intern-level slots
                resident_type="rotator",
                notes=f"{long_name} rotator — monthly IP blocks",
            ))
    return rotators


# ---------------------------------------------------------------------------
# Rotator pre-scheduling
# ---------------------------------------------------------------------------

def _find_block(
    start: int,
    n: int,
    max_w: int,
    blackout: set | None = None,
) -> tuple[int, int] | None:
    """
    Find the first window of n calendar-consecutive weeks starting at or after
    'start' such that no week in the window is a blackout week.

    If a proposed window overlaps a blackout week, the cursor is advanced past
    that blackout and a new candidate window is tried.

    Returns (first_week, last_week) or None if the window would exceed max_w.
    """
    blackout = blackout or set()
    w = start
    while True:
        # Advance start past any leading blackout week
        while w in blackout:
            w += 1
        block_end = w + n - 1
        if block_end > max_w:
            return None
        # Check for blackout weeks inside the proposed window
        conflict = next((bw for bw in sorted(blackout) if w <= bw <= block_end), None)
        if conflict is None:
            return (w, block_end)
        # Skip past the conflicting blackout and retry
        w = conflict + 1


def schedule_rotators(
    rotator_residents: list[Resident],
    academic_year: AcademicYear,
    weeks_per_block: int = 4,     # 1 month ≈ 4 weeks
    june_block_start: int = 44,   # weeks ≥ this are treated as "June" — block for neuro/psych
) -> list[Assignment]:
    """
    Generate pre-scheduled Assignment objects for all named rotators.

    Algorithm: sequential stagger with per-program, per-rotation cursors.
    Each program's rotators go through their rotation sequence one at a time;
    the rotation cursor advances after each 4-week block so max 1 rotator
    per program is at each rotation simultaneously.

    Multiple programs CAN overlap at the same rotation simultaneously
    (they fill different intern slots).

    Blackout weeks (e.g. July 4 ramp, winter break) are never included in a
    rotator block — the cursor skips past them automatically.

    Returns a list of Assignment objects ready to be injected into the solver.
    """
    max_w    = academic_year.total_weeks
    blackout = set(academic_year.blackout_weeks)
    assignments: list[Assignment] = []

    def _rotators_by_prefix(prefix: str) -> list[Resident]:
        rs = [r for r in rotator_residents if r.resident_id.startswith(prefix)]
        rs.sort(key=lambda r: int(r.resident_id[len(prefix):]))
        return rs

    def _stagger(rotators, rotation_sequence, specialty, start_w=3,
                 block_weeks=weeks_per_block, cutoff=max_w):
        """
        Schedule each rotator through their rotation_sequence.
        rot_cursors: independent per-rotation cursor for this program.
        Each rotator's blocks are strictly sequential (no self-overlap).
        Blackout weeks are skipped by _find_block so no block straddles a vacation.
        """
        rot_cursors: dict[str, int] = {r: start_w for r in rotation_sequence}
        for res in rotators:
            earliest = start_w
            for rot_id in rotation_sequence:
                cursor = max(rot_cursors.get(rot_id, start_w), earliest)
                block = _find_block(cursor, block_weeks, cutoff, blackout=blackout)
                if block:
                    assignments.append(Assignment(
                        resident_id=res.resident_id,
                        rotation_id=rot_id,
                        start_week=block[0],
                        end_week=block[1],
                        is_rotator_slot=True,
                        rotator_specialty=specialty,
                    ))
                    rot_cursors[rot_id] = block[1] + 1
                    earliest = block[1] + 1

    # ── EM: 8 rotators × 1 month MICU each, no June block ─────────────────
    _stagger(_rotators_by_prefix("em"), ["MICU"], "Emergency Medicine", start_w=3)

    # ── Psychiatry: 8 rotators × 1 month VA each, block June ───────────────
    # cutoff = june_block_start - 1 so no block starts in/after June
    _stagger(_rotators_by_prefix("psy"), ["VA"], "Psychiatry",
             start_w=3, cutoff=june_block_start - 1)

    # ── Anesthesia: 10 rotators × (1 MICU + 1 SLUH) each ──────────────────
    # Rotation sequence: MICU first, then SLUH (can overlap across residents)
    _stagger(_rotators_by_prefix("anes"), ["MICU", "SLUH"], "Anesthesia", start_w=3)

    # ── Neurology: 6 rotators × 4 months (SLUH→VA→MICU→SLUH), block June ──
    _stagger(_rotators_by_prefix("neuro"),
             ["SLUH", "VA", "MICU", "SLUH"], "Neurology",
             start_w=3, cutoff=june_block_start - 1)

    return assignments


# ---------------------------------------------------------------------------
# Default Resident Roster (IM program only — rotators handled separately)
# ---------------------------------------------------------------------------

def default_residents() -> list[Resident]:
    """
    Placeholder roster for the IM training program.
    Replace via Configuration page upload or manual entry.
    Structure: 18 PGY3 categorical, 18 PGY2 categorical, 19 PGY2 preliminary,
    and 31 PGY1 interns (mix of categorical and preliminary).
    """
    residents = []
    rid = 1

    # PGY3 seniors (categorical) — 18
    pgy3_names = [
        "Adams, J.", "Baker, M.", "Chen, L.", "Davis, R.", "Evans, S.",
        "Foster, K.", "Garcia, T.", "Harris, N.", "Ivanov, P.", "Johnson, C.",
        "Kim, H.", "Lopez, A.", "Martinez, B.", "Nelson, D.", "Ortiz, F.",
        "Patel, G.", "Quinn, E.", "Roberts, I.",
    ]
    for name in pgy3_names:
        residents.append(Resident(
            resident_id=f"R{rid:03d}",
            name=name,
            pgy_year=3,
            resident_type="categorical",
        ))
        rid += 1

    # PGY2 seniors (categorical) — 18
    pgy2_cat_names = [
        "Stone, W.", "Turner, V.", "Upton, X.", "Vance, Y.", "Walsh, Z.",
        "Xavier, A.", "Young, B.", "Zhang, C.", "Allen, D.", "Bennett, E.",
        "Castro, F.", "Dixon, G.", "Ellis, H.", "Flynn, I.", "Grant, J.",
        "Hughes, K.", "Ingram, L.", "James, M.",
    ]
    for name in pgy2_cat_names:
        residents.append(Resident(
            resident_id=f"R{rid:03d}",
            name=name,
            pgy_year=2,
            resident_type="categorical",
        ))
        rid += 1

    # PGY2 preliminary seniors — 19 (to reach ~55 seniors total)
    pgy2_prelim_names = [
        "Kent, N.", "Lane, O.", "Moore, P.", "Nash, Q.", "Owen, R.",
        "Park, S.", "Reid, T.", "Scott, U.", "Todd, V.", "Uhl, W.",
        "Voss, X.", "Ward, Y.", "Xu, Z.", "Yoon, A.", "Zane, B.",
        "Avery, C.", "Blake, D.", "Cole, E.", "Dean, F.",
    ]
    for name in pgy2_prelim_names:
        residents.append(Resident(
            resident_id=f"R{rid:03d}",
            name=name,
            pgy_year=2,
            resident_type="preliminary",
        ))
        rid += 1

    # PGY1 interns (categorical) — 20
    int_cat_names = [
        "Earl, G.", "Ford, H.", "Gill, I.", "Hall, J.", "Iyer, K.",
        "Joel, L.", "Katz, M.", "Lowe, N.", "Mack, O.", "Nair, P.",
        "Oz, Q.", "Paul, R.", "Qin, S.", "Ross, T.", "Shah, U.",
        "Tran, V.", "Ulm, W.", "Vega, X.", "Wren, Y.", "Xie, Z.",
    ]
    for name in int_cat_names:
        residents.append(Resident(
            resident_id=f"R{rid:03d}",
            name=name,
            pgy_year=1,
            resident_type="categorical",
        ))
        rid += 1

    # PGY1 interns (preliminary) — 11 (to reach ~31 interns total)
    int_prelim_names = [
        "Yale, A.", "Zhu, B.", "Ash, C.", "Bay, D.", "Cox, E.",
        "Day, F.", "Elm, G.", "Fay, H.", "Guy, I.", "Haj, J.",
        "Ibe, K.",
    ]
    for name in int_prelim_names:
        residents.append(Resident(
            resident_id=f"R{rid:03d}",
            name=name,
            pgy_year=1,
            resident_type="preliminary",
        ))
        rid += 1

    return residents


def default_all_residents() -> list[Resident]:
    """Return IM program residents + all named rotators combined."""
    return default_residents() + default_rotator_residents()
