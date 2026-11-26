"""Peak-event intraday performance pipeline.

Python (pandas) implementation of the calculation chain used by the dashboard in index.html:

    snapshots -> hourly -> brand_hour -> brand_status -> cluster_kpi -> action_points

The business rules are documented in docs/methodology.md. Run this file to export the results:

    python python/pipeline.py
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUT_DIR = Path(__file__).resolve().parent / "output"

LOW = 0.8          # a metric is flagged when it is at least 20% below its benchmark
MIN_VIEWS = 50     # minimum views before a rate is judged
ADDITIVE = ["gmv", "orders", "items", "buyers", "views", "comments", "likes", "shares", "ads"]
ADDITIVE_BH = ADDITIVE + ["views_o", "orders_o", "gmv_o"]
KEEP_TEXT = {
    "LivestreamID", "Slot", "Source File", "Campaign", "Campaign Date", "Captured At",
    "Livestream Start", "Livestream End", "Livestream Duration", "View Duration",
    "Avg Viewing Duration", "Avg Viewing Duration per View",
}
TABLES = {
    "chA": "Channel_A", "chB": "Channel_B", "targets": "Master_Targets",
    "curve": "Pacing_Curve", "benchmarks": "Brand_Benchmarks",
}


# ----------------------------------------------------------------------------- helpers
def _ok(v) -> bool:
    return v is not None and not (isinstance(v, float) and math.isnan(v))


def _f(v):
    """NaN -> None so that missing values behave like blanks."""
    return v if _ok(v) else None


def slot_label(h: int) -> str:
    return "23.59" if h == 24 else f"{h:02d}.00"


def eur(v) -> str:
    if not _ok(v) or not math.isfinite(v):
        return "—"
    a = abs(v)
    if a >= 1e6:
        return f"€{v / 1e6:.2f}M"
    if a >= 1e3:
        return f"€{v / 1e3:.1f}K"
    return f"€{math.floor(v + 0.5)}"


def pct(v) -> str:
    if not _ok(v) or not math.isfinite(v):
        return "—"
    return f"{v * 100:.{2 if v < 0.1 else 1}f}%"


def _to_value(col: str, cell):
    if cell is None:
        return None
    t = str(cell).strip()
    if t == "":
        return None
    if col in KEEP_TEXT:
        return t
    if re.fullmatch(r"-?[\d,]+(\.\d+)?", t):
        return float(t.replace(",", ""))
    if re.fullmatch(r"-?[\d.]+%", t):
        return float(t[:-1]) / 100
    return t


# ----------------------------------------------------------------------------- 1. load
def read_table(name: str, data_dir: Path = DATA_DIR) -> pd.DataFrame:
    """Read one CSV. Thousands separators and % become numbers; IDs, slots and times stay text."""
    raw = pd.read_csv(data_dir / f"{name}.csv", dtype=str, keep_default_na=False)
    raw.columns = [c.strip() for c in raw.columns]
    out = {}
    for col in raw.columns:
        vals = [_to_value(col, x) for x in raw[col]]
        if all(v is None or isinstance(v, float) for v in vals):
            out[col] = pd.Series([np.nan if v is None else v for v in vals], dtype="float64")
        else:
            out[col] = pd.Series(vals, dtype="object")
    return pd.DataFrame(out)


def load_tables(data_dir: Path = DATA_DIR) -> dict[str, pd.DataFrame]:
    return {key: read_table(name, data_dir) for key, name in TABLES.items()}


# ----------------------------------------------------------------------------- 2. snapshots
def _norm_ts(v) -> str:
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?", str(v or "").strip())
    if not m:
        return ""
    p2 = lambda x: str(x or 0).zfill(2)  # noqa: E731
    return f"{m[1]}-{p2(m[2])}-{p2(m[3])} {p2(m[4])}:{p2(m[5])}:{p2(m[6])}"


def _slot_hour(s):
    if not _ok(s) or s == "":
        return None
    t = str(s)
    if t == "23.59":
        return 24
    m = re.match(r"\d+", t)
    return int(m.group()) if m else None


def build_snapshots(tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Every checkpoint of both channels in one shape, with campaign, normalised time and hour slot.

    Midnight rule: a 00.00 checkpoint (or one captured just after midnight) of a session that started
    the previous day closes that day as hour 24 (23.59).
    """
    campaign_of = {str(r["Campaign Date"]): str(r["Campaign"]) for r in tables["targets"].to_dict("records")}
    out = []

    def add(platform: str, r: dict, m: dict):
        if not _ok(r.get("Brand")) or not _ok(r.get("Captured At")) or not _ok(r.get("GMV")):
            return
        at = _norm_ts(r["Captured At"])
        if not at:
            return
        day = at[:10]
        hour = _slot_hour(r.get("Slot"))
        start = _norm_ts(r.get("Livestream Start"))[:10]
        cap_hour = int(at[11:13])
        if start and start < day and (hour == 0 or (hour is None and cap_hour == 0)):
            day = start
            hour = 24 if hour == 0 else None
        if hour == 0:
            hour = None
        campaign = campaign_of.get(day)
        if not campaign:
            return
        out.append({"campaign": campaign, "brand": r["Brand"], "platform": platform,
                    "session": str(r["LivestreamID"]), "at": at, "hour": hour,
                    "flags": str(r.get("Flags") or "") if _ok(r.get("Flags")) else "", **m})

    for r in tables["chA"].to_dict("records"):
        add("Channel A", r, dict(gmv=_f(r["GMV"]), orders=_f(r.get("Orders")), buyers=_f(r.get("Buyer")),
                                 items=_f(r.get("Quantity Sold")), views=_f(r.get("Views")),
                                 comments=_f(r.get("Comment")), likes=_f(r.get("Likes")),
                                 shares=_f(r.get("Shares")), ads=None, roi=None, boost=None))
    for r in tables["chB"].to_dict("records"):
        add("Channel B", r, dict(gmv=_f(r["GMV"]), orders=_f(r.get("Attributed Orders")), buyers=None,
                                 items=_f(r.get("Attributed Items Sold")), views=_f(r.get("Views")),
                                 comments=_f(r.get("Comments")), likes=None, shares=None,
                                 ads=_f(r.get("Ads Cost")), roi=_f(r.get("Paid Boost ROI")),
                                 boost="Paid boost active" in str(r.get("GMV Type") or "")))
    return pd.DataFrame(out)


