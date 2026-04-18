#!/usr/bin/env bash
set -euo pipefail

MODEL_PATH="${1:-}"

if [[ -z "${MODEL_PATH}" ]]; then
  echo "Missing model path argument for vLLM serve." >&2
  exit 64
fi

python3 /opt/a-cong/check_vllm_qwen35_runtime.py \
  --expect-model-type qwen3_5 \
  --model-dir "${MODEL_PATH}"

exec vllm serve "$@"
