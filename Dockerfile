# The skintrackr server, with the frontend (static/) baked in.
# The image keeps the repo layout under /app (python/, static/).

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
COPY static /app/static
ENV PATH=/app/python/.venv/bin:$PATH \
    SKINTRACKR_STATIC_DIR=/app/static \
    PYTHONUNBUFFERED=1
CMD ["skintrackr", "serve", "--port", "8080"]
