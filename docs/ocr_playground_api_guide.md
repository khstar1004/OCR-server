# army-ocr API Guide

작성 기준: 현재 저장소의 `app/ocr_service.py`, `app/api/playground.py`, `app/services/datalab_compat.py`, `app/services/datalab_defense.py`

## 1. 모델 개요

army-ocr은 Chandra OCR 모델을 서버 API로 제공하는 범용 문서 OCR 시스템입니다.

이 API는 단순히 이미지에서 글자만 읽는 것이 아니라, 문서 페이지를 구조화된 데이터로 바꿉니다.

- 페이지 전체 텍스트
- 제목, 본문, 이미지 등 layout block
- block별 bbox 좌표
- JSON, Markdown, HTML, chunks
- 페이지 이미지와 crop 이미지를 포함한 ZIP 산출물

외부 시스템이 직접 연동해야 하는 것은 `/api/v1/*` 범용 OCR API입니다. 국회 OCR은 이 범용 OCR API 결과를 국회 문서 처리에 맞게 가공한 특화 레이어입니다. `/playground/*`는 모델/API 품질을 사람이 확인하는 데모 UI입니다.

| 구분 | 설명 | 대표 경로 |
| --- | --- | --- |
| 범용 OCR API | 외부 시스템 연동 대상 | `/api/v1/ocr`, `/api/v1/marker` |
| Chandra OCR 모델 | OCR/layout 추론 엔진 | vLLM 또는 HF runner |
| 국회 OCR | 범용 OCR 결과를 국회 문서 처리에 맞게 후처리 | 운영 데모/국회 처리 경로 |
| 체험 UI | OCR API 결과를 브라우저에서 확인 | `/playground/` |

## 2. 기본 주소

| 환경 | Base URL |
| --- | --- |
| 로컬 preview | `http://127.0.0.1:18110` |
| 인터넷망 preview | `http://14.50.225.33:18110` |
| nocodeaidev ingress | `https://nocodeaidev.army.mil:20443/a-cong-ocr-playground` |

운영 연동 시에는 `/api/v1/*`를 기준으로 계약하는 것이 맞습니다.

## 3. API 계약

이 문서는 현재 FastAPI OpenAPI에 실제 등록된 endpoint만 기준으로 작성합니다.

| 항목 | 값 |
| --- | --- |
| API prefix | `/api/v1` |
| 인증 | preview 없음. 운영 공개 시 gateway/reverse proxy 인증 권장 |
| 업로드 요청 | `multipart/form-data` |
| record 기반 요청 | `application/json` |
| 비동기 상태 | `processing`, `complete`, `failed` |
| 주요 ID | `request_id`, `file_id`, `document_id`, `workflow_id`, `batch_run_id` |

## 4. Quickstart

### 4.1 Health 확인

```bash
curl http://127.0.0.1:18110/health
```

### 4.2 지원 기능 확인

```bash
curl http://127.0.0.1:18110/api/v1/capabilities
```

### 4.3 문서 변환

```bash
curl -X POST "http://127.0.0.1:18110/api/v1/marker" \
  -F "file=@sample.pdf" \
  -F "output_format=json,markdown,html,chunks" \
  -F "page_range=0" \
  -F "mode=balanced"
```

응답에서 `request_id`를 받고 결과를 조회합니다.

```bash
curl "http://127.0.0.1:18110/api/v1/marker/{request_id}"
```

### 4.4 Python polling 예시

```python
import time
import requests

base_url = "http://127.0.0.1:18110"

with open("sample.pdf", "rb") as file:
    submit = requests.post(
        f"{base_url}/api/v1/marker",
        files={"file": file},
        data={
            "output_format": "json,markdown,html,chunks",
            "page_range": "0",
            "mode": "balanced",
        },
    ).json()

request_id = submit["request_id"]

while True:
    result = requests.get(f"{base_url}/api/v1/marker/{request_id}").json()
    if result["status"] != "processing":
        break
    time.sleep(1)

print(result["status"])
print(result.get("markdown", ""))
```

