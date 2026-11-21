# Claude.md - Development Guide for Skintrackr

This file provides guidelines and context for Claude Code when working on the Skintrackr project.

## Project Overview

**Skintrackr** (Backcountry Ski Tracker) archives athletes' [intervals.icu](https://intervals.icu) activities to S3-compatible blob storage and keeps them in sync, shows season vert stats and a 3D map, and exports activities as GPX. Athletes connect with their intervals.icu API key, which is stored encrypted in Redis (OAuth may replace the key later).

The backend lives in `python/`: a Python 3.12 [uv](https://docs.astral.sh/uv/) project with an async Litestar server, a click CLI, and attrs/cattrs data types. The frontend lives in `frontend/`: a Vue 3 + Vite + TypeScript SPA (vue-router), kept separate from the server code. The server serves its production build (`frontend/dist`): `/assets/*` with a one-year immutable cache, other build files as-is, and `index.html` (uncached) for every other GET path so the SPA can route it. Stored formats (Redis keys, encrypted API keys, issued JWTs, archived blobs) must stay readable by later versions; changing one ships with a `skintrackr migrate` subcommand (see "Stored formats" and "Migrations" in `python/README.md`).

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

#### **CRITICAL: Separation of Concerns**

1. **All intervals.icu API requests MUST go through `IntervalsClient` in `clients/intervals.py`**
   - Use `request()` (it takes `params=` and `headers=`, and adds the API key as HTTP Basic auth)
   - Get an athlete's client with `services.intervals_for(athlete_id)` (loads their API key)
   - Never call `httpx` directly for intervals.icu outside of `IntervalsClient`

2. **All Redis operations MUST go through `Store` in `clients/store.py`**
   - API key storage and retrieval (encrypted)
   - Encryption/decryption of sensitive data
   - Never access Redis directly outside of `Store`

3. **All blob-storage operations MUST go through `BlobStore` in `clients/blob_store.py`**
   - A boto3 `s3` client against the S3-compatible endpoint in `BlobStorageConfig`, built by `BlobStore.build()`; blocking calls run in `asyncio.to_thread`
   - `archive_activity` is the entry point: it fetches the activity and all its streams via `IntervalsClient`, then uploads them (gzipped, as received) under `athletes/{athleteId}/activities/{activityId}/`
   - Never create a boto3 client or make raw bucket calls outside of `BlobStore`

4. **Authentication**
   - Protected handlers MUST call `authenticate_request()` (in `server/auth.py`) first and use the returned `athlete_id`
   - It accepts the API token as a bearer header (CLI) or the HttpOnly `skintrackr_session` cookie (browser, set by `/auth/connect`); issue tokens with `issue_token()` and build the cookie with `session_cookie()`
   - JWT and encryption primitives live in `crypto.py`; do not hand-roll token or crypto logic elsewhere

5. **Handler Logic Flow**
   - Handlers (`api.py`, `connect.py`, `token_api.py`) → get `Services` with `get_services(state)` → call `IntervalsClient`, `Store`, and `BlobStore` methods
   - Wrap failing steps in `fail_with("message", **context)` rather than hand-written try/except
   - Keep business logic in the appropriate layer; handlers stay thin

### Data Flow

```
Request (API / periodic sync)
        ↓
Handler (api.py / connect.py / token_api.py) or ingest.py
        ↓
  ┌──────────────┬───────────────┬─────────────────┐
  ↓              ↓               ↓                  ↓
IntervalsClient Store           BlobStore         (auth via
(intervals.py)  (store.py)      (blob_store.py)    auth.py +
  ↓              ↓               ↓                  crypto.py)
intervals.icu   Redis           S3-compatible bucket
        ↓
     Response
```

Activities are stored in full in the bucket (source of truth) and as summaries in a Redis index
(`athlete:{id}:activities`, what stats read). The periodic sync (every 15 minutes, activities from the last 30
days) and bulk imports (`server/ingest.py`) write both, in background tasks (`services.spawn`) that are paced and
wait out intervals.icu's rate limits (`RateLimiter` in `intervals.py`; use `IntervalsClient.in_background()` for
background work). Activities intervals.icu lists but doesn't serve (synced to it from Strava) are skipped.
Each archived activity also gets a simplified `track.geojson.gz`, and each athlete a `tracks.pmtiles` of their
3,000 most recent tracks, rebuilt in the background after changes (tippecanoe: `brew install tippecanoe` locally;
the Dockerfile and CI build it).

