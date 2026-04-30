# External API Spec

기준 코드:

- 저장소 HEAD
- 작성 기준일: 2026-04-30

이 문서는 외부 PC에서 이 시스템을 연동할 때 필요한 API 계약을 정리한다.

- 메인 앱 API: 신문 PDF/이미지 작업 실행, 상태 조회, 결과 조회, callback 수신
- OCR 서비스 API: 이미지/PDF OCR, Datalab 호환 OCR/Marker API

## 1. 기본 접속 정보

기본 `docker-compose.yml` 기준 포트:

- 메인 앱: `http://<HOST>:18007`
- OCR 서비스: `http://<HOST>:18009`
  - `remote-ocr` 프로필을 올렸을 때만 노출

공통 사항:

- 기본 API prefix: `/api/v1`
- 현재 인증/인가 강제는 없다.
- FastAPI 기본 문서도 열려 있다.
  - 메인 앱: `/docs`, `/openapi.json`
  - OCR 서비스: `/docs`, `/openapi.json`

## 2. 메인 앱 API

외부 PC에서 직접 연동할 때 핵심 계약은 아래 2가지다.

1. 작업 등록
2. 상태 polling 또는 delivery URL로 결과 전송

### 2.1 처리 흐름

디렉터리 스캔 방식:

1. `POST /api/v1/jobs/run-daily`
2. 응답으로 `job_id` 수신
3. `GET /api/v1/jobs/{job_id}` 또는 `GET /api/v1/jobs/{job_id}/detail` polling
4. 완료 후 `GET /api/v1/jobs/{job_id}/result` 수신
5. `callback_url`을 넣었다면 서버가 최종 기사들을 `/news` 형식으로 별도 POST

단일 파일 업로드 방식:

1. `POST /api/v1/jobs/run-single?file_name=...`
2. raw PDF 또는 이미지 body 전송
3. 이후 흐름은 동일

### 2.2 엔드포인트 요약

| Method | Path | 용도 |
| --- | --- | --- |
| `GET` | `/api/v1/health` | 메인 런타임 liveness |
| `GET` | `/api/v1/ready` | DB/경로/전송 설정 readiness |
| `POST` | `/api/v1/jobs/run-daily` | 디렉터리 기반 작업 등록 |
| `POST` | `/api/v1/jobs/run-single` | 단일 PDF/이미지 raw 업로드 작업 등록 |
| `GET` | `/api/v1/jobs/{job_id}` | 간단 상태 조회 |
| `GET` | `/api/v1/jobs/{job_id}/detail` | 상세 진행률 조회 |
| `GET` | `/api/v1/jobs/{job_id}/result` | 최종 결과 조회 |
| `GET` | `/api/v1/jobs/{job_id}/news-payload` | 국회 `/news` 전송 payload 사전검증 |
| `POST` | `/api/v1/jobs/{job_id}/deliver` | 작업 전체 국회 `/news` 재전송 |
| `GET` | `/api/v1/jobs/{job_id}/pages/{page_id}/preview` | 페이지 overlay/기사 preview |
| `GET` | `/api/v1/jobs/{job_id}/pages/{page_id}/image` | 원본 페이지 이미지 |
| `GET` | `/api/v1/jobs/{job_id}/article-images/{image_id}` | 기사 이미지 crop |

### 2.3 `GET /api/v1/health`

응답:

```json
{
  "status": "ok"
}
```

### 2.4 `POST /api/v1/jobs/run-daily`

디렉터리 내 PDF/PNG/JPG/WEBP 입력 파일을 찾아 작업을 큐에 넣는다.

Request body:

```json
{
  "source_dir": "C:/shared/news_pdfs",
  "date": "2026-04-07",
  "callback_url": "http://other-pc:9000/callback/news-ocr",
  "force_reprocess": false
}
```

필드:

