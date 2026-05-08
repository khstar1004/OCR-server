#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nocodeaidev}"
HOST="${HOST:-nocodeaidev.army.mil}"
PORT="${PORT:-20443}"
HARBOR_PROJECT="${HARBOR_PROJECT:-nocodeaidev}"
REGISTRY="${REGISTRY:-${HOST}:${PORT}}"
APP_UI_IMAGE="${APP_UI_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr-app-ui:chandra}"
PLAYGROUND_IMAGE="${PLAYGROUND_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr-playground:chandra}"
OCR_API_IMAGE="${OCR_API_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr-api:chandra}"
VLLM_IMAGE="${VLLM_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-vllm-openai:chandra}"
APP_UI_TAR="${APP_UI_TAR:-dist/a-cong-ocr-app-ui_chandra.tar}"
PLAYGROUND_TAR="${PLAYGROUND_TAR:-dist/a-cong-ocr-playground_chandra.tar}"
OCR_API_TAR="${OCR_API_TAR:-dist/a-cong-ocr-api_chandra.tar}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-harbor-reg-cred}"
MANIFEST="${MANIFEST:-k8s/defense-remote-ocr.nocodeaidev.yaml}"
PLAYGROUND_ADMIN_PASSWORD="${PLAYGROUND_ADMIN_PASSWORD:-}"
TARGET_API_BASE_URL="${TARGET_API_BASE_URL:-}"
TARGET_API_TOKEN="${TARGET_API_TOKEN:-}"
SKIP_HARBOR_PUSH="${SKIP_HARBOR_PUSH:-1}"
SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-1}"
SKIP_PVC_APPLY="${SKIP_PVC_APPLY:-1}"
CLEANUP_SMOKE_PODS="${CLEANUP_SMOKE_PODS:-1}"
RUN_EXTERNAL_CHECKS="${RUN_EXTERNAL_CHECKS:-1}"
RUN_CAPACITY_CHECK="${RUN_CAPACITY_CHECK:-1}"
ROLLOUT_TIMEOUT_SEC="${ROLLOUT_TIMEOUT_SEC:-900}"
REQUIRE_DOCKER_RUNTIME="${REQUIRE_DOCKER_RUNTIME:-1}"

log() {
  printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"
}

warn() {
  echo "WARN: $*" >&2
}

fail() {
  echo "ERROR: $*" >&2
  exit 2
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Required command not found: $1"
}

require_file() {
  [[ -f "$1" ]] || fail "Required file not found: $1"
}

