# syntax=docker/dockerfile:1.7
# cc-spawner: the one service that holds /var/run/docker.sock (W9). It starts Container-CC turns and runs the three
# fixed SEEK scripts for the app, which no longer needs the socket. Stdlib HTTP server plus the docker SDK.
# Base image and uv pin follow NessieAI/docker/bedrock-proxy/Dockerfile; the SDK version is the one uv.lock pins
# for the app. The process runs as root INSIDE the container so it can use the socket by file ownership; compose
# drops every capability, sets no-new-privileges and a read-only root, and publishes no port.
FROM --platform=linux/amd64 python:3.14-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.12.8 /uv /uvx /usr/local/bin/

ENV VIRTUAL_ENV=/opt/spawner-venv PATH="/opt/spawner-venv/bin:$PATH" PYTHONDONTWRITEBYTECODE=1
RUN uv venv /opt/spawner-venv && uv pip install "docker==7.1.0"

WORKDIR /app
COPY spawner.py /app/spawner.py
COPY seek_scripts/ /app/seek_scripts/

# GET /health answers without a token (only {"ok": bool}), so the healthcheck needs no secret.
HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8080/health', timeout=4).getcode()==200 else 1)"]

CMD ["python", "/app/spawner.py"]
