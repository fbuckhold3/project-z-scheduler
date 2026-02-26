"""
pages/1_Configuration.py
Set up rotations, resident roster, rotator programs, and academic year.
"""
import streamlit as st
import pandas as pd
import io
from core.models import (
    Rotation, RotationType, RotationPattern,
    RotatorProgram, Resident, AcademicYear,
)
from core.defaults import (
    default_academic_year, default_rotations,
    default_rotator_programs, default_residents,
)

st.set_page_config(page_title="Configuration", page_icon="⚙️", layout="wide")

# Ensure session state is initialised (in case user lands here directly)
if "rotations" not in st.session_state:
    st.session_state.rotations          = default_rotations()
    st.session_state.residents          = default_residents()
    st.session_state.rotator_programs   = default_rotator_programs()
    st.session_state.academic_year      = default_academic_year()
    st.session_state.schedule           = None
    st.session_state.feasibility        = None
    st.session_state.solve_result       = None

st.title("⚙️ Configuration")
st.caption("Review and edit all scheduling inputs before running the capacity calculator.")

# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
tab_ay, tab_rot, tab_res, tab_rotators = st.tabs([
    "📅 Academic Year",
    "🔄 Rotations",
    "👥 Residents",
    "🔁 Rotator Programs",
])


# ===========================================================================
# TAB 1 — Academic Year
# ===========================================================================
with tab_ay:
    st.subheader("Academic Year Settings")
    ay: AcademicYear = st.session_state.academic_year

    col1, col2 = st.columns(2)
    with col1:
        new_label = st.text_input("Year label", value=ay.label)
        new_total = st.number_input("Total weeks", min_value=40, max_value=56,
                                     value=ay.total_weeks, step=1)
        new_start = st.text_input("Start date (YYYY-MM-DD)", value=ay.start_date)

    with col2:
        st.markdown("**Blackout weeks** (comma-separated week numbers, 1-indexed)")
        blackout_str = st.text_input(
            "Blackout weeks",
            value=", ".join(str(w) for w in ay.blackout_weeks),
            help="Weeks when the program is closed (July 4 ramp, Dec holiday, etc.)",
        )
        try:
            new_blackout = [int(x.strip()) for x in blackout_str.split(",") if x.strip()]
        except ValueError:
            st.error("Please enter comma-separated integers.")
            new_blackout = ay.blackout_weeks

        st.markdown(f"**Active weeks:** {new_total - len(new_blackout)}")
        st.markdown(f"**Blackout weeks:** {sorted(new_blackout)}")

    if st.button("💾 Save Academic Year", key="save_ay"):
        st.session_state.academic_year = AcademicYear(
            label=new_label,
            total_weeks=int(new_total),
            start_date=new_start,
            blackout_weeks=sorted(new_blackout),
        )
        st.session_state.schedule = None
        st.session_state.feasibility = None
        st.success("Academic year saved. Re-run Capacity Calculator and Schedule Builder.")


# ===========================================================================
# TAB 2 — Rotations
# ===========================================================================
with tab_rot:
    st.subheader("Rotation Definitions")
    st.markdown(
        "Edit capacities, patterns, and notes for each rotation. "
        "Toggle **Active** off to exclude from scheduling (e.g. Diamond placeholder)."
    )

    rotations: list[Rotation] = st.session_state.rotations

    # Display as editable dataframe
    rot_data = pd.DataFrame([{
        "ID":               r.rotation_id,
        "Name":             r.name,
        "Abbrev":           r.abbrev,
        "Type":             r.rot_type.value,
        "Pattern":          r.pattern.value,
        "Senior Cap":       r.senior_capacity,
        "Intern Cap":       r.intern_capacity,
        "Min Block Wks":    r.min_block_weeks,
        "Max Block Wks":    r.max_block_weeks,
        "Eligible Levels":  ", ".join(r.eligible_levels),
        "Required":         r.required,
        "Active":           r.active,
        "Color":            r.color,
        "Notes":            r.notes,
    } for r in rotations])

    edited_rot = st.data_editor(
        rot_data,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Type": st.column_config.SelectboxColumn(
                options=["Inpatient", "Outpatient", "Mixed", "Backup"]
            ),
            "Pattern": st.column_config.SelectboxColumn(
                options=["standard", "MK", "ABABA", "NF", "clinic", "backup"]
            ),
            "Required": st.column_config.CheckboxColumn(),
            "Active":   st.column_config.CheckboxColumn(),
            "Color": st.column_config.TextColumn(help="Hex color for schedule grid"),
            "Senior Cap": st.column_config.NumberColumn(min_value=0, max_value=20, step=1),
            "Intern Cap":  st.column_config.NumberColumn(min_value=0, max_value=20, step=1),
        },
        key="rot_editor",
    )

    if st.button("💾 Save Rotations", key="save_rot"):
        new_rots = []
        for _, row in edited_rot.iterrows():
            try:
                new_rots.append(Rotation(
                    rotation_id=str(row["ID"]),
                    name=str(row["Name"]),
                    abbrev=str(row["Abbrev"]),
                    rot_type=RotationType(row["Type"]),
                    pattern=RotationPattern(row["Pattern"]),
                    senior_capacity=int(row["Senior Cap"]),
                    intern_capacity=int(row["Intern Cap"]),
                    min_block_weeks=int(row["Min Block Wks"]),
                    max_block_weeks=int(row["Max Block Wks"]),
                    eligible_levels=[x.strip() for x in str(row["Eligible Levels"]).split(",") if x.strip()],
                    required=bool(row["Required"]),
                    active=bool(row["Active"]),
                    color=str(row["Color"]),
                    notes=str(row["Notes"]),
                ))
            except Exception as e:
                st.error(f"Row error: {e}")
        if new_rots:
            st.session_state.rotations = new_rots
            st.session_state.schedule = None
            st.session_state.feasibility = None
            st.success(f"Saved {len(new_rots)} rotations.")

    # Color legend preview
    st.markdown("**Color legend preview:**")
    cols = st.columns(len(rotations))
    for i, rot in enumerate(rotations):
        with cols[i]:
            st.markdown(
                f'<div style="background:{rot.color};border-radius:4px;padding:4px 6px;'
                f'text-align:center;font-size:12px;color:white;font-weight:bold">'
                f'{rot.abbrev}</div>',
                unsafe_allow_html=True,
            )


