"""Activities from the Garmin Personal API cache (cache/activities, filled by `garmin_sync`)."""
import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo

MI = 1609.344
ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / "cache"


@dataclass
class Activity:
    id: str
    start: dt.datetime  # local wall-clock time
    type: str           # Run, Ride, Swim, Weight Training, Workout, Walk, Hike, ...
    name: str           # set by workouts.label(): inferred workout + place
    meters: float
    moving_s: float
    elapsed_s: float
    avg_hr: float | None
    max_hr: float | None
    cadence_spm: float | None
    file: Path | None
    extra: dict = field(default_factory=dict)  # sport, location, training effect, load, VO2max…

    @property
    def date(self):
        return self.start.date()

    @property
    def miles(self):
        return self.meters / MI

    @property
    def week(self):
        return self.date - dt.timedelta(days=self.date.weekday())



def _num(v):
    try:
        return float(v) if str(v).strip() else None
    except (TypeError, ValueError):
        return None


# ---- Garmin Personal API cache ---------------------------------------------
TYPE_MAP = {
    "running": "Run", "treadmill_running": "Run", "trail_running": "Run", "track_running": "Run",
    "indoor_running": "Run", "virtual_run": "Run", "street_running": "Run",
    "cycling": "Ride", "road_biking": "Ride", "indoor_cycling": "Ride", "virtual_ride": "Ride",
    "mountain_biking": "Ride", "gravel_cycling": "Ride",
    "lap_swimming": "Swim", "open_water_swimming": "Swim",
    "strength_training": "Weight Training", "hiit": "Workout", "indoor_cardio": "Workout",
    "fitness_equipment": "Workout", "yoga": "Yoga", "pilates": "Workout",
    "walking": "Walk", "hiking": "Hike", "indoor_rowing": "Rowing", "rowing": "Rowing",
}
GARMIN_EXTRAS = {
    "aerobicTrainingEffect": "aerobic_te", "anaerobicTrainingEffect": "anaerobic_te",
    "activityTrainingLoad": "training_load", "vO2MaxValue": "vo2max",
    "elevationGain": "elevation_gain_m", "avgPower": "avg_power", "normPower": "norm_power",
    "avgStrideLength": "stride_cm", "avgVerticalOscillation": "vert_osc_cm",
    "avgGroundContactTime": "gct_ms", "minTemperature": "min_temp_c", "maxTemperature": "max_temp_c",
    "trainingEffectLabel": "te_label", "moderateIntensityMinutes": "moderate_min",
    "vigorousIntensityMinutes": "vigorous_min", "lactateThresholdBpm": "lthr",
    "hrTimeInZone_1": "z1_s", "hrTimeInZone_2": "z2_s", "hrTimeInZone_3": "z3_s",
    "hrTimeInZone_4": "z4_s", "hrTimeInZone_5": "z5_s",
}


def _type_key(a):
    t = a.get("activityType") or {}
    return (t.get("typeKey") if isinstance(t, dict) else str(t)) or ""


def _garmin_type(a):
    key = _type_key(a)
    return TYPE_MAP.get(key, (key or "Other").replace("_", " ").title())


def load_cache(cache=CACHE):
    idx_path = Path(cache) / "activities/index.json"
    if not idx_path.exists():
        return []
    out = []
    for k, a in json.loads(idx_path.read_text()).items():
        start = a.get("startTimeLocal") or a.get("startTimeGMT")
        if not start:
            continue
        fit = Path(cache) / "activities" / f"{k}.fit"
        cad = a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute")
        out.append(Activity(
            id=str(k),
            start=dt.datetime.fromisoformat(start.replace("T", " ")[:19]),
            type=_garmin_type(a), name="",
            meters=_num(a.get("distance")) or 0.0,
            moving_s=_num(a.get("movingDuration")) or _num(a.get("duration")) or 0.0,
            elapsed_s=_num(a.get("elapsedDuration")) or _num(a.get("duration")) or 0.0,
            avg_hr=_num(a.get("averageHR")), max_hr=_num(a.get("maxHR")),
            cadence_spm=_num(cad),
            file=fit if fit.exists() else None,
            extra={"sport": _type_key(a), "location": a.get("locationName") or "",
                   **{v: a[k2] for k2, v in GARMIN_EXTRAS.items() if a.get(k2) is not None}},
        ))
    return sorted(out, key=lambda a: a.start)


def load(config=None):
    """Activities from the Garmin API cache. Names are filled in later by workouts.label()."""
    return load_cache(), "garmin-api"
