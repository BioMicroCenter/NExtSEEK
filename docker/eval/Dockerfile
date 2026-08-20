# NExtSEEK-owned eval image for Plan 018 V4-4 (Lane F)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY nextseek_api/eval /app/nextseek_api/eval

RUN pip install --no-cache-dir \
    pydantic orjson numpy scipy pytest \
    "jax[cpu]" "numpyro>=0.21.0" "arviz>=0.21.0,<1.0.0"

ENV PYTHONPATH=/app
ENTRYPOINT ["python"]
