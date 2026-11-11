"""Name every activity from what the watch recorded: workout type (inferred from laps) and place.

No titles or notes are used, so every label is built from Garmin data only:
    "Race · NYC Marathon"        a goal race or tune-up from blocks.json, found by date
    "5 × 800 m · New York"       interval reps found in the laps (see reps.py)
    "Tempo · New York"           3 km+ of back-to-back laps ~10% faster than your median run pace
    "Long run, fast finish"      10 mi+; "fast finish" if the last 3 km+ were tempo laps
    "Easy run" / "Shakeout"      everything else (shakeout = under 3 mi)
    "Strength training", "Lap swimming", …   other sports, from Garmin's activity type
"""
import collections

from .data import MI

LONG_MI = 10
SHAKEOUT_MI = 3
TEMPO_M = 3000
TEMPO_PACE = .95  # tempo laps must be ≥ 5% quicker than the rep ceiling (~10% quicker than your median run)


def _pace(lap):
    d, t = lap.get("dist", 0), lap.get("timer") or lap.get("elapsed") or 0
    return t / d if d > 50 and t > 0 else None


def fast_stretches(laps, ceiling):
    """[(start lap, end lap, metres)] runs of consecutive laps faster than `ceiling` (s/m)."""
    out, start, metres = [], None, 0.0
    laps = [l for l in laps if _pace(l)]
    for i, lap in enumerate(laps + [None]):
        if lap is not None and _pace(lap) <= ceiling:
            start = i if start is None else start
            metres += lap["dist"]
        elif start is not None:
            out.append((start, i - 1, metres))
            start, metres = None, 0.0
    return out, len(laps)


def race_days(cfg, runs):
    """{activity id: race name} for every goal race and other race in blocks.json."""
    from .analysis import other_races, race_run
    out = {}
    for race in [b["race"] for b in cfg.get("blocks", [])] + other_races(cfg):
        if a := race_run(race, runs):
            out[a.id] = race["name"]
    return out


def label(acts, laps, reps, cfg, ceiling):
    """Set `name` and extra["kind"] on every activity in place."""
    races = race_days(cfg, [a for a in acts if a.type == "Run"])
    for a in acts:
        sport = a.extra.get("sport", "")
        place = a.extra.get("location") or ("Treadmill" if "treadmill" in sport else "")
        if a.type != "Run":
            kind, what = "other", sport.replace("_v2", "").replace("_", " ").capitalize() or a.type
        elif a.id in races:
            kind, what = "race", f"Race · {races[a.id]}"
        elif reps.get(a.id):
            counts = collections.Counter(r["dist"] for r in reps[a.id])
            kind, what = "intervals", " + ".join(f"{n} × {d}" for d, n in counts.items())
        else:
            stretches, n = fast_stretches(laps.get(a.id, []), ceiling * TEMPO_PACE) if ceiling else ([], 0)
            tempo = [s for s in stretches if s[2] >= TEMPO_M]
            if a.miles >= LONG_MI:
                kind = "long"
                what = "Long run, fast finish" if tempo and tempo[-1][1] >= n - 2 else "Long run"
            elif tempo:
                kind, what = "tempo", "Tempo"
            elif a.miles < SHAKEOUT_MI:
                kind, what = "easy", "Shakeout"
            else:
                kind, what = "easy", "Easy run"
        a.extra["kind"] = kind
        a.name = f"{what} · {place}" if place and place not in what else what