## Git Workflow & Best Practices

### Branch Strategy (GitHub flow)

Follow [GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow): `main` is the only
long-lived branch, and it's always deployable, since every merge to `main` deploys to production. All work
happens on short-lived branches made from `main` and merged back into it through a pull request. There is no
`develop`, `release/*` or `hotfix/*` branch: an urgent fix is just a small branch and a quick PR.

Name branches by kind, with a short description: `feature/*` for new features, `bugfix/*` for fixes, and
`chore/*`, `docs/*` or `refactor/*` for other changes.

### Workflow Steps

```bash
# 1. Branch from an up-to-date main
git checkout main
git pull origin main
git checkout -b feature/descriptive-name

# 2. Work in small commits with descriptive messages
git add <files>
git commit -m "feat: descriptive commit message"

# 3. Push and open a PR into main (early, as a draft, if you want feedback)
git push -u origin feature/descriptive-name
gh pr create --base main

# 4. Address review comments with more commits on the same branch; CI runs on every push

# 5. Once approved and green, merge the PR (this deploys) and delete the branch
```

### Commit Message Convention

Follow conventional commits:
- `feat:` - New feature
- `fix:` - Bug fix
- `refactor:` - Code refactoring
- `test:` - Adding/updating tests
- `docs:` - Documentation changes
- `chore:` - Maintenance tasks
- `perf:` - Performance improvements

### Pull Request Process

**ALWAYS create PRs instead of pushing directly to main**

1. **Before Creating PR**
   ```bash
   just test    # pytest
   just check   # ty type check
   ```

2. **PR Title Format**
   - `feat: Add activity search`
   - `fix: Correct API key validation errors`
   - `refactor: Standardize intervals.icu API requests`

3. **PR Description Should Include**
   - What: Brief description of changes
   - Why: Reason for the change
   - How: Implementation approach
   - Testing: How it was tested
   - References: Related issues/tickets

4. **Create PR using gh CLI**
   ```bash
   gh pr create --title "feat: Add feature name" --body "Description of changes"
   ```

5. **Never push directly to main**
   - All changes go through PR review process
   - Even for small fixes, create a PR

## Build, Test & Deploy

### Local Development

Recipes in the root `justfile`; `py-*` recipes run in `python/`, and `test`/`check` aggregate across the repo (and are what CI runs).

```bash
just py-sync       # uv sync (installs dev tools too)
just deps-up       # docker compose: just Redis (:6379) and SeaweedFS (:8333, the local S3 stand-in, bucket created)
just py-serve      # run the server against deps-up, with the rest of .env loaded
just py-cli ...    # a skintrackr command (e.g. `migrate ...`) against deps-up
just test          # all tests (py-test: uv run pytest)
just check         # all static checks (py-check: uv run ty check)
just build         # build the production Docker image
just dev-up        # docker compose: app + Redis + SeaweedFS on http://localhost:8080
just compose-logs  # follow logs
just dev-down      # stop the stack
just compose-restart  # restart just the app
```

Direct equivalents, from `python/`: `uv run pytest`, `uv run pytest tests/test_api.py -v`, `uv run pytest --cov`
(if pytest-cov is installed), `uv run skintrackr serve --port 8080`.

### Deployment to Fly.io

CI (`.github/workflows/ci.yml`) runs `just test` and `just check` on every PR. Pushes to `main` run CI and then deploy (`.github/workflows/fly-deploy.yml`, `flyctl deploy --remote-only`), building the top-level `Dockerfile`, which builds the frontend in a Node stage and bakes `frontend/dist` into the Python image.

```bash
fly deploy      # deploy by hand
fly status      # check machines
fly logs        # view logs
fly secrets set APP_SECRET=$(openssl rand -hex 32)   # first-time setup; also UPSTASH_REDIS_URL,
                # AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
```

### Environment Setup

