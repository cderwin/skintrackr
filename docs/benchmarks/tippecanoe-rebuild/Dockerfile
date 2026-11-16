# tippecanoe at the release the plan pins, built the way the app's Dockerfile stage would
FROM debian:bookworm-slim AS tippecanoe
RUN apt-get update && apt-get install -y --no-install-recommends build-essential git ca-certificates libsqlite3-dev zlib1g-dev \
 && git clone --depth 1 --branch 2.79.0 https://github.com/felt/tippecanoe.git /src \
 && make -C /src -j"$(nproc)" && make -C /src install

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libsqlite3-0 zlib1g time && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir pmtiles==3.4.1
COPY --from=tippecanoe /usr/local/bin/tippecanoe /usr/local/bin/tile-join /usr/local/bin/
