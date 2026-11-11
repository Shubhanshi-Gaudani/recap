"""Turn activities into the handful of numbers the recap is built on."""
import collections
import datetime as dt
import statistics as st

from .data import MI
from .fit import read_fit

MIN_BASE_MI = 8  # typical week below this = coming back from a break; skip the ratio
CROSS_TYPES = {"Ride", "Swim", "Rowing", "Water Sport", "Elliptical", "Yoga"}


def resolve_window(cfg, acts=()):
    """(start, end) dates. `start` may be "first" (your first Garmin activity);
    `end` may be "today" or omitted, meaning up to now."""
    start, *rest = cfg["window"]
    if start in (None, "", "first"):
        start = min((a.date for a in acts), default=dt.date.today()).isoformat()
    end = rest[0] if rest and rest[0] not in (None, "", "today", "now") else dt.date.today().isoformat()
    return parse_date(start), parse_date(end)


def active_blocks(cfg, w0, w1):
    """Blocks whose race falls inside the window, clipped to start no earlier than it."""
    out = []
    for b in cfg["blocks"]:
        race_day = parse_date(b["race"].get("date") or b["end"])
        if w0 <= race_day <= w1:
            out.append({**b, "start": str(max(parse_date(b["start"]), w0))})
    return out


def race_run(race, runs):
    """The run that was this race: its Garmin activity `id` if given, else the run that day
    closest to the race distance (so a warm-up or a later run doesn't get picked)."""
    if race.get("id"):
        hit = [a for a in runs if a.id == str(race["id"])]
        if hit:
            return hit[0]
    day = [a for a in runs if str(a.date) == race.get("date")]
    if not day:
        return None
    target = race.get("distance_mi", 0) * MI
    long_enough = [a for a in day if a.meters >= target * .95] or day
    return min(long_enough, key=lambda a: abs(a.meters - target))


def other_races(cfg):
    """Non-goal races from blocks.json (`races`; `tuneups` is the old name)."""
    return cfg.get("races", cfg.get("tuneups", []))


def best_effort_span(records, meters):
    """(seconds, first ts, last ts) of the fastest stretch of `meters` in per-second records."""
    pts = [(x["ts"], x["dist"]) for x in records if "dist" in x]
    best, j = None, 0
    for i in range(len(pts)):
        while j < len(pts) and pts[j][1] - pts[i][1] < meters:
            j += 1
        if j == len(pts):
            break
        t = pts[j][0] - pts[i][0]
        if best is None or t < best[0]:
            best = (t, pts[i][0], pts[j][0])
    return best


def best_effort(records, meters):
    """Fastest time (s) over any stretch of `meters`."""
    span = best_effort_span(records, meters)
    return span[0] if span else None


def race_records(a, distance_mi, records):
    """(seconds, records of just the race). Elapsed time and the whole recording, unless the
    watch recorded more than the race (a warm-up, a walk home, or left running after the
    finish): then the fastest stretch of the official distance."""
    if records and (a.meters > distance_mi * MI * 1.06 or a.elapsed_s > a.moving_s * 1.15):
        if span := best_effort_span(records, distance_mi * MI):
            return span[0], [x for x in records if span[1] <= x["ts"] <= span[2]]
    return a.elapsed_s, records or []


def race_time(a, distance_mi, records):
    return race_records(a, distance_mi, records)[0]


def load_fit(runs, laps=None):
    """Per-second records by run id; pass a dict as `laps` to also collect each run's laps."""
    out = {}
    for r in runs:
        if r.file and r.file.exists():
            f = read_fit(r.file)
            out[r.id] = f["records"]
            if laps is not None:
                laps[r.id] = f["laps"]
    return out


def parse_date(s):
    return dt.date.fromisoformat(s)


def is_strength(a):
    return a.type == "Weight Training"


def in_range(a, start, end):
    return start <= a.date <= end


def fmt_pace(sec_per_mile):
    m, s = divmod(round(sec_per_mile), 60)
    return f"{m}:{s:02d}"


