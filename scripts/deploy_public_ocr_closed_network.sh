#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nocodeaidev}"
HOST="${HOST:-nocodeaidev.army.mil}"
PORT="${PORT:-20443}"
HARBOR_PROJECT="${HARBOR_PROJECT:-nocodeaidev}"
REGISTRY="${REGISTRY:-${HOST}:${PORT}}"
UI_IMAGE="${UI_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr-ui:chandra}"
OCR_API_IMAGE="${OCR_API_IMAGE:-${APP_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr:chandra}}"
VLLM_IMAGE="${VLLM_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-vllm-openai:chandra}"
UI_TAR="${UI_TAR:-dist/a-cong-ocr-ui_chandra.tar}"
OCR_API_TAR="${OCR_API_TAR:-dist/a-cong-ocr_chandra.tar}"
VLLM_TAR="${VLLM_TAR:-dist/a-cong-vllm-openai_chandra.tar}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-harbor-reg-cred}"
MANIFEST="${MANIFEST:-k8s/defense-remote-ocr.nocodeaidev.yaml}"
MODEL_SOURCE="${MODEL_SOURCE:-./news_models/chandra-ocr-2}"
TARGET_API_BASE_URL="${TARGET_API_BASE_URL:-}"
TARGET_API_TOKEN="${TARGET_API_TOKEN:-}"
SKIP_HARBOR_PUSH="${SKIP_HARBOR_PUSH:-0}"
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-0}"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "Required file not found: $1" >&2
    exit 2
  fi
}

require_dir() {
  if [[ ! -d "$1" ]]; then
    echo "Required directory not found: $1" >&2
    exit 2
  fi
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Required command not found: $1" >&2
    exit 2
  fi
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
  echo "Required image tag not found after docker load: ${target}" >&2
  echo "Rebuild the tar with that tag, or include one of the fallback local tags." >&2
  exit 2
}

require_cmd docker
require_cmd kubectl
require_cmd python3
require_file "${MANIFEST}"
require_file "${UI_TAR}"
require_file "${OCR_API_TAR}"
require_file "${VLLM_TAR}"
require_dir "${MODEL_SOURCE}"
require_file "${MODEL_SOURCE}/config.json"

if [[ "${SKIP_PREFLIGHT}" != "1" ]]; then
  require_file "scripts/preflight_k8s_hami_public_ocr.sh"
  log "Running k8s/HAMi/Ingress preflight"
  NAMESPACE="${NAMESPACE}" \
  HOST="${HOST}" \
  INGRESS_CLASS="nginx" \
  STORAGE_CLASS="local-path" \
  IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET}" \
  GPU_NODE="${GPU_NODE:-nocode-ai-army01}" \
  EXPECTED_GPU_REQUEST="1" \
  EXPECTED_GPUMEM_PERCENTAGE="30" \
  EXPECTED_GPUCORES="30" \
  scripts/preflight_k8s_hami_public_ocr.sh
else
  log "Skipping k8s/HAMi/Ingress preflight because SKIP_PREFLIGHT=1"
fi

log "Reading model config"
python3 - <<PY
import json
from pathlib import Path
p = Path("${MODEL_SOURCE}") / "config.json"
cfg = json.loads(p.read_text(encoding="utf-8"))
print({"model_type": cfg.get("model_type"), "architectures": cfg.get("architectures")})
PY

log "Loading image tar files"
docker load -i "${UI_TAR}"
docker load -i "${OCR_API_TAR}"
docker load -i "${VLLM_TAR}"

log "Ensuring expected image tags exist"
ensure_image_tag "${UI_IMAGE}" "a-cong-ocr-ui:chandra"
ensure_image_tag "${OCR_API_IMAGE}" "a-cong-ocr:chandra"
ensure_image_tag "${VLLM_IMAGE}" "a-cong-vllm-openai:chandra"

log "Validating vLLM image against the exact model folder"
scripts/validate_vllm_image_offline.sh "${VLLM_IMAGE}" "${MODEL_SOURCE}"

if [[ "${SKIP_HARBOR_PUSH}" != "1" ]]; then
  log "Pushing images to Harbor ${REGISTRY}"
  docker push "${UI_IMAGE}"
  docker push "${OCR_API_IMAGE}"
  docker push "${VLLM_IMAGE}"
else
  log "Skipping Harbor push because SKIP_HARBOR_PUSH=1"
fi

log "Applying manifest"
RENDERED_MANIFEST="$(mktemp /tmp/a-cong-ocr-manifest.XXXXXX.yaml)"
python3 - "${MANIFEST}" "${RENDERED_MANIFEST}" "${UI_IMAGE}" "${OCR_API_IMAGE}" "${VLLM_IMAGE}" "${HOST}" "${NAMESPACE}" "${IMAGE_PULL_SECRET}" <<'PY'
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
ui_image = sys.argv[3]
ocr_api_image = sys.argv[4]
vllm_image = sys.argv[5]
host = sys.argv[6]
namespace = sys.argv[7]
image_pull_secret = sys.argv[8]

