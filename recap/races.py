"""Spot runs that look like races, from Garmin data alone.

A race is an unusually fast effort for its distance. For every outdoor run near a standard race
distance (allowing for a warm-up or a walk home on the watch), take the fastest stretch of that
distance, convert it to the marathon time it predicts (Riegel), and compare it with your other
runs from the two months around it. Garmin's own "race" event type always counts.
"""
import json
import statistics as st

from .analysis import best_effort, fmt_pace, fmt_time
from .data import CACHE, MI, load_cache
from .fit import read_records

DISTANCES = {"5K": 5000, "4M": 4 * MI, "8K": 8000, "5M": 5 * MI, "10K": 10000, "15K": 15000,
             "10M": 10 * MI, "Half": 21097.5, "18M": 18 * MI, "Marathon": 42195}
DISTANCE_MI = {k: round(v / MI, 3) for k, v in DISTANCES.items()}
SUGGEST = 0.76  # ≤ 76% of your usual marathon-equivalent for the period → probably a race
WINDOW_DAYS = 60


def _event_types():
    idx = CACHE / "activities" / "index.json"
    if not idx.exists():
        return {}
    return {k: (a.get("eventType") or {}).get("typeKey") for k, a in json.loads(idx.read_text()).items()}


def candidates(acts=None, limit=30):
    """Likely races, fastest-for-the-period first: dicts with id, date, distance, time, score."""
    acts = acts if acts is not None else load_cache()
    events = _event_types()
    rows = []
    for r in acts:
        if r.type != "Run" or "treadmill" in r.extra.get("sport", "") or not r.meters:
            continue
        fits = [(n, m) for n, m in DISTANCES.items() if m * .97 <= r.meters <= m * 1.3]
        if not fits:
            continue
        name, metres = max(fits, key=lambda x: x[1])
        t = best_effort(read_records(r.file), metres) if r.file else None
        t = t or (r.moving_s or r.elapsed_s) * metres / r.meters
        rows.append(dict(id=r.id, date=str(r.date), weekday=r.start.strftime("%a"), hour=r.start.hour,
                         distance=name, distance_mi=DISTANCE_MI[name], seconds=round(t),
                         time=fmt_time(t), pace=fmt_pace(t / (metres / MI)),
                         avg_hr=round(r.avg_hr) if r.avg_hr else None,
                         place=r.extra.get("location", ""), race_flag=events.get(r.id) == "race",
                         equiv=t * (42195 / metres) ** 1.06, _d=r.date))
    for x in rows:
        near = [y["equiv"] for y in rows if abs((y["_d"] - x["_d"]).days) <= WINDOW_DAYS]
        score = x["equiv"] / st.median(near)
        weekend_am = x["weekday"] in ("Sat", "Sun") and x["hour"] < 11  # most races
        x["score"] = round(score, 3)
        x["suggested"] = x["race_flag"] or (weekend_am and score <= SUGGEST)
    rows.sort(key=lambda x: (not x["race_flag"], x["score"]))
    out = rows[:limit]
    for x in out:
        x.pop("_d"); x.pop("equiv")
    return sorted(out, key=lambda x: x["date"])
