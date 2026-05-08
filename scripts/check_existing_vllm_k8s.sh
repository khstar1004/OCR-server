#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-${NAMESPACE:-nocodeaidev}}"

echo "[1/4] Existing vLLM deployment"
kubectl -n "${NAMESPACE}" get deploy/a-cong-vllm-ocr
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/name=a-cong-ocr,app.kubernetes.io/component=vllm-ocr -o wide

echo "[2/4] vLLM launch contract"
vllm_expect_model_type="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_EXPECT_MODEL_TYPE 2>/dev/null || true)"
vllm_model_config_type="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- python3 -c "import json; print(json.load(open('/models/chandra-ocr-2/config.json', encoding='utf-8')).get('model_type', ''))" 2>/dev/null || true)"
vllm_max_model_len="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_MAX_MODEL_LEN)"
vllm_gpu_memory_utilization="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- printenv VLLM_GPU_MEMORY_UTILIZATION)"
vllm_api_base="$(kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-service -- printenv VLLM_API_BASE 2>/dev/null || true)"
echo "VLLM_EXPECT_MODEL_TYPE=${vllm_expect_model_type}"
echo "VLLM_MODEL_CONFIG_TYPE=${vllm_model_config_type}"
echo "VLLM_MAX_MODEL_LEN=${vllm_max_model_len}"
echo "VLLM_GPU_MEMORY_UTILIZATION=${vllm_gpu_memory_utilization}"
if [[ -n "${vllm_api_base}" ]]; then
  echo "OCR_SERVICE.VLLM_API_BASE=${vllm_api_base}"
fi
if [[ -n "${vllm_expect_model_type}" ]]; then
  [[ "${vllm_expect_model_type}" == "qwen3_5" ]] || { echo "ERROR: VLLM_EXPECT_MODEL_TYPE must be qwen3_5." >&2; exit 1; }
else
  [[ "${vllm_model_config_type}" == "qwen3_5" ]] || { echo "ERROR: VLLM model config model_type must be qwen3_5 when VLLM_EXPECT_MODEL_TYPE is unset." >&2; exit 1; }
  echo "WARN: VLLM_EXPECT_MODEL_TYPE is unset on the existing vLLM Pod; accepted because /models/chandra-ocr-2/config.json is qwen3_5."
fi
[[ "${vllm_max_model_len}" == "16384" ]] || { echo "ERROR: VLLM_MAX_MODEL_LEN must be 16384." >&2; exit 1; }
[[ "${vllm_gpu_memory_utilization}" == "0.80" ]] || { echo "ERROR: VLLM_GPU_MEMORY_UTILIZATION must be 0.80." >&2; exit 1; }

echo "[3/4] vLLM internal health"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=5).read().decode())"

echo "[4/4] vLLM service object"
kubectl -n "${NAMESPACE}" get svc/a-cong-vllm-ocr
echo "Existing vLLM check passed."
