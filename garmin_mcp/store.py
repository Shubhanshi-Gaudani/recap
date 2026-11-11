"""Local cache + bulk backfill ("sync") of everything the Personal API exposes.

Layout (all gitignored):
    cache/activities/index.json        activity summaries, keyed by activityId
    cache/activities/<id>.json         activity detail
    cache/activities/<id>.workouts.json
    cache/activities/<id>.fit          original FIT file (unzipped)
    cache/health/<dataset>/<start>_<end>.json   range endpoints, in chunks
    cache/health/<dataset>/<date>.json          single-day endpoints
    cache/profile/<name>.json
"""
from __future__ import annotations

import datetime as dt
import io
import json
import time
import zipfile
from pathlib import Path

from .api import ROOT, Client, GarminError

CACHE = ROOT / "cache"
PAUSE_S = 0.25  # be polite: Garmin publishes no rate limit for the Personal tier

# dataset name -> (client method, preferred chunk in days)
RANGE_DATASETS = {
    "daily_summaries": ("daily_summaries", 28),
    "sleep": ("sleep_summaries", 28),
    "hrv": ("hrv_summaries", 28),
    "body_battery": ("body_battery", 28),
    "stress": ("daily_stress", 28),
    "steps": ("daily_steps", 28),
    "hydration": ("daily_hydration", 28),
    "weight": ("weight", 90),
    "weight_goals": ("weight_goals", 90),
    "wellness_activities": ("wellness_activity_summaries", 28),
}
# Per-day endpoints are expensive over a year (one call a day), so they're opt-in.
DAY_DATASETS = {
    "sleep_detail": "sleep",
    "hrv_detail": "hrv",
    "heart_rate": "heart_rate",
    "spo2": "spo2",
    "fitness_age": "fitness_age",
    "food_logs": "food_logs",
    "menstrual": "menstrual_summary",
}
PROFILE = {"settings": "profile_settings", "hr_zones": "heart_rate_zones"}


def _write(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1))


def _read(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def daterange_chunks(start: dt.date, end: dt.date, days: int):
    s = start
    while s <= end:
        e = min(end, s + dt.timedelta(days=days - 1))
        yield s, e
        s = e + dt.timedelta(days=1)


class Store:
    def __init__(self, client: Client | None = None):
        self.c = client or Client()

    def _call(self, fn, *a, **kw):
        out = getattr(self.c, fn)(*a, **kw)
        time.sleep(PAUSE_S)
        return out

    # ---- activities ---------------------------------------------------------
    def index(self) -> dict:
        return _read(CACHE / "activities/index.json") or {}

    def sync_activities(self, start: dt.date, end: dt.date, fit=True, details=True, log=print):
        idx = self.index()
        page, n_new = 0, 0
        while True:
            batch = self._call("search_activities", start=page * 100, limit=100,
                               start_date=start, end_date=end) or []
            for a in batch:
                key = str(a["activityId"])
                n_new += key not in idx
                idx[key] = a
            log(f"activities: page {page} → {len(batch)} (total {len(idx)})")
            if len(batch) < 100:
                break
            page += 1
        _write(CACHE / "activities/index.json", idx)

        in_range = [k for k, a in idx.items()
                    if start.isoformat() <= a.get("startTimeLocal", "")[:10] <= end.isoformat()]
        for i, k in enumerate(sorted(in_range)):
            base = CACHE / "activities" / k
            try:
                if details and not base.with_suffix(".json").exists():
                    _write(base.with_suffix(".json"), self._call("get_activity", k))
                    _write(base.with_suffix(".workouts.json"), self._call("get_activity_workouts", k))
                if fit and not base.with_suffix(".fit").exists():
                    self._save_fit(k, self._call("download_activity", k))
            except GarminError as e:
                log(f"activity {k}: {e}")
            if i % 25 == 0:
                log(f"details/FIT: {i + 1}/{len(in_range)}")
        return dict(activities=len(in_range), new=n_new)

    def _save_fit(self, key, blob: bytes):
        path = CACHE / "activities" / f"{key}.fit"
        if blob[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                fits = [n for n in z.namelist() if n.lower().endswith(".fit")]
                if fits:
                    path.write_bytes(z.read(fits[0]))
                    return
                blob = z.read(z.namelist()[0])
        path.write_bytes(blob)

    # ---- health -------------------------------------------------------------
    def sync_range(self, name: str, start: dt.date, end: dt.date, log=print):
        method, chunk = RANGE_DATASETS[name]
        folder, done = CACHE / "health" / name, 0
        recent = dt.date.today() - dt.timedelta(days=3)
        for s, e in daterange_chunks(start, end, chunk):
            path = folder / f"{s}_{e}.json"
            if path.exists() and e < recent:
                continue
            done += self._fetch_range(method, s, e, folder, log)
        return dict(dataset=name, chunks_fetched=done)

    def _fetch_range(self, method, s, e, folder, log) -> int:
        """Fetch [s, e]; if Garmin rejects the span, split it in half and retry."""
        try:
            _write(folder / f"{s}_{e}.json", self._call(method, s, e))
            return 1
        except GarminError as err:
            if err.status in (400, 413, 422) and s < e:
                mid = s + (e - s) // 2
                return (self._fetch_range(method, s, mid, folder, log)
                        + self._fetch_range(method, mid + dt.timedelta(days=1), e, folder, log))
            log(f"{method} {s}..{e}: {err}")
            return 0

    def sync_days(self, name: str, start: dt.date, end: dt.date, log=print):
        method, folder, n = DAY_DATASETS[name], CACHE / "health" / name, 0
        recent = dt.date.today() - dt.timedelta(days=3)
        for s, _ in daterange_chunks(start, end, 1):
            path = folder / f"{s}.json"
            if path.exists() and s < recent:
                continue
            try:
                _write(path, self._call(method, s))
                n += 1
            except GarminError as err:
                log(f"{method} {s}: {err}")
        return dict(dataset=name, days_fetched=n)

    def sync_profile(self):
        for name, method in PROFILE.items():
            _write(CACHE / "profile" / f"{name}.json", self._call(method))
        return dict(profile=list(PROFILE))

    def sync_all(self, start, end, include_daily=(), fit=True, log=print):
        report = [self.sync_profile(), self.sync_activities(start, end, fit=fit, log=log)]
        for name in RANGE_DATASETS:
            report.append(self.sync_range(name, start, end, log=log))
        for name in include_daily:
            report.append(self.sync_days(name, start, end, log=log))
        return report

    # ---- reading back ---------------------------------------------------------
    def cached(self, name: str):
        """All cached JSON for a dataset, concatenated in date order."""
        folder = CACHE / "health" / name
        return [json.loads(p.read_text()) for p in sorted(folder.glob("*.json"))] if folder.exists() else []

    def status(self):
        out = {"activities": len(self.index()),
               "fit_files": len(list((CACHE / "activities").glob("*.fit")))}
        for d in list(RANGE_DATASETS) + list(DAY_DATASETS):
            files = sorted((CACHE / "health" / d).glob("*.json"))
            if files:
                out[d] = dict(files=len(files), first=files[0].stem[:10], last=files[-1].stem[-10:])
        return out
