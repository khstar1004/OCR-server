# Defense Network OCR Guide

이 문서는 국방망 내부에서 `a-cong OCR Service`를 범용 문서 OCR/변환/추출 서버로 사용할 때의 운영 기준을 정리한다.

기준 시점:

- 코드 기준: 현재 저장소 HEAD
- 서버 기준: `app/ocr_service.py`

## 서비스가 제공하는 것

현재 서버는 아래 기능을 제공한다.

- OCR: 이미지/PDF를 Chandra OCR로 처리
- Marker 호환 변환: block/article/markdown/html/json/chunks 반환
- 파일 저장소: 업로드 슬롯, 업로드 확인, 파일 목록/메타데이터/다운로드
- 문서 등록: 파일을 문서 단위 리소스로 등록
- 문서 변환: 문서를 Marker 스타일 결과로 변환
- 문서 세그먼트: 페이지 block, 기사/article 후보 반환
- 구조화 추출: schema/template 기반 rule-first extraction
- 스키마 생성: example JSON 또는 field list에서 schema 생성
- 폼 채우기: `{{ field }}` 치환
- 변경 추적: before/after diff
- 템플릿 관리: CRUD, clone, 예제 추가/삭제/썸네일
- 컬렉션 관리: 파일 묶음 구성
- 배치 실행: 컬렉션 단위 변환/세그먼트/추출
- 평가 루브릭: CRUD, extraction scoring에 사용
- 워크플로우: step type 조회, workflow CRUD, 실행, 상태 조회

## 현재 적합한 문서 유형

- 신문/브리핑 자료
- 보고서 PDF
- 공문/회의자료 이미지
- 구조가 비교적 일정한 양식 문서

## 현재 한계

- OCR 백엔드는 `Chandra`만 사용
- 구조화 추출은 rule-first 방식이라 LLM 기반 자유 추출보다 보수적
- 템플릿/배치/루브릭은 로컬 JSON 저장 방식
- 권한/계정/RBAC는 아직 없음
- `custom pipelines`, `template marketplace`, `feedback-driven rubric generation`은 아직 경량 수준

## 기본 운영 경로

### 1. 파일 업로드

가장 단순한 방식:

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/files `
  -F "file=@report.pdf"
```

업로드 슬롯 방식:

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/files/request_upload_url `
  -H "Content-Type: application/json" `
  -d "{\"file_name\":\"report.pdf\",\"content_type\":\"application/pdf\"}"
```

응답의 `upload_url` 로 raw body 업로드 후 `confirm_url` 호출:

```powershell
curl.exe -X PUT "<upload_url>" `
  --data-binary "@report.pdf" `
  -H "Content-Type: application/pdf"

curl.exe "<confirm_url>"
```

### 2. 문서 등록

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/create_document `
  -H "Content-Type: application/json" `
  -d "{\"file_id\":\"file_xxx\"}"
```

결과 조회:

```powershell
curl.exe http://127.0.0.1:18009/api/v1/create_document/<request_id>
```

### 3. 문서 변환

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/convert_document `
  -H "Content-Type: application/json" `
  -d "{\"document_id\":\"doc_xxx\",\"output_format\":\"json\"}"
```

결과 조회:

```powershell
curl.exe http://127.0.0.1:18009/api/v1/convert_document/<request_id>
```

### 4. 문서 세그먼트

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/segment_document `
  -H "Content-Type: application/json" `
  -d "{\"document_id\":\"doc_xxx\"}"
```

응답에는 페이지별 `blocks`, `articles`, `full_text`가 포함된다.

### 5. 구조화 추출

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/extract_structured_data `
  -H "Content-Type: application/json" `
  -d "{
    \"document_id\":\"doc_xxx\",
    \"schema\":{
      \"name\":\"daily_report\",
      \"fields\":[
        {\"name\":\"title\",\"label\":\"제목\",\"type\":\"string\"},
        {\"name\":\"document_date\",\"label\":\"문서일자\",\"type\":\"date\"},
        {\"name\":\"summary\",\"label\":\"요약\",\"type\":\"string\"}
      ]
    }
  }"
```

### 6. 배치 실행

1. 파일들을 collection에 묶는다.
2. batch run을 시작한다.

```powershell
curl.exe -X POST http://127.0.0.1:18009/api/v1/batch_runs `
  -H "Content-Type: application/json" `
  -d "{
    \"collection_id\":1,
    \"operation\":\"extract_structured_data\",
    \"params\":{
      \"schema\":{
        \"name\":\"daily_report\",
        \"fields\":[
          {\"name\":\"title\"},
          {\"name\":\"summary\"}
        ]
      }
    }
  }"
```

## 엔드포인트 묶음

### Health