- `source_dir`: 선택. 서버가 접근 가능한 디렉터리 경로. 비우면 서버 기본 `INPUT_ROOT` 사용.
- `date`: 선택. `YYYY-MM-DD`. 현재 코드에서는 스캔 필터가 아니라 작업 메타데이터로만 저장된다.
- `callback_url`: 선택. 작업 종료 시 `/news` endpoint로 multipart 기사 payload를 POST할 URL.
- `force_reprocess`: 선택. 기본 `false`. `false`면 기존 완료 파일과 동일 hash는 `duplicate_hash`로 skip될 수 있다.

성공 응답:

- `202 Accepted`

```json
{
  "job_id": "job_20260407_091530",
  "status": "queued"
}
```

주의:

- 이 API는 등록 시점에 `source_dir` 존재 여부를 바로 검증하지 않는다.
- 실제 실행 단계에서 경로가 없으면 job 최종 상태가 `failed`가 된다.

예시:

```bash
curl -X POST "http://<HOST>:18007/api/v1/jobs/run-daily" \
  -H "Content-Type: application/json" \
  -d '{
    "source_dir": "C:/shared/news_pdfs",
    "callback_url": "http://other-pc:9000/callback/news-ocr",
    "force_reprocess": false
  }'
```

### 2.5 `POST /api/v1/jobs/run-single`

PDF 또는 이미지 1개를 HTTP body로 직접 업로드해서 작업을 큐에 넣는다. 암호화 PDF 때문에 서버 렌더링이 막히는 경우, 클라이언트에서 페이지를 이미지로 변환한 뒤 이 API에 넣으면 같은 기사 분할/국회 전송 파이프라인을 탄다.

Query params:

- `file_name`: 필수. `.pdf`, `.png`, `.jpg`, `.jpeg`, `.webp` 확장자 필요
- `force_reprocess`: 선택. 기본 `true`
- `ocr_mode`: 선택. `fast`, `balanced`, `accurate`
- `page_range`: 선택. 예: `0,2-4`
- `max_pages`: 선택. 최대 처리 페이지 수
- `output_format`: 선택. 예: `markdown`, `json,markdown,html,chunks`
- `paginate`, `add_block_ids`, `include_markdown_in_chunks`, `skip_cache`: 선택. Datalab-style marker 옵션

요청 본문:

- `multipart/form-data`가 아니라 raw binary body
- 본문 전체가 PDF 또는 이미지 bytes여야 한다
- 현재 `callback_url` 파라미터는 지원하지 않는다. 이 경로는 polling 기반으로만 결과 회수 가능하다.

성공 응답:

- `202 Accepted`

```json
{
  "job_id": "job_20260407_091700",
  "status": "queued"
}
```

주요 오류:

- `400`: `file_name` 없음
- `400`: 지원하지 않는 확장자
- `400`: 빈 body
- `400`: 본문 첫 1024 bytes 안에 PDF header가 없음
- `400`: 이미지 파일 검증 실패
- `413`: 업로드가 512 MiB를 초과함

예시:

```bash
curl -X POST "http://<HOST>:18007/api/v1/jobs/run-single?file_name=0213-seoggan.pdf&force_reprocess=true" \
  -H "Content-Type: application/pdf" \
  --data-binary "@0213-seoggan.pdf"
```

암호화 PDF를 페이지 이미지로 우회하는 예:

```bash
curl -X POST "http://<HOST>:18007/api/v1/jobs/run-single?file_name=0213-seoggan-page-001.png&force_reprocess=true" \
  -H "Content-Type: image/png" \
  --data-binary "@0213-seoggan-page-001.png"
```

### 2.6 `GET /api/v1/jobs/{job_id}`

가벼운 상태 조회용.

응답:

```json
{
  "job_id": "job_20260407_091530",
  "status": "running",
  "total_pdfs": 12,
  "processed_pdfs": 5,
  "total_articles": 37
}
```

### 2.7 `GET /api/v1/jobs/{job_id}/detail`

