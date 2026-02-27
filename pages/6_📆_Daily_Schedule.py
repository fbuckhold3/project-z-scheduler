"""
pages/6_Daily_Schedule.py
Expand the solved week-level schedule into a per-day view.

Four tabs:
  1. Calendar View   – per-resident day grid for a chosen week range
  2. Team Rotation   – MK group/floor assignments; NF coverage areas
  3. Coverage        – daily headcounts vs capacity targets
  4. Compliance      – max-consecutive stats + Excel export
"""
import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from core.daily import (
    build_daily_schedule, mk_off_group, mk_floor, mk_is_working,
    mk_week_roster, DailySchedule, DAY_NAMES, MK_TEAM_NAMES, MK_FLOORS,
    NF_SR_COVERS, NF_INT_COVERS,
)
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_residents,
)

st.set_page_config(page_title="Daily Schedule", page_icon="📆", layout="wide")

# ── Custom CSS ────────────────────────────────────────────────────────────────
st.markdown("""
<style>
.cal-wrap { overflow-x: auto; font-size: 9px; }
.cal-wrap table { border-collapse: collapse; min-width: max-content; }
.cal-wrap th {
    background: #1e40af; color: white;
    padding: 2px 5px; font-size: 8px; text-align: center;
    position: sticky; top: 0; z-index: 2; white-space: nowrap;
}
.cal-wrap td {
    padding: 2px 4px; text-align: center;
    border: 1px solid #e5e7eb;
    font-size: 8px; white-space: nowrap; min-width: 28px;
}
td.c-off  { background:#F3F4F6 !important; color:#9CA3AF; }
td.c-work { font-weight: 600; }
td.c-viol { outline: 2px solid #EF4444; }
td.wk-sep { border-left: 2px solid #6B7280 !important; }
.res-hdr  { text-align:left; font-weight:700; background:#F9FAFB;
            position:sticky; left:0; z-index:1; padding: 2px 6px; }
</style>
""", unsafe_allow_html=True)

# ── Session state guard ───────────────────────────────────────────────────────
if "rotations" not in st.session_state:
    st.session_state.rotations        = default_rotations()
    st.session_state.residents        = default_residents()
    st.session_state.rotator_programs = default_rotator_programs()
    st.session_state.academic_year    = default_academic_year()
    st.session_state.schedule         = None

schedule  = st.session_state.get("schedule")
if schedule is None:
    st.warning("No schedule built yet. Go to **🔧 Schedule Builder** (page 3) first.")
    st.stop()

residents  = st.session_state.residents
rotations  = st.session_state.rotations
ay         = st.session_state.academic_year
rot_map    = {r.rotation_id: r for r in rotations}
res_map    = {r.resident_id: r for r in residents}

st.title("📆 Daily Schedule")
st.caption("Day-by-day assignments derived from the solved week-level schedule.")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Settings")

    st.subheader("MarioKart cycle")
    mk_days_off = st.number_input(
        "Days off per turn", min_value=1, max_value=4, value=2,
        help="Consecutive days off each MK group gets per cycle turn. "
             "With 5 groups × 2 days = 10-day cycle."
    )
    max_consec = st.number_input(
        "Highlight if consecutive days ≥", min_value=5, max_value=14, value=7,
        help="Calendar cells get a red outline when a resident hits this streak."
    )

    st.divider()
    st.subheader("View")
    week_range = st.slider("Week range", 1, ay.total_weeks, (1, 6), step=1)
    week_start, week_end = week_range  # 1-indexed

    level_filter = st.multiselect(
        "Show levels", ["Senior", "Intern"], default=["Senior", "Intern"]
    )
    rot_filter_opts = ["All"] + sorted({a.rotation_id for a in schedule.assignments})
    rot_filter = st.selectbox("Filter by rotation", rot_filter_opts)

