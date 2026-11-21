# Choosing tracks in a CalTopo export

Status: implemented (2026-11-20), in PR #19. Over the limit, the newest 100 start ticked. Builds on `docs/plans/caltopo-export.md` (implemented, PR #19).

## Problem

An export is everything in the drawn area that passes the map's filters. In a busy area (a home trailhead, a
resort's sidecountry) that is often over the 100-track limit, or holds laps the athlete doesn't want, and the only
way to narrow it is to redraw the box or change filters, which also changes the map. Athletes should be able to tick
the tracks they want in the export dialog.

## Approach

The preview already returns the candidate ids, newest first, and the map page already holds every activity's
summary (`api.activities()`: name, type, date, vert, distance, used by the popups and filters). So the dialog can
list the candidates with no new data from the server, and the export request carries the chosen ids instead of the
search.

### Server

1. **`TrackExportRequest.activity_ids: list[str] | None = None`** (`models.py`). When it's given, the export holds
   those activities and ignores `sports`/`date_from`/`date_to`; when it's absent, it searches as today (kept for API
   and CLI callers; the README documents both). `bbox` stays required: `whole` mode still drops a track that misses
   the area, and `clip` mode needs it.
2. **`_export_request`** validates the list: at most `EXPORT_TRACKS_LIMIT` ids (400 "N tracks: exports are limited
   to 100..." as now), each a non-empty string, duplicates dropped keeping order. An empty list is a 400 (no tracks).
3. **`export_tracks`**: `ids = export.activity_ids if export.activity_ids is not None else await export_candidates(...)`.
   Everything after (limit check, the index lookup, `build`, sorting, the points limit) is shared. Ownership needs no
   new check: ids are looked up in the athlete's own index and read under `athletes/{athlete_id}/`, so an id that
   isn't theirs is skipped like a deleted one, and if none remain it's the existing 400 `NO_TRACKS`.
4. **The preview is unchanged.** It already returns every candidate (not just the first 100), so the dialog can
   offer all of them when there are more than the limit.

I'd trust the client's ids rather than re-running the area search on export and intersecting. The search is ~0.5 s,
but intersecting adds nothing for safety (the index lookup already scopes ids to the athlete) and would silently
drop a ticked track if the archive is rebuilt between preview and download.

### Frontend

5. **`api.ts`**: `activity_ids?: string[]` on `TrackExportRequest`.
6. **`ExportDialog.vue`**:
   - New prop `activities: Map<string, ActivitySummary>` (MapView already builds an index for the popups; pass it).
   - State: `excluded = ref(new Set<string>())`, the ids the athlete unticked. Keeping the *unticked* set means a
     re-preview (filters changed) keeps their choices and newly found tracks arrive ticked. Cleared on Redraw.
   - `selected = computed(() => candidates.filter(id => !excluded.has(id)))`; the request sends
     `activity_ids: selected`, so the file is exactly what the list shows.
   - Summary line: "12 of 140 tracks selected · <filters>". Download is enabled for 1 to `limit` selected; over the
     limit it says "Untick N more, or narrow the area" instead of the current dead-end `over` state. The `over`
     state goes away (`ready` covers it, with the button disabled).
   - A "Choose tracks" disclosure under the summary, opening a scrolling list (max-height ~40vh): a checkbox per
     track with the sport's color dot, name, date, and vert/distance in the athlete's units (`formatVert`,
     `formatDistance`), newest first. "All" / "None" links above it. It opens by itself when the preview is over the
     limit.
   - `preview` emits the **selected** ids, so the map highlights only the ticked tracks as the athlete edits.
   - Hovering a row emits `focus(id | null)`, and the map draws that track brighter (one extra filter on the
     selected layers), so "Morning ski" vs "Morning ski" can be told apart by where it goes.
7. **`MapView.vue`**: pass `activities`, handle `focus`. Phones: the bottom sheet gets `max-height: 75vh` with the
   list scrolling inside it.

### Over the limit: what is ticked at first

Two options:

- **Nothing over the limit pre-ticked except the newest 100** (recommended): Download works straight away with a
  sensible default, and the summary says which ones ("newest 100 of 140 selected").
- **Everything ticked**, Download disabled until the athlete unticks enough: no surprise about what's left out, but
  the athlete has to do something before every large export.

## Known rough edge

The preview's candidates come from simplified tiles grown by a margin, so a track passing within a few metres of
the box can be listed and then export nothing (`whole`: misses the area; `clip`: no piece inside). That already
happens today; the list just makes it visible. The downloaded notice already reports the real count
(`X-Track-Count`). Fixing it would mean an exact check per candidate at preview time, which needs the full tracks,
so it's out of scope.

## Later (not in this change)

- Clicking a highlighted track on the map toggles it in the list (the map already hit-tests tracks for the popup;
  while the export dialog is open, a click would toggle instead of opening the popup).
- Remembering exclusions across dialogs (e.g. "never export commutes") belongs in the map's filters, not here.

## Tests and docs

- `tests/test_api.py`: export by ids (only those, filters ignored, sorted oldest first in the file as now); ids
  the athlete doesn't own skipped (and all foreign is 400); duplicates collapsed; over 100 ids is 400; empty list
  400; a non-list or non-string id 400; the search path still works with no `activity_ids`.
- Manual check in the browser against the local stack (`i-demo`): untick, re-filter (choices kept), redraw
  (cleared), over-limit area, hover focus, phone width.
- `python/README.md`: the `activity_ids` field in the export row. `Claude.md`: one clause in the frontend note.
  `docs/plans/caltopo-export.md`: a line under implementation notes pointing here.
