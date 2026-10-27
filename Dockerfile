# Stage 1, the embedding models (#28): both models, at their pinned Hugging Face
# commits, hash-checked (docker/README.md "Embedding models"). It must come first
# (COPY --from names an earlier stage, and compose builds the last one). It COPYs
# named files, not the folder, so a doc edit in docker/models/ re-downloads nothing.
# Its Python is pinned on the RUN line, not with a second ENV UV_PYTHON (the guard
# below reads the first one). The downloader's version cannot change the bytes: every
# file is checked against its sha256.
FROM ghcr.io/astral-sh/uv:0.12.8-debian AS models
COPY docker/models/*.manifest.json docker/models/fetch_models.py docker/models/verify_models.py /src/
RUN uv run --no-project --python 3.14 --with huggingface-hub==1.11.0 python /src/fetch_models.py /src /opt/models \
    && chmod -R a+rX /opt/models

FROM ghcr.io/astral-sh/uv:0.12.8-debian

RUN mkdir /app

# The app runs as uid/gid 10001 (compose `user:`, and USER below). A real passwd entry
# with a home is needed: uv opens its cache under $HOME, and ssh refuses a uid with no
# entry (luria/ssh.py). Every path a service writes is created here owned by 10001, so a
# fresh named volume mounted on it copies that owner up; the list is the writable named
# volumes of `nextseek` and `web` in docker-compose.yml (pinned by
# nextseek_api/tests/repo_guards/test_dockerfile_non_root.py). /app/runs is the Nessie lane's out dir.
RUN useradd -m -u 10001 -U nextseek \
    && install -d -o 10001 -g 10001 /static /media /var/celery /var/lib/attribute-broker \
        /var/tmp/nextseek-throttle /dmac/users /app/runs \
    && install -d -m 0700 -o 10001 -g 10001 /app/.ssh

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# The image's Python. uv takes the newest Python that requires-python (">=3.14")
# allows and downloads it at build time; that became 3.15, which torch publishes
# no wheel for, so every app build failed at `uv sync` (2026-10-12). Keep it equal
# to UV_PYTHON in .github/workflows/ci-pytest.yml (pinned by
# nextseek_api/tests/repo_guards/test_build_context_env_guard.py).
ENV UV_PYTHON=3.14
# uv puts the venv's python under /root (mode 0700) by default, which uid 10001 cannot
# run. Install it where every user can read it.
ENV UV_PYTHON_INSTALL_DIR=/opt/uv-python

# ssh/scp client for the Luria SLURM launch backend (NessieAI/chat_nextseek/src/chat_nextseek/luria/ssh.py).
# Required only when PIPELINE_LAUNCH_MODE=luria; harmless otherwise.
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client \
    && rm -rf /var/lib/apt/lists/*

# The baked models. sentence-transformers reads SENTENCE_TRANSFORMERS_HOME when no
# cache folder is passed (the semantic shortlist), and dmac/settings.py points
# schema_rag there. Offline: the app never downloads a model at run time, and a
# model that is not baked fails loudly. A hand-run Hub download inside this image
# needs -e HF_HUB_OFFLINE=0.
COPY --from=models /opt/models /opt/models
ENV SENTENCE_TRANSFORMERS_HOME=/opt/models HF_HUB_OFFLINE=1

COPY . /app/

# Compile at build time, since nothing may write bytecode at run time (above) and
# `uv sync` does not compile by default. Without it every process recompiled
# Django, torch and the app from source on each start: 10.6 s per Django process
# instead of 5.4 s, and `migrate` 27.7 s instead of 12.0 s (measured 2026-10-12),
# all of it on the path between a restart and the first served request.
RUN UV_COMPILE_BYTECODE=1 uv sync

# Load every baked model offline with the app's own libraries. A pin or a library
# bump the image cannot load stops the build here, before anything restarts.
RUN /app/.venv/bin/python /app/docker/models/verify_models.py --load /opt/models /app/docker/models/*.manifest.json

# Step 1c: generate vendored dmac_assistant BAML router client at build time
# (baml_src is committed; baml_client is gitignored generated output).
RUN mkdir -p /app/NessieAI/dmac_assistant/src/dmac_assistant/router && \
    uv run baml-cli generate --from /app/NessieAI/dmac_assistant/baml_src --no-version-check && \
    test -d /app/NessieAI/dmac_assistant/src/dmac_assistant/router/baml_client

# The evaluator's client (output_dir puts baml_client at chat_nextseek/src/).
RUN uv run baml-cli generate --from /app/NessieAI/chat_nextseek/src/chat_nextseek/evaluator/baml_src --no-version-check && \
    test -d /app/NessieAI/chat_nextseek/src/baml_client

# The app's own source, including the BAML client just generated (uv compiles only
# the venv). compileall writes explicitly, which PYTHONDONTWRITEBYTECODE allows.
# Bytecode is only a speed-up, so a file that does not compile (an untracked
# scratch file in a box's checkout) must not fail the build: Python compiles it
# on import, as it did before.
RUN /app/.venv/bin/python -m compileall -q -j 0 -x '/\.venv/' /app \
    || echo "compileall: some files did not compile; they compile on import instead"

# Runtime-written paths inside the image (chat context refresh, DuckDB session files),
# after compileall so its bytecode is 10001-owned too.
RUN mkdir -p /app/schema_rag/duckdb \
    && chown -R 10001:10001 /app/NessieAI/chat_nextseek/src/chat_nextseek/context /app/schema_rag/duckdb

# The venv is final here (the first-party packages are editable installs of this
# tree), so no `uv run` in a running container syncs: a sync re-installs those
# two packages, and on a box without network it fails after a long wait.
ENV UV_NO_SYNC=1

EXPOSE 8000

USER 10001:10001

CMD ["/app/docker/scripts/entrypoint.sh"]