password_is_unsafe() {
  local password="$1"
  case "${password}" in
    ""|"admin123!"|"roqkfrhk1!"|"CHANGE_ME"*) return 0 ;;
  esac
  (( ${#password} < 12 ))
}

prompt_password_if_needed() {
  local password="${PLAYGROUND_ADMIN_PASSWORD}"
  while password_is_unsafe "${password}"; do
    if [[ ! -t 0 ]]; then
      fail "Set PLAYGROUND_ADMIN_PASSWORD to a site-specific value with at least 12 characters."
    fi
    read -r -s -p "PLAYGROUND_ADMIN_PASSWORD: " password
    echo
    if password_is_unsafe "${password}"; then
      echo "Password is empty, known-default, or shorter than 12 characters. Try again." >&2
    fi
  done
  PLAYGROUND_ADMIN_PASSWORD="${password}"
  export PLAYGROUND_ADMIN_PASSWORD
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

deployment_exists() {
  kubectl -n "${NAMESPACE}" get "deploy/$1" >/dev/null 2>&1
}

scale_down_if_exists() {
  local deploy="$1"
  if deployment_exists "${deploy}"; then
    log "Scaling ${deploy} to 0 before replacement"
    kubectl -n "${NAMESPACE}" scale "deploy/${deploy}" --replicas=0
  else
    log "Deployment ${deploy} does not exist yet; it will be created"
  fi
}

delete_component_pods() {
  local component="$1"
  log "Deleting leftover pods for component=${component}"
  kubectl -n "${NAMESPACE}" delete pod \
    -l "app.kubernetes.io/name=a-cong-ocr,app.kubernetes.io/component=${component}" \
    --ignore-not-found --grace-period=0 --force >/dev/null 2>&1 || true
}

delete_smoke_pods() {
  [[ "${CLEANUP_SMOKE_PODS}" == "1" ]] || return 0
  local pods
  pods="$(kubectl -n "${NAMESPACE}" get pods --no-headers 2>/dev/null | awk '/vllm-gpu-smoke/ {print $1}' || true)"
  if [[ -n "${pods}" ]]; then
    log "Deleting old vllm-gpu-smoke completed/test pods"
    echo "${pods}" | xargs -r kubectl -n "${NAMESPACE}" delete pod --ignore-not-found
  fi
}

wait_non_vllm_pods_gone() {
  local deadline=$((SECONDS + 180))
  log "Waiting for old app/playground/OCR API pods to disappear"
  while (( SECONDS < deadline )); do
    local pods
    pods="$(kubectl -n "${NAMESPACE}" get pods \
      -l "app.kubernetes.io/name=a-cong-ocr" --no-headers 2>/dev/null \
      | awk '$1 ~ /^a-cong-ocr-(service|playground|app)-/ {print}' || true)"
    if [[ -z "${pods}" ]]; then
      echo "Old non-vLLM pods removed."
      return 0
    fi
    printf '.'
    sleep 5
  done
  echo
  kubectl -n "${NAMESPACE}" get pods -l "app.kubernetes.io/name=a-cong-ocr" -o wide || true
  fail "Old app/playground/OCR API pods did not disappear in time. Check Terminating pods before retrying."
}

wait_component_ready() {
  local deploy="$1"
  local component="$2"
  local deadline=$((SECONDS + ROLLOUT_TIMEOUT_SEC))

  log "Waiting for ${deploy} to become ready"
  while (( SECONDS < deadline )); do
    local ready
    ready="$(kubectl -n "${NAMESPACE}" get "deploy/${deploy}" -o jsonpath='{.status.readyReplicas}' 2>/dev/null || true)"
    if [[ "${ready:-0}" == "1" ]]; then
      kubectl -n "${NAMESPACE}" get pods \
        -l "app.kubernetes.io/name=a-cong-ocr,app.kubernetes.io/component=${component}" -o wide
      return 0
    fi

    local pod_state
    pod_state="$(kubectl -n "${NAMESPACE}" get pods \
      -l "app.kubernetes.io/name=a-cong-ocr,app.kubernetes.io/component=${component}" \
      -o jsonpath='{range .items[*]}{.metadata.name}{" phase="}{.status.phase}{" reason="}{range .status.containerStatuses[*]}{.state.waiting.reason}{" "}{end}{range .status.initContainerStatuses[*]}{.state.waiting.reason}{" "}{end}{" scheduled="}{range .status.conditions[?(@.type=="PodScheduled")]}{.status}{" "}{.reason}{" "}{.message}{end}{"\n"}{end}' 2>/dev/null || true)"
    if echo "${pod_state}" | grep -Eq 'ImagePullBackOff|ErrImagePull|InvalidImageName'; then
      echo "${pod_state}" >&2
      diagnose_component "${component}"
      fail "${deploy} cannot start because the image is not available locally or pull authentication failed."
    fi
    if echo "${pod_state}" | grep -q 'Too many pods'; then
      echo "${pod_state}" >&2
      diagnose_component "${component}"
      fail "${deploy} is Pending because the node pod limit is full."
    fi

    printf '.'
    sleep 10
  done
  echo
  diagnose_component "${component}"
  fail "Timed out waiting for ${deploy}"
}

diagnose_component() {
  local component="$1"
  echo
  echo "---- pods for component=${component} ----" >&2
  kubectl -n "${NAMESPACE}" get pods \
    -l "app.kubernetes.io/name=a-cong-ocr,app.kubernetes.io/component=${component}" -o wide >&2 || true
  echo "---- describe pods for component=${component} ----" >&2
  kubectl -n "${NAMESPACE}" describe pods \
    -l "app.kubernetes.io/name=a-cong-ocr,app.kubernetes.io/component=${component}" >&2 || true
  echo "---- recent namespace events ----" >&2
  kubectl -n "${NAMESPACE}" get events --sort-by=.lastTimestamp | tail -80 >&2 || true
}

scale_up_and_wait() {
  local deploy="$1"
  local component="$2"
  log "Scaling ${deploy} to 1"
  kubectl -n "${NAMESPACE}" scale "deploy/${deploy}" --replicas=1
  wait_component_ready "${deploy}" "${component}"
}

check_existing_vllm_health() {
  log "Checking existing vLLM health without restarting it"
  kubectl -n "${NAMESPACE}" get deploy/a-cong-vllm-ocr >/dev/null
  kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- \
    python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=10).read().decode())"
}

check_runtime_contract() {
  log "Checking cluster runtime and required PVCs"
  python3 - "${NAMESPACE}" "${REQUIRE_DOCKER_RUNTIME}" "${SKIP_HARBOR_PUSH}" <<'PY'
import json
import subprocess
import sys

namespace = sys.argv[1]
require_docker = sys.argv[2] == "1"
skip_harbor_push = sys.argv[3] == "1"


def kubectl_json(*args: str) -> dict:
    return json.loads(subprocess.check_output(["kubectl", *args], text=True))


nodes = kubectl_json("get", "nodes", "-o", "json").get("items") or []
if not nodes:
    raise SystemExit("ERROR: no Kubernetes nodes found")
runtime_versions = [
    ((node.get("metadata") or {}).get("name", ""), ((node.get("status") or {}).get("nodeInfo") or {}).get("containerRuntimeVersion", ""))
    for node in nodes
]
for name, runtime in runtime_versions:
    print(f"Node runtime: {name} {runtime}")
if require_docker and skip_harbor_push and not any(runtime.startswith("docker://") for _, runtime in runtime_versions):
    raise SystemExit(
        "ERROR: SKIP_HARBOR_PUSH=1 uses local docker-loaded images, but Kubernetes runtime does not report docker://. "
        "Use SKIP_HARBOR_PUSH=0 with a valid Harbor imagePullSecret, or import images into the node container runtime."
    )

required_pvcs = [
    "a-cong-ocr-models-pvc",
    "a-cong-ocr-model-cache-pvc",
    "a-cong-ocr-runtime-pvc",
]
pvc_items = kubectl_json("-n", namespace, "get", "pvc", "-o", "json").get("items") or []
pvcs = {(item.get("metadata") or {}).get("name"): item for item in pvc_items}
missing = [name for name in required_pvcs if name not in pvcs]
if missing:
    raise SystemExit(f"ERROR: required PVCs are missing: {', '.join(missing)}")
not_bound = [
    name
    for name in required_pvcs
    if ((pvcs[name].get("status") or {}).get("phase") != "Bound")
]
if not_bound:
    raise SystemExit(f"ERROR: required PVCs are not Bound: {', '.join(not_bound)}")
for name in required_pvcs:
    pvc = pvcs[name]
    capacity = ((pvc.get("status") or {}).get("capacity") or {}).get("storage", "?")
    volume = (pvc.get("spec") or {}).get("volumeName", "?")
    print(f"PVC OK: {name} phase=Bound volume={volume} capacity={capacity}")
PY
}

check_internal_health() {
  log "Checking internal service health"
  kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-service -- \
    python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=10).read().decode())"
  kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-playground -- \
    python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/playground/api/health', timeout=10).read().decode())"
  kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-app -- \
    python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/api/v1/health', timeout=10).read().decode())"
}

