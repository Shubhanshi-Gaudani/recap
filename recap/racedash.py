"""Everything the race dashboard shows for one race: splits, the night before, the weeks before."""
import datetime as dt
import statistics as st

from .analysis import fmt_pace, race_records
from .data import MI

# metric -> (label, unit, higher is better?)
NIGHT = {
    "sleep_h": ("Sleep", "h", True), "sleep_score": ("Sleep score", "", True),
    "deep_pct": ("Deep sleep", "%", True), "rem_pct": ("REM sleep", "%", True),
    "hrv": ("Overnight HRV", "ms", True), "rhr": ("Resting HR", "bpm", False),
    "body_battery": ("Body Battery", "", True), "respiration": ("Respiration", "brpm", None),
}
BASELINE_DAYS = 28


def splits(records):
    """Per-mile splits of the race, the last one partial: pace, HR, cadence, climb."""
    recs = [r for r in records if "dist" in r]
    if len(recs) < 60:
        return []
    out, start = [], 0
    d0 = recs[0]["dist"]

    def close(i, j):
        seg = recs[i:j + 1]
        dist = (seg[-1]["dist"] - seg[0]["dist"]) / MI
        sec = seg[-1]["ts"] - seg[0]["ts"]
        hrs = [x["hr"] for x in seg if "hr" in x]
        cad = [x["cad"] * 2 for x in seg if x.get("cad")]  # FIT running cadence is per foot
        alt = [x["alt"] for x in seg if "alt" in x]
        climb = sum(max(0, b - a) for a, b in zip(alt, alt[1:])) if alt else None
        out.append(dict(mile=len(out) + 1, dist=round(dist, 2), sec=round(sec),
                        pace=round(sec / dist) if dist > .05 else None,
                        hr=round(st.mean(hrs)) if hrs else None,
                        max_hr=round(max(hrs)) if hrs else None,
                        cad=round(st.mean(cad)) if cad else None,
                        climb_m=round(climb) if climb is not None else None))

    mark = d0 + MI
    for i, r in enumerate(recs):
        if r["dist"] >= mark:
            close(start, i)
            start, mark = i, mark + MI
    if recs[-1]["dist"] - recs[start]["dist"] > .1 * MI:
        close(start, len(recs) - 1)
    return out


def night_before(race_day, health):
    """{metric: {value, base, label, unit, better}}. Garmin dates sleep/HRV by the morning
    you wake up, so race day's record is the night before the race."""
    out = {}
    for k, (label, unit, better) in NIGHT.items():
        series = dict(health.get(k, []))
        v = series.get(str(race_day))
        prior = [series[d] for d in (str(race_day - dt.timedelta(days=i)) for i in range(1, BASELINE_DAYS + 1))
                 if d in series]
        if v is None:
            continue
        out[k] = dict(value=v, base=round(st.median(prior), 1) if len(prior) >= 7 else None,
                      label=label, unit=unit, better=better)
    return out


def lead_in(race, race_day, runs, kinds):
    """The 8 weeks before the race (each week ends the day before race day)."""
    before = [r for r in runs if r.date < race_day and r.id != race["id"]]
    weeks = []
    for w in range(8, 0, -1):
        end = race_day - dt.timedelta(days=1 + 7 * (w - 1))
        start = end - dt.timedelta(days=6)
        rs = [r for r in before if start <= r.date <= end]
        weeks.append(dict(weeks_out=w, end=str(end), mi=round(sum(r.miles for r in rs), 1), runs=len(rs)))
    peak = max((w["mi"] for w in weeks), default=0)
    six = [r for r in before if r.date >= race_day - dt.timedelta(weeks=6)]
    longest = max(six, key=lambda r: r.miles, default=None)
    hard = [r for r in before if r.date >= race_day - dt.timedelta(days=14)
            and kinds.get(r.id) in ("intervals", "tempo", "race")]
    last = max((r.date for r in before), default=None)
    return dict(
        weeks=weeks,
        last7=weeks[-1]["mi"] if weeks else 0,
        peak=peak,
        taper_pct=round(100 * (1 - weeks[-1]["mi"] / peak)) if peak else None,
        longest=dict(mi=round(longest.miles, 1), date=str(longest.date), name=longest.name) if longest else None,
        hard=[dict(date=str(r.date), name=r.name) for r in sorted(hard, key=lambda r: r.date)],
        rest_days=(race_day - last).days - 1 if last else None,
    )


def dash(races, acts, fit, health):
    """One entry per race in analysis output, newest last."""
    by_id = {a.id: a for a in acts}
    runs = [a for a in acts if a.type == "Run"]
    kinds = {a.id: a.extra.get("kind") for a in acts}
    out = []
    for race in races:
        a = by_id.get(race["id"])
        if not a:
            continue
        _, recs = race_records(a, race["distance_mi"], fit.get(a.id))
        sp = splits(recs)
        full = [s for s in sp if s["dist"] > .9 and s["pace"]]
        half = len(full) // 2
        first = sum(s["sec"] for s in full[:half]) / max(1, sum(s["dist"] for s in full[:half]))
        second = sum(s["sec"] for s in full[half:]) / max(1, sum(s["dist"] for s in full[half:]))
        hrs = [s["hr"] for s in full if s["hr"]]
        temps = [x["temp"] for x in recs if "temp" in x]
        day = a.date
        out.append(dict(
            id=a.id, name=race["name"], date=race["date"], goal=race["goal"], block=race["block"],
            distance_mi=race["distance_mi"], time=race["time"], seconds=race["seconds"], pace=race["pace"],
            start=a.start.strftime("%-I:%M %p"), place=a.extra.get("location", ""),
            avg_hr=round(a.avg_hr) if a.avg_hr else None, max_hr=round(a.max_hr) if a.max_hr else None,
            splits=sp,
            first_half=fmt_pace(first) if half else None, second_half=fmt_pace(second) if half else None,
            fade_pct=round(100 * (second / first - 1), 1) if half and first else None,
            hr_drift=(hrs[-1] - hrs[0]) if len(hrs) > 1 else None,
            fastest=min(full, key=lambda s: s["pace"])["mile"] if full else None,
            slowest=max(full, key=lambda s: s["pace"])["mile"] if full else None,
            climb_m=round(a.extra["elevation_gain_m"]) if a.extra.get("elevation_gain_m") else None,
            temp_c=dict(min=a.extra.get("min_temp_c"), max=a.extra.get("max_temp_c"),
                        watch=round(st.median(temps)) if temps else None),
            aerobic_te=a.extra.get("aerobic_te"), training_load=a.extra.get("training_load"),
            marathon_equiv=race["marathon_equiv"],
            night=night_before(day, health),
            lead_in=lead_in(race, day, runs, kinds),
        ))
    return out
