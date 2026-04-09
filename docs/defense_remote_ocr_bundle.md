# Defense Remote OCR Bundle

국방망 반입용 기준은 `app + ocr-service + vllm-ocr` 3컨테이너 구조다.

## 반입 필수 항목

- `dist/a-cong-ocr_chandra.tar`
- `dist/vllm-vllm-openai_v0.17.0.tar`
- `news_models/chandra-ocr-2/`
- `docker-compose.defense-remote-ocr.yml`
- `.env.defense-remote-ocr.example`
- `scripts/load_offline_images.ps1`
- `scripts/start_defense_remote_ocr.ps1`

## 반입 권장 항목

- `docs/defense_remote_ocr_bundle.md`
- `docs/open_source_intake_list.csv`
- `docs/open_source_intake_list.md`

## 국방망 초기 실행

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\load_offline_images.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start_defense_remote_ocr.ps1
```

## 서비스 역할

- `app`
  뉴스 PDF OCR 작업 실행, 결과 조회, callback 전송
- `ocr-service`
  범용 OCR API 엔드포인트 제공
- `vllm-ocr`
  Chandra OCR 모델 서빙

## 기본 포트

- `app`: `18007`
- `ocr-service`: `18009`
- `vllm-ocr`: 내부 `8000`

## 주 사용 API

- 뉴스 처리 API
  `POST /api/v1/jobs/run-daily`
  `POST /api/v1/jobs/run-single`
  `GET /api/v1/jobs/{job_id}`
  `GET /api/v1/jobs/{job_id}/detail`
- OCR API
  `POST /api/v1/ocr/image`
  `POST /api/v1/ocr/pdf`
  `POST /api/v1/ocr`
  `GET /api/v1/ocr/{request_id}`
  `POST /api/v1/marker`
  `GET /api/v1/marker/{request_id}`

## 참고

- `app`는 `.env`의 `OCR_SERVICE_URL=http://ocr-service:8000` 을 통해 OCR API를 호출한다.
- `ocr-service`는 `CHANDRA_METHOD=vllm` 과 `VLLM_API_BASE=http://vllm-ocr:8000/v1` 로 vLLM 모델 서버를 사용한다.