check_external_health() {
  [[ "${RUN_EXTERNAL_CHECKS}" == "1" ]] || {
    log "Skipping external Ingress checks because RUN_EXTERNAL_CHECKS=0"
    return 0
  }
  log "Checking external Ingress health"
  curl --noproxy '*' -kfsS "https://${HOST}:${PORT}/a-cong-ocr-api/health"
  echo
  curl --noproxy '*' -kfsS "https://${HOST}:${PORT}/a-cong-ocr-playground/api/health"
  echo
  curl --noproxy '*' -kfsS "https://${HOST}:${PORT}/a-cong-ocr/api/v1/health"
  echo
}

validate_rendered_manifest() {
  local rendered_manifest="$1"
  log "Validating rendered manifest with kubectl server dry-run"
  kubectl apply --dry-run=server -f "${rendered_manifest}" >/dev/null
}

simulate_cluster_capacity() {
  [[ "${RUN_CAPACITY_CHECK}" == "1" ]] || {
    log "Skipping cluster capacity simulation because RUN_CAPACITY_CHECK=0"
    return 0
  }
  local phase="${1:-planned}"
  log "Simulating Kubernetes capacity for ${phase} final state"
  GPU_NODE="${GPU_NODE:-}" python3 - "${NAMESPACE}" "${CLEANUP_SMOKE_PODS}" <<'PY'
import json
import os
import subprocess
import sys
from typing import Any

namespace = sys.argv[1]
cleanup_smoke = sys.argv[2] == "1"
target_components = {"ocr-service", "playground", "app"}


def kubectl_json(*args: str) -> dict[str, Any]:
    return json.loads(subprocess.check_output(["kubectl", *args], text=True))


def parse_cpu(value: str | None) -> int:
    if not value:
        return 0
    text = str(value).strip()
    if text.endswith("m"):
        return int(float(text[:-1]))
    return int(float(text) * 1000)


def parse_memory(value: str | None) -> int:
    if not value:
        return 0
    text = str(value).strip()
    units = {
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "Ti": 1024 ** 4,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3,
        "T": 1000 ** 4,
    }
    for suffix, factor in units.items():
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * factor)
    return int(float(text))


