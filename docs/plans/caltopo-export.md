# Plan: export an area's tracks to CalTopo

Status: ready to implement (2026-11-20). Branch: `feature/caltopo-export` (from `main`).

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
   simplified `track.geojson.gz`, even though that's slower. The simplified tracks are only used to find out
   quickly *which* activities might be in the area (see "Selecting tracks").
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
first. The dialog shows the count and highlights them. It's computed from the simplified tracks, so it's fast,
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

Shared by both endpoints, in a new `export_candidates()`:

1. **Map tracks.** Load the index (`store.activities`) and the ids that have a track
   (`blob_store.track_activity_ids`), then take `map_track_ids(...).included`. These are the tracks the map
   draws (the 3,000 most recent), so an export never contains a track the athlete couldn't see.
2. **Filters.** Keep the activities whose `type` is in `sports` (or all, if it's empty) and whose
   `feature_date(start_date_local)` is between `date_from` and `date_to`. This is the same test
   `mapFilters.ts` applies on the map.
3. **Prefilter on the simplified tracks.** Read their `track.geojson.gz` (`read_track_features`, 16 at a time)
   and keep those with a segment that meets the box **grown by `PREFILTER_MARGIN_M` = 5 m**. A simplified track
   is never more than 2 m (`TOLERANCE_M`) plus rounding (~0.1 m) from the full track, so the margin makes this
   step safe: a full track in the box always passes. It can only let through a few extra tracks that pass just
   outside the box. That is all the preview does.
4. **Exact test (export only).** Each survivor's full-detail track is clipped to the exact box. A track with
   nothing inside is dropped. In whole mode, the whole track is exported as soon as clipping leaves anything.

The one thing the prefilter can miss: `_segments` drops a lone fix between two GPS dropouts (a "segment" of
one point). A track whose only point in the box is such a fix is missed. That's rare enough to accept and
document.

**Cost.** Step 3 reads one small file per candidate. With no filters, that's up to 3,000. Phase 0 times it on
Fly. If the preview is slow (over ~2 s), phase 6 adds a bounding-box index (see "Later phase").

## Building the tracks

Per selected activity, in order:

1. `blob_store.read_activity()` returns the archived `Activity` and every stream.
2. **Points.** `merge_streams(streams, skip_unknown=True)` is already used by the GPX export. It drops samples
   with no position, and fills gaps in the other streams. A point is `(lon, lat, ele, ms)`:
   - `lon` and `lat` are rounded to 6 decimals (`PRECISION`), as in CalTopo's own tracks.
   - `ele` is the `altitude` stream, rounded to whole meters.
   - `ms` is the activity's start plus the `time` offset, in Unix milliseconds. The start comes from the same
     parsing the GPX export uses: `_start_time` in `intervals.py`, renamed to `activity_start` and made public.
   - If the activity has no `time` stream, points are `(lon, lat, ele)`, and with no `altitude` stream they're
     `(lon, lat)`. CalTopo imports both, and fills a missing elevation profile from its own terrain data, while
     zeros would show up as a fake profile.
3. **Dropouts.** The points are split wherever consecutive fixes are more than `MAX_GAP_M` (200 m) apart, with
   the same rule as the map. `_segments` is generalized so that the map and the export share it: it works on any
   point tuples whose first two values are lon and lat.
4. **Area.** `clip_line(points, bbox)` is applied to each segment.
   - **Whole**: if any segment clips to something, the activity gives **one line of all its points**, unsplit,
     like a track recorded in CalTopo (CalTopo draws a straight line across a dropout). Never a
     `MultiLineString`: CalTopo imports only part of one.
   - **Clip**: every clipped piece becomes its own line. A track that leaves the area and comes back gives
     several lines, titled "… (1/3)", "… (2/3)" and so on.

**Clipping** is Liang–Barsky per segment, about 40 lines with no new dependency:

- A point on the box's edge counts as inside.
- Where a piece enters or leaves the box, a point is interpolated on the edge. All of its values are
  interpolated, so the crossing point gets a matching elevation and time.
- A segment that crosses the box with no point inside still counts.
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
  line or keeps the old one wasn't tested (phase 0 checks it). Either way it doesn't duplicate.

## Limits

Full-detail tracks are big. A 5-hour tour is about 18,000 points, or about 0.8 MB of GeoJSON. Two limits are set
as constants:

- **`EXPORT_TRACKS_LIMIT` = 100.** This bounds the time an export takes: each track is a bucket read plus JSON
  decoding of every stream. The preview reports it, so the dialog can refuse before the download starts. Phase 0
  tunes it on Fly.
- **`EXPORT_POINTS_LIMIT` = 1,000,000** (about 41 MB). This bounds the file, for CalTopo's sake. In the import
  test, CalTopo took about 3 s per 100,000 points (34 s for 1.2M points, 50 MB) and stayed smooth afterwards; a
  forum report failed at 125 MB. So 1M points imports in under half a minute. Points are counted as tracks are
  built, and the export stops with a 400 as soon as the total is over the limit. In clip mode, only the points
  inside the area count. (100 long tours could reach it, but `EXPORT_TRACKS_LIMIT` will usually bind first.)

Tracks are read and built **4 at a time** (`EXPORT_READ_CONCURRENCY`). Decoding and geometry run in
`asyncio.to_thread`, so a long export doesn't block other requests on the single shared CPU. The finished
document is returned in one response (no streaming). That keeps errors such as the point limit as clean 400s.
If phase 0 finds that Fly's proxy times out on long exports, switch to a streamed response (see "Risks").

## Server changes

Paths relative to `python/skintrackr/`.

- **`tracks.py`**
  - `BBox` (frozen attrs): `west, south, east, north`. It has a `from_list()` that validates (raising
    `ValueError`), an `expanded(metres)` method, and a `contains` helper.
  - `split_at_gaps(points)`: the generalized `_segments` logic. `_segments` keeps its signature and calls it, so
    `track_feature` doesn't change.
  - `clip_line(points, bbox) -> list[list[Point]]`, generic over the point's length.
  - `meets(lines, bbox) -> bool`: true if clipping leaves anything. The prefilter and the whole-mode test use it.
  - The constants `PREFILTER_MARGIN_M`, `EXPORT_TRACKS_LIMIT`, `EXPORT_POINTS_LIMIT` and
    `EXPORT_READ_CONCURRENCY`.
- **`clients/intervals.py`**: rename `_start_time` → `activity_start`, now public; its two callers stay the same.
- **`caltopo.py`** (new):
  - `export_points(activity, streams) -> list[tuple]`: step 2 of "Building the tracks".
  - `export_lines(points, bbox, mode) -> list[list[tuple]]`: steps 3 and 4.
  - `CaltopoDocument`: holds the folder id, collects encoded features and counts points (raising
    `ExportTooLarge`), and has `to_bytes()`.
  - `folder_id(athlete_id, name)` and `line_id(athlete_id, activity_id, folder, piece)`: the keys in "Ids".
  - `track_title(...)` and `track_description(...)`.

  The module holds no I/O, so it's tested with plain data.
- **`models.py`**: `TrackExportRequest` and `SportStyle` (attrs, decoded with the shared converter; a decode
  error becomes a 400), plus `ExportPreview` (`activity_ids`, `limit`).
- **`server/api.py`**:
  - `export_preview` and `export_tracks` handlers, added to the module's handler list.
  - `export_candidates(services, athlete_id, request)` lives here or in a small helper beside it, since it
    combines `Store` and `BlobStore` calls the way `list_activities` does.
  - Each step is wrapped in `fail_with`. Over-limit cases raise `ClientException` with the messages above.
  - One INFO log per export with `athlete_id`, `mode`, `tracks`, `points`, `bytes` and `seconds`.
- **Docs**: the two endpoints go in the `python/README.md` API table, and the CLAUDE.md component list gets
  `caltopo.py` and a line about the export.

There are **no stored-format changes**: the export only reads what's already in the bucket and Redis.

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
  - `split_at_gaps` keeps the map's behavior: the existing `track_feature` tests still pass.
  - `BBox.from_list` rejects malformed boxes (parametrized). `expanded` grows the box by about the right number
    of degrees.
  - The prefilter: a full-detail track just inside the box still passes when tested against its simplified
    version.
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
  - Preview and export for activities archived in `FakeBucket`.
  - Sport and date filters apply. Tracks beyond the map limit are left out (a small limit patched in).
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
     random ids and drop decision 7.
   - On Fly (`fly ssh console`), time `read_track_features` for 3,000 tracks, and `read_activity` plus
     `export_points` for 20 activities. This sets `EXPORT_TRACKS_LIMIT` and decides whether phase 6 is needed.
   - Check how long a response can take behind Fly's proxy before it gives up.
1. **Geometry**: `BBox`, `split_at_gaps`, `clip_line`, `meets`, with tests. Refactor only, so the map is
   unchanged.
2. **Document**: `caltopo.py`, `activity_start`, with tests and the fixture.
3. **Endpoints**: the models, `export_candidates`, both handlers, logging, the README API table, and tests.
   This can merge on its own and is usable with `curl`.
4. **Map**: `buttonControl`, the area button, `areaSelect.ts`, and the highlight that takes a list of ids.
5. **Dialog**: `ExportDialog.vue`, the `api.ts` split, settings, `exportFilters` and `sportStyles`. Then the
   CLAUDE.md updates.
6. **Only if phase 0 says the preview is slow**: a derived `athletes/{id}/track-bounds.json.gz`
   (`{activity_id: [w, s, e, n]}`). It's written by the tracks rebuild from the features it already reads, and
   by `migrate build-tracks` for existing athletes. A missing index falls back to step 3 of "Selecting tracks".
   It's a new stored object, so it's documented in the README's stored formats.

Each phase is one commit, and all of them go in one PR to `main`. Before opening it, run `just test` and
`just check`.

## Risks

- **The format is undocumented**, and CalTopo can change it. The examples cover what we write, and test imports of
  files in exactly this format worked. All of the format lives in `caltopo.py`, pinned by its fixture.
- **Export time.** Every exported track decodes all of its streams (heart rate, power and so on, not only
  position). If 100 tracks take too long on the shared CPU, there are two cheaper fixes before a background job:
  a smaller `EXPORT_TRACKS_LIMIT`, or a streamed response (send the folder, then each line as it's built, and
  report the points limit from the preview's estimate instead).
- **Memory.** One response holds up to ~43 MB of encoded JSON, plus the streams of the 4 activities in progress.
  That's within the 1 GB machine, alongside tippecanoe rebuilds, but phase 0 should watch it.
- **Stale lines in CalTopo.** Because re-imports are matched by id, a track that changed since it was last
  exported (renamed, restyled, or re-archived with new streams) may keep its old version on the map, if CalTopo
  skips known ids rather than updating them (phase 0). The athlete can delete the line in CalTopo and import
  again. A line the athlete deleted from the map may also be skipped on re-import, if CalTopo remembers deleted
  ids; that would show up in the same phase 0 check.
- **The map's 3,000-track limit** leaves older tracks out of exports, as it does on the map. The dialog repeats
  the map's note when tracks are omitted.

## Later (out of scope)

- GPX as a second format (for Gaia, onX and Garmin), from the same selection.
- Editable box corners, a polygon (lasso), or "export this track" from the popup.
- More styling options (width, opacity, dashed lines for older seasons), and a folder per sport or per year, all
  as fields the request already has room for. CalTopo's dashed style is the `pattern` `"M0 -3 L0 3,,12,F"`.
