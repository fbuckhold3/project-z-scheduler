"""
pages/5_Export.py
Export the schedule as Excel (formatted), CSV, or JSON.
"""
import streamlit as st
import pandas as pd
import io
import json
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_residents,
)

st.set_page_config(page_title="Export", page_icon="📥", layout="wide")

if "rotations" not in st.session_state:
    st.session_state.rotations          = default_rotations()
    st.session_state.residents          = default_residents()
    st.session_state.rotator_programs   = default_rotator_programs()
    st.session_state.academic_year      = default_academic_year()
    st.session_state.schedule           = None
    st.session_state.feasibility        = None
    st.session_state.solve_result       = None

st.title("📥 Export")
st.caption("Download the schedule in your preferred format.")

if st.session_state.schedule is None:
    st.warning("No schedule to export. Go to **🔧 Schedule Builder** first.")
    st.stop()

schedule  = st.session_state.schedule
residents = st.session_state.residents
rotations = st.session_state.rotations
ay        = schedule.academic_year

rot_map   = {r.rotation_id: r for r in rotations}
res_map   = {r.resident_id: r for r in residents}

# ---------------------------------------------------------------------------
# Build DataFrames
# ---------------------------------------------------------------------------

@st.cache_data
def build_wide_df(_schedule, _residents, _rotations):
    """Wide format: one row per resident, one column per week."""
    return _schedule.to_dataframe(_residents, _rotations)

@st.cache_data
def build_long_df(_schedule, _residents, _rotations):
    """Long format: one row per assignment block."""
    _rot_map = {r.rotation_id: r for r in _rotations}
    _res_map = {r.resident_id: r for r in _residents}
    rows = []
    for a in _schedule.assignments:
        res = _res_map.get(a.resident_id)
        rot = _rot_map.get(a.rotation_id)
        rows.append({
            "resident_id":     a.resident_id,
            "name":            res.name if res else "",
            "pgy_year":        res.pgy_year if res else "",
            "resident_type":   res.resident_type if res else "",
            "rotation_id":     a.rotation_id,
            "rotation_name":   rot.name if rot else a.rotation_id,
            "rotation_abbrev": rot.abbrev if rot else a.rotation_id,
            "rotation_type":   rot.rot_type.value if rot else "",
            "start_week":      a.start_week,
            "end_week":        a.end_week,
            "duration_weeks":  a.duration_weeks,
        })
    return pd.DataFrame(rows)

df_wide = build_wide_df(schedule, residents, rotations)
df_long = build_long_df(schedule, residents, rotations)

# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------
st.subheader("Preview")
tab_prev_w, tab_prev_l = st.tabs(["Wide (resident × week)", "Long (assignments)"])
with tab_prev_w:
    st.dataframe(df_wide.head(20), use_container_width=True)
    st.caption(f"{len(df_wide)} residents × {len(df_wide.columns)} columns")
with tab_prev_l:
    st.dataframe(df_long.head(30), use_container_width=True)
    st.caption(f"{len(df_long)} assignment blocks")

st.markdown("---")

# ---------------------------------------------------------------------------
# Downloads
# ---------------------------------------------------------------------------
st.subheader("Download")

col1, col2, col3 = st.columns(3)

