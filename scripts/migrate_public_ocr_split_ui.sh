#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-nocodeaidev}"
HOST="${HOST:-nocodeaidev.army.mil}"
PORT="${PORT:-20443}"
HARBOR_PROJECT="${HARBOR_PROJECT:-nocodeaidev}"
REGISTRY="${REGISTRY:-${HOST}:${PORT}}"
IMAGE_PULL_SECRET="${IMAGE_PULL_SECRET:-harbor-reg-cred}"
MANIFEST="${MANIFEST:-k8s/defense-remote-ocr.nocodeaidev.yaml}"

UI_IMAGE="${UI_IMAGE:-${APP_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr-ui:chandra}}"
UI_TAR="${UI_TAR:-${APP_TAR:-dist/a-cong-ocr-ui_chandra.tar}}"
UPDATE_OCR_API_IMAGE="${UPDATE_OCR_API_IMAGE:-0}"
OCR_API_IMAGE="${OCR_API_IMAGE:-${REGISTRY}/${HARBOR_PROJECT}/a-cong-ocr:chandra}"
OCR_API_TAR="${OCR_API_TAR:-dist/a-cong-ocr_chandra.tar}"
SKIP_HARBOR_PUSH="${SKIP_HARBOR_PUSH:-0}"
RESTART_APP="${RESTART_APP:-1}"
RESTART_PLAYGROUND="${RESTART_PLAYGROUND:-1}"
RESTART_OCR_SERVICE="${RESTART_OCR_SERVICE:-0}"

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
require_cmd python3
require_file "${MANIFEST}"
require_file "${UI_TAR}"
if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
  require_file "${OCR_API_TAR}"
  RESTART_OCR_SERVICE=1
fi

log "Checking existing successful OCR/vLLM services"
kubectl -n "${NAMESPACE}" get cm/a-cong-ocr-config >/dev/null
kubectl -n "${NAMESPACE}" get deploy/a-cong-ocr-service >/dev/null
kubectl -n "${NAMESPACE}" get deploy/a-cong-vllm-ocr >/dev/null
kubectl -n "${NAMESPACE}" get svc/a-cong-ocr-service >/dev/null
kubectl -n "${NAMESPACE}" get svc/a-cong-vllm-ocr >/dev/null

log "Loading UI image tar"
docker load -i "${UI_TAR}"
ensure_image_tag "${UI_IMAGE}" "a-cong-ocr-ui:chandra"

if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
  log "Loading OCR API image tar"
  docker load -i "${OCR_API_TAR}"
  ensure_image_tag "${OCR_API_IMAGE}" "a-cong-ocr:chandra"
fi

if [[ "${SKIP_HARBOR_PUSH}" != "1" ]]; then
  log "Pushing image(s) to Harbor ${REGISTRY}"
  docker push "${UI_IMAGE}"
  if [[ "${UPDATE_OCR_API_IMAGE}" == "1" ]]; then
    docker push "${OCR_API_IMAGE}"
  fi
else
  log "Skipping Harbor push because SKIP_HARBOR_PUSH=1"
fi

log "Patching ConfigMap keys needed by the split UI"
kubectl -n "${NAMESPACE}" patch cm/a-cong-ocr-config --type merge \
  -p '{"data":{"OCR_SERVICE_URL":"http://a-cong-ocr-service:5000","PLAYGROUND_UPSTREAM_BASE_URL":"http://a-cong-ocr-service:5000"}}'

log "Applying only app/playground services, deployments, and ingress rules"
RENDERED_MANIFEST="$(mktemp /tmp/a-cong-ocr-split-ui.XXXXXX.yaml)"
python3 - "${MANIFEST}" "${RENDERED_MANIFEST}" "${UI_IMAGE}" "${HOST}" "${NAMESPACE}" "${IMAGE_PULL_SECRET}" <<'PY'
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
ui_image = sys.argv[3]
host = sys.argv[4]
namespace = sys.argv[5]
image_pull_secret = sys.argv[6]

text = src.read_text(encoding="utf-8")
text = text.replace(
    "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-ui:chandra",
    ui_image,
)
text = text.replace("host: nocodeaidev.army.mil", f"host: {host}")
text = text.replace("namespace: nocodeaidev", f"namespace: {namespace}")
text = text.replace("- name: harbor-reg-cred", f"- name: {image_pull_secret}")

keep = {
    ("Deployment", "a-cong-ocr-app"),
    ("Service", "a-cong-ocr-app"),
    ("Deployment", "a-cong-ocr-playground"),
    ("Service", "a-cong-ocr-playground"),
    ("Ingress", "a-cong-ocr-app"),
    ("Ingress", "a-cong-ocr-api"),
    ("Ingress", "a-cong-ocr-playground"),
}

selected: list[str] = []
for doc in re.split(r"\n---\s*\n", text):
    kind_match = re.search(r"^kind:\s*(\S+)\s*$", doc, flags=re.MULTILINE)
    name_match = re.search(r"^metadata:\s*\n(?:[^\n]*\n)*?\s+name:\s*(\S+)\s*$", doc, flags=re.MULTILINE)
    if not kind_match or not name_match:
        continue
    key = (kind_match.group(1), name_match.group(1))
    if key in keep:
        selected.append(doc.strip())

if len(selected) != len(keep):
    found = {
        (
            re.search(r"^kind:\s*(\S+)\s*$", doc, flags=re.MULTILINE).group(1),
            re.search(r"^metadata:\s*\n(?:[^\n]*\n)*?\s+name:\s*(\S+)\s*$", doc, flags=re.MULTILINE).group(1),
        )
        for doc in selected
    }
    missing = sorted(keep - found)
    raise SystemExit(f"Missing expected manifest objects: {missing}")

dst.write_text("---\n" + "\n---\n".join(selected) + "\n", encoding="utf-8")
PY
kubectl apply -f "${RENDERED_MANIFEST}"

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

log "Waiting for changed rollouts"
if [[ "${RESTART_OCR_SERVICE}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-service --timeout=900s
fi
if [[ "${RESTART_PLAYGROUND}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-playground --timeout=900s
fi
if [[ "${RESTART_APP}" == "1" ]]; then
  kubectl -n "${NAMESPACE}" rollout status deploy/a-cong-ocr-app --timeout=900s
fi

log "Done. vLLM was not restarted by this script."
cat <<EOF
App:
  https://${HOST}:${PORT}/a-cong-ocr/demo/jobs
Playground:
  https://${HOST}:${PORT}/a-cong-ocr-playground/
Check:
  scripts/check_k8s_public_ocr.sh ${NAMESPACE} ${HOST} ${PORT}
EOF
