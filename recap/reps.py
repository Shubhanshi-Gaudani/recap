"""Find interval repeats (200 m … 1 mile) in the laps of every run.

Watches mark nearly every lap as "interval" and auto-lap every mile, so the FIT flags
can't be trusted. A lap counts as a rep when it:
  1. matches a standard rep distance (± tolerance),
  2. is clearly faster than that run's easy running (its slower laps), and
  3. sits next to a recovery: a slower or much shorter lap,
  4. is faster than your typical run pace overall (drops walk breaks in ultras / Hyrox),
and the run has at least MIN_REPS of that distance.
That keeps 6 × 800 or 4 × 1 mile, and drops tempo miles, progression runs and race splits.
"""
import statistics as st

from .data import MI

DISTANCES = {"200 m": 200, "400 m": 400, "600 m": 600, "800 m": 800, "1 km": 1000,
             "1200 m": 1200, "1 mile": MI}
TOLERANCE = 0.10     # GPS laps are rarely exact; 10% keeps 800 m and 1 km apart
FASTER_THAN = 0.92   # rep pace must be ≤ 92% of the run's easy pace (sec per metre)
RECOVERY = 1.12      # a neighbour this much slower (per metre) counts as recovery
MIN_REPS = 3


def _pace(lap):
    """Seconds per metre."""
    d, t = lap.get("dist", 0), lap.get("timer") or lap.get("elapsed") or 0
    return t / d if d > 50 and t > 0 else None


def label_for(dist):
    for name, target in DISTANCES.items():
        if abs(dist - target) <= target * TOLERANCE:
            return name, target
    return None, None


def find_reps(run, laps, ceiling=None):
    """[{dist label, rep #, time, pace/mi, hr, ...}] for one run.

    ceiling: seconds per metre a rep must beat (e.g. 95% of your median run pace).
    """
    laps = [l for l in laps if _pace(l)]
    if len(laps) < 3:
        return []
    paces = sorted(_pace(l) for l in laps)
    easy = st.median(paces[len(paces) // 2:])  # median of the slower half
    out = []
    for i, lap in enumerate(laps):
        name, target = label_for(lap["dist"])
        p = _pace(lap)
        if not name or p > easy * FASTER_THAN or (ceiling and p > ceiling):
            continue
        nbrs = [laps[j] for j in (i - 1, i + 1) if 0 <= j < len(laps)]
        if not any(_pace(n) >= p * RECOVERY or n["dist"] < lap["dist"] * .6 for n in nbrs):
            continue
        sec = p * target  # normalise to the exact distance
        out.append(dict(lap_index=i, id=run.id, date=str(run.date), name=run.name, dist=name, target_m=round(target),
                        lap=i + 1, sec=round(sec, 1), pace_mi=round(p * MI, 1),
                        hr=lap.get("hr"), max_hr=lap.get("max_hr"), gps_m=round(lap["dist"])))
    # A session needs several reps of the same distance; a couple of fast laps isn't a workout.
    counts = {}
    for r in out:
        counts[r["dist"]] = counts.get(r["dist"], 0) + 1
    out = [r for r in out if counts[r["dist"]] >= MIN_REPS]
    for n, r in enumerate(out, 1):
        r["rep"] = n
    return out


def session_laps(laps):
    """Compact lap list for the per-workout chart: [dist m, seconds, hr]."""
    return [[round(l["dist"]), round(l.get("timer") or l.get("elapsed") or 0, 1),
             round(l["hr"]) if l.get("hr") else None] for l in laps if _pace(l)]  # same laps find_reps indexes
