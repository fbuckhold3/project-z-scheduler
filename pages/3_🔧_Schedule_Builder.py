"""
pages/3_Schedule_Builder.py
Run greedy or CP-SAT solver to generate the schedule.
"""
import streamlit as st
import time
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_residents,
)
from core.solver import run_solver

st.set_page_config(page_title="Schedule Builder", page_icon="🔧", layout="wide")

if "rotations" not in st.session_state:
    st.session_state.rotations          = default_rotations()
    st.session_state.residents          = default_residents()
    st.session_state.rotator_programs   = default_rotator_programs()
    st.session_state.academic_year      = default_academic_year()
    st.session_state.schedule           = None
    st.session_state.feasibility        = None
    st.session_state.solve_result       = None

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
            "**Greedy** fills rotations in priority order (ABABA → NF → Clinic → SLUH/VA → OP). "
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
# Build button
# ---------------------------------------------------------------------------
st.markdown("---")

col_btn, col_info = st.columns([1, 3])
with col_btn:
    build_clicked = st.button("🚀 Build Schedule", type="primary", use_container_width=True)

if build_clicked:
    with st.spinner(f"Running {method.upper()} solver…"):
        result = run_solver(
            residents=st.session_state.residents,
            rotations=st.session_state.rotations,
            academic_year=st.session_state.academic_year,
            method=method,
            time_limit_sec=time_limit or 120,
            seed=int(seed),
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
    mc[0].metric("Solver", sr.solver_used.upper())
    mc[1].metric("Time", f"{sr.solve_time_sec}s")
    mc[2].metric("Status", "✅ Success" if sr.success else "❌ Failed")
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

        # Quick preview: rotation counts per week
        import pandas as pd
        ay = sr.schedule.academic_year
        rot_map = {r.rotation_id: r for r in st.session_state.rotations}
        weeks = ay.all_weeks(include_blackout=False)

        weekly_counts = []
        for w in weeks[:24]:  # first 24 active weeks
            week_assign = sr.schedule.get_week_assignments(w)
            counts = {}
            for a in week_assign:
                rot_abbrev = rot_map.get(a.rotation_id, type("R", (), {"abbrev": a.rotation_id})()).abbrev
                counts[rot_abbrev] = counts.get(rot_abbrev, 0) + 1
            counts["Week"] = f"W{w:02d}"
            weekly_counts.append(counts)

        df_preview = pd.DataFrame(weekly_counts).set_index("Week").fillna(0).astype(int)
        st.markdown("**Weekly rotation headcounts (first 24 active weeks):**")
        st.dataframe(df_preview, use_container_width=True)
