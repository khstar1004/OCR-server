#!/usr/bin/env bash
set -euo pipefail

APP_TAR="${APP_TAR:-./dist/a-cong-ocr_chandra.tar}"
VLLM_TAR="${VLLM_TAR:-./dist/a-cong-vllm-openai_chandra.tar}"
VLLM_IMAGE_TAG="${VLLM_IMAGE_TAG:-a-cong-vllm-openai:chandra}"
SKIP_RUNTIME_VALIDATION="${SKIP_RUNTIME_VALIDATION:-0}"
SKIP_GPU_RUNTIME_VALIDATION="${SKIP_GPU_RUNTIME_VALIDATION:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKER_BIN="${DOCKER_BIN:-docker}"

docker_image_exists() {
  "${DOCKER_BIN}" image inspect "$1" >/dev/null 2>&1
}

resolve_bundle_path() {
  local path_value="$1"
  if [[ "${path_value}" = /* ]]; then
    printf '%s\n' "${path_value}"
  else
    printf '%s\n' "${REPO_ROOT}/${path_value#./}"
  fi
}

APP_TAR_PATH="$(resolve_bundle_path "${APP_TAR}")"
VLLM_TAR_PATH="$(resolve_bundle_path "${VLLM_TAR}")"
MODEL_DIR_PATH="$(resolve_bundle_path "./news_models/chandra-ocr-2")"

if [[ ! -f "${APP_TAR_PATH}" ]]; then
  echo "App image tar not found: ${APP_TAR_PATH}" >&2
  exit 1
fi

"${DOCKER_BIN}" load -i "${APP_TAR_PATH}"

if [[ -f "${VLLM_TAR_PATH}" ]]; then
  "${DOCKER_BIN}" load -i "${VLLM_TAR_PATH}"
else
  echo "vLLM image tar not found: ${VLLM_TAR_PATH}" >&2
  exit 1
fi

if [[ "${SKIP_RUNTIME_VALIDATION}" != "1" ]]; then
  if ! docker_image_exists "${VLLM_IMAGE_TAG}"; then
    echo "vLLM image tag not found after load: ${VLLM_IMAGE_TAG}" >&2
    exit 1
  fi

  if [[ ! -d "${MODEL_DIR_PATH}" ]]; then
    echo "Model directory not found for runtime validation: ${MODEL_DIR_PATH}" >&2
    exit 1
  fi

  if [[ "${SKIP_GPU_RUNTIME_VALIDATION}" != "1" ]]; then
    "${DOCKER_BIN}" run --rm \
      --runtime=nvidia \
      --entrypoint python3 \
      "${VLLM_IMAGE_TAG}" \
      -c "import json, torch; info = {'cuda_available': torch.cuda.is_available(), 'device_count': torch.cuda.device_count(), 'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}; print(json.dumps(info, ensure_ascii=True)); raise SystemExit(0 if torch.cuda.is_available() else 1)"
  fi

  "${DOCKER_BIN}" run --rm \
    --entrypoint python3 \
    -v "${MODEL_DIR_PATH}:/models/chandra-ocr-2:ro" \
    "${VLLM_IMAGE_TAG}" \
    /opt/a-cong/check_vllm_qwen35_runtime.py \
    --expect-model-type qwen3_5 \
    --model-dir /models/chandra-ocr-2
fi

echo "Offline image load complete."
