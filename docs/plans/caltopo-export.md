# Plan: export an area's tracks to CalTopo

Status: implemented (2026-11-20), in draft PR #19. Branch: `feature/caltopo-export` (from `main`). See
"Implementation notes" for where it differs from this plan.

## Goal

On `/map`, draw a rectangle and download every track in it that passes the map's filters, as a GeoJSON file
that CalTopo imports. The tracks come in at full detail (every recorded point, with elevation and time), colored
like the map, and optionally grouped in one folder. CalTopo's format, and what test imports showed about it, is
described in [`docs/caltopo.md`](../caltopo.md).

## Decisions (confirmed 2026-11-20)

1. **Only tracks that pass the map's sport and date filters** are exported.
2. **Folder**: a checkbox, "Put tracks in a CalTopo folder", with a text box for the folder's name, which
   defaults to "skintrackr exports". The checkbox is on by default, and both are remembered per browser. Unchecked,
   the lines import at the map's top level.
3. **Whole tracks by default.** "Clip to the area" is the option.
4. **Full detail.** The exported lines are built from the archived streams (`streams.json.gz`), never from the
   simplified tracks, even though that's slower. The map's tile archive (`tracks.pmtiles`) is only used to find
   out quickly *which* activities might be in the area (see "Selecting tracks").
5. **Styled like the map**: each sport's map color, a solid line, fully opaque. CalTopo can't draw the map's
   white casing.
6. **No direct push to CalTopo.** The athlete downloads a file and imports it.
7. **Stable ids.** CalTopo keeps a file's ids and recognizes objects by them: re-importing an id that's already on
   the map adds nothing, and a folder with a new id is a new folder, even with the same name. So ids are derived,
   not random (see "Ids"). Repeated exports into the same folder name land in one folder, and tracks already
   there aren't duplicated.

## User flow

1. On the map, the athlete clicks the **area button**, at the top of the bottom-right control stack. Its icon is a
   dashed rectangle with a pointer. The button turns blue, the cursor becomes a crosshair, and a hint appears:
   "Click two corners of the area · Esc to cancel". Clicks on tracks no longer open popups.
2. **First click** drops a corner. On desktop, the rectangle then follows the pointer. Panning and zooming still
   work.
3. **Second click** finishes the rectangle. It stays on the map, dashed, draped over the terrain. The export
   dialog opens, and the dialog immediately asks the server which tracks are in the area. Those tracks are
   highlighted on the map, and the dialog reads "**23 tracks** · Ski touring · 2025", naming the active map
   filters.
4. The dialog's options:
   - **Tracks**: "Whole tracks" (default) or "Clip to the area".
   - **Folder**: [x] "Put tracks in a CalTopo folder", with a text box below it ("skintrackr exports").
   - Buttons: **Redraw** (back to step 1, keeping the options), **Cancel**, and **Download for CalTopo**.
5. Download shows "Preparing…" (full-detail exports take a few seconds). The browser then saves
   `skintrackr-tracks-2026-11-20.json`, and the dialog changes to: "Downloaded 23 tracks. In CalTopo, open a map,
   choose Import at the top of the sidebar, and pick this file. Tracks already on the map from an earlier export aren't added twice."
6. Esc, Cancel or the area button (pressed again) at any point removes the rectangle and leaves the mode.

The dialog explains the cases where nothing can be downloaded instead:

- **No tracks in the area**: "No tracks in this area match the map's filters." Download is disabled.
- **Over the limit**: "142 tracks: exports are limited to 100. Narrow the area or the map's filters."
- **Signed out or other errors**: shown inline, as other pages do.

## API

Both endpoints are authenticated (`authenticate_request`) and take the same JSON body:

```json
{
  "bbox": [-121.52, 47.38, -121.31, 47.51],
  "sports": ["BackcountrySki"],
  "date_from": 20250101,
  "date_to": 20251231,
  "mode": "whole",
  "folder": "skintrackr exports",
  "units": "ft",
  "styles": {"BackcountrySki": {"label": "Backcountry ski", "color": "#e11d48"}},
  "stroke_width": 3
}
```

