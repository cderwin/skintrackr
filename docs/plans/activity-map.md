# Plan: every activity on the map

Status: draft for review (2026-11-15). Branch: `feature/activity-map` (from `main`).

## Goal

Show every archived activity's track on `/map`. Clicking a track opens a popup with its sport, date, distance,
elevation gain and a link to the activity on intervals.icu. Buttons on the map filter the tracks by date and
sport. It has to stay fast with thousands of activities over many years, in 2D and 3D, on phones too.
The map shows an athlete's **3,000 most recent tracks**. Older activities are still synced, archived and
counted in stats, just not drawn (see "Map track limit").

## Approach in one paragraph

For each athlete, the server builds one **vector PMTiles archive** of their tracks with
[tippecanoe](https://github.com/felt/tippecanoe) and stores it in the bucket next to their activities. The
browser reads it through one authenticated endpoint that serves HTTP range requests, using the `pmtiles`
MapLibre protocol. Each tile feature carries only the activity's `id`, `type` and `date`, which is enough to
draw it, color it by sport and filter it. The popup reads the rest from the athlete's activity index, which a new
`GET /api/activities` endpoint returns once when the page loads. Filtering happens entirely in the browser with
MapLibre filter expressions, so it's instant and needs no rebuild. The archive is rebuilt in the background
(debounced) whenever an activity is ingested or removed.

## Why PMTiles (and not one GeoJSON file)

| | One GeoJSON per athlete | PMTiles archive (chosen) |
| --- | --- | --- |
| Size at 1,000 activities | ~1M points after simplification: ~20 MB raw, ~6 MB gzipped, all parsed up front | Only the tiles in view are fetched, simplified for their zoom level |
| Detail | One simplification level for every zoom: too coarse up close or too heavy zoomed out | Full detail at z14 (~0.6 m), overzoomed beyond that, coarse when zoomed out |
| Browser cost | geojson-vt tiles everything in a worker on load: slow on phones | Decodes only the visible tiles |
| Server cost | Concatenate the features | Runs tippecanoe (a few seconds per build) |
| Filtering | Same (MapLibre filter expressions on feature properties) | Same |

GeoJSON would be quicker to write, but it gets slower with every activity. With PMTiles, the work per map view
stays flat no matter how many activities there are.

**tippecanoe vs. a pure-Python tiler.** Writing our own (shapely clipping and simplification, `mapbox-vector-tile`,
the `pmtiles` writer) avoids a native binary. But it's ~200 lines of geometry code to maintain, and we'd have to
handle tile-size limits and per-zoom simplification ourselves. tippecanoe is the standard tool and does all of
that. The cost is a build stage in the Dockerfile and an install step in CI. Chosen: **tippecanoe**.

## Storage changes

Two new derived objects in the bucket. Both can be rebuilt from `streams.json.gz`, so the bucket is still the
source of truth:

```
athletes/{athlete}/activities/{activity}/track.geojson.gz   the activity's track: one GeoJSON Feature (new)
athletes/{athlete}/tracks.pmtiles                           every track of the athlete, vector tiles (new)
```

- **`track.geojson.gz`**: a `LineString`, or a `MultiLineString` split wherever `latlng` has nulls (GPS dropouts),
  so dropouts don't become straight lines. Coordinates are rounded to 6 decimals and simplified with
  Douglas–Peucker at ~2 m, which cuts a 1 Hz track by 5–10× without visible change at z14. Properties:
  `id`, `type`, `date` (an integer, `YYYYMMDD` from `start_date_local`, so a date range is a numeric comparison).
  It's written by `archive_activity` from the streams it already has, so archiving needs no extra requests.
  Activities without `latlng` (indoor, manual) get no track file.
- **`tracks.pmtiles`**: built from the `track.geojson.gz` of the athlete's 3,000 most recent activities with a
  track, so a rebuild never re-reads the large stream files. Layer `tracks`, z0–z14.
- Why per-activity files instead of one combined input file: ingest and remove already work per activity, and
  `delete_activity`/`delete_athlete` stay one line each. A rebuild reads at most 3,000 small objects (~6 KB
  gzipped each) with bounded concurrency.
- Redis: no new keys. The popup and filter data come from the existing `athlete:{id}:activities` index.
  `ImportStatus` (`athlete:{id}:import`) gains `map_tracks_omitted` (see below). Decoding is lenient and a missing
  field takes its default of 0, so existing statuses still read and no migration is needed.
- This is a bucket layout change, so it comes with `skintrackr migrate build-tracks`, which backfills
  `track.geojson.gz` for every archived activity and builds each athlete's archive. It's idempotent and stays in
  `cli/migrate.py`. Update the layout in `blob_store.py`'s docstring, in "Stored formats" in `python/README.md`
  and in `CLAUDE.md`.

## Map track limit

Benchmark-driven (see "Benchmark" below): each rebuild only includes the athlete's **3,000 most recent
activities that have a track**. At 3,000, a full rebuild should take ~22 s of one CPU, with peak memory around
170 MB and the largest tile around 140 KB. These are interpolated from the 2,000 and 5,000 runs, which were linear. So builds and tiles stay bounded however long an athlete's history is, and the
year partitioning the benchmark suggested isn't needed.

- **It only limits the map.** Syncs, imports, the archive, the Redis index, stats and GPX export still cover
  every activity, and every activity still gets its `track.geojson.gz`. Raising the limit later only needs a
  rebuild, with no re-sync or migration.
- **Only activities with a track count**, since those are what the build costs. Indoor and manual activities
  don't use up the limit. "Most recent" means by `start_date_local`, newest first, ties broken by id.
- **Selection** (`tracks.py`): `map_track_ids(index, track_ids) -> MapTracks(included, omitted)`, a pure
  function that takes the index (`Store.activities`) and the ids with a track file (one bucket listing). Both
  the build and the import status use it, so they always agree. The limit is a constant, `MAP_TRACKS_LIMIT =
  3000`, not an environment setting, because the benchmark only covers sizes up to there.
- **Logging**: when the limit is hit, the build logs a WARN:
  `logger.warning("map tracks limited", extra={"athlete_id": ..., "tracks": total, "included": 3000, "omitted": n})`.
  It logs once per build, and builds are debounced, so it isn't noisy.
- **Telling the athlete**: when an import (Re-sync) finishes, `run_import` runs the same selection and saves
  `ImportStatus.map_tracks_omitted` (0 when under the limit). The Re-sync panel then shows a warning (see
  Frontend). The periodic sync doesn't change the status. An athlete who crosses the limit through syncs sees
  the warning after their next Re-sync, and the WARN log shows it immediately.

## Server

Code goes where CLAUDE.md puts it:

- **`skintrackr/tracks.py`** (new, pure, like `gpx.py`/`vert.py`): `track_feature(activity, streams) -> dict | None`
  splits `latlng` at nulls, rounds the coordinates, simplifies them (a short Douglas–Peucker, no new dependency)
  and adds the properties. `MAP_TRACKS_LIMIT` and `map_track_ids` (see "Map track limit") live here too. It also has `build_pmtiles(features_path, out_path)`, which runs tippecanoe with
  `asyncio.create_subprocess_exec` in a temp dir. Starting flags: `-l tracks -z14 -Z0 --no-tile-size-limit
  --no-feature-limit --simplification=4`, so no track ever disappears when zoomed out. Phase 0 tunes these
  against real data.
- **`clients/blob_store.py`**:
  - `ArchiveKeys` gains `track`. `archive_activity` writes `track.geojson.gz` when there's a track, and deletes a
    stale one when there isn't. `delete_activity` deletes it.
  - `tracks_key(athlete_id)`, `track_activity_ids(athlete_id)` (one listing, like `archived_activity_ids`),
    `read_track_features(athlete_id, activity_ids)` (concurrent gets of just the selected tracks), `put()` for
    the archive.
  - `get_range(key, range_header) -> RangedObject | None` (body, `Content-Range`, `ETag`, size) for the
    endpoint below. boto3 passes `Range=` straight through, and S3/Tigris return `ETag` on ranged reads.
- **`server/ingest.py`**: `schedule_tracks(services, athlete_id)`, called after `ingest_activity`,
  `remove_activity` and a sync that changed something. It sets the athlete's dirty flag in memory and spawns a
  `tracks-{athlete}` task (`services.spawn`) if none is running. The task loops: clear the flag, build, upload,
  then wait `TRACKS_DEBOUNCE` (60 s) and repeat while the flag is set. Each build selects tracks with
  `map_track_ids` (index + `track_activity_ids`), logs the WARN if any are omitted, reads only the included
  features, runs tippecanoe and uploads. So a 1,000-activity import rebuilds about
  once a minute, plus once at the end, and the map fills in as the import runs. `run_import` also schedules a
  build when it finishes, and sets `status.map_tracks_omitted` from `map_track_ids` before saving the `done`
  status. `remove_athlete` cancels the task.
- **`models.py`**: `ImportStatus.map_tracks_omitted: int = 0`, documented in its `Attributes:`.
- **`server/api.py`**:
  - `GET /api/activities`: the athlete's index (`Store.activities`) as JSON, for the popup and the filter
    options (which sports exist, which date range).
  - `GET /api/tracks.pmtiles`: authenticated, and answers `Range` requests with 206, `Content-Range`, `ETag`
    and `Accept-Ranges: bytes`. It returns 404 if no archive exists yet, which the map shows as "no tracks yet".
    `Cache-Control: private, no-cache`, so a browser revalidates by ETag and never shows another athlete's
    tracks. The `pmtiles` client notices an ETag change mid-session and reloads.
- **Dockerfile**: a `tippecanoe` build stage (clone a pinned release tag; `make -j`; needs `build-essential`,
  `libsqlite3-dev`, `zlib1g-dev`). Copy the `tippecanoe` binary into the runtime image and add `libsqlite3-0` there.
  It's cached between builds, so only the first build is slower.
- **CI** (`ci.yml`): build tippecanoe at the same tag, cached with `actions/cache`, so the `build_pmtiles` test
  really runs it. Locally: `brew install tippecanoe`. The test is marked
  `skipif(shutil.which("tippecanoe") is None)` so a dev without it can still run `just test`, but CI always has it.

## Frontend

- **Dependency**: `pmtiles` (Protomaps). Register `maplibregl.addProtocol('pmtiles', protocol.tile)` once in
  `MapView.vue`, next to `setWorkerUrl`.
- **`api.ts`**: `activities()` → `ActivitySummary[]`. `tracksUrl` is `pmtiles:///api/tracks.pmtiles` (same-origin,
  so the session cookie goes with it). `vite.config.ts` already proxies `/api`.
- **`MapView.vue`**: on `load`, if signed in (`useSignedIn()`), add a vector source from the archive and these
  layers, all draped on the 3D terrain:
  - `tracks`: a line layer colored by `type` (a `match` expression; one palette entry per sport family:
    ski touring, nordic, alpine, run/hike, ride, other), with a width that grows with zoom.
  - `tracks-selected`: the same source, filtered to `['==', ['get', 'id'], selectedId]`, drawn wider and on top.
    Filtering by id works across tile boundaries, where feature-state wouldn't, because tippecanoe ids must be
    numeric.
  - A wider transparent hit layer under them, so thin lines are easy to tap on a phone.
  - Pointer cursor on hover.
  Signed out: the terrain only, plus a small card linking to `/connect`.
- **Popup** (`components/ActivityPopup.vue`, mounted into a `maplibregl.Popup` with `setDOMContent`, so the
  activity name is escaped by Vue): the activity name, then sport (`BackcountrySki` → "Backcountry ski"), date,
  distance, elevation gain and "Open in intervals.icu ↗" (`https://intervals.icu/activities/{id}`). The values
  come from the `/api/activities` index by id. Where tracks overlap, for example at a trailhead, clicking returns
  several features. The popup then lists them (up to 5, newest first), and choosing one shows its details and
  highlights it.
- **Filters** (`components/MapFilters.vue`, a floating button that opens a panel, styled like the existing
  `FloatingButton`):
  - **Sport**: chips for each type in the index, with counts, multi-select, plus "all".
  - **Date**: calendar-year presets (this year, each earlier year), last 12 months, all time, and a custom
    from–to range.
  - Shows "N of M activities shown". Applied with `map.setFilter` on both layers: `['in', ['get','type'], ...]`
    and `['>=', ['get','date'], from]`/`['<=', ...]`. If the selected track gets filtered out, its popup closes.
  - Filters are saved per browser in `settings.ts` (`mapFilters`), like units and the vert goal.
- **`settings.ts`**: `formatDistance(meters)` (km or mi, following `units`), next to `formatVert`.
- **Map limit warning** (`components/ImportPanel.vue`, the Re-sync panel): `ImportStatus` in `api.ts` gains
  `map_tracks_omitted`, and `MAP_TRACKS_LIMIT = 3000` mirrors the server constant, the same way
  `SYNC_WINDOW_DAYS` does. When a finished import has `map_tracks_omitted > 0`, the panel shows a dismissible
  amber warning: "Your map shows your 3,000 most recent activities. N older activities are still synced and
  count in your stats, but aren't drawn on the map." It shows after a Re-sync, and again whenever the panel loads
  that status, until dismissed. Dismissing is remembered per browser (in `localStorage`, keyed by the omitted
  count), so the warning comes back if the count grows.

## Implementation phases

0. **Check against your real account**: the activity link format; how often `latlng` has nulls; rerun
   `docs/benchmarks/tippecanoe-rebuild/scripts/bench.py` on your real track features (archive size, largest
   tile); time a 1,000-activity build and the bucket reads on the Fly VM itself (shared-CPU throttling); that
   Tigris returns `ETag` and `Content-Range` on ranged reads.
1. **Track features**: `tracks.py` (`track_feature` + simplification), `archive_activity`/`delete_activity`
   writing and deleting `track.geojson.gz`, tests (`test_tracks.py`, `test_blob_store.py`).
2. **Archive build**: `build_pmtiles`, `MAP_TRACKS_LIMIT`/`map_track_ids`, `track_activity_ids`,
   `read_track_features`, `schedule_tracks` wired into ingest, sync and import, `ImportStatus.map_tracks_omitted`,
   the WARN log, Dockerfile and CI tippecanoe. Tests: selection (newest first, ties, activities without a track
   don't count, exactly 3,000 vs 3,001), a build over the limit includes only the newest (with a small limit
   patched in), the WARN is logged (`caplog`), an import over the limit saves `map_tracks_omitted`, and an
   old status without the field decodes to 0.
3. **Endpoints**: `GET /api/activities`, `GET /api/tracks.pmtiles` with ranges and ETag, tests (full,
   ranged, unauthenticated, no archive yet).
4. **Migration**: `skintrackr migrate build-tracks`, docs (README stored formats, CLAUDE.md, blob_store docstring).
5. **Map layers + popup**: `pmtiles` protocol, layers, hit layer, popup, signed-out state.
6. **Filters**: `MapFilters.vue`, saved filters, the count.
7. **Limit warning**: the `ImportPanel.vue` warning and its dismissal.

Each phase is one commit. Phases 1–4 can merge on their own: the map ignores the archive until phase 5.

## Benchmark (2026-11-15)

Full results, method and how to reproduce: [`docs/benchmarks/tippecanoe-rebuild`](../benchmarks/tippecanoe-rebuild/README.md).
Synthetic tracks, tippecanoe 2.79.0, 1 CPU and 1 GB:

| Activities | 100 | 500 | 1,000 | 2,000 | 5,000 | 10,000 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Full rebuild | 0.9 s | 3.9 s | 7.4 s | 14.9 s | 36.4 s | 74.4 s |
| Peak memory | 86 MB | 99 MB | 113 MB | 135 MB | 216 MB | 334 MB |
| Archive | 0.7 MB | 3.2 MB | 5.9 MB | 11.3 MB | 27.3 MB | 53.9 MB |
| Largest tile | 7 KB | 26 KB | 49 KB | 95 KB | 237 KB | 467 KB |

Rebuilds cost ~7.4 ms of CPU per activity, and memory stays low. That's why the map is limited to 3,000 tracks
(about 22 s per build, interpolated), and why the year partitioning the benchmark suggested isn't needed. 4 CPUs only build 1.9× faster. A global tile-size cap
drops tracks at high zooms, so any cap has to apply to low zooms only.

## Rollout

Merge phases 1–4, deploy, run `fly ssh console -C "skintrackr migrate build-tracks"` against production, and
check one athlete's `tracks.pmtiles` with `pmtiles show` or https://pmtiles.io. Then merge phases 5–6.

## Decisions (confirmed 2026-11-15)

1. **Date presets use calendar years** (like the vert stats): this year, each earlier year, last 12 months, all
   time, and a custom range.
2. **Every activity with GPS is on the map**, whatever its sport. Sport filtering is up to the viewer.
3. **The initial view stays centered on the Cascades.** Fitting the view to the athlete's tracks would zoom far
   out whenever there are activities abroad.

## Later (out of scope)

- Serve the archive straight from the bucket with a short-lived presigned URL (needs CORS on the bucket),
  instead of proxying ranges through the server.
- Color by date or by speed, heatmap mode at low zoom, an elevation profile in the popup.
- Raise or remove the 3,000-track limit with one archive per year (only the current year rebuilt on sync):
  see the benchmark.
- Show the limit warning on the map page too, and after periodic syncs (it would need the build's count saved
  in Redis).

## Risks

- **Strava-sourced activities** are skipped by imports, so they won't be on the map either.
- **Low-zoom tile size**: the densest tile (z7–z8) gains ~47 KB per 1,000 overlapping activities, so ~140 KB
  at the 3,000-track limit. If phones struggle, build z0–z10 with `--drop-densest-as-needed --maximum-tile-bytes` and z11–z14
  without, then `tile-join` them. A global cap drops tracks at z13–z14 too.
- **Fly's shared CPU**: the benchmark ran on an M1 core. Fly's `shared-cpu-1x` sustains a fraction of a core
  after its burst, so phase 0 times a build there.
- **One machine assumed**: the dirty flag and debounce live in memory. With several Fly machines, two could
  rebuild at once. The result is still correct (last write wins), just wasted work.
- **Native dependency**: tippecanoe is pinned to a release tag in both the Dockerfile and CI, so they don't drift.
