"""
core/solver.py
Two-tier scheduling solver:
  1. Greedy (fast, ~seconds) — good for quick iteration
  2. CP-SAT (optimal, minutes) — Google OR-Tools constraint programming

Hard constraints encoded:
  - One rotation per resident per week
  - Rotation capacities (senior/intern slots)
  - Max 3 IP weeks in any 6-week sliding window
  - Min 1 Clinic week in every 6-week window (1/6 rule)
  - NF only in 2-week blocks; 6-week gap between NF blocks; no IP adjacent
  - ABABA: MICU/Bronze weeks 1,3,5 of every 5-week ABABA cycle
  - Blackout weeks = vacation
  - Level eligibility (seniors/interns only where appropriate)

Soft constraints (greedy: best-effort; CP-SAT: weighted penalties):
  - Spread IP load evenly across residents
  - Cardiology filled when possible
  - Stagger MICU/Bronze starts (Sun vs Mon) — tracked as metadata
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from .models import (
    AcademicYear, Assignment, Resident, ResidentLevel,
    Rotation, RotationPattern, RotationType, Schedule,
)

# ---------------------------------------------------------------------------
# Solver result
# ---------------------------------------------------------------------------

@dataclass
class SolveResult:
    success: bool
    schedule: Optional[Schedule]
    solver_used: str          # "greedy" or "cpsat"
    solve_time_sec: float
    n_violations: int
    violation_details: list = field(default_factory=list)
    status_message: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IP_PATTERNS = {RotationPattern.MK, RotationPattern.ABABA, RotationPattern.NF, RotationPattern.STANDARD}
IP_ROTATION_IDS_CACHE: set[str] = set()


def _ip_rotations(rotations: list[Rotation]) -> set[str]:
    return {r.rotation_id for r in rotations if r.rot_type == RotationType.IP and r.active}


def _level_ok(resident: Resident, rotation: Rotation) -> bool:
    lvl = resident.level.value  # "senior" or "intern"
    return lvl in rotation.eligible_levels


def _contiguous_runs(weeks: list[int]) -> list[list[int]]:
    """
    Split a sorted list of week numbers into sub-lists of calendar-consecutive weeks.
    Any gap ≥ 2 (e.g. caused by blackout weeks) starts a new run.

    Example: [2,3,4,...,24, 27,28,...,48] → [[2..24], [27..48]]
    """
    if not weeks:
        return []
    runs: list[list[int]] = []
    current: list[int] = [weeks[0]]
    for w in weeks[1:]:
        if w == current[-1] + 1:
            current.append(w)
        else:
            runs.append(current)
            current = [w]
    runs.append(current)
    return runs


# ---------------------------------------------------------------------------
# Greedy Solver
# ---------------------------------------------------------------------------

class GreedySolver:
    """
    Deterministic greedy solver.
    Pass: per-week, fill high-priority rotations first (NF, MICU, SLUH, VA, …),
    then clinic, then outpatient.

    Strategy:
      - Pre-compute ABABA anchor weeks for MICU/Bronze per resident
      - Pre-compute NF windows (every ~12-14 weeks, 2-week blocks)
      - Fill clinic slots (1 per 6-week block, per resident)
      - Fill remaining with SLUH/VA alternation, then OP
    """

    def __init__(
        self,
        residents: list[Resident],
        rotations: list[Rotation],
        academic_year: AcademicYear,
        seed: int = 42,
    ):
        self.residents = residents
        self.rotations = rotations
        self.ay = academic_year
        self.rng = random.Random(seed)
        self.rot_map: dict[str, Rotation] = {r.rotation_id: r for r in rotations}
        self.ip_ids = _ip_rotations(rotations)

        # week → {resident_id → rotation_id}
        self.grid: dict[int, dict[str, str]] = {
            w: {} for w in self.ay.all_weeks()
        }
        # week → {rotation_id → [resident_ids]}
        self.weekly_slots: dict[int, dict[str, list[str]]] = {
            w: {r.rotation_id: [] for r in rotations}
            for w in self.ay.all_weeks()
        }

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def solve(self, pre_assigned: list = None) -> SolveResult:
        """
        pre_assigned: optional list of Assignment objects to inject before
        the greedy solver runs.  Used to pin rotator schedules so the solver
        treats those slots as already filled.
        """
        t0 = time.time()

        # Active weeks exclude blackout/vacation weeks (e.g. July 4 ramp, winter break).
        # The grid is still initialised for ALL 48 calendar weeks so that
        # adjacency checks across a blackout boundary work correctly.
        active_weeks = self.ay.active_weeks()

        # Step 0: inject pre-assigned rotator blocks
        if pre_assigned:
            self._inject_assignments(pre_assigned)

        # Step 1: Clinic FIRST — guaranteed before any IP so every resident
        #         gets exactly one clinic slot per 6-week cycle with no conflicts.
        self._assign_clinic(active_weeks)

        # Step 2: NF — works around pre-placed clinic weeks
        self._assign_nf(active_weeks)

        # Step 3: ABABA (MICU and Bronze) — Clinic + NF weeks now in grid
        self._assign_ababa(active_weeks)

        # Step 4: Fill SLUH / VA / Gold / Cards
        self._assign_main_ip(active_weeks)

        # Step 6: Fill remaining with OP
        self._fill_op(active_weeks)

        # Build assignments list
        assignments = self._build_assignments()
        schedule = Schedule(
            academic_year=self.ay,
            assignments=assignments,
            generated_by="greedy",
        )

        violations, vdetails = self._check_violations(schedule)
        elapsed = time.time() - t0

        return SolveResult(
            success=True,
            schedule=schedule,
            solver_used="greedy",
            solve_time_sec=round(elapsed, 2),
            n_violations=violations,
            violation_details=vdetails,
            status_message=f"Greedy solve complete in {elapsed:.1f}s with {violations} soft violations.",
        )

    # ------------------------------------------------------------------
    # Pre-inject fixed assignments (rotators)
    # ------------------------------------------------------------------

    def _inject_assignments(self, assignments: list):
        """
        Write pre-scheduled assignments (e.g. rotator blocks) into the grid
        before the greedy solver runs.  Blackout weeks are handled in Step 1
        and will overwrite these if they coincide (unlikely with well-formed
        rotator schedules, but safe either way).
        """
        for a in assignments:
            for w in range(a.start_week, a.end_week + 1):
                if w in self.grid:
                    self.grid[w][a.resident_id] = a.rotation_id
                    if a.rotation_id in self.weekly_slots[w]:
                        if a.resident_id not in self.weekly_slots[w][a.rotation_id]:
                            self.weekly_slots[w][a.rotation_id].append(a.resident_id)

    # ------------------------------------------------------------------
    # ABABA (MICU + Bronze)
    # ------------------------------------------------------------------

    def _assign_ababa(self, active_weeks: list[int]):
        """
        Assign MICU and Bronze following ABABA pattern.
        ABABA = weeks 1, 3, 5 within a 5-week cycle are IP;
        weeks 2, 4 are B (OP/off — left unassigned here).
        Each senior is assigned to exactly one ABABA stream (MICU or Bronze).
        Interns to MICU only.
        """
        micu = self.rot_map.get("MICU")
        bronze = self.rot_map.get("Bronze")
        cards = self.rot_map.get("Cards")
        if not micu and not bronze and not cards:
            return

        # Rotators have pre-injected assignments; exclude from ABABA greedy fill
        seniors = [r for r in self.residents if r.is_senior
                   and r.resident_type != "rotator"]
        interns  = [r for r in self.residents if not r.is_senior
                    and r.resident_type != "rotator"]

        # How many senior "streams" are needed per week?
        # MICU: 4 seniors/week, Bronze: 2 seniors/week → 6 streams
        # Divide seniors into groups of 6; each group cycles ABABA
        # For simplicity: assign seniors to streams round-robin across 5-week cycles

        micu_cap   = micu.senior_capacity   if micu   else 0
        bronze_cap = bronze.senior_capacity if bronze else 0
        cards_cap  = cards.senior_capacity  if (cards and cards.active) else 0
        total_ababa_senior_slots = micu_cap + bronze_cap + cards_cap  # per week

        # Build 5-week cycles across the year
        # Weeks cycle: A B A B A (5 weeks), repeating
        # We want a set of residents to cover each A-week slot.
        # Simplification: every resident gets at most 1 ABABA assignment per 5-week cycle.

        if not active_weeks:
            return

        # Group active weeks into 5-week ABABA chunks, but NEVER let a chunk
        # span a calendar gap (blackout weeks).  Split active_weeks into
        # contiguous calendar runs first, then slice each run into 5-week cycles.
        # This prevents the holiday gap (e.g. weeks 25-26) from causing two
        # consecutive A-weeks at the boundary of adjacent cycles.
        runs = _contiguous_runs(active_weeks)
        cycles: list[list[int]] = []
        for run in runs:
            for i in range(0, len(run), 5):
                cycles.append(run[i:i + 5])

        # For MICU senior slots: pick 4 seniors per A-week (weeks 0,2,4 of each cycle)
        # For MICU intern slots: pick 2 interns per A-week
        # For Bronze senior slots: pick 2 seniors per A-week

        # Track how many ABABA A-weeks each resident has been assigned
        senior_ababa_count: dict[str, int] = {r.resident_id: 0 for r in seniors}
        intern_ababa_count: dict[str, int]  = {r.resident_id: 0 for r in interns}

        # ABABA week indices within a 5-week cycle (0-indexed)
        a_indices = [0, 2, 4]

        for cycle in cycles:
            # Track which residents are already assigned within this 5-week cycle.
            # Giving them priority for subsequent A-weeks promotes 2-3 consecutive
            # ABABA weeks per resident rather than isolated single-week assignments.
            cycle_sr: set[str] = set()
            cycle_in: set[str] = set()

            for ai in a_indices:
                if ai >= len(cycle):
                    break
                w = cycle[ai]

                # --- MICU seniors ---
                if micu and micu_cap > 0:
                    available = [
                        r for r in seniors
                        if self.grid[w].get(r.resident_id) is None
                        and _level_ok(r, micu)
                        and not self._ip_would_violate(r.resident_id, w)
                        # No consecutive MICU/Bronze weeks
                        and self.grid.get(w - 1, {}).get(r.resident_id) not in {"MICU", "Bronze"}
                        # No IP immediately adjacent to NF block
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    # Prefer residents already in this cycle (promotes full-cycle runs),
                    # then by fewest total ABABA weeks for fairness.
                    available.sort(key=lambda r: (
                        0 if r.resident_id in cycle_sr else 1,
                        senior_ababa_count[r.resident_id],
                    ))
                    chosen = available[:micu_cap]
                    for res in chosen:
                        self.grid[w][res.resident_id] = "MICU"
                        self.weekly_slots[w]["MICU"].append(res.resident_id)
                        senior_ababa_count[res.resident_id] += 1
                        cycle_sr.add(res.resident_id)

                # --- MICU interns ---
                if micu:
                    intern_cap = micu.intern_capacity
                    avail_i = [
                        r for r in interns
                        if self.grid[w].get(r.resident_id) is None
                        and _level_ok(r, micu)
                        and not self._ip_would_violate(r.resident_id, w)
                        # No consecutive MICU/Bronze weeks
                        and self.grid.get(w - 1, {}).get(r.resident_id) not in {"MICU", "Bronze"}
                        # No IP immediately adjacent to NF block
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    avail_i.sort(key=lambda r: (
                        0 if r.resident_id in cycle_in else 1,
                        intern_ababa_count[r.resident_id],
                    ))
                    chosen_i = avail_i[:intern_cap]
                    for res in chosen_i:
                        self.grid[w][res.resident_id] = "MICU"
                        self.weekly_slots[w]["MICU"].append(res.resident_id)
                        intern_ababa_count[res.resident_id] += 1
                        cycle_in.add(res.resident_id)

                # --- Bronze seniors ---
                if bronze and bronze_cap > 0:
                    available = [
                        r for r in seniors
                        if self.grid[w].get(r.resident_id) is None
                        and _level_ok(r, bronze)
                        and not self._ip_would_violate(r.resident_id, w)
                        # No consecutive MICU/Bronze/Cards weeks
                        and self.grid.get(w - 1, {}).get(r.resident_id) not in {"MICU", "Bronze", "Cards"}
                        # No IP immediately adjacent to NF block
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    available.sort(key=lambda r: (
                        0 if r.resident_id in cycle_sr else 1,
                        senior_ababa_count[r.resident_id],
                    ))
                    chosen = available[:bronze_cap]
                    for res in chosen:
                        self.grid[w][res.resident_id] = "Bronze"
                        self.weekly_slots[w]["Bronze"].append(res.resident_id)
                        senior_ababa_count[res.resident_id] += 1
                        cycle_sr.add(res.resident_id)

                # --- Cards seniors (A-weeks only, soft fill) ---
                if cards and cards.active and cards_cap > 0:
                    available = [
                        r for r in seniors
                        if self.grid[w].get(r.resident_id) is None
                        and _level_ok(r, cards)
                        and not self._ip_would_violate(r.resident_id, w)
                        # No consecutive MICU/Bronze/Cards weeks
                        and self.grid.get(w - 1, {}).get(r.resident_id) not in {"MICU", "Bronze", "Cards"}
                        # No IP immediately adjacent to NF block
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    available.sort(key=lambda r: (
                        0 if r.resident_id in cycle_sr else 1,
                        senior_ababa_count[r.resident_id],
                    ))
                    chosen = available[:cards_cap]
                    for res in chosen:
                        self.grid[w][res.resident_id] = "Cards"
                        self.weekly_slots[w]["Cards"].append(res.resident_id)
                        senior_ababa_count[res.resident_id] += 1
                        cycle_sr.add(res.resident_id)

                # --- Cards interns (A-weeks only, soft fill) ---
                if cards and cards.active and cards.intern_capacity > 0:
                    avail_i = [
                        r for r in interns
                        if self.grid[w].get(r.resident_id) is None
                        and _level_ok(r, cards)
                        and not self._ip_would_violate(r.resident_id, w)
                        and self.grid.get(w - 1, {}).get(r.resident_id) not in {"MICU", "Bronze", "Cards"}
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    avail_i.sort(key=lambda r: (
                        0 if r.resident_id in cycle_in else 1,
                        intern_ababa_count[r.resident_id],
                    ))
                    chosen_i = avail_i[:cards.intern_capacity]
                    for res in chosen_i:
                        self.grid[w][res.resident_id] = "Cards"
                        self.weekly_slots[w]["Cards"].append(res.resident_id)
                        intern_ababa_count[res.resident_id] += 1
                        cycle_in.add(res.resident_id)

        # B-weeks (indices 1, 3 of each 5-week cycle): fill MICU only.
        # These are the "off" weeks of the A-cohort; a fresh B-cohort staffs MICU
        # so that MICU has coverage every week.  The ababa_count tracking ensures
        # residents who just did an A-week are ranked lower and a different set is chosen.
        if micu:
            for cycle in cycles:
                for bi in [1, 3]:
                    if bi >= len(cycle):
                        break
                    w = cycle[bi]
                    _ababa_excl = {"MICU", "Bronze", "Cards"}
                    if micu_cap > 0:
                        available = [
                            r for r in seniors
                            if self.grid[w].get(r.resident_id) is None
                            and _level_ok(r, micu)
                            and not self._ip_would_violate(r.resident_id, w)
                            # No consecutive MICU/Bronze/Cards weeks (check both neighbours;
                            # A-weeks are already in the grid when B-weeks are filled)
                            and self.grid.get(w - 1, {}).get(r.resident_id) not in _ababa_excl
                            and self.grid.get(w + 1, {}).get(r.resident_id) not in _ababa_excl
                            # No IP immediately adjacent to NF block
                            and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                            and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                        ]
                        available.sort(key=lambda r: senior_ababa_count[r.resident_id])
                        for res in available[:micu_cap]:
                            self.grid[w][res.resident_id] = "MICU"
                            self.weekly_slots[w]["MICU"].append(res.resident_id)
                            senior_ababa_count[res.resident_id] += 1
                    intern_cap = micu.intern_capacity
                    if intern_cap > 0:
                        avail_i = [
                            r for r in interns
                            if self.grid[w].get(r.resident_id) is None
                            and _level_ok(r, micu)
                            and not self._ip_would_violate(r.resident_id, w)
                            # No consecutive MICU/Bronze/Cards weeks
                            and self.grid.get(w - 1, {}).get(r.resident_id) not in _ababa_excl
                            and self.grid.get(w + 1, {}).get(r.resident_id) not in _ababa_excl
                            # No IP immediately adjacent to NF block
                            and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                            and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                        ]
                        avail_i.sort(key=lambda r: intern_ababa_count[r.resident_id])
                        for res in avail_i[:intern_cap]:
                            self.grid[w][res.resident_id] = "MICU"
                            self.weekly_slots[w]["MICU"].append(res.resident_id)
                            intern_ababa_count[res.resident_id] += 1

    # ------------------------------------------------------------------
    # NF assignment
    # ------------------------------------------------------------------

    def _assign_nf(self, active_weeks: list[int]):
        """
        Assign Night Float in 2-week blocks.
        Constraints enforced here:
          - 2-week consecutive blocks only
          - At least 6 weeks between NF blocks for same resident
          - No IP week immediately before or after NF block
            (implemented by checking grid; since ABABA assigned first,
             we avoid placing NF adjacent to those weeks)
        """
        nf = self.rot_map.get("NF")
        if not nf or not nf.active:
            return

        # Rotators do not do NF
        seniors = [r for r in self.residents if r.is_senior
                   and r.resident_type != "rotator"]
        interns  = [r for r in self.residents if not r.is_senior
                    and r.resident_type != "rotator"]

        senior_cap = nf.senior_capacity  # 4 seniors per NF block
        intern_cap  = nf.intern_capacity   # 3 interns per NF block

        # Track last NF end week per resident
        last_nf_end: dict[str, int] = {}
        nf_gap = 6  # minimum gap weeks after NF block

        # Find valid 2-week windows for NF
        # NF should not be in week 1 of year (no prior week to check)
        # or last week (no following week)
        # We'll place NF blocks every ~12-14 weeks through the year
        # by scanning for available windows

        # Collect 2-week windows where NF could go
        valid_windows: list[tuple[int, int]] = []
        w_set = set(active_weeks)
        for i in range(len(active_weeks) - 1):
            w1 = active_weeks[i]
            w2 = active_weeks[i + 1]
            if w2 == w1 + 1:  # consecutive
                # Adjacent weeks — valid NF window
                valid_windows.append((w1, w2))

        if not valid_windows:
            return

        # Select non-overlapping 2-week windows covering the ENTIRE year.
        # step=2 ensures every week has NF coverage; the per-resident eligibility
        # checks below handle gaps and IP-adjacency for each individual resident.
        step = 2
        selected_windows: list[tuple[int, int]] = []
        last_selected = -step
        for (w1, w2) in valid_windows:
            if w1 >= last_selected + step:
                selected_windows.append((w1, w2))
                last_selected = w1

        for (w1, w2) in selected_windows:
            # --- Seniors ---
            eligible_s = [
                r for r in seniors
                if _level_ok(r, nf)
                and self.grid[w1].get(r.resident_id) is None
                and self.grid[w2].get(r.resident_id) is None
                and (last_nf_end.get(r.resident_id, -99) + nf_gap < w1)
                # no IP week directly before (w1-1) or after (w2+1)
                and self.grid.get(w1 - 1, {}).get(r.resident_id) not in self.ip_ids
                and self.grid.get(w2 + 1, {}).get(r.resident_id) not in self.ip_ids
            ]
            # Prioritize residents with fewer NF assignments
            eligible_s.sort(key=lambda r: sum(
                1 for w in active_weeks
                if self.grid[w].get(r.resident_id) == "NF"
            ))
            chosen_s = eligible_s[:senior_cap]  # senior_cap already reflects full team size

            for res in chosen_s:
                for w in (w1, w2):
                    self.grid[w][res.resident_id] = "NF"
                    self.weekly_slots[w]["NF"].append(res.resident_id)
                last_nf_end[res.resident_id] = w2

            # --- Interns ---
            eligible_i = [
                r for r in interns
                if _level_ok(r, nf)
                and self.grid[w1].get(r.resident_id) is None
                and self.grid[w2].get(r.resident_id) is None
                and (last_nf_end.get(r.resident_id, -99) + nf_gap < w1)
                and self.grid.get(w1 - 1, {}).get(r.resident_id) not in self.ip_ids
                and self.grid.get(w2 + 1, {}).get(r.resident_id) not in self.ip_ids
            ]
            eligible_i.sort(key=lambda r: sum(
                1 for w in active_weeks
                if self.grid[w].get(r.resident_id) == "NF"
            ))
            chosen_i = eligible_i[:intern_cap]  # intern_cap already reflects full team size
            for res in chosen_i:
                for w in (w1, w2):
                    self.grid[w][res.resident_id] = "NF"
                    self.weekly_slots[w]["NF"].append(res.resident_id)
                last_nf_end[res.resident_id] = w2

    # ------------------------------------------------------------------
    # Clinic assignment (1 per 6-week block)
    # ------------------------------------------------------------------

    def _assign_clinic(self, active_weeks: list[int]):
        """
        Assign clinic using six fixed groups with sizes [14, 14, 13, 14, 14, 13].

        Residents are assigned to groups in roster order:
          Group 0 → 14 residents → clinic on week index 0 of each 6-week cycle
          Group 1 → 14 residents → clinic on week index 1
          Group 2 → 13 residents → clinic on week index 2
          Group 3 → 14 residents → clinic on week index 3
          Group 4 → 14 residents → clinic on week index 4
          Group 5 → 13 residents → clinic on week index 5

        Any extra residents beyond the group total are placed in the last group.

        Runs FIRST (before NF / ABABA / main IP) so clinic is never squeezed out.

        If the preferred week is already occupied (e.g. by a rotator pre-assignment),
        we fall back to the nearest free week within the same cycle.
        """
        # Fixed group sizes as specified: 14-14-13-14-14-13
        GROUP_SIZES = [14, 14, 13, 14, 14, 13]

        non_rotators = [r for r in self.residents if r.resident_type != "rotator"]

        # Assign each resident to a clinic group in roster order
        group_of: dict[str, int] = {}
        idx = 0
        for g, size in enumerate(GROUP_SIZES):
            for _ in range(size):
                if idx < len(non_rotators):
                    group_of[non_rotators[idx].resident_id] = g
                    idx += 1
        # Any overflow residents (when roster > sum(GROUP_SIZES)) go to group 5
        while idx < len(non_rotators):
            group_of[non_rotators[idx].resident_id] = len(GROUP_SIZES) - 1
            idx += 1

        cycles = [active_weeks[i:i+6] for i in range(0, len(active_weeks), 6)]

        for res in non_rotators:
            base_pos = group_of.get(res.resident_id, 0)
            for cycle in cycles:
                if not cycle:
                    continue
                pos = min(base_pos, len(cycle) - 1)   # clamp for short last cycle
                preferred = cycle[pos]

                # Try the preferred week; fall back to nearest free week in cycle.
                candidate = None
                if self.grid[preferred].get(res.resident_id) is None:
                    candidate = preferred
                else:
                    for offset in range(1, len(cycle)):
                        for w in [preferred - offset, preferred + offset]:
                            if w in cycle and self.grid[w].get(res.resident_id) is None:
                                candidate = w
                                break
                        if candidate is not None:
                            break

                if candidate is not None:
                    self.grid[candidate][res.resident_id] = "Clinic"
                    self.weekly_slots[candidate]["Clinic"].append(res.resident_id)

    # ------------------------------------------------------------------
    # Main IP (SLUH, VA, Gold, Cards)
    # ------------------------------------------------------------------

    def _ip_would_violate(self, res_id: str, candidate_week: int) -> bool:
        """
        True if assigning an IP rotation to candidate_week would create > 3 IP
        in ANY 6-week sliding window that contains candidate_week.
        Checks all windows [start, start+5] where start ∈ [w-5, w].
        """
        for start in range(max(1, candidate_week - 5), candidate_week + 1):
            count = 0
            for wk in range(start, start + 6):
                if wk == candidate_week:
                    count += 1  # the candidate itself
                elif self.grid.get(wk, {}).get(res_id) in self.ip_ids:
                    count += 1
            if count > 3:
                return True
        return False

    def _ip_weeks_so_far(self, res_id: str, active_weeks: list[int]) -> int:
        """Total IP weeks assigned to a resident so far."""
        return sum(
            1 for w in active_weeks
            if self.grid.get(w, {}).get(res_id) in self.ip_ids
        )

    def _assign_main_ip(self, active_weeks: list[int]):
        """
        Fill SLUH, VA, Gold, Cardiology for weeks not yet assigned.
        Respects:
          - Capacity per week
          - Max 3 IP in any 6-week sliding window (proper check)
          - Level eligibility
        """
        sluh  = self.rot_map.get("SLUH")
        va    = self.rot_map.get("VA")
        gold  = self.rot_map.get("Gold")
        # Cards is ABABA — handled in _assign_ababa(), not here

        ip_rots = [r for r in [sluh, va, gold]
                   if r and r.active and r.rot_type == RotationType.IP]

        # Prevent direct SLUH↔VA transitions: no back-to-back sibling MK blocks
        MK_SIBLINGS: dict[str, str] = {"SLUH": "VA", "VA": "SLUH"}

        for w in active_weeks:
            # Determine capacity already filled by prior steps
            for rot in ip_rots:
                # How many slots remain?
                already_s = sum(
                    1 for rid in self.weekly_slots[w].get(rot.rotation_id, [])
                    if any(r.resident_id == rid and r.is_senior for r in self.residents)
                )
                already_i = sum(
                    1 for rid in self.weekly_slots[w].get(rot.rotation_id, [])
                    if any(r.resident_id == rid and not r.is_senior for r in self.residents)
                )
                need_s = max(0, rot.senior_capacity - already_s)
                need_i = max(0, rot.intern_capacity - already_i)

                if need_s == 0 and need_i == 0:
                    continue

                # Senior fill
                sibling = MK_SIBLINGS.get(rot.rotation_id)
                if need_s > 0:
                    candidates = [
                        r for r in self.residents
                        if r.is_senior
                        and r.resident_type != "rotator"
                        and _level_ok(r, rot)
                        and self.grid[w].get(r.resident_id) is None
                        and not self._ip_would_violate(r.resident_id, w)
                        # No direct SLUH↔VA back-to-back blocks
                        and self.grid.get(w - 1, {}).get(r.resident_id) != sibling
                        # No IP immediately adjacent to NF block
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    # Prioritize those with fewest total IP weeks (balance load)
                    candidates.sort(key=lambda r: self._ip_weeks_so_far(r.resident_id, active_weeks))
                    chosen = candidates[:need_s]
                    for res in chosen:
                        self.grid[w][res.resident_id] = rot.rotation_id
                        self.weekly_slots[w][rot.rotation_id].append(res.resident_id)

                # Intern fill
                if need_i > 0:
                    candidates = [
                        r for r in self.residents
                        if not r.is_senior
                        and r.resident_type != "rotator"
                        and _level_ok(r, rot)
                        and self.grid[w].get(r.resident_id) is None
                        and not self._ip_would_violate(r.resident_id, w)
                        # No direct SLUH↔VA back-to-back blocks
                        and self.grid.get(w - 1, {}).get(r.resident_id) != sibling
                        # No IP immediately adjacent to NF block
                        and self.grid.get(w - 1, {}).get(r.resident_id) != "NF"
                        and self.grid.get(w + 1, {}).get(r.resident_id) != "NF"
                    ]
                    candidates.sort(key=lambda r: self._ip_weeks_so_far(r.resident_id, active_weeks))
                    chosen = candidates[:need_i]
                    for res in chosen:
                        self.grid[w][res.resident_id] = rot.rotation_id
                        self.weekly_slots[w][rot.rotation_id].append(res.resident_id)

    # ------------------------------------------------------------------
    # Fill OP
    # ------------------------------------------------------------------

    def _fill_op(self, active_weeks: list[int]):
        """Assign 'OP' to any non-rotator resident-week not yet filled."""
        for w in active_weeks:
            for res in self.residents:
                if res.resident_type == "rotator":
                    continue   # rotators are absent from our schedule on non-rotation weeks
                if self.grid[w].get(res.resident_id) is None:
                    self.grid[w][res.resident_id] = "OP"
                    self.weekly_slots[w]["OP"].append(res.resident_id)

    # ------------------------------------------------------------------
    # Build Assignment objects
    # ------------------------------------------------------------------

    def _build_assignments(self) -> list[Assignment]:
        """
        Compress the week-by-week grid into Assignment blocks
        (consecutive weeks with same rotation → single Assignment).
        """
        assignments = []
        for res in self.residents:
            prev_rot = None
            block_start = None
            for w in range(1, self.ay.total_weeks + 1):
                rot_id = self.grid.get(w, {}).get(res.resident_id)
                if rot_id != prev_rot:
                    if prev_rot and block_start is not None:
                        if prev_rot != "VACATION":
                            assignments.append(Assignment(
                                resident_id=res.resident_id,
                                rotation_id=prev_rot,
                                start_week=block_start,
                                end_week=w - 1,
                            ))
                    prev_rot = rot_id
                    block_start = w
            # Close final block
            if prev_rot and prev_rot != "VACATION" and block_start is not None:
                assignments.append(Assignment(
                    resident_id=res.resident_id,
                    rotation_id=prev_rot,
                    start_week=block_start,
                    end_week=self.ay.total_weeks,
                ))
        return assignments

    # ------------------------------------------------------------------
    # Constraint checker
    # ------------------------------------------------------------------

    def _check_violations(self, schedule: Schedule) -> tuple[int, list[str]]:
        """Check hard constraint violations in the generated schedule."""
        violations = 0
        details = []
        active = self.ay.active_weeks()  # skip blackout weeks in constraint checks

        for res in self.residents:
            # Rotators follow their own external schedule; skip IM constraint checks
            if res.resident_type == "rotator":
                continue

            # --- Max 3 IP in any 6-week window ---
            for start in active:
                window = [w for w in range(start, start + 6) if w in active]
                ip_count = sum(
                    1 for w in window
                    if self.grid.get(w, {}).get(res.resident_id) in self.ip_ids
                )
                if ip_count > 3:
                    violations += 1
                    details.append(
                        f"{res.name}: {ip_count} IP weeks in window {start}–{start+5} (max 3)"
                    )
                    break  # one violation report per resident

            # --- Min 1 Clinic per 6-week window ---
            cycles = [active[i:i+6] for i in range(0, len(active), 6)]
            for cycle in cycles:
                clinic_count = sum(
                    1 for w in cycle
                    if self.grid.get(w, {}).get(res.resident_id) == "Clinic"
                )
                if clinic_count < 1:
                    violations += 1
                    details.append(
                        f"{res.name}: no Clinic week in cycle starting w{cycle[0]}"
                    )

            # --- NF 2-week block and 6-week gap ---
            nf_weeks = [
                w for w in active
                if self.grid.get(w, {}).get(res.resident_id) == "NF"
            ]
            for i, w in enumerate(nf_weeks):
                # Check for isolated NF week (not part of 2-week block)
                is_paired = (
                    (w + 1 in nf_weeks) or (w - 1 in nf_weeks)
                )
                if not is_paired:
                    violations += 1
                    details.append(f"{res.name}: isolated NF week at w{w} (must be 2-week block)")

        return violations, details[:50]  # cap at 50 for display


# ---------------------------------------------------------------------------
# CP-SAT Solver (wrapper)
# ---------------------------------------------------------------------------

class CPSATSolver:
    """
    Google OR-Tools CP-SAT solver.
    Falls back to greedy if ortools not available.
    """

    def __init__(
        self,
        residents: list[Resident],
        rotations: list[Rotation],
        academic_year: AcademicYear,
        time_limit_sec: int = 120,
    ):
        self.residents = residents
        self.rotations = rotations
        self.ay = academic_year
        self.time_limit = time_limit_sec
        self.ip_ids = _ip_rotations(rotations)

    def solve(self) -> SolveResult:
        try:
            from ortools.sat.python import cp_model
        except ImportError:
            return SolveResult(
                success=False,
                schedule=None,
                solver_used="cpsat",
                solve_time_sec=0.0,
                n_violations=0,
                status_message=(
                    "OR-Tools not installed. Run `pip install ortools` "
                    "or use the Greedy solver."
                ),
            )

        t0 = time.time()
        model = cp_model.CpModel()

        # Use only schedulable (non-blackout) weeks — all_weeks() would create
        # infeasible constraints because capacity == requirements can't be met
        # during vacation/ramp weeks when residents are unavailable.
        active_weeks = self.ay.active_weeks()
        n_weeks = len(active_weeks)
        w_idx = {w: i for i, w in enumerate(active_weeks)}

        residents = self.residents
        rotations = [r for r in self.rotations if r.active]
        rot_idx = {r.rotation_id: i for i, r in enumerate(rotations)}
        n_res = len(residents)
        n_rot = len(rotations)

        # ------------------------------------------------------------------
        # Decision variables: x[r, w, rot] ∈ {0, 1}
        # ------------------------------------------------------------------
        x = {}
        for ri, res in enumerate(residents):
            for wi, w in enumerate(active_weeks):
                for roti, rot in enumerate(rotations):
                    if _level_ok(res, rot):
                        x[ri, wi, roti] = model.new_bool_var(f"x_{ri}_{wi}_{roti}")

        def xvar(ri, wi, roti):
            return x.get((ri, wi, roti), None)

        # ------------------------------------------------------------------
        # C1: Each resident assigned to exactly one rotation per week
        # ------------------------------------------------------------------
        for ri in range(n_res):
            for wi in range(n_weeks):
                model.add_exactly_one(
                    [xvar(ri, wi, roti) for roti in range(n_rot)
                     if xvar(ri, wi, roti) is not None]
                )

        # ------------------------------------------------------------------
        # C2: Rotation capacity per week (senior and intern separately)
        # ------------------------------------------------------------------
        seniors_idx = [ri for ri, r in enumerate(residents) if r.is_senior]
        interns_idx  = [ri for ri, r in enumerate(residents) if not r.is_senior]

        for wi in range(n_weeks):
            for roti, rot in enumerate(rotations):
                if rot.pattern == RotationPattern.CLINIC:
                    continue  # handled separately
                s_vars = [xvar(ri, wi, roti) for ri in seniors_idx if xvar(ri, wi, roti) is not None]
                i_vars = [xvar(ri, wi, roti) for ri in interns_idx if xvar(ri, wi, roti) is not None]
                # Use <= (capacity ceiling) for all rotations — strict == causes INFEASIBLE
                # when NF, clinic, and IP-window constraints simultaneously apply.
                # Maximize fill via the objective function instead.
                if s_vars and rot.senior_capacity > 0:
                    model.add(sum(s_vars) <= rot.senior_capacity)
                if i_vars and rot.intern_capacity > 0:
                    model.add(sum(i_vars) <= rot.intern_capacity)

        # ------------------------------------------------------------------
        # C3: Max 3 IP weeks in any 6-week sliding window
        # ------------------------------------------------------------------
        ip_rot_indices = [roti for roti, rot in enumerate(rotations)
                          if rot.rot_type == RotationType.IP]

        for ri in range(n_res):
            for wi_start in range(n_weeks - 5):
                window = range(wi_start, wi_start + 6)
                ip_vars = [
                    xvar(ri, wi, roti)
                    for wi in window
                    for roti in ip_rot_indices
                    if xvar(ri, wi, roti) is not None
                ]
                if ip_vars:
                    model.add(sum(ip_vars) <= 3)

        # ------------------------------------------------------------------
        # C4: Min 1 clinic week per 6-week window
        # ------------------------------------------------------------------
        clinic_roti = rot_idx.get("Clinic")
        if clinic_roti is not None:
            for ri in range(n_res):
                cycles = [range(i, min(i + 6, n_weeks)) for i in range(0, n_weeks, 6)]
                for cycle in cycles:
                    c_vars = [xvar(ri, wi, clinic_roti) for wi in cycle
                               if xvar(ri, wi, clinic_roti) is not None]
                    if c_vars:
                        model.add(sum(c_vars) >= 1)

        # ------------------------------------------------------------------
        # C5: NF in 2-week consecutive blocks; 6-week gap; no IP adjacent
        # ------------------------------------------------------------------
        nf_roti = rot_idx.get("NF")
        if nf_roti is not None:
            for ri in range(n_res):
                # Must appear in pairs
                for wi in range(n_weeks - 1):
                    v_now  = xvar(ri, wi,     nf_roti)
                    v_next = xvar(ri, wi + 1, nf_roti)
                    if v_now is not None and v_next is not None:
                        # If on NF at wi, must also be on NF at wi+1 OR wi-1
                        if wi > 0:
                            v_prev = xvar(ri, wi - 1, nf_roti)
                            if v_prev is not None:
                                model.add_bool_or([v_prev, v_next, v_now.negated()])
                        else:
                            model.add_implication(v_now, v_next)

                # 6-week gap: if NF block ends at wi+1, next NF not until wi+7
                for wi in range(n_weeks - 1):
                    v_w1 = xvar(ri, wi,     nf_roti)
                    v_w2 = xvar(ri, wi + 1, nf_roti)
                    if v_w1 is not None and v_w2 is not None:
                        for gap_wi in range(wi + 2, min(wi + 8, n_weeks)):
                            v_gap = xvar(ri, gap_wi, nf_roti)
                            if v_gap is not None:
                                model.add_bool_or([v_w1.negated(), v_w2.negated(), v_gap.negated()])

                # No IP adjacent to NF
                for wi in range(n_weeks):
                    v_nf = xvar(ri, wi, nf_roti)
                    if v_nf is None:
                        continue
                    for adj_wi in [wi - 1, wi + 1]:
                        if 0 <= adj_wi < n_weeks:
                            for roti in ip_rot_indices:
                                if roti == nf_roti:
                                    continue
                                v_ip = xvar(ri, adj_wi, roti)
                                if v_ip is not None:
                                    model.add_bool_or([v_nf.negated(), v_ip.negated()])

        # ------------------------------------------------------------------
        # C6: Clinic distribution — equal counts per week within each cycle
        # ------------------------------------------------------------------
        if clinic_roti is not None:
            cycles_by_idx = [range(i, min(i + 6, n_weeks)) for i in range(0, n_weeks, 6)]
            for cycle in cycles_by_idx:
                for wi in cycle:
                    week_clinic_vars = [
                        xvar(ri, wi, clinic_roti) for ri in range(n_res)
                        if xvar(ri, wi, clinic_roti) is not None
                    ]
                    if week_clinic_vars:
                        # Target: (n_seniors + n_interns) / 6 ± 1
                        target = (n_res) // 6
                        model.add(sum(week_clinic_vars) >= target - 1)
                        model.add(sum(week_clinic_vars) <= target + 1)

        # ------------------------------------------------------------------
        # Objective: maximize total filled slots across all required rotations.
        # Since capacity constraints are now <= (not ==), the solver needs an
        # incentive to fill slots up to capacity.  Weight required rotations
        # higher than optional ones.
        # ------------------------------------------------------------------
        fill_terms = []
        for wi in range(n_weeks):
            for roti, rot in enumerate(rotations):
                weight = 2 if rot.required else 1
                for ri in range(n_res):
                    v = xvar(ri, wi, roti)
                    if v is not None:
                        fill_terms.append(weight * v)
        if fill_terms:
            model.maximize(sum(fill_terms))

        # ------------------------------------------------------------------
        # Solve
        # ------------------------------------------------------------------
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit
        solver.parameters.num_workers = 8
        solver.parameters.log_search_progress = False

        status = solver.solve(model)
        elapsed = time.time() - t0

        feasible_statuses = {cp_model.OPTIMAL, cp_model.FEASIBLE}
        if status not in feasible_statuses:
            return SolveResult(
                success=False,
                schedule=None,
                solver_used="cpsat",
                solve_time_sec=round(elapsed, 2),
                n_violations=0,
                status_message=(
                    f"CP-SAT: {solver.status_name(status)} after {elapsed:.1f}s. "
                    "Try relaxing constraints or use the Greedy solver."
                ),
            )

        # Extract solution
        assignments = []
        grid: dict[tuple[int, int], str] = {}
        for ri, res in enumerate(residents):
            prev_rot = None
            block_start_w = None
            for wi, w in enumerate(active_weeks):
                assigned_rot = None
                for roti, rot in enumerate(rotations):
                    v = xvar(ri, wi, roti)
                    if v is not None and solver.value(v) == 1:
                        assigned_rot = rot.rotation_id
                        break
                grid[(ri, wi)] = assigned_rot

                if assigned_rot != prev_rot:
                    if prev_rot and block_start_w is not None:
                        assignments.append(Assignment(
                            resident_id=res.resident_id,
                            rotation_id=prev_rot,
                            start_week=block_start_w,
                            end_week=active_weeks[wi - 1],
                        ))
                    prev_rot = assigned_rot
                    block_start_w = w

            if prev_rot and block_start_w is not None:
                assignments.append(Assignment(
                    resident_id=res.resident_id,
                    rotation_id=prev_rot,
                    start_week=block_start_w,
                    end_week=active_weeks[-1],
                ))

        schedule = Schedule(
            academic_year=self.ay,
            assignments=assignments,
            generated_by="cpsat",
            notes=f"CP-SAT status: {solver.status_name(status)}",
        )

        return SolveResult(
            success=True,
            schedule=schedule,
            solver_used="cpsat",
            solve_time_sec=round(elapsed, 2),
            n_violations=0,
            status_message=(
                f"CP-SAT: {solver.status_name(status)} in {elapsed:.1f}s. "
                f"Objective = {solver.objective_value:.0f}."
            ),
        )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_solver(
    residents: list[Resident],
    rotations: list[Rotation],
    academic_year: AcademicYear,
    method: str = "greedy",
    time_limit_sec: int = 120,
    seed: int = 42,
    pre_assigned: list = None,
) -> SolveResult:
    """
    Dispatch to the chosen solver.
    method: "greedy" | "cpsat"
    pre_assigned: list of Assignment objects to inject before solving
                  (used for rotator pre-scheduled blocks).
    """
    if method == "cpsat":
        return CPSATSolver(residents, rotations, academic_year, time_limit_sec).solve()
    else:
        return GreedySolver(residents, rotations, academic_year, seed=seed).solve(
            pre_assigned=pre_assigned
        )
