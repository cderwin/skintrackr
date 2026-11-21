# Plan: migrate from Strava to intervals.icu

Status: decisions confirmed 2026-11-15. Branch: `feature/intervals-icu` (from `develop`).

## Goal

Replace Strava with [intervals.icu](https://intervals.icu/api-docs.html) as the only activity source, keeping
every current feature, and remove every Strava reference from code, tests, docs and the website. Use an
intervals.icu **API key** for auth for now (OAuth later). We start with empty Redis and an empty bucket, so the
"stored formats are frozen" rule and the Go-compatibility constraints no longer apply.

## What changes, and what doesn't

The architecture stays the same: handlers go through `Services` to a source client, `Store` and `BlobStore`.
The Strava-specific parts change:

| Today (Strava) | After (intervals.icu) |
| --- | --- |
| OAuth app (`STRAVA_CLIENT_ID/SECRET`), access + refresh tokens | per-athlete API key, entered once and stored encrypted |
| `/oauth2/connect` → Strava → `/oauth2/callback` | `/connect` page form → `POST /auth/connect` (validates the key, sets the session cookie) |
| `/token/new` → OAuth → `/token/callback` (CLI JWT) | `POST /token/new` with the same credentials → returns the JWT |
| Webhook subscription + `/subscriptions/callback` | **polling sync** (intervals.icu webhooks only exist for registered OAuth apps) |
| Deauthorization webhook → `remove_athlete` | "Disconnect and delete my data" in Settings → `remove_athlete` |
| Integer athlete and activity ids | **string ids** (`i123456`, `i98765432`) |
| Paged `/athlete/activities` (200 per page) | `GET /athlete/{id}/activities?oldest=&newest=` (no paging, newest first) |
| Strava streams (`data` + `original_size`, latlng pairs) | `ActivityStream` (`type`, `data`, plus `data2` = longitudes for `latlng`) |
| `RateLimiter` driven by `X-RateLimit-*` headers, 15 min / daily windows | `RateLimiter` kept, counting its own requests against generous per-minute and per-day budgets (intervals.icu allows ~50,000/day); retry on 429 |
| `sport_type` (Strava) | `type` (intervals.icu, the same names: `BackcountrySki`, `Ride`, …) |
| `/api/strava-token` + Settings "Strava access token" | removed (it only existed to hand Strava tokens to scripts) |
| `strava-debug` CLI command | removed (a `skintrackr-cli` may come back later) |
| `migrate-legacy` CLI command | `skintrackr migrate legacy-layout`, under a new `migrate` group that keeps every migration, plus a new `migrate intervals-reset` for this cutover |

## Decisions (confirmed)

1. **Each athlete enters their API key in a form.** The landing page's "Connect" button opens `/connect`, where
   the athlete enters their API key (from intervals.icu → Settings → Developer). The server checks it, stores it
   encrypted in Redis (like Strava tokens today) and signs the browser in. This keeps per-athlete keys, the
   session/JWT model and the Redis layout as they are, so OAuth can replace the form later without touching
   anything else.
2. **Webhooks become polling** (webhooks may replace it later). intervals.icu sends webhooks only to registered OAuth apps, configured on the
   app's "Manage App" page. It also never sends activity webhooks for Strava-sourced activities. So with an API
   key, a **sync loop** replaces the subscription:
   - Every 15 minutes (`SYNC_INTERVAL_MINUTES`), for each connected athlete: list activities that started in
     the last 30 days (`SYNC_WINDOW_DAYS`) and compare them to the index by a change fingerprint. Ingest new or
     changed activities, and remove indexed activities in the window that are missing from the list (deleted).
   - The "Re-sync" button (the existing bulk import) still reconciles the athlete's whole history.
   - Edits to activities older than the window are only picked up by Re-sync. That's acceptable for a prototype,
     and webhooks fix it later.
   - Build the code so a webhook handler can be added later (`ACTIVITY_UPLOADED`/`ACTIVITY_UPDATED` → ingest,
     `ACTIVITY_DELETED` → remove, checking the payload `secret`) with no other changes.
3. **String ids everywhere.** intervals.icu ids are strings (`i…`). Make `athlete_id` and `activity_id` `str` in
   models, `Store`, `BlobStore`, JWT claims, `Services`, `ingest.py` and routes. Activity ids in paths are
   checked against `^[A-Za-z0-9_-]+$` instead of `isdigit()`.
4. **A fresh start in the same bucket.** `DEFAULT_BUCKET_NAME` stays `skintrackr-storage`. Before the first
   deploy, `skintrackr migrate intervals-reset` (phase 5) deletes everything in it (`athletes/` and the legacy
   `activities/` prefix) and skintrackr's Redis keys. Existing JWTs stop working (their `athlete_id` is an
   int), so everyone signs in again.
