# OCR 빠른 체감 가이드

이 저장소는 이제 `Chandra OCR`만 사용합니다. 가장 빨리 결과를 체감하는 방법은 PDF 또는 이미지 한 개를 넣고, 데모 UI에서 페이지 박스와 기사 결과를 같이 보는 방식입니다.

## 1. 준비

기본 입력 폴더는 [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs) 입니다. PDF가 암호화되어 서버 렌더링이 막히면 페이지 이미지를 PNG/JPG/WEBP로 넣어도 됩니다.

```powershell
Copy-Item .env.example .env
docker compose --profile remote-ocr up --build
```

프로필별 별도 env 파일은 쓰지 않습니다.
항상 루트 `.env` 하나만 수정합니다.

기본 확인값:

- `OCR_BACKEND=chandra`
- `CHANDRA_METHOD=hf`
- `CHANDRA_MODEL_ID=datalab-to/chandra-ocr-2`
- `API_HOST_PORT=18007`
- `OCR_SERVICE_TIMEOUT_SEC=300.0`

인터넷망/현장 검증용 compose 실행은 `remote-ocr` 프로필을 기본 경로로 봅니다. 이 프로필은 `app`, `playground`, `ocr-service`, `vllm-ocr` 4개 컨테이너를 올립니다.

외부 OCR 모델 서버를 붙일 때:

- `.env`에 `OCR_SERVICE_URL=http://<OCR_IP>:8000` 또는 `http://<OCR_IP>:8000/api/v1`
- 같은 compose 안의 `remote-ocr` 프로필을 쓰면 `OCR_SERVICE_URL=http://ocr-service:8000`
- 필요하면 `OCR_SERVICE_MODE`, `OCR_SERVICE_API_KEY`, `OCR_SERVICE_MARKER_MODE` 도 함께 설정

데모사이트와 유사한 상위 변환 파이프라인을 붙일 때:

- `OCR_SERVICE_MODE=datalab_marker`
- `OCR_SERVICE_URL=https://<your-datalab-host>` 또는 `https://<your-datalab-host>/api/v1/marker`
- `OCR_SERVICE_API_KEY=<key>`
- `OCR_SERVICE_MARKER_MODE=accurate`

## 2. 가장 빠른 체감 방법

### 방법 A. PDF/이미지 한 개만 바로 실행

```powershell
Invoke-WebRequest `
  -Method Post `
  -InFile .\sample.pdf `
  -ContentType "application/pdf" `
  -Uri "http://127.0.0.1:18007/api/v1/jobs/run-single?file_name=sample.pdf&force_reprocess=true"
```

응답에서 받은 `job_id`로 상태를 확인합니다.

```powershell
Invoke-WebRequest "http://127.0.0.1:18007/api/v1/jobs/<job_id>/detail"
Invoke-WebRequest "http://127.0.0.1:18007/api/v1/jobs/<job_id>/result"
```

이미지로 넣을 때:

```powershell
Invoke-WebRequest `
  -Method Post `
  -InFile .\page-001.png `
  -ContentType "image/png" `
  -Uri "http://127.0.0.1:18007/api/v1/jobs/run-single?file_name=page-001.png&force_reprocess=true"
```

### 방법 B. 데모 UI에서 결과 보기

브라우저:

```text
http://127.0.0.1:18007/demo/jobs
```

### OCR 전용 서비스 사용

아래 명령은 OCR API와 체험 UI를 같이 올립니다.

```powershell
docker compose --profile remote-ocr up --build
```

OCR 전용 컨테이너는 `http://127.0.0.1:18009/api/v1/ocr/*`로 호출합니다.
Chandra OCR 체험 UI는 `http://127.0.0.1:18109/playground/`에서 확인합니다.

구성 요약:

- `app`: 메인 서비스
- `playground`: OCR API 결과를 브라우저에서 확인하는 체험 UI/proxy
- `ocr-service`: 현재 OCR API 계약을 유지하는 어댑터
- `vllm-ocr`: `a-cong-vllm-openai:chandra` 이미지로 Chandra 모델 전체를 호스팅
- `OCR_MAX_CONCURRENT_REQUESTS=1`: 단일 vLLM/GPU 기준으로 OCR 추론 호출을 직렬 큐잉합니다. 2-5명이 동시에 써도 요청별 결과는 분리되고, 초과 요청은 대기합니다.
- 내부 런타임 검증은 Chandra 설정의 `model_type=qwen3_5`를 확인하지만, 별도 Qwen 전용 OCR을 띄우는 구조는 아닙니다.
- `MODELS_DIR`는 `/models`로, `MODEL_CACHE_DIR`는 `/root/.cache/huggingface`로 마운트됩니다.

인터넷망 준비 PC에서 이미 `18009` OCR API가 떠 있고 새 UI만 현재 코드로 체험하려면:

```powershell
.\scripts\start_playground_preview.ps1 -Port 18110 -BindHost 0.0.0.0 -UpstreamOcrUrl http://127.0.0.1:18009
```

브라우저:

```text
http://127.0.0.1:18110/playground/
```

현재 인터넷망 미리보기는 아래 주소에서 확인합니다.

```text
http://14.50.225.33:18110/playground/
```

PowerShell 예시:

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/ocr/image `
  -F "file=@image.png" `
  -F "page_number=1"

curl.exe -X POST http://127.0.0.1:18009/api/v1/ocr/pdf `
  -F "file=@sample.pdf" `
  -F "dpi=300"
```

Datalab 호환 제출/조회 패턴 예시:

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/ocr `
  -F "file=@image.png"

curl.exe http://127.0.0.1:18009/api/v1/ocr/<request_id>

curl.exe -X POST http://127.0.0.1:18009/api/v1/marker `
  -F "file=@sample.pdf" `
  -F "output_format=json"

curl.exe http://127.0.0.1:18009/api/v1/marker/<request_id>
```

파일/문서/배치/추출까지 포함한 국방망 운영 가이드는 [docs/defense_network_ocr_guide.md](docs/defense_network_ocr_guide.md) 를 참고하면 됩니다.

여기서 바로 확인할 수 있는 것:

- 최근 작업 목록
- 페이지별 박스 오버레이
- 기사 제목/본문
- 기사 crop 이미지
- raw OCR payload

### 방법 C. PyQt GUI로 보기

```powershell
pip install -r requirements-gui.txt
python -m app.gui.dashboard
```

## 3. 결과는 어디서 보나

결과는 `OUTPUT_ROOT_HOST` 아래에 저장됩니다.

대표적으로 확인할 파일:

- `article.md`
- `article.json`
- `images/`

OCR 품질을 체감할 때는 아래 네 가지를 같이 보면 됩니다.

- 제목 박스가 정확한지
- 본문이 기사 단위로 자연스럽게 묶이는지
- 기사 이미지가 제대로 crop 되는지
- `article.md`가 후속 시스템에 바로 넘길 수 있는 형태인지

## 4. 추천 사용 순서

1. `docker compose --profile remote-ocr up --build`
2. PDF 또는 이미지 한 개를 `run-single`로 실행
3. `/demo/jobs`에서 결과 확인
4. 결과 폴더의 `article.md`, `article.json` 확인

## 5. 자주 막히는 경우

### `CHANDRA_MODEL_DIR`를 못 찾는 경우

모델을 로컬에 미리 내려받았으면 `.env`에 실제 경로를 넣어야 합니다.

### `404`가 나는 경우

예전 이미지가 떠 있을 수 있습니다.

```powershell
docker compose --profile remote-ocr up --build
```

### 같은 파일이 스킵되는 경우

중복 해시 스킵이 있으므로 다시 돌릴 때는 `force_reprocess=true`를 같이 주는 편이 안전합니다.
