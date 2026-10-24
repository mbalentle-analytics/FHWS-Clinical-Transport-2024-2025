"""FHWS student transport dashboard (mockup for a later Power BI build).
Reads the standardised workbook; consolidation rules live in consolidation.py."""
import re
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

from consolidation import find_overlaps, find_structural_overlaps, removable_trips

try:
    BASE = Path(__file__).parent
except NameError:
    BASE = Path.cwd()
DEFAULT_FILE = BASE / "data" / "FHWS_Transport_standardised_AY2024_AY2025.xlsx"

# Streamlit 1.50+ uses width="stretch"; older versions use use_container_width=True
_v = tuple(int(p) for p in re.findall(r"\d+", st.__version__)[:2])
W = {"width": "stretch"} if _v >= (1, 50) else {"use_container_width": True}

st.set_page_config(page_title="FHWS Transport Dashboard", layout="wide")


def nf(x):
    return f"{x:,.0f}"


# ---------------------------------------------------------------- data
@st.cache_data(show_spinner="Loading workbook...")
def load(src):
    x = pd.ExcelFile(src)
    fact = x.parse("Fact_Trips")
    fact = fact[fact["Trip_ID"].astype(str).str.match(r"^[TP]20\d\d-")].copy()
    for c in ("Start_Date", "End_Date"):
        fact[c] = pd.to_datetime(fact[c])
    sites = x.parse("Dim_Site")
    bs = x.parse("Bridge_Trip_Site")[["Trip_ID", "Site_ID", "Site_Name"]]
    bd = x.parse("Bridge_Trip_Date")
    bd["Date"] = pd.to_datetime(bd["Date"])
    return fact, sites, bs, bd, x.parse("Data Issues")


def sensitivity(v, ov, w_dep, unknown_ok, special=None):
    """Qualifying overlap days for a range of pick-up windows (only the overlap days are re-checked, so it is fast)."""
    keys = ov[["Site", "Date"]].rename(columns={"Site": "Site_Name"})
    sub = v.merge(keys, on=["Site_Name", "Date"])
    rows = []
    for wr in (15, 30, 45, 60, 90, 120, 180, 240):
        o = find_overlaps(sub, w_dep, wr, unknown_ok, special)
        rows.append((wr, int(o["Time_Compatible"].sum()) if len(o) else 0))
    return pd.DataFrame(rows, columns=["Pick-up window (min)", "Compatible overlap days"])


def sensitivity25(lines, w_dep, unknown_ok, special=None):
    rows = [(wr, int(find_structural_overlaps(lines, w_dep, wr, unknown_ok, special)["Time_Compatible"].sum())) for wr in (15, 30, 45, 60, 90, 120, 180, 240)]
    return pd.DataFrame(rows, columns=["Pick-up window (min)", "Compatible sites"])


# ---------------------------------------------------------------- sidebar
st.sidebar.title("FHWS transport")
up = st.sidebar.file_uploader("Use a different workbook (same layout)", type="xlsx")
try:
    fact, sites, bs, bd, issues = load(up if up is not None else DEFAULT_FILE)
except Exception as e:
    st.error(f"Could not load the workbook: {e}")
    st.stop()

f24 = fact[fact["AY"] == "AY2024"].copy()
f25 = fact[fact["AY"] == "AY2025"].copy()
cols = ["Trip_ID", "Dept_Code", "Cost_Per_Trip", "Sites_In_Route", "Times_As_Captured"]
v_all = (bd.merge(bs, on="Trip_ID").merge(f24[cols], on="Trip_ID").merge(sites[["Site_ID", "Locality"]], on="Site_ID"))
ay = st.sidebar.radio("Academic year", ["AY2024", "AY2025"], help="AY2024: booked trips with dates and quoted costs. "
                      "AY2025: requirements only (sites, shifts), with assumed cost per trip and no trip dates.")