- `bbox`: `[west, south, east, north]` in degrees.
- `sports`: an empty list means every sport.
- `date_from` and `date_to`: inclusive YYYYMMDD numbers. The browser computes them with `dateRange()`, so the
  server doesn't reimplement the period presets.
- `mode`: `"whole"` or `"clip"`.
- `folder`: `null` for no folder.
- `units`: the athlete's units (`"ft"` or `"m"`), for the descriptions.
- `styles`: each activity type's label and color, from `sports.ts`. Sending them keeps the server from keeping a
  second copy of the sport table.
- `stroke_width`: the line width. It's in the request so styling can become an option later.

The preview only reads `bbox`, `sports`, `date_from` and `date_to`.

### `POST /api/activities/export/preview`

Answers `{"activity_ids": ["i123", ...], "limit": 100}`: the activities whose tracks meet the area, newest
first. The dialog shows the count and highlights them. It's computed from the map's tile archive, so it's fast,
and it can differ from the export only for a track that passes within a few meters of the area's edge (see
"Selecting tracks").

### `POST /api/activities/export`

Answers the GeoJSON document:

- Media type `application/geo+json`.
- `Content-Disposition: attachment; filename="skintrackr-tracks-YYYY-MM-DD.json"`: `.json`, because it's what
  CalTopo's docs promise to accept (`.geojson` worked too in the import test, on web and mobile, but the docs say
  the mobile app ignores it).
- `X-Track-Count`, so the dialog can say how many it got.

Errors are `{"message": ...}`, as everywhere else:

- 400: a malformed body or invalid box ("invalid area"), "No tracks in this area match the map's filters",
  "142 tracks: exports are limited to 100 …", and "This export is too large (1.4M points; the limit is 1M). Clip
  to the area, or narrow it or the filters."
- 401/403 as usual.

## Selecting tracks

Shared by both endpoints, in a new `export_candidates()`. It reads the athlete's **`tracks.pmtiles`**, the
archive the map itself draws, rather than one `track.geojson.gz` per activity: the archive is already a spatial
index, so a preview costs a few range reads instead of up to 3,000 object reads.