6. **Migrations stay in the code after they've run.** They live under `skintrackr migrate <name>` and may be
   cleaned up in the distant future, but are never deleted as part of other work.
5. **Keep the crypto primitives and GPX output.** `crypto.py` (HS256 JWT, secretbox) and `gpx.py` don't change.
   Only the JWT `athlete_id` claim becomes a string. Rewrite the "Go-produced compatibility fixture" tests that
   embed integer athlete ids. The GPX fixtures stay as they are (GPX output still comes from merged streams).

## Things to check against your real account first (phase 0)

The OpenAPI spec (`https://intervals.icu/api/v1/docs`) leaves some behavior unspecified. Before writing the
client, run a short probe script with your key (stored in `.env` as `INTERVALS_API_KEY`, never committed) and
save trimmed responses as test fixtures:

- [ ] Auth: HTTP Basic, username `API_KEY`, password = the key. Does `GET /api/v1/athlete/0` resolve to the key's
      own athlete? If so, the connect form needs only the key, and the athlete id (`i…`) comes from the response.
- [ ] **Strava-sourced activities**: the API doesn't serve activities synced from Strava (`source: STRAVA`). The
      list returns stubs, and `/file` says "Strava activities not supported". Check that your migrated history
      is `UPLOAD`/`GARMIN_CONNECT`/`OAUTH_CLIENT`, and decide whether stubs are skipped or counted (recommended:
      skip them and report the count in the import status).
- [ ] `GET /athlete/{id}/activities?oldest=1970-01-01`: the size of a full-history response. Use `fields=` to
      trim it to the summary fields, and chunk by year if it's large.
- [ ] `GET /activity/{id}`: field names and units for `total_elevation_gain`, `start_date` (UTC, `Z`?),
      `start_date_local`, `type`, `icu_athlete_id`, and which field changes on edit (`icu_sync_date`/`analyzed`)
      for the sync fingerprint.
- [ ] `GET /activity/{id}/streams.json` (the `{ext}` path parameter): stream type names (`time`, `latlng`,
      `altitude` vs `fixed_altitude`, `distance`, `heartrate`, `temp`), the `latlng` `data`/`data2` split, nulls
      in GPS dropouts, and the response for manual activities without streams (404 or `[]`).
- [ ] Rate limiting: any rate-limit headers, and the response on 429 (`Retry-After`?). The daily budget is about
      50,000 requests. Look for a documented short-term (per-second or per-minute) limit, to set the
      per-minute default.

## Implementation phases

Each phase leaves `just test` and `just check` green. Paths are relative to `python/` unless noted.

### Phase 1: the source client