depts = sorted(fact[fact["AY"] == ay]["Dept_Code"].unique())
sel_depts = st.sidebar.multiselect("Departments", depts, default=depts)
dmin, dmax = v_all["Date"].min().date(), v_all["Date"].max().date()
d0, d1, conf_only, skip_air = dmin, dmax, True, True
if ay == "AY2024":
    rng = st.sidebar.date_input("Date range", (dmin, dmax), min_value=dmin, max_value=dmax)
    d0, d1 = (rng[0], rng[1]) if isinstance(rng, (list, tuple)) and len(rng) == 2 else (dmin, dmax)
    conf_only = st.sidebar.checkbox("Only trips whose dates match units booked", value=True,
                                    help="14 of 92 AY2024 lines have dates that do not reconcile to units; untick to include them.")
    skip_air = st.sidebar.checkbox("Exclude airport trips", value=True)
st.sidebar.markdown("**Consolidation rules**")
w_dep = st.sidebar.slider("Departure from campus/residence: max minutes apart", 15, 180, 30, 15)
w_ret = st.sidebar.slider("Pick-up from hospital site: max minutes apart", 15, 180, 60, 15)
unk_ok = st.sidebar.checkbox("Treat unknown return time as compatible", value=False)
sep = st.sidebar.checkbox("Separate windows for night-duty and weekend trips", value=False,
                          help="Off: night-duty and weekend trips use the windows above (they are still never mixed with day trips).")
special = None
if sep:
    n_dep = st.sidebar.slider("Night duty: departure max minutes apart", 15, 180, 30, 15)
    n_ret = st.sidebar.slider("Night duty: pick-up max minutes apart", 15, 180, 60, 15)
    k_dep = st.sidebar.slider("Weekend: departure max minutes apart", 15, 180, 30, 15)
    k_ret = st.sidebar.slider("Weekend: pick-up max minutes apart", 15, 180, 60, 15)
    special = {"Night": (n_dep, n_ret), "Weekend": (k_dep, k_ret)}
dep_txt = f"Within {w_dep} min (day); night duty {n_dep} min; weekend {k_dep} min" if sep else f"Within {w_dep} min"
ret_txt = f"Within {w_ret} min (day); night duty {n_ret} min; weekend {k_ret} min" if sep else f"Within {w_ret} min"

v = v_all[v_all["Dept_Code"].isin(sel_depts) & (v_all["Date"].dt.date >= d0) & (v_all["Date"].dt.date <= d1)]
if conf_only:
    v = v[v["Reconciled_To_Units"] == "Yes"]
if skip_air:
    v = v[v["Site_Name"] != "CPT International Airport"]
td = v.drop_duplicates(["Trip_ID", "Date"]).assign(Month=lambda d: d["Date"].dt.to_period("M").astype(str))
ov = find_overlaps(v, w_dep, w_ret, unk_ok, special)
comp = ov[ov["Time_Compatible"]] if len(ov) else ov


st.title("FHWS student transport to clinical sites")
st.caption(("AY2024 booked requests (file dated 19 Aug 2024; later months are planned trips). " if ay == "AY2024" else "AY2025 requirements with assumed costs. ")
           + "Costs are as quoted in the TSQT requests unless stated. Mockup for a Power BI build.")
names = ["Overview", "Departments", "Consolidation", "Cost analysis", "Schedule (Gantt)", "Data quality"]
T = dict(zip(names, st.tabs(names)))

