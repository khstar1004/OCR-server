# A-Cong OCR

`Chandra OCR` 단일 백엔드로 신문 PDF를 페이지 렌더링하고, 기사 단위로 묶어 결과를 저장/조회하는 서버입니다.

빠르게 체감해보려면 [docs/ocr_quick_guide.md](docs/ocr_quick_guide.md)를 먼저 보면 됩니다.

## 핵심 원칙

- OCR 백엔드는 `chandra`만 지원합니다.
- 기본 실행 경로는 `Dockerfile` + `docker-compose.yml` 하나입니다.
- 기본 compose 실행은 앱 내부의 로컬 Chandra OCR만 사용합니다.
- `.env`에 `OCR_SERVICE_URL=http://<OCR_IP>:8000` 을 넣으면 app가 해당 OCR 호스트/IP를 호출합니다.
- `ocr-service`는 별도 OCR API가 필요할 때만 프로필로 올리는 선택 기능입니다.
- `remote-ocr` 프로필을 쓰면 `app -> ocr-service -> vllm-ocr` 구조로 뜹니다.
- `vllm` 프로필을 쓰면 `app -> vllm-ocr` 2컨테이너 구조도 사용할 수 있습니다.

## 주요 경로

- 데모 UI: `GET /demo/jobs`
- 기사 상세: `GET /demo/articles/{article_id}`
- 기사 재처리: `POST /api/articles/{article_id}/reprocess`
- 기사 재전송: `POST /api/articles/{article_id}/redeliver`
- 작업 실행: `POST /api/v1/jobs/run-daily`
- 단일 PDF 실행: `POST /api/v1/jobs/run-single?file_name=...`
- OCR 전용 API: `POST /api/v1/ocr/image`, `POST /api/v1/ocr/pdf`
- Datalab 호환 OCR API: `POST /api/v1/ocr`, `GET /api/v1/ocr/{request_id}`
- Datalab 호환 Marker API: `POST /api/v1/marker`, `GET /api/v1/marker/{request_id}`
- Workflow API: `GET/POST /api/v1/workflows/workflows`, `POST /api/v1/workflows/workflows/{workflow_id}/execute`
- 작업 상세: `GET /api/v1/jobs/{job_id}/detail`
- 페이지 미리보기: `GET /api/v1/jobs/{job_id}/pages/{page_id}/preview`

호환 API 지원 범위는 [docs/datalab_compat_api.md](docs/datalab_compat_api.md) 에 정리했습니다.
외부 연동 기준 통합 명세는 [docs/external_api_spec.md](docs/external_api_spec.md) 에 정리했습니다.
국방망 운영 가이드는 [docs/defense_network_ocr_guide.md](docs/defense_network_ocr_guide.md) 에 정리했습니다.
오프라인 반입/광매체 용량 가이드는 [docs/offline_transfer_guide.md](docs/offline_transfer_guide.md) 에 정리했습니다.

## 로컬 실행

```powershell
Copy-Item .env.example .env
docker compose up --build
```

기본 접속 주소:

- API: `http://127.0.0.1:18007`
- 데모 UI: `http://127.0.0.1:18007/demo/jobs`

## PDF 넣는 폴더

기본 PDF 입력 폴더는 [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs) 입니다.

같이 만들어 둔 기본 폴더:

- 입력 PDF: [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs)
- OCR 결과: [news_output](C:\Users\USER\Desktop\a-cong-OCR-V2\news_output)
- 런타임 데이터/DB: [news_data](C:\Users\USER\Desktop\a-cong-OCR-V2\news_data)
- 모델 폴더: [news_models](C:\Users\USER\Desktop\a-cong-OCR-V2\news_models)
- 모델 캐시: [model_cache](C:\Users\USER\Desktop\a-cong-OCR-V2\model_cache)

기본 환경값:

- `WATCH_DIR=./news_pdfs`
- `DATA_DIR=./news_data`
- `MODELS_DIR=./news_models`
- `MODEL_CACHE_DIR=./model_cache`

PDF를 [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs)에 넣은 뒤 작업을 실행하면 됩니다.

## 환경 변수

핵심값:

- `OCR_BACKEND=chandra`
- `OCR_SERVICE_URL=` (비우면 로컬 OCR 실행)
- `OCR_SERVICE_MODE=native`
- `OCR_SERVICE_TIMEOUT_SEC=300.0`
- `CHANDRA_METHOD=hf`
- `CHANDRA_MODEL_ID=datalab-to/chandra-ocr-2`
- `CHANDRA_MODEL_DIR=/models/chandra-ocr-2` 또는 로컬 경로
- `CHANDRA_PROMPT_TYPE=ocr_layout`
- `CHANDRA_BATCH_SIZE=1`