- `clients/strava.py` → **`clients/intervals.py`**: `IntervalsClient`, `IntervalsError`, `RateLimiter`,
  `merge_streams`, `activity_gpx`.
  - `API_URL = "https://intervals.icu/api/v1"`. `request()` keeps its signature but sends Basic auth
    (`httpx.BasicAuth("API_KEY", key)`) in place of the bearer token.
  - `for_athlete(athlete_id, api_key)` replaces `with_token`. `in_background()` stays.
  - Methods: `get_athlete()` (validates a key), `list_activities(oldest, newest=None)`, `get_activity_json`,
    `get_activity`, `get_streams_json(types)`, `get_activity_stream`, `export_activity_gpx`.
  - Remove the OAuth and subscription methods, `AUTH_URL`/`TOKEN_URL`/`SCOPE`, and `ACTIVITIES_PER_PAGE`.
  - `RateLimiter` stays, with the same role: shared app-wide, it paces background requests and keeps a share
    of the budget (`headroom`, 10%) for pages, which never wait. intervals.icu doesn't report usage in headers
    (phase 0 confirms), so the limiter counts every request it sees itself:
    - Replace `update(headers)` with `record()`. Usage is tracked per minute and per UTC day, in place of
      quarter-hour and daily windows. `_next_window` and `delay`/`wait` keep their structure.
    - Generous defaults: **300 requests/minute** and **50,000/day**, less the 10% headroom for background work
      (about 270/min and 45,000/day). At two requests per activity, a backfill does roughly 135 activities a
      minute, and the daily budget covers about 22,000 activities. Tune the per-minute default from phase 0.
    - Settings `INTERVALS_RATE_LIMIT_PER_MINUTE` / `INTERVALS_RATE_LIMIT_PER_DAY` (in `Config`, with these
      defaults) can tune it without a code change.
    - On a 429, background requests still `block()`: they wait for `Retry-After` if it's sent, else until the
      next minute, then retry. Foreground requests fail on 429, as today.
    - Usage is in memory: a restart resets the count, as today.
  - `merge_streams`: take the intervals.icu stream shape (`latlng` from `data` + `data2`, no `original_size`;
    lengths are checked against the first stream). Points with null lat/lng are dropped from GPX output.
- `models.py`:
  - `StravaActivity` → `Activity` (`id: str`, `icu_athlete_id`, `name`, `type`, `start_date`, `start_date_local`,
    `distance`, `moving_time`, `elapsed_time`, `total_elevation_gain`, `description`, `calories`, `source`).
  - `ActivitySummary`: `id: str`, `type` (was `sport_type`), drop `map` (`summary_polyline` is unused), add the
    sync fingerprint field.
  - `RawStream` → `Stream` (`type`, `data`, `data2`).
  - Remove `TokenInfo`, `TokenResponse`, `TokenAthlete`, `PushEvent`, `Subscription`, `AthleteRef`. Add `Athlete`
    (`id`, `name`) for key validation.
  - Rewrite the module docstring (drop the Go-compat text).
- `vert.py`: filter on `activity.type`. Check `EXCLUDED_SPORT_TYPES` against intervals.icu's enum: add
  `TrackRide`, `Cyclocross`, `VirtualSki`; keep `AlpineSki`, `Snowboard`.
- Tests: `test_strava.py` → `test_intervals.py`, with fixtures from phase 0. `FakeStrava` → `FakeIntervals` in
  `conftest.py` (checks Basic auth, the `oldest`/`newest` params and the streams path).

### Phase 2: config, storage and credentials

- `config.py`: drop `strava_client_id/secret` and `verify_token`. `DEBUG_STRAVA_RESPONSE_BODY` →
  `DEBUG_INTERVALS_RESPONSE_BODY` (`debug_intervals_responses`). Add `sync_interval` and `sync_window` (with
  defaults; env overrides optional). `ConfigError` no longer asks for Strava variables.
- `clients/store.py`:
  - `athlete:{id}:strava-token` → **`athlete:{id}:intervals-key`** (encrypted key).
  - `fetch_token`/`fetch_token_info`/`save_token` → `fetch_api_key`/`save_api_key`. Drop the refresh logic and
    the `strava` attribute (`Store` no longer needs the source client).
  - Remove `save_oauth_state`/`consume_oauth_state` and the `oauth:state:*` key (no OAuth for now; keep them
    only if OAuth is next on the roadmap).
  - Add `athletes:connected` (a set of athlete ids, for the sync loop) and per-athlete sync state
    `athlete:{id}:sync` (last run time and errors).
  - Add `delete_everything()` (a `SCAN` over the key prefixes in the docstring table, then delete them) for
    the `intervals-reset` migration.
  - All ids become `str`. Update the key table in the docstring.
