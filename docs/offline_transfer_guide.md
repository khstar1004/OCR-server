# Offline Docker Transfer Guide

이 문서는 현재 저장소를 국방망 반입용 오프라인 Docker 번들로 만드는 기준을 정리한다.

기준 시점:

- 작업 기준일: 2026-04-03
- 저장소 경로: `C:\Users\USER\Desktop\a-cong-OCR-V2`

## 먼저 결론

- 현재 상태 기준으로 `700MB CD` 1장 반입은 불가능하다.
- 현재 구성은 `Docker 이미지`와 `news_models`를 분리해서 반입하는 방식이 가장 현실적이다.
- 이유는 현재 `docker-compose.yml`이 이미 `./news_models -> /models` 볼륨 마운트 구조를 쓰고 있고, `.dockerignore`도 `news_models`를 이미지 빌드 컨텍스트에서 제외하기 때문이다.
- 모델을 이미지 안에 굽는 방식은 반입물 한 덩어리가 너무 커지고, 재반출/재반입 시에도 비효율적이다.

## 현재 확인된 원본 크기

현재 로컬에서 확인한 기준:

- `a-cong-ocr:chandra` Docker 이미지 unique size: 약 `21.25 GB` (`19.79 GiB`)
- `news_models`: 약 `9.88 GiB`
- `news_data`: 약 `0.41 GiB`
- `news_pdfs`: 약 `0.00 GiB`
- 전체 번들 합계: 약 `30.09 GiB`

핵심 원인:

- `news_models/chandra-ocr-2/model.safetensors` 단일 파일이 약 `10.59 GB` (`9.86 GiB`)
- 이 파일이 전체 반입 용량 대부분을 차지한다.

## 압축 예상

실무적으로는 아래처럼 보는 것이 맞다.

### 1. 모델 폴더

- `model.safetensors`는 수치 모델 가중치 바이너리라 압축 효율이 낮다.
- `7z` 기준으로도 보통 `2% ~ 8%` 정도만 줄어드는 편으로 봐야 한다.
- 따라서 `news_models`는 대략 `9.1 ~ 9.7 GiB` 정도로만 줄어든다고 보는 것이 안전하다.

### 2. Docker 이미지 tar

- `docker save` 결과 tar는 압축 전송 시 어느 정도 줄어든다.
- 다만 CUDA, torch, transformers, 시스템 라이브러리가 많아서 아주 작아지지는 않는다.
- `7z` 기준 보수적으로 `20% ~ 35%` 정도 절감, `zip` 기준은 그보다 덜 줄어드는 것으로 잡는 편이 안전하다.

### 3. 현재 저장소 기준 현실적 예측

- `7z` 현실값 가정:
  - 전체 번들 약 `24.6 GiB`
- `zip` 현실값 가정:
  - 전체 번들 약 `26.2 GiB`

즉 압축해도 `CD 700MB`에 들어가는 수준은 아니다.

## 광매체 장수 예측

현재 전체 번들을 기준으로 보면 다음 정도가 필요하다.

### 원본 기준

- `CD-700MB`: `47장`
- `DVD-4.7GB`: `7장`
- `DVD-DL-8.5GB`: `4장`
- `BD-25GB`: `2장`

### 7z 압축 현실값 기준

- `CD-700MB`: `38장`
- `DVD-4.7GB`: `6장`
- `DVD-DL-8.5GB`: `4장`
- `BD-25GB`: `2장`

따라서 `CD`가 정책적으로 고정이면 사실상 운영 가능한 방식이 아니다.
광매체를 유지해야 한다면 최소 `DVD-DL 다장` 또는 `BD`가 필요하다.

## 권장 반입 전략

권장 순서:

1. Docker 이미지는 따로 export 한다.
2. `news_models`는 별도 압축본으로 분리한다.
3. `news_data`는 반드시 필요한 초기 데이터만 포함한다.
4. `news_pdfs`는 샘플만 넣고 실제 입력은 망 내부에서 받는다.

가장 실무적인 반입물 구성:

- `a-cong-ocr-image.tar.7z`
- `news_models.7z.001`, `news_models.7z.002`, ...
- `news_data.7z` 또는 생략
- `.env.example`
- `docker-compose.yml`
- `README.md`
- 해시 목록 파일

## 오프라인 설치 필요 여부

필요하다. 다만 구분을 정확히 해야 한다.

### 1. 국방망 안에서 필요 없는 것

이미지를 외부망에서 미리 만들어 반입하면, 국방망 안에서 아래 작업은 할 필요가 없다.

- `pip install`
- `docker build`
- Hugging Face 모델 다운로드
- Git clone

