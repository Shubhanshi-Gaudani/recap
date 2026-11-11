# recap

A year-in-review for Garmin training data: an **MCP server for the Garmin Connect Personal API**
plus a **visual recap** that compares training blocks.

Plain Python 3.12+, standard library only. Nothing to install.

## Quick start

```sh
git clone <this repo> && cd recap
python3 start.py
open report/recap.html
```

`start.py` walks you through everything:

1. **Garmin key.** It asks for your Personal API key (see below) and saves it to `.garmin/api-key`, which is gitignored.
2. **Download.** It syncs your activities, FIT files and daily health data into `cache/`.
3. **Races.** It lists runs that look like races (unusually fast for their distance compared with
   your runs around then). You tick the real ones, add any it missed by date, and name them.
4. **Blocks.** You pick which races were goal races and when each build-up started.
5. **Build.** It writes `blocks.json` and `report/recap.html`.

Run it again any time to sync new runs or change races; your previous answers are the defaults.
`python3 start.py --no-sync` skips the download.

```
garmin_mcp/   MCP server (stdio) over Garmin's official Personal API
  api.py        auth (API key → 24h bearer token, cached), every documented endpoint
  store.py      local cache + incremental backfill ("sync")
  server.py     29 MCP tools
  sync.py       same backfill from the command line
recap/        analysis + visuals
  fit.py        dependency-free FIT decoder (per-second records + laps)
  data.py       one Activity model, from the Garmin API cache
  reps.py       interval reps (200 m … 1 mile) found in each run's laps
  workouts.py   names every run from Garmin data: inferred workout + place
  races.py      finds runs that look like races
  setup.py      the guided setup behind start.py
  analysis.py   block stats, race splits, Riegel predictions, efficiency, mileage jumps
  health.py     HRV / resting HR / sleep / Body Battery / stress from the cache
  build.py      → report/recap.html (single self-contained page) + report/metrics.json
start.py      guided setup: key → sync → pick races and blocks → build
blocks.json   your training blocks, races and window (written by start.py; gitignored)
blocks.example.json  what blocks.json looks like
```

## 1. Connect Garmin

1. Create a Personal API key in Garmin Connect: **Settings → Personal API Key**
   (<https://connect.garmin.com/app/settings/personalApiToken>). Garmin shows it once.
2. Save it where the server looks (gitignored):
   ```sh
   mkdir -p .garmin && pbpaste > .garmin/api-key && chmod 600 .garmin/api-key
   ```
   or export `GARMIN_PERSONAL_API_KEY`.

## 2. Use it from Claude Code

`.mcp.json` registers the server for this project; open Claude Code in this folder and approve
`garmin`. Or register it yourself:

```sh
claude mcp add garmin -- python3 -m garmin_mcp
```

Then ask things like *"sync my last year of Garmin data and build the recap"* or
*"how did my HRV change during the marathon block?"*.

### Tools

| Group | Tools |
|---|---|
| Setup | `garmin_status`, `garmin_sync` (backfills everything below into `cache/`) |
| Activities | `garmin_search_activities`, `garmin_get_activity`, `garmin_get_activity_workouts` (laps / sets), `garmin_download_activity_fit`, `garmin_activity_stream` (per-second HR, pace, cadence, altitude) |
| Daily health, by date range | `garmin_daily_summaries`, `garmin_sleep`, `garmin_hrv`, `garmin_body_battery`, `garmin_stress`, `garmin_steps`, `garmin_hydration`, `garmin_weight`, `garmin_weight_goals`, `garmin_wellness_activities` |
| Daily health, one day | `garmin_sleep_detail`, `garmin_hrv_detail`, `garmin_heart_rate`, `garmin_spo2`, `garmin_fitness_age`, `garmin_food_logs`, `garmin_menstrual` |
| Profile | `garmin_profile_settings`, `garmin_heart_rate_zones` |
| Anything else | `garmin_get` (GET any Personal API path) |
| Recap | `recap_find_races`, `recap_build` |

`garmin_sync` is incremental and splits long date ranges into chunks; if Garmin rejects a span it
halves it and retries. Per-day datasets cost one request per day, so they're opt-in.

## 3. Build the recap

```sh
python3 -m garmin_mcp.sync          # pulls from Garmin, then builds
python3 -m recap.build              # rebuild from the cache, offline
open report/recap.html
```

Pick any range; the end defaults to today:

```sh
python3 -m recap.build --since 2026-01-01
python3 -m recap.build --since 2025-07-01 --until 2025-10-31
```

Blocks whose race falls outside the range drop out, and the page adapts.

### Blocks

`start.py` writes this file for you; edit it by hand if you prefer. Each block is a date range ending
in a goal race:

```json
{
  "key": "B5",
  "name": "Block 5 · Boston Marathon",
  "start": "2027-01-05",
  "end": "2027-04-19",
  "race": {"name": "Boston Marathon", "distance_mi": 26.219, "date": "2027-04-19"}
}
```

Each race is the longest run on its `date`. `races` lists your other races (tune-ups, fun runs).
If the watch kept recording past the finish (a warm-up, a walk home), the race is timed as the
fastest stretch of its distance. `window` is `["first", "today"]` by default, meaning from your
first Garmin activity to now. `athlete` is the name in the page title.

### Garmin data only

Everything comes from the Garmin Personal API. Activity titles and notes aren't used: each run is
named from its date, the workout inferred from its laps (intervals, tempo, long run, easy run,
race) and Garmin's location, e.g. *5 × 800 m · New York*.

### Health data

`garmin_sync` pulls daily summaries, sleep, HRV, Body Battery, stress, steps, hydration and weight
for the whole range by default; the Recovery section charts them. For per-night or per-minute
detail, add per-day datasets (one request per day each):

```sh
python3 -m garmin_mcp.sync --daily sleep_detail hrv_detail heart_rate spo2
```

### What's in the page

1. **The year, one square per day.** A calendar heatmap with block bars and race days.
2. **Blocks aligned to race day.** Weekly miles, counted back from each race, plus a side-by-side table.
3. **Every run, drawn on top of each other.** All GPS tracks, filterable by block, plus thumbnails for runs away from home.
4. **HR ridgeline.** Each month's running heart-rate distribution, measured against marathon race HR.
5. **The marathon, mile by mile.** Pace against goal pace, with heart rate per mile.
6. **Every race.** All races on one scale, as the marathon time each one predicts.
7. **Efficiency factor.** Every run, with the monthly median and the summer heat dip.
8. **Your repeats, over time.** Average 200 m / 400 m / 600 m / 800 m / 1 km / 1 mile rep per session.
9. **Inside a workout.** Every lap of any interval session, with heart rate.
10. **Mileage jumps.** Acute:chronic mileage ratio and the biggest jumps.
11. **When you run.** A weekday × hour punchcard.
12. **Recovery.** HRV, resting HR, sleep, Body Battery and stress as small multiples (needs Garmin API data).

Every chart has hover tooltips, light and dark themes, and a data table where it matters.

## Privacy

Credentials (`.garmin/`), the API cache (`cache/`), FIT files and the generated report (which embeds
GPS tracks) are all gitignored. Garmin's Personal API terms cover personal, non-commercial use.
