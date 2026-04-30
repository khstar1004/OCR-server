# Datalab Compatibility API

국방망용 범용 OCR 서비스로 확장하기 위해 `ocr_service`에 Datalab 스타일 호환 레이어를 추가했다.

핵심 원칙:

- 기존 `/api/v1/ocr/image`, `/api/v1/ocr/pdf`는 유지한다.
- 새 호환 API는 `request_id` 기반 제출/조회 패턴을 제공한다.
- 실제로 동작하는 기능만 구현한다.
- 아직 없는 기능은 조용히 흉내내지 않고 문서에서 명시한다.

## 현재 지원 엔드포인트

- `GET /health`
- `GET /api/health`
- `GET /api/v1/health`
- `GET /api/v1/capabilities`
- `POST /api/v1/ocr`
- `GET /api/v1/ocr/{request_id}`
- `POST /api/v1/marker`
- `GET /api/v1/marker/{request_id}`
- `DELETE /api/v1/requests`
- `GET /api/v1/thumbnails/{lookup_key}`
- `GET /api/v1/workflows/step_types`
- `GET /api/v1/workflows/workflows`
- `POST /api/v1/workflows/workflows`
- `GET /api/v1/workflows/workflows/{workflow_id}`
- `DELETE /api/v1/workflows/workflows/{workflow_id}`
- `POST /api/v1/workflows/workflows/{workflow_id}/execute`
- `GET /api/v1/workflows/executions/{execution_id}`
- `POST /api/v1/files`
- `POST /api/v1/files/request_upload_url`
- `PUT /api/v1/files/uploads/{upload_id}`
- `GET /api/v1/files/uploads/{upload_id}/confirm`
- `GET /api/v1/files`
- `GET /api/v1/files/{file_id}/metadata`
- `GET /api/v1/files/{file_id}/download_url`
- `DELETE /api/v1/files/{file_id}`
- `POST /api/v1/create_document`
- `GET /api/v1/create_document/{request_id}`
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
- `GET/POST /api/v1/templates...`
- `GET/POST /api/v1/collections...`
- `GET/POST /api/v1/batch_runs...`
- `GET/POST /api/v1/eval_rubrics...`
- `GET /api/v1/check_pipeline_access`
- `GET /api/v1/custom_pipelines`

## 현재 워크플로우 step_key

- `ocr`
- `marker_parse`
- `convert_document` 연계는 batch/document layer에서 지원
- `extract_structured_data` 연계는 schema/template layer에서 지원

## 구현 메모

- `ocr` 결과는 페이지별 `lines`, `blocks`, raw OCR payload를 반환한다.
- `marker` 결과는 페이지별 `blocks`, `articles`, `markdown`, `html`, `json`, `chunks`를 함께 반환한다.
- `marker` 제출은 Datalab Marker의 핵심 form 옵션인 `mode`, `max_pages`, `page_range`, 복수 `output_format`, `paginate`, `add_block_ids`, `include_markdown_in_chunks`, `skip_cache`, `file_url`을 받는다.
- `extras`, `additional_config`는 호환 요청을 깨지 않도록 수신하고 결과 `metadata`에 기록하지만, 현 단계에서는 별도 후처리 엔진을 실행하지 않는다.
- 완료/실패 결과에는 `runtime` 객체를 넣어 `request_id`, 종류, 파일명, 파일 크기, 시작/종료 시각, 처리 시간, 페이지 수, 오류 코드를 추적한다.
- 오래된 요청 보관함은 `DELETE /api/v1/requests?older_than_hours=24&status_filter=complete&dry_run=true`로 먼저 후보를 확인한 뒤 `dry_run=false`로 삭제한다.
- `thumbnails`는 기존 request 결과에서 생성된 페이지 이미지를 다시 썸네일로 변환한다.
- workflow 실행 입력은 현재 `input_config.file_url` 또는 `input_config.file_urls`만 지원한다.
- `file_url`은 로컬 절대경로, `file://`, `http(s)://`를 허용한다.
- file/template/collection/batch/eval_rubric은 모두 로컬 JSON/파일시스템 기반 저장소를 사용한다.
- structured extraction은 현재 rule-first 방식이며 field `label`, `pattern`을 줄수록 정확도가 올라간다.

## 제한사항

아래 기능은 상용 Datalab API의 전체 동작을 복제하지 않고, 현재 Chandra OCR/국회 기사 처리 파이프라인에 맞춘 로컬 구현으로 제공한다.

- Generate Extraction Schemas, Form Filling, Track Changes는 JSON/text 기반 보조 기능이다.
- Create/Convert/Segment/Extract/Score는 파일 저장소와 현재 OCR 결과를 기준으로 동작한다.
- Template/File/Eval Rubric/Collection/Batch Run 계열은 로컬 JSON/파일시스템 저장소를 사용한다.
- custom pipeline 실행기는 아직 범용 DAG 엔진이 아니며, 현재 등록된 workflow step 중심으로 제한된다.

## 권장 확장 순서

1. `runtime`/`metadata`를 운영 로그와 연결해 처리시간, 실패율, 페이지당 비용을 대시보드화
2. 국회 기사 전송 payload의 사전검증 결과를 운영 승인 단계와 연결
3. 파일 업로드/참조 체계(`datalab://file-...`)를 외부 client 계약으로 고정
4. custom pipeline/template/eval_rubric을 workflow 정의 저장소 위에 확장