# ===========================================================================
# TAB 3 — Residents
# ===========================================================================
with tab_res:
    st.subheader("Resident Roster")

    col1, col2 = st.columns([2, 1])
    with col1:
        st.markdown(
            "Upload a CSV or edit the table inline. "
            "Required columns: `name`, `pgy_year`, `resident_type`. "
            "Optional: `start_week`, `end_week`, `notes`."
        )
    with col2:
        # CSV download template
        template_df = pd.DataFrame({
            "name": ["Smith, J.", "Doe, A."],
            "pgy_year": [3, 1],
            "resident_type": ["categorical", "categorical"],
            "start_week": [1, 1],
            "end_week": [48, 48],
            "notes": ["", ""],
        })
        csv_bytes = template_df.to_csv(index=False).encode()
        st.download_button(
            "⬇️ Download CSV Template",
            data=csv_bytes,
            file_name="resident_roster_template.csv",
            mime="text/csv",
        )

    # Upload
    uploaded = st.file_uploader(
        "Upload roster CSV", type=["csv"], key="res_upload"
    )
    if uploaded:
        try:
            df_up = pd.read_csv(uploaded)
            required_cols = {"name", "pgy_year", "resident_type"}
            if not required_cols.issubset(set(df_up.columns)):
                st.error(f"Missing columns. Required: {required_cols}")
            else:
                new_residents = []
                for i, row in df_up.iterrows():
                    new_residents.append(Resident(
                        resident_id=f"R{i+1:03d}",
                        name=str(row["name"]),
                        pgy_year=int(row["pgy_year"]),
                        resident_type=str(row["resident_type"]),
                        start_week=int(row.get("start_week", 1)),
                        end_week=int(row.get("end_week", 48)),
                        notes=str(row.get("notes", "")),
                    ))
                st.session_state.residents = new_residents
                st.session_state.schedule = None
                st.session_state.feasibility = None
                st.success(f"Loaded {len(new_residents)} residents from CSV.")
        except Exception as e:
            st.error(f"Error reading CSV: {e}")

    # Inline editor
    residents: list[Resident] = st.session_state.residents
    res_df = pd.DataFrame([{
        "ID":           r.resident_id,
        "Name":         r.name,
        "PGY":          r.pgy_year,
        "Type":         r.resident_type,
        "Level":        r.level.value,
        "Start Week":   r.start_week,
        "End Week":     r.end_week,
        "Notes":        r.notes,
    } for r in residents])

    edited_res = st.data_editor(
        res_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "PGY": st.column_config.SelectboxColumn(options=[1, 2, 3]),
            "Type": st.column_config.SelectboxColumn(
                options=["categorical", "preliminary"]
            ),
            "Level": st.column_config.TextColumn(disabled=True),
            "Start Week": st.column_config.NumberColumn(min_value=1, max_value=48, step=1),
            "End Week":   st.column_config.NumberColumn(min_value=1, max_value=48, step=1),
        },
        key="res_editor",
    )

    col_a, col_b = st.columns(2)
    with col_a:
        if st.button("💾 Save Residents", key="save_res"):
            new_res = []
            for i, (_, row) in enumerate(edited_res.iterrows()):
                try:
                    rid = str(row.get("ID", f"R{i+1:03d}")) or f"R{i+1:03d}"
                    new_res.append(Resident(
                        resident_id=rid,
                        name=str(row["Name"]),
                        pgy_year=int(row["PGY"]),
                        resident_type=str(row["Type"]),
                        start_week=int(row.get("Start Week", 1)),
                        end_week=int(row.get("End Week", 48)),
                        notes=str(row.get("Notes", "")),
                    ))
                except Exception as e:
                    st.error(f"Row {i} error: {e}")
            if new_res:
                st.session_state.residents = new_res
                st.session_state.schedule = None
                st.session_state.feasibility = None
                st.success(f"Saved {len(new_res)} residents.")

    with col_b:
        if st.button("🔄 Reset to Defaults", key="reset_res"):
            st.session_state.residents = default_residents()
            st.session_state.schedule = None
            st.rerun()

    # Summary stats
    st.markdown("---")
    st.markdown("**Roster Summary**")
    summary_cols = st.columns(4)
    n_pgy3 = sum(1 for r in residents if r.pgy_year == 3)
    n_pgy2 = sum(1 for r in residents if r.pgy_year == 2)
    n_pgy1 = sum(1 for r in residents if r.pgy_year == 1)
    n_total = len(residents)
    with summary_cols[0]: st.metric("Total", n_total)
    with summary_cols[1]: st.metric("PGY3 (senior)", n_pgy3)
    with summary_cols[2]: st.metric("PGY2 (senior)", n_pgy2)
    with summary_cols[3]: st.metric("PGY1 (intern)", n_pgy1)