전송 연동값:

- 데모 UI의 `Delivery URL (/news)` 입력값을 쓰면 작업 완료 후 기사들을 `multipart/form-data`로 해당 URL에 전송합니다.
- 입력값이 없으면 `.env`의 `TARGET_API_BASE_URL=http://<HOST>` 를 기준으로 `/news`를 붙여 전송할 수 있습니다.
- 인증이 필요하면 `TARGET_API_TOKEN`, 타임아웃은 `TARGET_API_TIMEOUT_SEC` 를 사용합니다.

### OCR 서비스 분리 시

- 기본 `docker compose up -d` 는 app만 올리고, app는 항상 로컬 OCR만 사용합니다.
- app가 vLLM 서버를 직접 호출하게 하려면 `.env`에 `OCR_SERVICE_URL=` 로 비우고 `CHANDRA_METHOD=vllm` 로 둔 뒤 `docker compose --profile vllm up -d --build` 를 사용하면 됩니다.
- 이 경우 실제 런타임은 `app + vllm-ocr` 2컨테이너입니다.
- app가 외부 OCR 모델 서버를 호출하게 하려면 `.env`에 `OCR_SERVICE_URL=http://<OCR_IP>:8000` 또는 `http://<OCR_IP>:8000/api/v1` 를 넣으면 됩니다.
- 같은 compose 안에서 `remote-ocr` 프로필을 같이 띄울 때는 `OCR_SERVICE_URL=http://ocr-service:8000` 으로 두는 편이 가장 안전합니다.
- app는 base URL을 받아 내부적으로 `/api/v1/ocr/image` 경로를 붙여 호출합니다.
- 별도 OCR API가 필요할 때만 `docker compose --profile remote-ocr up -d` 로 `ocr-service`와 `vllm-ocr`를 같이 올립니다.
- 기본 URL: `http://127.0.0.1:18007` (app), `http://127.0.0.1:18009` (`remote-ocr` 프로필의 OCR service)
- `OCR_SERVICE_MODE`, `OCR_SERVICE_API_KEY`, `OCR_SERVICE_MARKER_MODE` 도 `.env`에서 app 컨테이너로 같이 전달됩니다.
- `ocr-service`는 현재 OCR API 계약을 유지하는 어댑터이고, 실제 모델 호스팅은 공식 `vllm/vllm-openai:v0.17.0` 이미지 기반의 `vllm-ocr`가 담당합니다.
- `vllm-ocr`는 `${MODELS_DIR}`를 `/models`로, `${MODEL_CACHE_DIR}`를 `/root/.cache/huggingface`로 마운트합니다.
- `vllm-ocr`는 Chandra upstream `chandra_vllm` 스크립트와 맞춘 기본값으로 `VLLM_MAX_MODEL_LEN=18000`, `VLLM_GPU_MEMORY_UTILIZATION=0.85`, `VLLM_MM_PROCESSOR_KWARGS={"min_pixels":3136,"max_pixels":6291456}`를 사용합니다.
- 기본 `remote-ocr` 구성은 `VLLM_MODEL_PATH=/models/chandra-ocr-2`, `VLLM_MODEL_NAME=chandra-ocr-2`를 사용합니다.

### Datalab/온프렘 Marker 모드

- 데모사이트와 같은 상위 변환 파이프라인을 쓰려면 `OCR_SERVICE_MODE=datalab_marker` 로 설정합니다.
- `OCR_SERVICE_URL` 에는 Datalab base URL 또는 `/api/v1/marker` URL을 넣으면 됩니다.
- 필요하면 `OCR_SERVICE_API_KEY` 를 같이 넣습니다.
- 기본 요청 모드는 `OCR_SERVICE_MARKER_MODE=accurate` 입니다.
- Marker 모드는 `json` 출력의 block bbox/type을 그대로 받아 내부 기사 군집화에 사용합니다.

## Docker

온라인 빌드:

```powershell
docker compose build
docker compose up -d
```

오프라인 이미지 생성:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\build_chandra_offline_image.ps1
```

오프라인 반입 용량 추정:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\estimate_offline_bundle.ps1 `
  -ImageName a-cong-ocr:chandra `
  -ArchiveKind 7z
```

## 테스트

```powershell
pip install -r requirements.txt
python -m pytest tests
```