즉 반입 후에는 `압축 해제 -> docker load -> docker compose up` 경로로 가야 한다.

### 2. 국방망 안에서 미리 오프라인 설치되어 있어야 하는 것

호스트 서버에는 아래가 사전에 오프라인 설치되어 있어야 한다.

- Docker Engine
- Docker Compose v2 plugin
- NVIDIA Driver
- NVIDIA Container Toolkit
- 압축 해제 도구 (`7z` 권장)

현재 런타임 기준:

- 앱 이미지: `a-cong-ocr:chandra`
- 보조 이미지: `vllm/vllm-openai:v0.17.0`
- 컨테이너 OS: `Ubuntu 22.04.4 LTS`
- 컨테이너 Python: `3.10.12`
- CUDA 런타임 베이스: `nvidia/cuda:12.4.1-cudnn-runtime-ubuntu22.04`

### 3. 중요한 점

현재 compose는 `gpus: all` 기준이다.

- GPU 서버가 아니면 현재 이미지 그대로는 운영이 어렵다.
- GPU 서버라도 NVIDIA 드라이버와 Container Toolkit이 없으면 컨테이너는 떠도 OCR 추론이 정상 동작하지 않는다.
- 국방망 서버에 Docker만 있고 GPU 런타임이 없으면, 별도 CPU 경량 이미지로 다시 만들어야 한다.

## 온라인 환경에서 준비

### 1. Docker 이미지 export

```powershell
docker save -o .\dist\a-cong-ocr_chandra.tar a-cong-ocr:chandra
```

정확한 이미지 반입 크기를 보려면 tar를 만든 뒤 아래 스크립트로 다시 계산한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\estimate_offline_bundle.ps1 `
  -ImageName a-cong-ocr:chandra `
  -ImageTarPath .\dist\a-cong-ocr_chandra.tar `
  -ArchiveKind 7z
```

### 2. 압축

`7z` 사용 권장:

- 이미지 tar: `-mx=5`
- 모델 폴더: `-mx=1` 또는 `-mx=3`

이유:

- 이미지 tar는 압축 이득이 조금 있다.
- 모델은 압축 이득이 매우 작아서 고압축만 걸면 시간만 오래 걸린다.

예시:

```powershell
7z a -mx=5 .\dist\a-cong-ocr_chandra.tar.7z .\dist\a-cong-ocr_chandra.tar
7z a -mx=1 .\dist\news_models.7z .\news_models\chandra-ocr-2
```

### 3. 광매체 분할

매체별로 여유를 두고 쪼개는 편이 안전하다.

- CD: `-v650m`
- DVD 4.7GB: `-v4300m`
- DVD-DL 8.5GB: `-v8000m`
- BD-25GB: `-v23000m`

예시:

```powershell
7z a -mx=1 -v650m .\dist\news_models_cd.7z .\news_models\chandra-ocr-2
7z a -mx=5 -v650m .\dist\a-cong-ocr_image_cd.7z .\dist\a-cong-ocr_chandra.tar
```

## 국방망 내부 복원

### 1. 파일 복사 및 압축 해제

광매체에서 모든 분할 파일을 같은 폴더에 모은 뒤 첫 파일만 기준으로 해제한다.

```powershell
7z x .\news_models.7z.001 -o.\deploy
7z x .\a-cong-ocr_chandra.tar.7z -o.\deploy
```

### 2. Docker 이미지 load

```powershell
docker load -i .\deploy\a-cong-ocr_chandra.tar
```

### 3. 디렉터리 배치

복원 후 최소 구조:

- `.\deploy\news_models\chandra-ocr-2`
- `.\deploy\news_data`
- `.\deploy\news_pdfs`

### 4. 실행

```powershell
Copy-Item .env.example .env
docker compose up -d
```

## 반드시 따로 준비해야 하는 것

오프라인 반입물에 이것까지 자동 포함되는 것은 아니다.

- Docker Engine
- Docker Compose
- NVIDIA Driver
- NVIDIA Container Toolkit
- GPU/CUDA 호환성 확인

이 항목들은 망 내부 서버에 미리 설치되어 있어야 한다.

## 권장 판단

- `CD 700MB`만 허용되면 이 프로젝트를 현재 상태 그대로 들고 들어가는 방식은 비실용적이다.
- 같은 광매체 정책이라도 `DVD-DL` 또는 `BD`로 바꾸는 것이 맞다.
- 만약 매체 정책을 바꿀 수 없다면, 반입물 자체를 다시 설계해야 한다.
  - 더 작은 OCR 모델 사용
  - CPU 경량판 이미지 별도 제작
  - 샘플 데이터 제거
  - 모델 서버 분리
