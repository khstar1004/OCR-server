#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="${1:-nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra}"
MODEL_DIR="${2:-./news_models/chandra-ocr-2}"
DOCKER_GPU_ARGS="${DOCKER_GPU_ARGS:---runtime=nvidia}"
read -r -a GPU_ARGS <<< "${DOCKER_GPU_ARGS}"

if [[ ! -f "${MODEL_DIR}/config.json" ]]; then
  echo "config.json not found: ${MODEL_DIR}/config.json" >&2
  exit 2
fi

docker image inspect "${IMAGE_TAG}" >/dev/null
MODEL_DIR_ABS="$(cd "${MODEL_DIR}" && pwd)"
SMOKE_CONTAINER="a-cong-vllm-offline-validate-$$"

cleanup() {
  docker rm -f "${SMOKE_CONTAINER}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "[1/4] Checking Chandra model config with the exact vLLM image and no network..."
docker run --rm \
  --network none \
  --entrypoint python3 \
  -e HF_HUB_OFFLINE=1 \
  -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HUB_DISABLE_TELEMETRY=1 \
  -v "${MODEL_DIR_ABS}:/models/chandra-ocr-2:ro" \
  "${IMAGE_TAG}" \
  /opt/a-cong/check_vllm_qwen35_runtime.py \
  --model-dir /models/chandra-ocr-2

echo "[2/4] Checking CUDA visibility from the same image and no network..."
docker run --rm \
  --network none \
  "${GPU_ARGS[@]}" \
  --entrypoint python3 \
  "${IMAGE_TAG}" \
  -c "import json, torch; print(json.dumps({'cuda_available': torch.cuda.is_available(), 'device_count': torch.cuda.device_count()}, ensure_ascii=True)); raise SystemExit(0 if torch.cuda.is_available() else 1)"

echo "[3/4] Starting vLLM serve with the exact model folder and no network..."
docker rm -f "${SMOKE_CONTAINER}" >/dev/null 2>&1 || true
docker run -d \
  --name "${SMOKE_CONTAINER}" \
  --network none \
  "${GPU_ARGS[@]}" \
  --shm-size=16g \
  -e HF_HUB_OFFLINE=1 \
  -e TRANSFORMERS_OFFLINE=1 \
  -e HF_HUB_DISABLE_TELEMETRY=1 \
  -v "${MODEL_DIR_ABS}:/models/chandra-ocr-2:ro" \
  "${IMAGE_TAG}" \
  /models/chandra-ocr-2 \
  --trust-remote-code \
  --served-model-name chandra-ocr-2 \
  --dtype bfloat16 \
  --host 127.0.0.1 \
  --port 8000 \
  --max-model-len 18000 \
  --gpu-memory-utilization 0.85 \
  --enable-prefix-caching \
  --mm-processor-kwargs '{"min_pixels":3136,"max_pixels":6291456}' >/dev/null

for attempt in $(seq 1 90); do
  sleep 10
  state="$(docker inspect -f '{{.State.Status}}' "${SMOKE_CONTAINER}")"
  if [[ "${state}" != "running" ]]; then
    docker logs --tail=240 "${SMOKE_CONTAINER}" >&2 || true
    echo "vLLM smoke container exited before health check passed: ${state}" >&2
    exit 1
  fi
  if docker exec "${SMOKE_CONTAINER}" python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).read(); print('health-ok')" >/dev/null 2>&1; then
    echo "vLLM health-ok"
    break
  fi
  echo "waiting-vllm-health ${attempt}"
  if [[ "${attempt}" == "90" ]]; then
    docker logs --tail=240 "${SMOKE_CONTAINER}" >&2 || true
    echo "vLLM health check timed out" >&2
    exit 1
  fi
done

echo "[4/4] Offline vLLM image validation passed."