# ── Build daily schedule ──────────────────────────────────────────────────────
# Cache keyed on schedule identity + parameters
@st.cache_data(show_spinner="Expanding to day-level…")
def _build(sched_json: str, mk_days_off_: int, _residents, _rotations, _ay):
    from core.models import Schedule as Sched
    sched = Sched.from_json(sched_json)
    return build_daily_schedule(sched, _residents, _rotations, _ay,
                                mk_days_off=mk_days_off_)

ds: DailySchedule = _build(
    schedule.to_json(), int(mk_days_off), residents, rotations, ay
)

# ── KPI strip ─────────────────────────────────────────────────────────────────
avg_on      = np.mean([s.days_on          for s in ds.stats.values()])
avg_wknd    = np.mean([s.weekends_on      for s in ds.stats.values()])
max_consec_ = max(s.max_consecutive       for s in ds.stats.values())
n_viols     = sum(1 for s in ds.stats.values() if s.max_consecutive >= max_consec)

k1, k2, k3, k4, k5 = st.columns(5)
k1.metric("Residents", len(residents))
k2.metric("Avg days on", f"{avg_on:.0f}")
k3.metric("Avg weekend days", f"{avg_wknd:.0f}")
k4.metric("Max consecutive run", max_consec_)
k5.metric(f"Residents ≥ {max_consec} consec", n_viols,
          delta=None if n_viols == 0 else "⚠️",
          delta_color="inverse")

# ── Helper: filter residents for display ─────────────────────────────────────
def _visible_residents() -> list:
    want_levels = set()
    if "Senior" in level_filter:
        want_levels.add("senior")
    if "Intern" in level_filter:
        want_levels.add("intern")
    rlist = [r for r in residents if r.level.value in want_levels]
    if rot_filter != "All":
        # Keep only residents who have at least one assignment on rot_filter
        # in the selected week range
        rids = {
            a.resident_id for a in schedule.assignments
            if a.rotation_id == rot_filter
            and a.start_week <= week_end
            and a.end_week   >= week_start
        }
        rlist = [r for r in rlist if r.resident_id in rids]
    return rlist

# ── Rotation color lookup ─────────────────────────────────────────────────────
ROT_COLORS: dict[str, str] = {r.rotation_id: r.color for r in rotations}
ROT_COLORS.update({"Off": "#F3F4F6", "": "#FFFFFF"})

# ── Tab layout ────────────────────────────────────────────────────────────────
tab_cal, tab_team, tab_cov, tab_comp = st.tabs([
    "📅 Calendar View", "🎮 Team Rotation", "📊 Coverage", "✅ Compliance & Export"
])


# =============================================================================
# TAB 1 — CALENDAR VIEW
# =============================================================================
with tab_cal:
    st.caption(
        "One cell per day per resident. Coloured = rotation (working); "
        "grey = off-day within rotation; red outline = consecutive-day alert."
    )

    vis_res = _visible_residents()
    if not vis_res:
        st.info("No residents match the current filters.")
    else:
        day_start = (week_start - 1) * 7
        day_end   = week_end * 7  # exclusive

        # Build HTML table
        html = '<div class="cal-wrap"><table>'

        # ── Header rows ──────────────────────────────────────────────────────
        html += '<thead>'
        html += '<tr><th class="res-hdr" rowspan="2" style="min-width:90px">Resident</th>'
        for w in range(week_start, week_end + 1):
            sep = ' wk-sep' if True else ''
            html += (f'<th colspan="7" class="wk-sep" style="font-size:9px">'
                     f'Wk {w}</th>')
        html += '</tr><tr>'
        for w in range(week_start, week_end + 1):
            for i, dn in enumerate(DAY_NAMES):
                cls = "wk-sep" if i == 0 else ""
                html += f'<th class="{cls}">{dn}</th>'
        html += '</tr></thead><tbody>'

        # ── Resident rows ─────────────────────────────────────────────────────
        for res in vis_res:
            rid  = res.resident_id
            name = f"{res.name} (PGY{res.pgy_year})"
            html += f'<tr><td class="res-hdr">{name}</td>'
            consec = 0
            for d in range(day_start, day_end):
                entry = ds.resident_daily[rid][d] if d < ds.total_days else DayEntry("","",False)
                rot_id  = entry.rotation_id
                working = entry.working
                assign  = entry.assignment
                team    = entry.team

                consec = (consec + 1) if (working and rot_id) else 0
                viol   = consec >= max_consec

                # Cell CSS classes
                cls = ""
                if not working and rot_id:
                    cls = "c-off"
                elif working and rot_id:
                    cls = "c-work"
                if viol:
                    cls += " c-viol"
                if (d - day_start) % 7 == 0:
                    cls += " wk-sep"

                # Background colour
                bg = ROT_COLORS.get(rot_id, "#FFFFFF") if working and rot_id else "#F3F4F6"

                # Label: short abbrev
                if not working and rot_id:
                    label = "off"
                elif assign:
                    label = assign[:5]
                elif rot_id:
                    label = rot_id[:5]
                else:
                    label = ""

                tip = f"{name} | {DAY_NAMES[d % 7]} | {rot_id} — {assign}"
                if team:
                    tip += f" [{team}]"

                html += (f'<td class="{cls}" style="background:{bg}" '
                         f'title="{tip}">{label}</td>')
            html += '</tr>'

        html += '</tbody></table></div>'
        st.markdown(html, unsafe_allow_html=True)