def fmt_time(sec):
    h, rem = divmod(round(sec), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def weekly_miles(runs, start, end):
    weeks = collections.OrderedDict()
    w = start - dt.timedelta(days=start.weekday())
    while w <= end:
        weeks[w] = 0.0
        w += dt.timedelta(days=7)
    for r in runs:
        if r.week in weeks:
            weeks[r.week] += r.miles
    return weeks


def mile_splits(records):
    """[(seconds, avg_hr)] per mile from per-second FIT records."""
    recs = [r for r in records if "dist" in r]
    out, mark, t0, hrs = [], MI, recs[0]["ts"], []
    for r in recs:
        if "hr" in r:
            hrs.append(r["hr"])
        if r["dist"] >= mark:
            out.append((r["ts"] - t0, st.mean(hrs) if hrs else None))
            t0, mark, hrs = r["ts"], mark + MI, []
    return out


def efficiency_factor(records):
    """Friel's EF: meters/min ÷ bpm over moving samples. Higher = more speed per heartbeat."""
    pts = [(r["speed"], r["hr"]) for r in records
           if r.get("speed", 0) > 1.8 and r.get("hr", 0) > 100]
    if len(pts) < 600:  # need ≥10 min of clean data
        return None
    return st.mean(p[0] for p in pts) * 60 / st.mean(p[1] for p in pts)


def analyze(acts, cfg, fit=None):
    w0, w1 = resolve_window(cfg, acts)
    cfg = {**cfg, "blocks": active_blocks(cfg, w0, w1)}
    window = [a for a in acts if in_range(a, w0, w1)]
    runs = [a for a in window if a.type == "Run"]
    if not runs:
        raise ValueError(f"No runs between {w0} and {w1}.")

    # Per-second data is parsed once per run.
    if fit is None:
        fit = load_fit(runs)

    def find(race):
        return race_run(race, runs)

    # ---- races -------------------------------------------------------------
    races = []
    for b in cfg["blocks"]:
        a = find(b["race"])
        if a is None:
            continue
        splits = mile_splits(fit[a.id]) if fit.get(a.id) else []
        half = len(splits) // 2
        first = sum(s for s, _ in splits[:half]) or 1
        second = sum(s for s, _ in splits[half:2 * half]) or 1
        sec = race_time(a, b["race"]["distance_mi"], fit.get(a.id))
        races.append(dict(id=a.id, block=b["key"], goal=True, key=b["race"]["name"], name=b["race"]["name"], date=str(a.date),
                          distance_mi=b["race"]["distance_mi"],
                          miles=round(a.miles, 2), time=fmt_time(sec), seconds=sec,
                          pace=fmt_pace(sec / b["race"]["distance_mi"]), avg_hr=a.avg_hr,
                          split_drift_pct=round(100 * (second - first) / first, 1),
                          splits=[dict(mile=i + 1, pace=round(s), hr=round(h) if h else None)
                                  for i, (s, h) in enumerate(splits)]))
    for t in other_races(cfg):
        if not (a := find(t)) or not in_range(a, w0, w1):
            continue
        sec = race_time(a, t["distance_mi"], fit.get(a.id))
        races.append(dict(id=a.id, block="tuneup", goal=False, key=f'{t["name"]} {t["date"]}', name=t["name"],
                          date=str(a.date), distance_mi=t["distance_mi"],
                          miles=round(a.miles, 2), time=fmt_time(sec), seconds=sec,
                          pace=fmt_pace(sec / t["distance_mi"]), avg_hr=a.avg_hr))
    races.sort(key=lambda r: r["date"])

    # Riegel: T2 = T1 · (D2/D1)^1.06 — what each race "predicts" for a marathon.
    # Uses the official race distance, not the (always long) GPS distance.
    for r in races:
        r["marathon_equiv_s"] = round(r["seconds"] * (26.219 / r["distance_mi"]) ** 1.06)
        r["marathon_equiv"] = fmt_time(r["marathon_equiv_s"])

    # Effort reference: the last goal race's average HR, else a hard-run proxy.
    goal_hr = [r["avg_hr"] for r in races if r["block"] != "tuneup" and r["avg_hr"]]
    run_hr = sorted(r.avg_hr for r in runs if r.avg_hr)
    race_hr = goal_hr[-1] if goal_hr else (run_hr[int(len(run_hr) * .9)] if run_hr else 175)
    hard_hr = race_hr - 10

    # ---- blocks ------------------------------------------------------------
    periods = [dict(key=b["key"], name=b["name"], start=parse_date(b["start"]), end=min(parse_date(b["end"]), w1))
               for b in cfg["blocks"]]
    after = parse_date(cfg["blocks"][-1]["end"]) + dt.timedelta(days=1) if cfg["blocks"] else w0
    if after <= w1:
        name = f"After the {cfg['blocks'][-1]['race']['name']}" if cfg["blocks"] else "All training"
        periods.append(dict(key="Post", name=name, start=after, end=w1))

    blocks = []
    for p in periods:
        acts_p = [a for a in window if in_range(a, p["start"], p["end"])]
        rp = [a for a in acts_p if a.type == "Run"]
        if not rp:
            continue
        weeks = weekly_miles(rp, p["start"], p["end"])
        n_weeks = len(weeks)
        hr_samples = [x["hr"] for r in rp if r.id in fit for x in fit[r.id] if "hr" in x]
        bands = collections.Counter(
            "easy" if h < 150 else "steady" if h < 165 else "hard" if h < race_hr else "race+"
            for h in hr_samples)
        easy_hr = [r.avg_hr for r in rp if r.avg_hr and r.miles >= 3
                   and r.extra.get("kind", "easy") == "easy" and r.miles < 12]
        blocks.append(dict(
            key=p["key"], name=p["name"], start=str(p["start"]), end=str(p["end"]),
            weeks=n_weeks,
            miles=round(sum(r.miles for r in rp)),
            avg_week=round(sum(weeks.values()) / n_weeks, 1),
            peak_week=round(max(weeks.values()), 1),
            runs_per_week=round(len(rp) / n_weeks, 1),
            long_runs_12mi=sum(r.miles >= 12 for r in rp),
            longest=round(max(r.miles for r in rp), 1),
            strength_per_week=round(sum(map(is_strength, acts_p)) / n_weeks, 2),
            cross_per_week=round(sum(a.type in CROSS_TYPES for a in acts_p) / n_weeks, 2),
            pace=fmt_pace(sum(r.moving_s for r in rp) / sum(r.miles for r in rp)),
            median_easy_hr=round(st.median(easy_hr)) if easy_hr else None,
            hr_share={k: round(100 * bands[k] / max(1, len(hr_samples))) for k in
                      ("easy", "steady", "hard", "race+")},
            share_within_10_of_race=round(100 * sum(h >= hard_hr for h in hr_samples)
                                          / max(1, len(hr_samples))),
        ))

    # ---- mileage jumps: acute:chronic ratio ---------------------------------
    all_runs = [a for a in acts if a.type == "Run"]

    first_run = min((r.date for r in all_runs), default=w0)

    def miles_between(a, b):
        return sum(r.miles for r in all_runs if a <= r.date <= b)

    def acwr(day):
        """Last-7-day miles ÷ average week of the 28 days before that."""
        acute = miles_between(day - dt.timedelta(days=6), day)
        chronic = miles_between(day - dt.timedelta(days=34), day - dt.timedelta(days=7)) / 4
        # Needs a full month of history and a real base; after a break the ratio is meaningless.
        ok = chronic >= MIN_BASE_MI and day - dt.timedelta(days=34) >= first_run
        return acute, chronic, (acute / chronic if ok else None)

    daily = []
    for i in range((w1 - w0).days + 1):
        day = w0 + dt.timedelta(days=i)
        acute, chronic, ratio = acwr(day)
        daily.append(dict(date=str(day), last7=round(acute, 1), ratio=round(ratio, 2) if ratio else None))
    ratios = [d["ratio"] for d in daily if d["ratio"]] or [1.0]
    load = dict(daily=daily, median_ratio=round(st.median(ratios), 2),
                pct_days_ratio_ge_1_3=round(100 * sum(r >= 1.3 for r in ratios) / len(ratios)))
    weeks = weekly_miles(runs, w0, w1)

    # ---- aerobic efficiency by month --------------------------------------
    ef = collections.defaultdict(list)
    for r in runs:
        if r.id in fit and (v := efficiency_factor(fit[r.id])):
            ef[r.date.strftime("%Y-%m")].append(v)
    ef_month = [dict(month=m, ef=round(st.median(v), 3), runs=len(v))
                for m, v in sorted(ef.items()) if len(v) >= 5]

    return dict(
        window=[str(w0), str(w1)],
        totals=dict(runs=len(runs), miles=round(sum(r.miles for r in runs)),
                    hours=round(sum(r.moving_s for r in runs) / 3600)),
        race_hr=race_hr,
        races=races,
        blocks=blocks,
        weekly=[dict(week=str(w), miles=round(m, 1),
                     block=next((p["key"] for p in periods[:-1] if p["start"] <= w + dt.timedelta(days=6)
                                 and w <= p["end"]), None))
                for w, m in weeks.items()],
        load=load,
        efficiency=ef_month,
    )