## 5. 동기/비동기 선택

| 방식 | Endpoint | 언제 사용 | 결과 |
| --- | --- | --- | --- |
| 동기 이미지 OCR | `POST /api/v1/ocr/image` | 이미지 1장 즉시 분석 | 응답 본문 |
| 동기 PDF OCR | `POST /api/v1/ocr/pdf` | 짧은 PDF를 바로 처리 | 응답 본문 |
| 비동기 OCR | `POST /api/v1/ocr` | 긴 문서, 작업 큐 연동 | `GET /api/v1/ocr/{request_id}` |
| 비동기 문서 변환 | `POST /api/v1/marker` | JSON/Markdown/HTML/chunks 필요 | `GET /api/v1/marker/{request_id}` |

## 6. 대표 연동 흐름

### 6.1 바로 OCR/문서 변환

```text
POST /api/v1/marker
GET  /api/v1/marker/{request_id}
```

### 6.2 파일 저장소 기반 문서 처리

```text
POST /api/v1/files
POST /api/v1/create_document
GET  /api/v1/create_document/{request_id}
POST /api/v1/convert_document
GET  /api/v1/convert_document/{request_id}
```

### 6.3 구조화 추출

```text
POST /api/v1/generate_extraction_schemas
GET  /api/v1/generate_extraction_schemas/{request_id}
POST /api/v1/extract_structured_data
GET  /api/v1/extract_structured_data/{request_id}
```

### 6.4 컬렉션 기반 일괄 처리

```text
POST /api/v1/collections
POST /api/v1/collections/{collection_id}/files
POST /api/v1/batch_runs
GET  /api/v1/batch_runs/{batch_run_id}/results
```

## 7. 처리 파이프라인

1. 입력 수신: 업로드 파일 또는 `file_url`을 받습니다.
2. 페이지 렌더링: PDF는 페이지 이미지로 렌더링합니다.
3. Chandra OCR 추론: vLLM 또는 HF runner로 OCR/layout 추론을 실행합니다.
4. 구조화: 블록, bbox, 텍스트, Markdown, HTML, chunks를 구성합니다.
5. 저장: 요청별 `request_id` 디렉터리에 결과와 이미지 산출물을 분리 저장합니다.

## 8. 지원 입력

| 입력 | 지원 | 메모 |
| --- | --- | --- |
| PDF | 지원 | 페이지별 이미지 렌더링 후 OCR |
| PNG/JPG/JPEG/WEBP | 지원 | 단일 이미지 또는 문서 이미지 OCR |
| URL 입력 | 지원 | `file_url` 또는 UI URL 입력 |
| DOCX/스프레드시트 | 범용 OCR 경로에서는 미지원 | PDF/이미지 변환 후 입력 권장 |

## 9. 공통 규칙

| 항목 | 규칙 |
| --- | --- |
| `page_range` | zero-based index. 예: `0`, `0-2`, `0,2,4` |
| 응답 `page_number` | 1-based page number |
| `bbox` | `[x1, y1, x2, y2]` |
| 좌표 기준 | 렌더링된 페이지 이미지 픽셀 |
| 상태값 | `processing`, `complete`, `failed` |

## 10. 출력 형식

| output | 설명 | 사용처 |
| --- | --- | --- |
| `json` | 페이지, 블록, bbox, metadata 구조 | 후처리, DB 저장, 감사 |
| `markdown` | 사람이 읽기 쉬운 문서 결과 | RAG, 지식관리, 리뷰 |
| `html` | 브라우저 렌더링용 문서 | 웹 검수, 미리보기 |
| `chunks` | 검색/임베딩용 조각 | 벡터 검색, 색인 |
| `zip` | 결과 문서와 이미지 포함 archive | 오프라인 전달, 재현 가능한 보관 |

## 11. 오류 처리

