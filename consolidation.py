"""Consolidation rules for FHWS transport (shared by the dashboard and the export script).

Rules applied to two trips (from different departments) that go to the same site on the same date:
  Rule 2  departure from campus/residence within `w_dep` minutes
  Rule 5  pick-up from the hospital site (return leg) within `w_ret` minutes
  Rule 7  day, night-duty and weekend trips are not mixed (a day leg is never combined with a night leg or a
          weekend leg). Night-duty and weekend trips can have their own departure and pick-up windows (`special`);
          if none are given they use the same windows as day trips. A trip that qualifies as night duty
          (departs 17h00 or later, or returns before it departs) counts as night duty even on a weekend.
Not applied (no data): capacity, 5 km distance, accessibility, provider pricing.
"""
import re
import pandas as pd

NIGHT_FROM = 17 * 60


def _legs_one(t):
    pat = r"(\d{1,2})h(\d{2})"
    head, tail = (t.split("return", 1) + [""])[:2] if "return" in t else (t, "")
    deps = [int(h) * 60 + int(m) for h, m in re.findall(pat, head)]
    rets = [int(h) * 60 + int(m) for h, m in re.findall(pat, tail)]
    if not deps:
        return []
    if not rets:
        return [(d, None) for d in deps]
    if len(deps) == len(rets):
        return list(zip(deps, rets))
    return [(d, r) for d in deps for r in rets]


def parse_legs(txt):
    """[(departure_min, return_min or None)] from text like '06h00 & 18h00 return 18h00 & 06h00'.
    Text with several shifts ('Shift 1: ...; Shift 2: ...') is read shift by shift."""
    t = str(txt).lower().replace("\n", " ")
    segs = [x for x in re.split(r";|shift\s*\d\s*:", t) if x.strip()]
    out = []
    for seg in segs:
        out += _legs_one(seg)
    return out


def leg_type(dep, ret, weekend=False):
    """'Night' (departs 17h00 or later, or returns before it departs), else 'Weekend' if the day is a Saturday
    or Sunday, else 'Day'."""
    if dep >= NIGHT_FROM or (ret is not None and ret < dep):
        return "Night"
    return "Weekend" if weekend else "Day"


def _windows(trip_type, w_dep, w_ret, special):
    """(departure window, pick-up window) for a trip type; `special` = {"Night": (dep, ret), "Weekend": (dep, ret)}."""
    return (special or {}).get(trip_type, (w_dep, w_ret))


def compare_detail(legs_a, legs_b, w_dep, w_ret, unknown_ok=False, weekend=False, special=None):
    """(compatible, blocking_reason, matched_trip_types) for two trips' legs. Legs are only compared with legs of
    the same type (Day, Night, Weekend), each type using its own windows."""
    if not legs_a or not legs_b:
        return False, "Departure time unknown", set()
    same_type = [(a, b) for a in legs_a for b in legs_b if leg_type(*a, weekend) == leg_type(*b, weekend)]
    if not same_type:
        return False, "Day vs night trips", set()
    dep_ok = ret_ok = False
    matched = set()
    first_w = None
    for a, b in same_type:
        t = leg_type(*a, weekend)
        wd, wr = _windows(t, w_dep, w_ret, special)
        first_w = first_w or (wd, wr)
        d_ok = abs(a[0] - b[0]) <= wd
        if a[1] is None or b[1] is None:
            r_ok = unknown_ok
        else:
            r_ok = abs(a[1] - b[1]) <= wr
        if d_ok and r_ok:
            matched.add(t)
        dep_ok |= d_ok
        ret_ok |= r_ok
    if matched:
        return True, "", matched
    wd, wr = first_w
    if dep_ok and not ret_ok:
        return False, f"Pick-up from site more than {wr} min apart (or return unknown)", set()
    if ret_ok and not dep_ok:
        return False, f"Departure more than {wd} min apart", set()
    return False, "Departure and pick-up times too far apart", set()


def compare(legs_a, legs_b, w_dep, w_ret, unknown_ok=False, weekend=False, special=None):
    """(compatible, blocking_reason) for two trips' legs."""
    ok, why, _ = compare_detail(legs_a, legs_b, w_dep, w_ret, unknown_ok, weekend, special)
    return ok, why


