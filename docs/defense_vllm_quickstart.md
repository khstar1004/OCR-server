# Defense vLLM Quickstart

This quickstart is for an offline defense-network deployment using:

- `a-cong-ocr:chandra`
- `a-cong-vllm-openai:chandra`
- local Chandra model files in `news_models/chandra-ocr-2`

## Required bundle

- `dist/a-cong-ocr_chandra.tar`
- `dist/a-cong-vllm-openai_chandra.tar`
- project source tree
- `news_models/chandra-ocr-2`

## First-time setup

```bash
chmod +x ./scripts/load_offline_images.sh ./scripts/start_defense_remote_ocr.sh
./scripts/load_offline_images.sh
cp -f ./.env.example ./.env
sed -i 's/^CHANDRA_METHOD=.*/CHANDRA_METHOD=vllm/' ./.env
sed -i 's|^OCR_SERVICE_URL=.*|OCR_SERVICE_URL=|' ./.env
mkdir -p ./model_cache
```

`load_offline_images.ps1`는 custom `vllm` 이미지 안에서 Chandra 모델 디렉터리와 런타임 호환성을 바로 검증한다.
내부 확인값으로 `model_type=qwen3_5`를 보지만, 배포 모델은 Chandra OCR이다.
같은 단계에서 `--runtime=nvidia` 기반 GPU 런타임도 함께 점검한다.

Confirm the model directory exists:

```bash
test -d ./news_models/chandra-ocr-2
```

## Start in vLLM mode

```bash
docker compose --profile vllm up -d --wait
```

## Check health

```bash
docker compose --profile vllm ps
curl http://127.0.0.1:18007/health
```

## Stop

```bash
docker compose --profile vllm down
```

## When to use remote-ocr instead

Use `remote-ocr` only if you need a separate OCR API adapter container.

```bash
docker compose --profile remote-ocr up -d
```
