# skintrackr server

The Python backend: a Strava webhook server with OAuth, encrypted token storage in Redis, a JWT-authenticated
API, GPX export, and activity archiving (metadata + GPX track) to S3-compatible storage. It also serves the
frontend in the repository's `static/`.

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
| `SKINTRACKR_STATIC_DIR` | no | `../static` (the repository's `static/`) | Pages served at `/` and `/static` |

Missing or invalid settings fail at startup (`ConfigError`).

## Routes

| Route | Auth | |
| --- | --- | --- |
| `GET /`, `GET /static/*` | | Landing and confirmation pages (the repo's `static/`); any other unmatched GET path also gets the landing page, uncached |
| `GET /healthcheck` | | `{"ok": true}` |
| `GET /oauth2/connect` | | Redirect to Strava to connect an account |
| `GET /oauth2/callback` | | Store the athlete's tokens; redirect to the confirmation page with the token in the fragment |
| `GET /subscriptions/callback` | `hub.verify_token` | Webhook verification: echoes `hub.challenge` |
| `POST /subscriptions/callback` | | Webhook events; activity `create`/`update` archive the activity in the background |
| `GET /token/new` | | Start the OAuth flow that issues an API token (with CSRF `state`) |
| `GET /token/callback` | `state` | `{"access_token", "token_type": "Bearer", "athlete_id"}`; a JWT valid for 30 days |
| `POST /token/verify` | bearer | The token's details |
| `POST /token/revoke` | bearer | Revoke the token |
| `GET /api/strava-token` | bearer | The athlete's Strava token (refreshed if expired) |
| `GET /api/activity/{id}/export` | bearer | The activity as a GPX download |
| `POST /api/activity/{id}/persist` | bearer | Archive the activity: `{"ok": true, "activityId": id}` |

Errors are `{"message": "..."}`. A missing or malformed `Authorization` header is 401; an invalid, expired or
revoked token is 403 on `/api/activity/...` and 401 on the `/token/...` and `/api/strava-token` routes.

At startup the server makes sure a Strava webhook subscription exists (creating one pointing at
`$APP_BASE_URL/subscriptions/callback` if not), in the background.

## Layout

```
skintrackr/
  config.py          Config and BlobStorageConfig, from the environment
  crypto.py          JWTs (PyJWT, HS256) and NaCl secretbox encryption (PyNaCl)
  models.py          attrs types for Strava data, and the shared cattrs converter
  gpx.py             GPX 1.1 documents from activity streams
  logging.py         JSON-lines logging (like Go's slog); structured context via `extra=`
  clients/
    strava.py        StravaClient: every Strava request (OAuth, subscriptions, activities, streams)
    store.py         Store: every Redis operation (tokens, OAuth state, JWT revocation)
    blob_store.py    BlobStore: every bucket operation, and persist_activity
  server/
    app.py           create_app(): routes, static files, lifespan, error handlers
    services.py      Services shared by handlers (on app.state), and background tasks
    auth.py          authenticate_request(): bearer token -> AuthTokenInfo
    errors.py        `{"message": ...}` error responses and fail_with()
    oauth2.py  webhooks.py  token_api.py  api.py    route handlers
  cli/               click commands: serve, strava-debug
tests/               pytest; fakes for Strava (httpx.MockTransport), Redis (fakeredis) and S3 (botocore Stubber)
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
- **Blob keys:** `activities/{athlete}/metadata/{activity}.json` (the same fields as Go's `StravaActivity`) and
  `activities/{athlete}/tracks/{activity}.gpx`.

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
