"""MCP server (stdio, JSON-RPC 2.0) exposing the whole Garmin Connect Personal API.

Run:  python3 -m garmin_mcp
Claude Code:  claude mcp add garmin -- python3 -m garmin_mcp   (from the repo root)
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import traceback

from . import api
from .store import CACHE, DAY_DATASETS, RANGE_DATASETS, Store

SERVER_INFO = {"name": "recap-garmin", "version": "0.1.0"}
DEFAULT_PROTOCOL = "2025-06-18"
MAX_CHARS = 60_000  # keep tool results inside a sensible context budget

store = Store()

DATE = {"type": "string", "description": "YYYY-MM-DD"}
RANGE_SCHEMA = {"type": "object", "properties": {"start_date": DATE, "end_date": DATE},
                "required": ["start_date", "end_date"]}
DAY_SCHEMA = {"type": "object", "properties": {"date": DATE}, "required": ["date"]}
ID_SCHEMA = {"type": "object", "properties": {"activity_id": {"type": "string"}}, "required": ["activity_id"]}
NONE_SCHEMA = {"type": "object", "properties": {}}

RANGE_DOCS = {
    "daily_summaries": "Daily summary per day: steps, calories, resting HR, stress, intensity minutes, floors.",
    "sleep": "Nightly sleep summaries: duration, stages (deep/light/REM/awake), sleep score.",
    "hrv": "Overnight HRV summaries: nightly average, 5-min high, weekly average, baseline, status.",
    "body_battery": "Daily Body Battery reports: charged/drained, highs and lows.",
    "stress": "Daily average and max stress.",
    "steps": "Daily step totals and goals.",
    "hydration": "Daily hydration intake and goal.",
    "weight": "Weight / body-composition measurements.",
    "weight_goals": "Effective weight goals over the range.",
    "wellness_activities": "All-day wellness activity summaries (intensity, move minutes).",
}
DAY_DOCS = {
    "sleep_detail": "Full sleep record for one night incl. stage timeline, SpO2/respiration if recorded.",
    "hrv_detail": "One night's HRV readings at 5-minute resolution.",
    "heart_rate": "All-day heart rate samples for one day, plus resting HR.",
    "spo2": "Pulse-ox (SpO2) acclimation readings for one day.",
    "fitness_age": "Garmin fitness age as of a date.",
    "food_logs": "Food/nutrition log for one day.",
    "menstrual": "Menstrual cycle summary as of a date.",
}


def _d(s):
    return dt.date.fromisoformat(s)


def _clip(obj):
    text = obj if isinstance(obj, str) else json.dumps(obj, default=str)
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS] + f"\n…[truncated {len(text) - MAX_CHARS} chars — narrow the date range]"
    return text


# ---- tool implementations ---------------------------------------------------
def t_status(_):
    try:
        api.resolve_key()
        key = "configured"
    except api.GarminError as e:
        key = str(e)
    return {"api_key": key, "cache_dir": str(CACHE), "cache": store.status()}


def t_sync(a):
    end = _d(a.get("end_date") or dt.date.today().isoformat())
    start = _d(a.get("start_date") or (end - dt.timedelta(days=365)).isoformat())
    logs = []
    report = store.sync_all(start, end, include_daily=a.get("include_daily", []),
                            fit=a.get("fit", True), log=logs.append)
    return {"range": [str(start), str(end)], "report": report, "log_tail": logs[-30:], "cache": store.status()}


def t_search(a):
    return store.c.search_activities(start=a.get("start", 0), limit=a.get("limit", 50),
                                     start_date=a.get("start_date"), end_date=a.get("end_date"),
                                     activity_type=a.get("activity_type"))


def t_fit(a):
    k = str(int(a["activity_id"]))
    path = CACHE / "activities" / f"{k}.fit"
    if not path.exists():
        store._save_fit(k, store.c.download_activity(k))
    return {"path": str(path), "bytes": path.stat().st_size}


def t_stream(a):
    """Down-sampled per-second series from the activity's FIT file."""
    from recap.fit import read_records
    t_fit(a)
    recs = read_records(CACHE / "activities" / f"{int(a['activity_id'])}.fit")
    every = max(1, int(a.get("every_seconds", 30)))
    t0 = recs[0]["ts"] if recs else 0
    keep = ("dist", "hr", "speed", "cad", "alt", "temp")
    rows = [[int(r["ts"] - t0)] + [round(r[k], 2) if k in r else None for k in keep]
            for i, r in enumerate(recs) if i % every == 0]
    return {"columns": ["t_s", "dist_m", "hr", "speed_mps", "cadence", "alt_m", "temp_c"], "rows": rows}


def t_get(a):
    return store.c.get(a["path"], a.get("params") or None)


def t_recap(a):
    from recap.build import build
    return build(config=a.get("config", "blocks.json"),
                 since=a.get("since"), until=a.get("until"))