운영 화면이나 외부 모니터링에 적합한 상세 상태.

응답 주요 필드:

- `job_id`, `status`
- `source_dir`
- `requested_date`, `requested_at`, `started_at`, `finished_at`
- `total_pdfs`, `processed_pdfs`, `success_pdfs`, `failed_pdfs`, `total_articles`
- `progress_percent`
- `stages[]`
- `pdf_files[]`
- `recent_logs[]`

응답 예시:

```json
{
  "job_id": "job_20260407_091530",
  "status": "running",
  "source_dir": "C:/shared/news_pdfs",
  "requested_date": null,
  "requested_at": "2026-04-07T00:15:30.000000Z",
  "started_at": "2026-04-07T00:15:31.000000Z",
  "finished_at": null,
  "total_pdfs": 12,
  "processed_pdfs": 5,
  "success_pdfs": 5,
  "failed_pdfs": 0,
  "total_articles": 37,
  "progress_percent": 41.7,
  "stages": [
    {
      "stage_key": "scan",
      "label": "PDF 탐색 / 해시",
      "status": "completed",
      "message": "discovered=12",
      "updated_at": "2026-04-07T00:15:32.000000Z"
    }
  ],
  "pdf_files": [
    {
      "pdf_file_id": 11,
      "file_name": "0213-seoggan.pdf",
      "status": "completed",
      "page_count": 12,
      "parsed_pages": 12,
      "failed_pages": 0,
      "article_count": 41,
      "skip_reason": null,
      "processed_at": "2026-04-07T00:17:08.000000Z",
      "pages": [
        {
          "page_id": 201,
          "page_number": 1,
          "status": "parsed",
          "article_count": 4
        }
      ]
    }
  ],
  "recent_logs": []
}
```

### 2.8 `GET /api/v1/jobs/{job_id}/result`

최종 기사 결과를 반환한다.

응답 주요 구조:

- `job_id`
- `status`
- `files[]`
  - `pdf_file`
  - `pages`
  - `articles[]`
    - `article_id`, `page_number`, `article_order`
    - `title`, `body_text`
    - `original_title`, `original_body_text`
    - `corrected_title`, `corrected_body_text`
    - `correction_source`, `correction_model`
    - `title_bbox`, `article_bbox`
    - `relevance_score`, `relevance_reason`, `relevance_label`, `relevance_model`, `relevance_source`
    - `source_metadata`
    - `delivery_status`, `delivery_response_code`, `delivery_last_error`, `delivery_updated_at`, `delivery_request_available`
    - `images[]`
    - `bundle_dir`, `markdown_path`, `metadata_path`

응답 예시:

```json
{
  "job_id": "job_20260407_091530",
  "status": "completed",
  "files": [
    {
      "pdf_file": "0213-seoggan.pdf",
      "pages": 12,
      "articles": [
        {
          "article_id": 501,
          "page_number": 1,
          "article_order": 1,
          "title": "국방 일일 브리핑",
          "body_text": "훈련 결과와 장비 점검 내용을 정리했다.",
          "original_title": "국방 일일 브리핑",
          "original_body_text": "훈련 결과와 장비 점검 내용을 정리했다.",
          "corrected_title": null,
          "corrected_body_text": null,
          "correction_source": null,
          "correction_model": null,
          "title_bbox": [40, 40, 320, 92],
          "article_bbox": [40, 40, 620, 320],
          "relevance_score": 0.93,
          "relevance_reason": "국방 관련 키워드 다수",
          "relevance_label": "relevant",
          "relevance_model": "gpt-oss-20b",
          "relevance_source": "llm",
          "delivery_status": "delivered",
          "delivery_response_code": 201,
          "delivery_last_error": null,
          "delivery_updated_at": "2026-04-07T00:18:10+00:00",
          "delivery_request_available": true,
          "source_metadata": {
            "publication": "국방일보",
            "issue_date": "2026-04-07",
            "issue_date_text": "2026.04.07",
            "issue_weekday": "화",
            "issue_page": "1",
            "issue_page_label": "1면",
            "issue_section": null,
            "raw_publication_text": null,
            "raw_issue_text": null,
            "publication_bbox": null,
            "issue_bbox": null
          },
          "images": [
            {
              "image_id": 812,
              "image_path": "/data/runtime/output/job_20260407_091530/0213-seoggan/parsed/page_0001/article_01/images/image_01.png",
              "bbox": [360, 80, 620, 260],
              "captions": [
                {
                  "text": "훈련 장면",
                  "bbox": [360, 262, 620, 292],
                  "confidence": 0.88
                }
              ]
            }
          ],
          "bundle_dir": "/data/runtime/output/job_20260407_091530/0213-seoggan/parsed/page_0001/article_01",
          "markdown_path": "/data/runtime/output/job_20260407_091530/0213-seoggan/parsed/page_0001/article_01/article.md",
          "metadata_path": "/data/runtime/output/job_20260407_091530/0213-seoggan/parsed/page_0001/article_01/article.json"
        }
      ]
    }
  ]
}
```

