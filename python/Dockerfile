# Production image for the Python server. Build from the repository root (it needs ../static):
#   docker build -f python/Dockerfile -t skintrackr-py .
FROM ghcr.io/astral-sh/uv:0.8.22-python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0

WORKDIR /usr/src/app
# dependencies first, so code changes don't reinstall them
COPY python/pyproject.toml python/uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY python/ ./
RUN uv sync --locked --no-dev --no-editable


FROM python:3.12-slim-bookworm
COPY --from=builder /usr/src/app/.venv /usr/src/app/.venv
COPY static /usr/src/static
ENV PATH=/usr/src/app/.venv/bin:$PATH \
    SKINTRACKR_STATIC_DIR=/usr/src/static \
    PYTHONUNBUFFERED=1
CMD ["skintrackr", "serve", "--port", "8080"]
