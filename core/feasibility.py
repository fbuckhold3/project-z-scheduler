"""
core/feasibility.py
Capacity calculator — answers "is this configuration mathematically feasible?"
BEFORE running the full solver.  Uses simple arithmetic + LP relaxation.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import numpy as np
from .models import Rotation, RotationType, RotationPattern, RotatorProgram, Resident, AcademicYear


# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------

@dataclass
class RotationDemand:
    rotation_id: str
    name: str
    abbrev: str
    rot_type: str
    required: bool
    # Resident-weeks demanded from our own pool (after rotator fill)
    senior_weeks_demanded: float
    intern_weeks_demanded: float
    # Gross demand before rotator credit
    senior_weeks_gross: float
    intern_weeks_gross: float
    # Rotator credit
    rotator_senior_credit: float
    rotator_intern_credit: float


@dataclass
class FeasibilityResult:
    feasible: bool
    # Aggregate
    n_seniors: int
    n_interns: int
    active_weeks: int
    # Available resident-weeks (total pool)
    senior_weeks_available: float
    intern_weeks_available: float
    # Clinic carve-out
    clinic_weeks_per_senior: float    # = active_weeks / 6
    clinic_weeks_per_intern: float
    senior_clinic_total: float
    intern_clinic_total: float
    # IP-available (after clinic carve)
    senior_ip_available: float
    intern_ip_available: float
    # IP demand (sum of hard required rotations)
    senior_ip_demanded: float
    intern_ip_demanded: float
    # Soft IP demand (optional rotations like Cardiology)
    senior_ip_soft: float
    intern_ip_soft: float
    # Gaps (positive = surplus, negative = deficit)
    senior_ip_gap: float
    intern_ip_gap: float
    # Per-rotation breakdown
    rotation_demands: list = field(default_factory=list)  # list[RotationDemand]
    # Rotator contribution summary
    rotator_summary: list = field(default_factory=list)   # list[dict]
    # Warnings / notes
    warnings: list = field(default_factory=list)          # list[str]
    # Clinic distribution
    clinic_target_per_week: float = 0.0
    clinic_pattern: list = field(default_factory=list)    # [14,14,13,14,14,13]


# ---------------------------------------------------------------------------
# Main feasibility check
# ---------------------------------------------------------------------------

def check_feasibility(
    residents: list[Resident],
    rotations: list[Rotation],
    rotator_programs: list[RotatorProgram],
    academic_year: AcademicYear,
) -> FeasibilityResult:
    """
    Run the capacity feasibility analysis.
    Steps:
      1. Count available resident-weeks by level.
      2. Compute rotator contributions per rotation.
      3. Compute per-rotation demand (gross - rotator credit).
      4. Apply 1/6 clinic carve-out.
      5. Check if remaining IP pool covers IP demand.
    """
    active_weeks = academic_year.active_weeks
    n_seniors = sum(1 for r in residents if r.is_senior)
    n_interns = sum(1 for r in residents if not r.is_senior)

    # Available resident-weeks (each resident × their active weeks)
    def res_active_weeks(res: Resident) -> int:
        start = max(res.start_week, 1)
        end = min(res.end_week, academic_year.total_weeks)
        resident_weeks = set(range(start, end + 1)) - set(academic_year.blackout_weeks)
        return len(resident_weeks)

    senior_weeks_avail = sum(res_active_weeks(r) for r in residents if r.is_senior)
    intern_weeks_avail  = sum(res_active_weeks(r) for r in residents if not r.is_senior)

    # -----------------------------------------------------------------------
    # Rotator contributions
    # -----------------------------------------------------------------------
    # Map rotation_id → (senior_credit, intern_credit)
    rotator_credit: dict[str, tuple[float, float]] = {}
    rotator_summary = []

    for prog in rotator_programs:
        total_weeks = prog.total_rotator_weeks()  # total resident-weeks this program covers
        # Allocate credit across eligible rotations
        n_eligible = len(prog.eligible_rotation_ids)
        if n_eligible == 0:
            continue
        # Simple even split for now (refined allocation can be done in solver)
        weeks_per_rot = total_weeks / n_eligible

        # Determine if rotators are senior-level or intern-level
        # All rotators are treated as "senior-equivalent" for slot-filling purposes
        for rot_id in prog.eligible_rotation_ids:
            sc, ic = rotator_credit.get(rot_id, (0.0, 0.0))
            # Rotators fill senior slots preferentially
            rotator_credit[rot_id] = (sc + weeks_per_rot, ic)

        rotator_summary.append({
            "specialty": prog.specialty,
            "rotators": prog.total_rotators,
            "months_each": prog.months_inpatient,
            "total_weeks": round(total_weeks, 1),
            "rotations": ", ".join(prog.eligible_rotation_ids),
            "max_simultaneous": prog.max_simultaneous,
        })

    # -----------------------------------------------------------------------
    # Per-rotation demand
    # -----------------------------------------------------------------------
    rotation_demands = []
    total_ip_senior_gross   = 0.0
    total_ip_intern_gross   = 0.0
    total_ip_senior_net     = 0.0
    total_ip_intern_net     = 0.0
    total_ip_soft_senior    = 0.0
    total_ip_soft_intern    = 0.0

    active_rotations = [r for r in rotations if r.active and r.pattern != RotationPattern.CLINIC]

    for rot in active_rotations:
        # Gross resident-weeks demanded (per week × active weeks)
        s_gross = rot.senior_capacity * active_weeks
        i_gross = rot.intern_capacity * active_weeks

        # Rotator credit
        r_sc, r_ic = rotator_credit.get(rot.rotation_id, (0.0, 0.0))
        # Don't over-credit (can't credit more than gross demand)
        r_sc = min(r_sc, s_gross)
        r_ic = min(r_ic, i_gross)

        s_net = max(0.0, s_gross - r_sc)
        i_net = max(0.0, i_gross - r_ic)

        rd = RotationDemand(
            rotation_id=rot.rotation_id,
            name=rot.name,
            abbrev=rot.abbrev,
            rot_type=rot.rot_type.value,
            required=rot.required,
            senior_weeks_gross=round(s_gross, 1),
            intern_weeks_gross=round(i_gross, 1),
            rotator_senior_credit=round(r_sc, 1),
            rotator_intern_credit=round(r_ic, 1),
            senior_weeks_demanded=round(s_net, 1),
            intern_weeks_demanded=round(i_net, 1),
        )
        rotation_demands.append(rd)

        # Only count IP-type rotations against the IP pool
        if rot.rot_type == RotationType.IP:
            if rot.required:
                total_ip_senior_gross += s_gross
                total_ip_intern_gross += i_gross
                total_ip_senior_net   += s_net
                total_ip_intern_net   += i_net
            else:
                total_ip_soft_senior  += s_net
                total_ip_soft_intern  += i_net

    # -----------------------------------------------------------------------
    # Clinic carve-out (1/6 of every resident's active weeks)
    # -----------------------------------------------------------------------
    clinic_per_senior = active_weeks / 6
    clinic_per_intern = active_weeks / 6
    senior_clinic_total = n_seniors * clinic_per_senior
    intern_clinic_total  = n_interns  * clinic_per_intern

    senior_ip_avail = senior_weeks_avail - senior_clinic_total
    intern_ip_avail  = intern_weeks_avail  - intern_clinic_total

    # -----------------------------------------------------------------------
    # Gaps
    # -----------------------------------------------------------------------
    senior_gap = senior_ip_avail - total_ip_senior_net
    intern_gap  = intern_ip_avail  - total_ip_intern_net

    feasible = (senior_gap >= 0) and (intern_gap >= 0)

    # -----------------------------------------------------------------------
    # Clinic distribution pattern: 14-14-13-14-14-13 per 6-week cycle
    # -----------------------------------------------------------------------
    total_residents = n_seniors + n_interns
    base = total_residents // 6
    remainder = total_residents % 6
    # remainder weeks get base+1, rest get base
    clinic_pattern = []
    for i in range(6):
        clinic_pattern.append(base + 1 if i < remainder else base)

    # -----------------------------------------------------------------------
    # Warnings
    # -----------------------------------------------------------------------
    warnings = []
    if senior_gap < 0:
        warnings.append(
            f"⚠️ Senior resident-week DEFICIT: need {abs(senior_gap):.0f} more senior-weeks "
            f"for required IP rotations (after clinic carve-out and rotator credit)."
        )
    if intern_gap < 0:
        warnings.append(
            f"⚠️ Intern resident-week DEFICIT: need {abs(intern_gap):.0f} more intern-weeks "
            f"for required IP rotations (after clinic carve-out and rotator credit)."
        )
    if senior_gap >= 0 and senior_gap < senior_weeks_avail * 0.05:
        warnings.append(
            "⚡ Senior pool is tight (< 5% buffer). Small schedule changes could cause infeasibility."
        )
    if intern_gap >= 0 and intern_gap < intern_weeks_avail * 0.05:
        warnings.append(
            "⚡ Intern pool is tight (< 5% buffer). Small schedule changes could cause infeasibility."
        )
    # Check for max-3-IP constraint pressure
    # In a 6-week window: max 3 IP weeks → 3/6 = 50% of time on IP per resident
    senior_ip_fraction = total_ip_senior_net / max(senior_weeks_avail, 1)
    intern_ip_fraction  = total_ip_intern_net  / max(intern_weeks_avail,  1)
    if senior_ip_fraction > 0.50:
        warnings.append(
            f"🚨 Senior IP fraction ({senior_ip_fraction:.1%}) exceeds 50%, which violates the "
            f"'max 3 IP weeks per 6-week window' hard constraint. Reduce IP demand or add residents."
        )
    if intern_ip_fraction > 0.50:
        warnings.append(
            f"🚨 Intern IP fraction ({intern_ip_fraction:.1%}) exceeds 50%, which violates the "
            f"'max 3 IP weeks per 6-week window' hard constraint. Reduce IP demand or add residents."
        )

    return FeasibilityResult(
        feasible=feasible,
        n_seniors=n_seniors,
        n_interns=n_interns,
        active_weeks=active_weeks,
        senior_weeks_available=round(senior_weeks_avail, 1),
        intern_weeks_available=round(intern_weeks_avail, 1),
        clinic_weeks_per_senior=round(clinic_per_senior, 1),
        clinic_weeks_per_intern=round(clinic_per_intern, 1),
        senior_clinic_total=round(senior_clinic_total, 1),
        intern_clinic_total=round(intern_clinic_total, 1),
        senior_ip_available=round(senior_ip_avail, 1),
        intern_ip_available=round(intern_ip_avail, 1),
        senior_ip_demanded=round(total_ip_senior_net, 1),
        intern_ip_demanded=round(total_ip_intern_net, 1),
        senior_ip_soft=round(total_ip_soft_senior, 1),
        intern_ip_soft=round(total_ip_soft_intern, 1),
        senior_ip_gap=round(senior_gap, 1),
        intern_ip_gap=round(intern_gap, 1),
        rotation_demands=rotation_demands,
        rotator_summary=rotator_summary,
        warnings=warnings,
        clinic_target_per_week=round(total_residents / 6, 1),
        clinic_pattern=clinic_pattern,
    )


# ---------------------------------------------------------------------------
# Helper: constraint pressure per rotation
# ---------------------------------------------------------------------------

def rotation_utilisation(
    demand: float, available: float
) -> tuple[float, str]:
    """
    Returns (fraction 0-1, status label).
    status: "ok", "tight", "over"
    """
    if available <= 0:
        return (1.0, "over")
    frac = demand / available
    if frac > 1.0:
        return (frac, "over")
    elif frac > 0.90:
        return (frac, "tight")
    else:
        return (frac, "ok")


def six_week_ip_pressure(
    n_residents: int,
    ip_weeks_per_resident_per_year: float,
    active_weeks: int,
) -> dict:
    """
    Check whether the average IP load per resident per 6-week window
    exceeds the hard cap of 3.
    """
    avg_ip_per_6wk = ip_weeks_per_resident_per_year * 6 / active_weeks
    return {
        "avg_ip_per_6wk_window": round(avg_ip_per_6wk, 2),
        "hard_cap": 3,
        "exceeds_cap": avg_ip_per_6wk > 3,
        "headroom": round(3 - avg_ip_per_6wk, 2),
    }
