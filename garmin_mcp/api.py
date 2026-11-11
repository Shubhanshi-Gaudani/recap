"""Garmin Connect Personal API client — standard library only.

Mirrors Garmin's official client (github.com/garmin/connect-personal-api-python):
the Personal API key is POSTed to the token endpoint for a 24h JWT bearer token,
which is cached and reused until it is within an hour of expiry.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("GARMIN_PERSONAL_API_BASE_URL", "https://personal.connectapi.garmin.com").rstrip("/")
TOKEN_PATH = "/personalapitoken-service/personal-api/personal-api-key/token"
ROOT = Path(__file__).resolve().parent.parent
CRED_DIR = Path(os.environ.get("RECAP_GARMIN_DIR", ROOT / ".garmin"))
KEY_FILE = CRED_DIR / "api-key"
TOKEN_FILE = CRED_DIR / "token.json"
REFRESH_WINDOW_S = 3600
USER_AGENT = "recap-garmin-mcp/0.1"


class GarminError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def resolve_key() -> str:
    if key := os.environ.get("GARMIN_PERSONAL_API_KEY", "").strip():
        return key
    if KEY_FILE.exists():
        if key := KEY_FILE.read_text().strip():
            return key
    raise GarminError(
        "No Garmin Personal API key. Create one at "
        "https://connect.garmin.com/app/settings/personalApiToken and either set "
        f"GARMIN_PERSONAL_API_KEY or save it to {KEY_FILE} (chmod 600).")


def _jwt_exp(token: str) -> float:
    payload = token.split(".")[1]
    return float(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"])


def iso(d) -> str:
    return d.isoformat() if isinstance(d, (dt.date, dt.datetime)) else str(d)


class Client:
    def __init__(self, timeout=30.0):
        self.timeout = timeout
        self._token = None

    # ---- auth ---------------------------------------------------------------
    def token(self, force=False) -> str:
        key = resolve_key()
        fp = hashlib.sha256(f"{BASE_URL}\0{key}".encode()).hexdigest()
        if not force and self._token and self._token[1] - time.time() > REFRESH_WINDOW_S:
            return self._token[0]
        if not force and TOKEN_FILE.exists():
            try:
                c = json.loads(TOKEN_FILE.read_text())
                if c["fingerprint"] == fp and c["expires_at"] - time.time() > REFRESH_WINDOW_S:
                    self._token = (c["access_token"], c["expires_at"])
                    return c["access_token"]
            except (ValueError, KeyError):
                pass
        req = urllib.request.Request(BASE_URL + TOKEN_PATH, method="POST", data=b"",
                                     headers={"Accept": "*/*", "Authorization": f"ApiKey {key}",
                                              "User-Agent": USER_AGENT})
        body = self._send(req).decode().strip()
        exp = _jwt_exp(body)
        CRED_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump({"fingerprint": fp, "access_token": body, "expires_at": exp}, f)
        self._token = (body, exp)
        return body

    # ---- transport ----------------------------------------------------------
    def _send(self, req, retries=4) -> bytes:
        for attempt in range(retries):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")[:500]
                if e.code in (429, 500, 502, 503, 504) and attempt < retries - 1:
                    wait = float(e.headers.get("Retry-After") or 2 ** attempt * 2)
                    time.sleep(min(wait, 60))
                    continue
                raise GarminError(f"HTTP {e.code} {req.get_method()} {req.full_url}: {text}", e.code)
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                raise GarminError(f"Network error for {req.full_url}: {e.reason}")
        raise GarminError("unreachable")

    def raw(self, path: str, params: dict | None = None, accept="application/json") -> bytes:
        if not path.startswith("/"):
            raise GarminError("path must start with /")
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for refresh in (False, True):
            req = urllib.request.Request(url, headers={
                "Accept": accept, "Authorization": f"Bearer {self.token(force=refresh)}",
                "User-Agent": USER_AGENT})
            try:
                return self._send(req)
            except GarminError as e:
                if e.status == 401 and not refresh:
                    continue
                raise
        raise GarminError("unreachable")

    def get(self, path: str, params: dict | None = None):
        body = self.raw(path, params)
        return json.loads(body) if body.strip() else None

    # ---- activities ---------------------------------------------------------
    def search_activities(self, start=0, limit=20, start_date=None, end_date=None, activity_type=None):
        p = {"start": start, "limit": limit}
        if start_date: p["startDate"] = iso(start_date)
        if end_date: p["endDate"] = iso(end_date)
        if activity_type: p["activityType"] = activity_type
        return self.get("/activitylist-service/activities/search/activities", p)

    def get_activity(self, activity_id):
        return self.get(f"/activity-service/activity/{int(activity_id)}")

    def get_activity_workouts(self, activity_id):
        return self.get(f"/activity-service/activity/{int(activity_id)}/workouts")

    def download_activity(self, activity_id) -> bytes:
        return self.raw(f"/download-service/files/activity/{int(activity_id)}",
                        accept="application/zip, application/x-zip-compressed, application/octet-stream")

    # ---- daily health / wellness (ranges) -----------------------------------
    def daily_summaries(self, s, e):
        return self.get("/usersummary-service/v2/dailysummaries", {"startDate": iso(s), "endDate": iso(e)})

    def daily_stress(self, s, e):
        return self.get(f"/usersummary-service/stats/stress/daily/{iso(s)}/{iso(e)}")

    def daily_steps(self, s, e):
        return self.get(f"/usersummary-service/stats/steps/daily/{iso(s)}/{iso(e)}")

    def daily_hydration(self, s, e):
        return self.get(f"/usersummary-service/stats/hydration/daily/{iso(s)}/{iso(e)}")

    def sleep_summaries(self, s, e):
        return self.get("/sleep-service/sleep/dailySleepSummariesByDate", {"startDate": iso(s), "endDate": iso(e)})

    def hrv_summaries(self, s, e):
        return self.get(f"/hrv-service/hrv/daily/{iso(s)}/{iso(e)}")

    def body_battery(self, s, e, chunks=None):
        p = {"startDate": iso(s), "endDate": iso(e)}
        if chunks: p["chunks"] = chunks
        return self.get("/wellness-service/wellness/bodyBattery/reports/daily", p)

    def weight(self, s, e):
        return self.get(f"/weight-service/weight/range/{iso(s)}/{iso(e)}", {"includeAll": "true"})

    def weight_goals(self, s, e):
        return self.get(f"/goal-service/goal/user/effective/weightgoal/{iso(s)}/{iso(e)}")

    def wellness_activity_summaries(self, s, e):
        return self.get(f"/wellnessactivity-service/activity/summary/range/{iso(s)}/{iso(e)}")

    # ---- daily health (single day) ------------------------------------------
    def sleep(self, d):
        return self.get("/sleep-service/sleep/dailySleep", {"date": iso(d)})

    def hrv(self, d):
        return self.get(f"/hrv-service/hrv/{iso(d)}")

    def heart_rate(self, d):
        return self.get("/wellness-service/wellness/dailyHeartRate", {"date": iso(d)})

    def spo2(self, d):
        return self.get(f"/wellness-service/wellness/daily/spo2acclimation/{iso(d)}")

    def fitness_age(self, d):
        return self.get(f"/fitnessage-service/fitnessage/v2/{iso(d)}")

    def food_logs(self, d):
        return self.get(f"/nutrition-service/food/logs/{iso(d)}")

    def menstrual_summary(self, d):
        return self.get(f"/periodichealth-service/menstrualcycle/summary/{iso(d)}")

    def menstrual_day_view(self, d, today=None):
        return self.get(f"/periodichealth-service/menstrualcycle/dayview/{iso(d)}",
                        {"todayDate": iso(today or dt.date.today())})

    # ---- profile -------------------------------------------------------------
    def profile_settings(self):
        return self.get("/userprofile-service/userprofile/settings")

    def heart_rate_zones(self):
        return self.get("/biometric-service/heartRateZones/v2")