- `crypto.py`: `athlete_id: str` in `TokenClaims`, `generate_jwt` and `verify_jwt` (a string claim). Update the
  docstring and the fixtures in `test_crypto.py`.
- `server/services.py`: `strava` → `intervals`; `strava_for` → `intervals_for(athlete_id)` (loads the
  decrypted key).

### Phase 3: ingest, sync and archive

- `clients/blob_store.py`: `archive_activity(intervals, athlete_id, activity_id)` uploads intervals.icu's
  activity and streams JSON as received, in the same layout (`athletes/{a}/activities/{id}/activity.json.gz`,
  `streams.json.gz`). `read_activity` returns `(Activity, list[Stream])`. The id regex in
  `archived_activity_ids` accepts string ids. Keep `legacy_prefix`, `legacy_activity_ids` and the legacy half
  of `delete_athlete`, which the `legacy-layout` migration uses. Add `delete_everything()` (every key in the
  bucket, built on `delete_prefix("")`) for `intervals-reset`.
- `server/ingest.py`:
  - Import becomes one listing (`list_activities(EPOCH)`, chunked if phase 0 says so), skipping
    Strava-sourced stubs. Then archive the activities that aren't archived yet. No per-activity token refresh
    (keys don't expire). A 404 still means "deleted since listed".
  - A 401 or 403 from intervals.icu (key revoked) fails the import with a clear "reconnect" error.
  - **New `sync_athlete(services, athlete_id)`**: the window diff from decision 2. It reuses `ingest_activity`
    and `remove_activity`, and only skips athletes who have an import running.
  - **New `run_sync_loop(services)`**: started from the lifespan as a tracked background task. Every interval
    it runs `sync_athlete` for each connected athlete, one at a time on the background client. Errors are
    logged per athlete and never stop the loop.
- `server/webhooks.py`: **deleted** (`subscription_callback`, `push_event`, `ensure_subscription`). Its
  `_logged` helper moves into `ingest.py` if the sync loop uses it.

### Phase 4: auth, routes and app

- `server/oauth2.py` → **`server/connect.py`** (`connect_router` at `/auth`):
  - `POST /auth/connect {api_key[, athlete_id]}`: validate with `get_athlete()`, then `save_api_key`, add the
    athlete to `athletes:connected`, `issue_token`, set the session cookie, and answer
    `{athlete_id, name}`. A 401 from intervals.icu → 400 "invalid API key".
  - `POST /auth/logout`: moved from `/oauth2/logout`, unchanged.
  - `POST /auth/disconnect` (authenticated): `remove_athlete` (deletes the stored key, index, archive and import
    state, cancels the import, and removes the athlete from the connected set), then clears the cookie. This
    replaces deauthorization.
  - `CONFIRMATION_PAGE` goes away: the frontend routes to `/connected` after a successful POST.
- `server/token_api.py`: `GET /token/new` + `GET /token/callback` → **`POST /token/new {api_key}`**, which
  answers `{access_token, token_type, athlete_id}` for the CLI. It shares a `connect_athlete()` helper with
  `/auth/connect` (no duplication). `verify`/`revoke` stay. Delete `GET /api/strava-token`.
- `server/api.py`: `_activity_id` validates string ids. `export_track`'s fallback uses `intervals_for` and
  the same `merge_streams`/`activity_gpx` path. Error messages say "intervals.icu" (or no source name).
- `server/app.py`: drop `ensure_subscription`, start the sync loop and `resume_imports`, build the
  `IntervalsClient`, `STRAVA_TIMEOUT` → `HTTP_TIMEOUT`, and update the route table.
- `server/auth.py`: the session/bearer logic doesn't change; `athlete_id: str`.
- Tests: `test_oauth2.py` → `test_connect.py`; delete `test_webhooks.py`; add sync tests to `test_ingest.py`
  (new, changed, deleted, the Strava-stub skip, revoked key); update `test_token_api.py`, `test_api.py`,
  `test_app.py`, `test_store.py`, `test_blob_store.py`, `test_config.py` and `test_vert.py`.

### Phase 5: CLI

- Delete `cli/strava_debug.py` and its tests. No replacement for now; a `skintrackr-cli` may come back later.
- `cli/migrate.py` becomes a **`skintrackr migrate` click group**, with one subcommand per migration. Each
  migration is a self-contained command with a docstring saying when it was written, what it's for and whether
  it's still needed. Running `skintrackr migrate` with no subcommand lists them (click's help).
  - **`migrate legacy-layout --athlete ID [--allow-missing] [--yes]`**: today's `migrate-legacy`, moved without
    behavior changes. It deletes the Go server's `activities/{athlete}/` archive once every activity in it is
    in the new layout. Only the types change: ids are strings now, and legacy ids are compared as strings.
    After the intervals.icu cutover, legacy Strava ids never match the new archive, so it needs
    `--allow-missing`; the docstring says so.
  - **`migrate intervals-reset [--yes]`** (new): the cutover from decision 4. It deletes every object in the
    bucket (`BlobStore.delete_everything()`) and every skintrackr key in Redis (`Store.delete_everything()`: a
    `SCAN` over the app's key prefixes `athlete:*`, `jwt:*`, `oauth:*`, `imports:running`, not `FLUSHDB`, so it
    is safe on a shared Redis). It prints what it found first (object and key counts) and asks for
    confirmation unless `--yes`. It's safe to re-run.
  - Both read their settings from the environment, as `migrate-legacy` does today. `intervals-reset` also needs
    `UPSTASH_REDIS_URL` and `APP_SECRET`, so it loads `Config.from_env()`.
  - Tests in `test_cli.py`: the existing `migrate-legacy` tests move to `legacy-layout`; add `intervals-reset`
    tests (deletes everything, leaves non-skintrackr Redis keys alone, confirmation, idempotent).
