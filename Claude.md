# Claude.md - Development Guide for Skintrackr

This file provides guidelines and context for Claude Code when working on the Skintrackr project.

## Project Overview

**Skintrackr** is a webhook server for Strava activity notifications with OAuth integration and Redis-based token management. It processes Strava webhooks, manages OAuth tokens securely, exports activity data to GPX, and archives activities (metadata + GPX track) to S3-compatible blob storage.

The backend lives in `python/`: a Python 3.12 [uv](https://docs.astral.sh/uv/) project with an async Litestar server, a click CLI, and attrs/cattrs data types. The frontend lives in `frontend/`: a Vue 3 + Vite + TypeScript SPA (vue-router), kept separate from the server code. The server serves its production build (`frontend/dist`): `/assets/*` with a one-year immutable cache, other build files as-is, and `index.html` (uncached) for every other GET path so the SPA can route it. It was originally written in Go; the Go code is gone, but the data it wrote (Redis keys, encrypted tokens, issued JWTs, archived blobs) is still live, so those formats must not change (see "Compatibility" in `python/README.md`).

## Architecture Overview

### Core Components

Paths below are relative to `python/`.

1. **skintrackr/server/app.py** - `create_app()`: routes, serving the frontend build, lifespan (builds `Services`), error handlers
2. **skintrackr/server/services.py** - `Services` (config, `Store`, `StravaClient`, `BlobStore`) on `app.state`, and tracked background tasks (`spawn`)
3. **skintrackr/clients/strava.py** - ALL Strava API interactions via `StravaClient`
4. **skintrackr/clients/store.py** - ALL Redis operations and token management via `Store`
5. **skintrackr/clients/blob_store.py** - ALL blob-storage (S3-compatible) operations via `BlobStore`; orchestrates `persist_activity` (download from Strava → upload to bucket)
6. **skintrackr/server/oauth2.py** - OAuth flow handlers
7. **skintrackr/server/webhooks.py** - Webhook subscription management and inbound push-event handling
8. **skintrackr/server/api.py** - Authenticated activity API (`export_track`, `persist_activity`)
9. **skintrackr/server/token_api.py** - API token issue/verify/revoke handlers; **server/auth.py** has `authenticate_request`
10. **skintrackr/crypto.py** - JWT signing/verification and NaCl secretbox encryption
11. **skintrackr/config.py** - Environment configuration (`Config`, `BlobStorageConfig`)
12. **skintrackr/server/errors.py** - `{"message": ...}` error responses and `fail_with()`
13. **skintrackr/models.py**, **gpx.py**, **logging.py** - attrs types + cattrs converter, GPX generation, JSON-lines logging
14. **skintrackr/vert.py** - `VertCache` (on `Services.vert`): year-to-date vert per athlete, an in-memory LRU fed by `StravaClient.list_activities`
15. **skintrackr/cli/** - click commands: `serve`, `strava-debug`

### Architecture Rules

#### **CRITICAL: Separation of Concerns**

1. **All Strava API requests MUST go through `StravaClient` in `clients/strava.py`**
   - Use `request()` (it takes `params=`, `form=` and `headers=`, and adds the bearer token)
   - Get an athlete's client with `services.strava_for(athlete_id)` (refreshes the token if needed)
   - Never call `httpx` directly for Strava outside of `StravaClient`

2. **All Redis operations MUST go through `Store` in `clients/store.py`**
   - Token storage, retrieval, and refresh logic
   - Encryption/decryption of sensitive data
   - Never access Redis directly outside of `Store`

3. **All blob-storage operations MUST go through `BlobStore` in `clients/blob_store.py`**
   - A boto3 `s3` client against the S3-compatible endpoint in `BlobStorageConfig`, built by `BlobStore.build()`; blocking calls run in `asyncio.to_thread`
   - `persist_activity` is the entry point: it fetches the activity and GPX track via `StravaClient`, then uploads them under `activities/{athleteId}/...`
   - Never create a boto3 client or make raw bucket calls outside of `BlobStore`

4. **Authentication**
   - Protected handlers MUST call `authenticate_request()` (in `server/auth.py`) first and use the returned `athlete_id`
   - It accepts the API token as a bearer header (CLI) or the HttpOnly `skintrackr_session` cookie (browser, set by `/oauth2/callback`); issue tokens with `issue_token()` and build the cookie with `session_cookie()`
   - JWT and encryption primitives live in `crypto.py`; do not hand-roll token or crypto logic elsewhere

5. **Handler Logic Flow**
   - Handlers (`api.py`, `oauth2.py`, `webhooks.py`, `token_api.py`) → get `Services` with `get_services(state)` → call `StravaClient`, `Store`, and `BlobStore` methods
   - Wrap failing steps in `fail_with("message", **context)` rather than hand-written try/except
   - Keep business logic in the appropriate layer; handlers stay thin

### Data Flow

```
Request (webhook / API)
        ↓
Handler (api.py / oauth2.py / webhooks.py / token_api.py)
        ↓
  ┌──────────────┬───────────────┬─────────────────┐
  ↓              ↓               ↓                  ↓
StravaClient    Store           BlobStore         (auth via
(strava.py)     (store.py)      (blob_store.py)    auth.py +
  ↓              ↓               ↓                  crypto.py)
Strava API      Redis           S3-compatible bucket
        ↓
     Response
```

Webhook activity `create`/`update` events trigger `BlobStore.persist_activity`
in a background task (`services.spawn`) so the webhook is acknowledged promptly.

## Git Workflow & Best Practices

### Branch Strategy (Gitflow)

1. **Main Branches**
   - `main` - Production-ready code
   - `develop` - Integration branch for features

2. **Supporting Branches**
   - `feature/*` - New features (branch from `develop`)
   - `bugfix/*` - Bug fixes (branch from `develop`)
   - `hotfix/*` - Urgent production fixes (branch from `main`)
   - `release/*` - Release preparation (branch from `develop`)

### Workflow Steps

#### For New Features
```bash
# 1. Create feature branch from develop
git checkout develop
git pull origin develop
git checkout -b feature/descriptive-name

# 2. Work on feature, commit regularly
git add .
git commit -m "feat: descriptive commit message"

# 3. Push and create PR
git push -u origin feature/descriptive-name
# Then create PR to merge into develop
```

#### For Bug Fixes
```bash
# Branch from develop
git checkout -b bugfix/fix-description

# Work, commit, push, create PR to develop
```

#### For Hotfixes
```bash
# Branch from main for urgent production fixes
git checkout main
git checkout -b hotfix/critical-issue

# Fix, commit, push, create PR to BOTH main and develop
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

**ALWAYS create PRs instead of pushing directly to main/develop**

1. **Before Creating PR**
   ```bash
   just test    # pytest
   just check   # ty type check
   ```

2. **PR Title Format**
   - `feat: Add token refresh functionality`
   - `fix: Correct OAuth callback error handling`
   - `refactor: Standardize Strava API requests`

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
just py-serve      # run the server with .env loaded (needs Redis)
just test          # all tests (py-test: uv run pytest)
just check         # all static checks (py-check: uv run ty check)
just build         # build the production Docker image
just dev-up        # docker compose: app + Redis on http://localhost:8080
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
fly secrets set APP_SECRET=$(openssl rand -hex 32)   # first-time setup; also STRAVA_CLIENT_ID,
                # STRAVA_CLIENT_SECRET, UPSTASH_REDIS_URL, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
```

### Environment Setup

Required environment variables (see `.env.example` and `python/README.md`):
- `APP_BASE_URL` - Base URL for OAuth callbacks
- `APP_SECRET` - hex string, at least 32 bytes decoded (`openssl rand -hex 32`)
- `STRAVA_CLIENT_ID` / `STRAVA_CLIENT_SECRET` - From Strava API settings
- `UPSTASH_REDIS_URL` - Redis connection string
- `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` - Keys for the S3-compatible blob store
- `VITE_MAPTILER_KEY` - (frontend build only) public MapTiler key for the map page; a Docker build arg, from `.env` locally and a GitHub secret in the deploy workflow

The blob-storage bucket name and endpoint are defaults in `BlobStorageConfig` in `config.py`.

### Frontend

```bash
just web-install     # npm ci
just web-dev         # Vite dev server on :5173 with hot reload; proxies server routes to `just py-serve` on :8080
just web-build       # production build into frontend/dist (what `just py-serve` serves)
just web-typecheck   # vue-tsc (part of `just check`)
```

- Pages are views in `frontend/src/views/`, registered in `frontend/src/router.ts`; shared pieces go in `src/components/` (e.g. `PageCard.vue`), global styles in `src/assets/main.css`
- Server routes (`/oauth2/...`, `/token/...`, `/api/...`) are plain links or `fetch` calls, not router links; add any new top-level server prefix to the proxy list in `vite.config.ts`
- The OAuth callback signs the browser in (session cookie) and redirects to the `/connected` route (`CONFIRMATION_PAGE` in `server/oauth2.py`); keep the two in sync
- Call the server through `src/api.ts` (it sends the cookie; 401/403 become `SignedOutError`); `useSignedIn()` tells whether the browser is signed in
- Per-browser preferences (vert goal, units) live in `src/settings.ts` (reactive, saved to `localStorage`); format vert with `formatVert()`
- Pages: `/` (landing), `/connected`, `/stats` (season vert), `/map` (MapLibre GL + MapTiler Outdoor and terrain-RGB, 3D terrain on; the worker URL is set explicitly with `setWorkerUrl`)
- Server tests use a stand-in build (`frontend_dir` fixture), so they don't need `npm run build`

## Testing Guidelines

### Unit Test Requirements

1. **All new features MUST have unit tests** in `python/tests/test_<module>.py`
2. **Never make real network calls** - use the fakes in `python/tests/conftest.py`: `FakeStrava` (`httpx.MockTransport`), fakeredis, and `FakeBucket` (botocore `Stubber`), plus the `client` fixture (Litestar `TestClient`)
3. Use `pytest.mark.parametrize` for multiple scenarios
4. Critical paths (auth, token management) must be well-tested; keep the Go-produced compatibility fixtures in `tests/test_crypto.py` and `tests/test_gpx.py` passing

## Code Style & Standards

### **CRITICAL: Compact, Organized, Non-Duplicated Code**

Every change MUST prioritize the following, in order:

1. **Do not duplicate code that already exists.** Before writing anything, look for an existing helper, method, or pattern that already does the job and reuse it. If similar logic exists in two places, factor out a shared function rather than copy-pasting. This is the single most important rule — duplicated logic is the primary thing to avoid.
2. **Be consistent with the surrounding code.** Match the existing style, naming, error-handling (`fail_with`), logging, and architectural layering (`StravaClient` / `Store` / `BlobStore`). New code should be indistinguishable from the code already in the file.
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
   - ✅ Use `StravaClient` methods (or `StravaClient.request()`)
   - ❌ Never use `httpx` directly for Strava

2. **Token Management**
   - ✅ Use `Store.fetch_token()`, `Store.save_token()`
   - ❌ Never access Redis directly outside `Store`

3. **Blob Storage**
   - ✅ Use `BlobStore` methods (e.g. `persist_activity()`)
   - ❌ Never create a boto3 client or call the bucket directly outside `BlobStore`

4. **Configuration**
   - Use environment variables via `Config.from_env()`
   - Never hardcode secrets or URLs

5. **Logging**
   - `logger = get_logger(__name__)`; structured context via `extra=`: `logger.info("saved new token", extra={"athlete_id": athlete_id})`
   - Include relevant context (athlete_id, activity_id, etc.)
   - Log errors before returning or raising them (`fail_with` does this for handlers)

6. **Stored formats are frozen** - Redis keys, ciphertext layout, JWT claims and blob keys/metadata must stay compatible with existing data

## Common Tasks

### Adding a New Strava API Endpoint

1. Add an async method to `StravaClient` in `clients/strava.py`
2. Use `self.request()` and decode the body with `_decode()` (the shared cattrs `converter`)
3. Add unit tests in `tests/test_strava.py` (extend `FakeStrava`)
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
3. Write tests (stub the S3 calls with `FakeBucket`)

### Adding New HTTP Handlers

1. Add the handler to the most relevant module in `python/skintrackr/server/` (reuse an existing module rather than creating a new one) and to its `*_handlers` list or router
2. Make sure the list/router is registered in `create_app()` in `server/app.py`
3. Authenticate with `authenticate_request()` for protected endpoints
4. Reuse existing `StravaClient`, `Store`, and `BlobStore` methods — do not duplicate their logic in the handler
5. Raise `HTTPException` (or use `fail_with`) for error statuses

Note: every unmatched GET path serves the landing page (SPA fallback), so a new GET route that is mistyped returns the landing page instead of 404.

## Troubleshooting

- **Dependencies out of date:** `just py-sync` (`uv lock --upgrade` to bump)
- **Missing settings:** startup fails with `ConfigError` naming the variable
- **Docker:** `docker compose down -v && docker compose build --no-cache && docker compose up`
- **Fly.io:** `fly logs`, `fly secrets list`, `fly config validate`
- **Strava responses:** set `DEBUG_STRAVA_RESPONSE_BODY=true` to dump bodies to `.request-debug-*.log`

## Security Considerations

1. **Never commit secrets** - Use environment variables
2. **Encrypt sensitive data at rest** - `Store` encrypts tokens with `crypto.encrypt`/`decrypt`
3. **Validate OAuth state** - CSRF protection via `Store.save_oauth_state`/`consume_oauth_state`
4. **Use HTTPS** - Enforced in production via Fly.io
5. **Token rotation** - Refresh tokens expire, handle gracefully

## Resources

- **Strava API Docs**: https://developers.strava.com/docs/reference/
- **Fly.io Docs**: https://fly.io/docs/
- **Litestar**: https://docs.litestar.dev/
- **uv**: https://docs.astral.sh/uv/
- **attrs / cattrs**: https://www.attrs.org/ , https://catt.rs/
- **redis-py (asyncio)**: https://redis.readthedocs.io/
- **boto3 (S3)**: https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html

---

**Remember**: Write compact, organized code that reuses what already exists and stays consistent with the surrounding style — never duplicate logic. Maintain separation of concerns (Strava API in `clients/strava.py`, Redis in `clients/store.py`, blob storage in `clients/blob_store.py`, auth/crypto in `server/auth.py`/`crypto.py`), run `just test` and `just check` before creating PRs, and follow gitflow with PRs for all changes.