# ----------------------------------------------------------------------------- 3. hourly
def build_hourly(snaps: pd.DataFrame) -> pd.DataFrame:
    """Cumulative metrics per campaign, brand, channel and hour.

    At each hour: the latest checkpoint of every livestream session so far that day, added together.
    ROI and paid-boost status are the latest reported. Conversion uses only sessions that report both
    orders and views (columns *_o), so sessions without attributed orders do not dilute it.
    """
    rows = []
    recs = snaps.to_dict("records")
    groups: dict[tuple, list] = {}
    for s in recs:
        groups.setdefault((s["campaign"], s["brand"], s["platform"]), []).append(s)
    for (campaign, brand, platform), lst in groups.items():
        lst = sorted(lst, key=lambda s: s["at"])  # stable, like the dashboard
        latest: dict[str, dict] = {}
        by_hour: dict[int, dict] = {}
        for s in lst:
            latest[s["session"]] = s
            if not _ok(s["hour"]):
                continue
            row = {"campaign": campaign, "brand": brand, "platform": platform, "hour": int(s["hour"]), "at": s["at"]}
            for m in ADDITIVE:
                vals = [_f(v[m]) for v in latest.values() if _f(v[m]) is not None]
                row[m] = float(sum(vals)) if vals else None
            ov = oo = og = 0.0
            for v in latest.values():
                if _f(v["orders"]) is not None and _f(v["views"]) is not None:
                    ov += v["views"]; oo += v["orders"]; og += v["gmv"]
            row.update(views_o=ov, orders_o=oo, gmv_o=og, roi=_f(s["roi"]), boost=s["boost"],
                       ended="livestream ended" in s["flags"])
            by_hour[int(s["hour"])] = row
        rows.extend(by_hour.values())
    return pd.DataFrame(rows)