- `cli/main.py`: `serve` and the `migrate` group; the group help becomes "skintrackr: intervals.icu activity
  archive and stats".
- With `strava-debug` gone, `export_activity_gpx`'s `use_heart_rate`/`use_temperature` options have no callers.
  Remove them from the client (`gpx.py` keeps its own options and fixtures).

### Phase 6: frontend (`frontend/src`)

- `components/ConnectButton.vue`: "Connect intervals.icu" goes to the `/connect` route (a router link, not a
  server link).
- **New `views/ConnectView.vue`** (route `/connect` in `router.ts`), built from `PageCard` and styled like the
  existing forms. On success it goes to `/connected`; errors show inline. Top to bottom:
  - **The API key form**: a key field with a link to intervals.icu Settings → Developer, and a note that the key
    is stored encrypted and that disconnecting deletes it.
  - **Why intervals.icu**: a short blurb. Strava's API terms and rate limits restrict what third-party apps may
    do with your data (e.g. low request quotas and limits on storing and showing it). intervals.icu offers a
    free, open API with generous limits, so skintrackr can archive full tracks and backfill your whole history
    quickly.
  - **Setting up intervals.icu**: a short numbered list. (1) Create a free account at intervals.icu. (2) Connect
    your device's platform (Garmin, Coros, Suunto, Wahoo, …) under Settings → Connections, or upload files;
    note that activities synced only through Strava aren't available through the API. (3) Copy your API key from
    Settings → Developer and paste it above.
  - **What syncs automatically**: new activities and edits within the last 30 days sync within about 15
    minutes. Activities added or edited with a start date more than 30 days ago need a manual **Re-sync** (on the
    home page).
