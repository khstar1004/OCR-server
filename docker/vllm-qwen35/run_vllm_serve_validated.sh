#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${1:-}"
EXPECT_MODEL_TYPE="${VLLM_EXPECT_MODEL_TYPE:-}"

if [[ -z "${MODEL_PATH}" ]]; then
  echo "Missing model path argument for vLLM serve." >&2
  exit 64
fi

VALIDATION_ARGS=(--model-dir "${MODEL_PATH}")
if [[ -n "${EXPECT_MODEL_TYPE}" ]]; then
  VALIDATION_ARGS+=(--expect-model-type "${EXPECT_MODEL_TYPE}")
fi

python3 /opt/a-cong/check_vllm_qwen35_runtime.py "${VALIDATION_ARGS[@]}"

for arg in "$@"; do
  if [[ "${arg}" == "--trust-remote-code" ]]; then
    exec vllm serve "$@"
  fi
done

exec vllm serve "$@" --trust-remote-code
