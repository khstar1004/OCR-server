#!/usr/bin/env bash
set -euo pipefail

SERVE_ARGS=("$@")
EXPECT_MODEL_TYPE="${VLLM_EXPECT_MODEL_TYPE:-}"

runtime_setting() {
  local key="$1"
  python3 - "${key}" <<'PY'
import json
import os
import sys

key = sys.argv[1]
path = (os.environ.get("RUNTIME_CONFIG_PATH") or "").strip()
if not path:
    raise SystemExit(0)
try:
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
except Exception:
    raise SystemExit(0)

values = payload.get("values")
if not isinstance(values, dict):
    raise SystemExit(0)
value = values.get(key)
if value is None or value == "":
    raise SystemExit(0)
if isinstance(value, (dict, list)):
    print(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
elif isinstance(value, bool):
    print("true" if value else "false")
else:
    print(value)
PY
}

replace_arg_value() {
  local option="$1"
  local value="$2"
  local idx

  if [[ -z "${value}" ]]; then
    return
  fi

  for idx in "${!SERVE_ARGS[@]}"; do
    if [[ "${SERVE_ARGS[${idx}]}" == "${option}" ]]; then
      if (( idx + 1 < ${#SERVE_ARGS[@]} )); then
        SERVE_ARGS[$((idx + 1))]="${value}"
      else
        SERVE_ARGS+=("${value}")
      fi
      return
    fi
  done
  SERVE_ARGS+=("${option}" "${value}")
}

RUNTIME_MODEL_PATH="$(runtime_setting vllm_model_path || true)"
if [[ -n "${RUNTIME_MODEL_PATH}" ]]; then
  if (( ${#SERVE_ARGS[@]} == 0 )); then
    SERVE_ARGS=("${RUNTIME_MODEL_PATH}")
  else
    SERVE_ARGS[0]="${RUNTIME_MODEL_PATH}"
  fi
fi

replace_arg_value "--served-model-name" "$(runtime_setting vllm_model_name || true)"
replace_arg_value "--max-model-len" "$(runtime_setting vllm_max_model_len || true)"
replace_arg_value "--gpu-memory-utilization" "$(runtime_setting vllm_gpu_memory_utilization || true)"
replace_arg_value "--max-num-seqs" "$(runtime_setting vllm_max_num_seqs || true)"
replace_arg_value "--mm-processor-kwargs" "$(runtime_setting vllm_mm_processor_kwargs || true)"

MODEL_PATH="${SERVE_ARGS[0]:-}"
if [[ -z "${MODEL_PATH}" ]]; then
  echo "Missing model path argument for vLLM serve." >&2
  exit 64
fi

VALIDATION_ARGS=(--model-dir "${MODEL_PATH}")
if [[ -n "${EXPECT_MODEL_TYPE}" ]]; then
  VALIDATION_ARGS+=(--expect-model-type "${EXPECT_MODEL_TYPE}")
fi

python3 /opt/a-cong/check_vllm_qwen35_runtime.py "${VALIDATION_ARGS[@]}"

for arg in "${SERVE_ARGS[@]}"; do
  if [[ "${arg}" == "--trust-remote-code" ]]; then
    exec vllm serve "${SERVE_ARGS[@]}"
  fi
done

exec vllm serve "${SERVE_ARGS[@]}" --trust-remote-code
