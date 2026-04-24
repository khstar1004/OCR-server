#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nocodeaidev}"
HOST="${HOST:-nocodeaidev.army.mil}"
INGRESS_CLASS="${INGRESS_CLASS:-nginx}"
STORAGE_CLASS="${STORAGE_CLASS:-local-path}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-harbor-reg-cred}"
GPU_NODE="${GPU_NODE:-nocode-ai-army01}"
APP_PREFIX="${APP_PREFIX:-/a-cong-ocr}"
OCR_PREFIX="${OCR_PREFIX:-/a-cong-ocr-api}"
EXPECTED_GPU_REQUEST="${EXPECTED_GPU_REQUEST:-1}"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

warn() {
  echo "WARN: $*" >&2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

jsonpath() {
  kubectl "$@" 2>/dev/null || true
}

extract_allocated_resource() {
  local node="$1"
  local resource="$2"
  kubectl describe node "${node}" | awk -v resource="${resource}" '
    /^Allocated resources:/ { in_alloc=1; next }
    in_alloc && /^Events:/ { in_alloc=0 }
    in_alloc && $1 == resource { print $2; exit }
  '
}

require_cmd kubectl

log "Kubernetes context"
kubectl config current-context || true
kubectl get nodes -o wide

log "Checking namespace"
kubectl get namespace "${NAMESPACE}" >/dev/null || fail "Namespace not found: ${NAMESPACE}"

log "Checking IngressClass"
kubectl get ingressclass "${INGRESS_CLASS}" >/dev/null || fail "IngressClass not found: ${INGRESS_CLASS}"

log "Checking StorageClass"
kubectl get storageclass "${STORAGE_CLASS}" >/dev/null || fail "StorageClass not found: ${STORAGE_CLASS}"

log "Checking imagePullSecret"
kubectl -n "${NAMESPACE}" get secret "${IMAGE_PULL_SECRET}" >/dev/null || fail "Secret not found in ${NAMESPACE}: ${IMAGE_PULL_SECRET}"

log "Checking GPU node"
kubectl get node "${GPU_NODE}" >/dev/null || fail "GPU node not found: ${GPU_NODE}"
kubectl get node "${GPU_NODE}" -o wide

capacity_gpu="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.capacity.nvidia\.com/gpu}')"
allocatable_gpu="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}')"
allocated_gpu="$(extract_allocated_resource "${GPU_NODE}" "nvidia.com/gpu")"
capacity_gpumem="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.capacity.nvidia\.com/gpumem}')"
allocatable_gpumem="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.allocatable.nvidia\.com/gpumem}')"
capacity_gpucores="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.capacity.nvidia\.com/gpucores}')"

capacity_gpu="${capacity_gpu:-0}"
allocatable_gpu="${allocatable_gpu:-0}"
allocated_gpu="${allocated_gpu:-0}"

echo "nvidia.com/gpu capacity=${capacity_gpu} allocatable=${allocatable_gpu} allocated=${allocated_gpu} required=${EXPECTED_GPU_REQUEST}"
if [[ -n "${capacity_gpumem}" || -n "${allocatable_gpumem}" ]]; then
  echo "nvidia.com/gpumem capacity=${capacity_gpumem:-unknown} allocatable=${allocatable_gpumem:-unknown}"
else
  warn "nvidia.com/gpumem is not exposed on this node. Keeping manifest on nvidia.com/gpu only."
fi
if [[ -n "${capacity_gpucores}" ]]; then
  echo "nvidia.com/gpucores capacity=${capacity_gpucores}"
fi

if ! [[ "${allocatable_gpu}" =~ ^[0-9]+$ && "${allocated_gpu}" =~ ^[0-9]+$ && "${EXPECTED_GPU_REQUEST}" =~ ^[0-9]+$ ]]; then
  warn "Could not parse GPU values as integers. Check kubectl describe node ${GPU_NODE} manually."
else
  free_gpu=$((allocatable_gpu - allocated_gpu))
  echo "estimated_free_nvidia.com/gpu=${free_gpu}"
  if (( free_gpu < EXPECTED_GPU_REQUEST )); then
    fail "Insufficient nvidia.com/gpu on ${GPU_NODE}. free=${free_gpu}, required=${EXPECTED_GPU_REQUEST}. Reduce existing workloads or adjust HAMi allocation before deploy."
  fi
fi

log "Checking HAMi and ingress controller pods"
kubectl get pods -A -o wide | grep -Ei 'hami|ingress|nginx' || warn "Could not find HAMi/Ingress pods by name. Verify with k9s."

log "Checking ResourceQuota"
if kubectl -n "${NAMESPACE}" get resourcequota >/tmp/a-cong-resourcequota.txt 2>/dev/null; then
  cat /tmp/a-cong-resourcequota.txt
else
  warn "No ResourceQuota output or access denied for namespace ${NAMESPACE}."
fi

log "Checking existing Ingress paths for collisions"
ingress_paths="$(kubectl get ingress -A -o jsonpath='{range .items[*]}{.metadata.namespace}{"\t"}{.metadata.name}{"\t"}{range .spec.rules[*]}{.host}{"\t"}{range .http.paths[*]}{.path}{" "}{end}{end}{"\n"}{end}' 2>/dev/null || true)"
echo "${ingress_paths}"
collision=0
while IFS=$'\t' read -r ns name host paths; do
  [[ -z "${ns:-}" ]] && continue
  [[ "${host}" == "${HOST}" ]] || continue
  if [[ "${paths}" == *"${APP_PREFIX}"* || "${paths}" == *"${OCR_PREFIX}"* ]]; then
    if [[ "${ns}/${name}" != "${NAMESPACE}/a-cong-ocr-app" && "${ns}/${name}" != "${NAMESPACE}/a-cong-ocr-api" ]]; then
      warn "Ingress path may collide: ${ns}/${name} host=${host} paths=${paths}"
      collision=1
    fi
  fi
done <<< "${ingress_paths}"
if (( collision != 0 )); then
  fail "Existing Ingress uses ${APP_PREFIX} or ${OCR_PREFIX}. Pick different prefixes before deploy."
fi

log "Checking local-path PVC status if already present"
kubectl -n "${NAMESPACE}" get pvc a-cong-ocr-models-pvc a-cong-ocr-model-cache-pvc a-cong-ocr-runtime-pvc 2>/dev/null || true

log "Preflight passed"
