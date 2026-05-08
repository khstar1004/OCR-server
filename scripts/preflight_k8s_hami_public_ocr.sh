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
PLAYGROUND_PREFIX="${PLAYGROUND_PREFIX:-/a-cong-ocr-playground}"
EXPECTED_GPU_REQUEST="${EXPECTED_GPU_REQUEST:-1}"
EXPECTED_GPUMEM_PERCENTAGE="${EXPECTED_GPUMEM_PERCENTAGE:-30}"
EXPECTED_GPUCORES="${EXPECTED_GPUCORES:-30}"
REQUIRE_HAMI_PERCENTAGE_RESOURCES="${REQUIRE_HAMI_PERCENTAGE_RESOURCES:-1}"
MANIFEST="${MANIFEST:-k8s/defense-remote-ocr.nocodeaidev.yaml}"

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

log "Checking local manifest contract"
if [[ -f "${MANIFEST}" ]]; then
  grep -Eq "host:[[:space:]]+${HOST}(:[0-9]+)?" "${MANIFEST}" || fail "Manifest does not contain host ${HOST}: ${MANIFEST}"
  if grep -Eq "host:[[:space:]]+${HOST}:[0-9]+" "${MANIFEST}"; then
    fail "Ingress host in YAML must not include a port. Use host: ${HOST}; keep ${HOST}:20443 only in curl/browser URLs."
  fi
  grep -q "ingressClassName: ${INGRESS_CLASS}" "${MANIFEST}" || fail "Manifest does not use ingressClassName: ${INGRESS_CLASS}"
  grep -q "storageClassName: ${STORAGE_CLASS}" "${MANIFEST}" || fail "Manifest does not use storageClassName: ${STORAGE_CLASS}"
  grep -q "claimName: a-cong-ocr-models-pvc" "${MANIFEST}" || fail "Manifest does not mount a-cong-ocr-models-pvc"
  grep -q "claimName: a-cong-ocr-model-cache-pvc" "${MANIFEST}" || fail "Manifest does not mount a-cong-ocr-model-cache-pvc"
  grep -q "claimName: a-cong-ocr-runtime-pvc" "${MANIFEST}" || fail "Manifest does not mount a-cong-ocr-runtime-pvc"
  grep -q "strategy:" "${MANIFEST}" || fail "Manifest must define a Deployment strategy for vLLM"
  grep -q "type: Recreate" "${MANIFEST}" || fail "Manifest must use Recreate strategy for a-cong-vllm-ocr"
  grep -q "schedulerName: hami-scheduler" "${MANIFEST}" || fail "Manifest must schedule vLLM through hami-scheduler"
  grep -q "hami.io/node-scheduler-policy: binpack" "${MANIFEST}" || fail "Manifest missing HAMi node scheduler annotation"
  grep -q "hami.io/gpu-scheduler-policy: spread" "${MANIFEST}" || fail "Manifest missing HAMi GPU scheduler annotation"
  grep -q "nvidia.com/vgpu-mode: hami-core" "${MANIFEST}" || fail "Manifest missing HAMi vGPU mode annotation"
  grep -q "VLLM_API_BASE: \"http://a-cong-vllm-ocr:5000/v1\"" "${MANIFEST}" || fail "OCR API must call internal vLLM over http://a-cong-vllm-ocr:5000/v1"
  grep -q "VLLM_EXPECT_MODEL_TYPE: \"qwen3_5\"" "${MANIFEST}" || fail "Manifest must set VLLM_EXPECT_MODEL_TYPE=qwen3_5"
  grep -q "VLLM_MAX_MODEL_LEN: \"16384\"" "${MANIFEST}" || fail "Manifest must set VLLM_MAX_MODEL_LEN=16384"
  grep -q "VLLM_GPU_MEMORY_UTILIZATION: \"0.80\"" "${MANIFEST}" || fail "Manifest must set VLLM_GPU_MEMORY_UTILIZATION=0.80"
  grep -q -- "--distributed-executor-backend" "${MANIFEST}" || fail "vLLM args missing --distributed-executor-backend"
  grep -q -- "--disable-custom-all-reduce" "${MANIFEST}" || fail "vLLM args missing --disable-custom-all-reduce"
  grep -q -- "--enforce-eager" "${MANIFEST}" || fail "vLLM args missing --enforce-eager"
  grep -q -- "--max-num-seqs" "${MANIFEST}" || fail "vLLM args missing --max-num-seqs"
  grep -Fq "path: ${APP_PREFIX}(/|$)(.*)" "${MANIFEST}" || fail "Manifest missing app Ingress prefix ${APP_PREFIX}"
  grep -Fq "path: ${OCR_PREFIX}(/|$)(.*)" "${MANIFEST}" || fail "Manifest missing OCR API Ingress prefix ${OCR_PREFIX}"
  grep -Fq "path: ${PLAYGROUND_PREFIX}(/|$)(.*)" "${MANIFEST}" || fail "Manifest missing playground Ingress prefix ${PLAYGROUND_PREFIX}"
