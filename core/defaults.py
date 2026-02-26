"""
core/defaults.py
Pre-loaded default data derived from schedule_explorer.xlsx.
These are the starting values shown in Configuration; everything is editable in the UI.
"""
from __future__ import annotations
from .models import (
    Rotation, RotationType, RotationPattern,
    RotatorProgram, Resident, ResidentLevel, AcademicYear,
)


# ---------------------------------------------------------------------------
# Default Academic Year
# ---------------------------------------------------------------------------

def default_academic_year() -> AcademicYear:
    """
    48-week year.
    Blackout: week 1 (July 4 holiday ramp), week 25-26 (Dec 23–Jan 2 holiday).
    Adjust in Configuration page.
    """
    return AcademicYear(
        label="2025-2026",
        total_weeks=48,
        start_date="2025-07-07",
        blackout_weeks=[1, 25, 26],   # ~Jul 4 block + Dec 23–Jan 2
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
            senior_capacity=4,   # 5 assigned to cover 4 slots (days off built in)
            intern_capacity=3,   # 4 assigned to cover 3 slots nightly
            min_block_weeks=2,
            max_block_weeks=2,   # always 2-week blocks
            eligible_levels=["senior", "intern"],
            required=True,
            color="#8B5CF6",
            notes=(
                "14-day block. 5 seniors assigned → 4 slots (1 off per day for wellness). "
                "4 interns assigned → 3 nightly slots. "
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
# Default Rotator Programs
# ---------------------------------------------------------------------------

def default_rotator_programs() -> list[RotatorProgram]:
    return [
        RotatorProgram(
            specialty="Neurology",
            total_rotators=6,
            months_inpatient=4,         # 4 months total per year across all 6
            eligible_rotation_ids=["SLUH", "VA", "MICU"],
            max_simultaneous=1,
            blackout_months=[6],        # block June
            notes="6 neurology residents rotate through SLUH, VA, MICU. Block out June.",
        ),
        RotatorProgram(
            specialty="Emergency Medicine",
            total_rotators=8,
            months_inpatient=1,         # 1 month MICU each
            eligible_rotation_ids=["MICU"],
            max_simultaneous=1,
            blackout_months=[],
            notes="8 EM residents, 1 month MICU each. Stagger August through spring. Only 1 at a time.",
        ),
        RotatorProgram(
            specialty="Anesthesia",
            total_rotators=10,
            months_inpatient=2,         # 2 months each: 1 MICU + 1 SLUH
            eligible_rotation_ids=["MICU", "SLUH"],
            max_simultaneous=1,
            blackout_months=[],
            notes="10 anesthesia residents, 2 months each (1 MICU + 1 SLUH). Max 1 per rotation at a time.",
        ),
        RotatorProgram(
            specialty="Psychiatry",
            total_rotators=8,
            months_inpatient=1,         # 1 month VA each
            eligible_rotation_ids=["VA"],
            max_simultaneous=1,
            blackout_months=[6],        # block June
            notes="8 psychiatry residents, 1 month VA each. Only 1 at a time. Block June.",
        ),
    ]


# ---------------------------------------------------------------------------
# Default Resident Roster (placeholder — 55 seniors + 31 interns)
# ---------------------------------------------------------------------------

def default_residents() -> list[Resident]:
    """
    Placeholder roster. Replace via Configuration page upload or manual entry.
    Structure: 18 PGY3 categorical, 18 PGY2 categorical, 13 PGY2 preliminary-ish,
    and 31 PGY1 interns (mix of categorical and preliminary).
    Adjust to match actual program headcount.
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