- `components/ImportPanel.vue` repeats that last note in one line next to the Re-sync button, so it can still be
  found after signup.
- Keep the copy in one place, e.g. a constant in `src/` for the 30-day window, so it matches the server's
  `SYNC_WINDOW_DAYS` default.
- `api.ts`: add `connect(apiKey)`, `disconnect()`, `logout` → `/auth/logout`; drop `StravaToken`/`stravaToken`.
  `call()` gains an optional JSON body.
- `components/SettingsPanel.vue`: remove the Strava token section (and `TokenField.vue` if nothing else uses
  it; check first). Add "Disconnect and delete my data", with an inline second click to confirm (no
  `window.confirm`).
- `views/HomeView.vue`, `ConnectedView.vue`, `StatsView.vue`, `components/ImportPanel.vue`, `assets/main.css`:
  replace the copy ("from Strava" → "from intervals.icu", "Strava's rate limits" → removed, the
  `strava.com/settings/apps` revoke link → intervals.icu settings plus the new Disconnect button). Rename the
  `.strava` class to `.brand-source` (or drop it).
- `vite.config.ts`: replace `/oauth2` and `/subscriptions` with `/auth` in the proxy list.
- `just web-typecheck` passes.

### Phase 7: docs, config files and cleanup

- `README.md`, `python/README.md` (rewrite "Compatibility" as a short "Storage layout" section; update the
  rate-limit section to the new budgets; replace the webhook section with sync and import; add a
  "Migrations" section on `skintrackr migrate` and the policy that migrations stay), `Claude.md` (overview,
  components list including `cli/migrate.py`, architecture rules for `IntervalsClient`, data flow, env vars,
  frozen-formats rule → describe the current layout and say that layout changes ship with a `migrate`
  subcommand, troubleshooting), `python/pyproject.toml` description.
- `.env.example`, `docker-compose.yml`: drop `STRAVA_*`; add `DEBUG_INTERVALS_RESPONSE_BODY`. The server needs
  no intervals.icu setting (keys are per athlete); `INTERVALS_API_KEY` in `.env` is only for phase 0 probing,
  and isn't added to `.env.example`. `.gitignore` comment.
- Fly: `fly secrets unset STRAVA_CLIENT_ID STRAVA_CLIENT_SECRET` after deploy. No new server secrets are needed.
- `nushell/sktk.nu` (untracked): drop `token`; update `persist` and `list-activities` to the new layout.
- **Final check:** `git grep -i strava` returns nothing, apart from intervals.icu's own `source: STRAVA` enum
  value in the stub-skip code and its tests.

## Rollout

1. Phases 0–7 on `feature/intervals-icu`, one commit per phase, then a PR to `develop`.
2. Before merging to `main`: run `skintrackr migrate intervals-reset` against production (after you confirm the
   counts it prints), deploy, connect with your key, run the import, and check stats, GPX export and that a new
   activity syncs within 15 minutes.
3. Afterwards: unset the Strava secrets and delete the Strava API app (this also stops Strava webhooks).

## Later (out of scope)

- intervals.icu OAuth (register an app): replaces the key form with an authorize redirect, adds webhooks
  (`ACTIVITY_UPLOADED/UPDATED/DELETED`, verified by `secret`), and the sync loop becomes a fallback.
- Use intervals.icu's `fields=` / `gpx-file` endpoints where they're cheaper than archived streams.

## Risks

- **Strava-sourced activities aren't available through the API.** If part of your history only exists in
  intervals.icu as a Strava sync, it can't be imported (phase 0 checks this).
- Polling misses edits to old activities until the next Re-sync.
- Rate limits: intervals.icu doesn't report usage, so the limiter's counts are only an estimate (for example,
  they reset on restart). The 429 handling covers any gap: the import slows down but doesn't fail.
- String ids change many type signatures at once. `ty` catches most of them; the phase order keeps every
  phase compiling.
