# Skintrackr

A webhook server for Strava activity notifications: OAuth with Strava, encrypted token storage in Redis, a
JWT-authenticated API, GPX export, and activity archiving (metadata + GPX track) to S3-compatible storage.

| Directory | |
| --- | --- |
| [`python/`](python/README.md) | The backend: Litestar server and CLI (uv project). Configuration, routes and layout are in its README. |
| `frontend/` | The frontend: a Vue 3 + Vite + TypeScript SPA. The server serves its build (`frontend/dist`) in production. |

## Commands

```sh
just            # list recipes
just test       # all tests (python: pytest)
just check      # all static checks (python: ty; frontend: vue-tsc)
just build      # build the production image (Dockerfile)
just dev-up     # docker compose: the app plus Redis at http://localhost:8080 (just dev-down to stop)
just py-serve   # run the server directly (needs Redis and .env); serves frontend/dist after `just web-build`
just web-dev    # frontend dev server with hot reload on :5173 (proxies server routes to py-serve on :8080)
```

## Local setup

```sh
cp .env.example .env   # then set STRAVA_CLIENT_ID/SECRET (https://www.strava.com/settings/api),
                       # APP_SECRET (openssl rand -hex 32) and the AWS_* blob-storage keys
just py-sync
just web-install
just dev-up            # or: run Redis yourself, set UPSTASH_REDIS_URL in .env, and `just py-serve`
```

## CI and deployment

- **CI** (`.github/workflows/ci.yml`) runs `just test` and `just check` on every pull request.
- **Deploy** (`.github/workflows/fly-deploy.yml`) runs CI on every push to `main`, then deploys to Fly.io if it passes. `fly deploy` does the same by hand.

Secrets are set with `fly secrets set` (the required variables are listed in `python/README.md`).
