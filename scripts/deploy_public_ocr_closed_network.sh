#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nocodeaidev}"
HOST="${HOST:-nocodeaidev.army.mil}"
PORT="${PORT:-20443}"
HARBOR_PROJECT="${HARBOR_PROJECT:-nocodeaidev}"
REGISTRY="${REGISTRY:-${HOST}:${PORT}}"
APP_IMAGE="${APP_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr:chandra}"
VLLM_IMAGE="${VLLM_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-vllm-openai:chandra}"
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

require_cmd docker
require_cmd kubectl
require_file "${MANIFEST}"
require_file "dist/a-cong-ocr_chandra.tar"
require_file "dist/a-cong-vllm-openai_chandra.tar"
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
docker load -i dist/a-cong-ocr_chandra.tar
docker load -i dist/a-cong-vllm-openai_chandra.tar

log "Ensuring expected image tags exist"
docker image inspect "${APP_IMAGE}" >/dev/null
docker image inspect "${VLLM_IMAGE}" >/dev/null

log "Validating vLLM image against the exact model folder"
scripts/validate_vllm_image_offline.sh "${VLLM_IMAGE}" "${MODEL_SOURCE}"

if [[ "${SKIP_HARBOR_PUSH}" != "1" ]]; then
  log "Pushing images to Harbor ${REGISTRY}"
  docker push "${APP_IMAGE}"
  docker push "${VLLM_IMAGE}"
else
  log "Skipping Harbor push because SKIP_HARBOR_PUSH=1"
fi

log "Applying manifest"
kubectl apply -f "${MANIFEST}"

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
      image: ${APP_IMAGE}
      command: ["sleep", "86400"]
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

log "Copying model into PVC"
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- rm -rf /models/chandra-ocr-2
kubectl -n "${NAMESPACE}" cp "${MODEL_SOURCE}" a-cong-model-loader:/models/chandra-ocr-2
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- test -f /models/chandra-ocr-2/config.json
kubectl -n "${NAMESPACE}" exec a-cong-model-loader -- ls -al /models/chandra-ocr-2 | head
kubectl -n "${NAMESPACE}" delete pod a-cong-model-loader

log "Restarting deployments"
kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-vllm-ocr
kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-service
kubectl -n "${NAMESPACE}" rollout restart deploy/a-cong-ocr-app

log "Waiting for rollouts"
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-vllm-ocr --timeout=1800s
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-service --timeout=900s
kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-app --timeout=900s

log "Running health checks"
scripts/check_k8s_public_ocr.sh "${NAMESPACE}" "${HOST}" "${PORT}"

log "Done"
cat <<EOF
App URL:
  https://${HOST}:${PORT}/a-cong-ocr/demo/jobs
OCR API:
  https://${HOST}:${PORT}/a-cong-ocr-api/api/v1/ocr/image
Logs:
  kubectl -n ${NAMESPACE} logs deploy/a-cong-vllm-ocr --tail=200
EOF
