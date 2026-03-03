"""
pages/4_Schedule_Viewer.py
Interactive schedule grid, Gantt chart, and per-resident timeline.
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_residents,
)

st.set_page_config(page_title="Schedule Viewer", page_icon="📅", layout="wide")

if "rotations" not in st.session_state:
    st.session_state.rotations          = default_rotations()
    st.session_state.residents          = default_residents()
    st.session_state.rotator_programs   = default_rotator_programs()
    st.session_state.academic_year      = default_academic_year()
    st.session_state.schedule           = None
    st.session_state.feasibility        = None
    st.session_state.solve_result       = None

st.title("📅 Schedule Viewer")

if st.session_state.schedule is None:
    st.warning("No schedule built yet. Go to **🔧 Schedule Builder** first.")
    st.stop()

schedule  = st.session_state.schedule
residents = st.session_state.residents
rotations = st.session_state.rotations
ay        = schedule.academic_year

rot_map   = {r.rotation_id: r for r in rotations}
res_map   = {r.resident_id: r for r in residents}

# Color maps
color_map: dict[str, str] = {r.rotation_id: r.color for r in rotations}
color_map["VACATION"] = "#374151"
color_map["OP"]       = color_map.get("OP", "#86EFAC")

# Blackout weeks are vacation — build a lookup set for display
blackout_weeks: set[int] = set(ay.blackout_weeks)

# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
st.markdown("---")
col_f1, col_f2, col_f3 = st.columns(3)

with col_f1:
    pgy_filter = st.multiselect(
        "Filter by PGY year", options=[1, 2, 3], default=[1, 2, 3]
    )
with col_f2:
    type_filter = st.multiselect(
        "Filter by resident type",
        options=["categorical", "preliminary", "rotator"],
        default=["categorical", "preliminary"],
    )
with col_f3:
    week_range = st.slider(
        "Week range", min_value=1, max_value=ay.total_weeks,
        value=(1, min(24, ay.total_weeks)), step=1,
    )

filtered_res = [
    r for r in residents
    if r.pgy_year in pgy_filter and r.resident_type in type_filter
]

# ---------------------------------------------------------------------------
# Tab layout
# ---------------------------------------------------------------------------
tab_grid, tab_gantt, tab_resident, tab_weekly = st.tabs([
    "🗂️ Grid View",
    "📊 Gantt Chart",
    "👤 Resident Timeline",
    "📈 Weekly Counts",
])


# ===========================================================================
# GRID VIEW (residents × weeks heatmap)
# ===========================================================================
with tab_grid:
    st.subheader("Schedule Grid")
    st.caption("Color-coded by rotation. Hover for details.")

    weeks_shown = list(range(week_range[0], week_range[1] + 1))

    if not filtered_res:
        st.info("No residents match the current filters.")
        st.stop()

    # Build matrix
    z_text  = []   # abbreviation labels
    z_color = []   # numeric index for colorscale

    rot_ids_ordered = [r.rotation_id for r in rotations] + ["VACATION", "OP", ""]
    rot_id_to_num   = {rid: i for i, rid in enumerate(rot_ids_ordered)}

    for res in filtered_res:
        row_text  = []
        row_color = []
        for w in weeks_shown:
            if w in blackout_weeks and res.resident_type != "rotator":
                row_text.append("Vac")
                row_color.append(rot_id_to_num.get("VACATION", len(rot_ids_ordered) - 1))
            else:
                a = schedule.get_resident_week(res.resident_id, w)
                if a:
                    rot = rot_map.get(a.rotation_id)
                    row_text.append(rot.abbrev if rot else a.rotation_id)
                    row_color.append(rot_id_to_num.get(a.rotation_id, len(rot_ids_ordered) - 1))
                else:
                    row_text.append("")
                    row_color.append(len(rot_ids_ordered) - 1)
        z_text.append(row_text)
        z_color.append(row_color)

    # Build custom colorscale from rotation colors
    n = len(rot_ids_ordered)
    colorscale = []
    for i, rid in enumerate(rot_ids_ordered):
        c = color_map.get(rid, "#CBD5E1")
        frac = i / max(n - 1, 1)
        colorscale.append([frac, c])

    fig_hm = go.Figure(go.Heatmap(
        z=z_color,
        text=z_text,
        texttemplate="%{text}",
        textfont={"size": 9, "color": "white"},
        x=[f"W{w:02d}" for w in weeks_shown],
        y=[f"{r.name[:20]} (PGY{r.pgy_year})" for r in filtered_res],
        colorscale=colorscale,
        showscale=False,
        hoverongaps=False,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Week %{x}<br>"
            "Rotation: %{text}<extra></extra>"
        ),
    ))

    row_height = max(12, min(20, 600 // max(len(filtered_res), 1)))
    total_height = max(300, row_height * len(filtered_res) + 80)

    fig_hm.update_layout(
        height=total_height,
        xaxis_title="Week",
        yaxis_title="Resident",
        plot_bgcolor="white",
        margin=dict(l=180, r=10, t=40, b=60),
        xaxis=dict(tickangle=-45, tickfont=dict(size=9)),
        yaxis=dict(tickfont=dict(size=9)),
    )

    st.plotly_chart(fig_hm, use_container_width=True)

    # Legend
    st.markdown("**Legend:**")
    leg_cols = st.columns(min(len(rotations), 8))
    for i, rot in enumerate(rotations):
        with leg_cols[i % len(leg_cols)]:
            st.markdown(
                f'<div style="background:{rot.color};border-radius:4px;padding:3px 6px;'
                f'text-align:center;font-size:11px;color:white;margin:2px">'
                f'{rot.abbrev}</div>',
                unsafe_allow_html=True,
            )


# ===========================================================================
# GANTT CHART
# ===========================================================================
with tab_gantt:
    st.subheader("Gantt Chart")
    st.caption("Rotation blocks per resident. Sorted by PGY year then name.")

    gantt_limit = st.slider(
        "Max residents shown", min_value=10, max_value=len(filtered_res),
        value=min(40, len(filtered_res)), step=5,
    )

    gantt_res = filtered_res[:gantt_limit]
    gantt_res_ids = {r.resident_id for r in gantt_res}

    gantt_rows = []
    for a in schedule.assignments:
        if a.resident_id not in gantt_res_ids:
            continue
        res = res_map.get(a.resident_id)
        rot = rot_map.get(a.rotation_id)
        if not res or not rot:
            continue
        gantt_rows.append({
            "Resident":   f"{res.name} (PGY{res.pgy_year})",
            "Rotation":   rot.name,
            "Start":      a.start_week,
            "End":        a.end_week + 1,
            "Color":      rot.color,
            "Abbrev":     rot.abbrev,
        })

    if not gantt_rows:
        st.info("No assignments for selected residents.")
    else:
        df_g = pd.DataFrame(gantt_rows)

        # Gantt chart using week numbers on the x-axis
        fig_gantt2 = go.Figure()
        for _, row in df_g.iterrows():
            fig_gantt2.add_trace(go.Bar(
                x=[row["End"] - row["Start"]],
                y=[row["Resident"]],
                base=[row["Start"]],
                orientation="h",
                marker_color=row["Color"],
                text=row["Abbrev"],
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate=f"<b>{row['Resident']}</b><br>{row['Rotation']}<br>"
                              f"Weeks {row['Start']}–{row['End']-1}<extra></extra>",
                showlegend=False,
            ))

        # Add legend traces
        shown_rots = df_g["Rotation"].unique()
        for rot in rotations:
            if rot.name in shown_rots:
                fig_gantt2.add_trace(go.Bar(
                    x=[0], y=[""],
                    marker_color=rot.color,
                    name=rot.name,
                    showlegend=True,
                    orientation="h",
                ))

        fig_gantt2.update_layout(
            barmode="overlay",
            height=max(400, 16 * gantt_limit + 100),
            xaxis_title="Week",
            yaxis_title="",
            plot_bgcolor="white",
            legend=dict(orientation="h", y=1.05, x=0),
            margin=dict(l=200, r=20, t=60, b=40),
            xaxis=dict(range=[week_range[0], week_range[1] + 1]),
        )
        st.plotly_chart(fig_gantt2, use_container_width=True)


# ===========================================================================
# RESIDENT TIMELINE
# ===========================================================================
with tab_resident:
    st.subheader("Individual Resident Timeline")

    res_names = {r.resident_id: f"{r.name} (PGY{r.pgy_year})" for r in residents}
    selected_rid = st.selectbox(
        "Select resident",
        options=[r.resident_id for r in residents],
        format_func=lambda rid: res_names[rid],
    )

    res = res_map[selected_rid]
    st.markdown(f"**{res.name}** | PGY{res.pgy_year} | {res.resident_type.title()}")

    # Build rotation sequence
    seq = []
    for w in ay.all_weeks():
        # Show blackout weeks as Vacation for non-rotators
        if w in blackout_weeks and res.resident_type != "rotator":
            seq.append({"Week": w, "Rotation": "Vacation", "Abbrev": "Vac", "Color": "#374151"})
            continue
        a = schedule.get_resident_week(res.resident_id, w)
        if a:
            rot = rot_map.get(a.rotation_id)
            seq.append({
                "Week": w,
                "Rotation": rot.name if rot else a.rotation_id,
                "Abbrev": rot.abbrev if rot else a.rotation_id,
                "Color": rot.color if rot else "#CBD5E1",
            })
        else:
            seq.append({"Week": w, "Rotation": "Unassigned", "Abbrev": "?", "Color": "#E2E8F0"})

    df_seq = pd.DataFrame(seq)

    # Horizontal bar for each rotation block
    fig_res = go.Figure()
    block_start = None
    block_rot = None
    block_color = None

    def add_block(fig, s, e, rot_name, color, res_name):
        fig.add_trace(go.Bar(
            x=[e - s + 1],
            y=[res_name],
            base=[s],
            orientation="h",
            marker_color=color,
            text=f"{rot_name[:4]}",
            textposition="inside",
            insidetextanchor="middle",
            textfont=dict(size=9, color="white"),
            hovertemplate=f"<b>{rot_name}</b><br>Weeks {s}–{e}<extra></extra>",
            showlegend=False,
        ))

    prev_rot = None
    prev_color = None
    bs = 1
    for _, row in df_seq.iterrows():
        if row["Rotation"] != prev_rot:
            if prev_rot is not None:
                add_block(fig_res, bs, row["Week"] - 1, prev_rot, prev_color, res.name)
            prev_rot = row["Rotation"]
            prev_color = row["Color"]
            bs = row["Week"]
    if prev_rot:
        add_block(fig_res, bs, ay.total_weeks, prev_rot, prev_color, res.name)

    fig_res.update_layout(
        barmode="overlay",
        height=120,
        xaxis_title="Week",
        yaxis_title="",
        plot_bgcolor="white",
        showlegend=False,
        margin=dict(l=20, r=20, t=20, b=40),
        xaxis=dict(range=[0.5, ay.total_weeks + 0.5], dtick=4),
    )
    st.plotly_chart(fig_res, use_container_width=True)

    # Summary table for this resident
    rotation_counts = df_seq[~df_seq["Rotation"].isin(["VACATION", "Vacation", "Unassigned"])]["Rotation"].value_counts().reset_index()
    rotation_counts.columns = ["Rotation", "Weeks"]
    ip_rots = {r.rotation_id for r in rotations if r.rot_type.value == "Inpatient"}
    ip_weeks = df_seq[df_seq["Abbrev"].isin([rot_map.get(rid, type("R", (), {"abbrev": rid})).abbrev
                                              for rid in ip_rots])].shape[0]

    col_a, col_b, col_c = st.columns(3)
    col_a.metric("IP Weeks", ip_weeks)
    col_b.metric("Clinic Weeks", df_seq[df_seq["Rotation"].str.contains("Clinic")].shape[0])
    col_c.metric("OP/Elective Weeks", df_seq[df_seq["Abbrev"] == "OP"].shape[0])

    st.dataframe(rotation_counts, use_container_width=True, hide_index=True)


# ===========================================================================
# WEEKLY COUNTS
# ===========================================================================
with tab_weekly:
    st.subheader("📋 Staffing Report")
    st.caption(
        "Week-by-week headcounts vs. capacity targets. "
        "🟢 At/above target · 🟡 Below target · 🔴 Zero (no coverage this week)."
    )

    # ── Build week × rotation counts (all 48 weeks) ───────────────────────
    all_w = ay.all_weeks()
    weekly_data = []
    for w in all_w:
        row = {"Week": w}
        week_assigns = schedule.get_week_assignments(w)
        for rot in rotations:
            row[rot.abbrev] = sum(1 for a in week_assigns if a.rotation_id == rot.rotation_id)
        weekly_data.append(row)
    df_wk = pd.DataFrame(weekly_data).set_index("Week")

    # ── Stacked bar — full 48 weeks ───────────────────────────────────────
    fig_wk = go.Figure()
    for rot in rotations:
        if rot.abbrev in df_wk.columns and df_wk[rot.abbrev].sum() > 0:
            fig_wk.add_trace(go.Bar(
                name=rot.abbrev, x=df_wk.index, y=df_wk[rot.abbrev],
                marker_color=rot.color,
            ))
    fig_wk.update_layout(
        barmode="stack",
        title="Residents per Rotation per Week",
        height=400, plot_bgcolor="white",
        legend=dict(orientation="h", y=1.1),
        xaxis=dict(title="Week", tickmode="linear", dtick=4),
        yaxis_title="# Residents",
    )
    st.plotly_chart(fig_wk, use_container_width=True)

    st.markdown("---")

    # ── Rotations with fixed capacity targets ─────────────────────────────
    tracked_rots = [
        r for r in rotations
        if r.active
        and r.rotation_id not in {"OP", "Clinic", "Diamond"}
        and (r.senior_capacity + r.intern_capacity) > 0
    ]
    targets = {r.abbrev: r.senior_capacity + r.intern_capacity for r in tracked_rots}

    df_status = df_wk[[r.abbrev for r in tracked_rots]].copy()

    # Column headers include the target so the table is self-contained
    col_rename = {r.abbrev: f"{r.abbrev} (cap {targets[r.abbrev]})" for r in tracked_rots}
    df_display = df_status.rename(columns=col_rename)
    abbrev_list = [r.abbrev for r in tracked_rots]

    def _cell_style(df):
        """Return a same-shape DataFrame of CSS styles, one per cell."""
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for col, abbrev in zip(df.columns, abbrev_list):
            t = targets[abbrev]
            for w in df.index:
                v = int(df.loc[w, col])
                if v == 0 and t > 0:
                    styles.loc[w, col] = "background-color:#FEE2E2;color:#991B1B"
                elif v < t:
                    styles.loc[w, col] = "background-color:#FEF9C3;color:#854D0E"
                else:
                    styles.loc[w, col] = "background-color:#DCFCE7;color:#166534"
        return styles

    # Problem-week filter toggle
    show_all = st.toggle("Show all 48 weeks", value=False,
                         help="Off = problem weeks only; On = full year")
    problem_wks = [
        w for w in all_w
        if any(int(df_status.loc[w, r.abbrev]) < targets[r.abbrev] for r in tracked_rots)
    ]

    display_idx = all_w if show_all else problem_wks
    if not display_idx:
        st.success("✅ All rotations fully staffed every week!")
    else:
        if not show_all:
            st.warning(f"⚠️ **{len(problem_wks)} week(s)** with at least one understaffed rotation.")
        st.dataframe(
            df_display.loc[display_idx].style.apply(_cell_style, axis=None),
            use_container_width=True,
            height=min(600, 40 + 35 * len(display_idx)),
        )

    st.markdown("---")

    # ── Compliance summary per rotation ───────────────────────────────────
    st.markdown("**Compliance Summary**")
    comp_rows = []
    for rot in tracked_rots:
        t    = targets[rot.abbrev]
        vals = df_wk[rot.abbrev].values
        comp_rows.append({
            "Rotation":         rot.name,
            "Target / wk":      t,
            "Avg actual":       f"{vals.mean():.1f}",
            "Wks at target":    int((vals >= t).sum()),
            "Wks under":        int((vals < t).sum()),
            "Wks empty (0)":    int((vals == 0).sum()),
        })

    df_comp = pd.DataFrame(comp_rows)

    def _comp_style(df):
        styles = pd.DataFrame("", index=df.index, columns=df.columns)
        for i in df.index:
            if df.loc[i, "Wks empty (0)"] > 0:
                styles.loc[i, "Wks empty (0)"] = "background-color:#FEE2E2;color:#991B1B"
            if df.loc[i, "Wks under"] > 0:
                styles.loc[i, "Wks under"] = "background-color:#FEF9C3;color:#854D0E"
            if df.loc[i, "Wks at target"] == len(all_w):
                styles.loc[i, "Wks at target"] = "background-color:#DCFCE7;color:#166534"
        return styles

    st.dataframe(
        df_comp.style.apply(_comp_style, axis=None),
        use_container_width=True, hide_index=True,
    )
