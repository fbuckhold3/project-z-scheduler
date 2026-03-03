"""
pages/3_Schedule_Builder.py
Run greedy or CP-SAT solver to generate the schedule.
Tabs:
  ✏️ Interactive Editor — click-to-assign grid with real-time validation
  ⚙️ Auto Solve        — rotator pre-schedule + one-click solve
"""
import pandas as pd
import streamlit as st
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_all_residents, schedule_rotators,
)
from core.models import Assignment
from core.solver import run_solver

st.set_page_config(page_title="Schedule Builder", page_icon="🔧", layout="wide")


# ---------------------------------------------------------------------------
# Helpers: convert between Assignment objects and the editable DataFrame
# ---------------------------------------------------------------------------

def _assignments_to_df(assignments: list, res_map: dict) -> pd.DataFrame:
    """Convert Assignment objects → editable DataFrame for the rotator editor.
    No hidden columns — resident_id is looked up by name in _df_to_assignments."""
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
    """Convert the edited DataFrame → Assignment objects for the solver.
    Derives resident_id by looking up the Resident display name."""
    result = []
    for _, row in df.iterrows():
        name = row.get("Resident")
        rot  = row.get("Rotation")
        sw   = row.get("Start Week")
        ew   = row.get("End Week")
        # Skip blank or incomplete rows
        if not name or not rot or pd.isna(sw) or pd.isna(ew):
            continue
        rid = name_to_id.get(str(name))
        if not rid:
            continue  # unknown name — skip
        result.append(Assignment(
            resident_id=rid,
            rotation_id=str(rot),
            start_week=int(sw),
            end_week=int(ew),
            is_rotator_slot=True,
            rotator_specialty=str(row.get("Program", "") or ""),
        ))
    return result


def _manual_grid_to_assignments(
    manual_grid: dict,  # {(resident_id, week): rotation_id}
) -> list[Assignment]:
    """
    Compress the manual_grid into contiguous Assignment blocks.
    Consecutive weeks with the same rotation for the same resident become one block.
    """
    from collections import defaultdict
    res_weeks: dict[str, dict[int, str]] = defaultdict(dict)
    for (rid, w), rot_id in manual_grid.items():
        res_weeks[rid][w] = rot_id

    assignments = []
    for rid, week_rot in res_weeks.items():
        sorted_weeks = sorted(week_rot.keys())
        if not sorted_weeks:
            continue

        prev_rot   = week_rot[sorted_weeks[0]]
        block_start = sorted_weeks[0]
        prev_week   = sorted_weeks[0]

        for w in sorted_weeks[1:]:
            cur_rot = week_rot[w]
            if cur_rot == prev_rot and w == prev_week + 1:
                # Extend current block
                prev_week = w
                continue
            # Close current block
            assignments.append(Assignment(
                resident_id=rid,
                rotation_id=prev_rot,
                start_week=block_start,
                end_week=prev_week,
            ))
            prev_rot    = cur_rot
            block_start = w
            prev_week   = w

        # Close final block
        assignments.append(Assignment(
            resident_id=rid,
            rotation_id=prev_rot,
            start_week=block_start,
            end_week=prev_week,
        ))
    return assignments


