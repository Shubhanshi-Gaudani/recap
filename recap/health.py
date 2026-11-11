"""Daily recovery signals from the Garmin API cache (see garmin_mcp/store.py for layout).

Field paths below match what the Personal API actually returns (checked against a real
sync, Nov 2026).
"""
import datetime as dt
import json

from .data import CACHE

# metric -> (dataset folder, path to the list of daily records, date key, value path, transform)
METRICS = {
    "hrv": ("hrv", "hrvSummaries", "calendarDate", "lastNightAvg", None),
    "rhr": ("daily_summaries", "data", "calendarDate", "heartRate.restingValue", None),
    "sleep_h": ("sleep", "", "calendarDate", "sleepTimeSeconds", lambda v: round(v / 3600, 2)),
    "sleep_score": ("sleep", "", "calendarDate", "sleepScores.overall.value", None),
    "deep_pct": ("sleep", "", "calendarDate", "sleepScores.deepPercentage.value", None),
    "rem_pct": ("sleep", "", "calendarDate", "sleepScores.remPercentage.value", None),
    "body_battery": ("daily_summaries", "data", "calendarDate", "bodyBattery.maxValue", None),
    "bb_charged": ("daily_summaries", "data", "calendarDate", "bodyBattery.chargedValue", None),
    "stress": ("daily_summaries", "data", "calendarDate", "stress.avgLevel", None),
    "respiration": ("sleep", "", "calendarDate", "averageRespirationValue", None),
    "steps": ("daily_summaries", "data", "calendarDate", "movement.steps.value", None),
}


def _get(obj, path):
    for part in path.split(".") if path else []:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(part)
    return obj


def collect(start: dt.date, end: dt.date):
    """{metric: [(date, value), ...]} for every metric with at least a week of data."""
    root = CACHE / "health"
    out = {}
    for metric, (folder, list_path, date_key, value_path, fn) in METRICS.items():
        series = {}
        for path in sorted((root / folder).glob("*.json")) if (root / folder).exists() else []:
            try:
                records = _get(json.loads(path.read_text()), list_path)
            except ValueError:
                continue
            for rec in records if isinstance(records, list) else []:
                day = str(rec.get(date_key) or "")[:10]
                v = _get(rec, value_path)
                if (str(start) <= day <= str(end) and isinstance(v, (int, float))
                        and not isinstance(v, bool) and v > 0):
                    series[day] = fn(v) if fn else v
        if len(series) >= 7:
            out[metric] = sorted(series.items())
    return out
