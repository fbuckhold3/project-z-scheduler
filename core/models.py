"""
core/models.py
All dataclasses and enums for the residency scheduling engine.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import json


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RotationType(str, Enum):
    IP = "Inpatient"
    OP = "Outpatient"
    MIXED = "Mixed"
    BACKUP = "Backup"


class RotationPattern(str, Enum):
    STANDARD = "standard"   # Plain weekly blocks (e.g. Outpatient, Gold)
    MK = "MK"               # MarioKart: 4 teams / 5 groups, 5-7 day stints
    ABABA = "ABABA"         # 7-day on, OP/off, repeat (MICU, Bronze)
    NF = "NF"               # Night Float: 14-day blocks, no IP adjacent
    CLINIC = "clinic"       # Continuity Clinic: 1 of every 6 weeks
    BACKUP = "backup"       # Jeopardy: pulled from IP/OP, never clinic


class ResidentLevel(str, Enum):
    INTERN = "intern"
    SENIOR = "senior"


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

@dataclass
class Rotation:
    rotation_id: str
    name: str
    abbrev: str
    rot_type: RotationType
    pattern: RotationPattern
    senior_capacity: int          # concurrent senior slots needed per week
    intern_capacity: int          # concurrent intern slots needed per week
    min_block_weeks: int = 1
    max_block_weeks: int = 4
    # Which levels may be assigned ("senior", "intern")
    eligible_levels: list = field(default_factory=lambda: ["senior", "intern"])
    required: bool = True         # False → soft (Cardiology, Jeopardy)
    color: str = "#4299E1"
    notes: str = ""
    active: bool = True

    def to_dict(self) -> dict:
        return {
            "rotation_id": self.rotation_id,
            "name": self.name,
            "abbrev": self.abbrev,
            "rot_type": self.rot_type.value,
            "pattern": self.pattern.value,
            "senior_capacity": self.senior_capacity,
            "intern_capacity": self.intern_capacity,
            "min_block_weeks": self.min_block_weeks,
            "max_block_weeks": self.max_block_weeks,
            "eligible_levels": self.eligible_levels,
            "required": self.required,
            "color": self.color,
            "notes": self.notes,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Rotation":
        d = dict(d)
        d["rot_type"] = RotationType(d["rot_type"])
        d["pattern"] = RotationPattern(d["pattern"])
        return cls(**d)


# ---------------------------------------------------------------------------
# Rotator program (residents from other programs)
# ---------------------------------------------------------------------------

@dataclass
class RotatorProgram:
    specialty: str
    total_rotators: int           # # of rotators in the program per year
    months_inpatient: int         # months each rotator spends on IP
    eligible_rotation_ids: list   # which of our rotations they may fill
    slot_level: str = "intern"    # "intern" or "senior" — which pool they fill
    max_simultaneous: int = 1     # max rotators at once
    blackout_months: list = field(default_factory=list)  # e.g. [6] = June
    notes: str = ""

    def total_rotator_weeks(self) -> float:
        """Approximate total resident-weeks covered by this program per year."""
        return self.total_rotators * self.months_inpatient * (48 / 12)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "RotatorProgram":
        return cls(**d)


# ---------------------------------------------------------------------------
# Resident
# ---------------------------------------------------------------------------

@dataclass
class Resident:
    resident_id: str
    name: str
    pgy_year: int                 # 1=intern, 2=PGY2, 3=PGY3
    resident_type: str            # "categorical", "preliminary"
    start_week: int = 1
    end_week: int = 48
    notes: str = ""

    @property
    def level(self) -> ResidentLevel:
        return ResidentLevel.INTERN if self.pgy_year == 1 else ResidentLevel.SENIOR

    @property
    def is_senior(self) -> bool:
        return self.pgy_year > 1

    def to_dict(self) -> dict:
        return {
            "resident_id": self.resident_id,
            "name": self.name,
            "pgy_year": self.pgy_year,
            "resident_type": self.resident_type,
            "start_week": self.start_week,
            "end_week": self.end_week,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Resident":
        return cls(**d)


# ---------------------------------------------------------------------------
# Academic Year
# ---------------------------------------------------------------------------

@dataclass
class AcademicYear:
    label: str = "2025-2026"
    total_weeks: int = 48
    start_date: str = "2025-07-07"          # First Monday of the academic year
    blackout_weeks: list = field(default_factory=list)  # e.g. [1, 25, 26] — vacation/ramp

    def all_weeks(self) -> list:
        """All 48 calendar weeks, including blackouts (used for grid init / display)."""
        return list(range(1, self.total_weeks + 1))

    def active_weeks(self) -> list:
        """Calendar weeks that are NOT blackout — only these receive scheduling."""
        bk = set(self.blackout_weeks)
        return [w for w in range(1, self.total_weeks + 1) if w not in bk]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "total_weeks": self.total_weeks,
            "start_date": self.start_date,
            "blackout_weeks": list(self.blackout_weeks),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "AcademicYear":
        allowed = {"label", "total_weeks", "start_date", "blackout_weeks"}
        d = {k: v for k, v in d.items() if k in allowed}
        return cls(**d)


# ---------------------------------------------------------------------------
# Assignment (a single rotation block for one resident)
# ---------------------------------------------------------------------------

@dataclass
class Assignment:
    resident_id: str
    rotation_id: str
    start_week: int
    end_week: int                 # inclusive
    is_rotator_slot: bool = False # True if filled by external rotator
    rotator_specialty: str = ""
    stagger_day: int = 0          # 0=Sunday start, 1=Monday (MICU stagger)
    notes: str = ""

    @property
    def weeks(self) -> list:
        return list(range(self.start_week, self.end_week + 1))

    @property
    def duration_weeks(self) -> int:
        return self.end_week - self.start_week + 1

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @classmethod
    def from_dict(cls, d: dict) -> "Assignment":
        return cls(**d)


# ---------------------------------------------------------------------------
# Schedule (full year schedule)
# ---------------------------------------------------------------------------

@dataclass
class Schedule:
    academic_year: AcademicYear
    assignments: list = field(default_factory=list)  # list[Assignment]
    generated_by: str = "manual"  # "manual", "greedy", "cpsat"
    notes: str = ""

    def get_resident_week(self, resident_id: str, week: int) -> Optional[Assignment]:
        for a in self.assignments:
            if a.resident_id == resident_id and a.start_week <= week <= a.end_week:
                return a
        return None

    def get_week_assignments(self, week: int) -> list:
        return [a for a in self.assignments if a.start_week <= week <= a.end_week]

    def resident_rotation_sequence(self, resident_id: str) -> list:
        """Return list of (week, rotation_id) for a resident, sorted by week."""
        seq = []
        for w in self.academic_year.all_weeks():
            a = self.get_resident_week(resident_id, w)
            seq.append((w, a.rotation_id if a else None))
        return seq

    def to_dataframe(self, residents: list, rotations: list):
        """Wide-format DataFrame: residents × weeks with rotation abbreviations."""
        import pandas as pd
        rot_map = {r.rotation_id: r.abbrev for r in rotations}
        rows = []
        for res in residents:
            row = {
                "Name": res.name,
                "PGY": res.pgy_year,
                "Type": res.resident_type,
                "Level": res.level.value,
            }
            for w in self.academic_year.all_weeks():
                col = f"W{w:02d}"
                a = self.get_resident_week(res.resident_id, w)
                row[col] = rot_map.get(a.rotation_id, a.rotation_id) if a else ""
            rows.append(row)
        return pd.DataFrame(rows)

    def to_dict(self) -> dict:
        return {
            "academic_year": self.academic_year.to_dict(),
            "assignments": [a.to_dict() for a in self.assignments],
            "generated_by": self.generated_by,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        ay = AcademicYear.from_dict(d["academic_year"])
        assignments = [Assignment.from_dict(a) for a in d["assignments"]]
        return cls(
            academic_year=ay,
            assignments=assignments,
            generated_by=d.get("generated_by", "manual"),
            notes=d.get("notes", ""),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, s: str) -> "Schedule":
        return cls.from_dict(json.loads(s))


# ---------------------------------------------------------------------------
# Constraint violation record (for display)
# ---------------------------------------------------------------------------

@dataclass
class ConstraintViolation:
    resident_id: str
    resident_name: str
    week: int
    rule: str
    severity: str  # "hard", "soft"
    description: str