### 2.9 `GET /api/v1/jobs/{job_id}/pages/{page_id}/preview`

페이지 이미지 위에 기사/레이아웃 bbox overlay 정보를 돌려준다.

Query param:

- `overlay`: 선택. `merged`, `vl`, `structure`, `fallback`

응답 주요 필드:

- `page_id`, `pdf_file`, `page_number`
- `parse_status`
- `width`, `height`
- `image_url`
- `overlay_type`
- `regions[]`
- `articles[]`
- `raw_payload`

예시:

```bash
curl "http://<HOST>:18007/api/v1/jobs/job_20260407_091530/pages/201/preview?overlay=merged"
```

### 2.10 이미지 조회

페이지 원본:

- `GET /api/v1/jobs/{job_id}/pages/{page_id}/image`

기사 이미지 crop:

- `GET /api/v1/jobs/{job_id}/article-images/{image_id}`

응답은 JSON이 아니라 이미지 바이너리다.

### 2.11 `GET /api/v1/jobs/{job_id}/news-payload`

실제 국회 `/news` API로 전송하기 전에 서버가 구성한 payload를 미리 확인한다. 외부 전송은 수행하지 않는다.

응답 주요 필드:

- `target_url`, `target_configured`
- `article_count`
- `included_image_count`, `skipped_image_count`
- `articles[].request_article`: 실제 `body` 배열에 들어갈 기사 객체
- `articles[].images[]`: 이미지 포함 여부, 파일 크기, 누락 사유
- `body`: multipart form field `body`에 들어갈 JSON 배열

예시:

```bash
curl "http://<HOST>:18007/api/v1/jobs/job_20260407_091530/news-payload"
```

### 2.12 `POST /api/v1/jobs/{job_id}/deliver`

작업 전체 기사 payload를 현재 `TARGET_API_BASE_URL` 기준 국회 `/news` API로 다시 전송한다.

성공 응답:

```json
{
  "job_id": "job_20260407_091530",
  "target_url": "http://target.example/news",
  "delivered": 12,
  "failed": 0,
  "skipped": 0
}
```

주요 오류:

- `404`: job 없음
- `409`: `TARGET_API_BASE_URL` 미설정 또는 전송 가능한 기사 없음
- `502`: 대상 API 호출 실패

### 2.13 delivery 계약

`run-daily`에서 `callback_url`을 넣으면, 작업 종료 시 서버가 아래를 수행한다.

- Method: `POST`
- Content-Type: `multipart/form-data`
- Form field `body`: 기사 배열 JSON 문자열
- File parts: `body[*].imgs[*].src` 가 가리키는 이미지 파일 part

`body` JSON 예시:

