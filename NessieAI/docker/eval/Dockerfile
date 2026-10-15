# NExtSEEK-owned eval image for Plan 018 V4-4 (Lane F)
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

WORKDIR /app
COPY NessieAI/__init__.py NessieAI/paths.py /app/NessieAI/
COPY NessieAI/hibayes /app/NessieAI/hibayes
# What NessieAI/hibayes/human_grade_fit.py needs from outside the package: the corpus
# taxonomy (family_labels, which reads the corpus through paths.py) at import, and on
# the fit path the set3 manifest model and corpus.json. Never NessieAI/history/.
COPY NessieAI/router/__init__.py NessieAI/router/family_labels.py /app/NessieAI/router/
COPY NessieAI/nessie_harness/__init__.py NessieAI/nessie_harness/manifest.py \
     NessieAI/nessie_harness/turn_cost.py NessieAI/nessie_harness/bayes_manifest.py \
     /app/NessieAI/nessie_harness/
COPY NessieAI/tests/nessie_tests/corpus.json /app/NessieAI/tests/nessie_tests/

RUN pip install --no-cache-dir \
    pydantic orjson numpy scipy pytest \
    "jax[cpu]" "numpyro>=0.21.0" "arviz>=0.21.0,<1.0.0"

ENV PYTHONPATH=/app
ENTRYPOINT ["python"]