# ----------------------------------------------------------------------------- 4. brand-hour
def build_brand_hour(hourly: pd.DataFrame, targets: pd.DataFrame, curve: pd.DataFrame,
                     campaign: str = "9.9", platform: str = "All") -> list[dict]:
    """Per brand and hour for one campaign: totals, pace, forecasts, variance, rates and per-hour changes."""
    share = {0: 0.0}
    for c in curve.to_dict("records"):
        h = 24 if str(c["Slot"]) == "23.59" else int(re.match(r"\d+", str(c["Slot"])).group())
        share[h] = float(c["Expected Cumulative Share"])
    tg = targets[(targets["Campaign"].astype(str) == campaign) & ((platform == "All") | (targets["Platform"] == platform))]
    rows = hourly[(hourly["campaign"] == campaign) & ((platform == "All") | (hourly["platform"] == platform))]
    latest = int(rows["hour"].max()) if len(rows) else 0
    rows_l = rows.to_dict("records")
    out: list[dict] = []
    brands = list(dict.fromkeys(tg["Brand"]))
    for brand in brands:
        mine = tg[tg["Brand"] == brand]
        target = float(mine["Target GMV (EUR)"].sum())
        plats = list(dict.fromkeys(mine["Platform"]))
        series = []
        for pl in plats:
            rs = [r for r in rows_l if r["brand"] == brand and r["platform"] == pl]
            by = {r["hour"]: r for r in rs}
            s, last = {}, None
            for h in range(1, latest + 1):
                r = by.get(h)
                if r:
                    last = r
                    s[h] = (r, "data")
                else:
                    later = any(x["hour"] > h for x in rs)
                    if not last and not later:
                        st = "none"
                    elif last and last["ended"]:
                        st = "not live"
                    elif later:
                        st = "missing"
                    else:
                        st = "not live"
                    s[h] = (last, st)
            series.append((pl, s))
        hist: dict[int, dict] = {}
        for h in range(1, latest + 1):
            o = {"campaign": campaign, "brand": brand, "platforms": " + ".join(plats), "hour": h,
                 "slot": slot_label(h), "target": target}
            for m in ADDITIVE_BH:
                t = None
                for _, s in series:
                    r = s[h][0]
                    if r and _f(r.get(m)) is not None:
                        t = (t or 0) + r[m]
                o[m] = t
            o["gmv"] = o["gmv"] or 0.0
            o["missing"] = ", ".join(pl for pl, s in series if s[h][1] == "missing")
            o["notlive"] = ", ".join(pl for pl, s in series if s[h][1] == "not live")
            o["fresh"] = any(s[h][1] == "data" for _, s in series)
            tr = next((s[h][0] for pl, s in series if pl == "Channel B"), None)
            o["roi"] = _f(tr["roi"]) if tr else None
            o["boost_off"] = bool(tr and tr["boost"] is False)
            sh = share.get(h)
            o["achieved"] = o["gmv"] / target if target else None
            o["expected_share"] = sh
            o["expected"] = target * sh if sh else None
            o["pace"] = o["gmv"] / o["expected"] if o["expected"] else None
            o["rr_linear"] = o["gmv"] / h * 24
            o["rr_curve"] = o["gmv"] / sh if sh else None
            o["gap"] = max(0.0, target - o["gmv"])
            o["hours_left"] = 24 - h
            o["need_per_hour"] = o["gap"] / o["hours_left"] if o["hours_left"] > 0 else None
            prev = hist.get(h - 1)

            def d(a, b):
                return a - b if a is not None and b is not None and a - b >= 0 else None

            o["hour_gmv"] = d(o["gmv"], prev["gmv"]) if prev else o["gmv"]
            o["hour_orders"] = d(o["orders_o"], prev["orders_o"]) if prev else o["orders_o"]
            o["hour_views"] = d(o["views"], prev["views"]) if prev else o["views"]
            o["hour_views_o"] = d(o["views_o"], prev["views_o"]) if prev else o["views_o"]
            o["hour_comments"] = d(o["comments"], prev["comments"]) if prev else o["comments"]
            back = hist.get(max(0, h - 3))
            o["rate3"] = (o["gmv"] - back["gmv"]) / 3 if h > 3 and back else o["gmv"] / h
            o["cr"] = o["orders_o"] / o["views_o"] if o["views_o"] else None
            o["cr_hour"] = o["hour_orders"] / o["hour_views_o"] if o["hour_views_o"] and o["hour_orders"] is not None else None
            o["aov"] = o["gmv_o"] / o["orders_o"] if o["orders_o"] else None
            if prev and o["hour_orders"]:
                dg = d(o["gmv_o"], prev["gmv_o"])
                o["aov_hour"] = (dg if dg is not None else 0) / o["hour_orders"]
            else:
                o["aov_hour"] = None
            o["cpk"] = (o["comments"] or 0) / o["views"] * 1000 if o["views"] else None
            o["status"] = ("No data" if o["pace"] is None else "On track" if o["pace"] >= 1
                           else "At risk" if o["pace"] >= 0.9 else "Behind")
            hist[h] = o
            out.append(o)
    return out