def find_overlaps(v, w_dep=30, w_ret=60, unknown_ok=False, special=None):
    """One row per site and date visited by two or more departments. `special` optionally gives night-duty and
    weekend trips their own windows: {"Night": (dep, ret), "Weekend": (dep, ret)}."""
    cols = ["Site", "Locality", "Date", "Weekday", "Trip_Type", "Departments", "Dept_Count", "Trips", "Time_Compatible",
            "Blocking_Rule", "Trip_IDs", "Cost_Of_Trips", "Departures", "info"]
    out = []
    for (site, date), g in v.groupby(["Site_Name", "Date"], sort=False):
        if g["Dept_Code"].nunique() < 2:
            continue
        trips = g.drop_duplicates("Trip_ID")
        depts = sorted(trips["Dept_Code"].unique())
        legs = {d: [l for t in trips.loc[trips["Dept_Code"] == d, "Times_As_Captured"] for l in parse_legs(t)] for d in depts}
        ok, why = False, ""
        reasons, matched = [], set()
        weekend = date.weekday() >= 5
        for i, d1 in enumerate(depts):
            for d2 in depts[i + 1:]:
                c, r, m = compare_detail(legs[d1], legs[d2], w_dep, w_ret, unknown_ok, weekend, special)
                ok |= c
                matched |= m
                if r:
                    reasons.append(r)
        present = matched or {leg_type(*l, weekend) for d in depts for l in legs[d]}
        if not ok:
            why = reasons[0] if len(set(reasons)) == 1 else "; ".join(sorted(set(reasons))[:2])
        info = [(r.Trip_ID, float(r.Cost_Per_Trip), int(r.Sites_In_Route), r.Dept_Code) for r in trips.itertuples()]
        out.append(dict(
            Site=site, Locality=g["Locality"].iloc[0], Date=date, Weekday=date.strftime("%a"), Trip_Type=" + ".join(sorted(present)),
            Departments=" + ".join(depts), Dept_Count=len(depts), Trips=len(trips), Time_Compatible=ok,
            Blocking_Rule=why, Trip_IDs=", ".join(trips["Trip_ID"]), Cost_Of_Trips=sum(i[1] for i in info),
            Departures=" | ".join(f"{d}: " + ", ".join(sorted({" ".join(str(t).split()) for t in trips.loc[trips['Dept_Code'] == d, 'Times_As_Captured']})) for d in depts),
            info=info))
    return pd.DataFrame(out, columns=cols)


def removable_trips(ov):
    """One single-site trip per time-compatible overlap day (the cheapest) as the most that could be absorbed."""
    rem = {}
    for r in ov[ov["Time_Compatible"]].itertuples():
        cands = [i for i in r.info if i[2] == 1]
        if cands:
            t = min(cands, key=lambda i: i[1])
            rem[(t[0], r.Date)] = (t[1], t[3], r.Site)
    return pd.DataFrame([(k[0], k[1], v[1], v[2], v[0]) for k, v in rem.items()],
                        columns=["Trip_ID", "Date", "Dept", "Site", "Cost_Per_Trip"])


def day_set(txt):
    """Days of the week (0=Mon) a requirement applies to, from the AY2025 date text. Night duty is assumed to run every day."""
    t = str(txt).lower()
    if "mondays to fridays" in t:
        return set(range(5))
    if "weekend" in t:
        return {5, 6}
    return set(range(7))


def find_structural_overlaps(lines, w_dep=30, w_ret=60, unknown_ok=False, special=None):
    """AY2025: sites needed by two or more departments. There are no dates, so the same-date rule becomes
    'share at least one day of the week'. Columns needed: Trip_ID, Site_Name, Dept_Code, Cost_Per_Trip,
    Times_As_Captured, Dates_As_Captured."""
    cols = ["Site", "Departments", "Dept_Count", "Lines", "Trip_Type", "Time_Compatible", "Blocking_Rule", "Departures",
            "Assumed_Cost_Of_One_Trip_Each", "Cheapest_Trip_Cost"]
    out = []
    for site, g in lines.groupby("Site_Name"):
        depts = sorted(g["Dept_Code"].unique())
        if len(depts) < 2:
            continue
        recs = [(r.Dept_Code, parse_legs(r.Times_As_Captured), day_set(r.Dates_As_Captured)) for r in g.itertuples()]
        ok, reasons, common, matched, seen = False, [], False, set(), set()
        for i, a in enumerate(recs):
            for b in recs[i + 1:]:
                if a[0] == b[0]:
                    continue
                if not (a[2] & b[2]):
                    continue
                common = True
                weekend = (a[2] & b[2]) <= {5, 6}
                c, r, m = compare_detail(a[1], b[1], w_dep, w_ret, unknown_ok, weekend, special)
                ok |= c
                matched |= m
                seen |= {leg_type(*l, weekend) for l in a[1] + b[1]}
                if r:
                    reasons.append(r)
        why = "" if ok else ("No common days of the week" if not common else sorted(set(reasons))[0])
        costs = g["Cost_Per_Trip"].astype(float).tolist()
        out.append(dict(Site=site, Departments=" + ".join(depts), Dept_Count=len(depts), Lines=", ".join(g["Trip_ID"]),
                        Trip_Type=" + ".join(sorted(matched or seen)), Time_Compatible=ok, Blocking_Rule=why,
                        Departures=" | ".join(f"{d}: " + ", ".join(sorted({" ".join(str(t).split()) for t in g.loc[g['Dept_Code'] == d, 'Times_As_Captured']})) for d in depts),
                        Assumed_Cost_Of_One_Trip_Each=sum(costs), Cheapest_Trip_Cost=min(costs)))
    return pd.DataFrame(out, columns=cols)