def fmt_cpu(millicpu: int) -> str:
    if millicpu % 1000 == 0:
        return f"{millicpu // 1000}"
    return f"{millicpu / 1000:.3f}".rstrip("0").rstrip(".")


def fmt_mem(bytes_value: int) -> str:
    return f"{bytes_value / (1024 ** 3):.2f}Gi"


def pod_resources(pod: dict[str, Any]) -> tuple[int, int, int, int]:
    spec = pod.get("spec") or {}

    def container_values(container: dict[str, Any]) -> tuple[int, int, int, int]:
        resources = container.get("resources") or {}
        requests = resources.get("requests") or {}
        limits = resources.get("limits") or {}
        return (
            parse_cpu(requests.get("cpu")),
            parse_memory(requests.get("memory")),
            parse_cpu(limits.get("cpu")),
            parse_memory(limits.get("memory")),
        )

    req_cpu = req_mem = lim_cpu = lim_mem = 0
    for container in spec.get("containers") or []:
        c_req_cpu, c_req_mem, c_lim_cpu, c_lim_mem = container_values(container)
        req_cpu += c_req_cpu
        req_mem += c_req_mem
        lim_cpu += c_lim_cpu
        lim_mem += c_lim_mem

    init_req_cpu = init_req_mem = init_lim_cpu = init_lim_mem = 0
    for container in spec.get("initContainers") or []:
        c_req_cpu, c_req_mem, c_lim_cpu, c_lim_mem = container_values(container)
        init_req_cpu = max(init_req_cpu, c_req_cpu)
        init_req_mem = max(init_req_mem, c_req_mem)
        init_lim_cpu = max(init_lim_cpu, c_lim_cpu)
        init_lim_mem = max(init_lim_mem, c_lim_mem)

    return (
        max(req_cpu, init_req_cpu),
        max(req_mem, init_req_mem),
        max(lim_cpu, init_lim_cpu),
        max(lim_mem, init_lim_mem),
    )


def is_active(pod: dict[str, Any]) -> bool:
    return (pod.get("status") or {}).get("phase") not in {"Succeeded", "Failed"}


def is_target_non_vllm(pod: dict[str, Any]) -> bool:
    labels = (pod.get("metadata") or {}).get("labels") or {}
    return labels.get("app.kubernetes.io/name") == "a-cong-ocr" and labels.get("app.kubernetes.io/component") in target_components


def is_smoke(pod: dict[str, Any]) -> bool:
    name = (pod.get("metadata") or {}).get("name", "")
    return "vllm-gpu-smoke" in name


def should_remove_for_projection(pod: dict[str, Any]) -> bool:
    if (pod.get("metadata") or {}).get("namespace") != namespace:
        return False
    if is_target_non_vllm(pod):
        return True
    return cleanup_smoke and is_smoke(pod)


nodes = kubectl_json("get", "nodes", "-o", "json").get("items") or []
pods = kubectl_json("get", "pods", "-A", "-o", "json").get("items") or []
if not nodes:
    raise SystemExit("ERROR: no Kubernetes nodes found")

preferred_node = os.environ.get("GPU_NODE") or ""
if not preferred_node:
    for pod in pods:
        labels = (pod.get("metadata") or {}).get("labels") or {}
        if labels.get("app.kubernetes.io/name") == "a-cong-ocr" and labels.get("app.kubernetes.io/component") == "vllm-ocr":
            preferred_node = (pod.get("spec") or {}).get("nodeName") or ""
            if preferred_node:
                break
node = next((item for item in nodes if (item.get("metadata") or {}).get("name") == preferred_node), None)
if node is None:
    node = nodes[0]
