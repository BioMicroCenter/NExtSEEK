FROM ghcr.io/astral-sh/uv:debian

RUN mkdir /app
RUN mkdir -p /var/celery

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ssh/scp client for the Luria SLURM launch backend (NessieAI/chat_nextseek/src/chat_nextseek/luria/ssh.py).
# Required only when PIPELINE_LAUNCH_MODE=luria; harmless otherwise.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

COPY . /app/

# Compile at build time, since nothing may write bytecode at run time (above) and
# `uv sync` does not compile by default. Without it every process recompiled
# Django, torch and the app from source on each start: 10.6 s per Django process
# instead of 5.4 s, and `migrate` 27.7 s instead of 12.0 s (measured 2026-10-12),
# all of it on the path between a restart and the first served request.
RUN UV_COMPILE_BYTECODE=1 uv sync

# Step 1c: generate vendored dmac_assistant BAML router client at build time
# (baml_src is committed; baml_client is gitignored generated output).
RUN mkdir -p /app/NessieAI/dmac_assistant/src/dmac_assistant/router && \
    uv run baml-cli generate --from /app/NessieAI/dmac_assistant/baml_src --no-version-check && \
    test -d /app/NessieAI/dmac_assistant/src/dmac_assistant/router/baml_client

# The app's own source, including the BAML client just generated (uv compiles only
# the venv). compileall writes explicitly, which PYTHONDONTWRITEBYTECODE allows.
# Bytecode is only a speed-up, so a file that does not compile (an untracked
# scratch file in a box's checkout) must not fail the build: Python compiles it
# on import, as it did before.
RUN /app/.venv/bin/python -m compileall -q -j 0 -x '/\.venv/' /app \
    || echo "compileall: some files did not compile; they compile on import instead"

# The venv is final here (the first-party packages are editable installs of this
# tree), so no `uv run` in a running container syncs: a sync re-installs those
# two packages, and on a box without network it fails after a long wait.
ENV UV_NO_SYNC=1

EXPOSE 8000

CMD ["/app/docker/scripts/entrypoint.sh"]
