FROM ghcr.io/astral-sh/uv:debian

RUN mkdir /app
RUN mkdir -p /var/celery

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY . /app/

RUN uv sync

EXPOSE 8000

CMD ["/app/docker/scripts/entrypoint.sh"]