- `GET /health`
- `GET /api/health`
- `GET /api/v1/health`

### OCR / Marker

- `POST /api/v1/ocr`
- `GET /api/v1/ocr/{request_id}`
- `POST /api/v1/marker`
- `GET /api/v1/marker/{request_id}`
- `GET /api/v1/thumbnails/{lookup_key}`

### Files

- `POST /api/v1/files`
- `POST /api/v1/files/request_upload_url`
- `PUT /api/v1/files/uploads/{upload_id}`
- `GET /api/v1/files/uploads/{upload_id}/confirm`
- `GET /api/v1/files`
- `GET /api/v1/files/{file_id}`
- `GET /api/v1/files/{file_id}/metadata`
- `GET /api/v1/files/{file_id}/download_url`
- `GET /api/v1/files/{file_id}/download`
- `DELETE /api/v1/files/{file_id}`

### Documents / Convert / Segment / Extract

- `POST /api/v1/create_document`
- `GET /api/v1/create_document/{request_id}`
- `GET /api/v1/documents/{document_id}`
- `POST /api/v1/convert_document`
- `GET /api/v1/convert_document/{request_id}`
- `POST /api/v1/segment_document`
- `GET /api/v1/segment_document/{request_id}`
- `POST /api/v1/generate_extraction_schemas`
- `GET /api/v1/generate_extraction_schemas/{request_id}`
- `POST /api/v1/extract_structured_data`
- `GET /api/v1/extract_structured_data/{request_id}`
- `POST /api/v1/score_extraction_results`
- `GET /api/v1/score_extraction_results/{request_id}`
- `POST /api/v1/form_filling`
- `GET /api/v1/form_filling/{request_id}`
- `POST /api/v1/track_changes`
- `GET /api/v1/track_changes/{request_id}`

### Templates / Collections / Batch

- `GET /api/v1/templates`
- `POST /api/v1/templates/promote`
- `GET /api/v1/templates/{template_id}`
- `PUT /api/v1/templates/{template_id}`
- `DELETE /api/v1/templates/{template_id}`
- `POST /api/v1/templates/{template_id}/clone`
- `POST /api/v1/templates/{template_id}/examples`
- `GET /api/v1/templates/{template_id}/examples/{example_id}/download`
- `DELETE /api/v1/templates/{template_id}/examples/{example_id}`
- `GET /api/v1/templates/{template_id}/examples/{example_id}/thumbnail`
- `GET /api/v1/collections`
- `POST /api/v1/collections`
- `GET /api/v1/collections/{collection_id}`
- `PUT /api/v1/collections/{collection_id}`
- `DELETE /api/v1/collections/{collection_id}`
- `POST /api/v1/collections/{collection_id}/files`
- `DELETE /api/v1/collections/{collection_id}/files/{file_id}`
- `GET /api/v1/batch_runs`
- `POST /api/v1/batch_runs`
- `GET /api/v1/batch_runs/{batch_run_id}`
- `GET /api/v1/batch_runs/{batch_run_id}/results`

### Eval Rubrics / Workflow / Pipeline

- `GET /api/v1/eval_rubrics`
- `POST /api/v1/eval_rubrics`
- `GET /api/v1/eval_rubrics/{rubric_id}`
- `PUT /api/v1/eval_rubrics/{rubric_id}`
- `DELETE /api/v1/eval_rubrics/{rubric_id}`
- `GET /api/v1/workflows/step_types`
- `GET /api/v1/workflows/workflows`
- `POST /api/v1/workflows/workflows`
- `GET /api/v1/workflows/workflows/{workflow_id}`
- `DELETE /api/v1/workflows/workflows/{workflow_id}`
- `POST /api/v1/workflows/workflows/{workflow_id}/execute`
- `GET /api/v1/workflows/executions/{execution_id}`
- `GET /api/v1/check_pipeline_access`
- `GET /api/v1/custom_pipelines`

## 권장 운영 절차

### 보고서/공문 처리

1. `files`에 업로드
2. `create_document`로 문서 등록
3. `convert_document`로 기본 변환 확인
4. `segment_document`로 구조 확인
5. `extract_structured_data`로 필드 추출
6. `score_extraction_results`로 기준 데이터와 비교

### 반복 양식 처리

1. schema/template를 등록
2. template example을 연결
3. collection에 문서 묶기
4. batch run으로 일괄 추출

## 추출 정확도를 높이는 방법

- schema field에 `label`을 명시
- 가능하면 `pattern`도 같이 명시
- 문서 종류별 template를 따로 분리
- collection도 문서 유형별로 나눠 배치 실행

## 테스트 상태

현재 저장소 기준으로 전체 테스트는 통과 상태다.

```powershell
python -m pytest tests
```
