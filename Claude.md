# Claude.md - Development Guide for Skintrackr

This file provides guidelines and context for Claude Code when working on the Skintrackr project.

## Project Overview

**Skintrackr** is a webhook server for Strava activity notifications with OAuth integration and Redis-based token management. It processes Strava webhooks, manages OAuth tokens securely, exports activity data to GPX, and archives activities (metadata + GPX track) to S3-compatible blob storage.

## Architecture Overview

### Core Components

1. **app/server.go** - Main HTTP server setup, routes, and `ServerState` management
2. **app/strava.go** - ALL Strava API interactions via `StravaClient`
3. **app/store.go** - ALL Redis operations and token management via `Store`
4. **app/blob_store.go** - ALL blob-storage (S3-compatible) operations via `BlobStore`; orchestrates `PersistActivity` (download from Strava → upload to bucket)
5. **app/oauth2.go** - OAuth flow handlers (uses `StravaClient` for API calls)
6. **app/webhooks.go** - Webhook subscription management and inbound push-event handling (uses `StravaClient`, `Store`, and `BlobStore`)
7. **app/api.go** - Authenticated JSON/file API handlers (`handleExportTrack`, `handlePersistActivity`)
8. **app/token_api.go** - JWT token generation/verification/revocation handlers and request authentication (`AuthenticateRequest`)
9. **app/crypto.go** - JWT signing/verification and AES encryption helpers
10. **app/config.go** - Environment configuration, including `BlobStorageConfig`

### Architecture Rules

#### **CRITICAL: Separation of Concerns**

1. **All Strava API requests MUST go through `StravaClient` in `strava.go`**
   - Use `performRequest()` for simple requests
   - Use `performRequestForm()` for form POST requests
   - Use `performRequestWithHeaders()` for custom headers
   - Never make raw `http.Client` calls to Strava outside of `StravaClient`

2. **All Redis operations MUST go through `Store` in `store.go`**
   - Token storage, retrieval, and refresh logic
   - Encryption/decryption of sensitive data
   - Never access Redis directly outside of `Store`

3. **All blob-storage operations MUST go through `BlobStore` in `blob_store.go`**
   - Uses the AWS SDK v2 (`s3` client) against an S3-compatible endpoint configured via `BlobStorageConfig`
   - `PersistActivity` is the entry point: it fetches the activity and GPX track via `StravaClient`, then uploads them under `activities/{athleteId}/...`
   - Never construct an `s3.Client` or make raw bucket calls outside of `BlobStore`

4. **Authentication**
   - Protected handlers MUST call `s.AuthenticateRequest()` (in `token_api.go`) first and use the returned `athleteId`
   - JWT and encryption primitives live in `crypto.go`; do not hand-roll token or crypto logic elsewhere

5. **Handler Logic Flow**
   - Handlers (`api.go`, `oauth2.go`, `webhooks.go`, `token_api.go`) → call `StravaClient`, `Store`, and `BlobStore` methods
   - Keep business logic in the appropriate layer; handlers stay thin

### Data Flow

```
Request (webhook / API)
        ↓
Handler (api.go / oauth2.go / webhooks.go / token_api.go)
        ↓
  ┌─────────────┬───────────────┬────────────────────┐
  ↓             ↓               ↓                     ↓
StravaClient   Store          BlobStore            (auth via
(strava.go)    (store.go)     (blob_store.go)       token_api.go +
  ↓             ↓               ↓                    crypto.go)
Strava API     Redis          S3-compatible bucket
        ↓
     Response
```

Webhook activity `create`/`update` events trigger `BlobStore.PersistActivity`
asynchronously (in a goroutine) so the webhook is acknowledged promptly.

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
   # Run tests
   just test

   # Build to verify
   go build ./...

   # Ensure code is formatted
   go fmt ./...
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

#### Using Just (Recommended)
```bash
# Run tests
just test

# Build Docker image
just build

# Start with docker-compose (full stack)
just dev-up

# View logs
just compose-logs

# Stop stack
just dev-down

# Restart just the app
just compose-restart
```

#### Using Go Directly
```bash
# Run tests
go test ./...

# Run tests with coverage
go test ./... -cover

# Run tests verbosely
go test ./app -v

# Build
go build ./...

# Run locally (requires Redis)
go run .
```

