"""Backfill the Garmin cache from the command line, then rebuild the recap.

    python3 -m garmin_mcp.sync                       # from blocks.json's window start (or your first activity) to today
    python3 -m garmin_mcp.sync --start 2025-02-01 --end 2026-02-15 --daily hrv_detail sleep_detail
"""
import argparse
import datetime as dt
import json

from .api import ROOT
from .store import DAY_DATASETS, Store


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--start", type=dt.date.fromisoformat)
    ap.add_argument("--end", type=dt.date.fromisoformat, default=dt.date.today())
    ap.add_argument("--daily", nargs="*", default=[], choices=list(DAY_DATASETS),
                    help="per-day datasets to include (one request per day each)")
    ap.add_argument("--no-fit", action="store_true", help="skip downloading original FIT files")
    ap.add_argument("--no-build", action="store_true", help="don't rebuild report/recap.html afterwards")
    a = ap.parse_args()
    if a.start is None:
        cfg = json.loads((ROOT / "blocks.json").read_text())
        first = cfg["window"][0]
        if first in (None, "", "first"):
            # Your first Garmin activity, if already cached; else look back 5 years.
            from recap.data import load_cache
            acts = load_cache()
            a.start = min(x.date for x in acts) if acts else a.end - dt.timedelta(days=5 * 365)
        else:
            a.start = dt.date.fromisoformat(first)
    start = a.start

    store = Store()
    report = store.sync_all(start, a.end, include_daily=a.daily, fit=not a.no_fit)
    print(json.dumps({"report": report, "cache": store.status()}, indent=1))
    if not a.no_build:
        from recap.build import build
        print(json.dumps(build(since=str(start), until=str(a.end)), indent=1))


if __name__ == "__main__":
    main()