# ===========================================================================
# TAB 4 — Rotator Programs
# ===========================================================================
with tab_rotators:
    st.subheader("Rotator Programs")
    st.markdown(
        "Residents from other programs who fill slots in our rotations. "
        "Rotator-filled slots reduce the demand on our resident pool."
    )

    progs: list[RotatorProgram] = st.session_state.rotator_programs
    prog_df = pd.DataFrame([{
        "Specialty":            p.specialty,
        "Total Rotators":       p.total_rotators,
        "Months/Rotator":       p.months_inpatient,
        "Total Rotator-Weeks":  round(p.total_rotator_weeks(), 1),
        "Eligible Rotations":   ", ".join(p.eligible_rotation_ids),
        "Slot Level":           p.slot_level,
        "Max Simultaneous":     p.max_simultaneous,
        "Blackout Months":      ", ".join(str(m) for m in p.blackout_months),
        "Notes":                p.notes,
    } for p in progs])

    edited_prog = st.data_editor(
        prog_df,
        num_rows="dynamic",
        use_container_width=True,
        column_config={
            "Total Rotators":  st.column_config.NumberColumn(min_value=0, step=1),
            "Months/Rotator":  st.column_config.NumberColumn(min_value=0, step=1),
            "Total Rotator-Weeks": st.column_config.NumberColumn(disabled=True),
            "Slot Level": st.column_config.SelectboxColumn(
                options=["intern", "senior"],
                help="Which resident pool these rotators fill (intern or senior slots)",
            ),
            "Max Simultaneous": st.column_config.NumberColumn(min_value=1, step=1),
        },
        key="prog_editor",
    )

    if st.button("💾 Save Rotator Programs", key="save_prog"):
        new_progs = []
        for _, row in edited_prog.iterrows():
            try:
                rot_ids = [x.strip() for x in str(row["Eligible Rotations"]).split(",") if x.strip()]
                bm_str = str(row["Blackout Months"]).strip()
                blackout_months = [int(x.strip()) for x in bm_str.split(",") if x.strip()] if bm_str else []
                new_progs.append(RotatorProgram(
                    specialty=str(row["Specialty"]),
                    total_rotators=int(row["Total Rotators"]),
                    months_inpatient=int(row["Months/Rotator"]),
                    eligible_rotation_ids=rot_ids,
                    slot_level=str(row.get("Slot Level", "intern")),
                    max_simultaneous=int(row["Max Simultaneous"]),
                    blackout_months=blackout_months,
                    notes=str(row.get("Notes", "")),
                ))
            except Exception as e:
                st.error(f"Row error: {e}")
        if new_progs:
            st.session_state.rotator_programs = new_progs
            st.session_state.feasibility = None
            st.success(f"Saved {len(new_progs)} rotator programs.")

    # Rotator contribution summary
    total_rotator_weeks = sum(p.total_rotator_weeks() for p in progs)
    st.markdown("---")
    st.metric("Total rotator-weeks contributed per year", f"{total_rotator_weeks:.0f} wks")