| HTTP status | 대표 상황 | 대응 |
| --- | --- | --- |
| `400` | 빈 파일, 잘못된 mode/page_range, 필수 입력 누락 | 요청 파라미터와 파일 확인 |
| `404` | 없는 request/file/document/template ID | ID와 결과 보존 기간 확인 |
| `413` | 체험 UI 업로드 크기 제한 초과 | 문서 분할 또는 제한 조정 |
| `422` | FastAPI validation 오류 | 필드 타입과 JSON 구조 확인 |
| `503` | OCR 서비스 초기화 전 | health와 vLLM/OCR worker 상태 확인 |

## 12. 이미지 보존

이미지가 중요한 문서에서는 Markdown에 이미지 링크만 남기면 결과 재현성이 떨어집니다. 체험 UI의 다운로드는 실제 이미지 파일을 함께 포함한 ZIP으로 제공합니다.

```text
result.json
result.md
result.html
README.txt
images/page-0001.png
images/page-0001-image-0001.png
```

## 13. API Reference

### Health

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/health` | OCR 서비스 health |
| `GET` | `/api/health` | 호환 health alias |
| `GET` | `/api/v1/health` | API prefix health |

### Capabilities

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/api/v1/capabilities` | 지원 입력/출력/기능/endpoint 조회 |

### 동기 OCR

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/ocr/image` | 이미지 1장 OCR |
| `POST` | `/api/v1/ocr/pdf` | PDF 페이지별 OCR |

### 비동기 OCR

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/ocr` | OCR 요청 제출 |
| `GET` | `/api/v1/ocr/{request_id}` | OCR 결과 조회 |

### Marker 문서 변환

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/marker` | 문서 변환 요청 제출 |
| `GET` | `/api/v1/marker/{request_id}` | 변환 결과 조회 |

Form fields:

| Field | 기본값 | 설명 |
| --- | --- | --- |
| `file` 또는 `file.0` | - | 업로드 파일 |
| `file_url` | - | 파일 URL 또는 로컬 경로 |
| `output_format` | `markdown` | `json,markdown,html,chunks` 복수 지정 |
| `mode` | `balanced` | `fast`, `balanced`, `accurate` |
| `max_pages` | 전체 | 최대 페이지 수 |
| `page_range` | 전체 | zero-based page range |
| `paginate` | `false` | 페이지 구분 유지 |
| `add_block_ids` | `false` | block id 부여 |
| `include_markdown_in_chunks` | `false` | chunk에 Markdown 포함 |
| `skip_cache` | `false` | cache bypass metadata |

주요 응답 필드:

| Field | 설명 |
| --- | --- |
| `status` | `processing`, `complete`, `failed` |
| `success` | 처리 성공 여부 |
| `request_id` | 결과 조회 및 산출물 조회에 쓰는 ID |
| `json.pages[].blocks[]` | label, bbox, text, confidence를 포함한 OCR block |
| `markdown` | 문서 본문과 이미지 참조를 포함한 Markdown |
| `html` | 브라우저 렌더링용 HTML |
| `chunks` | 검색/임베딩용 chunk |
| `runtime` | 처리 시간, page count, request kind metadata |

### Thumbnails

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/api/v1/thumbnails/{lookup_key}` | request 결과의 페이지 이미지 썸네일 |

### Request cleanup

| Method | Path | 설명 |
| --- | --- | --- |
| `DELETE` | `/api/v1/requests` | 오래된 request 결과 삭제 |

