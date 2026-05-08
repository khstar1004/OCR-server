#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-nocodeaidev}"
HOST="${2:-nocodeaidev.army.mil}"
PORT="${3:-20443}"
APP_PREFIX="${4:-/a-cong-ocr}"
OCR_PREFIX="${5:-/a-cong-ocr-api}"
PLAYGROUND_PREFIX="${6:-/a-cong-ocr-playground}"

echo "[1/8] Pods"
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/name=a-cong-ocr -o wide

echo "[2/8] Services"
kubectl -n "${NAMESPACE}" get svc a-cong-ocr-app a-cong-ocr-service a-cong-ocr-playground a-cong-vllm-ocr

echo "[3/8] Ingress"
kubectl -n "${NAMESPACE}" get ingress a-cong-ocr-app a-cong-ocr-api a-cong-ocr-playground

echo "[4/8] vLLM launch contract"
vllm_max_model_len="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_MAX_MODEL_LEN)"
vllm_gpu_memory_utilization="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_GPU_MEMORY_UTILIZATION)"
vllm_mm_processor_kwargs="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_MM_PROCESSOR_KWARGS)"
vllm_expect_model_type="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_EXPECT_MODEL_TYPE 2>/dev/null || true)"
vllm_model_config_type="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- python3 -c "import json; print(json.load(open('/models/chandra-ocr-2/config.json', encoding='utf-8')).get('model_type', ''))" 2>/dev/null || true)"
echo "VLLM_EXPECT_MODEL_TYPE=${vllm_expect_model_type}"
echo "VLLM_MODEL_CONFIG_TYPE=${vllm_model_config_type}"
echo "VLLM_MAX_MODEL_LEN=${vllm_max_model_len}"
echo "VLLM_GPU_MEMORY_UTILIZATION=${vllm_gpu_memory_utilization}"
echo "VLLM_MM_PROCESSOR_KWARGS=${vllm_mm_processor_kwargs}"
if [[ -n "${vllm_expect_model_type}" ]]; then
  [[ "${vllm_expect_model_type}" == "qwen3_5" ]] || { echo "ERROR: VLLM_EXPECT_MODEL_TYPE must be qwen3_5 for the bundled Chandra model snapshot." >&2; exit 1; }
else
  [[ "${vllm_model_config_type}" == "qwen3_5" ]] || { echo "ERROR: VLLM model config model_type must be qwen3_5 when VLLM_EXPECT_MODEL_TYPE is unset." >&2; exit 1; }
  echo "WARN: VLLM_EXPECT_MODEL_TYPE is unset on the existing vLLM Pod; accepted because /models/chandra-ocr-2/config.json is qwen3_5."
fi
[[ "${vllm_max_model_len}" == "16384" ]] || { echo "ERROR: VLLM_MAX_MODEL_LEN must be 16384 for current OCR requests." >&2; exit 1; }
[[ "${vllm_gpu_memory_utilization}" == "0.80" ]] || { echo "ERROR: VLLM_GPU_MEMORY_UTILIZATION must be 0.80 for the field-tested HAMi profile." >&2; exit 1; }

echo "[5/8] Internal health"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-app -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/api/v1/health', timeout=5).read().decode())"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-service -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).read().decode())"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-playground -- \
  python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).read().decode())"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).read().decode())"

echo "[6/8] External health through Ingress"
curl --noproxy '*' -kfsS "https://${HOST}:${PORT}${APP_PREFIX}/api/v1/health"
echo
curl --noproxy '*' -kfsS "https://${HOST}:${PORT}${OCR_PREFIX}/health"
echo
curl --noproxy '*' -kfsS "https://${HOST}:${PORT}${OCR_PREFIX}/api/v1/health"
echo
curl --noproxy '*' -kfsS "https://${HOST}:${PORT}${PLAYGROUND_PREFIX}/api/health"
echo

echo "[7/8] Playground HTML through Ingress"
playground_html="$(curl --noproxy '*' -kfsS "https://${HOST}:${PORT}${PLAYGROUND_PREFIX}/")"
printf '%s\n' "${playground_html:0:120}"
echo

echo "[8/8] Recent logs if needed"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-vllm-ocr --tail=200"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-service --tail=200"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-playground --tail=200"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-app --tail=200"
