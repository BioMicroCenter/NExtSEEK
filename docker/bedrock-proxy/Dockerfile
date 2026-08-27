# syntax=docker/dockerfile:1.7
# OI-3 hardened Bedrock auth-proxy image (T2). Ported verbatim from
# dmac-assistant/bedrock-proxy/Dockerfile (PLAN-7 Task 4) -- ONLY the COPY
# source path below is adapted, because this port's build context is
# docker/bedrock-proxy/ itself (see task-4-report.md's hermetic docker-build
# guard), not the source's repo-root context (its compose set `context: ..`
# so it COPY'd `bedrock-proxy/app/`). Everything else -- base image, deps,
# non-root user, healthcheck, CMD -- is byte-identical to the pinned source.
#
# The source dir `bedrock-proxy` is hyphenated (not a valid Python package
# name), but proxy.py imports `from app.config import ProxyConfig`, so we remap
# at COPY time: `app/` -> `/app/app/`. With WORKDIR /app and PYTHONPATH=/app
# the package is importable as `app.proxy` (no hyphen).
FROM --platform=linux/amd64 python:3.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV VIRTUAL_ENV=/opt/proxy-venv PATH="/opt/proxy-venv/bin:$PATH"
RUN uv venv /opt/proxy-venv

# Pinned to the floors the repo already resolves (pyproject.toml: fastapi>=0.116,
# httpx>=0.28.1, uvicorn[standard]>=0.35). The proxy only needs the relay app +
# an ASGI server + the upstream httpx client.
RUN uv pip install "fastapi>=0.116" "uvicorn[standard]>=0.35" "httpx>=0.28.1"

# Remap the app/ package to /app/app/ so it imports as `app.proxy` /
# `app.config` (no hyphen) inside the container. Source path is relative to
# THIS port's build context (docker/bedrock-proxy/), not repo root.
COPY app/ /app/app/

# The slim base ships a system user literally named `proxy` (uid 13), so use a
# distinct name. The uid (1001, non-root) is the load-bearing invariant.
RUN useradd -m -u 1001 -s /bin/sh proxyapp
USER proxyapp
WORKDIR /app
ENV PYTHONPATH=/app

# /healthz returns 200 with no token, so the healthcheck passes even before the
# bearer token is injected at runtime via env_file.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/healthz', timeout=4).getcode()==200 else 1)"]

CMD ["uvicorn", "app.proxy:app", "--host", "0.0.0.0", "--port", "8080"]