text = src.read_text(encoding="utf-8")
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-ui:chandra",
    ui_image,
)
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr:chandra",
    ocr_api_image,
)
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra",
    vllm_image,
)
text = text.replace("host: nocodeaidev.army.mil", f"host: {host}")
text = text.replace("namespace: nocodeaidev", f"namespace: {namespace}")
text = text.replace("- name: harbor-reg-cred", f"- name: {image_pull_secret}")
dst.write_text(text, encoding="utf-8")
PY
kubectl apply -f "${RENDERED_MANIFEST}"
kubectl -n "${NAMESPACE}" patch deploy/a-cong-vllm-ocr \
  --type merge \
  -p '{"spec":{"strategy":{"type":"Recreate"}}}'

log "Pausing vLLM while replacing the model PVC contents"
kubectl -n "${NAMESPACE}" scale deploy/a-cong-vllm-ocr --replicas=0
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-vllm-ocr --timeout=300s

if [[ -n "${TARGET_API_BASE_URL}" || -n "${TARGET_API_TOKEN}" ]]; then
  log "Patching callback target values"
  if [[ -n "${TARGET_API_BASE_URL}" ]]; then
    kubectl -n "${NAMESPACE}" patch configmap a-cong-ocr-config \
      --type merge \
      -p "{\"data\":{\"TARGET_API_BASE_URL\":\"${TARGET_API_BASE_URL}\"}}"
  fi
  if [[ -n "${TARGET_API_TOKEN}" ]]; then
    kubectl -n "${NAMESPACE}" patch secret a-cong-ocr-secret \
      --type merge \
      -p "{\"stringData\":{\"TARGET_API_TOKEN\":\"${TARGET_API_TOKEN}\"}}"
  fi
fi

log "Creating model loader pod"
cat >/tmp/a-cong-model-loader.yaml <<EOF
apiVersion: v1
kind: Pod
metadata:
  name: a-cong-model-loader
  namespace: ${NAMESPACE}
spec:
  restartPolicy: Never
  imagePullSecrets:
    - name: ${IMAGE_PULL_SECRET}
  containers:
    - name: loader
      image: ${UI_IMAGE}
      command: ["sleep", "86400"]
      resources:
        requests:
          cpu: "100m"
          memory: "256Mi"
        limits:
          cpu: "1"
          memory: "1Gi"
      volumeMounts:
        - name: models
          mountPath: /models
  volumes:
    - name: models
      persistentVolumeClaim:
        claimName: a-cong-ocr-models-pvc
EOF

kubectl -n "${NAMESPACE}" delete pod a-cong-model-loader --ignore-not-found=true
kubectl apply -f /tmp/a-cong-model-loader.yaml
kubectl -n "${NAMESPACE}" wait --for=condition=Ready pod/a-cong-model-loader --timeout=300s

MODEL_STAGE_NAME="chandra-ocr-2.uploading.$$"
MODEL_STAGE="/models/${MODEL_STAGE_NAME}"
log "Copying model into PVC staging path ${MODEL_STAGE}"
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- rm -rf "${MODEL_STAGE}"
kubectl -n "${NAMESPACE}" cp "${MODEL_SOURCE}" "a-cong-model-loader:${MODEL_STAGE}"
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- test -f "${MODEL_STAGE}/config.json"
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- sh -c \
  "rm -rf /models/chandra-ocr-2.previous && \
   if [ -d /models/chandra-ocr-2 ]; then mv /models/chandra-ocr-2 /models/chandra-ocr-2.previous; fi && \
   mv '${MODEL_STAGE}' /models/chandra-ocr-2 && \
   rm -rf /models/chandra-ocr-2.previous"
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- ls -al /models/chandra-ocr-2 | head
kubectl -n "${NAMESPACE}" delete pod a-cong-model-loader

log "Starting vLLM and restarting app deployments"
kubectl -n "${NAMESPACE}" scale deploy/a-cong-vllm-ocr --replicas=1
kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-service
kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-playground
kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-app

log "Waiting for rollouts"
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-vllm-ocr --timeout=1800s
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-service --timeout=900s
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-playground --timeout=900s
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-app --timeout=900s

log "Running health checks"
scripts/check_k8s_public_ocr.sh "${NAMESPACE}" "${HOST}" "${PORT}"

log "Done"
cat <<EOF
App URL:
  https://${HOST}:${PORT}/a-cong-ocr/demo/jobs
OCR API:
  https://${HOST}:${PORT}/a-cong-ocr-api/api/v1/ocr/image
OCR Playground:
  https://${HOST}:${PORT}/a-cong-ocr-playground/
Logs:
  kubectl -n ${NAMESPACE} logs deploy/a-cong-vllm-ocr --tail=200
  kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-playground --tail=200
EOF