def _validate_manual_grid(
    manual_grid: dict,
    residents: list,
    rotations: list,
    academic_year,
) -> list[str]:
    """
    Run basic constraint checks on the manual grid.
    Returns a list of human-readable violation strings (empty = all OK).
    """
    from core.solver import _ip_rotations
    ip_ids = _ip_rotations(rotations)
    rot_map = {r.rotation_id: r for r in rotations}
    violations = []

    # Capacity check per rotation per week
    all_weeks = list(range(1, academic_year.total_weeks + 1))
    for w in all_weeks:
        week_assign: dict[str, list[str]] = {}  # rot_id → [resident_ids]
        for (rid, wk), rot_id in manual_grid.items():
            if wk == w:
                week_assign.setdefault(rot_id, []).append(rid)
        for rot_id, assigned_rids in week_assign.items():
            rot = rot_map.get(rot_id)
            if not rot:
                continue
            sr_count = sum(1 for rid in assigned_rids
                           if any(r.resident_id == rid and r.is_senior for r in residents))
            in_count = sum(1 for rid in assigned_rids
                           if any(r.resident_id == rid and not r.is_senior for r in residents))
            if rot.senior_capacity > 0 and sr_count > rot.senior_capacity:
                violations.append(
                    f"W{w:02d} {rot.abbrev}: {sr_count} seniors assigned (max {rot.senior_capacity})"
                )
            if rot.intern_capacity > 0 and in_count > rot.intern_capacity:
                violations.append(
                    f"W{w:02d} {rot.abbrev}: {in_count} interns assigned (max {rot.intern_capacity})"
                )

    # IP sliding-window check per resident
    res_map = {r.resident_id: r for r in residents}
    for res in residents:
        if res.resident_type == "rotator":
            continue
        for start in range(1, academic_year.total_weeks - 4):
            ip_count = sum(
                1 for wk in range(start, start + 6)
                if manual_grid.get((res.resident_id, wk)) in ip_ids
            )
            if ip_count > 3:
                violations.append(
                    f"{res.name}: {ip_count} IP weeks in window W{start:02d}–W{start+5:02d} (max 3)"
                )
                break  # one per resident

    return violations[:40]  # cap for display


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

# Ensure rotator residents are present — Config page previously used default_residents()
# (IM only) so navigating Config → Builder left rotator_res_list empty.
_rotators_in_state = [r for r in st.session_state.residents if r.resident_type == "rotator"]
if not _rotators_in_state:
    from core.defaults import default_rotator_residents as _drr
    _rotators_in_state = _drr()
    st.session_state.residents = st.session_state.residents + _rotators_in_state

# Lookup maps used throughout this page
res_map            = {r.resident_id: r for r in st.session_state.residents}
rotator_res_list   = [r for r in st.session_state.residents if r.resident_type == "rotator"]
rotator_name_to_id = {r.name: r.resident_id for r in rotator_res_list}
rotator_names      = sorted(rotator_name_to_id.keys())
IP_ROTATION_IDS    = ["SLUH", "VA", "MICU", "Cards", "Gold"]

# Initialise rotator pre-schedule on first load
if "rotator_assignments" not in st.session_state:
    _pre = schedule_rotators(rotator_res_list, st.session_state.academic_year)
    st.session_state.rotator_assignments = _assignments_to_df(_pre, res_map)

# Initialise manual grid (interactive editor state)
if "manual_grid" not in st.session_state:
    st.session_state.manual_grid = {}  # {(resident_id, week): rotation_id}

# Abbrev ↔ ID lookups for the interactive editor
active_rots     = [r for r in st.session_state.rotations if r.active]
abbrev_to_id    = {r.abbrev: r.rotation_id for r in active_rots}
id_to_abbrev    = {r.rotation_id: r.abbrev  for r in active_rots}
rot_abbrev_opts = [""] + [r.abbrev for r in active_rots]

# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("🔧 Schedule Builder")

tab_editor, tab_solve = st.tabs(["✏️ Interactive Editor", "⚙️ Auto Solve"])


# ===========================================================================
# TAB 1: Interactive Editor
# ===========================================================================

