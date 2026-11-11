"""Assemble everything the visual recap draws, then render report/recap.html.

    python3 -m recap.build [--config blocks.json] [--since YYYY-MM-DD] [--until YYYY-MM-DD]

Garmin data only (the API cache filled by garmin_sync).
"""
import argparse
import collections
import datetime as dt
import itertools
import json
import math
import statistics as st
from pathlib import Path

from . import health
from .analysis import analyze, efficiency_factor, load_fit, parse_date, resolve_window
from .data import ROOT, load
from .racedash import dash
from .reps import find_reps, session_laps
from .workouts import label

TEMPLATE = Path(__file__).with_name("template.html")
OUT = ROOT / "report" / "recap.html"


def period_of(day, periods):
    for p in periods:
        if p["start"] <= day <= p["end"]:
            return p["key"]
    return None


def routes(runs, fit, periods, every_s=12):
    """Down-sampled GPS tracks, grouped into places (NYC vs race-day trips)."""
    out = []
    for r in runs:
        pts = [(x["lon"], x["lat"]) for i, x in enumerate(fit.get(r.id, []))
               if "lat" in x and i % every_s == 0 and abs(x["lat"]) > 0.1]
        if len(pts) < 10:
            continue
        lon = st.median(p[0] for p in pts)
        lat = st.median(p[1] for p in pts)
        out.append(dict(id=r.id, date=str(r.date), name=r.name, miles=round(r.miles, 1),
                        block=period_of(r.date, periods), c=[round(lon, 3), round(lat, 3)],
                        p=[[round(x, 4), round(y, 4)] for x, y in pts]))
    # "Home" = within 40 km of the median run location; everything else is a trip.
    if out:
        hx = st.median(r["c"][0] for r in out)
        hy = st.median(r["c"][1] for r in out)
        for r in out:
            dx = (r["c"][0] - hx) * 111 * math.cos(math.radians(hy))
            dy = (r["c"][1] - hy) * 111
            r["home"] = math.hypot(dx, dy) < 40
    return out


