#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-nocodeaidev}"
HOST="${2:-nocodeaidev.army.mil}"
PORT="${3:-20443}"
APP_PREFIX="${4:-/a-cong-ocr}"
OCR_PREFIX="${5:-/a-cong-ocr-api}"

echo "[1/6] Pods"
kubectl -n "${NAMESPACE}" get pods -l app.kubernetes.io/name=a-cong-ocr -o wide

echo "[2/6] Services"
kubectl -n "${NAMESPACE}" get svc a-cong-ocr-app a-cong-ocr-service a-cong-vllm-ocr

echo "[3/6] Ingress"
kubectl -n "${NAMESPACE}" get ingress a-cong-ocr-app a-cong-ocr-api

echo "[4/6] Internal health"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-app -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=5).read().decode())"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-ocr-service -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())"
kubectl -n "${NAMESPACE}" exec deploy/a-cong-vllm-ocr -- \
  python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).read().decode())"

echo "[5/6] External health through Ingress"
curl -kfsS "https://${HOST}:${PORT}${APP_PREFIX}/api/v1/health"
echo
curl -kfsS "https://${HOST}:${PORT}${OCR_PREFIX}/health"
echo
curl -kfsS "https://${HOST}:${PORT}${OCR_PREFIX}/api/v1/health"
echo

echo "[6/6] Recent logs if needed"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-vllm-ocr --tail=200"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-service --tail=200"
echo "kubectl -n ${NAMESPACE} logs deploy/a-cong-ocr-app --tail=200"