if ay == "AY2024":
    # ---------------------------------------------------------------- 1 overview
    with T["Overview"]:
        c = st.columns(5)
        c[0].metric("Trip-days", nf(len(td)))
        c[1].metric("Cost of trip-days", nf(td["Cost_Per_Trip"].sum()))
        c[2].metric("Sites visited", nf(v["Site_Name"].nunique()))
        c[3].metric("Overlap days", nf(len(ov)))
        c[4].metric("Overlaps meeting the rules", nf(len(comp)))
        if td.empty:
            st.info("No trips match the filters.")
        else:
            a, b = st.columns(2)
            m = td.groupby(["Month", "Dept_Code"], as_index=False).agg(Cost=("Cost_Per_Trip", "sum"), Trips=("Trip_ID", "size"))
            a.plotly_chart(px.bar(m, x="Month", y="Cost", color="Dept_Code", title="Cost by month and department"), **W)
            b.plotly_chart(px.bar(m, x="Month", y="Trips", color="Dept_Code", title="Trip-days by month and department"), **W)
            s = v.groupby(["Site_Name", "Dept_Code"], as_index=False).agg(Trip_days=("Date", "nunique"))
            top = s.groupby("Site_Name")["Trip_days"].sum().nlargest(15).index
            st.plotly_chart(px.bar(s[s["Site_Name"].isin(top)], y="Site_Name", x="Trip_days", color="Dept_Code", orientation="h",
                                   title="Busiest sites (days with a trip)").update_yaxes(categoryorder="total ascending"), **W)


    # ---------------------------------------------------------------- 2 departments
    with T["Departments"]:
        if not sel_depts:
            st.info("Select a department in the sidebar.")
        else:
            dsel = st.selectbox("Department", sel_depts)
            vd, tdd, fl = v[v["Dept_Code"] == dsel], td[td["Dept_Code"] == dsel], f24[f24["Dept_Code"] == dsel]
            c = st.columns(4)
            c[0].metric("Request lines", nf(len(fl)))
            c[1].metric("Trip-days", nf(len(tdd)))
            c[2].metric("Cost of trip-days", nf(tdd["Cost_Per_Trip"].sum()))
            c[3].metric("Sites visited", nf(vd["Site_Name"].nunique()))
            a, b = st.columns(2)
            ss = vd.groupby("Site_Name")["Date"].nunique().nlargest(15).reset_index(name="Days")
            a.plotly_chart(px.bar(ss, y="Site_Name", x="Days", orientation="h", title=f"{dsel}: days with a trip by site").update_yaxes(categoryorder="total ascending"), **W)
            mm = tdd.groupby("Month", as_index=False).agg(Trips=("Trip_ID", "size"), Cost=("Cost_Per_Trip", "sum"))
            b.plotly_chart(px.bar(mm, x="Month", y="Trips", title=f"{dsel}: trip-days by month"), **W)
            a2, b2 = st.columns(2)
            sh = fl.groupby("Shift_Type", as_index=False).size().rename(columns={"size": "Lines"})
            a2.plotly_chart(px.pie(sh, names="Shift_Type", values="Lines", title="Trip type (request lines)"), **W)
            vh = fl.assign(Vehicle=fl["Students_As_Captured"].astype("Int64").astype(str) + "-seater").groupby("Vehicle", as_index=False).size().rename(columns={"size": "Lines"})
            b2.plotly_chart(px.bar(vh, x="Vehicle", y="Lines", title="Vehicle size (read from the 'number of students' column)"), **W)
            st.markdown("#### Site by department: days with a trip")
            hm = v.groupby(["Site_Name", "Dept_Code"])["Date"].nunique().unstack(fill_value=0)
            if not hm.empty:
                hm = hm.loc[hm.sum(axis=1).sort_values(ascending=False).head(20).index]
                st.plotly_chart(px.imshow(hm, text_auto=True, aspect="auto", color_continuous_scale="Blues"), **W)
            st.dataframe(fl[["TSQT_Number", "Dates_As_Captured", "Trips", "Cost_Per_Trip", "Total_Cost", "Shift_Type", "Route_As_Captured"]], hide_index=True, **W)


    # ---------------------------------------------------------------- 3 consolidation
    with T["Consolidation"]:
        st.subheader("Consolidation rules applied")
        rules = pd.DataFrame([
            ("1 Same date", "Same calendar date", "Applied"),
            ("2 Departure from campus/residence", dep_txt, "Applied"),
            ("3 Geography", "Same site (5 km rule needs site coordinates)", "Same site only"),
            ("4 Capacity", "Needs student numbers per trip", "Not applied"),
            ("5 Pick-up from hospital site", ret_txt + (" (unknown return counts as compatible)" if unk_ok else " (unknown return does not count)"), "Applied"),
            ("6 Acceptable delay (30 min)", "Approximated by rule 5", "Not separate"),
            ("7 Trip type", "Day, night-duty and weekend trips are not mixed; both trips are on the same date", "Applied"),
            ("8 Accessibility", "No data", "Not applied"),
            ("9 Provider pricing", "Cost per trip as quoted", "Not applied")], columns=["Rule", "Setting", "Status"])
        st.dataframe(rules, hide_index=True, **W)
        if ov.empty:
            st.info("No cross-department overlaps for these filters (select two or more departments).")
        else:
            rem = removable_trips(ov)
            tdays = {(i[0], r.Date): i[1] for r in comp.itertuples() for i in r.info}
            c = st.columns(4)
            c[0].metric("Overlap days", nf(len(ov)))
            c[1].metric("Meeting all applied rules", nf(len(comp)))
            c[2].metric("Cost of trip-days on those days", nf(sum(tdays.values())))
            c[3].metric("Single-site trips that could be absorbed", nf(len(rem)))
            a, b = st.columns(2)
            blk = ov[~ov["Time_Compatible"]]["Blocking_Rule"].value_counts().rename_axis("Blocking rule").reset_index(name="Overlap days")
            a.plotly_chart(px.bar(blk, y="Blocking rule", x="Overlap days", orientation="h", title="Why overlaps do not qualify").update_yaxes(categoryorder="total ascending"), **W)
            sens = sensitivity(v, ov, w_dep, unk_ok, special)
            b.plotly_chart(px.line(sens, x="Pick-up window (min)", y="Compatible overlap days", markers=True,
                                   title=f"Qualifying overlaps if the pick-up window changed (departure window {w_dep} min)"), **W)
            a, b = st.columns(2)
            bysite = ov.groupby(["Site", "Departments"], as_index=False).size().rename(columns={"size": "Overlap_days"})
            top = bysite.groupby("Site")["Overlap_days"].sum().nlargest(12).index
            a.plotly_chart(px.bar(bysite[bysite["Site"].isin(top)], y="Site", x="Overlap_days", color="Departments", orientation="h",
                                  title="Overlap days by site and department pair").update_yaxes(categoryorder="total ascending"), **W)
            hm = ov.assign(Month=ov["Date"].dt.to_period("M").astype(str)).pivot_table(index="Site", columns="Month", values="Trips", aggfunc="size", fill_value=0)
            hm = hm.loc[hm.sum(axis=1).sort_values(ascending=False).head(12).index]
            b.plotly_chart(px.imshow(hm, text_auto=True, aspect="auto", color_continuous_scale="Blues", title="Overlap days: site by month"), **W)

            st.markdown("#### Scenario: how much could be absorbed?")
            st.caption("Assumption-driven: the data has vehicle capacity, not student numbers. The ceiling is the cost of the cheapest "
                       "single-site trip on each overlap day that meets the rules; the sliders scale it down.")
            s1, s2 = st.columns(2)
            share = s1.slider("Share of those trips that could realistically be absorbed", 0, 100, 40, 5, format="%d%%")
            keep = s2.slider("Saving per absorbed trip (as % of its cost)", 0, 100, 70, 5, format="%d%%",
                             help="Allows for a larger vehicle or longer route on the combined trip.")
            ceiling = rem["Cost_Per_Trip"].sum() if not rem.empty else 0
            est = ceiling * share / 100 * keep / 100
            k = st.columns(3)
            k[0].metric("Ceiling (all candidate trips)", nf(ceiling))
            k[1].metric("Scenario saving", nf(est))
            k[2].metric("Share of cost in view", f"{est / td['Cost_Per_Trip'].sum():.1%}" if td["Cost_Per_Trip"].sum() else "n/a")

            st.markdown("#### Overlap days")
            only_comp = st.checkbox("Only days that meet the rules", value=False)
            show = ov.drop(columns=["info"]).sort_values(["Site", "Date"])
            if only_comp:
                show = show[show["Time_Compatible"]]
            st.dataframe(show, hide_index=True, **W)
            st.download_button("Download overlap days (CSV)", show.to_csv(index=False).encode(), "overlap_days.csv", "text/csv")


    # ---------------------------------------------------------------- 4 cost analysis
    with T["Cost analysis"]:
        e = f24[f24["Dept_Code"].isin(sel_depts)].copy()
        e["Cost_per_site_visit"] = e["Cost_Per_Trip"] / e["Sites_In_Route"].replace(0, pd.NA)
        e["Route"] = e["Route_As_Captured"].str.slice(0, 90)
        c = st.columns(4)
        c[0].metric("Cost of trip-days", nf(td["Cost_Per_Trip"].sum()))
        c[1].metric("Average cost per trip-day", nf(td["Cost_Per_Trip"].mean()) if len(td) else "n/a")
        c[2].metric("Average cost per site visit", nf(e["Cost_per_site_visit"].astype(float).mean()) if len(e) else "n/a")
        c[3].metric("Total booked cost (selected departments)", nf(e["Total_Cost"].sum()))
        st.caption("Cost per student is not available: the data holds vehicle capacity, not students per trip.")
        if not td.empty:
            a, b = st.columns(2)
            cm = td.groupby(["Month", "Dept_Code"], as_index=False)["Cost_Per_Trip"].sum()
            a.plotly_chart(px.line(cm, x="Month", y="Cost_Per_Trip", color="Dept_Code", markers=True, title="Monthly cost by department"), **W)
            cd = e.groupby("Dept_Code", as_index=False)["Total_Cost"].sum()
            b.plotly_chart(px.pie(cd, names="Dept_Code", values="Total_Cost", title="Share of booked cost by department"), **W)
            a, b = st.columns(2)
            a.plotly_chart(px.box(e, x="Dept_Code", y="Cost_Per_Trip", points="all", hover_data=["TSQT_Number", "Route"], title="Cost per trip by department"), **W)
            b.plotly_chart(px.scatter(e, x="Sites_In_Route", y="Cost_Per_Trip", color="Dept_Code", size="Total_Cost",
                                      hover_data=["TSQT_Number", "Route", "Trips"], title="Cost per trip against sites on the route"), **W)
            st.markdown("#### Most expensive request lines per site visit")
            st.dataframe(e.sort_values("Cost_per_site_visit", ascending=False).head(15)[["TSQT_Number", "Dept_Code", "Route", "Sites_In_Route", "Trips", "Cost_Per_Trip", "Cost_per_site_visit", "Total_Cost"]], hide_index=True, **W)


    # ---------------------------------------------------------------- 5 Gantt
    with T["Schedule (Gantt)"]:
        mode = st.radio("View", ["Request lines (TSQT)", "Site occupancy by department"], horizontal=True)
        if mode.startswith("Request"):
            g = f24[f24["Dept_Code"].isin(sel_depts) & (f24["End_Date"].dt.date >= d0) & (f24["Start_Date"].dt.date <= d1)].copy()
            if conf_only:
                g = g[~g["Date_Check"].astype(str).str.startswith("MISMATCH")]
            if g.empty:
                st.info("No request lines match the filters.")
            else:
                g["Line"] = g["TSQT_Number"].astype(str).str.replace("TSQT", "") + " | " + g["Route_As_Captured"].str.replace("Bellville Campus to ", "", regex=False).str.slice(0, 55)
                g["Finish"] = g["End_Date"] + pd.Timedelta(days=1)
                fig = px.timeline(g.sort_values("Start_Date"), x_start="Start_Date", x_end="Finish", y="Line", color="Dept_Code",
                                  hover_data=["Dates_As_Captured", "Trips", "Cost_Per_Trip", "Date_Check"])
                fig.update_yaxes(autorange="reversed", title=None)
                fig.update_layout(height=max(450, 20 * len(g)), title="Request lines: first to last trip date (bars span the whole window, not every day)")
                st.plotly_chart(fig, **W)
        else:
            gap = st.slider("Join dates less than this many days apart into one bar", 1, 10, 3)
            if v.empty:
                st.info("No trips match the filters.")
            else:
                top_sites = v.groupby("Site_Name")["Date"].nunique().nlargest(40).index.tolist()
                pick = st.multiselect("Sites", sorted(v["Site_Name"].unique()), default=top_sites[:12])
                vs = v[v["Site_Name"].isin(pick)]
                rows = []
                for (site, dep), gg in vs.groupby(["Site_Name", "Dept_Code"]):
                    ds = sorted(gg["Date"].unique())
                    start = prev = ds[0]
                    for d in ds[1:]:
                        if (d - prev).days > gap:
                            rows.append((site, dep, start, prev))
                            start = d
                        prev = d
                    rows.append((site, dep, start, prev))
                sp = pd.DataFrame(rows, columns=["Site", "Dept", "Start", "End"])
                if sp.empty:
                    st.info("Select at least one site.")
                else:
                    sp["Finish"] = sp["End"] + pd.Timedelta(days=1)
                    fig = px.timeline(sp, x_start="Start", x_end="Finish", y="Site", color="Dept", opacity=0.6)
                    fig.update_yaxes(autorange="reversed", title=None)
                    fig.update_layout(height=max(400, 45 * sp["Site"].nunique()), title="Periods when each department travels to a site (overlapping bars = overlap)")
                    st.plotly_chart(fig, **W)


