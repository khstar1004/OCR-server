FROM nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
ARG TORCH_PACKAGES="torch==2.6.0+cu124 torchvision==0.21.0+cu124"
ARG CHANDRA_MODEL_ID=datalab-to/chandra-ocr-2
ARG CHANDRA_MODEL_DIR=/models/chandra-ocr-2
ARG PRELOAD_CHANDRA=false
ARG ACONG_BUILD_VERSION=local
ARG ACONG_BUILD_DATE=unknown

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=docker \
    API_PREFIX=/api/v1 \
    WATCH_DIR=/data/watch \
    DATA_DIR=/data/runtime \
    DATABASE_URL=sqlite:////data/runtime/db/news_ocr.db \
    AUTO_CREATE_TABLES=true \
    INPUT_ROOT=/data/watch \
    OUTPUT_ROOT=/data/runtime/output \
    MODELS_ROOT=/models \
    OCR_BACKEND=chandra \
    OCR_OFFLINE=true \
    OCR_DEVICE=gpu \
    OCR_RETRY_LOW_QUALITY=true \
    PDF_RENDER_DPI=300 \
    CHANDRA_MODEL_ID=${CHANDRA_MODEL_ID} \
    CHANDRA_MODEL_DIR=${CHANDRA_MODEL_DIR} \
    CHANDRA_PROMPT_TYPE=ocr_layout \
    CHANDRA_BATCH_SIZE=1 \
    CHANDRA_DEVICE_MAP=auto \
    CHANDRA_DTYPE=bfloat16 \
    HF_HOME=/opt/model-cache/huggingface \
    HF_HUB_DISABLE_TELEMETRY=1

LABEL org.opencontainers.image.title="army-ocr App" \
      org.opencontainers.image.description="army-ocr application image for closed-network carry-in" \
      org.opencontainers.image.version="${ACONG_BUILD_VERSION}" \
      org.opencontainers.image.created="${ACONG_BUILD_DATE}"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 \
        python3-pip \
        python3-venv \
        build-essential \
        ca-certificates \
        curl \
        git \
        libglib2.0-0 \
        libgl1 \
        libgomp1 \
        libsm6 \
        libxext6 \
        libxrender1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
COPY scripts/preload_chandra_model.py /app/scripts/preload_chandra_model.py

RUN python3 -m pip install --upgrade pip setuptools wheel \
    && python3 -m pip install --index-url ${TORCH_INDEX_URL} ${TORCH_PACKAGES} \
    && python3 -m pip install -r /app/requirements.txt

RUN mkdir -p /data/watch /data/runtime/db /data/runtime/output /models /opt/model-cache \
    && if [ "${PRELOAD_CHANDRA}" = "true" ]; then \
        python3 /app/scripts/preload_chandra_model.py \
          --model-id "${CHANDRA_MODEL_ID}" \
          --target-dir "${CHANDRA_MODEL_DIR}"; \
       fi

COPY app /app/app
COPY templates /app/templates
COPY static /app/static
COPY scripts /app/scripts
COPY README.md /app/README.md
COPY docker-compose.yml /app/docker-compose.yml
COPY .env.example /app/.env.example

VOLUME ["/data/watch", "/data/runtime", "/models", "/opt/model-cache"]

EXPOSE 8000

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
