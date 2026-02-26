"""
pages/2_Capacity_Calculator.py
Feasibility analysis: are we numerically able to staff all rotations?
"""
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_residents,
)
from core.feasibility import check_feasibility, six_week_ip_pressure

st.set_page_config(page_title="Capacity Calculator", page_icon="📊", layout="wide")

if "rotations" not in st.session_state:
    st.session_state.rotations          = default_rotations()
    st.session_state.residents          = default_residents()
    st.session_state.rotator_programs   = default_rotator_programs()
    st.session_state.academic_year      = default_academic_year()
    st.session_state.schedule           = None
    st.session_state.feasibility        = None
    st.session_state.solve_result       = None

st.title("📊 Capacity Calculator")
st.caption(
    "Run this before building the schedule. "
    "It checks whether your roster can mathematically cover all rotations."
)

if st.button("▶️ Run Feasibility Analysis", type="primary"):
    with st.spinner("Calculating…"):
        result = check_feasibility(
            residents=st.session_state.residents,
            rotations=st.session_state.rotations,
            rotator_programs=st.session_state.rotator_programs,
            academic_year=st.session_state.academic_year,
        )
        st.session_state.feasibility = result

f = st.session_state.feasibility
if f is None:
    st.info("Click **Run Feasibility Analysis** above to check your configuration.")
    st.stop()

# ---------------------------------------------------------------------------
# Overall verdict banner
# ---------------------------------------------------------------------------
if f.feasible:
    st.success(
        f"✅ **FEASIBLE** — Your configuration can staff all required rotations.  "
        f"Senior buffer: **{f.senior_ip_gap:+.0f} wks** | Intern buffer: **{f.intern_ip_gap:+.0f} wks**"
    )
else:
    st.error(
        f"❌ **INFEASIBLE** — Cannot staff all required rotations with current configuration."
    )

for w in f.warnings:
    st.warning(w)

# ---------------------------------------------------------------------------
# Headline metrics
# ---------------------------------------------------------------------------
st.markdown("---")
mc = st.columns(6)
mc[0].metric("Seniors", f.n_seniors)
mc[1].metric("Interns",  f.n_interns)
mc[2].metric("Active Weeks", f.active_weeks)
mc[3].metric("Senior IP Buffer", f"{f.senior_ip_gap:+.0f} wks",
             delta_color="normal" if f.senior_ip_gap >= 0 else "inverse")
mc[4].metric("Intern IP Buffer",  f"{f.intern_ip_gap:+.0f} wks",
             delta_color="normal" if f.intern_ip_gap >= 0 else "inverse")
mc[5].metric("Clinic/wk target", f"~{f.clinic_target_per_week:.0f} residents")

# ---------------------------------------------------------------------------
# Resident-week waterfall: available → clinic → IP demand
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Resident-Week Budget")

col_s, col_i = st.columns(2)

def waterfall_chart(title, avail, clinic, ip_demand, ip_soft, gap):
    categories = [
        "Available", "– Clinic", "IP Available",
        "– IP Demand (hard)", "IP Buffer", "– IP Demand (soft)",
    ]
    values    = [avail,   -clinic,   avail - clinic,  -ip_demand, gap, -ip_soft]
    measures  = ["absolute", "relative", "total",
                 "relative", "total", "relative"]
    colors    = ["#3B82F6", "#14B8A6", "#6366F1",
                 "#EF4444",
                 "#10B981" if gap >= 0 else "#EF4444",
                 "#F59E0B"]
    fig = go.Figure(go.Waterfall(
        orientation="v",
        measure=measures,
        x=categories,
        y=values,
        connector={"line": {"color": "#94A3B8"}},
        decreasing={"marker": {"color": "#EF4444"}},
        increasing={"marker": {"color": "#10B981"}},
        totals={"marker": {"color": "#3B82F6"}},
        text=[f"{abs(v):.0f}" for v in values],
        textposition="outside",
    ))
    fig.update_layout(
        title=title,
        height=380,
        showlegend=False,
        plot_bgcolor="white",
        yaxis_title="Resident-Weeks",
    )
    return fig

with col_s:
    st.plotly_chart(
        waterfall_chart(
            "Senior Resident-Weeks",
            f.senior_weeks_available, f.senior_clinic_total,
            f.senior_ip_demanded, f.senior_ip_soft, f.senior_ip_gap,
        ),
        use_container_width=True,
    )