node_name = (node.get("metadata") or {}).get("name", "")
alloc = (node.get("status") or {}).get("allocatable") or {}
alloc_pods = int(alloc.get("pods") or 0)
alloc_cpu = parse_cpu(alloc.get("cpu"))
alloc_mem = parse_memory(alloc.get("memory"))

kept_pods = []
removed_pods = []
for pod in pods:
    if not is_active(pod):
        continue
    if should_remove_for_projection(pod):
        removed_pods.append(pod)
        continue
    kept_pods.append(pod)

kept_on_node = [pod for pod in kept_pods if (pod.get("spec") or {}).get("nodeName") == node_name]
current_on_node = [
    pod
    for pod in pods
    if is_active(pod) and (pod.get("spec") or {}).get("nodeName") == node_name
]

target_req_cpu = parse_cpu("2") + parse_cpu("500m") + parse_cpu("2")
target_req_mem = parse_memory("8Gi") + parse_memory("512Mi") + parse_memory("4Gi")
target_lim_cpu = parse_cpu("8") + parse_cpu("2") + parse_cpu("8")
target_lim_mem = parse_memory("32Gi") + parse_memory("4Gi") + parse_memory("16Gi")

kept_req_cpu = kept_req_mem = kept_lim_cpu = kept_lim_mem = 0
for pod in kept_on_node:
    req_cpu, req_mem, lim_cpu, lim_mem = pod_resources(pod)
    kept_req_cpu += req_cpu
    kept_req_mem += req_mem
    kept_lim_cpu += lim_cpu
    kept_lim_mem += lim_mem

projected_pods = len(kept_on_node) + 3
projected_req_cpu = kept_req_cpu + target_req_cpu
projected_req_mem = kept_req_mem + target_req_mem

print(f"Node: {node_name}")
print(f"Pod slots: current={len(current_on_node)} after-cleanup-plus-target={projected_pods} allocatable={alloc_pods}")
print(f"CPU requests: projected={fmt_cpu(projected_req_cpu)} allocatable={fmt_cpu(alloc_cpu)}")
print(f"Memory requests: projected={fmt_mem(projected_req_mem)} allocatable={fmt_mem(alloc_mem)}")
if removed_pods:
    print("Pods excluded from projection:")
    for pod in removed_pods:
        meta = pod.get("metadata") or {}
        print(f"  - {meta.get('namespace')}/{meta.get('name')}")

errors: list[str] = []
if alloc_pods and projected_pods > alloc_pods:
    errors.append(f"pod slots would exceed node allocatable pods: {projected_pods}>{alloc_pods}")
if alloc_cpu and projected_req_cpu > alloc_cpu:
    errors.append(f"CPU requests would exceed node allocatable CPU: {fmt_cpu(projected_req_cpu)}>{fmt_cpu(alloc_cpu)}")
if alloc_mem and projected_req_mem > alloc_mem:
    errors.append(f"memory requests would exceed node allocatable memory: {fmt_mem(projected_req_mem)}>{fmt_mem(alloc_mem)}")

try:
    quotas = kubectl_json("-n", namespace, "get", "resourcequota", "-o", "json").get("items") or []
except subprocess.CalledProcessError:
    quotas = []

if quotas:
    print("ResourceQuota simulation:")
for quota in quotas:
    name = (quota.get("metadata") or {}).get("name", "")
    status = quota.get("status") or {}
    hard = status.get("hard") or {}
    used = status.get("used") or {}
    checks = {
        "requests.cpu": (parse_cpu, target_req_cpu, fmt_cpu),
        "requests.memory": (parse_memory, target_req_mem, fmt_mem),
        "limits.cpu": (parse_cpu, target_lim_cpu, fmt_cpu),
        "limits.memory": (parse_memory, target_lim_mem, fmt_mem),
    }
    for key, (parser, target_value, formatter) in checks.items():
        if key not in hard:
            continue
        hard_value = parser(hard.get(key))
        used_value = parser(used.get(key))
        removed_value = 0
        for pod in removed_pods:
            if (pod.get("metadata") or {}).get("namespace") != namespace:
                continue
            req_cpu, req_mem, lim_cpu, lim_mem = pod_resources(pod)
            removed_value += {
                "requests.cpu": req_cpu,
                "requests.memory": req_mem,
                "limits.cpu": lim_cpu,
                "limits.memory": lim_mem,
            }[key]
        projected = max(0, used_value - removed_value) + target_value
        print(f"  {name} {key}: projected={formatter(projected)} hard={formatter(hard_value)}")
        if hard_value and projected > hard_value:
            errors.append(f"quota {name} {key} would be exceeded: {formatter(projected)}>{formatter(hard_value)}")

