# Defense Remote OCR Bundle

국방망 반입 기준은 `app + ocr-service + vllm-ocr` 3컨테이너 구성이다.
이미지 tar는 고정 자산이고, 현장 커스텀은 `docker-compose.defense-remote-ocr.yml`과 `.env`에서 처리한다.
국방망 compose 제약 때문에 GPU 연결은 `gpus:` 대신 `runtime: nvidia`로 고정한다.

## 권장 번들 생성 경로

현재 저장소 기준으로 반입 폴더는 아래 스크립트로 다시 생성한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_defense_remote_ocr_carry_in.ps1 `
  -Clean `
  -RebuildAppImage `
  -RebuildVllmImage
```

기본 출력 폴더:

- `dist/defense-remote-ocr-carry-in`

이 폴더만 통째로 반입하면 된다.

## 반입 필수 항목

- `dist/a-cong-ocr_chandra.tar`
- `dist/a-cong-vllm-openai_chandra.tar`
- `news_models/chandra-ocr-2/`
- `docker-compose.defense-remote-ocr.yml`
- `.env.example`
- `.env`
- `scripts/load_offline_images.ps1`
- `scripts/load_offline_images.sh`
- `scripts/start_defense_remote_ocr.ps1`
- `scripts/start_defense_remote_ocr.sh`

컨테이너 3개지만 이미지 tar는 2개다.

- `app`와 `ocr-service`는 같은 `a-cong-ocr:chandra` 이미지를 같이 쓴다.
- `vllm-ocr`만 별도 `a-cong-vllm-openai:chandra` 이미지를 쓴다.
- 따라서 `a-cong-ocr_chandra.tar` 안에 이미 `app`용 이미지가 포함되어 있다.

## 반입 권장 항목

- `docs/defense_remote_ocr_bundle.md`
- `docs/open_source_intake_list.csv`
- `docs/open_source_intake_list.md`
- `CARRY_IN_MANIFEST.txt`
- `SERVICE_IMAGE_MAP.txt`

## 국방망 초기 실행

```bash
chmod +x ./scripts/load_offline_images.sh ./scripts/start_defense_remote_ocr.sh
./scripts/load_offline_images.sh
```

이 단계에서 custom `vllm` 이미지 안에서 Chandra 모델 디렉터리와 런타임 호환성 검증도 함께 수행된다.
내부적으로는 upstream 설정의 `model_type=qwen3_5`를 확인하지만, 실제 서빙 모델은 전체 Chandra OCR이다.
같은 단계에서 `--runtime=nvidia` GPU 런타임도 점검한다.

그다음 `.env`를 수정한다.

반드시 확인할 값:

- `TARGET_API_BASE_URL`
- `OCR_SERVICE_URL=http://ocr-service:8000`
- `VLLM_API_BASE=http://vllm-ocr:8000/v1`
- `WATCH_DIR`, `DATA_DIR`, `MODELS_DIR`, `MODEL_CACHE_DIR`
- `API_HOST_PORT`, `OCR_API_HOST_PORT`

실행:

```bash
./scripts/start_defense_remote_ocr.sh
```

`.env`를 다시 바꾼 뒤 반영할 때:

```bash
docker compose -f ./docker-compose.defense-remote-ocr.yml up -d --no-build --force-recreate
```

## 서비스 역할

- `app`: 뉴스 PDF OCR 작업 실행, 결과 조회, callback 전송
- `ocr-service`: 범용 OCR API 엔드포인트 제공
- `vllm-ocr`: Chandra OCR 모델 서빙

## 기본 포트

- `app`: `18007`
- `ocr-service`: `18009`
- `vllm-ocr`: 내부 `8000`

## 참고

- `app`는 `.env`의 `OCR_SERVICE_URL=http://ocr-service:8000` 을 통해 OCR API를 호출한다.
- `ocr-service`는 `CHANDRA_METHOD=vllm` 과 `VLLM_API_BASE=http://vllm-ocr:8000/v1` 로 vLLM 모델 서버를 사용한다.
- `TARGET_API_BASE_URL`은 런타임 환경변수라서 새 tar를 다시 만들지 않아도 `.env` 수정만으로 바꿀 수 있다.
