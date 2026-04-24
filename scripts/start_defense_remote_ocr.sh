#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${COMPOSE_FILE:-./docker-compose.defense-remote-ocr.yml}"
ENV_TEMPLATE="${ENV_TEMPLATE:-./.env.example}"
FORCE_ENV_COPY="${FORCE_ENV_COPY:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DOCKER_BIN="${DOCKER_BIN:-docker}"

resolve_repo_path() {
  local path_value="$1"
  if [[ "${path_value}" = /* ]]; then
    printf '%s\n' "${path_value}"
  else
    printf '%s\n' "${REPO_ROOT}/${path_value#./}"
  fi
}

read_env_value() {
  local env_file="$1"
  local key="$2"
  local default_value="$3"

  if [[ ! -f "${env_file}" ]]; then
    printf '%s\n' "${default_value}"
    return
  fi

  local value
  value="$(grep -E "^${key}=" "${env_file}" | head -n 1 | cut -d= -f2- || true)"
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"

  if [[ -n "${value}" ]]; then
    printf '%s\n' "${value}"
  else
    printf '%s\n' "${default_value}"
  fi
}

COMPOSE_PATH="$(resolve_repo_path "${COMPOSE_FILE}")"
ENV_TEMPLATE_PATH="$(resolve_repo_path "${ENV_TEMPLATE}")"
ENV_PATH="${REPO_ROOT}/.env"
MODEL_DIR_PATH="${REPO_ROOT}/news_models/chandra-ocr-2"

if [[ ! -f "${COMPOSE_PATH}" ]]; then
  echo "Compose file not found: ${COMPOSE_PATH}" >&2
  exit 1
fi

if [[ ! -f "${ENV_PATH}" || "${FORCE_ENV_COPY}" = "1" ]]; then
  if [[ ! -f "${ENV_TEMPLATE_PATH}" ]]; then
    echo "Env template not found: ${ENV_TEMPLATE_PATH}" >&2
    exit 1
  fi
  cp -f "${ENV_TEMPLATE_PATH}" "${ENV_PATH}"
fi

if [[ ! -d "${MODEL_DIR_PATH}" ]]; then
  echo "Model directory not found: ${MODEL_DIR_PATH}" >&2
  exit 1
fi

VLLM_IMAGE_REF="$(read_env_value "${ENV_PATH}" "VLLM_IMAGE" "a-cong-vllm-openai:chandra")"

if ! "${DOCKER_BIN}" image inspect "${VLLM_IMAGE_REF}" >/dev/null 2>&1; then
  echo "vLLM image tag not found locally: ${VLLM_IMAGE_REF}" >&2
  exit 1
fi

"${DOCKER_BIN}" run --rm \
  --entrypoint python3 \
  -v "${MODEL_DIR_PATH}:/models/chandra-ocr-2:ro" \
  "${VLLM_IMAGE_REF}" \
  /opt/a-cong/check_vllm_qwen35_runtime.py \
  --expect-model-type qwen3_5 \
  --model-dir /models/chandra-ocr-2

mkdir -p "${REPO_ROOT}/news_pdfs" "${REPO_ROOT}/news_data" "${REPO_ROOT}/model_cache"

(
  cd "${REPO_ROOT}"
  "${DOCKER_BIN}" compose -f "${COMPOSE_PATH}" config >/dev/null
  "${DOCKER_BIN}" compose -f "${COMPOSE_PATH}" up -d --wait
)

echo "Defense remote-ocr stack started."
