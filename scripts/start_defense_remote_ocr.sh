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

assert_defense_password() {
  local password="${1:-}"
  case "${password}" in
    ""|"admin123!"|"roqkfrhk1!"|"CHANGE_ME"* )
      echo "PLAYGROUND_ADMIN_PASSWORD must be changed in .env before starting the defense-network stack." >&2
      exit 1
      ;;
  esac
  if (( ${#password} < 12 )); then
    echo "PLAYGROUND_ADMIN_PASSWORD must be at least 12 characters for defense-network startup." >&2
    exit 1
  fi
}

assert_no_known_public_endpoint() {
  local key="$1"
  local value="${2:-}"
  local blocked
  for blocked in "121.153.7.193" "14.50.225.74" "183.107.244.138"; do
    if [[ "${value}" == *"${blocked}"* ]]; then
      echo "${key} still points to a known public/test endpoint (${blocked}). Clear it or replace it with the approved internal address in .env." >&2
      exit 1
    fi
  done
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

assert_defense_password "$(read_env_value "${ENV_PATH}" "PLAYGROUND_ADMIN_PASSWORD" "")"
assert_no_known_public_endpoint "TARGET_API_BASE_URL" "$(read_env_value "${ENV_PATH}" "TARGET_API_BASE_URL" "")"
assert_no_known_public_endpoint "LLM_BASE_URL" "$(read_env_value "${ENV_PATH}" "LLM_BASE_URL" "")"

if [[ ! -d "${MODEL_DIR_PATH}" ]]; then
  echo "Model directory not found: ${MODEL_DIR_PATH}" >&2
  exit 1
fi

UI_IMAGE_REF="$(read_env_value "${ENV_PATH}" "UI_IMAGE" "a-cong-ocr-ui:chandra")"
OCR_IMAGE_REF="$(read_env_value "${ENV_PATH}" "OCR_IMAGE" "a-cong-ocr:chandra")"
VLLM_IMAGE_REF="$(read_env_value "${ENV_PATH}" "VLLM_IMAGE" "a-cong-vllm-openai:chandra")"

if ! "${DOCKER_BIN}" image inspect "${UI_IMAGE_REF}" >/dev/null 2>&1; then
  echo "UI image tag not found locally: ${UI_IMAGE_REF}" >&2
  exit 1
fi

if ! "${DOCKER_BIN}" image inspect "${OCR_IMAGE_REF}" >/dev/null 2>&1; then
  echo "OCR image tag not found locally: ${OCR_IMAGE_REF}" >&2
  exit 1
fi

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