### Runtime settings

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/api/v1/runtime-settings` | 관리자 세션 필요. 현재 런타임 설정, 환경변수 기본값, 저장 파일 위치 조회 |
| `PUT` | `/api/v1/runtime-settings` | 관리자 세션 필요. OCR/API/playground/국회 연동 설정 저장 |

### Playground account/admin

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/playground/login` | 로그인/계정 신청 화면 |
| `GET` | `/playground/admin` | 관리자 페이지. 로그인한 관리자만 접근 |
| `POST` | `/playground/api/auth/signup` | 일반 사용자 계정 신청 |
| `POST` | `/playground/api/auth/login` | 승인된 사용자 로그인 |
| `POST` | `/playground/api/auth/logout` | 로그아웃 |
| `GET` | `/playground/api/auth/me` | 현재 로그인 상태 확인 |
| `GET` | `/playground/api/admin/users` | 관리자 전용 계정 목록 |
| `POST` | `/playground/api/admin/users/{user_id}/approve` | 관리자 전용 계정 승인 |
| `POST` | `/playground/api/admin/users/{user_id}/reject` | 관리자 전용 계정 반려 |
| `GET` | `/playground/api/admin/runtime-settings` | 관리자 전용 운영 설정 조회 |
| `PUT` | `/playground/api/admin/runtime-settings` | 관리자 전용 운영 설정 저장 |

## 14. 구현된 호환 API

### Workflows

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/api/v1/workflows/step_types` | step type 목록 |
| `GET` | `/api/v1/workflows/workflows` | workflow 목록 |
| `POST` | `/api/v1/workflows/workflows` | workflow 생성 |
| `GET` | `/api/v1/workflows/workflows/{workflow_id}` | workflow 조회 |
| `DELETE` | `/api/v1/workflows/workflows/{workflow_id}` | workflow 삭제 |
| `POST` | `/api/v1/workflows/workflows/{workflow_id}/execute` | workflow 실행 |
| `GET` | `/api/v1/workflows/executions/{execution_id}` | 실행 상태 조회 |

### Files

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/files` | multipart 파일 업로드 |
| `POST` | `/api/v1/files/request_upload_url` | upload slot 발급 |
| `PUT` | `/api/v1/files/uploads/{upload_id}` | upload slot에 payload 업로드 |
| `GET` | `/api/v1/files/uploads/{upload_id}/confirm` | upload 확정 |
| `GET` | `/api/v1/files` | 파일 목록 |
| `GET` | `/api/v1/files/{file_id}` | 파일 record 조회 |
| `GET` | `/api/v1/files/{file_id}/metadata` | 파일 metadata 조회 |
| `GET` | `/api/v1/files/{file_id}/download_url` | 다운로드 URL 조회 |
| `GET` | `/api/v1/files/{file_id}/download` | 파일 다운로드 |
| `DELETE` | `/api/v1/files/{file_id}` | 파일 삭제 |

직접 업로드 예시:

```bash
curl -X POST "http://127.0.0.1:18110/api/v1/files" \
  -F "file=@sample.pdf"
```

Upload slot 예시:

```bash
curl -X POST "http://127.0.0.1:18110/api/v1/files/request_upload_url" \
  -H "content-type: application/json" \
  -d '{"file_name":"sample.pdf","content_type":"application/pdf"}'

curl -X PUT "http://127.0.0.1:18110/api/v1/files/uploads/{upload_id}" \
  --data-binary "@sample.pdf"

curl "http://127.0.0.1:18110/api/v1/files/uploads/{upload_id}/confirm"
```

### Documents

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/create_document` | 문서 생성 요청 |
| `GET` | `/api/v1/create_document/{request_id}` | 문서 생성 결과 조회 |
| `GET` | `/api/v1/documents/{document_id}` | 문서 record 조회 |
| `POST` | `/api/v1/convert_document` | 문서 변환 요청 |
| `GET` | `/api/v1/convert_document/{request_id}` | 변환 결과 조회 |
| `POST` | `/api/v1/segment_document` | 문서 분할 요청 |
| `GET` | `/api/v1/segment_document/{request_id}` | 분할 결과 조회 |

문서 변환 예시:

```bash
curl -X POST "http://127.0.0.1:18110/api/v1/create_document" \
  -H "content-type: application/json" \
  -d '{"file_id":"file_..."}'

curl -X POST "http://127.0.0.1:18110/api/v1/convert_document" \
  -H "content-type: application/json" \
  -d '{"document_id":"doc_...","output_format":"json,markdown"}'

