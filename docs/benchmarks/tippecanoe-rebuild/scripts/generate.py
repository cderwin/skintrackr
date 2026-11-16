"""Generate synthetic activity tracks as tippecanoe input (newline-delimited GeoJSON features).

Each activity is a 1 Hz track (like intervals.icu's latlng stream) with smoothed GPS noise, then simplified the
way the plan's `track_feature` does it: Douglas–Peucker at 2 m, coordinates rounded to 6 decimals, split at
GPS dropouts. Properties match the plan: id, type, date (YYYYMMDD).

Usage: generate.py OUT_DIR MAX_N  -> OUT_DIR/tracks-{N}.ndjson for nested subsets, plus stats.json
"""

import json
import math
import sys
from multiprocessing import Pool
from pathlib import Path

import numpy as np

EARTH_M_PER_DEG = 111_320.0
TOLERANCE_M = 2.0
SIZES = [100, 250, 500, 1000, 2000, 5000, 10000]

# sport -> (share, duration range h, mean speed m/s, wiggle: heading change sd per s in rad)
SPORTS = {
    "BackcountrySki": (0.40, (2.5, 7.0), 0.9, 0.08),
    "Hike": (0.15, (2.0, 6.0), 1.1, 0.05),
    "Run": (0.20, (0.5, 2.0), 3.0, 0.04),
    "Ride": (0.15, (1.0, 4.5), 6.5, 0.03),
    "NordicSki": (0.05, (1.0, 3.0), 3.5, 0.04),
    "AlpineSki": (0.05, (3.0, 6.0), 4.0, 0.06),
}
# ~25 Cascades trailheads (heavy overlap: repeated routes from the same start) plus a few trips abroad
CASCADES = [(-121.75 + 0.7 * i / 5, 47.4 + 1.2 * j / 5) for i in range(5) for j in range(5)]
ABROAD = [(6.9, 45.9), (11.3, 47.2), (-115.6, 51.2), (138.0, 36.7), (-70.6, -33.3)]


def douglas_peucker(xy: np.ndarray, tol: float) -> np.ndarray:
    """Indices kept by Douglas–Peucker (iterative, numpy), in metres."""
    keep = np.zeros(len(xy), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(xy) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        seg = xy[b] - xy[a]
        pts = xy[a + 1 : b] - xy[a]
        norm = math.hypot(*seg)
        d = np.abs(seg[0] * pts[:, 1] - seg[1] * pts[:, 0]) / norm if norm else np.hypot(pts[:, 0], pts[:, 1])
        i = int(np.argmax(d))
        if d[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return np.flatnonzero(keep)


def smooth_noise(rng: np.random.Generator, n: int, sd: float) -> np.ndarray:
    """GPS-like error: noise correlated over ~10 s, standard deviation `sd` metres."""
    raw = rng.normal(0, 1, (n + 20, 2))
    kernel = np.ones(20) / 20
    out = np.stack([np.convolve(raw[:, k], kernel, "valid")[:n] for k in range(2)], axis=1)
    return out / out.std(axis=0) * sd


def activity(i: int) -> tuple[str, int, int]:
    """One activity's feature line, its 1 Hz sample count and kept point count."""
    rng = np.random.default_rng(i)
    sport = rng.choice(list(SPORTS), p=[s[0] for s in SPORTS.values()])
    _, (lo, hi), speed, wiggle = SPORTS[sport]
    n = 2 * int(rng.uniform(lo, hi) * 1800)
    home = ABROAD[rng.integers(len(ABROAD))] if rng.random() < 0.05 else CASCADES[rng.integers(len(CASCADES))]

    # out-and-back: wander out for half the time, then return near the same line (skin up, ski down)
    half = n // 2
    heading = np.cumsum(rng.normal(0, wiggle, half)) + rng.uniform(0, 2 * math.pi)
    if sport in ("BackcountrySki", "Hike"):  # switchbacks on the way up
        heading += 0.9 * np.sign(np.sin(np.arange(half) / rng.uniform(40, 120)))
    step = speed * rng.uniform(0.6, 1.4, half)
    out = np.cumsum(np.stack([np.cos(heading) * step, np.sin(heading) * step], axis=1), axis=0)
    back = out[::-1][: n - half] + rng.normal(0, 15, 2)
    xy = np.concatenate([out, back]) + smooth_noise(rng, n, 2.0)

    # GPS dropouts: ~30% of activities lose signal once for 30–300 s, which splits the line
    parts = [xy]
    if rng.random() < 0.3:
        start = int(rng.integers(n // 4, 3 * n // 4))
        parts = [xy[:start], xy[start + int(rng.integers(30, 300)) :]]

    lat0 = home[1]
    lines, kept = [], 0
    for part in parts:
        idx = douglas_peucker(part, TOLERANCE_M)
        kept += len(idx)
        lon = home[0] + part[idx, 0] / (EARTH_M_PER_DEG * math.cos(math.radians(lat0)))
        lat = lat0 + part[idx, 1] / EARTH_M_PER_DEG
        lines.append([[round(float(x), 6), round(float(y), 6)] for x, y in zip(lon, lat)])

    year = 2016 + int(rng.integers(0, 11))
    date = year * 10000 + int(rng.integers(1, 13)) * 100 + int(rng.integers(1, 29))
    geometry = {"type": "MultiLineString", "coordinates": lines} if len(lines) > 1 else {"type": "LineString", "coordinates": lines[0]}
    feature = {"type": "Feature", "properties": {"id": f"i{100000000 + i}", "type": str(sport), "date": date}, "geometry": geometry}
    return json.dumps(feature, separators=(",", ":")), n, kept


def main() -> None:
    out_dir, max_n = Path(sys.argv[1]), int(sys.argv[2])
    with Pool() as pool:
        results = pool.map(activity, range(max_n), chunksize=50)
    for size in [s for s in SIZES if s <= max_n]:
        (out_dir / f"tracks-{size}.ndjson").write_text("\n".join(r[0] for r in results[:size]) + "\n")
    samples, kept = np.array([r[1] for r in results]), np.array([r[2] for r in results])
    stats = {
        "activities": max_n,
        "samples_mean": float(samples.mean()),
        "kept_mean": float(kept.mean()),
        "kept_median": float(np.median(kept)),
        "kept_p95": float(np.percentile(kept, 95)),
        "reduction": float(samples.sum() / kept.sum()),
        "feature_bytes_mean": float(np.mean([len(r[0]) for r in results])),
    }
    (out_dir / "stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
