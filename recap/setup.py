"""Guided setup: Garmin key → sync → pick your races and blocks → build the recap.

    python3 start.py            (same as: python3 -m recap.setup)
    python3 start.py --no-sync  reuse what's already in cache/

Writes blocks.json (your races and training blocks; gitignored) and report/recap.html.
Run it again any time to add races or re-sync.
"""
import argparse
import datetime as dt
import getpass
import json
import os
import shutil
import sys

from garmin_mcp.api import KEY_FILE, Client, GarminError
from garmin_mcp.store import Store

from .data import ROOT, load_cache
from .races import DISTANCE_MI, candidates

CONFIG = ROOT / "blocks.json"
BLOCK_WEEKS = {"Marathon": 18, "Half": 12}  # default block length before a goal race
KEY_URL = "https://connect.garmin.com/app/settings/personalApiToken"


# ---- small prompt helpers ---------------------------------------------------
def ask(q, default=""):
    a = input(f"{q}{f' [{default}]' if default else ''}: ").strip()
    return a or default


def yes(q, default=True):
    a = input(f"{q} [{'Y/n' if default else 'y/N'}]: ").strip().lower()
    return default if not a else a.startswith("y")


def pick(q, n, default):
    """Comma-separated numbers 1..n (ranges like 3-5 ok). Returns 0-based indices."""
    while True:
        raw = ask(q, ",".join(str(i + 1) for i in default) or "none")
        if raw.lower() == "none":
            return []
        try:
            out = []
            for part in raw.replace(" ", "").split(","):
                lo, _, hi = part.partition("-")
                out += range(int(lo) - 1, int(hi or lo))
            if all(0 <= i < n for i in out):
                return sorted(set(out))
        except ValueError:
            pass
        print(f"  Numbers between 1 and {n}, e.g. 1,3,5-7.")


def date_in(q, default):
    while True:
        a = ask(q, default)
        try:
            return dt.date.fromisoformat(a)
        except ValueError:
            print("  Use YYYY-MM-DD.")


def hr(title):
    print(f"\n── {title} " + "─" * max(0, 60 - len(title)))


# ---- steps ------------------------------------------------------------------
def step_key():
    hr("1. Garmin Personal API key")
    if os.environ.get("GARMIN_PERSONAL_API_KEY") or (KEY_FILE.exists() and KEY_FILE.read_text().strip()):
        print("Key found.")
    else:
        print(f"Create one in Garmin Connect → Settings → Personal API Key:\n  {KEY_URL}\n"
              "Garmin shows it once. Paste it below (it isn't echoed). It's saved to .garmin/api-key,\n"
              "which is gitignored and never leaves this machine.")
        key = getpass.getpass("API key: ").strip()
        if not key:
            sys.exit("No key entered.")
        KEY_FILE.parent.mkdir(exist_ok=True)
        KEY_FILE.write_text(key + "\n")
        KEY_FILE.chmod(0o600)
    try:
        Client().token()
        print("Connected to Garmin.")
    except GarminError as e:
        sys.exit(f"Garmin rejected the key: {e}\nCheck it, or delete .garmin/api-key and run again.")


def step_sync(skip):
    hr("2. Download your data")
    acts = load_cache()
    if acts:
        print(f"Already cached: {len(acts)} activities, {min(a.date for a in acts)} → {max(a.date for a in acts)}.")
    if skip or (acts and not yes("Sync again to pick up new activities?", True)):
        return
    today = dt.date.today()
    default = (min(a.date for a in acts) if acts else today - dt.timedelta(days=3 * 365)).isoformat()
    start = date_in("Fetch from (your Garmin start date, or earlier — empty days are skipped)", default)
    print("Downloading activities, FIT files and daily health data. First run can take a few minutes…")
    Store().sync_all(start, today, fit=True, log=lambda *a, **k: print(" ", *a))


