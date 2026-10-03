# laya router sidecar (JevLevROUTING, SPEC s10). CPU torch, laya pinned with hashes, NO weights in the image:
# the fine-tuned checkpoint is mounted read-only at /models from docker/laya/models/<revision>/.
# Build is the operator's step on the box, never the laptop. No EXPOSE and no published port, ever: the compose
# service joins the internal laya-net only.
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HUB_OFFLINE=1 \
    LAYA_MODEL_DIR=/models

WORKDIR /app
COPY requirements.lock .
# The +cpu torch wheel lives on the pytorch index; every file is hash-checked, so the index order cannot matter.
RUN pip install --no-cache-dir --require-hashes \
        --index-url https://download.pytorch.org/whl/cpu --extra-index-url https://pypi.org/simple \
        -r requirements.lock

COPY serve_wrapper.py .
RUN useradd --system --no-create-home laya
USER laya

CMD ["python", "serve_wrapper.py"]
