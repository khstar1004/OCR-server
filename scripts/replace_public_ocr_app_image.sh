#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nocodeaidev}"
HOST="${HOST:-nocodeaidev.army.mil}"
PORT="${PORT:-20443}"
HARBOR_PROJECT="${HARBOR_PROJECT:-nocodeaidev}"
REGISTRY="${REGISTRY:-${HOST}:${PORT}}"
UI_IMAGE="${UI_IMAGE:-${APP_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr-ui:chandra}}"
UI_TAR="${UI_TAR:-${APP_TAR:-dist/a-cong-ocr-ui_chandra.tar}}"
UPDATE_OCR_API_IMAGE="${UPDATE_OCR_API_IMAGE:-0}"
OCR_API_IMAGE="${OCR_API_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr:chandra}"
OCR_API_TAR="${OCR_API_TAR:-dist/a-cong-ocr_chandra.tar}"
UPDATE_VLLM_IMAGE="${UPDATE_VLLM_IMAGE:-0}"
VLLM_IMAGE="${VLLM_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-vllm-openai:chandra}"
VLLM_TAR="${VLLM_TAR:-dist/a-cong-vllm-openai_chandra.tar}"
RESTART_APP="${RESTART_APP:-1}"
RESTART_PLAYGROUND="${RESTART_PLAYGROUND:-1}"
RESTART_OCR_SERVICE="${RESTART_OCR_SERVICE:-0}"
RESTART_VLLM="${RESTART_VLLM:-0}"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_file() {
  [[ -f "$1" ]] || fail "Required file not found: $1"
}

ensure_image_tag() {
  local target="$1"
  shift
  if docker image inspect "${target}" >/dev/null 2>&1; then
    return
  fi
  local source
  for source in "$@"; do
    if [[ -n "${source}" ]] && docker image inspect "${source}" >/dev/null 2>&1; then
      log "Tagging loaded image ${source} as ${target}"
      docker tag "${source}" "${target}"
      return
    fi
  done
  fail "Required image tag not found after docker load: ${target}"
}

require_cmd docker
require_cmd kubectl
require_file "${UI_TAR}"
if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
  require_file "${OCR_API_TAR}"
  RESTART_OCR_SERVICE=1
fi
if [[ "${UPDATE_VLLM_IMAGE}" == "1" ]]; then
  require_file "${VLLM_TAR}"
fi

log "Checking existing Kubernetes objects"
kubectl -n "${NAMESPACE}" get deploy/a-cong-ocr-app >/dev/null
kubectl -n "${NAMESPACE}" get deploy/a-cong-ocr-playground >/dev/null
if [[ "${UPDATE_OCR_API_IMAGE}" == "1" || "${RESTART_OCR_SERVICE}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" get deploy/a-cong-ocr-service >/dev/null
fi
if [[ "${UPDATE_VLLM_IMAGE}" == "1" || "${RESTART_VLLM}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" get deploy/a-cong-vllm-ocr >/dev/null
fi

log "Loading UI image tar"
docker load -i "${UI_TAR}"
ensure_image_tag "${UI_IMAGE}" "a-cong-ocr-ui:chandra"

if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
  log "Loading OCR API image tar"
  docker load -i "${OCR_API_TAR}"
  ensure_image_tag "${OCR_API_IMAGE}" "a-cong-ocr:chandra"
fi

if [[ "${UPDATE_VLLM_IMAGE}" == "1" ]]; then
  log "Loading vLLM image tar"
  docker load -i "${VLLM_TAR}"
  ensure_image_tag "${VLLM_IMAGE}" "a-cong-vllm-openai:chandra"
fi

log "Pushing image(s) to Harbor ${REGISTRY}"
docker push "${UI_IMAGE}"
if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
  docker push "${OCR_API_IMAGE}"
fi
if [[ "${UPDATE_VLLM_IMAGE}" == "1" ]]; then
  docker push "${VLLM_IMAGE}"
fi

log "Patching app and playground deployments to use ${UI_IMAGE}"
kubectl -n "${NAMESPACE}" patch deploy/a-cong-ocr-app \
  --type strategic \
  -p "{\"spec\":{\"template\":{\"spec\":{\"initContainers\":[{\"name\":\"init-runtime-dirs\",\"image\":\"${UI_IMAGE}\"}],\"containers\":[{\"name\":\"app\",\"image\":\"${UI_IMAGE}\"}]}}}}"
kubectl -n "${NAMESPACE}" patch deploy/a-cong-ocr-playground \
  --type strategic \
  -p "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"playground\",\"image\":\"${UI_IMAGE}\"}]}}}}"

if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
  log "Patching OCR API deployment to use ${OCR_API_IMAGE}"
  kubectl -n "${NAMESPACE}" patch deploy/a-cong-ocr-service \
    --type strategic \
    -p "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"ocr-service\",\"image\":\"${OCR_API_IMAGE}\"}]}}}}"
fi

if [[ "${UPDATE_VLLM_IMAGE}" == "1" ]]; then
  log "Patching vLLM deployment to use ${VLLM_IMAGE}"
  kubectl -n "${NAMESPACE}" patch deploy/a-cong-vllm-ocr \
    --type merge \
    -p '{"spec":{"strategy":{"type":"Recreate"}}}'
  kubectl -n "${NAMESPACE}" patch deploy/a-cong-vllm-ocr \
    --type strategic \
    -p "{\"spec\":{\"template\":{\"spec\":{\"containers\":[{\"name\":\"vllm-ocr\",\"image\":\"${VLLM_IMAGE}\"}]}}}}"
  RESTART_VLLM=1
fi

if [[ "${RESTART_VLLM}" == "1" ]]; then
  log "Restarting vLLM with Recreate strategy"
  kubectl -n "${NAMESPACE}" patch deploy/a-cong-vllm-ocr \
    --type merge \
    -p '{"spec":{"strategy":{"type":"Recreate"}}}'
  kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-vllm-ocr
fi
if [[ "${RESTART_OCR_SERVICE}" == "1" ]]; then
  log "Restarting OCR API"
  kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-service
fi
if [[ "${RESTART_PLAYGROUND}" == "1" ]]; then
  log "Restarting playground"
  kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-playground
fi
if [[ "${RESTART_APP}" == "1" ]]; then
  log "Restarting app"
  kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-app
fi

log "Waiting for rollouts"
if [[ "${RESTART_VLLM}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-vllm-ocr --timeout=1800s
fi
if [[ "${RESTART_OCR_SERVICE}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-service --timeout=900s
fi
if [[ "${RESTART_PLAYGROUND}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-playground --timeout=900s
fi
if [[ "${RESTART_APP}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-app --timeout=900s
fi

log "Done"
cat <<EOF
App URL:
  https://${HOST}:${PORT}/a-cong-ocr/demo/jobs
OCR API:
  https://${HOST}:${PORT}/a-cong-ocr-api/api/v1/ocr/image
Playground:
  https://${HOST}:${PORT}/a-cong-ocr-playground/
Health:
  scripts/check_k8s_public_ocr.sh ${NAMESPACE} ${HOST} ${PORT}
EOF