with tab_editor:
    st.caption(
        "Assign rotations week-by-week for any resident. "
        "Select a cell and choose a rotation from the dropdown. "
        "Violations are flagged below the table in real time. "
        "Click **🚀 Auto-fill Remaining** to run the solver with your entries locked."
    )

    # --- Controls row ---
    col_weeks, col_level, col_reset = st.columns([2, 2, 1])
    with col_weeks:
        week_range = st.select_slider(
            "View weeks",
            options=list(range(1, 49)),
            value=(1, 12),
            help="Scroll through the year in 12-week windows. Changes to weeks outside this view are preserved.",
        )
    with col_level:
        level_filter = st.multiselect(
            "Show residents",
            options=["PGY1 (interns)", "PGY2", "PGY3"],
            default=["PGY1 (interns)", "PGY2", "PGY3"],
        )
    with col_reset:
        st.write("")
        st.write("")
        if st.button("🗑️ Clear all", help="Wipe all manual assignments"):
            st.session_state.manual_grid = {}
            st.rerun()

    # Build PGY filter from multiselect
    pgy_filter: set[int] = set()
    if "PGY1 (interns)" in level_filter:
        pgy_filter.add(1)
    if "PGY2" in level_filter:
        pgy_filter.add(2)
    if "PGY3" in level_filter:
        pgy_filter.add(3)

    # Non-rotator residents matching the PGY filter
    view_residents = [
        r for r in st.session_state.residents
        if r.resident_type != "rotator" and r.pgy_year in pgy_filter
    ]

    w_start, w_end = week_range
    view_weeks = list(range(w_start, w_end + 1))

    # Build DataFrame for the current view
    ay = st.session_state.academic_year
    blackout_set = set(ay.blackout_weeks)

    rows = []
    for res in view_residents:
        row: dict = {"Name": res.name, "PGY": f"PGY{res.pgy_year}"}
        for w in view_weeks:
            rot_id = st.session_state.manual_grid.get((res.resident_id, w))
            if w in blackout_set:
                row[f"W{w:02d}"] = "Vac"
            else:
                row[f"W{w:02d}"] = id_to_abbrev.get(rot_id, "") if rot_id else ""
        rows.append(row)

    editor_df = pd.DataFrame(rows) if rows else pd.DataFrame()

    # Column config: fixed Name/PGY cols, SelectboxColumn for each week
    col_config: dict = {
        "Name": st.column_config.TextColumn("Resident", disabled=True, width="medium"),
        "PGY":  st.column_config.TextColumn("PGY",      disabled=True, width="small"),
    }
    for w in view_weeks:
        if w in blackout_set:
            col_config[f"W{w:02d}"] = st.column_config.TextColumn(
                f"W{w:02d}", disabled=True, width="small"
            )
        else:
            col_config[f"W{w:02d}"] = st.column_config.SelectboxColumn(
                f"W{w:02d}",
                options=rot_abbrev_opts,
                width="small",
            )

    if editor_df.empty:
        st.info("No residents match the current filter. Adjust the PGY selector above.")
    else:
        edited_grid = st.data_editor(
            editor_df,
            column_config=col_config,
            use_container_width=True,
            hide_index=True,
            key=f"interactive_editor_{w_start}_{w_end}_{''.join(map(str, sorted(pgy_filter)))}",
        )

        # Sync edited values back into manual_grid
        for i, res in enumerate(view_residents):
            for w in view_weeks:
                if w in blackout_set:
                    continue
                col = f"W{w:02d}"
                val = str(edited_grid.iloc[i].get(col, "") or "").strip()
                if val and val != "Vac":
                    rot_id = abbrev_to_id.get(val)
                    if rot_id:
                        st.session_state.manual_grid[(res.resident_id, w)] = rot_id
                    else:
                        st.session_state.manual_grid.pop((res.resident_id, w), None)
                else:
                    st.session_state.manual_grid.pop((res.resident_id, w), None)

    # --- Validation panel ---
    n_manual = len(st.session_state.manual_grid)
    st.caption(f"**{n_manual}** manual assignments across the year.")

    if n_manual > 0:
        violations = _validate_manual_grid(
            st.session_state.manual_grid,
            st.session_state.residents,
            st.session_state.rotations,
            ay,
        )
        if violations:
            with st.expander(f"⚠️ {len(violations)} constraint violation(s) detected", expanded=True):
                for v in violations:
                    st.markdown(f"- ⚠️ {v}")
        else:
            st.success("✅ No constraint violations in manual assignments.")

    # --- Auto-fill button ---
    st.markdown("---")
    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        autofill_clicked = st.button(
            "🚀 Auto-fill Remaining",
            type="primary",
            use_container_width=True,
            help="Runs the Greedy solver with your manual assignments locked. Fills all empty weeks.",
        )
    with col_info:
        st.info(
            "Manual assignments above are **locked** — the solver fills all remaining "
            "empty weeks around them. Solver method (Greedy/CP-SAT) is set in the "
            "**⚙️ Auto Solve** tab."
        )

    if autofill_clicked:
        if n_manual == 0:
            st.warning("No manual assignments to lock. Switch to Auto Solve tab for a full solve.")
        else:
            with st.spinner("Compressing manual assignments and running Greedy solver…"):
                # Compress manual_grid into pre_assigned Assignment list
                pre_from_manual = _manual_grid_to_assignments(st.session_state.manual_grid)
                # Also include rotator pre-schedule
                _build_df = st.session_state.rotator_assignments
                pre_from_rotators = _df_to_assignments(_build_df, rotator_name_to_id)
                pre_assigned = pre_from_rotators + pre_from_manual

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
                st.info("Navigate to **📅 Schedule Viewer** to explore the full schedule.")
            else:
                st.error(result.status_message)