1. **Zoom.** Pick the deepest zoom, at most 14 (the archive's maximum), at which the box covers at most
   `PREFILTER_MAX_TILES` = 64 tiles. A small box reads z14 tiles; a 50 km box reads z11 tiles. Every zoom holds
   every track (`--no-feature-limit`, nothing dropped), only simplified more at lower zooms.
2. **Margin.** Grow the box by `prefilter_margin_m(zoom, lat)`: the 2 m of `TOLERANCE_M`, plus 5 tile units at
   that zoom (tippecanoe's `--simplification=4` moves a line up to about 4 units, and quantization half a unit).
   A unit at z14 and 47°N is about 0.4 m, so the margin is about 4 m there, and about 18 m at z11. A full track in
   the box then always passes: this step can only let through a few extra tracks that pass just outside the box.
3. **Tiles.** `BlobStore.read_track_tiles(athlete_id, tiles)` reads the tiles covering the grown box with range
   GETs on the archive (header and directories first, then each tile that exists; empty tiles aren't in the
   archive), using the `pmtiles` package's reader in `asyncio.to_thread`.
4. **Features.** Each tile is decoded with `mapbox-vector-tile`. The grown box is converted into each tile's
   coordinates once (a lon/lat box is still a rectangle in Web Mercator), so no feature's points are converted.
   Keep the features whose properties pass the filters, the same `type` and `date` properties the map's
   `filterExpression` tests: `type` in `sports` (or all, if it's empty) and `date` between `date_from` and
   `date_to`. Then keep those whose geometry meets the box: one vectorized `shapely.intersects` call per tile.
   Collect their `id`s, newest first (by `date`, then id). That is all the preview does.
5. **Exact test (export only).** Each survivor's full-detail track is tested against the exact box (see
   "Building the tracks"). A track with nothing inside is dropped.

Because the archive is what the map draws, the export can't contain a track the athlete couldn't see, without
recomputing `map_track_ids`, and the filters are applied to the same properties as on the map. The archive is
rebuilt in the background after changes, so a just-synced activity can be missing for a moment, as it is on the
map, and a just-deleted one can linger: the export skips an activity whose archive is gone. No archive yet (a new
athlete) means no tracks.

The one thing the prefilter can miss: `_segments` drops a lone fix between two GPS dropouts (a "segment" of
one point), so the archive doesn't have it. A track whose only point in the box is such a fix is missed. That's
rare enough to accept and document.

**Cost.** At most 64 tile reads plus the directories, and decoding them. Locally, on the 3,000-track demo
archive (17 MB), a preview took 0.65 s for a small box (366 tracks) and 1 s for a large one (2,839 tracks). If
reads get slow, adjacent tiles (they're stored in Hilbert order) can be fetched in one range read.

## Building the tracks

Per selected activity, in order:

1. `blob_store.read_activity()` returns the archived `Activity` and every stream.
2. **Points.** `merge_streams(streams, skip_unknown=True)` is already used by the GPX export. It drops samples
   with no position, and fills gaps in the other streams. The points are a numpy array with one row per point,
   `(lon, lat, ele, ms)`:
   - `lon` and `lat` are rounded to 6 decimals (`PRECISION`), as in CalTopo's own tracks.
   - `ele` is the `altitude` stream, rounded to whole meters.
   - `ms` is the activity's start plus the `time` offset, in Unix milliseconds. The start comes from the same
     parsing the GPX export uses: `_start_time` in `intervals.py`, renamed to `activity_start` and made public.
   - If the activity has no `time` stream, points are `(lon, lat, ele)`, and with no `altitude` stream they're
     `(lon, lat)`. CalTopo imports both, and fills a missing elevation profile from its own terrain data, while
     zeros would show up as a fake profile.
3. **Dropouts.** The points are split wherever consecutive fixes are more than `MAX_GAP_M` (200 m) apart, with
   the same rule as the map: `split_at_gaps(points)`, shared with the map (see "Shapely and numpy in the existing
   code"). It works on arrays of any width whose first two columns are lon and lat.
4. **Area.**
   - **Whole**: if any segment meets the box (`shapely.intersects` on the segment's line and the box), the
     activity gives **one line of all its points**, unsplit, like a track recorded in CalTopo (CalTopo draws a
     straight line across a dropout). Never a `MultiLineString`: CalTopo imports only part of one.
   - **Clip**: `clip_line(points, bbox)` is applied to each segment, and every clipped piece becomes its own
     line. A track that leaves the area and comes back gives several lines, titled "… (1/3)", "… (2/3)" and so
     on.

**Clipping** is Liang–Barsky, vectorized with numpy over all of a segment's line pieces at once (about 40 lines):

- It isn't shapely's `intersection`: GEOS overlays keep at most one extra value (Z), and drop or don't
  interpolate the rest, while a crossing point needs both its elevation and its time.
- Each step from one point to the next gets its entry and exit parameters `t0 ≤ t1` against the four edges.
  Steps with `t0 > t1` are outside. Consecutive kept steps that join (`t1 = 1`, then `t0 = 0`) form one piece.
- A point on the box's edge counts as inside.
- Where a piece enters or leaves the box, a point is interpolated on the edge. Every column is interpolated
  (`p + t (q − p)`), so the crossing point gets a matching elevation and time.
- A step that crosses the box with no point inside still counts.
- Clipping happens in lon/lat, which is exact for a lon/lat box.
- The box must not cross the antimeridian. `BBox` rejects that, and every other invalid box: `west < east`,
  `south < north`, and within ±180°/±90°.

## The CalTopo document

`caltopo.py` (new) builds it, following the "file skintrackr should write" section of the CalTopo doc:

- A `FeatureCollection` of compact JSON. Each feature is encoded as soon as it's built, so memory holds bytes
  rather than large lists of floats.
- If `folder` is set, the document starts with one `Folder` feature: the folder id (see "Ids"), `title`,
  `visible: true` and `labelVisible: true`.
- Each line is a `Shape` with a `LineString` and the line's id. Its properties are `class`, `title`,
  `description`, `stroke`, `stroke-width`, `stroke-opacity: 1`, `pattern: "solid"`, and `folderId` when there's
  a folder.
  - **`title`**: `"2026-01-04 Snoqualmie Mountain"`, the local start date and then the name. CalTopo's sidebar
    sorts by title, so the list reads chronologically. Pieces get " (n/N)".
  - **`stroke`**: `styles[type].color`. If a type is missing from `styles`, it's CalTopo's default `#FF0000`.
    The browser sends a style for every type in the index, so that shouldn't happen.
  - **`description`**, one item per line (CalTopo keeps the newlines, and they read better than " · "):
    - the sport label (`styles[type].label`)
    - distance, gain and moving time, in the request's units
    - the intervals.icu link
    - the Strava link, when the index has a `strava_id`
- Lines are in order of start time, oldest first.

### Ids

Every id is a `uuid5` (`uuid.NAMESPACE_URL`) of a key naming what the object is, so the same object exported again
gets the same id, and CalTopo skips it:

| Object | Key |
| --- | --- |
| Folder | `skintrackr:{athlete_id}:folder:{name}` |
| Whole track | `skintrackr:{athlete_id}:track:{activity_id}:{folder}` |
| Clipped piece | `skintrackr:{athlete_id}:track:{activity_id}:{folder}:{w},{s},{e},{n}:{i}/{count}` |

- `{name}` and `{folder}` are the folder name as sent (trimmed); `{folder}` is empty with no folder. The folder is
  part of a line's key so that exporting a track into a second folder puts a copy there, instead of being skipped
  because the track is already in the first.
- The box in a piece's key is rounded to 6 decimals. Exporting the same area again skips its pieces; a different
  area gives new ones. Whole tracks and pieces never share ids, so both can be on one map.
- The athlete id keeps two athletes' exports apart if they import into a shared map.
- The keys are effectively a stored format: changing them would duplicate every line athletes re-export into maps
  that already have them. They're pinned by the fixture test, and a change needs a good reason.
- A re-export that only restyles a track (a new map color, say) keeps its ids. Whether CalTopo then updates the
  line or keeps the old one wasn't tested (see "Phases", phase 0). Either way it doesn't duplicate.

## Limits

Full-detail tracks are big. A 5-hour tour is about 18,000 points, or about 0.8 MB of GeoJSON. Two limits are set
as constants:

- **`EXPORT_TRACKS_LIMIT` = 100.** This bounds the time an export takes: each track is a bucket read plus JSON
  decoding of every stream. The preview reports it, so the dialog can refuse before the download starts.
- **`EXPORT_POINTS_LIMIT` = 1,000,000** (about 41 MB). This bounds the file, for CalTopo's sake. In the import
  test, CalTopo took about 3 s per 100,000 points (34 s for 1.2M points, 50 MB) and stayed smooth afterwards; a
  forum report failed at 125 MB. So 1M points imports in under half a minute. Points are counted as tracks are
  built, and the export stops with a 400 as soon as the total is over the limit. In clip mode, only the points
  inside the area count. (100 long tours could reach it, but `EXPORT_TRACKS_LIMIT` will usually bind first.)

Tracks are read and built **4 at a time** (`EXPORT_READ_CONCURRENCY`). Decoding and geometry run in
`asyncio.to_thread`, so a long export doesn't block other requests on the single shared CPU. The finished
document is returned in one response (no streaming). That keeps errors such as the point limit as clean 400s.
If Fly's proxy ever times out on long exports, switch to a streamed response (see "Risks").

## Server changes

Paths relative to `python/skintrackr/`.

- **Dependencies** (`pyproject.toml`): `shapely` (with numpy, which it requires), `pmtiles` and
  `mapbox-vector-tile`. Shapely and numpy are listed directly, since skintrackr uses them directly.
- **`tracks.py`**
  - `BBox` (frozen attrs): `west, south, east, north`. It has a `from_list()` that validates (raising
    `ValueError`), an `expanded(metres)` method, and a `polygon` property (a `shapely.box`).
  - `split_at_gaps(points)` and the GEOS simplification (see "Shapely and numpy in the existing code").
  - `clip_line(points, bbox) -> list[np.ndarray]`, for arrays of any width.
  - `prefilter_zoom(bbox)`, `prefilter_margin_m(zoom, lat)`, and `tile_features(tile, z, x, y, bbox, filters)`:
    steps 1, 2 and 4 of "Selecting tracks", with no I/O.
  - The constants `PREFILTER_MAX_TILES`, `EXPORT_TRACKS_LIMIT`, `EXPORT_POINTS_LIMIT` and
    `EXPORT_READ_CONCURRENCY`.
- **`clients/blob_store.py`**: `read_track_tiles(athlete_id, tiles) -> dict[tuple[int, int, int], bytes]`: the
  archive's tiles, decompressed, read with range GETs (a small `_read_range(key, offset, length)` beside
  `_read`). A missing archive gives no tiles.
- **`clients/intervals.py`**: rename `_start_time` → `activity_start`, now public; its two callers stay the same.
- **`caltopo.py`** (new):
  - `export_points(activity, streams) -> np.ndarray`: step 2 of "Building the tracks".
  - `export_lines(points, bbox, mode) -> list[np.ndarray]`: steps 3 and 4. Lines are encoded with
    `.tolist()`, after casting elevation and time to integers.
  - `CaltopoDocument`: holds the folder id, collects encoded features and counts points (raising
    `ExportTooLarge`), and has `to_bytes()`.
  - `folder_id(athlete_id, name)` and `line_id(athlete_id, activity_id, folder, piece)`: the keys in "Ids".
  - `track_title(...)` and `track_description(...)`.

  The module holds no I/O, so it's tested with plain data.
- **`models.py`**: `TrackExportRequest` and `SportStyle` (attrs, decoded with the shared converter; a decode
  error becomes a 400), plus `ExportPreview` (`activity_ids`, `limit`).
- **`server/api.py`**:
  - `export_preview` and `export_tracks` handlers, added to the module's handler list.
  - `export_candidates(services, athlete_id, request)` lives here or in a small helper beside it: it reads the
    tiles through `BlobStore` and selects with `tracks.py`. The export also loads the index (`store.activities`)
    for the selected activities' summaries, as `list_activities` does.
  - Each step is wrapped in `fail_with`. Over-limit cases raise `ClientException` with the messages above.
  - One INFO log per export with `athlete_id`, `mode`, `tracks`, `points`, `bytes` and `seconds`.
- **Docs**: the two endpoints go in the `python/README.md` API table, and the CLAUDE.md component list gets
  `caltopo.py` and a line about the export.

There are **no stored-format changes**: the export only reads what's already in the bucket and Redis. The
archive's tippecanoe flags become something the export depends on, though (every zoom keeps every track, and
`--simplification=4` sets the margin), so the comment on `TIPPECANOE_FLAGS` says so, and a test pins it.

## Shapely and numpy in the existing code

Shapely is new for the export, so the existing geometry in `tracks.py` was checked for what it could replace.
Measured on 60 real tracks (286,488 points, from the import-test GPX files):

| Code | Today | With shapely or numpy | Change |
| --- | ---: | ---: | --- |
| `_simplify` (Douglas–Peucker in Python) | 330 ms | 50 ms (`shapely.simplify`, GEOS) | **Replace** |
| `_segments` and `_metres` (gap splitting) | 54 ms | 39 ms (numpy) | Replace, for the shared array version |

- **`_simplify` → `shapely.simplify(line, TOLERANCE_M, preserve_topology=False)`**, on the same equirectangular
  projection to metres, then back to degrees before rounding. It's about 6.5× faster, which speeds up every
  track build (ingest and `migrate build-tracks`), and it drops about 30 lines of hand-written code. The output
  isn't identical: GEOS measures a point's distance to the *segment* between the kept points, while today's code
  measures it to the infinite *line* through them, which is a slightly looser test. On the 60 tracks, 7 came out
  identical, and the rest differed by 124 kept points in all. Both stay within 2 m of the
  track, and the existing `track_feature` tests check exactly that, so they still pass. Archived
  `track.geojson.gz` files aren't rebuilt: old and new simplifications are equally valid.
- **`_segments` and `_metres` → `split_at_gaps(points)`** with numpy: one vectorized distance per step, split
  with `np.flatnonzero(gaps > MAX_GAP_M)`. On its own the speedup is small, but the export needs the same split on
  full-detail arrays with elevation and time, so one array version serves both, and the scalar `_metres` goes
  away. `_segments` keeps reading the `latlng` stream and calls it.
- **Kept as they are**: `feature_date`, `map_track_ids` and `build_pmtiles` have no geometry. `merge_streams`
  (in `intervals.py`) is row-oriented stream merging that the GPX export also uses, so it stays, and the export
  turns its rows into an array once.

These are a refactor with no visible change on the map, so they go first, in phase 1.

## Frontend changes

Paths relative to `frontend/src/`.

- **`api.ts`**
  - Split `call()` into `send()`, which does the fetch and the shared 401/403 and `{message}` handling and
    returns the `Response`, and `call()`, which parses the JSON. The export needs the body as a blob, and this
    reuses the error handling instead of copying it.
  - Add `api.exportPreview(request)`, `api.exportTracks(request): Promise<{blob, filename, count}>` (the file
    name comes from `Content-Disposition`), the `TrackExportRequest` type, and `saveFile(blob, filename)` (an
    object URL and an `<a download>`, revoked afterwards).
- **`settings.ts`**: `caltopoExport: {mode: 'whole' | 'clip', folder: boolean, folderName: string}`. The
  defaults are `whole`, `true` and `"skintrackr exports"`, validated on load like `mapFilters`.
- **`mapFilters.ts`**: `exportFilters(filters)` returns `{sports, date_from, date_to}` from `dateRange()`. It
  sits next to `filterExpression`, so the map and the export can't disagree about the filters.
- **`sports.ts`**: `sportStyles(types)` returns `{type: {label, color}}` from `sportLabel` and `sportColor`.
- **`areaSelect.ts`** (new): a small class that owns the drawing on the map. The view only wires it up.
  - `start()` and `cancel()`, and an `onSelect(bbox)` callback.
  - A GeoJSON source with three layers: a translucent `fill`, a dashed `line` (`line-dasharray`) and a
    `circle` for the first corner. They're added above the track layers.
  - It handles `click`, `mousemove` (the rectangle follows the pointer after the first corner) and the crosshair
    cursor.
  - It ignores a second click at the same point (a zero-area box).
- **`MapView.vue`**
  - **Controls.** The satellite toggle's control boilerplate becomes `buttonControl(button)`, and both buttons
    use it. The area button gets an inline SVG icon in the same grey (blue when on), like the satellite icon. It's
    disabled until tracks have loaded.
  - **Clicks.** The track `click` handler returns early while the area mode is on.
  - **Esc.** `closeOnEscape` also cancels drawing and closes the dialog.
  - **Highlight.** The selected-track layers' filter changes from one id to a list. Their ids are the export
    preview's while the dialog is open, else the popup's `[selectedId]`. That reuses the existing highlight
    instead of adding layers.
- **`components/ExportDialog.vue`** (new)
  - Props: the bbox, the activities (for the filter summary), and the filters.
  - It emits `redraw`, `close` and `preview(ids)`.
  - It's a card on desktop, and a bottom sheet under 600 px.
  - It owns its states: counting, ready, over the limit, empty, preparing, downloaded, error.
  - The folder name input is disabled while the box is unchecked. A blank name falls back to the default.

## Tests

Python, with no network, using the existing fakes:

- **`test_tracks.py`**
  - `clip_line` (parametrized): entirely inside, entirely outside, entering, leaving, re-entering (two pieces),
    a point exactly on an edge, crossing the box with no point inside, a corner case through a box corner.
    The interpolated point's elevation and time must lie between its neighbours'.
  - `split_at_gaps` and the GEOS simplification keep the map's behavior: the existing `track_feature` tests
    still pass.
  - `BBox.from_list` rejects malformed boxes (parametrized). `expanded` grows the box by about the right number
    of degrees.
  - `prefilter_zoom` picks z14 for a small box and a lower zoom for a large one, never over the tile cap.
  - `tile_features` on a tile built from known features: the filters apply, a track just inside the box passes
    (including at a low zoom, with its margin), and one well outside doesn't.
  - Built with tippecanoe (skipped when it isn't installed; CI installs it): an archive from a few tracks, read
    back through the prefilter, finds the right ones at z14 and at a low zoom. This catches a change to the
    flags that the prefilter relies on.
- **`test_blob_store.py`**: `read_track_tiles` against an archive in `FakeBucket`: present tiles, absent tiles,
  and no archive.
- **`test_caltopo.py`**
  - With and without a folder: there's one `Folder`, every line's `folderId` matches it, and the ids are unique.
  - Ids are stable: building the same export twice gives the same ids. A different folder name, athlete or box
    gives different ones; whole tracks and pieces differ.
  - 4-value points with altitude and time, 3-value without time, 2-value without altitude. Time is the start
    plus the offset in ms.
  - Whole mode gives one line; clip mode gives "(n/N)" pieces.
  - Titles, and descriptions in m and ft, with and without a Strava link.
  - A missing style falls back to `#FF0000`. The points limit raises.
  - A small checked-in expected document (`tests/fixtures/caltopo-export.json`) pins the exact output format,
    ids included.
- **`test_api.py`**
  - Preview and export for activities archived in `FakeBucket`, with their archive built by tippecanoe.
  - Sport and date filters apply. A track in the archive whose activity is gone is skipped.
  - Whole vs. clip. The headers: media type, file name, `X-Track-Count`.
  - 400s: an empty area, the track limit, the points limit, a bad box, a bad body. 401 when unauthenticated.

Frontend: `just web-typecheck`, and a manual run through the user flow with `just web-dev` against
`just py-serve`, using local data. The frontend has no unit test setup, and this feature doesn't justify adding
one.

## Phases

0. **Check assumptions** (no code merged):
   - ~~CalTopo import tests~~: done 2026-11-20 (`docs/caltopo.md`). They set the description format, the point
     formats, `EXPORT_POINTS_LIMIT` and the ids.
   - Two quick imports for the ids, with `caltopo_examples/import/03-fixed-id.json`: import it into a second,
     fresh map (do the same ids import into another map?), then change the line's `stroke` in the file and
     import it again into the first (is the line updated, or kept?). If ids don't work across maps, fall back to
     random ids and drop decision 7. Still open.
   - ~~Timings on Fly~~: dropped (2026-11-20). Reading the tile archive made the preview's cost small, and the
     export's is bounded by `EXPORT_TRACKS_LIMIT`.
1. **Geometry**: the dependencies, the GEOS simplification and `split_at_gaps` (a refactor, so the map is
   unchanged), then `BBox` and `clip_line`, with tests. The Dockerfile installs from the lock file, so check that
   shapely's and numpy's wheels install in the image (they have manylinux wheels; `just build`).
2. **Prefilter**: `prefilter_zoom`, `prefilter_margin_m`, `tile_features`, `BlobStore.read_track_tiles`, and
   tests.
3. **Document**: `caltopo.py`, `activity_start`, with tests and the fixture.
4. **Endpoints**: the models, `export_candidates`, both handlers, logging, the README API table, and tests.
   This can merge on its own and is usable with `curl`.
5. **Map**: `buttonControl`, the area button, `areaSelect.ts`, and the highlight that takes a list of ids.
6. **Dialog**: `ExportDialog.vue`, the `api.ts` split, settings, `exportFilters` and `sportStyles`. Then the
   CLAUDE.md updates.

Each phase is one commit, and all of them go in one PR to `main`. Before opening it, run `just test` and
`just check`.

## Implementation notes

Where the code differs from the plan above:

- **Commits**: two, the server (phases 1–4) and the frontend (5–6), not one per phase.
- **Test archives**: tests build real archives with tippecanoe (`tracks_archive` in `tests/conftest.py`, skipped
  where tippecanoe isn't installed; CI installs it), rather than a checked-in `tracks.pmtiles`. This also tests
  the flags the prefilter relies on.
- **Names**: `tile_features` is `tile_track_ids`, and `area_track_ids` merges the tiles' results.
  `prefilter_zoom` counts tiles without listing them (a world-sized box would otherwise list 268M z14 tiles).
- **`read_activity`** decodes the streams in a worker thread, so exports (and GPX downloads) don't block the
  event loop.
- **Track limit**: the preview lists every track in the area, even over `EXPORT_TRACKS_LIMIT`, so the dialog can
  say how many there are; only the export refuses.
- **Points without positions**: an activity with no `latlng` stream gives no points.

## Risks

- **The format is undocumented**, and CalTopo can change it. The examples cover what we write, and test imports of
  files in exactly this format worked. All of the format lives in `caltopo.py`, pinned by its fixture.
- **Export time.** Every exported track decodes all of its streams (heart rate, power and so on, not only
  position). If 100 tracks take too long on the shared CPU, there are two cheaper fixes before a background job:
  a smaller `EXPORT_TRACKS_LIMIT`, or a streamed response (send the folder, then each line as it's built, and
  report the points limit from the preview's estimate instead).
- **Memory.** One response holds up to ~43 MB of encoded JSON, plus the streams of the 4 activities in progress.
  That's within the 1 GB machine, alongside tippecanoe rebuilds; the export log's `bytes` shows how close it gets.
- **Stale lines in CalTopo.** Because re-imports are matched by id, a track that changed since it was last
  exported (renamed, restyled, or re-archived with new streams) may keep its old version on the map, if CalTopo
  skips known ids rather than updating them (phase 0). The athlete can delete the line in CalTopo and import
  again. A line the athlete deleted from the map may also be skipped on re-import, if CalTopo remembers deleted
  ids; that would show up in the same phase 0 check.
- **The map's 3,000-track limit** leaves older tracks out of exports, as it does on the map. The dialog repeats
  the map's note when tracks are omitted.
- **The prefilter depends on the archive's build.** If the tippecanoe flags change (dropping features, or more
  simplification), the margin can become too small and tracks can go missing from previews and exports. The
  flags' comment and the tippecanoe-built test guard this.
- **New dependencies.** `pmtiles` and `mapbox-vector-tile` are small, less widely used packages. Both do one
  well-specified job (the PMTiles v3 directory format, and the vector tile protobuf), so either could be replaced
  by a few hundred lines if one is abandoned.

## Later (out of scope)

- GPX as a second format (for Gaia, onX and Garmin), from the same selection.
- Editable box corners, a polygon (lasso), or "export this track" from the popup.
- More styling options (width, opacity, dashed lines for older seasons), and a folder per sport or per year, all
  as fields the request already has room for. CalTopo's dashed style is the `pattern` `"M0 -3 L0 3,,12,F"`.
