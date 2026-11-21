# CalTopo's GeoJSON format

How [CalTopo](https://caltopo.com) represents map objects in GeoJSON, so skintrackr can export tracks that import
cleanly. CalTopo doesn't publish this format. These notes (2026-11-20) come from four sources, and each claim is
marked with where it came from:

- **Export**: seen in three real CalTopo exports (`caltopo_examples/` at the repo root; see "The examples").
- **Import test**: seen importing test files we wrote into scratch maps, on the web and the mobile app
  (2026-11-20; see "The examples").
- **Docs**: stated in CalTopo's [training docs](https://training.caltopo.com/all_users/import-export).
- **Library**: read from [`caltopo_python`](https://github.com/ncssar/caltopo_python), an unofficial client of
  the API CalTopo's web app uses (its docs say the API "can change at any time").

## Importing a file (docs, import test)

From [Importing Objects](https://training.caltopo.com/all_users/import-export/import):

- CalTopo imports **GPX, KML and GeoJSON**. The docs say the file must be named **`.gpx`, `.json` or `.kml`**, and
  that the mobile app lets you pick other files, but "nothing will happen". In the import test, though, a
  `.geojson` file imported fine on both the web and the mobile app. Name exports `*.json` anyway: it's what the docs
  promise to accept.
- The Import button is at the top of the map objects sidebar. It works on saved and unsaved maps.
- The import dialog lists every object in the file, all checked. You can uncheck them one by one or by kind
  (markers, shapes), and rename them before importing. A distance column marks objects in view (green), within a
  mile (yellow) or farther away (red, with the distance). The last column shows each line's length.
- There's no folder picker, and no "import as layer" option. Objects keep the folders the file gives them.
- **Only GeoJSON keeps folders**: "To import a set of objects and preserve folders and organization of items, you
  will need to have a file in the GeoJSON format." GPX and KML objects go to the Markers or Lines and Polygons
  folders, keeping "color and style if the file contains such info and if the style is available in CalTopo".
- CalTopo's own GeoJSON export is "a full back-up … including line and marker styles, and folder
  organization". A file shaped like that export is the most complete import there is.

## The document (export)

A `FeatureCollection`: `{"features": [...], "type": "FeatureCollection"}`, serialized compactly, with no other
top-level keys. Every object is a `Feature` with exactly four keys:

| Key | Value |
| --- | --- |
| `type` | `"Feature"` |
| `id` | A lowercase UUID (v4 format), unique within the file |
| `geometry` | GeoJSON geometry, or `null` for folders |
| `properties` | The object's attributes; `properties.class` says what kind of object it is |

All three exports list folders first, then the other objects in no particular order.

### Classes (export)

| `class` | Geometry | In the examples |
| --- | --- | --- |
| `Folder` | `null` | 11 |
| `Shape` | `LineString` (a line) or `Polygon` (an area) | 289, all `LineString` |
| `Marker` | `Point` | 94 |

The library also lists `AppTrack`, `LiveTrack`, `MapMediaObject`, `OperationalPeriod`, `Assignment`, `Clue`,
`Resource` and `SmsLocationRequest`, which are mostly for search and rescue. A track exports as a plain `Shape`, so
an export never needs those.

### Lines: class `Shape` (export)

| Property | Type | Seen | Notes |
| --- | --- | --- | --- |
| `class` | `"Shape"` | always | |
| `title` | string | always | The name in the sidebar and the map label. |
| `description` | string | usually | Free text; `""` when empty. Newlines are kept on import and shown as line breaks (import test). |
| `stroke` | `"#RRGGBB"` | usually | Line color. Upper or lower case hex, both seen. `null` or absent on a few lines; CalTopo then draws its default red. |
| `stroke-width` | int | usually | Pixels. `2` on nearly all lines; `4` and `8` seen. |
| `stroke-opacity` | number 0–1 | often | `1` in every example. |
| `pattern` | string | often | `"solid"` in every example. A line made dashed in the UI exports as `"M0 -3 L0 3,,12,F"` (import test): a pattern spec (an SVG-like path, then spacing), not a name. Only that one dashed style was recorded. |
| `fill` | `"#RRGGBB"` | often | Present on many lines, always equal to `stroke`. It's left over from the polygon style and doesn't matter for a line. |
| `fill-opacity` | number | sometimes | `0.1` or `0.2`; same leftover. |
| `folderId` | UUID | when in a folder | The `id` of a `Folder` feature in the same file; absent at the top level. |
| `gpstype` | `"TRACK"` or `"ROUTE"` | sometimes | Set on lines imported from GPX (`<trk>` or `<rte>`). Not needed for tracks to work. |
| `updated`, `creator`, `-created-on`, `-created-by`, `-updated-on`, `-updated-by`, `-originally-created-on`, `-originally-created-by` | ms timestamps, 6-character account ids | mostly | Bookkeeping that CalTopo adds. Leave these out of a file that is meant to be imported. |
| `name`, `timestamp` | string, ms | rarely | Only on a few lines imported from KML (one description reads `gx_image_links:`, a Google Earth artifact). Ignore them. |

Only `title` appears on every line in the examples (`class` does too, but a `LineString` without `class` still
imports as a line, with its elevation and times: import test). Every style property is sometimes missing, so
CalTopo has defaults for all of them: 2 px, solid, opaque, red.

### Coordinates (export)

`[lon, lat]` as usual, plus two optional values per point:

| Values per point | Meaning | Seen on |
| --- | --- | --- |
| 2: `[lon, lat]` | A drawn line | Hand-drawn routes and ski lines (most lines) |
| 3: `[lon, lat, ele]` | With elevation | Some imported tracks |
| 4: `[lon, lat, ele, time]` | With elevation and time: a recorded track | Every recorded track (12 lines, up to 49,389 points) |

All three import (import test). Only 4-value lines show times. A 2-value line still gets an elevation profile, from
CalTopo's own terrain data, and in the test its total gain matched the same track's 4-value copy. So leaving
elevation out costs little, but writing zeros for missing elevation would put a wrong profile on the line.

- `ele` is **meters**, as an integer (e.g. Moosilauke runs from 473 to 1,349).
- `time` is **Unix milliseconds** (UTC), and increases strictly along the line.
- Longitude and latitude have 6 decimals on recorded tracks (~0.1 m). Drawn lines have full float precision.
- Every point in a line has the same number of values.
- Recorded tracks are **full detail, 1 per second**. CalTopo doesn't simplify them.
- **A recorded track is one `LineString`, even across pauses.** The "Pemi Loop" has a 34-minute gap in time and
  stays one line. No example has a `MultiLineString`.
- **Don't write `MultiLineString`.** It imports, but only part of the track shows up, apparently just the last
  part (import test). Write separate `LineString`s instead.
- Markers always have 4 values, `[lon, lat, 0, 0]`.

So a full-detail skintrackr track is a `Shape` whose `LineString` has `[lon, lat, ele, time]` points. That's
the same as a track recorded in CalTopo or imported from a GPX with times.

### Folders (export)

```json
{"geometry": null, "id": "df7eed85-43af-42c4-9b78-cefed8abfc92", "type": "Feature",
 "properties": {"class": "Folder", "title": "Lines (skied)", "visible": true, "labelVisible": true}}
```

- `title` is the folder's name. `visible` was `true` on every folder. `labelVisible` (`true`) was on most
  folders, and absent on older ones.
- An object is in a folder when its `folderId` is that folder's `id`. All 100 `folderId`s in the examples point to
  a folder in the same file. A line whose `folderId` matches no folder imports into the default "Lines & Polygons"
  folder (import test).
- **Folders are one level deep.** No folder in the examples has a `folderId`, and the library's `addFolder` has no
  parent. A folder written with a `folderId` imports as a separate top-level folder, not nested (import test).

### Ids on import (import test)

**CalTopo keeps a file's `id`s, and it recognizes objects by `id`.**

- Exporting an imported line gives it back with the `id` it had in the file.
- Importing the same file twice into one map adds nothing the second time: there's still 1 folder and 3 lines.
- A folder with the same title but a different `id` is a new folder. Importing a second file whose "skintrackr
  exports" folder had a new random `id` left the map with two folders of that name. The two folders differed only
  by `id`, so matching is by `id`, not by title or contents.
- So a line is duplicated whenever its `id` is new, even if it's the same track. A line with a fixed `id` derived
  from its activity sat next to the same track imported earlier with a random `id`.

- Re-importing an `id` with changed contents **updates** the object: a line whose properties were edited in the
  file took the new ones on re-import.
- `id`s aren't global: a file whose `id`s were already imported into one map imports normally into another.

So stable `id`s are safe: a re-export updates the lines already on the map, and imports into any map.

### Markers (export)

`Point` geometry, with `class: "Marker"`, `title`, `description`, `marker-symbol` (always `"point"` in the
examples; the library says there are "well over a hundred" symbols), `marker-color` (hex, **with or without**
`#`), `marker-size` (a number or a numeric string: `"1"`, `1.5`, `"0.75"`), `marker-rotation` (`null`), and
`folderId`. Some markers also carry `stroke` and `stroke-width` (red, 2). The export doesn't need markers.

### Sizes (export, import test, docs)

| Example | Size | Objects | Points in lines | Bytes per point |
| --- | ---: | ---: | ---: | ---: |
| `bear_export.json` | 0.26 MB | 37 | 7,007 | ~38 |
| `nh48_export.json` | 8.1 MB | 84 | 224,630 | ~36 |
| `Snoqualmie_Ski_Atlas_export.json` | 7.9 MB | 273 | 208,126 | ~38 |

A 4-value point (`[-121.423528,47.445607,959,1738512074000]`) is about 43 bytes. A 5-hour ski tour at 1 point per
second is ~18,000 points, so **~0.8 MB of GeoJSON**.

Importing real tracks into a fresh map each time (web):

| File | Lines | Points | Import time |
| ---: | ---: | ---: | ---: |
| 10 MB | 47 | 243,503 | ~7 s |
| 25 MB | 119 | 610,844 | ~17 s |
| 50 MB | 239 | 1,221,522 | ~34 s |

The time grows linearly, at about 0.7 s per MB (about 3 s per 100,000 points). Even at 50 MB the map was only a
little sluggish while loading, and smooth afterwards to pan and inspect lines. A forum user reports that a 125 MB
GeoJSON never reached the import dialog, and CalTopo says very large imports can freeze the map, so the ceiling
lies somewhere between 50 and 125 MB.

## The file skintrackr should write

A file shaped like one CalTopo exported itself, which imports with its folder, styles, elevations and times
(import test):

```json
{"type": "FeatureCollection", "features": [
  {"type": "Feature", "id": "6f0c1b9e-6c0e-4c1e-9a53-2d2c8f3e7a10", "geometry": null,
   "properties": {"class": "Folder", "title": "skintrackr exports", "visible": true, "labelVisible": true}},
  {"type": "Feature", "id": "c2a0a3f4-2b8e-4f5d-8f6e-0e7b5d1c9a22",
   "geometry": {"type": "LineString", "coordinates": [[-121.423528, 47.445607, 959, 1738512074000],
                                                      [-121.423524, 47.445611, 959, 1738512075000]]},
   "properties": {"class": "Shape", "title": "2026-01-04 Snoqualmie Mountain", "description": "…",
                  "folderId": "6f0c1b9e-6c0e-4c1e-9a53-2d2c8f3e7a10",
                  "stroke": "#e11d48", "stroke-width": 3, "stroke-opacity": 1, "pattern": "solid"}}
]}
```

- Write `class`, `title`, `description`, `stroke`, `stroke-width`, `stroke-opacity` and `pattern` on every
  line, and `folderId` when the line is in a folder. Leave out `fill`, `gpstype` and the bookkeeping keys.
- Use 4-value points when the activity has altitude and time, and 2-value points otherwise (no made-up zeros).
- Use one `LineString` per track, never a `MultiLineString`. Several pieces (a track clipped to an area) become
  several lines.
- Separate the description's items with newlines.
- Don't nest folders: one level, or none.
- Choose `id`s knowing CalTopo matches on them (see "Ids on import"). Fresh UUIDs (v4) make every import add new
  objects, including a new folder of the same name each time. `id`s derived from stable keys (e.g. a `uuid5` of the
  athlete and folder name, or of the activity) would put repeated exports into one folder and skip tracks that are
  already on the map.
- Keep files under about 50 MB (about 1.2M points), which imports in about half a minute.

## The examples

`caltopo_examples/` holds three exports of real maps (a race map, a hiking list and a ski atlas). They contain
real tracks and CalTopo account ids, so they stay out of the repository. Tests use a small hand-made sample in
the same shape, built from the tables above.

`caltopo_examples/import/` holds the files used for the import tests, written by
`python/scripts/caltopo_test_files.py` from real full-detail tracks; its `README.md` says what each one tests.
They hold real tracks too, so they also stay out of the repository.

## The API (not used)

skintrackr hands the athlete a file to import, rather than writing into their maps. A direct push would use
CalTopo's unofficial API (library). For the record:

- **Credentials**: an account id, a credential id and a base64 key, from an offline activation
  (`https://caltopo.com/app/activate/offline?redirect=localhost`, then `GET /api/v1/activate?code=…`).
- **Signing**: each request carries `id`, `expires` (ms, ~2 minutes ahead) and `signature`, a base64
  HMAC-SHA256 keyed with the decoded key, over `"{METHOD} {path}\n{expires}\n{body}"`. The body is empty for GET
  and DELETE. In the query string for GET and DELETE; in the form body for POST, next to `json=<feature JSON>`.
- **Endpoints**:
  - `GET /api/v1/map/{map}/since/{ms}`: the map's objects (`0` gets them all)
  - `POST /api/v1/map/{map}/{Class}`: create one; the server assigns the `id`
  - `POST /api/v1/map/{map}/{Class}/{id}`: edit one; `DELETE` the same path deletes it, with no undo
  - `POST /api/v0/map/{map}/save`: create many, as `{"Shape": [...], "Folder": [...]}`
  - `POST /api/v1/acct/{account}/CollaborativeMap`: a new map
  - `GET /api/v1/acct/{account}/since/0`: the account's maps and folders

It would mean storing a CalTopo secret per athlete, and depending on an API that can change without notice, to save
one import step.

## Sources

- CalTopo training: [Import and Export Functions](https://training.caltopo.com/all_users/import-export),
  [Importing Objects](https://training.caltopo.com/all_users/import-export/import),
  [Exporting Objects](https://training.caltopo.com/all_users/import-export/export)
- `caltopo_python`: [docs](https://caltopo-python.readthedocs.io),
  [source](https://github.com/ncssar/caltopo_python/blob/main/caltopo_python/caltopo_python.py)
- CalTopo forum: [size limit on GeoJSON/KML import](https://help.caltopo.com/hc/en-us/community/posts/41225549919131-Is-there-a-size-limit-to-importing-objects-from-geojson-kml),
  [maximum number of markers on import](https://help.caltopo.com/hc/en-us/community/posts/27988260401307-Maximum-number-of-markers-on-import)
  (both refused automated reads; summarized from search results)
- The three exports in `caltopo_examples/`