# =============================================================================
# TAB 2 — TEAM ROTATION
# =============================================================================
with tab_team:
    st.caption(
        "MarioKart groups and Night Float coverage for the selected week range."
    )

    # ── MK rotations ─────────────────────────────────────────────────────────
    for rot_id, groups in ds.mk_groups.items():
        rot = rot_map.get(rot_id)
        if rot is None:
            continue

        # Are there any assignments on this rotation in the view range?
        in_range = any(
            a.rotation_id == rot_id
            and a.start_week <= week_end
            and a.end_week   >= week_start
            for a in schedule.assignments
        )
        if not in_range:
            continue

        st.subheader(f"🎮 {rot.name} — MarioKart Groups")
        n_days  = (week_end - week_start + 1) * 7
        n_teams = len(groups)
        floors  = MK_FLOORS.get(rot_id, [f"F{i}" for i in range(n_teams - 1)])

        # Who is in the view?  Residents with assignments in the window
        active_rids = {
            a.resident_id for a in schedule.assignments
            if a.rotation_id == rot_id
            and a.start_week <= week_end
            and a.end_week   >= week_start
        }

        html = '<div class="cal-wrap"><table>'
        html += '<thead>'
        html += '<tr><th style="min-width:60px">Group</th><th>Members (this period)</th>'
        for w in range(week_start, week_end + 1):
            html += f'<th colspan="7" class="wk-sep">Wk {w}</th>'
        html += '</tr><tr><th></th><th></th>'
        for w in range(week_start, week_end + 1):
            for i, dn in enumerate(DAY_NAMES):
                cls = "wk-sep" if i == 0 else ""
                html += f'<th class="{cls}">{dn}</th>'
        html += '</tr></thead><tbody>'

        team_colors = [
            "#FEE2E2","#DCFCE7","#EDE9FE","#FEF9C3","#DBEAFE",
        ]
        for g in groups:
            g_active = [rid for rid in g.resident_ids if rid in active_rids]
            member_names = ", ".join(
                res_map[rid].name for rid in g_active if rid in res_map
            ) or "—"
            bg = team_colors[g.group_idx % len(team_colors)]

            html += (f'<tr><td style="background:{bg};font-weight:700">'
                     f'{g.team_name}</td>'
                     f'<td style="font-size:8px;text-align:left">{member_names}</td>')

            for w in range(week_start, week_end + 1):
                w_day0 = (w - 1) * 7
                for di in range(7):
                    abs_d = w_day0 + di
                    cls   = "wk-sep" if di == 0 else ""
                    working = mk_is_working(g.group_idx, abs_d, n_teams, int(mk_days_off))
                    if not working:
                        html += f'<td class="c-off {cls}">off</td>'
                    else:
                        fl = mk_floor(g.group_idx, abs_d, floors, n_teams, int(mk_days_off)) \
                             if floors else "—"
                        html += (f'<td class="c-work {cls}" '
                                 f'style="background:{bg}">{fl}</td>')
            html += '</tr>'

        html += '</tbody></table></div>'
        st.markdown(html, unsafe_allow_html=True)
        st.markdown("---")

    # ── NF blocks ─────────────────────────────────────────────────────────────
    nf_visible = {
        k: b for k, b in ds.nf_blocks.items()
        if b.start_week <= week_end and b.end_week >= week_start
    }
    if nf_visible:
        st.subheader("🌙 Night Float Blocks")
        for (rot_id, sw, level), blk in sorted(nf_visible.items()):
            with st.expander(f"NF Wks {blk.start_week}–{blk.end_week}  [{level.title()}]",
                             expanded=True):
                n_days_blk = (blk.end_week - blk.start_week + 1) * 7
                day0       = (blk.start_week - 1) * 7
                html = '<div class="cal-wrap"><table><thead><tr><th>Resident</th>'
                for w in range(blk.start_week, blk.end_week + 1):
                    html += f'<th colspan="7" class="wk-sep">Wk {w}</th>'
                html += '</tr><tr><th></th>'
                for w in range(blk.start_week, blk.end_week + 1):
                    for i, dn in enumerate(DAY_NAMES):
                        html += f'<th class="{"wk-sep" if i==0 else ""}">{dn}</th>'
                html += '</tr></thead><tbody>'

                for ridx, rid in enumerate(blk.resident_ids):
                    rname = res_map[rid].name if rid in res_map else rid
                    html += f'<tr><td class="res-hdr">{rname}</td>'
                    for d in range(n_days_blk):
                        abs_d = day0 + d
                        cls   = "wk-sep" if d % 7 == 0 else ""
                        if d < len(blk.daily):
                            entry = next((e for e in blk.daily[d]
                                          if e["resident_idx"] == ridx), None)
                            if entry:
                                assign = entry["assignment"]
                                is_off = assign == "Off"
                                if is_off:
                                    html += f'<td class="c-off {cls}">off</td>'
                                else:
                                    html += (f'<td class="c-work {cls}" '
                                             f'style="background:#EDE9FE">'
                                             f'{assign[:6]}</td>')
                            else:
                                html += f'<td class="{cls}"></td>'
                        else:
                            html += f'<td class="{cls}"></td>'
                    html += '</tr>'

                html += '</tbody></table></div>'
                st.markdown(html, unsafe_allow_html=True)