curl "http://127.0.0.1:18110/api/v1/convert_document/{request_id}"
```

### Extraction, forms, scoring

| Method | Path | 설명 |
| --- | --- | --- |
| `POST` | `/api/v1/generate_extraction_schemas` | schema 생성 |
| `GET` | `/api/v1/generate_extraction_schemas/{request_id}` | schema 생성 결과 |
| `POST` | `/api/v1/extract_structured_data` | 구조화 추출 |
| `GET` | `/api/v1/extract_structured_data/{request_id}` | 추출 결과 |
| `POST` | `/api/v1/score_extraction_results` | 추출 결과 평가 |
| `GET` | `/api/v1/score_extraction_results/{request_id}` | 평가 결과 |
| `POST` | `/api/v1/form_filling` | 폼 채우기 |
| `GET` | `/api/v1/form_filling/{request_id}` | 폼 채우기 결과 |
| `POST` | `/api/v1/track_changes` | 변경 추적 |
| `GET` | `/api/v1/track_changes/{request_id}` | 변경 추적 결과 |

구조화 추출 예시:

```bash
curl -X POST "http://127.0.0.1:18110/api/v1/generate_extraction_schemas" \
  -H "content-type: application/json" \
  -d '{"field_names":["title","summary","document_date"]}'

curl -X POST "http://127.0.0.1:18110/api/v1/extract_structured_data" \
  -H "content-type: application/json" \
  -d '{"document_id":"doc_...","schema":{"name":"report","fields":[{"name":"title"},{"name":"summary"}]}}'
```

### Collections, templates, eval rubrics, batch

| 그룹 | 구현 경로 |
| --- | --- |
| Collections | `/api/v1/collections`, `/api/v1/collections/{collection_id}`, `/api/v1/collections/{collection_id}/files` |
| Templates | `/api/v1/templates`, `/api/v1/templates/promote`, `/api/v1/templates/{template_id}`, `/api/v1/templates/{template_id}/examples` |
| Eval Rubrics | `/api/v1/eval_rubrics`, `/api/v1/eval_rubrics/{rubric_id}` |
| Batch Runs | `/api/v1/batch_runs`, `/api/v1/batch_runs/{batch_run_id}`, `/api/v1/batch_runs/{batch_run_id}/results` |
| Custom Pipeline 조회 | `/api/v1/check_pipeline_access`, `/api/v1/custom_pipelines` |

Batch run 예시:

```bash
curl -X POST "http://127.0.0.1:18110/api/v1/collections" \
  -H "content-type: application/json" \
  -d '{"name":"demo-set","file_ids":["file_1","file_2"]}'

curl -X POST "http://127.0.0.1:18110/api/v1/batch_runs" \
  -H "content-type: application/json" \
  -d '{"collection_id":1,"operation":"convert_document","params":{"output_format":"json"}}'

curl "http://127.0.0.1:18110/api/v1/batch_runs/{batch_run_id}/results"
```

Workflow 예시:

```bash
curl "http://127.0.0.1:18110/api/v1/workflows/step_types"

curl -X POST "http://127.0.0.1:18110/api/v1/workflows/workflows" \
  -H "content-type: application/json" \
  -d '{"name":"OCR Workflow","steps":[{"step_key":"ocr","unique_name":"ocr_step","settings":{"max_pages":1}}]}'

curl -X POST "http://127.0.0.1:18110/api/v1/workflows/workflows/{workflow_id}/execute" \
  -H "content-type: application/json" \
  -d '{"input_config":{"file_urls":["C:/path/sample.pdf"]}}'