```json
[
  {
    "title": "국방 일일 브리핑",
    "body_text": "훈련 결과와 장비 점검 내용을 정리했다.",
    "imgs": [
      {
        "caption": "훈련 장면",
        "src": "file_0_0"
      }
    ],
    "relevance_score": 0.93,
    "publication": "국방일보",
    "issue_date": "2026-04-07"
  }
]
```

중요 동작:

- 완료된 기사만 전송한다. 기사 0건이면 전송하지 않는다.
- 앱은 문자열 길이, score 범위, issue_date 형식을 서버 제약에 맞게 정규화한 뒤 전송한다.
- 이미지 없음, issue_date 없음, 실제 이미지 파일 누락 같은 기사 단위 실패는 `delivery.json` 또는 `demo_delivery.json` 으로 남긴다.
- batch 전송이 `400`이면 기사 단위로 다시 보내서 어느 기사가 실패했는지 분리한다.

외부 PC는 최소한 아래를 처리하는 것이 안전하다.

- `201` 또는 `2xx` 응답
- validation 실패 시 `error_code`, `index`, `child_index`

### 2.14 메인 앱 상태값

`job.status`:

- `queued`
- `running`
- `completed`
- `completed_with_errors`
- `failed`

`pdf_files[].status`:

- `queued`
- `running`
- `completed`
- `completed_with_errors`
- `failed`
- `skipped`

`pages[].status`:

- `queued`
- `running`
- `parsed`
- `failed`

`stages[].stage_key`:

- `scan`
- `render`
- `ocr_vl`
- `ocr_structure`
- `ocr_fallback`
- `ocr_retry`
- `cluster`
- `relevance`
- `crop`
- `persist`

## 3. OCR 서비스 API

OCR 서비스는 두 층으로 나뉜다.

1. native OCR API
2. Datalab 호환 async API

메인 앱이 직접 쓰는 것은 아래 두 경우다.

- `OCR_SERVICE_MODE=native`: `/api/v1/ocr/image`
- `OCR_SERVICE_MODE=datalab_marker`: `/api/v1/marker` 제출 후 polling

### 3.1 OCR 서비스 요약

| Method | Path | 용도 |
| --- | --- | --- |
| `GET` | `/health` | health |
| `GET` | `/api/health` | health alias |
| `GET` | `/api/v1/health` | health alias |
| `POST` | `/api/v1/ocr/image` | 이미지 1장 synchronous OCR |
| `POST` | `/api/v1/ocr/pdf` | PDF 전체 synchronous OCR |
| `POST` | `/api/v1/ocr` | Datalab-style async OCR 제출 |
| `GET` | `/api/v1/ocr/{request_id}` | async OCR 결과 조회 |
| `POST` | `/api/v1/marker` | Datalab-style async Marker 제출 |
| `GET` | `/api/v1/marker/{request_id}` | async Marker 결과 조회 |
| `GET` | `/api/v1/thumbnails/{lookup_key}` | request 결과 썸네일 |

### 3.2 `POST /api/v1/ocr/image`

용도:

- 이미지 1장을 바로 OCR
- 메인 앱이 remote OCR native 모드에서 호출하는 핵심 엔드포인트

Request:

- `multipart/form-data`
- 필드:
  - `file`: 필수
  - `page_number`: 선택, 기본 `1`
  - `width`: 선택
  - `height`: 선택

성공 응답:

```json
{
  "page_number": 1,
  "width": 640,
  "height": 480,
  "image_path": "page.png",
  "blocks": [
    {
      "block_id": "title-1",
      "page_number": 1,
      "label": "title",
      "bbox": [40, 40, 320, 92],
      "text": "국방 일일 브리핑",
      "confidence": 0.98,
      "metadata": {}
    }
  ],
  "raw_vl": {},
  "raw_structure": {},
  "raw_fallback_ocr": {}
}
```

주요 오류:

- `400`: `page_number <= 0`
- `400`: 빈 이미지 업로드
- `400`: width/height 해상도 계산 실패

