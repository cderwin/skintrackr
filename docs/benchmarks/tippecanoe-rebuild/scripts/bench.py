"""Time tippecanoe builds of tracks.pmtiles inside the benchmark container; prints one JSON line per run.

Usage: bench.py LABEL "SIZE:REPEATS,..." [extra tippecanoe flags...]
Input /data/tracks-{SIZE}.ndjson (read-only mount), copied to local disk first so the mount isn't measured.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict

from pmtiles.reader import MmapSource, Reader, all_tiles

PLAN_FLAGS = ["-l", "tracks", "-z14", "-Z0", "--no-tile-size-limit", "--no-feature-limit", "--simplification=4", "-P"]


def tile_stats(path: str) -> dict:
    sizes: dict[int, list[int]] = defaultdict(list)
    with open(path, "rb") as f:
        for (z, _, _), data in all_tiles(MmapSource(f)):
            sizes[z].append(len(data))
    every = [s for zs in sizes.values() for s in zs]
    return {
        "tiles": len(every),
        "max_tile_kb": round(max(every) / 1024, 1),
        "max_tile_kb_by_zoom": {z: round(max(s) / 1024, 1) for z, s in sorted(sizes.items())},
    }


def run(label: str, size: int, flags: list[str], work: str, detail: bool) -> dict:
    src = os.path.join(work, f"tracks-{size}.ndjson")
    out = os.path.join(work, "tracks.pmtiles")
    cmd = ["/usr/bin/time", "-v", "tippecanoe", "-o", out, "--force", "-q", *flags, src]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    t = dict(re.findall(r"^\s*(.+?): (.+)$", proc.stderr, re.M))
    wall = t.get("Elapsed (wall clock) time (h:mm:ss or m:ss)", "0:0")
    secs = sum(float(p) * 60**i for i, p in enumerate(reversed(wall.split(":"))))
    result = {
        "label": label,
        "activities": size,
        "ok": proc.returncode == 0,
        "wall_s": round(secs, 2),
        "cpu_s": round(float(t.get("User time (seconds)", 0)) + float(t.get("System time (seconds)", 0)), 2),
        "max_rss_mb": round(int(t.get("Maximum resident set size (kbytes)", 0)) / 1024),
        "input_mb": round(os.path.getsize(src) / 2**20, 1),
    }
    if proc.returncode != 0:
        result["error"] = proc.stderr[-500:]
    else:
        result["output_mb"] = round(os.path.getsize(out) / 2**20, 2)
        if detail:
            result.update(tile_stats(out))
    return result


def main() -> None:
    label, plan, extra = sys.argv[1], sys.argv[2], sys.argv[3:]
    flags = os.environ["FLAGS"].split() if os.environ.get("FLAGS") else PLAN_FLAGS + extra
    work = tempfile.mkdtemp(dir="/work")
    for item in plan.split(","):
        size, repeats = map(int, item.split(":"))
        shutil.copy(f"/data/tracks-{size}.ndjson", work)
        for rep in range(repeats):
            print(json.dumps(run(label, size, flags, work, detail=rep == 0)), flush=True)
        os.remove(os.path.join(work, f"tracks-{size}.ndjson"))


if __name__ == "__main__":
    main()
