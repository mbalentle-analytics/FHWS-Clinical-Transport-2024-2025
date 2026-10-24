"""Writes the overlap table (one row per site and date visited by 2+ departments) for Power BI.
Usage: python export_overlaps.py [departure_window_min] [pickup_window_min] [night_dep night_pickup weekend_dep weekend_pickup]
Defaults 30 and 60. Night-duty and weekend trips use the same windows as day trips unless the last four are given."""
import sys
from pathlib import Path
import pandas as pd
from consolidation import find_overlaps

w_dep = int(sys.argv[1]) if len(sys.argv) > 1 else 30
w_ret = int(sys.argv[2]) if len(sys.argv) > 2 else 60
night = (int(sys.argv[3]), int(sys.argv[4])) if len(sys.argv) > 4 else (w_dep, w_ret)
wkend = (int(sys.argv[5]), int(sys.argv[6])) if len(sys.argv) > 6 else (w_dep, w_ret)
special = {"Night": night, "Weekend": wkend}
src = Path(__file__).parent / "data" / "FHWS_Transport_standardised_AY2024_AY2025.xlsx"
x = pd.ExcelFile(src)
ft = x.parse("Fact_Trips")
f24 = ft[ft["AY"] == "AY2024"]
sites, bs, bd = x.parse("Dim_Site"), x.parse("Bridge_Trip_Site"), x.parse("Bridge_Trip_Date")
v = (bd.merge(bs, on="Trip_ID").merge(f24[["Trip_ID", "Dept_Code", "Cost_Per_Trip", "Sites_In_Route", "Times_As_Captured"]], on="Trip_ID")
       .merge(sites[["Site_ID", "Locality"]], on="Site_ID"))
v = v[(v["Reconciled_To_Units"] == "Yes") & (v["Site_Name"] != "CPT International Airport")]
ov = find_overlaps(v, w_dep, w_ret, special=special).drop(columns=["info"])
ov.insert(0, "Overlap_ID", [f"O-{i + 1:04d}" for i in range(len(ov))])
ov["Departure_Window_Min"], ov["Pickup_Window_Min"] = w_dep, w_ret
ov["Night_Departure_Window_Min"], ov["Night_Pickup_Window_Min"] = night
ov["Weekend_Departure_Window_Min"], ov["Weekend_Pickup_Window_Min"] = wkend
out = Path(__file__).parent / "Fact_Overlap.csv"
ov.to_csv(out, index=False)
print(f"{len(ov)} overlap days, {int(ov['Time_Compatible'].sum())} meet the rules -> {out}")
