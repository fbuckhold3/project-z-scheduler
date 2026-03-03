"""
pages/4_Schedule_Builder.py
Build the resident schedule.

Tabs:
  ✏️ Interactive Editor — pre-built clinic grid (locked) + click-to-assign for
                          everything else; real-time validation; auto-fill button.
  ⚙️ Auto Solve        — rotator pre-schedule editor + one-click full solve.

Clinic is pre-built at page load with PGY-balanced groups (equal PGY3/2/1 per
group) and locked — it cannot be changed here.  All other weeks are editable.
48 weeks shown; vacation/blackout weeks appear blank like any other unassigned week.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st
from collections import defaultdict

from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_all_residents, schedule_rotators,
)
from core.models import AcademicYear, Assignment, Resident
from core.solver import run_solver

st.set_page_config(page_title="Schedule Builder", page_icon="🔧", layout="wide")


# ---------------------------------------------------------------------------
# Clinic helpers  (PGY-balanced groups, locked grid)
# ---------------------------------------------------------------------------

def _build_clinic_group_map(residents: list[Resident], n_groups: int = 6) -> dict[str, int]:
    """
    Assign each non-rotator resident to a clinic group (0–5) using round-robin
    within each PGY level, so every group has roughly equal PGY3 / PGY2 / PGY1.
    Returns {resident_id: group_index}.
    """
    non_rotators = [r for r in residents if r.resident_type != "rotator"]
    group_of: dict[str, int] = {}
    for pgy in (3, 2, 1):
        cohort = sorted(
            [r for r in non_rotators if r.pgy_year == pgy],
            key=lambda r: r.resident_id,
        )
        for i, res in enumerate(cohort):
            group_of[res.resident_id] = i % n_groups
    return group_of


def _compute_clinic_locked_grid(
    residents: list[Resident],
    ay: AcademicYear,
) -> dict[tuple[str, int], str]:
    """
    Pre-compute clinic assignments for all non-rotators across the full year.
    Returns {(resident_id, week): "Clinic"} — these are locked and cannot be
    overridden by manual edits in the interactive editor.
    """
    group_of = _build_clinic_group_map(residents)
    active_weeks = ay.active_weeks()
    cycles = [active_weeks[i:i+6] for i in range(0, len(active_weeks), 6)]
    locked: dict[tuple[str, int], str] = {}
    non_rotators = [r for r in residents if r.resident_type != "rotator"]
    for res in non_rotators:
        g = group_of.get(res.resident_id, 0)
        for cycle in cycles:
            if not cycle:
                continue
            pos = min(g, len(cycle) - 1)
            w = cycle[pos]
            locked[(res.resident_id, w)] = "Clinic"
    return locked


# ---------------------------------------------------------------------------
# Rotator pre-schedule helpers
# ---------------------------------------------------------------------------

def _assignments_to_df(assignments: list, res_map: dict) -> pd.DataFrame:
    rows = []
    for a in assignments:
        res = res_map.get(a.resident_id)
        rows.append({
            "Resident":   res.name if res else a.resident_id,
            "Program":    a.rotator_specialty or (res.notes if res else ""),
            "Rotation":   a.rotation_id,
            "Start Week": int(a.start_week),
            "End Week":   int(a.end_week),
        })
    return pd.DataFrame(rows)


def _df_to_assignments(df: pd.DataFrame, name_to_id: dict) -> list:
    result = []
    for _, row in df.iterrows():
        name = row.get("Resident")
        rot  = row.get("Rotation")
        sw   = row.get("Start Week")
        ew   = row.get("End Week")
        if not name or not rot or pd.isna(sw) or pd.isna(ew):
            continue
        rid = name_to_id.get(str(name))
        if not rid:
            continue
        result.append(Assignment(
            resident_id=rid,
            rotation_id=str(rot),
            start_week=int(sw),
            end_week=int(ew),
            is_rotator_slot=True,
            rotator_specialty=str(row.get("Program", "") or ""),
        ))
    return result


# ---------------------------------------------------------------------------
# Manual grid helpers  (interactive editor state)
# ---------------------------------------------------------------------------

def _manual_grid_to_assignments(
    manual_grid: dict[tuple[str, int], str],
) -> list[Assignment]:
    """Compress {(resident_id, week): rotation_id} into contiguous Assignment blocks."""
    res_weeks: dict[str, dict[int, str]] = defaultdict(dict)
    for (rid, w), rot_id in manual_grid.items():
        res_weeks[rid][w] = rot_id

    assignments: list[Assignment] = []
    for rid, week_rot in res_weeks.items():
        sorted_weeks = sorted(week_rot.keys())
        if not sorted_weeks:
            continue
        prev_rot    = week_rot[sorted_weeks[0]]
        block_start = sorted_weeks[0]
        prev_week   = sorted_weeks[0]

        for w in sorted_weeks[1:]:
            cur_rot = week_rot[w]
            if cur_rot == prev_rot and w == prev_week + 1:
                prev_week = w
                continue
            assignments.append(Assignment(
                resident_id=rid, rotation_id=prev_rot,
                start_week=block_start, end_week=prev_week,
            ))
            prev_rot    = cur_rot
            block_start = w
            prev_week   = w

        assignments.append(Assignment(
            resident_id=rid, rotation_id=prev_rot,
            start_week=block_start, end_week=prev_week,
        ))
    return assignments


def _validate_manual_grid(
    manual_grid: dict[tuple[str, int], str],
    residents: list,
    rotations: list,
    ay: AcademicYear,
) -> list[str]:
    """Basic constraint check on manual (non-clinic) assignments."""
    from core.solver import _ip_rotations
    ip_ids  = _ip_rotations(rotations)
    rot_map = {r.rotation_id: r for r in rotations}
    violations: list[str] = []

    for w in range(1, ay.total_weeks + 1):
        week_assign: dict[str, list[str]] = {}
        for (rid, wk), rot_id in manual_grid.items():
            if wk == w:
                week_assign.setdefault(rot_id, []).append(rid)
        for rot_id, rids in week_assign.items():
            rot = rot_map.get(rot_id)
            if not rot:
                continue
            sr = sum(1 for rid in rids if any(r.resident_id == rid and r.is_senior     for r in residents))
            ni = sum(1 for rid in rids if any(r.resident_id == rid and not r.is_senior for r in residents))
            if rot.senior_capacity > 0 and sr > rot.senior_capacity:
                violations.append(f"W{w:02d} {rot.abbrev}: {sr} seniors (max {rot.senior_capacity})")
            if rot.intern_capacity > 0 and ni > rot.intern_capacity:
                violations.append(f"W{w:02d} {rot.abbrev}: {ni} interns (max {rot.intern_capacity})")

    for res in residents:
        if res.resident_type == "rotator":
            continue
        for start in range(1, ay.total_weeks - 4):
            ip_count = sum(
                1 for wk in range(start, start + 6)
                if manual_grid.get((res.resident_id, wk)) in ip_ids
            )
            if ip_count > 3:
                violations.append(
                    f"{res.name}: {ip_count} IP weeks in W{start:02d}–W{start+5:02d} (max 3)"
                )
                break

    return violations[:40]


# ---------------------------------------------------------------------------
# Session state init
# ---------------------------------------------------------------------------

if "rotations" not in st.session_state:
    st.session_state.rotations        = default_rotations()
    st.session_state.residents        = default_all_residents()
    st.session_state.rotator_programs = default_rotator_programs()
    st.session_state.academic_year    = default_academic_year()
    st.session_state.schedule         = None
    st.session_state.feasibility      = None
    st.session_state.solve_result     = None

_rotators_in_state = [r for r in st.session_state.residents if r.resident_type == "rotator"]
if not _rotators_in_state:
    from core.defaults import default_rotator_residents as _drr
    st.session_state.residents = st.session_state.residents + _drr()

res_map            = {r.resident_id: r for r in st.session_state.residents}
rotator_res_list   = [r for r in st.session_state.residents if r.resident_type == "rotator"]
rotator_name_to_id = {r.name: r.resident_id for r in rotator_res_list}
rotator_names      = sorted(rotator_name_to_id.keys())
IP_ROTATION_IDS    = ["SLUH", "VA", "MICU", "Cards", "Gold"]

if "rotator_assignments" not in st.session_state:
    _pre = schedule_rotators(rotator_res_list, st.session_state.academic_year)
    st.session_state.rotator_assignments = _assignments_to_df(_pre, res_map)

# ── Clinic locked grid ────────────────────────────────────────────────────
if "clinic_locked_grid" not in st.session_state:
    st.session_state.clinic_locked_grid = _compute_clinic_locked_grid(
        st.session_state.residents,
        st.session_state.academic_year,
    )

# ── Manual (editable) grid ────────────────────────────────────────────────
if "manual_grid" not in st.session_state:
    st.session_state.manual_grid = {}

# Abbrev ↔ ID maps — Clinic excluded (it's locked, not in the dropdown)
active_rots  = [r for r in st.session_state.rotations if r.active and r.rotation_id != "Clinic"]
abbrev_to_id = {r.abbrev: r.rotation_id for r in active_rots}
id_to_abbrev = {r.rotation_id: r.abbrev  for r in active_rots}
rot_abbrev_opts = [""] + [r.abbrev for r in active_rots]

# ---------------------------------------------------------------------------
# Page header + tabs
# ---------------------------------------------------------------------------
st.title("🔧 Schedule Builder")
tab_editor, tab_solve = st.tabs(["✏️ Interactive Editor", "⚙️ Auto Solve"])


# ===========================================================================
# TAB 1: Interactive Editor
# ===========================================================================
with tab_editor:
    st.caption(
        "Clinic weeks (**Clin★**) are pre-assigned and locked — one week every 6 weeks "
        "per resident, PGY-balanced across 6 groups. "
        "Assign any other rotation by clicking a blank cell. "
        "Click **🚀 Auto-fill Remaining** to run the solver around your locked entries."
    )

    # ── Controls ──────────────────────────────────────────────────────────
    col_weeks, col_level, col_reset = st.columns([2, 2, 1])
    with col_weeks:
        week_range = st.select_slider(
            "View weeks",
            options=list(range(1, 49)),
            value=(1, 12),
        )
    with col_level:
        level_filter = st.multiselect(
            "Show residents",
            options=["PGY3", "PGY2", "PGY1"],
            default=["PGY3", "PGY2", "PGY1"],
        )
    with col_reset:
        st.write("")
        st.write("")
        if st.button("🗑️ Clear manual", help="Clear all manual (non-clinic) assignments"):
            st.session_state.manual_grid = {}
            st.rerun()

    pgy_filter = set()
    if "PGY3" in level_filter: pgy_filter.add(3)
    if "PGY2" in level_filter: pgy_filter.add(2)
    if "PGY1" in level_filter: pgy_filter.add(1)

    view_residents = [
        r for r in st.session_state.residents
        if r.resident_type != "rotator" and r.pgy_year in pgy_filter
    ]

    w_start, w_end = week_range
    view_weeks = list(range(w_start, w_end + 1))

    clinic_locked = st.session_state.clinic_locked_grid
    manual_grid   = st.session_state.manual_grid

    # ── Build editor DataFrame ─────────────────────────────────────────────
    rows = []
    for res in view_residents:
        row: dict = {"Resident": res.name, "PGY": f"PGY{res.pgy_year}"}
        for w in view_weeks:
            if clinic_locked.get((res.resident_id, w)):
                row[f"W{w:02d}"] = "Clin★"
            else:
                rot_id = manual_grid.get((res.resident_id, w))
                row[f"W{w:02d}"] = id_to_abbrev.get(rot_id, "") if rot_id else ""
        rows.append(row)

    editor_df = pd.DataFrame(rows) if rows else pd.DataFrame()

    # Options include "Clin★" so locked cells can display it without warnings
    editor_opts = ["", "Clin★"] + [r.abbrev for r in active_rots]
    col_config: dict = {
        "Resident": st.column_config.TextColumn("Resident", disabled=True, width="medium"),
        "PGY":      st.column_config.TextColumn("PGY",      disabled=True, width="small"),
    }
    for w in view_weeks:
        col_config[f"W{w:02d}"] = st.column_config.SelectboxColumn(
            f"W{w:02d}", options=editor_opts, width="small",
        )

    if editor_df.empty:
        st.info("No residents match the filter.")
    else:
        edited_grid = st.data_editor(
            editor_df,
            column_config=col_config,
            use_container_width=True,
            hide_index=True,
            key=f"editor_{w_start}_{w_end}_{''.join(map(str, sorted(pgy_filter)))}",
        )

        # Sync edits → manual_grid  (locked clinic cells are ignored)
        for i, res in enumerate(view_residents):
            for w in view_weeks:
                if clinic_locked.get((res.resident_id, w)):
                    continue  # locked — skip
                col = f"W{w:02d}"
                val = str(edited_grid.iloc[i].get(col, "") or "").strip()
                if val and val not in ("", "Clin★"):
                    rot_id = abbrev_to_id.get(val)
                    if rot_id:
                        manual_grid[(res.resident_id, w)] = rot_id
                    else:
                        manual_grid.pop((res.resident_id, w), None)
                else:
                    manual_grid.pop((res.resident_id, w), None)

    # ── Status + clinic breakdown ─────────────────────────────────────────
    n_clinic = len(clinic_locked)
    n_manual = len(manual_grid)
    st.caption(f"🔒 **{n_clinic}** clinic weeks locked  ·  ✏️ **{n_manual}** manual assignments")

    with st.expander("📋 Clinic group breakdown", expanded=False):
        group_map = _build_clinic_group_map(st.session_state.residents)
        non_rot   = [r for r in st.session_state.residents if r.resident_type != "rotator"]
        groups: dict[int, list] = defaultdict(list)
        for res in non_rot:
            groups[group_map.get(res.resident_id, 0)].append(res)

        cols = st.columns(3)
        for g in range(6):
            with cols[g % 3]:
                members = groups[g]
                pc = {3: 0, 2: 0, 1: 0}
                for r in members:
                    pc[r.pgy_year] = pc.get(r.pgy_year, 0) + 1
                st.markdown(
                    f"**Group {g+1}** — {len(members)} residents  \n"
                    f"PGY3: {pc[3]} · PGY2: {pc[2]} · PGY1: {pc[1]}"
                )

    # ── Validation ────────────────────────────────────────────────────────
    if n_manual > 0:
        violations = _validate_manual_grid(
            manual_grid,
            st.session_state.residents,
            st.session_state.rotations,
            st.session_state.academic_year,
        )
        if violations:
            with st.expander(f"⚠️ {len(violations)} violation(s)", expanded=True):
                for v in violations:
                    st.markdown(f"- {v}")
        else:
            st.success("✅ No constraint violations.")

    # ── Auto-fill ─────────────────────────────────────────────────────────
    st.markdown("---")
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        autofill = st.button("🚀 Auto-fill Remaining", type="primary", use_container_width=True)
    with col_info:
        st.info("Clinic + manual assignments locked. Greedy fills everything else.")

    if autofill:
        with st.spinner("Running Greedy solver…"):
            merged: dict[tuple[str, int], str] = {**clinic_locked, **manual_grid}
            pre_from_grid = _manual_grid_to_assignments(merged)
            _build_df = st.session_state.rotator_assignments
            pre_from_rotators = _df_to_assignments(_build_df, rotator_name_to_id)
            pre_assigned = pre_from_rotators + pre_from_grid

            result = run_solver(
                residents=st.session_state.residents,
                rotations=st.session_state.rotations,
                academic_year=st.session_state.academic_year,
                method="greedy",
                time_limit_sec=120,
                seed=42,
                pre_assigned=pre_assigned,
            )
        st.session_state.solve_result = result
        if result.success:
            st.session_state.schedule = result.schedule
            st.success(result.status_message)
            st.info("Navigate to **📅 Schedule Viewer** to explore.")
        else:
            st.error(result.status_message)


# ===========================================================================
# TAB 2: Auto Solve
# ===========================================================================
with tab_solve:
    st.caption("Generate a complete schedule from scratch using Greedy or CP-SAT.")

    col1, col2 = st.columns([1, 2])
    with col1:
        method = st.radio(
            "Solver",
            options=["greedy", "cpsat"],
            format_func=lambda x: "⚡ Greedy (seconds)" if x == "greedy" else "🧮 CP-SAT (minutes)",
            index=0,
        )
    with col2:
        if method == "greedy":
            seed = st.number_input("Random seed", min_value=0, value=42, step=1)
            time_limit = None
            st.info("**Greedy** fills in priority order. Fast, good for iteration.")
        else:
            time_limit = st.slider("Time limit (s)", 30, 600, 120, 30)
            seed = 42
            st.info("**CP-SAT** uses OR-Tools constraint programming. Requires `ortools`.")

    if st.session_state.feasibility is None:
        st.warning("⚠️ Run **Capacity Calculator** (page 2) first.")
    elif not st.session_state.feasibility.feasible:
        st.error("❌ Feasibility FAILED — fix configuration before building.")

    edited_df = st.session_state.rotator_assignments
    st.markdown("---")
    with st.expander("📋 External Rotator Pre-Schedule", expanded=True):
        st.info(
            "Blocks from partner programs (Neurology, EM, Anesthesia, Psychiatry). "
            "Locked before the solver runs and count against rotation capacity."
        )
        col_r, _ = st.columns([1, 3])
        with col_r:
            if st.button("↺ Reset to auto-generated", key="reset_rotators"):
                _pre2 = schedule_rotators(rotator_res_list, st.session_state.academic_year)
                st.session_state.rotator_assignments = _assignments_to_df(_pre2, res_map)
                st.session_state.pop("rotator_editor", None)
                st.rerun()

        edited_df = st.data_editor(
            st.session_state.rotator_assignments,
            column_config={
                "Resident":   st.column_config.SelectboxColumn(options=rotator_names),
                "Program":    st.column_config.TextColumn(disabled=True),
                "Rotation":   st.column_config.SelectboxColumn(options=IP_ROTATION_IDS),
                "Start Week": st.column_config.NumberColumn(min_value=1, max_value=48, step=1),
                "End Week":   st.column_config.NumberColumn(min_value=1, max_value=48, step=1),
            },
            use_container_width=True, hide_index=True, num_rows="dynamic",
            key="rotator_editor",
        )
        try:
            _d = edited_df if not edited_df.empty else st.session_state.rotator_assignments
            _vr = _d.dropna(subset=["Resident", "Rotation"])
            st.caption(f"**{len(_vr)}** block(s) across **{_vr['Resident'].nunique()}** rotator(s)")
        except Exception:
            st.caption(f"**{len(st.session_state.rotator_assignments)}** block(s) defined.")

    st.markdown("---")
    col_btn2, _ = st.columns([1, 3])
    with col_btn2:
        build_clicked = st.button("🚀 Build Schedule", type="primary", use_container_width=True)

    if build_clicked:
        with st.spinner(f"Running {method.upper()} solver…"):
            _bd = edited_df if not edited_df.empty else st.session_state.rotator_assignments
            pre2 = _df_to_assignments(_bd, rotator_name_to_id)
            # Inject locked clinic so solver respects them
            pre2 += _manual_grid_to_assignments(st.session_state.clinic_locked_grid)

            result2 = run_solver(
                residents=st.session_state.residents,
                rotations=st.session_state.rotations,
                academic_year=st.session_state.academic_year,
                method=method,
                time_limit_sec=time_limit or 120,
                seed=int(seed),
                pre_assigned=pre2,
            )
        st.session_state.solve_result = result2
        if result2.success:
            st.session_state.schedule = result2.schedule
            st.success(result2.status_message)
        else:
            st.error(result2.status_message)

    sr = st.session_state.solve_result
    if sr:
        st.markdown("---")
        st.subheader("Solve Summary")
        mc = st.columns(4)
        mc[0].metric("Solver",     sr.solver_used.upper())
        mc[1].metric("Time",       f"{sr.solve_time_sec}s")
        mc[2].metric("Status",     "✅ Success" if sr.success else "❌ Failed")
        mc[3].metric("Violations", sr.n_violations)

        if sr.n_violations > 0:
            st.markdown("**Violations:**")
            for v in sr.violation_details[:30]:
                st.markdown(f"- {v}")
            if sr.n_violations > 30:
                st.caption(f"… and {sr.n_violations - 30} more.")

        if sr.success and sr.schedule:
            st.markdown("---")
            st.success("✅ Navigate to **📅 Schedule Viewer** to explore.")
            rot_map_local = {r.rotation_id: r for r in st.session_state.rotations}
            weekly_counts = []
            for w in sr.schedule.academic_year.all_weeks()[:24]:
                counts: dict = {"Week": f"W{w:02d}"}
                for a in sr.schedule.get_week_assignments(w):
                    ab = rot_map_local[a.rotation_id].abbrev if a.rotation_id in rot_map_local else a.rotation_id
                    counts[ab] = counts.get(ab, 0) + 1
                weekly_counts.append(counts)
            df_prev = pd.DataFrame(weekly_counts).set_index("Week").fillna(0).astype(int)
            st.markdown("**Weekly headcounts (first 24 weeks, includes rotators):**")
            st.dataframe(df_prev, use_container_width=True)