예시:

```bash
curl -X POST "http://<HOST>:18009/api/v1/ocr/image" \
  -F "file=@page.png" \
  -F "page_number=1"
```

### 3.3 `POST /api/v1/ocr/pdf`

용도:

- PDF 전체를 렌더링한 뒤 모든 페이지를 synchronous OCR

Request:

- `multipart/form-data`
- 필드:
  - `file`: 필수, `.pdf`
  - `dpi`: 선택, 기본 `300`

응답:

```json
{
  "page_count": 2,
  "pdf_name": "report.pdf",
  "pages": [
    {
      "page_number": 1,
      "width": 2480,
      "height": 3508,
      "image_path": "page_0001.png",
      "blocks": [],
      "raw_vl": {},
      "raw_structure": {},
      "raw_fallback_ocr": {}
    }
  ]
}
```

### 3.4 `POST /api/v1/ocr`

Datalab 스타일 async OCR 제출 API.

Request:

- `multipart/form-data`
- 파일 필드:
  - `file`
  - 또는 `file.0`
- 추가 form fields:
  - `page_number` 기본 `1`
  - `width`, `height`
  - `dpi` 기본 `300`
  - `max_pages`
  - `page_range`

`page_range` 규칙:

- PDF 기준 zero-based page index
- 예: `0`, `0-2`, `0,2,4-5`

제출 응답:

```json
{
  "request_id": "4b4c6f0d5c5345cfb0cfa2f2d7c4d6b8",
  "request_check_url": "http://<HOST>:18009/api/v1/ocr/4b4c6f0d5c5345cfb0cfa2f2d7c4d6b8",
  "success": true,
  "error": null,
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  }
}
```

결과 조회:

- `GET /api/v1/ocr/{request_id}`

완료 응답 예시:

```json
{
  "status": "complete",
  "pages": [
    {
      "page_number": 1,
      "width": 640,
      "height": 480,
      "text": "국방 일일 브리핑\n훈련 결과와 장비 점검 내용을 정리했다.",
      "lines": [
        {
          "text": "국방 일일 브리핑",
          "bbox": [40, 40, 320, 92],
          "label": "title",
          "confidence": 0.98
        }
      ],
      "blocks": [],
      "raw_vl": {},
      "raw_structure": {},
      "raw_fallback_ocr": {}
    }
  ],
  "success": true,
  "error": null,
  "page_count": 1,
  "total_cost": 0,
  "cost_breakdown": {
    "credits": 0
  },
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  }
}
```

### 3.5 `POST /api/v1/marker`

Datalab 스타일 async Marker 제출 API.

Request:

- `multipart/form-data`
- 파일 필드:
  - `file`
  - 또는 `file.0`
  - 또는 `file_url`
- 추가 form fields:
  - `page_number`
  - `width`, `height`
  - `dpi`
  - `max_pages`
  - `page_range`
  - `mode`: `fast`, `balanced`, `accurate` (`balanced` 기본)
  - `output_format`: `json`, `markdown`, `html`, `chunks` 또는 `markdown,html` 같은 복수 포맷
  - `paginate`
  - `add_block_ids`
  - `include_markdown_in_chunks`
  - `skip_cache`
  - `extras`, `additional_config`

제출 응답 형식은 `/api/v1/ocr`와 동일하다.

결과 조회:

- `GET /api/v1/marker/{request_id}`

완료 응답 주요 필드:

- `status`
- `success`
- `output_format`
- `output_formats`
- `markdown`
- `html`
- `json`
  - `request_id`
  - `file_name`
  - `page_count`
  - `pages[]`
    - `page_number`, `width`, `height`, `text`
    - `blocks[]`
    - `articles[]`
    - `unassigned[]`
- `chunks`
- `parse_quality_score`
- `metadata`
- `checkpoint_id`

예시:

```json
{
  "status": "complete",
  "success": true,
  "error": null,
  "page_count": 1,
  "output_format": "json",
  "markdown": "# Page 1\n\n## 국방 일일 브리핑\n\n훈련 결과와 장비 점검 내용을 정리했다.",
  "html": "<html><body>...</body></html>",
  "json": {
    "request_id": "abcd1234",
    "file_name": "page.png",
    "page_count": 1,
    "pages": [
      {
        "page_number": 1,
        "width": 640,
        "height": 480,
        "text": "국방 일일 브리핑\n훈련 결과와 장비 점검 내용을 정리했다.",
        "blocks": [],
        "articles": [
          {
            "title": "국방 일일 브리핑",
            "body_text": "훈련 결과와 장비 점검 내용을 정리했다.",
            "title_bbox": [40, 40, 320, 92],
            "article_bbox": [40, 40, 620, 320],
            "confidence": 0.98,
            "layout_type": "article",
            "images": []
          }
        ],
        "unassigned": []
      }
    ]
  },
  "chunks": [],
  "checkpoint_id": "abcd1234",
  "total_cost": 0,
  "cost_breakdown": {
    "credits": 0
  },
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  }
}
```

### 3.6 `GET /api/v1/thumbnails/{lookup_key}`

기존 OCR/Marker request 결과에서 썸네일을 생성한다.

Query params:

- `page_range`
- `thumb_width` 기본 `300`

응답:

```json
{
  "thumbnails": ["<base64-jpeg>"],
  "success": true,
  "error": null
}
```

## 4. 메인 앱이 OCR 서비스를 호출하는 실제 계약

### 4.1 native 모드

메인 앱은 `OCR_SERVICE_URL`을 기준으로 아래 endpoint를 만든다.

- `http://host:18009` 입력 시 내부 호출 경로: `/api/v1/ocr/image`
- `http://host:18009/api/v1` 입력 시 내부 호출 경로: `/ocr/image`
- `http://host:18009/api/v1/ocr/image` 입력 시 그대로 사용

요청 형식:

- multipart
- `file`
- `page_number`
- `width`
- `height`

### 4.2 datalab_marker 모드

메인 앱은 아래 endpoint를 사용한다.

- `/api/v1/marker`

요청 형식:

- multipart
- `file`
- `output_format=json`
- `mode=<OCR_SERVICE_MARKER_MODE>`
- `additional_config={"keep_pageheader_in_output": true, "keep_pagefooter_in_output": true}`

응답에서 `request_check_url`을 받은 뒤 polling 한다.

## 5. OCR 서비스 확장 API 인벤토리

OCR 서비스는 OCR 외에도 아래 route group을 제공한다.

### 5.1 Files

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

### 5.2 Documents / Convert / Segment / Extract

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

### 5.3 Templates / Collections / Batch / Eval / Workflow

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

이 확장 API의 운영 예시는 기존 [defense_network_ocr_guide.md](./defense_network_ocr_guide.md)를 참고하면 된다.

## 6. 외부 연동 시 권장안

다른 PC에서 메인 앱만 붙을 경우:

1. 폴더 스캔이면 `run-daily`
2. 파일 전송이면 `run-single`
3. 결과 회수는 `callback_url` delivery + `result` polling fallback

다른 PC에서 OCR 서비스도 직접 붙을 경우:

1. 단순 OCR은 `/api/v1/ocr/image` 또는 `/api/v1/ocr/pdf`
2. 비동기 계약이 필요하면 `/api/v1/ocr`
3. 기사/블록/markdown/html까지 필요하면 `/api/v1/marker`

## 7. 코드상 주의사항

- `run-daily.date`는 현재 파일 선택 필터가 아니다.
- `run-single`은 raw PDF/이미지 body API다.
- delivery는 자동 재시도하지 않는다.
- 현재 인증/권한 체크가 없으므로 외부망 노출 시 별도 reverse proxy 또는 네트워크 통제가 필요하다.