TOOLS = {
    "garmin_status": ("Is the API key configured, and what's already cached locally?", NONE_SCHEMA, t_status),
    "garmin_sync": (
        "Backfill everything into the local cache: activity list, details, laps/workouts, original FIT "
        "files, and every range-based health dataset. Incremental — reruns only fetch what's missing. "
        "Per-day datasets (one request per day) are opt-in via include_daily.",
        {"type": "object", "properties": {
            "start_date": DATE, "end_date": DATE,
            "fit": {"type": "boolean", "description": "Download FIT files (default true)"},
            "include_daily": {"type": "array", "items": {"type": "string", "enum": list(DAY_DATASETS)}}}},
        t_sync),
    "garmin_search_activities": (
        "Search activities (paged). Returns Garmin activity summaries: type, distance, duration, HR, "
        "training effect, training load, VO2max, splits summary and more.",
        {"type": "object", "properties": {
            "start_date": DATE, "end_date": DATE, "activity_type": {"type": "string"},
            "start": {"type": "integer"}, "limit": {"type": "integer"}}},
        t_search),
    "garmin_get_activity": ("Full detail for one activity.", ID_SCHEMA,
                            lambda a: store.c.get_activity(a["activity_id"])),
    "garmin_get_activity_workouts": ("Laps / structured-workout steps / strength sets for one activity.",
                                     ID_SCHEMA, lambda a: store.c.get_activity_workouts(a["activity_id"])),
    "garmin_download_activity_fit": ("Download an activity's original FIT file into the cache.", ID_SCHEMA, t_fit),
    "garmin_activity_stream": (
        "Per-second data (distance, HR, speed, cadence, altitude, temperature) from an activity's FIT file, "
        "down-sampled to every N seconds.",
        {"type": "object", "properties": {"activity_id": {"type": "string"},
                                          "every_seconds": {"type": "integer"}}, "required": ["activity_id"]},
        t_stream),
    "garmin_profile_settings": ("User profile settings (units, birth year, weight, etc.).", NONE_SCHEMA,
                                lambda a: store.c.profile_settings()),
    "garmin_heart_rate_zones": ("Configured heart-rate zones (max HR, LTHR, zone floors) per sport.", NONE_SCHEMA,
                                lambda a: store.c.heart_rate_zones()),
    "garmin_get": (
        "Escape hatch: GET any other Personal API path, e.g. /hrv-service/hrv/2025-10-26.",
        {"type": "object", "properties": {"path": {"type": "string"}, "params": {"type": "object"}},
         "required": ["path"]},
        t_get),
    "recap_find_races": (
        "Runs that look like races (unusually fast for their distance vs. the runs around them), "
        "with date, distance, best-effort time, pace, HR and place. Use it to help fill blocks.json.",
        {"type": "object", "properties": {"limit": {"type": "integer", "default": 30}}},
        lambda a: __import__("recap.races", fromlist=["candidates"]).candidates(limit=a.get("limit", 30))),
    "recap_build": (
        "Run the recap analysis and render the interactive visual report (report/recap.html). "
        "Uses the Garmin API cache only.",
        {"type": "object", "properties": {"config": {"type": "string"},
                                          "since": {**DATE, "description": "Start of the recap (YYYY-MM-DD)"},
                                          "until": {"type": "string", "description": "YYYY-MM-DD or 'today' (default)"}}},
        t_recap),
}
for _name, (_method, _) in RANGE_DATASETS.items():
    TOOLS[f"garmin_{_name}"] = (RANGE_DOCS[_name] + " Inclusive date range.", RANGE_SCHEMA,
                                (lambda m: lambda a: getattr(store.c, m)(a["start_date"], a["end_date"]))(_method))
for _name, _method in DAY_DATASETS.items():
    TOOLS[f"garmin_{_name}"] = (DAY_DOCS[_name], DAY_SCHEMA,
                                (lambda m: lambda a: getattr(store.c, m)(a["date"]))(_method))


# ---- JSON-RPC plumbing ------------------------------------------------------
def handle(msg):
    method, mid, params = msg.get("method"), msg.get("id"), msg.get("params") or {}
    if mid is None:  # notification
        return None
    if method == "initialize":
        result = {"protocolVersion": params.get("protocolVersion", DEFAULT_PROTOCOL),
                  "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO,
                  "instructions": "Garmin Connect Personal API. Call garmin_status first; "
                                  "garmin_sync backfills a date range into a local cache; "
                                  "recap_build renders the visual year-in-review."}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (d, s, _) in TOOLS.items()]}
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {name}"}}
        try:
            out = TOOLS[name][2](params.get("arguments") or {})
            result = {"content": [{"type": "text", "text": _clip(out)}], "isError": False}
        except Exception as e:  # tool errors are results, not protocol errors
            traceback.print_exc(file=sys.stderr)
            result = {"content": [{"type": "text", "text": f"{type(e).__name__}: {e}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        reply = handle(msg)
        if reply is not None:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