with col_i:
    st.plotly_chart(
        waterfall_chart(
            "Intern Resident-Weeks",
            f.intern_weeks_available, f.intern_clinic_total,
            f.intern_ip_demanded, f.intern_ip_soft, f.intern_ip_gap,
        ),
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Per-rotation demand table
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Per-Rotation Demand")

rot_map = {r.rotation_id: r for r in st.session_state.rotations}
rows = []
for rd in f.rotation_demands:
    rot = rot_map.get(rd.rotation_id)
    color = rot.color if rot else "#999"
    rows.append({
        "":           f'<span style="display:inline-block;width:12px;height:12px;'
                      f'border-radius:50%;background:{color}"></span>',
        "Rotation":   rd.name,
        "Type":       rd.rot_type,
        "Required":   "✅" if rd.required else "⭕ soft",
        "Sr Gross":   rd.senior_weeks_gross,
        "Sr Rotator": f"–{rd.rotator_senior_credit:.0f}",
        "Sr Net":     rd.senior_weeks_demanded,
        "Int Gross":  rd.intern_weeks_gross,
        "Int Rotator":f"–{rd.rotator_intern_credit:.0f}",
        "Int Net":    rd.intern_weeks_demanded,
    })

df_rot = pd.DataFrame(rows)
st.dataframe(
    df_rot.drop(columns=[""]),
    use_container_width=True,
    hide_index=True,
)

# Bar chart: net demand vs available per level
fig_bar = go.Figure()
rotation_names = [rd.name for rd in f.rotation_demands if rd.senior_weeks_demanded > 0 or rd.intern_weeks_demanded > 0]
fig_bar.add_trace(go.Bar(
    name="Senior wks demanded",
    x=[rd.name for rd in f.rotation_demands],
    y=[rd.senior_weeks_demanded for rd in f.rotation_demands],
    marker_color="#3B82F6",
))
fig_bar.add_trace(go.Bar(
    name="Intern wks demanded",
    x=[rd.name for rd in f.rotation_demands],
    y=[rd.intern_weeks_demanded for rd in f.rotation_demands],
    marker_color="#10B981",
))
fig_bar.update_layout(
    barmode="group",
    title="Resident-Weeks Demanded per Rotation (net of rotator credit)",
    height=340,
    plot_bgcolor="white",
    legend=dict(orientation="h", y=1.1),
)
st.plotly_chart(fig_bar, use_container_width=True)

# ---------------------------------------------------------------------------
# 6-week IP pressure
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Max-3-IP-Per-6-Week Constraint Pressure")

col_sp, col_ip = st.columns(2)
total_res = f.n_seniors + f.n_interns

pressure_s = six_week_ip_pressure(
    f.n_seniors,
    f.senior_ip_demanded / max(f.n_seniors, 1),
    f.active_weeks,
)
pressure_i = six_week_ip_pressure(
    f.n_interns,
    f.intern_ip_demanded / max(f.n_interns, 1),
    f.active_weeks,
)

with col_sp:
    val = pressure_s["avg_ip_per_6wk_window"]
    color = "🔴" if pressure_s["exceeds_cap"] else ("🟡" if val > 2.5 else "🟢")
    st.metric(
        "Avg senior IP weeks per 6-wk window",
        f"{color} {val:.2f}",
        delta=f"{pressure_s['headroom']:+.2f} headroom to cap of 3",
        delta_color="normal" if pressure_s["headroom"] >= 0 else "inverse",
    )

with col_ip:
    val = pressure_i["avg_ip_per_6wk_window"]
    color = "🔴" if pressure_i["exceeds_cap"] else ("🟡" if val > 2.5 else "🟢")
    st.metric(
        "Avg intern IP weeks per 6-wk window",
        f"{color} {val:.2f}",
        delta=f"{pressure_i['headroom']:+.2f} headroom to cap of 3",
        delta_color="normal" if pressure_i["headroom"] >= 0 else "inverse",
    )

# ---------------------------------------------------------------------------
# Rotator contribution summary
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Rotator Program Contributions")

if f.rotator_summary:
    df_r = pd.DataFrame(f.rotator_summary)
    st.dataframe(df_r, use_container_width=True, hide_index=True)
    total_rw = sum(r["total_weeks"] for r in f.rotator_summary)
    st.caption(f"Total rotator-weeks covering our rotations: **{total_rw:.0f} wks/year**")
else:
    st.info("No rotator programs configured.")

# ---------------------------------------------------------------------------
# Clinic distribution
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Clinic Distribution Pattern")

st.markdown(
    f"Target residents per clinic week (within each 6-week cycle): "
    f"`{'  →  '.join(str(x) for x in f.clinic_pattern)}`"
)
fig_clinic = px.bar(
    x=[f"Wk {i+1} of cycle" for i in range(6)],
    y=f.clinic_pattern,
    labels={"x": "Week within 6-week cycle", "y": "# residents in clinic"},
    color=f.clinic_pattern,
    color_continuous_scale="Teal",
    title="Target Clinic Headcount per Week Within Cycle",
)
fig_clinic.update_layout(height=280, showlegend=False, plot_bgcolor="white",
                          coloraxis_showscale=False)
st.plotly_chart(fig_clinic, use_container_width=True)
