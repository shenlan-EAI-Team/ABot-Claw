FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    VPR_HOST=0.0.0.0 \
    VPR_PORT=8030 \
    VPR_DEVICE=auto \
    VPR_DATA_DIR=/app/data \
    VPR_INDEX_PATH=/app/data/index.faiss \
    VPR_DATABASE_PATH=/app/data/index.sqlite3 \
    VPR_CACHE_DIR=/app/data/cache \
    TORCH_HOME=/home/vpr/.cache/torch

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

RUN groupadd --gid 1000 vpr \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin vpr \
    && mkdir -p /app/data/cache/embeddings /app/data/cache/images /home/vpr/.cache/torch \
    && chown -R vpr:vpr /app /home/vpr

COPY --chown=vpr:vpr app ./app

USER vpr

VOLUME ["/app/data", "/home/vpr/.cache/torch"]
EXPOSE 8030

HEALTHCHECK --interval=30s --timeout=5s --start-period=300s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8030/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8030"]

