# Tasks across python/ (the backend, uv), frontend/ (vite) and Docker. `just` lists recipes.

default:
    @just --list --unsorted

# --- everything (what CI runs)

# All tests
test: py-test

# All static checks
check: py-check web-typecheck

# --- python (uv)

[group('python')]
[working-directory('python')]
py-sync:
    uv sync

# Run the server locally (needs Redis and the variables in .env)
[group('python')]
[working-directory('python')]
py-serve *args:
    uv run --env-file ../.env skintrackr serve {{ args }}

[group('python')]
[working-directory('python')]
py-test *args:
    uv run pytest {{ args }}

[group('python')]
[working-directory('python')]
py-check:
    uv run ty check

# --- frontend (vite)

[group('frontend')]
[working-directory('frontend')]
web-install:
    npm ci

# Dev server on http://localhost:5173, with hot reload; server routes go to `just py-serve` on :8080
[group('frontend')]
[working-directory('frontend')]
web-dev:
    npm run dev

# Production build into frontend/dist, which `just py-serve` serves
[group('frontend')]
[working-directory('frontend')]
web-build:
    npm run build

[group('frontend')]
[working-directory('frontend')]
web-typecheck:
    npm run typecheck

# --- docker

# Build the production image (what Fly deploys), with VITE_MAPTILER_KEY from .env if it's there
[group('docker')]
build:
    docker build . -t skintrackr --build-arg VITE_MAPTILER_KEY="$(grep -s '^VITE_MAPTILER_KEY=' .env | cut -d= -f2-)"

[group('docker')]
start:
    docker run --rm --env-file=.env -p 8080:8080 skintrackr

# Docker Compose commands for local end-to-end testing
[group('docker')]
compose-up:
    docker compose up --build -d

[group('docker')]
compose-down:
    docker compose down

[group('docker')]
compose-logs:
    docker compose logs -f

[group('docker')]
compose-restart:
    docker compose restart app

# Full stack management
[group('docker')]
dev-up: compose-up
    @echo "Stack is running at http://localhost:8080"
    @echo "Redis is available at localhost:6379"
    @echo "View logs with: just compose-logs"

[group('docker')]
dev-down: compose-down
    @echo "Stack stopped"