# --- Excel (formatted) ---
with col1:
    st.markdown("**📊 Excel (formatted)**")
    st.markdown("Color-coded grid + assignments sheet + violation report.")

    @st.cache_data
    def build_excel(_schedule, _residents, _rotations, _feasibility):
        """Build a formatted Excel workbook."""
        import xlsxwriter

        buf = io.BytesIO()
        wb  = xlsxwriter.Workbook(buf, {"in_memory": True})

        _rot_map = {r.rotation_id: r for r in _rotations}
        _res_map = {r.resident_id: r for r in _residents}
        _ay = _schedule.academic_year
        weeks = _ay.all_weeks(include_blackout=True)

        # ------ Sheet 1: Schedule Grid ------
        ws = wb.add_worksheet("Schedule Grid")

        header_fmt = wb.add_format({
            "bold": True, "bg_color": "#1E293B", "font_color": "#FFFFFF",
            "border": 1, "font_size": 8,
        })
        default_fmt = wb.add_format({"border": 1, "font_size": 8})
        blackout_fmt = wb.add_format({
            "bg_color": "#374151", "font_color": "#FFFFFF",
            "border": 1, "font_size": 8,
        })

        # Cache rotation cell formats
        rot_fmt_cache = {}
        for rot in _rotations:
            # Convert hex to RGB
            h = rot.color.lstrip("#")
            r_val, g_val, b_val = tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
            lum = 0.299 * r_val + 0.587 * g_val + 0.114 * b_val
            font_col = "#000000" if lum > 140 else "#FFFFFF"
            rot_fmt_cache[rot.rotation_id] = wb.add_format({
                "bg_color": rot.color,
                "font_color": font_col,
                "border": 1,
                "font_size": 8,
                "align": "center",
            })

        # Header row
        ws.write(0, 0, "Resident",  header_fmt)
        ws.write(0, 1, "PGY",       header_fmt)
        ws.write(0, 2, "Type",      header_fmt)
        ws.set_column(0, 0, 20)
        ws.set_column(1, 1, 5)
        ws.set_column(2, 2, 12)

        for j, w in enumerate(weeks):
            ws.write(0, j + 3, f"W{w:02d}", header_fmt)
            ws.set_column(j + 3, j + 3, 5)

        # Data rows
        for i, res in enumerate(_residents):
            ws.write(i + 1, 0, res.name,          default_fmt)
            ws.write(i + 1, 1, res.pgy_year,       default_fmt)
            ws.write(i + 1, 2, res.resident_type,  default_fmt)
            for j, w in enumerate(weeks):
                col = j + 3
                if _ay.is_blackout(w):
                    ws.write(i + 1, col, "—", blackout_fmt)
                else:
                    a = _schedule.get_resident_week(res.resident_id, w)
                    if a:
                        rot = _rot_map.get(a.rotation_id)
                        fmt = rot_fmt_cache.get(a.rotation_id, default_fmt)
                        ws.write(i + 1, col, rot.abbrev if rot else a.rotation_id, fmt)
                    else:
                        ws.write(i + 1, col, "", default_fmt)

        ws.freeze_panes(1, 3)

        # ------ Sheet 2: Assignments (long) ------
        ws2 = wb.add_worksheet("Assignments")
        headers2 = ["Name", "PGY", "Type", "Rotation", "Start Wk", "End Wk", "Duration Wks"]
        for j, h in enumerate(headers2):
            ws2.write(0, j, h, header_fmt)
        for i, a in enumerate(_schedule.assignments):
            res = _res_map.get(a.resident_id)
            rot = _rot_map.get(a.rotation_id)
            ws2.write(i + 1, 0, res.name if res else a.resident_id)
            ws2.write(i + 1, 1, res.pgy_year if res else "")
            ws2.write(i + 1, 2, res.resident_type if res else "")
            ws2.write(i + 1, 3, rot.name if rot else a.rotation_id)
            ws2.write(i + 1, 4, a.start_week)
            ws2.write(i + 1, 5, a.end_week)
            ws2.write(i + 1, 6, a.duration_weeks)

        # ------ Sheet 3: Violations ------
        if _feasibility and hasattr(_feasibility, "warnings"):
            ws3 = wb.add_worksheet("Feasibility & Violations")
            ws3.write(0, 0, "Status", header_fmt)
            ws3.write(0, 1, "✅ Feasible" if _feasibility.feasible else "❌ Infeasible", default_fmt)
            ws3.write(2, 0, "Warning / Violation", header_fmt)
            for i, w_text in enumerate(_feasibility.warnings):
                ws3.write(i + 3, 0, w_text)

        wb.close()
        buf.seek(0)
        return buf.read()

    excel_bytes = build_excel(
        schedule, residents, rotations, st.session_state.feasibility
    )
    st.download_button(
        "⬇️ Download Excel",
        data=excel_bytes,
        file_name=f"schedule_{ay.label}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        use_container_width=True,
    )

# --- CSV ---
with col2:
    st.markdown("**📄 CSV (wide)**")
    st.markdown("One row per resident, one column per week — easy to open in Excel.")
    csv_bytes = df_wide.to_csv().encode()
    st.download_button(
        "⬇️ Download CSV (wide)",
        data=csv_bytes,
        file_name=f"schedule_{ay.label}_wide.csv",
        mime="text/csv",
        use_container_width=True,
    )
    st.markdown("&nbsp;")
    st.markdown("**📄 CSV (long)**")
    st.markdown("One row per assignment block — for analysis in pandas/R/Excel.")
    csv_long = df_long.to_csv(index=False).encode()
    st.download_button(
        "⬇️ Download CSV (long)",
        data=csv_long,
        file_name=f"schedule_{ay.label}_long.csv",
        mime="text/csv",
        use_container_width=True,
    )

# --- JSON ---
with col3:
    st.markdown("**🗄️ JSON (full)**")
    st.markdown("Complete serialised schedule including all assignments and academic year config. Importable back into the app.")
    json_str = schedule.to_json()
    st.download_button(
        "⬇️ Download JSON",
        data=json_str.encode(),
        file_name=f"schedule_{ay.label}.json",
        mime="application/json",
        use_container_width=True,
    )

# ---------------------------------------------------------------------------
# Import schedule from JSON
# ---------------------------------------------------------------------------
st.markdown("---")
st.subheader("Import Schedule from JSON")
st.caption("Load a previously exported schedule back into the app.")

uploaded_json = st.file_uploader("Upload JSON schedule", type=["json"])
if uploaded_json:
    try:
        from core.models import Schedule as Sched
        loaded = Sched.from_json(uploaded_json.read().decode())
        st.session_state.schedule = loaded
        st.success(f"Loaded schedule: {loaded.academic_year.label} ({len(loaded.assignments)} blocks)")
        st.rerun()
    except Exception as e:
        st.error(f"Failed to load: {e}")