curl "http://127.0.0.1:18110/api/v1/workflows/executions/{execution_id}"
```

## 15. 체험 UI 경로

아래 경로는 제품 OCR API가 아니라 브라우저 체험 UI와 QA를 위한 보조 경로입니다.

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/playground/` | OCR 체험 UI |
| `GET` | `/playground/docs` | 한국어 OCR 모델 문서 |
| `GET` | `/playground/api-reference` | 한국어 API Reference |
| `GET` | `/playground/api-guide.md` | Markdown 문서 |
| `POST` | `/playground/api/convert/start` | UI용 비동기 OCR 변환 시작 |
| `GET` | `/playground/api/convert/{request_id}` | UI 변환 상태와 결과 조회 |
| `POST` | `/playground/api/convert` | 이전 UI 호환용 동기 변환 |
| `GET` | `/playground/api/history` | Playground OCR 작업 기록 조회 |
| `GET` | `/playground/api/images/{request_id}/{asset_name}` | 페이지/crop 이미지 조회 |
| `GET` | `/playground/api/download/{request_id}` | 이미지 포함 ZIP 다운로드 |
| `GET` | `/playground/api/auth/me` | 로그인 상태 확인 |
| `POST` | `/playground/api/auth/signup` | 계정 신청 |
| `GET` | `/playground/api/admin/runtime-settings` | 관리자 전용 운영 설정 조회 |
| `PUT` | `/playground/api/admin/runtime-settings` | 관리자 전용 운영 설정 저장 |

## 16. 운영과 동시성

`OCR_MAX_CONCURRENT_REQUESTS`는 OCR 모델 진입 동시성을 제어합니다.

단일 vLLM worker/GPU 구성에서는 `1`을 권장합니다.

동시에 2-5명이 요청하면:

1. 각 요청은 고유 `request_id`를 받습니다.
2. 결과 디렉터리는 request별로 분리됩니다.
3. OCR 모델 호출은 gate를 통과한 요청만 실행됩니다.
4. 초과 요청은 대기합니다.

실제 병렬 처리량을 늘리려면 다음을 함께 조정해야 합니다.

- `OCR_MAX_CONCURRENT_REQUESTS`
- vLLM `--max-num-seqs`
- GPU 메모리와 `VLLM_GPU_MEMORY_UTILIZATION`
- page range와 max pages 제한

## 17. 운영 설정 변경

컨테이너 이미지를 다시 만들지 않아도 자주 바뀌는 값은 런타임 설정으로 바꿀 수 있습니다. 이 설정은 `/playground/admin` 관리자 페이지 또는 관리자 세션을 가진 API 요청에서만 수정합니다.

설정 저장 위치는 기본적으로 `RUNTIME_CONFIG_PATH`입니다. 값이 비어 있으면 `OUTPUT_ROOT/_runtime_config/settings.json`에 저장합니다. Docker/k8s 배포에서는 `/data/runtime/runtime-config/settings.json`을 쓰도록 설정해 app, OCR API, playground가 같은 PVC의 값을 읽습니다.

계정/세션 저장 위치는 `AUTH_STORE_PATH`입니다. Docker/k8s 기본값은 `/data/runtime/runtime-config/auth.json`입니다. 비밀번호는 PBKDF2 해시로 저장되고, 로그인 세션은 HttpOnly 쿠키로 관리됩니다.

관리자 로그인:

```bash
curl -c cookies.txt -X POST "$BASE_URL/playground/api/auth/login" \
  -H "content-type: application/json" \
  -d '{"username":"admin","password":"<admin-password>"}'
```

조회:

```bash
curl -b cookies.txt "$BASE_URL/playground/api/admin/runtime-settings"
curl -b cookies.txt "$BASE_URL/api/v1/runtime-settings"
```

저장:

```bash
curl -b cookies.txt -X PUT "$BASE_URL/playground/api/admin/runtime-settings" \
  -H "content-type: application/json" \
  -d '{
    "values": {
      "ocr_service_timeout_sec": 300,
      "playground_default_max_pages": 20,
      "playground_max_upload_mb": 1024,
      "target_api_base_url": "http://target-server:8000/news",
      "target_api_timeout_sec": 60
    }
  }'
```

자주 쓰는 키:

| Key | 적용 | 설명 |
| --- | --- | --- |
| `ocr_service_url` | 새 요청 | app/playground가 호출할 OCR API upstream 주소 |
| `ocr_service_mode` | 새 요청 | `native` 또는 `datalab_marker` 호환 호출 방식 |
| `ocr_service_timeout_sec` | 즉시/새 요청 | OCR API 또는 원격 OCR worker 응답 대기 시간 |
| `ocr_max_concurrent_requests` | 새 요청 | OCR 모델 진입 동시성 gate |
| `pdf_render_dpi` | 새 요청 | PDF를 페이지 이미지로 렌더링하는 DPI |
| `chandra_prompt_type` | 새 요청 | Chandra OCR prompt type |
| `chandra_batch_size` | 새 요청 | HF/local runner batch size |
| `playground_default_max_pages` | 즉시 | 체험 UI 기본 최대 쪽수 |
| `playground_max_upload_mb` | 즉시 | 체험 UI 업로드 제한 |
| `playground_upstream_base_url` | 새 요청 | 분리된 playground proxy가 호출할 OCR API 주소 |
| `llm_base_url` | 새 요청 | 국회 기사 후처리 LLM base URL, 비우면 휴리스틱 |
| `llm_model` | 새 요청 | 국회 기사 후처리 LLM 모델명 |
| `llm_timeout_sec` | 새 요청 | 국회 기사 후처리 LLM timeout |
| `target_api_base_url` | 즉시/새 전송 | 국회 OCR 결과 전송 대상 base URL |
| `target_api_timeout_sec` | 즉시/새 전송 | 국회 API 전송 timeout |
| `callback_timeout_seconds` | 즉시/새 callback | 작업 완료 callback timeout |
| `watch_poll_interval_sec` | 다음 loop | 국회 OCR 감시 폴더 polling 간격 |
| `watch_stable_scan_count` | 다음 scan | 파일 안정화 확인 횟수 |
| `vllm_api_base` | 새 요청 | OCR 서비스가 호출할 vLLM base URL |
| `vllm_model_name` | vLLM 재시작 | vLLM served-model-name |
| `vllm_max_retries` | 새 요청 | vLLM 일시 실패 재시도 횟수 |
| `vllm_model_path` | vLLM 재시작 | vLLM이 로드할 모델 경로 |
| `vllm_max_num_seqs` | vLLM 재시작 | vLLM 동시 sequence 수 |
| `vllm_max_model_len` | vLLM 재시작 | vLLM 실행 인자 |
| `vllm_gpu_memory_utilization` | vLLM 재시작 | vLLM 실행 인자 |
| `vllm_mm_processor_kwargs` | vLLM 재시작 | 이미지 pixel limit 등 multi-modal processor 인자 |

`restart_required=true`로 표시되는 값은 파일에 저장은 되지만 이미 떠 있는 vLLM 프로세스의 실행 인자는 바뀌지 않습니다. 이 경우 설정 저장 후 vLLM Deployment/컨테이너만 재시작하면 됩니다. vLLM entrypoint가 `/data/runtime/runtime-config/settings.json`을 읽어 실행 인자에 반영하므로 OCR API/playground 이미지 재빌드는 필요 없습니다.

## 18. 제한사항

- 중심 기능은 Chandra OCR 기반 범용 OCR/문서 변환입니다.
- `forms`, `queries`, `selection_marks`, 완전한 table recognition은 capabilities에서 `false`로 표시됩니다.
- 파일/컬렉션/템플릿/평가/배치 계열은 로컬 JSON/파일시스템 기반 구현입니다.
- Track Changes, Form Filling, Extraction Schema 등은 현재 OCR 결과와 JSON/text 기반 보조 기능입니다.
- Datalab의 custom processor 전체 버전관리, transfer/archive/restore, table recognition 전용 API는 현재 구현된 OCR 서비스 API가 아니므로 이 Guide에 endpoint로 넣지 않습니다.