# ===========================================================================
# TAB 2: Auto Solve
# ===========================================================================

with tab_solve:
    st.caption("Generate a complete schedule using the Greedy heuristic or the CP-SAT optimizer.")

    # -----------------------------------------------------------------------
    # Solver selection
    # -----------------------------------------------------------------------
    col1, col2 = st.columns([1, 2])

    with col1:
        method = st.radio(
            "Solver",
            options=["greedy", "cpsat"],
            format_func=lambda x: "⚡ Greedy (seconds)" if x == "greedy" else "🧮 CP-SAT (minutes, optimal)",
            index=0,
        )

    with col2:
        if method == "greedy":
            seed = st.number_input("Random seed", min_value=0, value=42, step=1,
                                   help="Change to get a different greedy solution")
            time_limit = None
            st.info(
                "**Greedy** fills rotations in priority order (NF → ABABA → Clinic → SLUH/VA → OP). "
                "Fast and good for iteration. May not be globally optimal."
            )
        else:
            time_limit = st.slider(
                "Time limit (seconds)", min_value=30, max_value=600, value=120, step=30,
                help="CP-SAT returns best solution found within this time"
            )
            seed = 42
            st.info(
                "**CP-SAT** uses Google OR-Tools constraint programming. "
                "Optimises across all constraints simultaneously. "
                "Requires `ortools` package. Solve time scales with problem size."
            )

    # -----------------------------------------------------------------------
    # Feasibility gate
    # -----------------------------------------------------------------------
    if st.session_state.feasibility is None:
        st.warning(
            "⚠️ Run the **Capacity Calculator** (page 2) before building the schedule. "
            "This ensures your configuration is feasible."
        )
    elif not st.session_state.feasibility.feasible:
        st.error(
            "❌ Feasibility check FAILED. Building a schedule may be impossible or produce "
            "many violations. Fix the configuration first."
        )

    # -----------------------------------------------------------------------
    # Rotator Pre-Schedule editor
    # -----------------------------------------------------------------------
    edited_df = st.session_state.rotator_assignments

    st.markdown("---")
    with st.expander("📋 External Rotator Pre-Schedule", expanded=True):
        st.info(
            "**What is this?**  \n"
            "Residents from partner programs (Neurology, EM, Anesthesia, Psychiatry) rotate "
            "through your service on pre-negotiated agreements. Their blocks are **locked in before "
            "the solver runs** and count against each rotation's capacity — your own residents fill "
            "around them.  \n\n"
            "**What should I do?**  \n"
            "The table is auto-generated from program defaults. If you have actual scheduled dates "
            "from the partnering programs, edit the **Start Week** and **End Week** columns to match. "
            "Otherwise, leave the defaults and click **Build Schedule** — you can always re-run "
            "after adjusting."
        )
        st.caption(
            "Edit **Rotation**, **Start Week**, or **End Week** inline. "
            "Use the **＋** icon below the table to add a block; "
            "select a row and press **Delete** to remove it."
        )

        col_reset, col_info = st.columns([1, 3])
        with col_reset:
            if st.button("↺ Reset to auto-generated", key="reset_rotators"):
                _pre = schedule_rotators(rotator_res_list, st.session_state.academic_year)
                st.session_state.rotator_assignments = _assignments_to_df(_pre, res_map)
                st.session_state.pop("rotator_editor", None)  # clear widget state so editor reloads fresh data
                st.rerun()

        edited_df = st.data_editor(
            st.session_state.rotator_assignments,
            column_config={
                # No label strings — keys match column names exactly to avoid renaming
                "Resident":   st.column_config.SelectboxColumn(
                    options=rotator_names,
                    help="External rotator name",
                ),
                "Program":    st.column_config.TextColumn(disabled=True),
                "Rotation":   st.column_config.SelectboxColumn(
                    options=IP_ROTATION_IDS,
                    help="Which inpatient service this rotator joins",
                ),
                "Start Week": st.column_config.NumberColumn(
                    min_value=1, max_value=48, step=1,
                ),
                "End Week":   st.column_config.NumberColumn(
                    min_value=1, max_value=48, step=1,
                    help="Inclusive. Default block = 4 weeks (Start + 3).",
                ),
            },
            use_container_width=True,
            hide_index=True,
            num_rows="dynamic",
            key="rotator_editor",
        )

        # Don't write edited_df back to session state — the data_editor widget preserves
        # its own edits via key="rotator_editor". Session state is only the initial data
        # source; edits live in widget state and are read directly from edited_df at build time.
        # Fall back to session state on first render when Streamlit may return an empty df.
        try:
            _disp = edited_df if not edited_df.empty else st.session_state.rotator_assignments
            valid_rows = _disp.dropna(subset=["Resident", "Rotation"])
            st.caption(
                f"**{len(valid_rows)}** block(s) across "
                f"**{valid_rows['Resident'].nunique()}** rotator(s) — edits apply on Build."
            )
        except Exception:
            n = len(st.session_state.rotator_assignments)
            st.caption(f"**{n}** block(s) defined.")

    # -----------------------------------------------------------------------
    # Build button
    # -----------------------------------------------------------------------
    st.markdown("---")

    col_btn2, col_info2 = st.columns([1, 3])
    with col_btn2:
        build_clicked = st.button("🚀 Build Schedule", type="primary", use_container_width=True)

    if build_clicked:
        with st.spinner(f"Running {method.upper()} solver…"):
            # Use the (possibly edited) rotator pre-schedule from the table above
            _build_df = edited_df if not edited_df.empty else st.session_state.rotator_assignments
            pre_assigned = _df_to_assignments(_build_df, rotator_name_to_id)

            result = run_solver(
                residents=st.session_state.residents,
                rotations=st.session_state.rotations,
                academic_year=st.session_state.academic_year,
                method=method,
                time_limit_sec=time_limit or 120,
                seed=int(seed),
                pre_assigned=pre_assigned,
            )
        st.session_state.solve_result = result
        if result.success:
            st.session_state.schedule = result.schedule
            st.success(result.status_message)
        else:
            st.error(result.status_message)

    # -----------------------------------------------------------------------
    # Results
    # -----------------------------------------------------------------------
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
            st.markdown("**Constraint violations detected:**")
            for v in sr.violation_details[:30]:
                st.markdown(f"- {v}")
            if sr.n_violations > 30:
                st.caption(f"… and {sr.n_violations - 30} more. View full schedule in the Schedule Viewer.")

        if sr.success and sr.schedule:
            st.markdown("---")
            st.success("✅ Schedule built. Navigate to **📅 Schedule Viewer** to explore it.")

            # Quick preview: rotation counts per week (first 24 weeks)
            ay = sr.schedule.academic_year
            rot_map_local = {r.rotation_id: r for r in st.session_state.rotations}
            weeks = ay.all_weeks()

            weekly_counts = []
            for w in weeks[:24]:
                week_assign = sr.schedule.get_week_assignments(w)
                counts = {}
                for a in week_assign:
                    abbrev = rot_map_local[a.rotation_id].abbrev if a.rotation_id in rot_map_local else a.rotation_id
                    counts[abbrev] = counts.get(abbrev, 0) + 1
                counts["Week"] = f"W{w:02d}"
                weekly_counts.append(counts)

            df_preview = pd.DataFrame(weekly_counts).set_index("Week").fillna(0).astype(int)
            st.markdown("**Weekly rotation headcounts (first 24 weeks, includes rotators):**")
            st.dataframe(df_preview, use_container_width=True)
