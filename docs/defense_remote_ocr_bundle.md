# Defense Remote OCR Bundle

국방망 반입 기준은 `app + playground + ocr-service + vllm-ocr` 4컨테이너 구성이다.
이미지 tar는 고정 자산이고, 현장 커스텀은 `docker-compose.defense-remote-ocr.yml`과 `.env`에서 처리한다.
국방망 compose 제약 때문에 GPU 연결은 `gpus:` 대신 `runtime: nvidia`로 고정한다.

## 권장 번들 생성 경로

현재 저장소 기준으로 반입 폴더는 아래 스크립트로 다시 생성한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\prepare_defense_remote_ocr_carry_in.ps1 `
  -Clean `
  -RebuildUiImage `
  -RebuildAppImage `
  -RebuildVllmImage
```

기본 출력 폴더:

- `dist/defense-remote-ocr-carry-in`

이 폴더만 통째로 반입하면 된다.

## 반입 필수 항목

- `dist/a-cong-ocr-ui_chandra.tar`
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

컨테이너는 4개이고 이미지 tar는 3개다.

- `app`와 `playground`는 `a-cong-ocr-ui:chandra` 이미지를 같이 쓴다.
- `ocr-service`는 `a-cong-ocr:chandra` 이미지를 쓴다.
- `vllm-ocr`는 `a-cong-vllm-openai:chandra` 이미지를 쓴다.

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

이 단계에서 세 Docker 이미지가 모두 로드되고, custom `vllm` 이미지 안에서 Chandra 모델 디렉터리와 런타임 호환성 검증도 함께 수행된다.
내부적으로는 upstream 설정의 `model_type=qwen3_5`를 확인하지만, 실제 서빙 모델은 전체 Chandra OCR이다.
같은 단계에서 `--runtime=nvidia` GPU 런타임도 점검한다.

그다음 `.env`를 수정한다.

반드시 확인할 값:

- `PLAYGROUND_ADMIN_PASSWORD`: 기본/예시 비밀번호 금지. 12자 이상 현장 비밀번호 사용.
- `TARGET_API_BASE_URL`: 국회/업무망 전송 대상이 있을 때 내부망 `/news` 주소로 설정.
- `UI_IMAGE=a-cong-ocr-ui:chandra`
- `OCR_IMAGE=a-cong-ocr:chandra`
- `VLLM_IMAGE=a-cong-vllm-openai:chandra`
- `OCR_SERVICE_URL=http://ocr-service:8000`
- `PLAYGROUND_UPSTREAM_BASE_URL=http://ocr-service:8000`
- `VLLM_API_BASE=http://vllm-ocr:8000/v1`
- `WATCH_DIR`, `DATA_DIR`, `MODELS_DIR`, `MODEL_CACHE_DIR`
- `API_HOST_PORT=18007`, `PLAYGROUND_HOST_PORT=18109`, `OCR_API_HOST_PORT=18009`

실행:

```bash
./scripts/start_defense_remote_ocr.sh
```

`.env`를 다시 바꾼 뒤 반영할 때:

```bash
docker compose -f ./docker-compose.defense-remote-ocr.yml up -d --no-build --force-recreate
```

## 서비스 역할

- `app`: 뉴스 PDF OCR 작업 실행, 결과 조회, callback 전송, 국회 OCR 운영 UI
- `playground`: OCR 체험/검증 UI, 관리자 계정, 과거 작업 기록 조회 proxy
- `ocr-service`: 범용 OCR API 엔드포인트 제공
- `vllm-ocr`: Chandra OCR 모델 서빙

## 기본 포트

- `app`: `18007`
- `playground`: `18109`
- `ocr-service`: `18009`
- `vllm-ocr`: 내부 `8000`

## 확인 순서

```bash
python ./scripts/validate_defense_remote_ocr_bundle.py --bundle-dir .
docker compose -f ./docker-compose.defense-remote-ocr.yml config
docker compose -f ./docker-compose.defense-remote-ocr.yml ps
curl http://127.0.0.1:18007/health
curl http://127.0.0.1:18109/health
curl http://127.0.0.1:18109/playground/api/health
curl http://127.0.0.1:18009/health
```

## 참고

- 국방망 안에서는 `docker build`를 실행하지 않는다. 이미지 tar와 모델 스냅샷을 반입해서 `docker load`만 한다.
- `app/playground`의 관리자 계정과 런타임 설정은 `${DATA_DIR}/runtime-config`에 저장된다.
- `playground` 컨테이너도 `${DATA_DIR}`를 마운트해야 계정, 설정, 작업 기록 링크가 재시작 후 유지된다.
- `TARGET_API_BASE_URL`, `LLM_BASE_URL`은 비워 두면 외부 호출을 하지 않는다.
- `TARGET_API_BASE_URL`은 런타임 환경변수라서 새 tar를 다시 만들지 않아도 `.env` 수정만으로 바꿀 수 있다.
