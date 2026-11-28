FROM python:3.12-slim
WORKDIR /app
ARG STAGING_OFFLINE=0
RUN --mount=type=bind,target=/build \
    if [ "$STAGING_OFFLINE" = 1 ]; then set -- --no-index --find-links /build/wheelhouse; else set --; fi; \
    pip install "$@" --no-cache-dir --no-compile --only-binary=:all: --require-hashes -r /build/requirements-linux-amd64.lock \
    && cp -a /build/source/. /app/ \
    && install -m 0644 /build/smoke.py /usr/local/share/staging-smoke.py
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