def hr_ridges(runs, fit, lo=100, hi=200, step=2):
    """Per-month histogram of running heart-rate seconds (shares, sum to 1)."""
    by = collections.defaultdict(lambda: [0] * ((hi - lo) // step))
    for r in runs:
        for x in fit.get(r.id, []):
            h = x.get("hr")
            if h and lo <= h < hi:
                by[r.date.strftime("%Y-%m")][int((h - lo) // step)] += 1
    out = []
    for m in sorted(by):
        tot = sum(by[m])
        if tot > 3600:  # at least an hour of HR that month
            hist = by[m]
            med = lo + step * next(i for i, c in enumerate(itertools.accumulate(hist)) if c >= tot / 2)
            out.append(dict(month=m, hours=round(tot / 3600, 1), median=med,
                            share=[round(c / tot, 4) for c in hist]))
    return dict(lo=lo, hi=hi, step=step, months=out)


def build(config="blocks.json", since=None, until=None):
    cfg = json.loads((ROOT / config).read_text())
    if since or until:
        start, *rest = cfg["window"]
        cfg["window"] = [since or start, until or (rest[0] if rest else "today")]
    acts, src = load(cfg)
    if not acts:
        raise SystemExit("No activities — run `python3 -m garmin_mcp.sync` first.")
    w0, w1 = resolve_window(cfg, acts)
    window = [a for a in acts if w0 <= a.date <= w1]
    runs = [a for a in window if a.type == "Run"]
    laps = {}
    fit = load_fit(runs, laps)

    # Interval reps from each run's laps, then a name for every activity (no titles or notes).
    paces = sorted(r.moving_s / r.meters for r in runs if r.meters > 3000 and r.moving_s)
    ceiling = paces[len(paces) // 2] * .95 if paces else None
    found = {r.id: find_reps(r, laps.get(r.id, []), ceiling) for r in runs}
    label(window, laps, found, cfg, ceiling)
    for r in runs:
        for x in found[r.id]:
            x["name"] = r.name
    m = analyze(acts, cfg, fit)

    # Same periods the analysis used: blocks whose race is in the window, then "after".
    race_dates = {b["key"]: b["race"].get("date") or b["end"] for b in cfg["blocks"]}
    allp = [dict(key=p["key"], name=p["name"], start=parse_date(p["start"]), end=parse_date(p["end"]),
                 race_date=race_dates.get(p["key"])) for p in m["blocks"]]
    periods = [p for p in allp if p["key"] != "Post"]

    # Every day of the window: miles run, plus what else happened.
    days = collections.OrderedDict()
    for i in range((w1 - w0).days + 1):
        d = w0 + dt.timedelta(days=i)
        days[str(d)] = dict(mi=0.0, runs=[], other=[], block=period_of(d, allp))
    for a in window:
        day = days[str(a.date)]
        if a.type == "Run":
            day["mi"] += a.miles
            day["runs"].append(a.name)
        else:
            day["other"].append(a.type)
    for v in days.values():
        v["mi"] = round(v["mi"], 2)

    # Each block's weekly miles counted back from its race day.
    aligned = []
    for p in periods:
        race = parse_date(p["race_date"])
        weeks = []
        for wk in range(-int((race - p["start"]).days // 7), 1):
            end = race + dt.timedelta(days=7 * wk)
            s = end - dt.timedelta(days=6)
            rs = [r for r in runs if s <= r.date <= end]
            weeks.append(dict(w=wk, mi=round(sum(r.miles for r in rs), 1),
                              long=round(max((r.miles for r in rs), default=0), 1), end=str(end)))
        aligned.append(dict(key=p["key"], name=p["name"], weeks=weeks))

    # Every run's efficiency factor.
    ef = []
    for r in runs:
        if r.id in fit and (v := efficiency_factor(fit[r.id])):
            ef.append(dict(date=str(r.date), ef=round(v, 4), mi=round(r.miles, 1), name=r.name,
                           block=period_of(r.date, allp), temp=r.extra.get("avg_temp_c")))

    # Interval sessions, for the repeats and workout charts.
    reps, sessions = [], []
    for r in runs:
        if not found[r.id]:
            continue
        block = period_of(r.date, allp)
        for x in found[r.id]:
            x["block"] = block
        reps += found[r.id]
        counts = collections.Counter(x["dist"] for x in found[r.id])
        sessions.append(dict(id=r.id, date=str(r.date), name=r.name, place=r.extra.get("location", ""),
                             block=block, summary=" + ".join(f"{n} × {d}" for d, n in counts.items()),
                             laps=session_laps(laps[r.id]),
                             reps={x["lap_index"]: x["dist"] for x in found[r.id]}))
    kinds = collections.Counter(r.extra["kind"] for r in runs)

    # When: weekday x hour of day (local time).
    clock = collections.Counter((r.start.weekday(), r.start.hour) for r in runs)

    # Health from a month before the window, so races near the start still get a baseline.
    hl = health.collect(w0 - dt.timedelta(days=35), w1)
    data = dict(
        source=src, generated=dt.datetime.now().isoformat(timespec="minutes"),
        window=[str(w0), str(w1)], metrics=m,
        periods=[dict(key=p["key"], name=p["name"], start=str(p["start"]), end=str(p["end"]),
                      race_date=p.get("race_date")) for p in allp],
        days=days, aligned=aligned, ef=ef,
        clock=[[d, h, n] for (d, h), n in clock.items()],
        athlete=cfg.get("athlete", ""), kinds=dict(kinds),
        ridges=hr_ridges(runs, fit),
        reps=reps, sessions=sessions,
        routes=routes(runs, fit, allp),
        health=hl,
        races=dash(m["races"], acts, fit, hl),
    )
    OUT.parent.mkdir(exist_ok=True)
    blob = json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")
    OUT.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", blob))
    (OUT.parent / "metrics.json").write_text(json.dumps(m, indent=1, default=str))
    return dict(report=str(OUT), source=src, kb=round(OUT.stat().st_size / 1024),
                totals=m["totals"], races=[{k: r[k] for k in ("name", "time", "pace", "marathon_equiv")}
                                           for r in m["races"]])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="blocks.json")
    ap.add_argument("--since", help="YYYY-MM-DD; overrides the window start in the config")
    ap.add_argument("--until", help="YYYY-MM-DD or 'today' (default: the config's end, else today)")
    print(json.dumps(build(**vars(ap.parse_args())), indent=1))
