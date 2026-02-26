"""
app.py — Project Z v.2: Residency Scheduling Suite
Main entry point for Streamlit / Posit Connect Cloud deployment.
"""
import streamlit as st
from core.defaults import (
    default_academic_year,
    default_rotations,
    default_rotator_programs,
    default_residents,
)

st.set_page_config(
    page_title="Project Z — Residency Scheduler",
    page_icon="🏥",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Session state initialisation (runs once per session)
# ---------------------------------------------------------------------------

def init_state():
    if "rotations" not in st.session_state:
        st.session_state.rotations = default_rotations()
    if "residents" not in st.session_state:
        st.session_state.residents = default_residents()
    if "rotator_programs" not in st.session_state:
        st.session_state.rotator_programs = default_rotator_programs()
    if "academic_year" not in st.session_state:
        st.session_state.academic_year = default_academic_year()
    if "schedule" not in st.session_state:
        st.session_state.schedule = None
    if "feasibility" not in st.session_state:
        st.session_state.feasibility = None
    if "solve_result" not in st.session_state:
        st.session_state.solve_result = None

init_state()

# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

st.title("🏥 Project Z — Residency Scheduling Suite")
st.caption("Internal Medicine Residency Program • 48-Week Academic Year")

st.markdown("---")

col1, col2, col3, col4, col5 = st.columns(5)

with col1:
    st.markdown("### ⚙️ Configure")
    st.markdown(
        "Set up rotations, resident roster, rotator programs, and academic year settings."
    )
    n_res = len(st.session_state.residents)
    n_rot = len([r for r in st.session_state.rotations if r.active])
    st.metric("Residents", n_res)
    st.metric("Active Rotations", n_rot)

with col2:
    st.markdown("### 📊 Capacity")
    st.markdown(
        "Run the feasibility calculator to verify your configuration is mathematically solvable."
    )
    if st.session_state.feasibility:
        f = st.session_state.feasibility
        color = "🟢" if f.feasible else "🔴"
        st.metric("Status", f"{color} {'Feasible' if f.feasible else 'Infeasible'}")
        st.metric("Warnings", len(f.warnings))
    else:
        st.info("Not yet run")

with col3:
    st.markdown("### 🔧 Build")
    st.markdown(
        "Generate a schedule using the Greedy heuristic or CP-SAT optimizer."
    )
    if st.session_state.solve_result:
        sr = st.session_state.solve_result
        st.metric("Solver", sr.solver_used.upper())
        st.metric("Violations", sr.n_violations)
        st.metric("Time", f"{sr.solve_time_sec}s")
    else:
        st.info("No schedule built yet")

with col4:
    st.markdown("### 📅 View")
    st.markdown(
        "Explore the schedule as an interactive grid, Gantt chart, or resident timeline."
    )
    if st.session_state.schedule:
        n_assign = len(st.session_state.schedule.assignments)
        st.metric("Assignments", n_assign)
    else:
        st.info("No schedule to display")

with col5:
    st.markdown("### 📥 Export")
    st.markdown(
        "Download the schedule as Excel, CSV, or JSON for use in other tools."
    )

st.markdown("---")

# Quick workflow guide
with st.expander("📖 Getting Started", expanded=True):
    st.markdown("""
    **Recommended workflow:**

    1. **⚙️ Configuration** → Review rotation definitions, update your resident roster
       (upload a CSV or edit inline), adjust rotator programs and blackout weeks.

    2. **📊 Capacity Calculator** → Check feasibility *before* solving.
       The calculator tells you whether your roster can cover all required rotations
       given clinic obligations, blackout weeks, and the max-3-IP-per-6-week constraint.

    3. **🔧 Schedule Builder** → Choose **Greedy** (seconds, good for iteration) or
       **CP-SAT** (minutes, constraint-optimal). Adjust solver parameters as needed.

    4. **📅 Schedule Viewer** → Inspect the generated schedule in a color-coded grid.
       Filter by resident, PGY year, or rotation. Identify gaps and violations.

    5. **📥 Export** → Download as Excel (formatted grid + violation report) or CSV/JSON.

    ---
    **Key scheduling rules encoded:**
    - ✅ Max **3 inpatient weeks** in any 6-week sliding window
    - ✅ **Clinic every 6 weeks** (1 of 6 weeks), distributed 14-14-13-14-14-13
    - ✅ **NF in 2-week blocks** only, with ≥ 6-week gap and no IP adjacent
    - ✅ **MICU / Bronze ABABA** pattern (weeks 1, 3, 5 of every 5-week cycle)
    - ✅ **Rotator credit** for Neurology, EM, Anesthesia, Psychiatry slots
    - ✅ **Blackout weeks** (July 4 ramp, Dec 23–Jan 2)
    - ✅ Level eligibility (senior-only, intern-only where applicable)
    """)

st.markdown("---")
st.caption(
    "Project Z v.2 · Built with Streamlit · "
    "Navigate using the sidebar →"
)
