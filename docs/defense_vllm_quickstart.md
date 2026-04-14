# Defense vLLM Quickstart

This quickstart is for an offline defense-network deployment using:

- `a-cong-ocr:chandra`
- `vllm/vllm-openai:v0.17.0`
- local Chandra model files in `news_models/chandra-ocr-2`

## Required bundle

- `dist/a-cong-ocr_chandra.tar`
- `dist/vllm-vllm-openai_v0.17.0.tar`
- project source tree
- `news_models/chandra-ocr-2`

## First-time setup

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\load_offline_images.ps1
Copy-Item .\.env.example .\.env
((Get-Content .\.env) `
  -replace '^CHANDRA_METHOD=.*$', 'CHANDRA_METHOD=vllm' `
  -replace '^OCR_SERVICE_URL=.*$', 'OCR_SERVICE_URL=') | Set-Content .\.env
New-Item -ItemType Directory -Force .\model_cache | Out-Null
```

Confirm the model directory exists:

```powershell
Test-Path .\news_models\chandra-ocr-2
```

## Start in vLLM mode

```powershell
docker compose --profile vllm up -d
```

## Check health

```powershell
docker compose --profile vllm ps
curl.exe http://127.0.0.1:18007/health
```

## Stop

```powershell
docker compose --profile vllm down
```

## When to use remote-ocr instead

Use `remote-ocr` only if you need a separate OCR API adapter container.

```powershell
docker compose --profile remote-ocr up -d
```