Required environment variables (see `.env.example` and `python/README.md`):
- `APP_SECRET` - hex string, at least 32 bytes decoded (`openssl rand -hex 32`)
- `UPSTASH_REDIS_URL` - Redis connection string
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - Keys for the S3-compatible blob store
- `VITE_MAPTILER_KEY` - (frontend build only) public MapTiler key for the map page; a Docker build arg, from `.env` locally and a GitHub secret in the deploy workflow

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
- Pages: `/` (landing), `/connected`, `/stats` (season vert), `/map` (MapLibre GL + MapTiler Outdoor and terrain-RGB, 3D terrain on; the worker URL is set explicitly with `setWorkerUrl`). The map draws the athlete's tracks from `/api/tracks.pmtiles` (the `pmtiles` protocol), colored by sport (`src/sports.ts`); clicking opens `ActivityPopup`, and `MapFilters` narrows by sport and calendar year (`src/mapFilters.ts` holds the shared filter logic). The area button (`src/areaSelect.ts`) draws a box, and `ExportDialog` previews and downloads its tracks for CalTopo, with the map's filters (`exportFilters`) and colors (`sportStyles`). `MAP_TRACKS_LIMIT` in `src/api.ts` mirrors the server's
- Server tests use a stand-in build (`frontend_dir` fixture), so they don't need `npm run build`

## Testing Guidelines

### Unit Test Requirements

1. **All new features MUST have unit tests** in `python/tests/test_<module>.py`
2. **Never make real network calls** - use the fakes in `python/tests/conftest.py`: `FakeIntervals` (`httpx.MockTransport`), fakeredis, and `FakeBucket` (an in-memory moto bucket), plus the `client` fixture (Litestar `TestClient`)
3. Use `pytest.mark.parametrize` for multiple scenarios
4. Critical paths (auth, API key storage) must be well-tested; keep the stored-format fixtures in `tests/test_crypto.py` and `tests/test_gpx.py` passing

## Code Style & Standards

### **CRITICAL: Compact, Organized, Non-Duplicated Code**

Every change MUST prioritize the following, in order:

1. **Do not duplicate code that already exists.** Before writing anything, look for an existing helper, method, or pattern that already does the job and reuse it. If similar logic exists in two places, factor out a shared function rather than copy-pasting. This is the single most important rule — duplicated logic is the primary thing to avoid.
2. **Be consistent with the surrounding code.** Match the existing style, naming, error-handling (`fail_with`), logging, and architectural layering (`IntervalsClient` / `Store` / `BlobStore`). New code should be indistinguishable from the code already in the file.
3. **Stay compact and organized.** Prefer the smallest clear change that solves the problem. Keep handlers thin, push logic into the appropriate layer, and avoid speculative abstraction or unnecessary scaffolding.

When a new feature would otherwise duplicate existing behavior, refactor the shared piece instead — leave the codebase smaller and more cohesive, not larger.

### Python Conventions

1. **Fully typed** - `just check` (`ty`) must pass; annotate every function
2. **Async I/O** - httpx, `redis.asyncio`; run blocking calls (boto3) with `asyncio.to_thread`
3. **Data types** - attrs classes in `models.py`, (un)structured with the shared cattrs `converter`
4. **Docstrings** - Google style (`Args:`, `Raises:`) on public functions and classes
5. **Errors** - raise or let errors propagate; in handlers use `fail_with()` so they are logged and become `{"message": ...}` responses

### Project-Specific Guidelines

1. **HTTP Client Usage**
   - ✅ Use `IntervalsClient` methods (or `IntervalsClient.request()`)
   - ❌ Never use `httpx` directly for intervals.icu

2. **API Key Management**
   - ✅ Use `Store.fetch_api_key()`, `Store.save_api_key()` (or `services.intervals_for()`)
   - ❌ Never access Redis directly outside `Store`

3. **Blob Storage**
   - ✅ Use `BlobStore` methods (e.g. `archive_activity()`)
   - ❌ Never create a boto3 client or call the bucket directly outside `BlobStore`

4. **Configuration**
   - Use environment variables via `Config.from_env()`
   - Never hardcode secrets or URLs

5. **Logging**
   - `logger = get_logger(__name__)`; structured context via `extra=`: `logger.info("saved API key", extra={"athlete_id": athlete_id})`
   - Include relevant context (athlete_id, activity_id, etc.)
   - Log errors before returning or raising them (`fail_with` does this for handlers)