def step_races(old):
    hr("3. Your races")
    found = candidates()
    if not found:
        print("No outdoor runs near race distances found.")
    old_dates = {r["date"]: r for r in old.get("races", [])} | {
        b["race"]["date"]: b["race"] for b in old.get("blocks", [])}
    print("Runs that look like race efforts (fastest stretch of the distance, vs. your runs around then):\n")
    print(f"  {'#':>3}  {'date':<15} {'dist':<9} {'time':>8} {'pace':>6} {'HR':>4}  place")
    for i, c in enumerate(found, 1):
        star = "*" if c["suggested"] or c["date"] in old_dates else " "
        print(f" {star}{i:>3}  {c['weekday']} {c['date']:<11} {c['distance']:<9} {c['time']:>8} "
              f"{c['pace']:>6} {c['avg_hr'] or '':>4}  {c['place']}")
    default = [i for i, c in enumerate(found) if c["suggested"] or c["date"] in old_dates]
    print("\n* = suggested. Pick the ones that were races; you can add others by date next.")
    chosen = [found[i] for i in pick("Races", len(found), default)]
    while extra := ask("Another race not listed? Date YYYY-MM-DD (Enter to continue)", ""):
        try:
            dt.date.fromisoformat(extra)
        except ValueError:
            print("  Use YYYY-MM-DD.")
            continue
        chosen.append(dict(id=None, date=extra, distance="", time="", weekday="", place=""))

    races = []
    print("\nName each race and confirm its distance (5K, 5M, 10K, 10M, Half, Marathon, or miles).")
    for c in sorted(chosen, key=lambda c: c["date"]):
        prev = old_dates.get(c["date"], {})
        label = f"{c['date']} {c['distance']} {c['time']}".strip()
        name = ask(f"  {label} — name", prev.get("name", ""))
        while not name:
            name = ask(f"  {label} — name (required)")
        dist = ask("    distance", next((k for k, v in DISTANCE_MI.items() if v == prev.get("distance_mi")),
                                        c["distance"] or "Half"))
        miles = DISTANCE_MI.get(dist) or DISTANCE_MI.get(dist.capitalize())
        if miles is None:
            try:
                miles = float(dist)
            except ValueError:
                miles = DISTANCE_MI["Half"]
                print("    Didn't recognise that; using Half.")
        race = dict(name=name, date=c["date"], distance_mi=miles)
        if c.get("id") or prev.get("id"):
            race["id"] = c.get("id") or prev["id"]  # the exact Garmin activity
        races.append(race)
    return races


def step_blocks(races, old):
    hr("4. Training blocks")
    if not races:
        return []
    print("A block is the build-up to a goal race. Which races were goal races?\n")
    for i, r in enumerate(races, 1):
        print(f"  {i:>3}  {r['date']}  {r['name']}")
    old_goals = {b["race"]["date"]: b for b in old.get("blocks", [])}
    default = [i for i, r in enumerate(races) if r["date"] in old_goals or r["distance_mi"] > 20]
    goals = [races[i] for i in pick("Goal races", len(races), default)]
    blocks = []
    for n, r in enumerate(sorted(goals, key=lambda r: r["date"]), 1):
        kind = "Marathon" if r["distance_mi"] > 20 else "Half" if r["distance_mi"] > 13 else ""
        race_day = dt.date.fromisoformat(r["date"])
        prev = old_goals.get(r["date"])
        guess = prev["start"] if prev else (race_day - dt.timedelta(weeks=BLOCK_WEEKS.get(kind, 8))).isoformat()
        start = date_in(f"  {r['name']}: block started", guess)
        blocks.append(dict(key=f"B{n}", name=prev["name"] if prev else f"Block {n} · {r['name']}",
                           start=start.isoformat(),
                           end=r["date"], race=r))
    return blocks


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-sync", action="store_true", help="skip the Garmin download; use cache/")
    a = ap.parse_args()
    print("recap: a running recap from your Garmin data. Ctrl-C any time; nothing is saved until the end.")
    old = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}

    if not a.no_sync:
        step_key()
    step_sync(a.no_sync)
    if not load_cache():
        sys.exit("No activities in cache/. Run again without --no-sync.")
    hr("Your name")
    name = ask("First name for the title (\"<name>'s running recap\")", old.get("athlete", ""))
    races = step_races(old)
    blocks = step_blocks(races, old)
    goal_dates = {b["race"]["date"] for b in blocks}

    cfg = dict(athlete=name, timezone=old.get("timezone", "America/New_York"),
               window=old.get("window", ["first", "today"]), blocks=blocks,
               races=[r for r in races if r["date"] not in goal_dates])
    if CONFIG.exists():
        shutil.copy(CONFIG, CONFIG.with_suffix(".json.bak"))
    CONFIG.write_text(json.dumps(cfg, indent=2) + "\n")
    print(f"\nSaved {CONFIG.name}: {len(blocks)} blocks, {len(races)} races.")

    hr("5. Build")
    from .build import build
    out = build()
    print(f"Done: {out['totals']['runs']} runs, {out['totals']['miles']:,} miles.\n"
          f"Open it with:  open {os.path.relpath(out['report'])}")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        sys.exit("\nStopped. Nothing was saved.")
