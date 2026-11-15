# skintrackr server

The Python backend: a Strava webhook server with OAuth, encrypted token storage in Redis, a JWT-authenticated
API, GPX export, and activity archiving (metadata + GPX track) to S3-compatible storage. It also serves the
production build of the frontend (`../frontend`, a Vue SPA).

Built with Python 3.12 and [uv](https://docs.astral.sh/uv/): [Litestar](https://litestar.dev) (async),
[click](https://click.palletsprojects.com), [attrs](https://www.attrs.org)/[cattrs](https://catt.rs), httpx,
redis-py (asyncio) and boto3.

## Commands

From the repository root (or the plain `uv run ...` commands in this directory):

```sh
just py-sync    # uv sync: install, including dev tools
just py-serve   # uv run skintrackr serve, with ../.env loaded (0.0.0.0:8080; needs Redis)
just py-test    # uv run pytest
just py-check   # uv run ty check
```

`uv run skintrackr migrate-legacy --athlete N` deletes an athlete's archive in the former layout (see below).
`uv run skintrackr strava-debug -t TOKEN -a ACTIVITY_ID -o out.gpx` downloads a GPX track straight from
Strava; it reads the token from `STRAVA_TOKEN` if `-t` is omitted.

## Configuration

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| `APP_BASE_URL` | no | `http://localhost:8080` | Public URL, for OAuth and webhook callbacks |
| `APP_SECRET` | yes | | Hex key, at least 32 bytes (`openssl rand -hex 32`); signs JWTs and encrypts stored tokens |
| `STRAVA_CLIENT_ID`, `STRAVA_CLIENT_SECRET` | yes | | Strava API application credentials |
| `UPSTASH_REDIS_URL` | yes | | Redis URL |
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | yes | | Blob storage credentials (bucket `skintrackr-storage` at `https://t3.storage.dev`) |
| `DEBUG_STRAVA_RESPONSE_BODY` | no | `false` | Save every Strava response body to `.request-debug-*.log` in the working directory |
| `SKINTRACKR_FRONTEND_DIR` | no | `../frontend/dist` | The frontend build to serve (`just web-build`) |

Missing or invalid settings fail at startup (`ConfigError`).

## Routes

| Route | Auth | |
| --- | --- | --- |
| `GET /assets/*` | | The frontend's hashed bundles, cached for a year (missing ones are 404) |
| any other `GET` | | A file from the frontend build (e.g. `/favicon.svg`), else its `index.html`, uncached, for the SPA to route |
| `GET /healthcheck` | | `{"ok": true}` |
| `GET /oauth2/connect` | | Redirect to Strava to connect an account |
| `GET /oauth2/callback` | | Store the athlete's tokens, sign the browser in (session cookie, 30 days), and redirect to the frontend's `/connected` page |
| `POST /oauth2/logout` | | Revoke the browser's session token (if valid) and clear the cookie |
| `GET /subscriptions/callback` | `hub.verify_token` | Webhook verification: echoes `hub.challenge` |
| `POST /subscriptions/callback` | | Webhook events, handled in the background: activity `create`/`update` archive and index it, `delete` removes it, athlete deauthorization deletes everything stored for them |
| `GET /token/new` | | Start the OAuth flow that issues an API token (with CSRF `state`) |
| `GET /token/callback` | `state` | `{"access_token", "token_type": "Bearer", "athlete_id"}`; a JWT valid for 30 days |
| `POST /token/verify` | bearer | The token's details |
| `POST /token/revoke` | bearer | Revoke the token |
| `GET /api/strava-token` | bearer | The athlete's Strava token (refreshed if expired) |
| `GET /api/activity/{id}/export` | bearer | The activity as a GPX download: from the bucket if archived, else straight from Strava |
| `POST /api/activity/{id}/persist` | bearer | Archive and index the activity: `{"ok": true, "activityId": id}` |
| `POST /api/import` | bearer | Start a bulk import of the athlete's activities (202; no-op if one is running); answers with its status |
| `GET /api/import` | bearer | Import progress: `{"state": "idle"\|"running"\|"done"\|"failed", "activities", "tracks_done", "tracks_total", "error"}` |
| `GET /api/stats/vert?today=YYYY-MM-DD` | bearer | On-foot vert from Jan 1 to today's date, this year and the 5 before, from the activity index: `{"as_of", "activities", "years": [{"year", "vert_m"}, ...]}` |

"bearer" routes take the API token as `Authorization: Bearer <token>` (the CLI's, from `/token/callback`) or, when
there's no `Authorization` header, from the `skintrackr_session` cookie (the browser's: HttpOnly, Secure,
SameSite=Lax). The token itself lives only with the client; Redis keeps just its `jwt:jti:` record, so it can be revoked.

Errors are `{"message": "..."}`. A missing token or malformed `Authorization` header is 401; an invalid, expired or
revoked token is 403 on `/api/activity/...` and `/api/stats/...`, and 401 on the `/token/...` and `/api/strava-token` routes.

`/api/stats/vert` counts every sport except cycling and lift-served skiing (`EXCLUDED_SPORT_TYPES` in `vert.py`),
by each activity's local start date, summed from the athlete's Redis activity index on each request.

## Ingesting activities

Activities are stored twice (`server/ingest.py`):

- **Bucket (source of truth):** each activity in full, as Strava sent it, gzipped:
  `athletes/{athlete}/activities/{activity}/activity.json.gz` (DetailedActivity) and `.../streams.json.gz`
  (every stream type recorded). GPX exports are built from these.
- **Redis index (derived, rebuildable):** `athlete:{id}:activities`, one compact `ActivitySummary` per activity
  (sport type, local start date, elevation, distance, moving time, Strava's summary polyline). Stats read only this.

They get there three ways:

- **Webhooks:** Strava has one subscription per app, delivering events for every athlete who authorized it
  (`/oauth2/callback` and startup make sure it exists). New and edited activities are archived and indexed.
- **Bulk import** (`POST /api/import`, the button on `/`): lists every activity (200 per request) into the
  index first, so stats work within seconds, then archives each one not yet in the bucket (two requests each).
  Progress is saved in `athlete:{id}:import` after every activity; imports running when the server stops resume
  at startup (`imports:running`).
- **`POST /api/activity/{id}/persist`**, for one activity.

Strava's rate limits apply to the whole app (by default about 100 read requests per 15 minutes and 1,000 per
day). `StravaClient`'s `RateLimiter` tracks usage from every response's headers; imports run "in the background",
waiting for the next window when usage gets within 10% of a limit (and retrying after a 429), so webhooks and
pages keep working. A large history therefore takes a day or more to archive in full.

The former layout (`activities/{athlete}/metadata/{id}.json` and `tracks/{id}.gpx`, from the Go server) is no
longer written. Once an athlete's import has finished, `skintrackr migrate-legacy --athlete N` deletes it: it
first checks that every activity in it is archived in the new layout (`--allow-missing` for activities since
deleted on Strava).

At startup the server makes sure a Strava webhook subscription exists (creating one pointing at
`$APP_BASE_URL/subscriptions/callback` if not), in the background.

## Layout

```
skintrackr/
  config.py          Config and BlobStorageConfig, from the environment
  crypto.py          JWTs (PyJWT, HS256) and NaCl secretbox encryption (PyNaCl)
  models.py          attrs types for Strava data, and the shared cattrs converter
  gpx.py             GPX 1.1 documents from activity streams
  vert.py            year-to-date vert from an athlete's activity index
  logging.py         JSON-lines logging (like Go's slog); structured context via `extra=`
  clients/
    strava.py        StravaClient: every Strava request (OAuth, subscriptions, activities, streams)
    store.py         Store: every Redis operation (tokens, OAuth state, JWT revocation)
    blob_store.py    BlobStore: every bucket operation; the activity archive layout
  server/
    app.py           create_app(): routes, the frontend build, lifespan, error handlers
    services.py      Services shared by handlers (on app.state), and background tasks
    ingest.py        ingesting activities: webhooks, bulk imports, removal
    auth.py          authenticate_request(): bearer token -> AuthTokenInfo
    errors.py        `{"message": ...}` error responses and fail_with()
    oauth2.py  webhooks.py  token_api.py  api.py    route handlers
  cli/               click commands: serve, strava-debug
tests/               pytest; fakes for Strava (httpx.MockTransport), Redis (fakeredis) and S3 (moto)
```

Strava requests go only through `StravaClient`, Redis only through `Store`, the bucket only through
`BlobStore`, and handlers authenticate first and stay thin.

## Compatibility with the former Go server

skintrackr was originally written in Go. The Python server reads and writes the same data, so everything
the Go server stored (tokens, issued JWTs, archived activities) keeps working:

- **Redis keys:** `athlete:{id}:strava-token` (hash of encrypted `access_token`/`refresh_token`, `expires_at`),
  `oauth:state:{state}` (10 min TTL), `jwt:jti:{jti}`, and `jwt:revoked:{jti}` (both expire with the token).
- **Encryption:** NaCl secretbox keyed by the first 32 bytes of hex-decoded `APP_SECRET`, stored as
  base64(nonce || box).
- **JWTs:** HS256 over `APP_SECRET` with the claims `athlete_id`, `expires_at`, `jti`, `exp`, `iat` and `nbf`.
- **Blob keys:** the Go server's `activities/{athlete}/metadata/{activity}.json` and `tracks/{activity}.gpx` are
  replaced by the layout above; `migrate-legacy` deletes them once they're re-archived.

The tests check this against fixtures produced by the Go code (`tests/test_crypto.py`, `tests/test_gpx.py`).

Deliberate differences from the Go server's API:

- `POST /token/verify` answers with the token's details (`valid`, `athlete_id`, `issued_at`, `expires_at`,
  `jti`). Go answers `{}` because its struct's fields are unexported.
- Error bodies are always `{"message": "..."}`. Some Go errors wrap a non-string error and serialize as `{}`.
- GPX `atemp` holds the temperature. Go writes the heart rate there. This only affects `strava-debug`,
  because exports don't request those streams.
- GPX documents declare the `xsi` namespace they use. Go's output doesn't, so XML parsers reject it.
- Malformed JSON from Strava is an error rather than silently decoding to zero values. Missing or null
  fields still take Go's zero values.
- Archived objects carry content types (`application/json`, `application/gpx+xml`).