# =============================================================================
# TAB 3 — COVERAGE
# =============================================================================
with tab_cov:
    st.caption(
        "Daily headcounts vs rotation capacity. "
        "Solid bar = program residents · striped bar = rotator credit · "
        "red dashed = full capacity target."
    )

    day_start = (week_start - 1) * 7
    day_end   = week_end * 7  # exclusive

    # Build x-axis labels (e.g. "W1 Mon")
    x_labels = []
    for w in range(week_start, week_end + 1):
        for dn in DAY_NAMES:
            x_labels.append(f"W{w} {dn}")

    # --- Rotator weekly credit per rotation ----------------------------------
    # Rotators don't produce Assignment objects, so we compute their average
    # weekly slot contribution from the rotator_programs config.
    rotator_programs = st.session_state.get("rotator_programs", [])
    rotator_credit_sr: dict[str, float] = {}   # rot_id -> avg sr slots/week
    rotator_credit_int: dict[str, float] = {}  # rot_id -> avg int slots/week
    active_week_count = max(ay.total_weeks - len(ay.blackout_weeks), 1)
    for prog in rotator_programs:
        total_weeks_prog = prog.total_rotators * prog.months_inpatient * (48 / 12)
        n_elig = max(len(prog.eligible_rotation_ids), 1)
        wk_per_rot = total_weeks_prog / n_elig
        for rot_id in prog.eligible_rotation_ids:
            avg_per_week = wk_per_rot / active_week_count
            if prog.slot_level == "intern":
                rotator_credit_int[rot_id] = rotator_credit_int.get(rot_id, 0) + avg_per_week
            else:
                rotator_credit_sr[rot_id] = rotator_credit_sr.get(rot_id, 0) + avg_per_week

    active_rots = [
        r for r in rotations
        if r.active
        and r.rotation_id in ds.coverage
        and any(
            ds.coverage[r.rotation_id][d] > 0
            for d in range(day_start, min(day_end, ds.total_days))
        )
    ]

    if not active_rots:
        st.info("No coverage data in this week range.")
    else:
        # Show 2 rotations side-by-side
        cols_per_row = 2
        for i in range(0, len(active_rots), cols_per_row):
            row_rots = active_rots[i: i + cols_per_row]
            cols = st.columns(len(row_rots))
            for col, rot in zip(cols, row_rots):
                rid    = rot.rotation_id
                counts = ds.coverage.get(rid, [0] * ds.total_days)
                y = [counts[d] if d < len(counts) else 0
                     for d in range(day_start, day_end)]
                target = rot.senior_capacity + rot.intern_capacity
                # Rotator credit (constant across all days for now)
                rot_credit = rotator_credit_sr.get(rid, 0) + rotator_credit_int.get(rid, 0)
                y_rot = [round(rot_credit, 2)] * len(y)

                fig = go.Figure()
                fig.add_bar(
                    x=x_labels, y=y,
                    marker_color=rot.color,
                    name="Program residents",
                    hovertemplate="%{x}<br>%{y} program residents<extra></extra>",
                )
                if rot_credit > 0.05:
                    fig.add_bar(
                        x=x_labels, y=y_rot,
                        marker_color="#A7F3D0",
                        marker_pattern_shape="/",
                        name=f"Rotator credit (~{rot_credit:.1f}/wk)",
                        hovertemplate="%{x}<br>~%{y:.1f} rotator slots<extra></extra>",
                    )
                    fig.update_layout(barmode="stack")
                if target > 0:
                    fig.add_hline(
                        y=target, line_dash="dash", line_color="#EF4444",
                        annotation_text=f"cap {target}",
                        annotation_position="top right",
                    )
                fig.update_layout(
                    title=rot.name,
                    height=240,
                    margin=dict(l=30, r=10, t=35, b=40),
                    showlegend=rot_credit > 0.05,
                    legend=dict(orientation="h", y=-0.35, font=dict(size=9)),
                    xaxis=dict(tickangle=45, tickfont=dict(size=8)),
                    yaxis=dict(title="# on", rangemode="tozero"),
                    plot_bgcolor="white",
                )
                col.plotly_chart(fig, use_container_width=True)

    # --- Rotator summary table -----------------------------------------------
    if rotator_programs:
        st.markdown("---")
        st.subheader("🔄 Rotator Program Contributions")
        rot_rows = []
        for prog in rotator_programs:
            total_wks = prog.total_rotators * prog.months_inpatient * (48 / 12)
            n_elig    = max(len(prog.eligible_rotation_ids), 1)
            rot_rows.append({
                "Specialty":        prog.specialty,
                "Rotators":         prog.total_rotators,
                "Months IP each":   prog.months_inpatient,
                "Total wks/yr":     f"{total_wks:.0f}",
                "Pool":             prog.slot_level,
                "Eligible rotations": ", ".join(prog.eligible_rotation_ids),
                "Avg wks/rot/yr":   f"{total_wks / n_elig:.1f}",
            })
        st.dataframe(pd.DataFrame(rot_rows), use_container_width=True, hide_index=True)
        st.caption(
            "ℹ️ Rotators fill slots within their eligible rotations but are not yet "
            "modelled as named individuals — they appear as credit in the stacked bars above."
        )