else
  warn "Manifest file not found locally, skipping static manifest checks: ${MANIFEST}"
fi

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
capacity_gpumem_percentage="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.capacity.nvidia\.com/gpumem-percentage}')"
allocatable_gpumem_percentage="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.allocatable.nvidia\.com/gpumem-percentage}')"
capacity_gpucores="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.capacity.nvidia\.com/gpucores}')"
allocatable_gpucores="$(jsonpath get node "${GPU_NODE}" -o jsonpath='{.status.allocatable.nvidia\.com/gpucores}')"

capacity_gpu="${capacity_gpu:-0}"
allocatable_gpu="${allocatable_gpu:-0}"
allocated_gpu="${allocated_gpu:-0}"

echo "nvidia.com/gpu capacity=${capacity_gpu} allocatable=${allocatable_gpu} allocated=${allocated_gpu} required=${EXPECTED_GPU_REQUEST}"
if [[ -n "${capacity_gpumem}" || -n "${allocatable_gpumem}" ]]; then
  echo "nvidia.com/gpumem capacity=${capacity_gpumem:-unknown} allocatable=${allocatable_gpumem:-unknown}"
else
  warn "nvidia.com/gpumem is not exposed on this node. This is OK when this HAMi cluster uses gpumem-percentage."
fi
if [[ -n "${capacity_gpumem_percentage}" || -n "${allocatable_gpumem_percentage}" ]]; then
  echo "nvidia.com/gpumem-percentage capacity=${capacity_gpumem_percentage:-unknown} allocatable=${allocatable_gpumem_percentage:-unknown} required=${EXPECTED_GPUMEM_PERCENTAGE}"
elif [[ "${REQUIRE_HAMI_PERCENTAGE_RESOURCES}" == "1" ]]; then
  fail "nvidia.com/gpumem-percentage is not exposed on ${GPU_NODE}, but the manifest requests it. Match the manifest to the working HAMi resource names before deploy."
else
  warn "nvidia.com/gpumem-percentage is not exposed on this node."
fi
if [[ -n "${capacity_gpucores}" || -n "${allocatable_gpucores}" ]]; then
  echo "nvidia.com/gpucores capacity=${capacity_gpucores:-unknown} allocatable=${allocatable_gpucores:-unknown} required=${EXPECTED_GPUCORES}"
elif [[ "${REQUIRE_HAMI_PERCENTAGE_RESOURCES}" == "1" ]]; then
  fail "nvidia.com/gpucores is not exposed on ${GPU_NODE}, but the manifest requests it. Match the manifest to the working HAMi resource names before deploy."
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
  if [[ "${paths}" == *"${APP_PREFIX}"* || "${paths}" == *"${OCR_PREFIX}"* || "${paths}" == *"${PLAYGROUND_PREFIX}"* ]]; then
    if [[ "${ns}/${name}" != "${NAMESPACE}/a-cong-ocr-app" && "${ns}/${name}" != "${NAMESPACE}/a-cong-ocr-api" && "${ns}/${name}" != "${NAMESPACE}/a-cong-ocr-playground" ]]; then
      warn "Ingress path may collide: ${ns}/${name} host=${host} paths=${paths}"
      collision=1
    fi
  fi
done <<< "${ingress_paths}"
if (( collision != 0 )); then
  fail "Existing Ingress uses ${APP_PREFIX}, ${OCR_PREFIX}, or ${PLAYGROUND_PREFIX}. Pick different prefixes before deploy."
fi

log "Checking local-path PVC status if already present"
kubectl -n "${NAMESPACE}" get pvc a-cong-ocr-models-pvc a-cong-ocr-model-cache-pvc a-cong-ocr-runtime-pvc 2>/dev/null || true

log "Preflight passed"
