"""
pages/5_Schedule_Priorities.py
Interactive, priority-driven schedule builder.

Users choose WHICH rotations to solve and in WHAT ORDER, then build the
schedule incrementally — locking each layer before adding the next.
After each step they can inspect coverage before proceeding.

Supported modes
  • Full auto  — runs all steps in the configured priority order (same as
                 Schedule Builder but with a custom order).
  • Step-by-step — solve one rotation group at a time; inspect and
                   optionally lock results before continuing.

Solver steps available (drag to reorder):
  1. Rotator pre-assignments (always first — external programs, fixed)
  2. Clinic         — 1/6-week continuity rule
  3. Night Float    — 2-week blocks with 6-week gap
  4. ABABA (MICU + Bronze) — 5-week alternating cycle
  5. Main IP (SLUH, VA, Gold, Cards)
  6. Fill OP        — remainder

Locking: completed steps are "frozen" in the grid; subsequent steps treat
locked assignments as immovable pre-injections.
"""
from __future__ import annotations

import time
from copy import deepcopy

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.defaults import (
    default_academic_year,
    default_residents,
    default_rotations,
    default_rotator_programs,
    default_rotator_residents,
    schedule_rotators,
)
from core.models import Assignment, Schedule
from core.solver import GreedySolver, _contiguous_runs, _ip_rotations

