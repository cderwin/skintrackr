# Claude.md - Development Guide for Skintrackr

This file provides guidelines and context for Claude Code when working on the Skintrackr project.

## Project Overview

**Skintrackr** (Backcountry Ski Tracker) archives athletes' [intervals.icu](https://intervals.icu) activities to S3-compatible blob storage and keeps them in sync, shows season vert stats and a 3D map, and exports activities as GPX. Athletes connect with their intervals.icu API key, which is stored encrypted in Redis (OAuth may replace the key later).

The backend lives in `python/`: a Python 3.12 [uv](https://docs.astral.sh/uv/) project with an async Litestar server, a click CLI, and attrs/cattrs data types. The frontend lives in `frontend/`: a Vue 3 + Vite + TypeScript SPA (vue-router), kept separate from the server code. The server serves its production build (`frontend/dist`): `/assets/*` with a one-year immutable cache, other build files as-is, and `index.html` (uncached) for every other GET path so the SPA can route it.

## Architecture Overview

### Core Components

Paths below are relative to `python/`.

1. **skintrackr/server/app.py** - `create_app()`: routes, serving the frontend build, lifespan (builds `Services`), error handlers
2. **skintrackr/server/services.py** - `Services` (config, `Store`, `IntervalsClient`, `BlobStore`) on `app.state`, and tracked background tasks (`spawn`)
3. **skintrackr/clients/intervals.py** - ALL intervals.icu API interactions via `IntervalsClient`, and its `RateLimiter`
4. **skintrackr/clients/store.py** - ALL Redis operations (encrypted API keys, JWT records, activity index, imports) via `Store`
5. **skintrackr/clients/blob_store.py** - ALL blob-storage (S3-compatible) operations via `BlobStore`, and the archive layout; `archive_activity` downloads an activity and its streams from intervals.icu and uploads them gzipped
6. **skintrackr/server/connect.py** - `/auth/connect` (API key form), `/auth/logout`, `/auth/disconnect`; `connect_athlete()` is shared with `/token/new`
7. **skintrackr/server/ingest.py** - Ingesting activities into the bucket and the Redis index: `ingest_activity`, bulk imports (`start_import`, resumed at startup), the periodic sync (`run_sync_loop`/`sync_athlete`; it stands in for webhooks, which intervals.icu only sends to registered OAuth apps), `remove_activity`/`remove_athlete`
8. **skintrackr/server/api.py** - Authenticated activity API (`export_track`, `persist_activity`, `import_start`/`import_status`, `vert_stats`, the map's `list_activities`/`map_tracks`, and CalTopo exports: `export_preview`/`export_tracks`, which find an area's tracks with `export_candidates`)
9. **skintrackr/server/token_api.py** - API token issue/verify/revoke handlers; **server/auth.py** has `authenticate_request`
10. **skintrackr/crypto.py** - JWT signing/verification and NaCl secretbox encryption
11. **skintrackr/config.py** - Environment configuration (`Config`, `BlobStorageConfig`)
12. **skintrackr/server/errors.py** - `{"message": ...}` error responses and `fail_with()`
13. **skintrackr/models.py**, **gpx.py**, **logging.py** - attrs types + cattrs converter, GPX generation, JSON-lines logging
14. **skintrackr/vert.py** - `year_to_date()`: year-to-date vert from an athlete's activity index
15. **skintrackr/tracks.py** - map tracks: `track_feature()` (simplified GeoJSON per activity), `map_track_ids()` (the `MAP_TRACKS_LIMIT` = 3,000 most recent), `build_pmtiles()` (runs tippecanoe); rebuilds are scheduled by `schedule_tracks` in `server/ingest.py`. Also areas for exports (shapely and numpy): `BBox`, `split_at_gaps`, `clip_line`, and the prefilter that finds an area's tracks in the archive's tiles (`tile_track_ids`), which relies on `TIPPECANOE_FLAGS`
16. **skintrackr/caltopo.py** - CalTopo exports: full-detail `[lon, lat, ele, ms]` lines from archived streams (`export_points`, `export_lines`), and `CaltopoDocument` (stable `uuid5` ids; the format is in `docs/caltopo.md`, pinned by `tests/fixtures/caltopo-export.json`)
17. **skintrackr/cli/** - click commands: `serve`, and `migrate <name>` (one-off data migrations, kept after they've run)

### Architecture Rules

1. **intervals.icu API requests go through `IntervalsClient` in `clients/intervals.py`**
   - Use `request()` (it takes `params=` and `headers=`, and adds the API key as HTTP Basic auth); decode bodies with `_decode()` (the shared cattrs `converter`); tests extend `FakeIntervals`
   - Get an athlete's client with `services.intervals_for(athlete_id)` (loads their API key)

2. **Redis operations go through `Store` in `clients/store.py`**, including encrypting and decrypting API keys (`crypto.encrypt`/`decrypt`)

3. **Blob-storage operations go through `BlobStore` in `clients/blob_store.py`**
   - A boto3 `s3` client against the S3-compatible endpoint in `BlobStorageConfig`, built by `BlobStore.build()`; blocking calls run in `asyncio.to_thread`; new methods reuse `put()` and the key helpers
   - `archive_activity` is the entry point: it fetches the activity and all its streams via `IntervalsClient`, then uploads them (gzipped, as received) under `athletes/{athleteId}/activities/{activityId}/`

4. **Authentication**
   - Protected handlers call `authenticate_request()` (in `server/auth.py`) first and use the returned `athlete_id`
   - It accepts the API token as a bearer header (CLI) or the HttpOnly `skintrackr_session` cookie (browser, set by `/auth/connect`); issue tokens with `issue_token()` and build the cookie with `session_cookie()`
   - JWT and encryption primitives live in `crypto.py`; do not hand-roll token or crypto logic elsewhere

5. **Handlers**
   - Handlers (`api.py`, `connect.py`, `token_api.py`) → get `Services` with `get_services(state)` → call `IntervalsClient`, `Store`, and `BlobStore` methods; handlers stay thin
   - Add a handler to the most relevant existing module and its `*_handlers` list, registered in `create_app()`
   - Wrap failing steps in `fail_with("message", **context)` rather than hand-written try/except
   - Every unmatched GET path serves the SPA, so a mistyped GET route returns the landing page instead of 404

### Data Flow

Activities are stored in full in the bucket (source of truth) and as summaries in a Redis index
(`athlete:{id}:activities`, what stats read). The periodic sync (every 15 minutes, activities from the last 30
days) and bulk imports (`server/ingest.py`) write both, in background tasks (`services.spawn`) that are paced and
wait out intervals.icu's rate limits (`RateLimiter` in `intervals.py`; use `IntervalsClient.in_background()` for
background work). Activities intervals.icu lists but doesn't serve (synced to it from Strava) are skipped.
Each archived activity also gets a simplified `track.geojson.gz`, and each athlete a `tracks.pmtiles` of their
3,000 most recent tracks, rebuilt in the background after changes (tippecanoe: `brew install tippecanoe` locally;
the Dockerfile and CI build it).

## Git

GitHub flow, with these project specifics (they override the global defaults):
- Every merge to `main` deploys to production (Fly.io, via `.github/workflows/fly-deploy.yml`).
- Branches: `feature/*`, `bugfix/*` (not `fix/*`), `chore/*`, `docs/*`, `refactor/*`.
- Commits and PR titles use conventional-commit prefixes (`feat:`, `fix:`, `refactor:`, `test:`, `docs:`,
  `chore:`, `perf:`) followed by a lowercase imperative, e.g. `feat: draw every activity on the map`.
- Run `just test` and `just check` before opening a PR; CI (`.github/workflows/ci.yml`) runs both.

## Build, Test & Deploy

### Local Development

Recipes in the root `justfile`; `py-*` recipes run in `python/`, and `test`/`check` aggregate across the repo (and are what CI runs).

```bash
just py-sync       # uv sync (installs dev tools too)
just deps-up       # docker compose: just Redis (:6379) and SeaweedFS (:8333, the local S3 stand-in, bucket created)
just py-serve      # run the server against deps-up, with the rest of .env loaded
just py-cli ...    # a skintrackr command (e.g. `migrate ...`) against deps-up
just test          # all tests (py-test: uv run pytest)
just check         # all static checks: ty and vue-tsc
just build         # build the production Docker image
just dev-up        # docker compose: app + Redis + SeaweedFS on http://localhost:8080
just dev-watch     # the same, rebuilding the app on frontend/server changes (Compose 2.22+)
just compose-logs  # follow logs
just dev-down      # stop the stack
just compose-restart  # restart just the app
```

Direct equivalents, from `python/`: `uv run pytest`, `uv run pytest tests/test_api.py -v`, `uv run pytest --cov`
(if pytest-cov is installed), `uv run skintrackr serve --port 8080`.

The deploy builds the top-level `Dockerfile`, which builds the frontend in a Node stage and bakes `frontend/dist` into the Python image. `VITE_MAPTILER_KEY` (the map page's public MapTiler key) is a Docker build arg: from `.env` locally, a GitHub secret in the deploy workflow. Other settings are in `.env.example` and `python/README.md`.

The blob-storage bucket name and endpoint default (in `BlobStorageConfig` in `config.py`) to production's; `S3_BUCKET_NAME`/`S3_ENDPOINT_URL` override them. Local runs (`just dev-up`, `py-serve`, `py-cli`) always use the local SeaweedFS and Redis from `docker-compose.yml`, with the fixed credentials in `seaweedfs/s3.json`, never production's: keep it that way, and don't run the server or migrations locally against the production bucket.

### Frontend

```bash
just web-install     # npm ci
just web-dev         # Vite dev server on :5173 with hot reload; proxies server routes to `just py-serve` on :8080
just web-build       # production build into frontend/dist (what `just py-serve` serves)
just web-typecheck   # vue-tsc (part of `just check`)
```

- Pages are views in `frontend/src/views/`, registered in `frontend/src/router.ts`; shared pieces go in `src/components/` (e.g. `PageCard.vue`), global styles in `src/assets/main.css`
- Server routes (`/auth/...`, `/token/...`, `/api/...`) are plain links or `fetch` calls, not router links; add any new top-level server prefix to the proxy list in `vite.config.ts`
- The `/connect` page posts the API key to `/auth/connect` (`api.connect`), which signs the browser in (session cookie), then routes to `/connected`; `SYNC_WINDOW_DAYS` in `src/api.ts` mirrors the server's default
- Call the server through `src/api.ts` (it sends the cookie; 401/403 become `SignedOutError`); `useSignedIn()` tells whether the browser is signed in
- Per-browser preferences (vert goal, units, map filters) live in `src/settings.ts` (reactive, saved to `localStorage`); format vert with `formatVert()` and distances with `formatDistance()`
- Pages: `/` (landing), `/connected`, `/stats` (season vert), `/map` (MapLibre GL + MapTiler Outdoor and terrain-RGB, 3D terrain on; the worker URL is set explicitly with `setWorkerUrl`). The map draws the athlete's tracks from `/api/tracks.pmtiles` (the `pmtiles` protocol), colored by sport (`src/sports.ts`); clicking opens `ActivityPopup` (GPX export and intervals.icu/Strava links), and `MapFilters` narrows by sport and calendar year (`src/mapFilters.ts` holds the shared filter logic). The export button beside the filters draws a box (`src/areaSelect.ts`), and `ExportDialog` previews its tracks with the map's filters (`exportFilters`), lists them to tick, and downloads the ticked ones (`activity_ids`) for CalTopo in the map's colors (`sportStyles`). `MAP_TRACKS_LIMIT` in `src/api.ts` mirrors the server's
- Server tests use a stand-in build (`frontend_dir` fixture), so they don't need `npm run build`

## Testing Guidelines

1. **New features get unit tests** in `python/tests/test_<module>.py`
2. **Never make real network calls** - use the fakes in `python/tests/conftest.py`: `FakeIntervals` (`httpx.MockTransport`), fakeredis, and `FakeBucket` (an in-memory moto bucket), plus the `client` fixture (Litestar `TestClient`)
3. Use `pytest.mark.parametrize` for multiple scenarios
4. Critical paths (auth, API key storage) must be well-tested; keep the stored-format fixtures in `tests/test_crypto.py` and `tests/test_gpx.py` passing

## Code Style & Standards

Reuse `IntervalsClient`/`Store`/`BlobStore` methods and `fail_with` rather than re-implementing them; when a feature would duplicate existing behavior, refactor the shared piece and leave the codebase smaller, not larger.

1. **Data types** - attrs classes in `models.py`, (un)structured with the shared cattrs `converter`
2. **Errors** - raise or let errors propagate; in handlers use `fail_with()` so they are logged and become `{"message": ...}` responses
3. **Configuration** - environment variables via `Config.from_env()`; never hardcode secrets or URLs
4. **Logging** - `logger = get_logger(__name__)`, with context (athlete_id, activity_id, ...) via `extra=`: `logger.info("saved API key", extra={"athlete_id": athlete_id})`; log errors before returning or raising them (`fail_with` does this for handlers)
5. **Stored formats are frozen** - Redis keys (including `athlete:{id}:intervals-key`/`:activities`/`:import`), ciphertext layout, JWT claims (`athlete_id` is the intervals.icu id, a string) and the bucket layout (`athletes/{id}/activities/{id}/activity.json.gz` and `streams.json.gz`, intervals.icu's JSON as received; the derived `track.geojson.gz` and `athletes/{id}/tracks.pmtiles`) must stay compatible with existing data; a change ships with a `skintrackr migrate` subcommand in `cli/migrate.py`, and migrations stay there after they've run (see "Stored formats" and "Migrations" in `python/README.md`)

## Troubleshooting

- **Dependencies out of date:** `just py-sync` (`uv lock --upgrade` to bump)
- **Missing settings:** startup fails with `ConfigError` naming the variable
- **Docker:** `docker compose down -v && docker compose build --no-cache && docker compose up`
- **intervals.icu responses:** set `DEBUG_INTERVALS_RESPONSE_BODY=true` to dump bodies to `.request-debug-*.log`
- **Revoked keys:** intervals.icu answers 401 once a key is regenerated; imports report it (`RECONNECT_MESSAGE`) and the athlete reconnects

intervals.icu API docs: https://intervals.icu/api-docs.html (spec: https://intervals.icu/api/v1/docs)
