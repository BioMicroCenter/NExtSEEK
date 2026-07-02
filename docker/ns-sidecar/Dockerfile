# syntax=docker/dockerfile:1.7
# NS shared-cred sidecar image (T16/T17 lean HTTP-forwarder contract). Ported
# verbatim from dmac-assistant/sidecar/Dockerfile (Task 12, G7-11) -- ONLY the
# COPY source paths below are adapted, because this port's build context is
# docker/ns-sidecar/ itself (see task-12-report.md's hermetic docker-build
# guard), not the source's repo-root context (its compose set `context: ..`
# so it COPY'd `sidecar/__init__.py` + `sidecar/app/`). Everything else --
# base image, deps, non-root user, WORKDIR/PYTHONPATH, CMD -- is
# byte-identical to the pinned source.
#
# Import-layout invariant (iter-1 L-5): `python -m sidecar.app.server` with
# PYTHONPATH=/app requires BOTH /app/sidecar/__init__.py and
# /app/sidecar/app/__init__.py to exist inside the image -- i.e. the COPY
# *destination* paths (which create the `sidecar` package name in the
# container) must stay exactly as upstream; only the COPY *source* paths
# (relative to this port's build context) are adapted.
FROM --platform=linux/amd64 python:3.14-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
ENV UV_INSTALL_DIR=/usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh

ENV VIRTUAL_ENV=/opt/sidecar-venv PATH="/opt/sidecar-venv/bin:$PATH"
RUN uv venv /opt/sidecar-venv

RUN uv pip install "httpx>=0.28.1" "websockets~=16.0" "pydantic>=2.9"

COPY __init__.py /app/sidecar/__init__.py
COPY app/ /app/sidecar/app/

RUN useradd -m -u 1001 -s /bin/sh sidecar
USER sidecar
WORKDIR /app
ENV PYTHONPATH=/app
CMD ["python", "-m", "sidecar.app.server"]