6. **Stored formats are frozen** - Redis keys (including `athlete:{id}:intervals-key`/`:activities`/`:import`), ciphertext layout, JWT claims (`athlete_id` is the intervals.icu id, a string) and the bucket layout (`athletes/{id}/activities/{id}/activity.json.gz` and `streams.json.gz`, intervals.icu's JSON as received; the derived `track.geojson.gz` and `athletes/{id}/tracks.pmtiles`) must stay compatible with existing data; a change ships with a `skintrackr migrate` subcommand in `cli/migrate.py`, and migrations stay there after they've run

## Common Tasks

### Adding a New intervals.icu API Endpoint

1. Add an async method to `IntervalsClient` in `clients/intervals.py`
2. Use `self.request()` and decode the body with `_decode()` (the shared cattrs `converter`)
3. Add unit tests in `tests/test_intervals.py` (extend `FakeIntervals`)
4. Document the method with a docstring

Example:
```python
async def get_athlete(self) -> Athlete:
    """The authenticated athlete."""
    return _decode(await self.request("GET", f"{API_URL}/athlete"), Athlete)
```

### Adding Redis Operations

1. Add an async method to `Store` in `clients/store.py`
2. Use `self.redis`; encrypt sensitive values with `crypto.encrypt`/`decrypt`
3. Log errors with structured context
4. Write tests (fakeredis)

### Adding Blob Storage Operations

1. Add a method to `BlobStore` in `clients/blob_store.py`, reusing `put()` and the existing key helpers
2. Log errors and raise them to the caller
3. Write tests (against `FakeBucket`'s in-memory bucket)

### Adding New HTTP Handlers

1. Add the handler to the most relevant module in `python/skintrackr/server/` (reuse an existing module rather than creating a new one) and to its `*_handlers` list or router
2. Make sure the list/router is registered in `create_app()` in `server/app.py`
3. Authenticate with `authenticate_request()` for protected endpoints
4. Reuse existing `IntervalsClient`, `Store`, and `BlobStore` methods — do not duplicate their logic in the handler
5. Raise `HTTPException` (or use `fail_with`) for error statuses

Note: every unmatched GET path serves the landing page (SPA fallback), so a new GET route that is mistyped returns the landing page instead of 404.

## Troubleshooting

- **Dependencies out of date:** `just py-sync` (`uv lock --upgrade` to bump)
- **Missing settings:** startup fails with `ConfigError` naming the variable
- **Docker:** `docker compose down -v && docker compose build --no-cache && docker compose up`
- **Fly.io:** `fly logs`, `fly secrets list`, `fly config validate`
- **intervals.icu responses:** set `DEBUG_INTERVALS_RESPONSE_BODY=true` to dump bodies to `.request-debug-*.log`

## Security Considerations

1. **Never commit secrets** - Use environment variables
2. **Encrypt sensitive data at rest** - `Store` encrypts API keys with `crypto.encrypt`/`decrypt`
3. **Validate API keys** - `connect_athlete()` checks a key with intervals.icu before storing it
4. **Use HTTPS** - Enforced in production via Fly.io
5. **Revoked keys** - intervals.icu answers 401 once a key is regenerated; imports report it (`RECONNECT_MESSAGE`) and the athlete reconnects

## Resources

- **intervals.icu API Docs**: https://intervals.icu/api-docs.html (spec: https://intervals.icu/api/v1/docs)
- **Fly.io Docs**: https://fly.io/docs/
- **Litestar**: https://docs.litestar.dev/
- **uv**: https://docs.astral.sh/uv/
- **attrs / cattrs**: https://www.attrs.org/ , https://catt.rs/
- **redis-py (asyncio)**: https://redis.readthedocs.io/
- **boto3 (S3)**: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html

---

**Remember**: Write compact, organized code that reuses what already exists and stays consistent with the surrounding style — never duplicate logic. Maintain separation of concerns (intervals.icu API in `clients/intervals.py`, Redis in `clients/store.py`, blob storage in `clients/blob_store.py`, auth/crypto in `server/auth.py`/`crypto.py`), run `just test` and `just check` before creating PRs, and follow GitHub flow (branch from `main`, merge back by PR) for all changes.
