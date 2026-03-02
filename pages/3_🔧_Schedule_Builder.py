"""
pages/3_Schedule_Builder.py
Run greedy or CP-SAT solver to generate the schedule.
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


# ---------------------------------------------------------------------------
# Page header
# ---------------------------------------------------------------------------
st.title("🔧 Schedule Builder")
st.caption("Generate a schedule using the Greedy heuristic or the CP-SAT optimizer.")

# ---------------------------------------------------------------------------
# Solver selection
# ---------------------------------------------------------------------------
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
            "Guarantees hard constraints are satisfied and optimises soft constraints. "
            "Requires `ortools` package. Solve time scales with problem size."
        )

# ---------------------------------------------------------------------------
# Feasibility gate
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Rotator Pre-Schedule editor
# ---------------------------------------------------------------------------
# Safety default: edited_df is assigned inside the expander (same Python scope),
# but initialise here so the Build section always has a valid reference.
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

# ---------------------------------------------------------------------------
# Build button
# ---------------------------------------------------------------------------
st.markdown("---")

col_btn, col_info = st.columns([1, 3])
with col_btn:
    build_clicked = st.button("🚀 Build Schedule", type="primary", use_container_width=True)

if build_clicked:
    with st.spinner(f"Running {method.upper()} solver…"):
        # Use the (possibly edited) rotator pre-schedule from the table above
        # edited_df comes from the data_editor widget above (widget state preserved across reruns).
        # Fall back to session state if edited_df is somehow empty on this render.
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

# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------
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