# ----------------------------------------------------------------------------- 5. brand status
def build_brand_status(bh: list[dict], benchmarks: pd.DataFrame, asof: int = 0, platform: str = "All") -> list[dict]:
    """Each brand at the selected hour, plus its conversion on the most recent other peak event."""
    latest = max((r["hour"] for r in bh), default=0)
    H = min(asof, latest) if asof > 0 else latest
    bm = benchmarks.to_dict("records")
    res = []
    for r in (x for x in bh if x["hour"] == H):
        prior = [b for b in bm if b["Brand"] == r["brand"] and str(b["Campaign"]) != r["campaign"]
                 and (platform == "All" or b["Platform"] == platform) and _f(b["Conversion (Orders / Views)"]) is not None]
        last_date = max((str(b["Campaign Date"]) for b in prior), default=None)
        recent = [b for b in prior if str(b["Campaign Date"]) == last_date]
        views = sum((_f(b["Views"]) or 0) for b in recent)
        prev_cr = sum(b["Conversion (Orders / Views)"] * (_f(b["Views"]) or 0) for b in recent) / views if views else None
        last_data = max([x["hour"] for x in bh if x["brand"] == r["brand"] and x["hour"] <= H and x["fresh"]], default=0)
        res.append({**r, "prev_cr": prev_cr, "hour_last": last_data, "last_slot": slot_label(last_data) if last_data else "—"})
    return res


# ----------------------------------------------------------------------------- 6. portfolio KPIs
def build_cluster_kpi(bs: list[dict]) -> dict | None:
    """All brands added together at the selected hour."""
    if not bs:
        return None
    s = lambda k: sum((b[k] or 0) for b in bs)  # noqa: E731
    gmv, target, expected, H = s("gmv"), s("target"), s("expected"), bs[0]["hour"]
    pace = gmv / expected if expected else None
    return {"hour": H, "gmv": gmv, "target": target, "achieved": gmv / target if target else None,
            "expected_share": bs[0]["expected_share"], "pace": pace, "gap": max(0.0, target - gmv),
            "need_per_hour": max(0.0, target - gmv) / (24 - H) if H < 24 else None,
            "rate3": s("rate3"), "rr_curve": s("rr_curve"), "rr_linear": s("rr_linear"),
            "orders": s("orders_o"), "cr": s("orders_o") / s("views_o") if s("views_o") else None,
            "status": "No data" if pace is None else "On track" if pace >= 1 else "At risk" if pace >= 0.9 else "Behind"}