### Docker Operations

```bash
# Build image
docker build . -t skintrackr

# Run with local Redis
docker compose up --build -d

# View logs
docker compose logs -f app

# Stop
docker compose down
```

### Deployment to Fly.io

#### Prerequisites
- Install `flyctl`: https://fly.io/docs/hands-on/install-flyctl/
- Authenticate: `fly auth login`

#### Deploy Process

```bash
# 1. Verify fly.toml is correct
cat fly.toml

# 2. Deploy to Fly.io
fly deploy

# 3. Check status
fly status

# 4. View logs
fly logs

# 5. Open app in browser
fly open
```

#### Set Secrets (First Time Setup)
```bash
fly secrets set APP_SECRET=$(openssl rand -hex 32)
fly secrets set STRAVA_CLIENT_ID=your_client_id
fly secrets set STRAVA_CLIENT_SECRET=your_client_secret
fly secrets set UPSTASH_REDIS_URL=your_redis_url
```

#### Common Fly.io Commands
```bash
# Scale app
fly scale count 2

# Check machine status
fly status

# SSH into machine
fly ssh console

# Restart app
fly apps restart skintrackr

# View app info
fly info
```

### Environment Setup

Required environment variables (see `.env.example`):
- `APP_BASE_URL` - Base URL for OAuth callbacks
- `APP_SECRET` - 64-char hex string for encryption
- `STRAVA_CLIENT_ID` - From Strava API settings
- `STRAVA_CLIENT_SECRET` - From Strava API settings
- `UPSTASH_REDIS_URL` - Redis connection string
- `AWS_ACCESS_KEY_ID` - Access key for the S3-compatible blob store
- `AWS_SECRET_ACCESS_KEY` - Secret key for the S3-compatible blob store

The blob-storage bucket name and endpoint are set in `LoadBlobStorageConfig()` in `config.go`.

## Testing Guidelines

### Unit Test Requirements

1. **All new features MUST have unit tests**
2. **Use httptest for HTTP mocking** - Never make real network calls
3. **Test files naming**: `*_test.go` in the same package

### Running Tests

```bash
# All tests
go test ./...

# Specific package
go test ./app

# With coverage
go test ./... -coverprofile=coverage.out
go tool cover -html=coverage.out

# Verbose output
go test ./app -v
```

### Test Coverage Expectations

- New code should maintain or improve coverage
- Critical paths (auth, token management) must be well-tested
- Use table-driven tests for multiple scenarios

## Code Style & Standards

### **CRITICAL: Compact, Organized, Non-Duplicated Code**

Every change MUST prioritize the following, in order:

1. **Do not duplicate code that already exists.** Before writing anything, look for an existing helper, method, or pattern that already does the job and reuse it. If similar logic exists in two places, factor out a shared function rather than copy-pasting. This is the single most important rule — duplicated logic is the primary thing to avoid.
2. **Be consistent with the surrounding code.** Match the existing style, naming, error-handling, logging (`slog` with structured context), and architectural layering (`StravaClient` / `Store` / `BlobStore`). New code should be indistinguishable from the code already in the file.
3. **Stay compact and organized.** Prefer the smallest clear change that solves the problem. Keep handlers thin, push logic into the appropriate layer, and avoid speculative abstraction or unnecessary scaffolding.

When a new feature would otherwise duplicate existing behavior, refactor the shared piece instead — leave the codebase smaller and more cohesive, not larger.

### Go Conventions

1. **Use `gofmt`** - Always format before committing
   ```bash
   go fmt ./...
   ```

2. **Follow effective Go** - https://go.dev/doc/effective_go

3. **Error Handling**
   - Always check errors
   - Use structured logging with `slog`
   - Include context in error messages

4. **Naming Conventions**
   - Exported: `UpperCamelCase`
   - Unexported: `lowerCamelCase`
   - Interfaces: `er` suffix (e.g., `Reader`, `Writer`) or descriptive names

### Project-Specific Guidelines

1. **HTTP Client Usage**
   - ✅ Use `StravaClient.performRequest*()` methods
   - ❌ Never use `http.Get`, `http.Post`, etc. directly for Strava