else:
    # ------------------------------------------------------------ AY2025 (requirements, assumed costs, no dates)
    l25 = (bs.merge(f25[["Trip_ID", "Dept_Code", "Cost_Per_Trip", "Cost_Basis", "Times_As_Captured", "Dates_As_Captured", "Students_As_Captured", "Shift_Type", "Vehicle_ID"]], on="Trip_ID")
             .merge(sites[["Site_ID", "Locality"]], on="Site_ID"))
    l25 = l25[l25["Dept_Code"].isin(sel_depts)]
    so = find_structural_overlaps(l25, w_dep, w_ret, unk_ok, special)
    so_ok = so[so["Time_Compatible"]] if len(so) else so
    st.info("AY2025 has requirements only: no trip dates, units or quotes. Cost per trip is an assumption (the same department's AY2024 cost "
            "for the site, or the assumed new-route cost), so there is no AY2025 total. Same date is replaced by sharing a day of the week.")

    with T["Overview"]:
        c = st.columns(5)
        c[0].metric("Requirement lines", nf(len(l25)))
        c[1].metric("Sites", nf(l25["Site_Name"].nunique()))
        c[2].metric("Sites needed by 2+ departments", nf(len(so)))
        c[3].metric("Meeting the rules", nf(len(so_ok)))
        c[4].metric("Lines at the assumed new-route cost", nf(int(l25["Cost_Basis"].astype(str).str.startswith("Assumed").sum())))
        if l25.empty:
            st.info("No requirement lines match the filters.")
        else:
            a, b = st.columns(2)
            ld = l25.groupby("Dept_Code", as_index=False).agg(Lines=("Trip_ID", "count"), Sites=("Site_Name", "nunique"))
            a.plotly_chart(px.bar(ld, x="Dept_Code", y="Sites", color="Dept_Code", title="Sites required by department"), **W)
            cb = l25.groupby(["Dept_Code", "Cost_Basis"], as_index=False).size().rename(columns={"size": "Lines"})
            b.plotly_chart(px.bar(cb, x="Dept_Code", y="Lines", color="Cost_Basis", title="How each line's cost was set"), **W)
            nd = l25.groupby("Site_Name")["Dept_Code"].nunique().reset_index(name="Departments")
            st.plotly_chart(px.histogram(nd, x="Departments", title="Sites by number of departments needing them"), **W)

    with T["Departments"]:
        if not sel_depts:
            st.info("Select a department in the sidebar.")
        else:
            dsel = st.selectbox("Department", sel_depts)
            ld = l25[l25["Dept_Code"] == dsel]
            c = st.columns(3)
            c[0].metric("Sites", nf(ld["Site_Name"].nunique()))
            c[1].metric("Average assumed cost per trip", nf(ld["Cost_Per_Trip"].mean()) if len(ld) else "n/a")
            c[2].metric("Cost of one trip to every site", nf(ld["Cost_Per_Trip"].sum()))
            a, b = st.columns(2)
            sh = ld.groupby("Shift_Type", as_index=False).size().rename(columns={"size": "Lines"})
            a.plotly_chart(px.pie(sh, names="Shift_Type", values="Lines", title="Trip type"), **W)
            top = ld.nlargest(15, "Cost_Per_Trip")
            b.plotly_chart(px.bar(top, y="Site_Name", x="Cost_Per_Trip", orientation="h", title="Highest assumed cost per trip").update_yaxes(categoryorder="total ascending"), **W)
            st.dataframe(ld.assign(Students_As_Captured=ld["Students_As_Captured"].astype(str))[["Site_Name", "Locality", "Students_As_Captured", "Times_As_Captured", "Cost_Per_Trip", "Cost_Basis"]], hide_index=True, **W)

    with T["Consolidation"]:
        st.subheader("Consolidation rules applied")
        rules = pd.DataFrame([
            ("1 Same date", "Not testable (no dates): departments must share a day of the week; night duty assumed every day", "Approximated"),
            ("2 Departure from campus/residence", dep_txt, "Applied"),
            ("3 Geography", "Same site (5 km rule needs site coordinates)", "Same site only"),
            ("4 Capacity", "Needs student numbers per trip", "Not applied"),
            ("5 Pick-up from hospital site", ret_txt, "Applied"),
            ("7 Trip type", "Day, night-duty and weekend trips are not mixed", "Applied")], columns=["Rule", "Setting", "Status"])
        st.dataframe(rules, hide_index=True, **W)
        if so.empty:
            st.info("No site is needed by two or more of the selected departments.")
        else:
            c = st.columns(3)
            c[0].metric("Sites needed by 2+ departments", nf(len(so)))
            c[1].metric("Meeting the rules", nf(len(so_ok)))
            c[2].metric("Assumed cost of one trip each (qualifying sites)", nf(so_ok["Assumed_Cost_Of_One_Trip_Each"].sum()) if len(so_ok) else "0")
            a, b = st.columns(2)
            blk = so[~so["Time_Compatible"]]["Blocking_Rule"].value_counts().rename_axis("Blocking rule").reset_index(name="Sites")
            if not blk.empty:
                a.plotly_chart(px.bar(blk, y="Blocking rule", x="Sites", orientation="h", title="Why sites do not qualify"), **W)
            sens = sensitivity25(l25, w_dep, unk_ok, special)
            b.plotly_chart(px.line(sens, x="Pick-up window (min)", y="Compatible sites", markers=True, title=f"Qualifying sites if the pick-up window changed (departure window {w_dep} min)"), **W)
            st.dataframe(so.sort_values(["Time_Compatible", "Site"], ascending=[False, True]), hide_index=True, **W)
            st.download_button("Download AY2025 shared sites (CSV)", so.to_csv(index=False).encode(), "ay2025_shared_sites.csv", "text/csv")

    with T["Cost analysis"]:
        if l25.empty:
            st.info("No requirement lines match the filters.")
        else:
            c = st.columns(3)
            c[0].metric("Average assumed cost per trip", nf(l25["Cost_Per_Trip"].mean()))
            c[1].metric("Cost of one trip to every site", nf(l25["Cost_Per_Trip"].sum()))
            c[2].metric("Assumed new-route cost lines", nf(int(l25["Cost_Basis"].astype(str).str.startswith("Assumed").sum())))
            st.caption("A size check, not a budget: AY2025 has no trip counts, so no AY2025 total is calculated.")
            a, b = st.columns(2)
            a.plotly_chart(px.box(l25, x="Dept_Code", y="Cost_Per_Trip", color="Cost_Basis", points="all", hover_data=["Site_Name"], title="Assumed cost per trip by department"), **W)
            cs = l25.groupby(["Dept_Code", "Cost_Basis"], as_index=False)["Cost_Per_Trip"].sum()
            b.plotly_chart(px.bar(cs, x="Dept_Code", y="Cost_Per_Trip", color="Cost_Basis", title="Cost of one trip to every site, by basis"), **W)

    with T["Schedule (Gantt)"]:
        st.info("AY2025 has no trip dates, so there is nothing to draw on a timeline yet. Switch to AY2024 for the Gantt views.")

# ---------------------------------------------------------------- data quality
with T["Data quality"]:
    mm = f24["Date_Check"].astype(str).str.startswith("MISMATCH").sum()
    c = st.columns(3)
    c[0].metric("AY2024 lines with unreconciled dates", int(mm))
    c[1].metric("Lines with TSQT filled down", int((f24["TSQT_Source"] != "Stated").sum()))
    c[2].metric("Issues logged", len(issues))
    sev = st.multiselect("Severity", sorted(issues["Severity"].unique()), default=sorted(issues["Severity"].unique()))
    st.dataframe(issues[issues["Severity"].isin(sev)], hide_index=True, **W)