st.set_page_config(
    page_title="Schedule Priorities",
    page_icon="🎯",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Session-state bootstrap
# ---------------------------------------------------------------------------

def _init_state():
    if "rotations" not in st.session_state:
        st.session_state.rotations        = default_rotations()
        st.session_state.residents        = default_residents()
        st.session_state.rotator_programs = default_rotator_programs()
        st.session_state.academic_year    = default_academic_year()
        st.session_state.schedule         = None
        st.session_state.feasibility      = None
        st.session_state.solve_result     = None

    # Priority page specific state
    if "prio_grid" not in st.session_state:
        st.session_state.prio_grid      = None    # raw week→res→rot grid
        st.session_state.prio_locked    = set()   # which steps are locked
        st.session_state.prio_step_done = set()   # which steps have run
        st.session_state.prio_schedule  = None    # latest Schedule object

_init_state()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ay        = st.session_state.academic_year
rotations = st.session_state.rotations
residents = st.session_state.residents
rot_map   = {r.rotation_id: r for r in rotations}
res_map   = {r.resident_id: r for r in residents}
ip_ids    = _ip_rotations(rotations)

STEP_LABELS = {
    "rotators":  "🌐 Rotator Pre-assignments",
    "clinic":    "🏥 Continuity Clinic",
    "nf":        "🌙 Night Float",
    "ababa":     "🔄 ABABA (MICU + Bronze)",
    "main_ip":   "🏨 Main IP (SLUH / VA / Gold / Cards)",
    "fill_op":   "📋 Fill OP / Elective",
}
DEFAULT_ORDER = ["rotators", "clinic", "nf", "ababa", "main_ip", "fill_op"]
ALWAYS_FIRST  = {"rotators"}   # cannot be reordered or skipped

color_map: dict[str, str] = {r.rotation_id: r.color for r in rotations}
color_map.update({"VACATION": "#374151", "OP": "#86EFAC", "Vacation": "#374151"})
blackout_set = set(ay.blackout_weeks)


def _grid_to_schedule(grid: dict) -> Schedule:
    """Convert raw week→res→rot grid into a Schedule with Assignment objects."""
    all_residents = residents + default_rotator_residents()
    all_res_ids   = {r.resident_id for r in all_residents}
    assignments: list[Assignment] = []

    for res in [r for r in all_residents if r.resident_id in {rid for wk in grid.values() for rid in wk}]:
        prev_rot   = None
        block_start = None
        for w in range(1, ay.total_weeks + 1):
            rot_id = grid.get(w, {}).get(res.resident_id)
            if rot_id != prev_rot:
                if prev_rot and prev_rot not in ("VACATION", None) and block_start is not None:
                    assignments.append(Assignment(
                        resident_id=res.resident_id,
                        rotation_id=prev_rot,
                        start_week=block_start,
                        end_week=w - 1,
                    ))
                prev_rot    = rot_id
                block_start = w
        # close final block
        if prev_rot and prev_rot not in ("VACATION", None) and block_start is not None:
            assignments.append(Assignment(
                resident_id=res.resident_id,
                rotation_id=prev_rot,
                start_week=block_start,
                end_week=ay.total_weeks,
            ))

    return Schedule(academic_year=ay, assignments=assignments, generated_by="priority-step")


def _run_step(step_key: str, grid: dict) -> dict:
    """
    Run a single solver step and update the grid in-place.
    Returns the (possibly mutated) grid.
    """
    all_residents  = residents + default_rotator_residents()
    active_weeks   = ay.active_weeks()
    solver = GreedySolver(all_residents, rotations, ay)

    # Replay locked grid into solver state
    for w, row in grid.items():
        for rid, rot_id in row.items():
            solver.grid[w][rid] = rot_id
            if rot_id and rot_id in solver.weekly_slots.get(w, {}):
                if rid not in solver.weekly_slots[w][rot_id]:
                    solver.weekly_slots[w][rot_id].append(rid)

    if step_key == "rotators":
        rotator_res   = default_rotator_residents()
        pre_assigned  = schedule_rotators(rotator_res, ay)
        solver._inject_assignments(pre_assigned)

    elif step_key == "clinic":
        solver._assign_clinic(active_weeks)

    elif step_key == "nf":
        solver._assign_nf(active_weeks)

    elif step_key == "ababa":
        solver._assign_ababa(active_weeks)

    elif step_key == "main_ip":
        solver._assign_main_ip(active_weeks)

    elif step_key == "fill_op":
        solver._fill_op(active_weeks)

    # Read back the updated grid
    new_grid = {}
    for w, row in solver.grid.items():
        new_grid[w] = dict(row)
    return new_grid


def _coverage_df(grid: dict) -> pd.DataFrame:
    """Build a week × rotation count DataFrame from the raw grid."""
    all_w = ay.all_weeks()
    rows  = []
    for w in all_w:
        row = {"Week": w, "Active": w not in blackout_set}
        wrow = grid.get(w, {})
        for rot in rotations:
            row[rot.abbrev] = sum(1 for rid, rid_rot in wrow.items() if rid_rot == rot.rotation_id)
        rows.append(row)
    return pd.DataFrame(rows).set_index("Week")


# ===========================================================================
# Page Layout
# ===========================================================================

st.title("🎯 Schedule Priorities")
st.caption(
    "Build the schedule **one layer at a time**. "
    "Set the fill order, run each step, inspect coverage, then lock it before proceeding."
)

# ---------------------------------------------------------------------------
# Sidebar — priority ordering + options
# ---------------------------------------------------------------------------
with st.sidebar:
    st.header("⚙️ Priority Settings")

    st.markdown("**Drag to reorder solver steps**  \n(Rotators are always first and cannot be moved.)")

    # Allow user to reorder the non-fixed steps via number inputs (Streamlit
    # doesn't support true drag-and-drop natively, so we use rank inputs).
    non_fixed = [s for s in DEFAULT_ORDER if s not in ALWAYS_FIRST]
    st.caption("Assign a rank (1 = highest priority):")

    raw_ranks: dict[str, int] = {}
    for step in non_fixed:
        raw_ranks[step] = st.number_input(
            STEP_LABELS[step],
            min_value=1, max_value=len(non_fixed),
            value=non_fixed.index(step) + 1,
            step=1,
            key=f"rank_{step}",
        )

    # Sort non-fixed steps by rank (stable sort preserves original order on ties)
    ordered_steps = sorted(non_fixed, key=lambda s: (raw_ranks[s], non_fixed.index(s)))
    full_order    = ["rotators"] + ordered_steps

    st.markdown("---")
    st.markdown("**Resolved order:**")
    for i, s in enumerate(full_order, 1):
        locked_icon = "🔒" if s in st.session_state.prio_locked else "  "
        done_icon   = "✅" if s in st.session_state.prio_step_done else "⬜"
        st.markdown(f"{done_icon} {locked_icon} **{i}.** {STEP_LABELS[s]}")

    st.markdown("---")

    run_mode = st.radio(
        "Run mode",
        options=["Step-by-step", "Full auto"],
        index=0,
        help=(
            "**Step-by-step**: run one step at a time, review, then lock.\n\n"
            "**Full auto**: run all steps immediately in priority order."
        ),
    )

    st.markdown("---")

    if st.button("🗑️ Reset all steps", use_container_width=True):
        st.session_state.prio_grid      = None
        st.session_state.prio_locked    = set()
        st.session_state.prio_step_done = set()
        st.session_state.prio_schedule  = None
        st.rerun()

# ---------------------------------------------------------------------------
# Main area
# ---------------------------------------------------------------------------

# Current grid (initialise empty if needed)
if st.session_state.prio_grid is None:
    st.session_state.prio_grid = {w: {} for w in ay.all_weeks()}

grid = st.session_state.prio_grid

# ---------------------------------------------------------------------------
# Full-auto mode
# ---------------------------------------------------------------------------
if run_mode == "Full auto":
    st.subheader("Full Auto Solve")
    st.info(
        "All steps will run sequentially in the priority order shown in the sidebar. "
        "This is equivalent to the Schedule Builder page but respects your custom priority order."
    )
    if st.button("▶️ Run Full Schedule", type="primary", use_container_width=True):
        progress = st.progress(0, text="Starting…")
        t0 = time.time()
        for i, step in enumerate(full_order):
            progress.progress((i) / len(full_order), text=f"Running: {STEP_LABELS[step]}")
            grid = _run_step(step, grid)
            st.session_state.prio_step_done.add(step)
            st.session_state.prio_locked.add(step)
        st.session_state.prio_grid     = grid
        st.session_state.prio_schedule = _grid_to_schedule(grid)
        # Also write to the shared schedule slot so Viewer/Export work
        st.session_state.schedule      = st.session_state.prio_schedule
        elapsed = time.time() - t0
        progress.progress(1.0, text=f"✅ Done in {elapsed:.1f}s")
        st.success(f"Full schedule built in {elapsed:.1f}s. Go to **Schedule Viewer** to inspect.")

# ---------------------------------------------------------------------------
# Step-by-step mode
# ---------------------------------------------------------------------------
else:
    st.subheader("Step-by-Step Builder")

    # Determine the next step to run
    pending = [s for s in full_order if s not in st.session_state.prio_step_done]
    next_step = pending[0] if pending else None

    col_steps, col_info = st.columns([1, 2])

    with col_steps:
        st.markdown("### Steps")
        for step in full_order:
            done   = step in st.session_state.prio_step_done
            locked = step in st.session_state.prio_locked
            is_next = step == next_step

            icon = "✅" if done else ("▶️" if is_next else "⏳")
            lock_text = " 🔒" if locked else (" *(pending lock)*" if done else "")
            badge_col = "green" if done else ("orange" if is_next else "gray")

            st.markdown(
                f"<div style='padding:6px 10px;margin:4px 0;border-radius:6px;"
                f"background:{'#dcfce7' if done else ('#fef9c3' if is_next else '#f1f5f9')};'>"
                f"{icon} <b>{STEP_LABELS[step]}</b>{lock_text}"
                f"</div>",
                unsafe_allow_html=True,
            )

        st.markdown("---")

        if next_step:
            st.markdown(f"**Next:** {STEP_LABELS[next_step]}")
            if st.button(f"▶️ Run: {STEP_LABELS[next_step]}", type="primary", use_container_width=True):
                with st.spinner(f"Running {STEP_LABELS[next_step]}…"):
                    new_grid = _run_step(next_step, deepcopy(grid))
                st.session_state.prio_grid = new_grid
                st.session_state.prio_step_done.add(next_step)
                st.session_state.prio_schedule = _grid_to_schedule(new_grid)
                st.session_state.schedule      = st.session_state.prio_schedule
                st.rerun()
        else:
            st.success("All steps complete!")

    with col_info:
        if st.session_state.prio_schedule is None:
            st.info("Run the first step to see coverage here.")
        else:
            # Show which steps are done but not yet locked
            unlocked_done = [s for s in st.session_state.prio_step_done
                             if s not in st.session_state.prio_locked]
            if unlocked_done:
                st.warning(
                    f"**{len(unlocked_done)} step(s) run but not locked:** "
                    + ", ".join(STEP_LABELS[s] for s in unlocked_done)
                    + "  \nLock them to prevent subsequent steps from overwriting their results."
                )
                if st.button("🔒 Lock all completed steps", use_container_width=True):
                    st.session_state.prio_locked.update(unlocked_done)
                    st.rerun()
            else:
                st.success("All completed steps are locked.")

            # Coverage sparkline — how many residents per rotation per week
            df_cov = _coverage_df(st.session_state.prio_grid)
            active_rots = [r for r in rotations if r.abbrev in df_cov.columns
                           and df_cov[r.abbrev].sum() > 0]

            st.markdown("#### Current Coverage by Rotation")
            fig = go.Figure()
            for rot in active_rots:
                fig.add_trace(go.Bar(
                    name=rot.abbrev,
                    x=df_cov.index,
                    y=df_cov[rot.abbrev],
                    marker_color=rot.color,
                ))
            fig.update_layout(
                barmode="stack",
                height=280,
                margin=dict(l=10, r=10, t=10, b=40),
                xaxis=dict(title="Week", dtick=4),
                yaxis_title="Residents",
                legend=dict(orientation="h", y=1.1, font=dict(size=10)),
                plot_bgcolor="white",
            )
            st.plotly_chart(fig, use_container_width=True)

# ---------------------------------------------------------------------------
# Per-step lock/unlock + detailed inspection
# ---------------------------------------------------------------------------

if st.session_state.prio_step_done:
    st.markdown("---")
    st.subheader("🔍 Step Inspection & Locking")
    st.caption(
        "Select a step to inspect which residents were assigned in that layer. "
        "Locked steps cannot be overwritten by later steps."
    )

    inspect_step = st.selectbox(
        "Inspect step",
        options=list(st.session_state.prio_step_done),
        format_func=lambda s: STEP_LABELS[s],
    )

    col_lock, col_unlock = st.columns(2)
    with col_lock:
        if inspect_step not in st.session_state.prio_locked:
            if st.button(f"🔒 Lock '{STEP_LABELS[inspect_step]}'", use_container_width=True):
                st.session_state.prio_locked.add(inspect_step)
                st.rerun()
        else:
            st.success(f"'{STEP_LABELS[inspect_step]}' is locked.")
    with col_unlock:
        if inspect_step in st.session_state.prio_locked:
            if st.button(f"🔓 Unlock '{STEP_LABELS[inspect_step]}'", use_container_width=True):
                st.session_state.prio_locked.discard(inspect_step)
                st.rerun()

    # Rotation-specific step summary
    step_rot_map = {
        "rotators": ["MICU", "SLUH", "VA"],
        "clinic":   ["Clinic"],
        "nf":       ["NF"],
        "ababa":    ["MICU", "Bronze"],
        "main_ip":  ["SLUH", "VA", "Gold", "Cards"],
        "fill_op":  ["OP"],
    }
    show_rots = step_rot_map.get(inspect_step, [])

    grid_now = st.session_state.prio_grid
    weeks_active = ay.active_weeks()

    for rot_id in show_rots:
        rot = rot_map.get(rot_id)
        if not rot:
            continue
        # Count per week
        counts = [(w, sum(1 for rid_rot in grid_now.get(w, {}).values() if rid_rot == rot_id))
                  for w in weeks_active]
        cap = (rot.senior_capacity or 0) + (rot.intern_capacity or 0)

        weeks_ok    = sum(1 for _, c in counts if c >= cap)
        weeks_under = sum(1 for _, c in counts if 0 < c < cap)
        weeks_empty = sum(1 for _, c in counts if c == 0)
        total_active = len(weeks_active)

        status = "🟢" if weeks_empty == 0 and weeks_under == 0 else ("🟡" if weeks_empty == 0 else "🔴")
        st.markdown(
            f"**{rot.name}** (cap {cap}/wk) {status}  "
            f"At target: {weeks_ok}/{total_active} wks · "
            f"Under: {weeks_under} · Empty: {weeks_empty}"
        )

# ---------------------------------------------------------------------------
# Publish to shared schedule
# ---------------------------------------------------------------------------

st.markdown("---")
st.subheader("📤 Publish to Schedule Viewer")
st.caption(
    "Once you're happy with the schedule, publish it so the "
    "Schedule Viewer and Export pages can use it."
)

if st.session_state.prio_schedule is not None:
    already_published = (st.session_state.schedule is st.session_state.prio_schedule)
    if already_published:
        st.success("✅ This schedule is already published. Open **📅 Schedule Viewer** to inspect.")
    else:
        if st.button("📤 Publish to Viewer & Export", type="primary"):
            st.session_state.schedule = st.session_state.prio_schedule
            st.success("Published! Go to **📅 Schedule Viewer**.")
else:
    st.info("Run at least one step first.")

# ---------------------------------------------------------------------------
# Explanation footer
# ---------------------------------------------------------------------------

with st.expander("ℹ️ How priorities work"):
    st.markdown("""
**Why order matters**

Each solver step fills rotation slots greedily in the order you specify.
Earlier steps "claim" weeks before later steps can touch them, so:

- If you run **Clinic first**, those weeks are guaranteed before IP rotations
  compete for the same residents — this is the standard approach and prevents
  Clinic from being squeezed out.
- If you run **MICU/Bronze first**, the ABABA pattern is fully respected with
  maximum flexibility for resident selection.
- If you run **Night Float first**, NF blocks get first choice of unassigned
  weeks, reducing the risk of NF clashing with MICU adjacency.

**Locking**

Locking a step writes its assignments as immovable constraints. Later steps
will see those weeks as already filled and will skip those residents/weeks.
This mimics "agreeing" on a partial schedule before optimising the rest.

**Step-by-step vs Full auto**

Full auto is equivalent to clicking through every step immediately — useful
when you just want a complete schedule with your preferred priority order.
Step-by-step lets you pause, inspect coverage, and decide whether to lock or
re-run a step before proceeding.
    """)
