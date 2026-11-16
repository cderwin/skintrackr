# Benchmark: rebuilding `tracks.pmtiles` with tippecanoe

Run 2026-11-15 for [the activity map plan](../../plans/activity-map.md). How long a full rebuild of one athlete's
track archive takes, and how big the archive and its tiles get, from 100 to 10,000 activities.

## Summary

| Activities | Build (1 CPU) | Peak memory | Archive | Largest tile (z8) |
| ---: | ---: | ---: | ---: | ---: |
| 100 | 0.9 s | 86 MB | 0.7 MB | 7 KB |
| 250 | 2.1 s | 90 MB | 1.6 MB | 13 KB |
| 500 | 3.9 s | 99 MB | 3.2 MB | 26 KB |
| 1,000 | 7.4 s | 113 MB | 5.9 MB | 49 KB |
| 2,000 | 14.9 s | 135 MB | 11.3 MB | 95 KB |
| 5,000 | 36.4 s | 216 MB | 27.3 MB | 237 KB |
| 10,000 | 74.4 s | 334 MB | 53.9 MB | 467 KB |

The plan's flags (`-z14 -Z0 --no-tile-size-limit --no-feature-limit --simplification=4 -P`) on 1 CPU and 1 GB.
Build times are medians of 3 runs (2 at 5,000 and 10,000), which agreed within ±5%. Sizes are gzipped, as served.

- **Build time grows linearly: ~7.4 ms per activity** (~970 points each after simplification). Every rebuild
  is a full rebuild, so it costs ~7.5 s per 1,000 activities, however small the change.
- **Memory isn't a constraint**: 334 MB at 10,000 activities, with tippecanoe's temp files on disk.
- **The archive is ~5.4 MB per 1,000 activities.** Each `track.geojson.gz` input is ~5.9 KB.
- **Low-zoom tiles are what grows**: the densest tile (z7–z8, where every Cascades track shares a tile or two)
  gains ~47 KB per 1,000 activities. At 10,000 it is 467 KB, just under tippecanoe's default 500 KB limit, and
  heavy for a phone to download and decode. High-zoom tiles stay small.

## Variants

| Variant (1 CPU unless noted) | 2,000 | 10,000 | What changes |
| --- | ---: | ---: | --- |
| Plan flags | 14.9 s | 74.4 s | (baseline above) |
| Plan flags, **4 CPUs** | — | 38.9 s | 1.9× faster, not 4×: parts of tippecanoe are single-threaded. 1,000: 4.0 s, 5,000: 20.5 s |
| `--drop-densest-as-needed` (default 500 KB limit) | — | 78.4 s | Identical output: no tile reaches 500 KB yet |
| `--drop-densest-as-needed --maximum-tile-bytes=200000` | 14.0 s | 90.5 s | Largest tile 188 KB, but it **drops tracks at z13–z14 too** (archive 54 → 35 MB): tracks vanish fully zoomed in. Not usable as a global flag |
| `-z12` | 9.0 s | 48.0 s | 35% faster, archive 16.5 MB, 1,059 tiles instead of 10,165; z12 geometry simplified by 4 units (~6.5 m) |
| `-z12 --simplify-only-low-zooms` | 8.5 s | 80–88 s | Full detail at z12 (a ~1.6 m grid at 47°N, finer than the 2 m input simplification): archive 25.6 MB, but no faster at scale. Noisy: 22 s and 38 s at 5,000 |

## What this means for the architecture

1. **Full rebuilds are fine up to a few thousand activities.** At 1,000 activities a rebuild is ~7 s of one
   CPU, so the plan's 60 s debounce keeps an import's rebuilds under ~15% of a CPU. At 10,000, every synced
   activity costs ~75 s of CPU.
2. **Past ~3,000 activities, or if Fly's CPU throttling bites, partition the archive by year.** New activities
   almost always land in the current year, so only that year's archive (a few hundred activities, ~2 s) would
   be rebuilt. The map would then either load one PMTiles source per year (they share layers and filters) or
   `tile-join` them, which wasn't benchmarked. Activity dates can be edited, so a moved activity rebuilds both
   years.