# ----------------------------------------------------------------------------- 7. interventions
def build_action_points(bh: list[dict]) -> list[dict]:
    """Rule-based interventions for every hour (thresholds: LOW = 0.8, MIN_VIEWS = 50)."""
    out: list[dict] = []
    by_hour: dict[int, list[dict]] = {}
    for r in bh:
        by_hour.setdefault(r["hour"], []).append(r)
    for h, rows in by_hour.items():
        prev_rows = {r["brand"]: r for r in by_hour.get(h - 1, [])}

        def avg(k):
            v = [r[k] for r in rows if r[k] is not None and math.isfinite(r[k])]
            return sum(v) / len(v) if v else None

        all_cr, all_cpk = avg("cr"), avg("cpk")

        def push(kind, rank, brand, text):
            out.append({"hour": h, "slot": slot_label(h), "kind": kind, "rank": rank, "brand": brand, "text": text})

        for r in sorted([r for r in rows if r["status"] in ("Behind", "At risk")], key=lambda r: -r["gap"]):
            head = f"pace {pct(r['pace'])} · {pct(r['achieved'])} of target" if h < 24 else f"{pct(r['achieved'])} of target"
            tail = (f"variance {eur(r['gap'])} · required {eur(r['need_per_hour'])}/h vs {eur(r['rate3'])}/h current"
                    if r["need_per_hour"] is not None else f"closed {eur(r['gap'])} short")
            push(r["status"], 1 if r["status"] == "Behind" else 2, r["brand"], f"▼ {head} · {tail}")
        for r in rows:
            pr = prev_rows.get(r["brand"])
            if (h >= 2 and (r["hour_views_o"] or 0) >= MIN_VIEWS and r["cr_hour"] is not None and pr and pr["cr"]
                    and r["cr_hour"] < pr["cr"] * LOW):
                push("Funnel flag", 3, r["brand"], f"conversion ▼ {pct(r['cr_hour'])} last hour (day {pct(pr['cr'])})")
            if r["cr"] is not None and all_cr and r["cr"] < all_cr * LOW and (r["views_o"] or 0) >= MIN_VIEWS:
                push("Funnel flag", 3, r["brand"], f"conversion ▼ {pct(r['cr'])} (all brands {pct(all_cr)})")
            if (h >= 3 and pr and r["hour_views"] is not None and pr["views"]
                    and r["hour_views"] < (pr["views"] / (h - 1)) * LOW and r["fresh"] and not r["notlive"]):
                push("Funnel flag", 3, r["brand"],
                     f"views ▼ {math.floor(r['hour_views'] + 0.5):,}/h (avg {math.floor(pr['views'] / (h - 1) + 0.5):,}/h)")
            if (h >= 2 and pr and pr["aov"] and r["aov_hour"] is not None and (r["hour_orders"] or 0) >= 5
                    and r["aov_hour"] < pr["aov"] * LOW):
                push("Funnel flag", 3, r["brand"], f"AOV ▼ {eur(r['aov_hour'])} last hour (day {eur(pr['aov'])})")
            if r["cpk"] is not None and all_cpk and r["cpk"] < all_cpk * LOW and (r["views"] or 0) >= MIN_VIEWS:
                push("Funnel flag", 3, r["brand"], f"comments ▼ {r['cpk']:.1f}/1k views (all brands {all_cpk:.1f})")
            if r["boost_off"] and "Channel B" in r["platforms"]:
                push("Paid media", 4, r["brand"], "paid boost switched off")
            elif h >= 3 and r["roi"] is not None:
                then = next((x for x in bh if x["brand"] == r["brand"] and x["hour"] == h - 3 and x["roi"] is not None), None)
                base = then["roi"] if then else None
                if base and r["roi"] < base * LOW:
                    push("Paid media", 4, r["brand"], f"ROI ▼ {r['roi']:.1f} (3h ago {base:.1f}) · spend {eur(r['ads'])}")
            if r["missing"]:
                push("Data", 5, r["brand"], f"no data checkpoint for {r['missing']} at {slot_label(h)}")
        for r in sorted([r for r in rows if r["status"] == "On track"], key=lambda r: -r["pace"]):
            head = f"pace {pct(r['pace'])} · {pct(r['achieved'])} of target" if h < 24 else f"{pct(r['achieved'])} of target"
            push("On track", 6, r["brand"], f"▲ {head}")
    return sorted(out, key=lambda a: (-a["hour"], a["rank"]))


# ----------------------------------------------------------------------------- run everything
def run(campaign: str = "9.9", platform: str = "All", asof: int = 0, data_dir: Path = DATA_DIR) -> dict:
    t = load_tables(data_dir)
    snaps = build_snapshots(t)
    hourly = build_hourly(snaps)
    bh = build_brand_hour(hourly, t["targets"], t["curve"], campaign, platform)
    bs = build_brand_status(bh, t["benchmarks"], asof, platform)
    return {"tables": t, "snapshots": snaps, "hourly": hourly, "brand_hour": bh, "brand_status": bs,
            "kpi": build_cluster_kpi(bs), "actions": build_action_points(bh)}


def export(res: dict, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(res["brand_hour"]).to_csv(out_dir / "brand_hour.csv", index=False)
    pd.DataFrame(res["brand_status"]).to_csv(out_dir / "brand_scorecard.csv", index=False)
    pd.DataFrame(res["actions"]).to_csv(out_dir / "interventions.csv", index=False)
    (out_dir / "portfolio_kpi.json").write_text(json.dumps(res["kpi"], indent=2, default=float))


if __name__ == "__main__":
    result = run()
    export(result)
    k = result["kpi"]
    print(f"Portfolio at {slot_label(k['hour'])}: {eur(k['gmv'])} of {eur(k['target'])} target "
          f"({pct(k['achieved'])}), status {k['status']}. Results written to {OUT_DIR}")