2. **Token Management**
   - ✅ Use `Store.FetchToken()`, `Store.SaveToken()`
   - ❌ Never access Redis directly outside `Store`

3. **Blob Storage**
   - ✅ Use `BlobStore` methods (e.g. `PersistActivity()`)
   - ❌ Never construct an `s3.Client` or call the bucket directly outside `BlobStore`

5. **Configuration**
   - Use environment variables via `Config`
   - Never hardcode secrets or URLs

6. **Logging**
   - Use structured logging: `slog.Info()`, `slog.Error()`
   - Include relevant context (athlete_id, activity_id, etc.)
   - Log errors before returning them

## Common Tasks

### Adding a New Strava API Endpoint

1. Add method to `StravaClient` in `app/strava.go`
2. Use existing `performRequest*()` methods
3. Add unit tests in `app/strava_test.go`
4. Document the method with comments

Example:
```go
func (c *StravaClient) GetAthlete() (*Athlete, error) {
    body, err := c.performRequest("GET", "https://www.strava.com/api/v3/athlete", nil)
    if err != nil {
        return nil, err
    }

    var athlete Athlete
    if err := json.NewDecoder(body).Decode(&athlete); err != nil {
        return nil, err
    }
    return &athlete, nil
}
```

### Adding Redis Operations

1. Add method to `Store` in `app/store.go`
2. Use `s.client` (Redis client) and `s.ctx` (context)
3. Handle encryption/decryption if needed
4. Add error logging (slog)
5. Write tests

### Adding Blob Storage Operations

1. Add method to `BlobStore` in `app/blob_store.go`
2. Build the `s3.Client` from `bs.config.BlobStorageConfig` (reuse the existing pattern in `writeFileToBlob`); never create clients elsewhere
3. Add error logging (slog) and return errors to the caller
4. Write tests (stub the S3 endpoint with `httptest`)

### Adding New HTTP Handlers

1. Add handler method to `ServerState` in `api.go` (or the most relevant existing handler file — reuse it rather than creating a new file)
2. Register route in `server.go` `RunForever()`
3. Authenticate with `s.AuthenticateRequest()` for protected endpoints
4. Reuse existing `StravaClient`, `Store`, and `BlobStore` methods — do not duplicate their logic in the handler
5. Return appropriate HTTP status codes via `echo.Context`

## Troubleshooting

### Build Issues

```bash
# Clean and rebuild
go clean
go mod tidy
go build ./...
```

### Test Failures

- Check if constants/variables need to be mutable for testing
- Ensure mock servers are set up correctly
- Verify test data matches expected formats

### Docker Issues

```bash
# Clean rebuild
docker compose down -v
docker compose build --no-cache
docker compose up
```

### Deployment Issues

```bash
# Check Fly.io logs
fly logs

# Check secrets are set
fly secrets list

# Verify fly.toml
fly config validate
```

## Security Considerations

1. **Never commit secrets** - Use environment variables
2. **Encrypt sensitive data at rest** - Use `encryptToken()`/`decryptToken()` in `store.go`
3. **Validate OAuth state** - Implement CSRF protection
4. **Use HTTPS** - Enforced in production via Fly.io
5. **Token rotation** - Refresh tokens expire, handle gracefully

## Resources

- **Strava API Docs**: https://developers.strava.com/docs/reference/
- **Fly.io Docs**: https://fly.io/docs/
- **Go Docs**: https://go.dev/doc/
- **Echo Framework**: https://echo.labstack.com/
- **Redis Go Client**: https://redis.uptrace.dev/
- **AWS SDK for Go v2 (S3)**: https://aws.github.io/aws-sdk-go-v2/docs/

## Getting Help

- Check existing tests for examples
- Review similar existing code
- Consult Strava API documentation
- Read Echo framework documentation for HTTP handling

---

**Remember**: Write compact, organized code that reuses what already exists and stays consistent with the surrounding style — never duplicate logic. Maintain separation of concerns (Strava API in `strava.go`, Redis in `store.go`, blob storage in `blob_store.go`, auth/crypto in `token_api.go`/`crypto.go`), always run tests before creating PRs, and follow gitflow with PRs for all changes.