if errors:
    print("Capacity simulation failed:", file=sys.stderr)
    for error in errors:
        print(f"  - {error}", file=sys.stderr)
    raise SystemExit(3)
print("Capacity simulation passed.")
PY
}

require_cmd docker
require_cmd kubectl
require_cmd python3
require_cmd curl
require_file "${MANIFEST}"
require_file "${APP_UI_TAR}"
require_file "${PLAYGROUND_TAR}"
require_file "${OCR_API_TAR}"
prompt_password_if_needed

if [[ "${SKIP_PREFLIGHT}" != "1" ]]; then
  require_file "scripts/preflight_k8s_hami_public_ocr.sh"
  log "Running optional static/k8s preflight"
  NAMESPACE="${NAMESPACE}" \
  HOST="${HOST}" \
  INGRESS_CLASS="nginx" \
  STORAGE_CLASS="local-path" \
  IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET}" \
  GPU_NODE="${GPU_NODE:-nocode-ai-army01}" \
  EXPECTED_GPU_REQUEST="1" \
  EXPECTED_GPUMEM_PERCENTAGE="30" \
  EXPECTED_GPUCORES="30" \
  MANIFEST="${MANIFEST}" \
  scripts/preflight_k8s_hami_public_ocr.sh
else
  log "Skipping optional preflight by default"
fi

check_existing_vllm_health
check_runtime_contract

log "Loading app/playground/OCR API image tar files into the local node runtime"
docker load -i "${APP_UI_TAR}"
docker load -i "${PLAYGROUND_TAR}"
docker load -i "${OCR_API_TAR}"

log "Ensuring exact image names used by Kubernetes exist locally"
ensure_image_tag "${APP_UI_IMAGE}" "a-cong-ocr-app-ui:chandra" "a-cong-ocr-ui:chandra"
ensure_image_tag "${PLAYGROUND_IMAGE}" "a-cong-ocr-playground:chandra" "a-cong-ocr-ui:chandra"
ensure_image_tag "${OCR_API_IMAGE}" "a-cong-ocr-api:chandra" "a-cong-ocr:chandra"
docker image inspect "${APP_UI_IMAGE}" "${PLAYGROUND_IMAGE}" "${OCR_API_IMAGE}" >/dev/null
simulate_cluster_capacity "before cleanup"

if [[ "${SKIP_HARBOR_PUSH}" != "1" ]]; then
  log "Pushing updated non-vLLM images to Harbor ${REGISTRY}"
  docker push "${APP_UI_IMAGE}"
  docker push "${PLAYGROUND_IMAGE}"
  docker push "${OCR_API_IMAGE}"
else
  log "Skipping Harbor push by default; Kubernetes will use local images with IfNotPresent"
fi

log "Cleaning non-vLLM rollout leftovers before applying the manifest"
scale_down_if_exists "a-cong-ocr-service"
scale_down_if_exists "a-cong-ocr-playground"
scale_down_if_exists "a-cong-ocr-app"
delete_component_pods "ocr-service"
delete_component_pods "playground"
delete_component_pods "app"
delete_smoke_pods
wait_non_vllm_pods_gone
simulate_cluster_capacity "after cleanup"

log "Rendering manifest without touching the existing a-cong-vllm-ocr Deployment"
RENDERED_MANIFEST="$(mktemp /tmp/a-cong-ocr-existing-vllm.XXXXXX.yaml)"
python3 - "${MANIFEST}" "${RENDERED_MANIFEST}" \
  "${APP_UI_IMAGE}" "${PLAYGROUND_IMAGE}" "${OCR_API_IMAGE}" "${VLLM_IMAGE}" \
  "${HOST}" "${NAMESPACE}" "${IMAGE_PULL_SECRET}" "${PLAYGROUND_ADMIN_PASSWORD}" \
  "${TARGET_API_BASE_URL}" "${TARGET_API_TOKEN}" "${SKIP_PVC_APPLY}" <<'PY'
