# The skintrackr server, with the built frontend baked in.
# The image keeps the repo layout under /app (python/, frontend/dist/).

# --- frontend: vite build -> /app/frontend/dist
FROM node:24-slim AS frontend
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


# --- python: locked dependencies + the project in /app/python/.venv
FROM ghcr.io/astral-sh/uv:0.8.22-python3.12-bookworm-slim AS python
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app/python
# dependencies first, so code changes don't reinstall them
COPY python/pyproject.toml python/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY python/README.md ./
COPY python/skintrackr ./skintrackr
RUN uv sync --locked --no-dev --no-editable


# --- runtime
FROM python:3.12-slim-bookworm
WORKDIR /app
COPY --from=python /app/python/.venv /app/python/.venv
COPY --from=frontend /app/frontend/dist /app/frontend/dist
ENV PATH=/app/python/.venv/bin:$PATH \
    SKINTRACKR_FRONTEND_DIR=/app/frontend/dist \
    PYTHONUNBUFFERED=1
CMD ["skintrackr", "serve", "--port", "8080"]