# =============================================================================
# TAB 4 — COMPLIANCE & EXPORT
# =============================================================================
with tab_comp:
    st.subheader("Per-Resident Stats (full year)")

    stat_rows = []
    for res in residents:
        rid = res.resident_id
        s   = ds.stats.get(rid)
        if s is None:
            continue
        stat_rows.append({
            "Resident":       res.name,
            "PGY":            res.pgy_year,
            "Level":          res.level.value,
            "Days On":        s.days_on,
            "Days Off (rot)": s.days_off_rot,
            "Weekends On":    s.weekends_on,
            "Max Consecutive":s.max_consecutive,
            "Alert":          "⚠️" if s.max_consecutive >= max_consec else "✓",
        })

    stat_df = pd.DataFrame(stat_rows)
    st.dataframe(
        stat_df.style.apply(
            lambda col: ["background:#FEE2E2" if v == "⚠️" else "" for v in col]
            if col.name == "Alert" else [""] * len(col),
            axis=0
        ),
        use_container_width=True,
        height=420,
    )

    # ── Fairness summary ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Fairness Summary")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Days On range",
              f"{stat_df['Days On'].min()}–{stat_df['Days On'].max()}")
    f2.metric("Days Off range",
              f"{stat_df['Days Off (rot)'].min()}–{stat_df['Days Off (rot)'].max()}")
    f3.metric("Weekend days range",
              f"{stat_df['Weekends On'].min()}–{stat_df['Weekends On'].max()}")
    f4.metric("Max-consec range",
              f"{stat_df['Max Consecutive'].min()}–{stat_df['Max Consecutive'].max()}")

    # ── Excel export ──────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📥 Export Day-Level Schedule")

    @st.cache_data(show_spinner="Building Excel…")
    def _export_excel(sched_json: str, mk_days_off_: int, _residents, _rotations, _ay):
        import io
        from core.models import Schedule as Sched
        sched  = Sched.from_json(sched_json)
        ds_ex  = build_daily_schedule(sched, _residents, _rotations, _ay,
                                      mk_days_off=mk_days_off_)
        rot_m  = {r.rotation_id: r for r in _rotations}
        buf    = io.BytesIO()
        with pd.ExcelWriter(buf, engine="xlsxwriter") as writer:
            wb  = writer.book
            hdr = wb.add_format({"bold": True, "bg_color": "#1e40af",
                                 "font_color": "white", "border": 1})
            off = wb.add_format({"font_color": "#9CA3AF", "border": 1})
            wrk = wb.add_format({"bold": True, "border": 1})

            for level_name, is_sr in [("Senior", True), ("Intern", False)]:
                ws   = wb.add_worksheet(f"{level_name} Daily")
                rlist = [r for r in _residents if r.is_senior == is_sr]

                # Column headers: Resident | W1_Mon | W1_Tue | …
                ws.write(0, 0, "Resident", hdr)
                col = 1
                for w in range(1, _ay.total_weeks + 1):
                    for dn in DAY_NAMES:
                        ws.write(0, col, f"W{w}_{dn}", hdr)
                        col += 1

                for row_i, res in enumerate(rlist, start=1):
                    ws.write(row_i, 0, res.name)
                    col = 1
                    for w in range(1, _ay.total_weeks + 1):
                        for d in range(7):
                            abs_d = (w - 1) * 7 + d
                            if abs_d < ds_ex.total_days:
                                entry = ds_ex.resident_daily[res.resident_id][abs_d]
                                if entry.working and entry.rotation_id:
                                    ws.write(row_i, col, entry.assignment, wrk)
                                elif entry.rotation_id:
                                    ws.write(row_i, col, "off", off)
                            col += 1

                ws.set_column(0, 0, 18)
                ws.set_column(1, _ay.total_weeks * 7, 5)

        buf.seek(0)
        return buf.read()

    excel_bytes = _export_excel(
        schedule.to_json(), int(mk_days_off), residents, rotations, ay
    )
    st.download_button(
        "⬇️ Download Daily Schedule (Excel)",
        data=excel_bytes,
        file_name="daily_schedule.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
