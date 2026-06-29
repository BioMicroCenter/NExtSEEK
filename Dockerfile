FROM ghcr.io/astral-sh/uv:debian

RUN mkdir /app
RUN mkdir -p /var/celery

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . /app/

RUN uv sync

# Step 1c: generate vendored dmac_assistant BAML router client at build time
# (baml_src is committed; baml_client is gitignored generated output).
RUN mkdir -p /app/dmac_assistant/src/dmac_assistant/router && \
    uv run baml-cli generate --from /app/dmac_assistant/baml_src --no-version-check && \
    test -d /app/dmac_assistant/src/dmac_assistant/router/baml_client

EXPOSE 8000

CMD ["/app/docker/scripts/entrypoint.sh"]
