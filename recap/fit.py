"""Minimal, dependency-free FIT decoder.

Pulls two message types:
- `record` (global #20): per-second timestamp, position, distance, speed, heart rate,
  cadence, altitude and temperature
- `lap` (global #19): every lap as recorded on the watch: manual/auto/workout-step laps
  with distance, time, pace, heart rate and whether it was a work or rest step
"""
import gzip
import struct

RECORD, LAP = 20, 19
FIELDS = {
    RECORD: {253: "ts", 0: "lat", 1: "lon", 5: "dist", 6: "speed", 73: "speed", 3: "hr", 4: "cad",
             2: "alt", 78: "alt", 13: "temp"},
    LAP: {253: "ts", 2: "start", 7: "elapsed", 8: "timer", 9: "dist", 13: "speed", 110: "speed",
          15: "hr", 16: "max_hr", 17: "cad", 23: "intensity", 24: "trigger", 71: "step"},
}
SEMICIRCLE = 180 / 2 ** 31
# key -> (scale, offset): value = raw / scale - offset
SCALE = {"dist": (100.0, 0), "speed": (1000.0, 0), "alt": (5.0, 500), "lat": (1 / SEMICIRCLE, 0),
         "lon": (1 / SEMICIRCLE, 0), "elapsed": (1000.0, 0), "timer": (1000.0, 0)}
FMT = {1: "b", 2: "B", 3: "h", 4: "H", 5: "i", 6: "I", 7: "s", 8: "f", 9: "d",
       10: "B", 11: "H", 12: "I", 13: "s", 14: "q", 15: "Q", 16: "Q"}
INVALID = {0xFF, 0xFFFF, 0xFFFFFFFF, 0x7F, 0x7FFF, 0x7FFFFFFF}
# FIT profile enums
INTENSITY = {0: "active", 1: "rest", 2: "warmup", 3: "cooldown", 4: "recovery", 5: "interval", 6: "other"}
TRIGGER = {0: "manual", 1: "time", 2: "distance", 3: "position_start", 4: "position_lap",
           5: "position_waypoint", 6: "position_marked", 7: "session_end", 8: "fitness_equipment"}


def read_fit(path):
    """{"records": [...], "laps": [...]} from a .fit or .fit.gz file."""
    with (gzip.open(path) if str(path).endswith(".gz") else open(path, "rb")) as f:
        raw = f.read()
    hdr = raw[0]
    size = struct.unpack("<I", raw[4:8])[0]
    pos, end = hdr, hdr + size
    defs, records, laps, last_ts = {}, [], [], 0

    while pos < end:
        h = raw[pos]; pos += 1
        if h & 0x80:  # compressed timestamp header
            local, offset = (h >> 5) & 0x3, h & 0x1F
            last_ts = (last_ts & ~0x1F) + offset + (0x20 if offset < (last_ts & 0x1F) else 0)
            is_def, has_dev = False, False
        else:
            local, is_def, has_dev = h & 0xF, bool(h & 0x40), bool(h & 0x20)

        if is_def:
            endian = "<" if raw[pos + 1] == 0 else ">"
            gnum = struct.unpack(endian + "H", raw[pos + 2:pos + 4])[0]
            n = raw[pos + 4]; pos += 5
            fields = [tuple(raw[pos + 3 * i:pos + 3 * i + 3]) for i in range(n)]
            pos += 3 * n
            dev = 0
            if has_dev:
                nd = raw[pos]; pos += 1
                dev = sum(raw[pos + 3 * i + 1] for i in range(nd)); pos += 3 * nd
            defs[local] = (endian, gnum, fields, dev)
            continue

        endian, gnum, fields, dev = defs[local]
        wanted = FIELDS.get(gnum)
        rec = {}
        for num, sz, base in fields:
            chunk = raw[pos:pos + sz]; pos += sz
            if not wanted or num not in wanted:
                continue
            f = FMT.get(base & 0x1F, "B")
            if f == "s" or sz != struct.calcsize(f):
                continue
            v = struct.unpack(endian + f, chunk)[0]
            if v in INVALID:
                continue
            key = wanted[num]
            scale, offset = SCALE.get(key, (1, 0))
            rec[key] = v / scale - offset
        pos += dev
        if gnum == RECORD:
            if "ts" in rec:
                last_ts = int(rec["ts"])
            else:
                rec["ts"] = last_ts
            records.append(rec)
        elif gnum == LAP:
            rec["intensity"] = INTENSITY.get(int(rec.get("intensity", 0)), "active")
            rec["trigger"] = TRIGGER.get(int(rec.get("trigger", 0)), "manual")
            laps.append(rec)
    return {"records": records, "laps": laps}


def read_records(path):
    return read_fit(path)["records"]