3. **Fly's shared CPU is the unknown.** These numbers come from one Apple M1 Pro core limited to 1 CPU and 1 GB
   in Docker. Fly's `shared-cpu-1x` runs x86 and only sustains a fraction of a core once its burst balance runs
   out. Short builds will burst, but a minute-long one may be throttled several times over. Phase 0 should time
   a 1,000-activity build on the real machine. If it's slow, run builds on a separate `performance` machine or
   process group, or use the year partitioning.
4. **Cap low-zoom tiles separately, if at all.** A global `--maximum-tile-bytes` drops tracks at high zoom.
   If phones struggle with the dense z6–z9 tiles, build z0–z10 with a cap and z11–z14 without, and `tile-join`
   the two. Tracks would only be thinned out when zoomed out.
5. **Keep z14 for now.** z12 halves the archive and has 10× fewer tiles, but once the detail is kept it builds
   no faster at scale. The archive size doesn't matter much when tiles are fetched by range.

## Method

- **Data** (`scripts/generate.py`, stats in `track-stats.json`): 10,000 synthetic activities, generated
  deterministically, as 1 Hz tracks with correlated GPS noise (σ 2 m). The mix is ski touring 40%, hike 15%,
  run 20%, ride 15%, nordic 5%, alpine 5%. Durations are 0.5–7 h by sport (mean 12,700 samples). Tracks are
  out-and-back, with switchbacks for ski touring and hiking, and 30% have a GPS dropout that splits the line.
  95% start from 25 Cascades trailheads in a 0.7°×1.2° box, so overlap is heavy (the worst case for low-zoom
  tiles); 5% are abroad. Each track is simplified as the plan's `track_feature` would: Douglas–Peucker at 2 m
  and 6 decimals, leaving 970 points on average (p95 1,750, 13× fewer). Smaller sets are prefixes of the
  10,000.
- **Build** (`scripts/bench.py`, image `Dockerfile`): tippecanoe 2.79.0 built from source on Debian bookworm, as
  the app's Dockerfile stage would. Each run is in `docker run --cpus=1 --memory=1g --memory-swap=1g`, with the
  input copied to the container's disk first. Wall time, CPU time and peak memory come from GNU `time -v`, tile
  sizes from the `pmtiles` reader. The host was an Apple M1 Pro with 4 CPUs given to Docker.
- **Raw results**: `results.jsonl`, one line per run, with tile sizes per zoom level on each config's first run.

## Not measured

- **Reading the inputs back from the bucket** (`scripts/read_bench.py`). It didn't run: the local SeaweedFS had
  no volume server registered, so every write failed. A local number wouldn't reflect Tigris latency from Fly
  either. Estimate: one GET per activity, so 1,000 activities at 16 concurrent requests and 30–50 ms each is
  ~2–3 s per rebuild, on top of the build. Measure it in phase 0.
- **`tile-join`** cost, and **browser-side** rendering of the dense tiles on a phone.
- **Real tracks**: the synthetic overlap and point counts are estimates. Phase 0 reruns `bench.py` on features
  built from a real account.

## Reproduce

```bash
cd docs/benchmarks/tippecanoe-rebuild
docker build -t tippecanoe-bench .
mkdir -p data && uv run --no-project --with numpy python -I scripts/generate.py data 10000   # ~30 s, 420 MB
docker run --rm --cpus=1 --memory=1g --memory-swap=1g -v "$PWD/data:/data:ro" -v "$PWD/scripts:/scripts:ro" \
  --mount type=volume,dst=/work tippecanoe-bench \
  python -I /scripts/bench.py cpu1-mem1g 100:3,250:3,500:3,1000:3,2000:3,5000:2,10000:2
# other flag sets: -e FLAGS="-l tracks -z12 ..." replaces the plan's flags
```
