# skintrackr server

The Python backend: archives athletes' [intervals.icu](https://intervals.icu) activities to S3-compatible storage
and keeps them in sync, with encrypted API key storage in Redis, a JWT-authenticated API, GPX export and season
vert stats. It also serves the production build of the frontend (`../frontend`, a Vue SPA).

Built with Python 3.12 and [uv](https://docs.astral.sh/uv/): [Litestar](https://litestar.dev) (async),
[click](https://click.palletsprojects.com), [attrs](https://www.attrs.org)/[cattrs](https://catt.rs), httpx,
redis-py (asyncio) and boto3.

## Commands

From the repository root (or the plain `uv run ...` commands in this directory):

```sh
just py-sync    # uv sync: install, including dev tools
just py-serve   # uv run skintrackr serve, with ../.env loaded, against `just deps-up`'s Redis and SeaweedFS (0.0.0.0:8080)
just py-cli     # uv run skintrackr ..., likewise
just py-test    # uv run pytest
just py-check   # uv run ty check
```

`uv run skintrackr migrate --help` lists the data migrations (see [Migrations](#migrations)).

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `APP_SECRET` | yes | | Hex key, at least 32 bytes (`openssl rand -hex 32`); signs JWTs and encrypts stored API keys |
| `UPSTASH_REDIS_URL` | yes | | Redis URL |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | yes | | Blob storage credentials |
| `S3_ENDPOINT_URL` | no | `https://t3.storage.dev` | The S3-compatible endpoint (Tigris); locally, SeaweedFS's `http://localhost:8333` |
| `S3_BUCKET_NAME` | no | `skintrackr-storage` | The bucket activities are archived to |
| `SYNC_INTERVAL_MINUTES` | no | `15` | How often connected athletes' recent activities are synced |
| `SYNC_WINDOW_DAYS` | no | `30` | How far back each sync looks, by activity start date |
| `INTERVALS_RATE_LIMIT_15MIN`, `INTERVALS_RATE_LIMIT_DAILY` | no | `6250`, `50000` | intervals.icu request budgets, until responses report the limits |
| `DEBUG_INTERVALS_RESPONSE_BODY` | no | `false` | Save every intervals.icu response body to `.request-debug-*.log` in the working directory |
| `SKINTRACKR_FRONTEND_DIR` | no | `../frontend/dist` | The frontend build to serve (`just web-build`) |

There are no app-wide intervals.icu credentials: each athlete connects with their own API key. Missing or invalid
settings fail at startup (`ConfigError`).

## Routes

| Route | Auth | |
| --- | --- | --- |
| `GET /assets/*` | | The frontend's hashed bundles, cached for a year (missing ones are 404) |
| any other `GET` | | A file from the frontend build (e.g. `/favicon.svg`), else its `index.html`, uncached, for the SPA to route |
| `GET /healthcheck` | | `{"ok": true}` |
| `POST /auth/connect` | | `{"api_key"}`: check the key with intervals.icu, store it encrypted, and sign the browser in (session cookie, 30 days): `{"athlete_id", "name"}` |
| `POST /auth/logout` | | Revoke the browser's session token (if valid) and clear the cookie |
| `POST /auth/disconnect` | bearer | Delete everything stored for the athlete (API key, archive, index, import), revoke the token and clear the cookie |
| `POST /token/new` | | `{"api_key"}`: connect like `/auth/connect`, but answer `{"access_token", "token_type": "Bearer", "athlete_id"}` (a JWT valid for 30 days, for scripts) |
| `POST /token/verify` | bearer | The token's details |
| `POST /token/revoke` | bearer | Revoke the token |
| `GET /api/activity/{id}/export` | bearer | The activity as a GPX download: from the bucket if archived, else straight from intervals.icu |
| `POST /api/activity/{id}/persist` | bearer | Archive and index the activity: `{"ok": true, "activityId": id}` |
| `POST /api/import` | bearer | Start a bulk import (Re-sync) of the athlete's activities (202; no-op if one is running); answers with its status |
| `GET /api/import` | bearer | Import progress: `{"state": "idle"\|"running"\|"done"\|"failed", "activities", "tracks_done", "tracks_total", "skipped", "error"}` |
| `GET /api/stats/vert?today=YYYY-MM-DD` | bearer | On-foot vert from Jan 1 to today's date, this year and the 5 before, from the activity index: `{"as_of", "activities", "years": [{"year", "vert_m"}, ...]}` |

"bearer" routes take the API token as `Authorization: Bearer <token>` (from `/token/new`) or, when there's no
`Authorization` header, from the `skintrackr_session` cookie (the browser's: HttpOnly, Secure, SameSite=Lax). The
token itself lives only with the client; Redis keeps just its `jwt:jti:` record, so it can be revoked.

Errors are `{"message": "..."}`. A missing or rejected API key is 400. A missing token or malformed `Authorization`
header is 401; an invalid, expired or revoked token is 403 on `/api/...` and `/auth/disconnect`, and 401 on the
`/token/...` routes.

`/api/stats/vert` counts every activity type except cycling and lift-served or virtual skiing (`EXCLUDED_TYPES` in
`vert.py`), by each activity's local start date, summed from the athlete's Redis activity index on each request.

## Ingesting activities

Activities are stored twice (`server/ingest.py`):

- **Bucket (source of truth):** each activity in full, as intervals.icu sent it, gzipped:
  `athletes/{athlete}/activities/{activity}/activity.json.gz` and `.../streams.json.gz` (every stream recorded).
  GPX exports are built from these.
- **Redis index (derived, rebuildable):** `athlete:{id}:activities`, one compact `ActivitySummary` per activity
  (type, local start date, elevation, distance, moving time, `icu_sync_date`). Stats read only this.

They get there three ways:

- **Sync:** every `SYNC_INTERVAL_MINUTES`, each connected athlete's activities that started in the last
  `SYNC_WINDOW_DAYS` are listed (one request) and compared with the index: new and changed ones are archived and
  indexed, and ones that are gone are removed. intervals.icu only sends webhooks to registered OAuth apps, so this
  polling stands in for them. Changes to older activities need a Re-sync.
- **Bulk import / Re-sync** (`POST /api/import`, the button on `/`): lists every activity into the index (one
  request), so stats work within seconds, removes activities deleted on intervals.icu, then archives each one not
  yet in the bucket (two requests each). Progress is saved in `athlete:{id}:import` after every activity; imports
  running when the server stops resume at startup (`imports:running`).
- **`POST /api/activity/{id}/persist`**, for one activity.

intervals.icu doesn't serve activities synced to it from Strava through its API (it lists them as stubs with
`source: STRAVA`); they're skipped and counted in the import's `skipped`.

### Rate limits

`IntervalsClient`'s `RateLimiter` tracks usage per 15 minutes and per UTC day: from the `X-RateLimit-Limit` and
`X-RateLimit-Remaining` headers ("15-minute,daily") when responses carry them, else by counting its own requests
against `INTERVALS_RATE_LIMIT_15MIN`/`_DAILY` (generous defaults: intervals.icu allows about 50,000 a day). Imports
and syncs run "in the background": their requests are spaced 0.2s apart (intervals.icu also limits each IP to 10
requests a second, unreported), wait for the next window when usage gets within 10% of a limit, and retry after a
429 (waiting `Retry-After`). Pages never wait. Usage is kept in memory, so a restart resets the count.

## Migrations

`skintrackr migrate <name>` runs a one-off data migration (`cli/migrate.py`). Migrations stay in the code after
they've run, so they can be re-run or read later; they may be cleaned up in the distant future. Each reads its
settings from the environment and asks for confirmation unless given `--yes`.

| Migration | |
| --- | --- |
| `intervals-reset` | The move to intervals.icu (2026-11): deletes everything in the bucket and every skintrackr key in Redis (keys matching `APP_KEY_PATTERNS`; other keys are left alone). Safe to re-run. |
| `legacy-layout --athlete ID [--allow-missing]` | The move from the Go server's `activities/{athlete}/metadata/{id}.json` and `tracks/{id}.gpx` to the `athletes/` layout: deletes the former once every activity is in the latter. Since intervals.icu's ids differ, use `--allow-missing` (or `intervals-reset`). |

## Layout

```
skintrackr/
  config.py          Config and BlobStorageConfig, from the environment
  crypto.py          JWTs (PyJWT, HS256) and NaCl secretbox encryption (PyNaCl)
  models.py          attrs types for intervals.icu data, and the shared cattrs converter
  gpx.py             GPX 1.1 documents from activity streams
  vert.py            year-to-date vert from an athlete's activity index
  logging.py         JSON-lines logging; structured context via `extra=`
  clients/
    intervals.py     IntervalsClient: every intervals.icu request (athletes, activities, streams), rate limits
    store.py         Store: every Redis operation (API keys, JWT revocation, activity index, imports)
    blob_store.py    BlobStore: every bucket operation; the activity archive layout
  server/
    app.py           create_app(): routes, the frontend build, lifespan, error handlers
    services.py      Services shared by handlers (on app.state), and background tasks
    ingest.py        ingesting activities: sync, bulk imports, removal
    auth.py          authenticate_request(): bearer token or session cookie -> AuthTokenInfo
    errors.py        `{"message": ...}` error responses and fail_with()
    connect.py  token_api.py  api.py    route handlers
  cli/               click commands: serve, migrate
tests/               pytest; fakes for intervals.icu (httpx.MockTransport), Redis (fakeredis) and S3 (moto)
```

intervals.icu requests go only through `IntervalsClient`, Redis only through `Store`, the bucket only through
`BlobStore`, and handlers authenticate first and stay thin.

## Stored formats

What's stored or handed out must stay readable by later versions (tests pin them with fixed fixtures in
`tests/test_crypto.py` and `tests/test_gpx.py`); a change to any of them ships with a `skintrackr migrate`
subcommand:

- **Redis keys:** listed in `clients/store.py`; API keys are stored as `athlete:{id}:intervals-key`.
- **Encryption:** NaCl secretbox keyed by the first 32 bytes of hex-decoded `APP_SECRET`, stored as
  base64(nonce || box).
- **JWTs:** HS256 over `APP_SECRET` with the claims `athlete_id` (the intervals.icu athlete id, a string),
  `expires_at`, `jti`, `exp`, `iat` and `nbf`.
- **Blob keys:** the layout above, holding intervals.icu's JSON as received.