import json
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
app_ui_image = sys.argv[3]
playground_image = sys.argv[4]
ocr_api_image = sys.argv[5]
vllm_image = sys.argv[6]
host = sys.argv[7]
namespace = sys.argv[8]
image_pull_secret = sys.argv[9]
playground_admin_password = sys.argv[10]
target_api_base_url = sys.argv[11]
target_api_token = sys.argv[12]
skip_pvc_apply = sys.argv[13] == "1"


def force_deployment_zero_recreate(doc: str) -> str:
    lines = doc.splitlines()
    try:
        spec_index = next(i for i, line in enumerate(lines) if line == "spec:")
    except StopIteration:
        return doc

    head = lines[: spec_index + 1]
    tail = lines[spec_index + 1 :]
    cleaned = []
    i = 0
    while i < len(tail):
        line = tail[i]
        if line.startswith("  replicas:"):
            i += 1
            continue
        if line == "  strategy:":
            i += 1
            while i < len(tail) and (tail[i].startswith("    ") or not tail[i].strip()):
                i += 1
            continue
        cleaned.append(line)
        i += 1
    return "\n".join(head + ["  replicas: 0", "  strategy:", "    type: Recreate"] + cleaned)


text = src.read_text(encoding="utf-8")
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-app-ui:chandra",
    app_ui_image,
)
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-playground:chandra",
    playground_image,
)
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-api:chandra",
    ocr_api_image,
)
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra",
    vllm_image,
)
text = text.replace("host: nocodeaidev.army.mil", f"host: {host}")
text = text.replace("namespace: nocodeaidev", f"namespace: {namespace}")
text = text.replace("- name: harbor-reg-cred", f"- name: {image_pull_secret}")
text = text.replace(
    'PLAYGROUND_ADMIN_PASSWORD: "CHANGE_ME_STRONG_ADMIN_PASSWORD"',
    f"PLAYGROUND_ADMIN_PASSWORD: {json.dumps(playground_admin_password)}",
)
if target_api_base_url:
    text = text.replace('TARGET_API_BASE_URL: ""', f"TARGET_API_BASE_URL: {json.dumps(target_api_base_url)}")
if target_api_token:
    text = text.replace('TARGET_API_TOKEN: ""', f"TARGET_API_TOKEN: {json.dumps(target_api_token)}")
text = text.replace("imagePullPolicy: Always", "imagePullPolicy: IfNotPresent")

rendered_docs = []
for doc in re.split(r"\n---\s*\n", text):
    kind_match = re.search(r"^kind:\s*(\S+)\s*$", doc, flags=re.MULTILINE)
    name_match = re.search(r"^  name:\s*(\S+)\s*$", doc, flags=re.MULTILINE)
    kind = kind_match.group(1) if kind_match else ""
    name = name_match.group(1) if name_match else ""
    if kind == "Deployment" and name == "a-cong-vllm-ocr":
        continue
    if skip_pvc_apply and kind == "PersistentVolumeClaim":
        continue
    if kind == "Deployment" and name in {"a-cong-ocr-service", "a-cong-ocr-playground", "a-cong-ocr-app"}:
        doc = force_deployment_zero_recreate(doc)
    rendered_docs.append(doc)

dst.write_text("\n---\n".join(rendered_docs) + "\n", encoding="utf-8")
PY

validate_rendered_manifest "${RENDERED_MANIFEST}"
kubectl apply -f "${RENDERED_MANIFEST}"

log "Scaling non-vLLM deployments sequentially to avoid Too many pods during rollout"
scale_up_and_wait "a-cong-ocr-service" "ocr-service"
scale_up_and_wait "a-cong-ocr-playground" "playground"
scale_up_and_wait "a-cong-ocr-app" "app"

check_internal_health
check_existing_vllm_health
check_external_health

log "Done. Existing a-cong-vllm-ocr was checked but not restarted."
cat <<EOF
Expected running pods:
  kubectl -n ${NAMESPACE} get pods -l app.kubernetes.io/name=a-cong-ocr -o wide

App URL:
  https://${HOST}:${PORT}/a-cong-ocr/demo/jobs
OCR API:
  https://${HOST}:${PORT}/a-cong-ocr-api/api/v1/ocr/image
OCR Playground:
  https://${HOST}:${PORT}/a-cong-ocr-playground/
Logs:
  kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-service --tail=200
  kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-playground --tail=200
  kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-app --tail=200
EOF
