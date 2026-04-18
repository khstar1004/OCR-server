# OCR API Guide

기준 코드:

- 저장소 HEAD
- 작성 기준일: 2026-04-15
- 기준 서버: `app/ocr_service.py`
- 구현 기준: `app/services/datalab_compat.py`

이 문서는 현재 저장소에서 OCR 모델만 범용적으로 외부에 제공할 때 필요한 API 계약을 개발자 협업 기준으로 정리한다.

현재 범용 OCR 백엔드는 `Chandra OCR` 단일 모델만 지원한다.

## 1. API 개요

### 1.1 목적

이 서비스는 이미지/PDF를 입력으로 받아 OCR 결과를 반환한다.

지원 방식:

1. 동기 OCR API
2. 비동기 호환 OCR API
3. 구조화 결과용 Marker API

### 1.2 기본 접속 정보

| 항목 | 값 |
| --- | --- |
| 기본 OCR 서비스 URL | `http://<HOST>:18009` |
| 기본 API Prefix | `/api/v1` |
| 문서 URL | `/docs` |
| OpenAPI JSON | `/openapi.json` |
| 인증 | 현재 없음 |
| OCR 백엔드 | `chandra` |

참고:

- `18009` 포트는 일반적으로 `docker compose --profile remote-ocr up -d` 실행 시 노출된다.
- 외부망 직접 노출보다는 reverse proxy 또는 내부망 접근 제어를 권장한다.

### 1.3 권장 사용 패턴

| 사용 목적 | 권장 API |
| --- | --- |
| 이미지 1장 바로 OCR | `POST /api/v1/ocr/image` |
| PDF 1건 전체 바로 OCR | `POST /api/v1/ocr/pdf` |
| polling 기반 비동기 OCR | `POST /api/v1/ocr` + `GET /api/v1/ocr/{request_id}` |
| 기사형/구조화 결과 필요 | `POST /api/v1/marker` + `GET /api/v1/marker/{request_id}` |
| 결과 이미지 썸네일 확인 | `GET /api/v1/thumbnails/{lookup_key}` |

## 2. 공통 규칙

### 2.1 Content-Type

| API 유형 | Content-Type |
| --- | --- |
| Health / Result 조회 | `application/json` |
| 동기 OCR 업로드 | `multipart/form-data` |
| 비동기 OCR/Marker 제출 | `multipart/form-data` |

### 2.2 좌표 규칙

| 항목 | 설명 |
| --- | --- |
| `bbox` 형식 | `[x1, y1, x2, y2]` |
| 좌표계 기준 | 페이지 이미지 픽셀 기준 |
| page index 규칙 | `page_range`는 zero-based index |
| page_number 규칙 | 응답 `page_number`는 1-based 페이지 번호 |

### 2.3 공통 버전 정보

비동기 API 응답에는 `versions` 객체가 포함된다.

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `service` | string | 서비스 식별자. 현재 `a-cong-ocr` |
| `compat_mode` | string | 현재 `datalab-like-v1` |
| `ocr_backend` | string | 현재 `chandra` |
| `chandra_model` | string | 모델 경로 또는 모델 ID |

### 2.4 공통 상태값

| 필드 | 값 | 설명 |
| --- | --- | --- |
| `status` | `processing` | 비동기 작업 처리 중 |
| `status` | `complete` | 비동기 작업 완료 |
| `status` | `failed` | 비동기 작업 실패 |

### 2.5 공통 오류 규칙

| HTTP Status | 의미 | 예시 |
| --- | --- | --- |
| `400` | 잘못된 요청 | 파일 누락, 빈 업로드, 잘못된 `page_range` |
| `404` | 조회 대상 없음 | `request_id` 없음, `lookup_key` 없음 |
| `500` | 서버 내부 오류 | 모델 처리 실패, 렌더링 실패 |

현재 구현은 명시적 오류 응답 외에도 비동기 결과에서 `status=failed`와 `error` 필드로 실패 내용을 반환할 수 있다.

## 3. 엔드포인트 요약

| Method | Path | 동기/비동기 | 설명 |
| --- | --- | --- | --- |
| `GET` | `/health` | 동기 | 기본 health |
| `GET` | `/api/health` | 동기 | 호환 health alias |
| `GET` | `/api/v1/health` | 동기 | 호환 health alias |
| `POST` | `/api/v1/ocr/image` | 동기 | 이미지 1장 OCR |
| `POST` | `/api/v1/ocr/pdf` | 동기 | PDF 전체 OCR |
| `POST` | `/api/v1/ocr` | 비동기 | OCR 작업 제출 |
| `GET` | `/api/v1/ocr/{request_id}` | 비동기 | OCR 결과 조회 |
| `POST` | `/api/v1/marker` | 비동기 | Marker 작업 제출 |
| `GET` | `/api/v1/marker/{request_id}` | 비동기 | Marker 결과 조회 |
| `GET` | `/api/v1/thumbnails/{lookup_key}` | 동기 | OCR/Marker 결과 썸네일 |

## 4. Health API

### 4.1 지원 경로

| Method | Path | 설명 |
| --- | --- | --- |
| `GET` | `/health` | 가장 단순한 health |
| `GET` | `/api/health` | 호환 health |
| `GET` | `/api/v1/health` | 호환 health |

### 4.2 응답 스키마

기본 health:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `status` | string | 현재 `ok` |

호환 health:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `status` | string | 현재 `ok` |
| `service` | string | 현재 `a-cong OCR Service` |
| `compat_mode` | string | 현재 `datalab-like-v1` |
| `versions` | object | 버전 정보 |

응답 예시:

```json
{
  "status": "ok",
  "service": "a-cong OCR Service",
  "compat_mode": "datalab-like-v1",
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  }
}
```

기본 health 최소 응답 예시:

```json
{
  "status": "ok"
}
```

## 5. 동기 OCR API

### 5.1 `POST /api/v1/ocr/image`

이미지 1장을 즉시 OCR한다.

#### Request

| 필드 | 위치 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `file` | form-data | file | Y | - | OCR 대상 이미지 |
| `page_number` | form-data | integer | N | `1` | 응답에 포함될 페이지 번호 |
| `width` | form-data | integer | N | 이미지에서 자동 추론 | 이미지 폭 |
| `height` | form-data | integer | N | 이미지에서 자동 추론 | 이미지 높이 |

#### Request 제약

| 항목 | 제약 |
| --- | --- |
| `page_number` | 1 이상 |
| `width`, `height` | 1 이상이어야 함 |
| 빈 파일 업로드 | 허용 안 함 |

#### Response 스키마

상위 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `page_number` | integer | 처리 페이지 번호 |
| `width` | integer | 실제 처리 폭 |
| `height` | integer | 실제 처리 높이 |
| `image_path` | string | 임시 처리 파일명 |
| `blocks` | array | OCR 블록 목록 |
| `raw_vl` | object | VL OCR 원본 payload |
| `raw_structure` | object | 구조 추론 원본 payload |
| `raw_fallback_ocr` | object | fallback OCR 원본 payload |

`blocks[]` 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `block_id` | string | 블록 식별자 |
| `page_number` | integer | 페이지 번호 |
| `label` | string | 블록 타입. 예: `title`, `text`, `image` |
| `bbox` | number[4] | 영역 좌표 |
| `text` | string | 인식 텍스트 |
| `confidence` | number | 신뢰도 |
| `metadata` | object | 부가 메타데이터 |

#### 성공 응답 예시

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

#### 성공 응답 예시: 이미지 블록 포함

```json
{
  "page_number": 1,
  "width": 1280,
  "height": 1810,
  "image_path": "newspaper_01.png",
  "blocks": [
    {
      "block_id": "title-1",
      "page_number": 1,
      "label": "title",
      "bbox": [76, 52, 980, 160],
      "text": "국방 일일 브리핑",
      "confidence": 0.98,
      "metadata": {}
    },
    {
      "block_id": "text-1",
      "page_number": 1,
      "label": "text",
      "bbox": [84, 190, 1012, 720],
      "text": "훈련 결과와 장비 점검 내용을 정리했다.",
      "confidence": 0.95,
      "metadata": {}
    },
    {
      "block_id": "image-1",
      "page_number": 1,
      "label": "image",
      "bbox": [820, 760, 1200, 1120],
      "text": "",
      "confidence": 0.88,
      "metadata": {}
    }
  ],
  "raw_vl": {
    "engine": "chandra",
    "parsing_res_list": [
      {
        "label": "title",
        "bbox": [76, 52, 980, 160],
        "content": "국방 일일 브리핑"
      }
    ]
  },
  "raw_structure": {},
  "raw_fallback_ocr": {}
}
```

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `400` | `file is required` |
| `400` | `page_number must be greater than zero` |
| `400` | `empty image upload` |
| `400` | `unable to resolve image dimensions` |

#### 오류 응답 예시: 빈 업로드

```json
{
  "detail": "empty image upload"
}
```

#### 오류 응답 예시: 잘못된 page_number

```json
{
  "detail": "page_number must be greater than zero"
}
```

#### 호출 예시

```bash
curl -X POST "http://127.0.0.1:18009/api/v1/ocr/image" \
  -F "file=@page.png" \
  -F "page_number=1"
```

### 5.2 `POST /api/v1/ocr/pdf`

PDF 전체를 렌더링한 뒤 각 페이지를 OCR한다.

#### Request

| 필드 | 위치 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `file` | form-data | file | Y | - | `.pdf` 파일 |
| `dpi` | form-data | integer | N | `300` | PDF 렌더링 DPI |

#### Request 제약

| 항목 | 제약 |
| --- | --- |
| 파일 확장자 | `.pdf` 만 허용 |
| `dpi` | 1 이상 |
| 빈 파일 업로드 | 허용 안 함 |

#### Response 스키마

상위 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `page_count` | integer | 처리된 전체 페이지 수 |
| `pdf_name` | string | 업로드 파일명 |
| `pages` | array | 페이지별 OCR 결과 |

`pages[]`는 `POST /api/v1/ocr/image` 응답과 같은 페이지 구조를 사용한다.

#### 성공 응답 예시

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

#### 성공 응답 예시: 다중 페이지

```json
{
  "page_count": 2,
  "pdf_name": "defense-report.pdf",
  "pages": [
    {
      "page_number": 1,
      "width": 2480,
      "height": 3508,
      "image_path": "page_0001.png",
      "blocks": [
        {
          "block_id": "title-1",
          "page_number": 1,
          "label": "title",
          "bbox": [120, 130, 1900, 260],
          "text": "국방 일일 브리핑",
          "confidence": 0.98,
          "metadata": {}
        }
      ],
      "raw_vl": {},
      "raw_structure": {},
      "raw_fallback_ocr": {}
    },
    {
      "page_number": 2,
      "width": 2480,
      "height": 3508,
      "image_path": "page_0002.png",
      "blocks": [
        {
          "block_id": "text-2",
          "page_number": 2,
          "label": "text",
          "bbox": [140, 220, 2100, 1800],
          "text": "추가 점검 결과와 후속 조치 계획을 정리했다.",
          "confidence": 0.94,
          "metadata": {}
        }
      ],
      "raw_vl": {},
      "raw_structure": {},
      "raw_fallback_ocr": {}
    }
  ]
}
```

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `400` | `only PDF files are supported` |
| `400` | `empty PDF upload` |
| `400` | `dpi must be a positive integer` |

#### 오류 응답 예시: PDF 아님

```json
{
  "detail": "only PDF files are supported"
}
```

#### 오류 응답 예시: 잘못된 DPI

```json
{
  "detail": "dpi must be a positive integer"
}
```

#### 호출 예시

```bash
curl -X POST "http://127.0.0.1:18009/api/v1/ocr/pdf" \
  -F "file=@report.pdf" \
  -F "dpi=300"
```

## 6. 비동기 OCR API

### 6.1 처리 흐름

| 순서 | 동작 |
| --- | --- |
| 1 | `POST /api/v1/ocr` 로 작업 제출 |
| 2 | 응답에서 `request_id` 수신 |
| 3 | `GET /api/v1/ocr/{request_id}` polling |
| 4 | `status=complete` 이면 결과 사용 |
| 5 | `status=failed` 이면 `error` 확인 |

### 6.2 `POST /api/v1/ocr`

비동기 OCR 작업을 등록한다.

#### Request

| 필드 | 위치 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `file` | form-data | file | 조건부 | - | 업로드 파일 |
| `file.0` | form-data | file | 조건부 | - | 호환용 대체 파일 필드 |
| `page_number` | form-data | integer | N | `1` | 이미지 입력 시 페이지 번호 |
| `width` | form-data | integer | N | 자동 추론 | 이미지 폭 |
| `height` | form-data | integer | N | 자동 추론 | 이미지 높이 |
| `dpi` | form-data | integer | N | `300` | PDF 렌더링 DPI |
| `max_pages` | form-data | integer | N | 없음 | 최대 처리 페이지 수 |
| `page_range` | form-data | string | N | 없음 | 선택 페이지 범위 |

파일 필드는 `file` 또는 `file.0` 중 하나가 필요하다.

#### `page_range` 규칙

| 예시 | 의미 |
| --- | --- |
| `0` | 첫 페이지만 |
| `0-2` | 0,1,2 페이지 |
| `0,2,4-5` | 0,2,4,5 페이지 |

제약:

- zero-based index만 허용
- 음수 금지
- range는 오름차순이어야 함

#### 제출 응답 스키마

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `request_id` | string | 비동기 요청 ID |
| `request_check_url` | string | 결과 조회 URL |
| `success` | boolean | 제출 성공 여부 |
| `error` | string \| null | 제출 단계 오류 |
| `versions` | object | 버전 정보 |

#### 제출 응답 예시

```json
{
  "request_id": "4b4c6f0d5c5345cfb0cfa2f2d7c4d6b8",
  "request_check_url": "http://127.0.0.1:18009/api/v1/ocr/4b4c6f0d5c5345cfb0cfa2f2d7c4d6b8",
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

#### 제출 응답 예시: file.0 사용

```json
{
  "request_id": "9b7d4d9c3bd34d4ab9fef1d8d32be210",
  "request_check_url": "http://127.0.0.1:18009/api/v1/ocr/9b7d4d9c3bd34d4ab9fef1d8d32be210",
  "success": true,
  "error": null,
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "/models/chandra-ocr-2"
  }
}
```

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `400` | `file is required` |
| `400` | `empty upload` |

#### 오류 응답 예시: 파일 누락

```json
{
  "detail": "file is required"
}
```

#### 오류 응답 예시: 빈 업로드

```json
{
  "detail": "empty upload"
}
```

### 6.3 `GET /api/v1/ocr/{request_id}`

비동기 OCR 결과를 조회한다.

#### Response 스키마

상위 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `status` | string | `processing`, `complete`, `failed` |
| `pages` | array \| null | 페이지 결과. 실패 시 `null` 가능 |
| `success` | boolean \| null | 완료 시 `true`, 실패 시 `false`, 처리 중이면 `null` 가능 |
| `error` | string \| null | 실패 메시지 |
| `page_count` | integer \| null | 전체 페이지 수 |
| `total_cost` | number | 현재 항상 `0` |
| `cost_breakdown` | object | 현재 `{ "credits": 0 }` |
| `versions` | object | 버전 정보 |

`pages[]` 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `page_number` | integer | 페이지 번호 |
| `width` | integer | 페이지 폭 |
| `height` | integer | 페이지 높이 |
| `text` | string | 페이지 전체 텍스트 |
| `lines` | array | 줄 단위 OCR 결과 |
| `blocks` | array | 블록 단위 OCR 결과 |
| `raw_vl` | object | 원본 payload |
| `raw_structure` | object | 원본 payload |
| `raw_fallback_ocr` | object | 원본 payload |

`lines[]` 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `text` | string | 줄 텍스트 |
| `bbox` | number[4] | 줄 좌표 |
| `label` | string | 라벨 |
| `confidence` | number | 신뢰도 |

#### 완료 응답 예시

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

#### 완료 응답 예시: 다중 페이지 OCR 결과

```json
{
  "status": "complete",
  "pages": [
    {
      "page_number": 1,
      "width": 2480,
      "height": 3508,
      "text": "국방 일일 브리핑\n훈련 결과와 장비 점검 내용을 정리했다.",
      "lines": [
        {
          "text": "국방 일일 브리핑",
          "bbox": [120, 130, 1900, 260],
          "label": "title",
          "confidence": 0.98
        },
        {
          "text": "훈련 결과와 장비 점검 내용을 정리했다.",
          "bbox": [140, 310, 2010, 980],
          "label": "text",
          "confidence": 0.95
        }
      ],
      "blocks": [
        {
          "block_id": "title-1",
          "page_number": 1,
          "label": "title",
          "bbox": [120, 130, 1900, 260],
          "text": "국방 일일 브리핑",
          "confidence": 0.98,
          "metadata": {}
        }
      ],
      "raw_vl": {},
      "raw_structure": {},
      "raw_fallback_ocr": {}
    },
    {
      "page_number": 2,
      "width": 2480,
      "height": 3508,
      "text": "추가 점검 결과와 후속 조치 계획을 정리했다.",
      "lines": [
        {
          "text": "추가 점검 결과와 후속 조치 계획을 정리했다.",
          "bbox": [140, 210, 2100, 920],
          "label": "text",
          "confidence": 0.94
        }
      ],
      "blocks": [
        {
          "block_id": "text-2",
          "page_number": 2,
          "label": "text",
          "bbox": [140, 210, 2100, 920],
          "text": "추가 점검 결과와 후속 조치 계획을 정리했다.",
          "confidence": 0.94,
          "metadata": {}
        }
      ],
      "raw_vl": {},
      "raw_structure": {},
      "raw_fallback_ocr": {}
    }
  ],
  "success": true,
  "error": null,
  "page_count": 2,
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

#### 처리 중 응답 예시

```json
{
  "status": "processing",
  "success": null,
  "error": null,
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  }
}
```

#### 실패 응답 예시

```json
{
  "status": "failed",
  "pages": null,
  "success": false,
  "error": "....",
  "page_count": null,
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

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `404` | `request not found` |

#### 오류 응답 예시: 존재하지 않는 request_id

```json
{
  "detail": "request not found"
}
```

## 7. Marker API

Marker API는 OCR 결과를 블록/기사형 구조와 함께 반환하는 비동기 API다.

### 7.1 `POST /api/v1/marker`

비동기 Marker 작업을 등록한다.

#### Request

| 필드 | 위치 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- | --- |
| `file` | form-data | file | 조건부 | - | 업로드 파일 |
| `file.0` | form-data | file | 조건부 | - | 호환용 대체 파일 필드 |
| `page_number` | form-data | integer | N | `1` | 이미지 입력 시 페이지 번호 |
| `width` | form-data | integer | N | 자동 추론 | 이미지 폭 |
| `height` | form-data | integer | N | 자동 추론 | 이미지 높이 |
| `dpi` | form-data | integer | N | `300` | PDF 렌더링 DPI |
| `max_pages` | form-data | integer | N | 없음 | 최대 처리 페이지 수 |
| `page_range` | form-data | string | N | 없음 | 선택 페이지 범위 |
| `output_format` | form-data | string | N | `json` | 출력 포맷 |

지원 `output_format`:

| 값 | 설명 |
| --- | --- |
| `json` | 구조화 JSON |
| `markdown` | markdown 결과 포함 |
| `html` | html 결과 포함 |
| `chunks` | chunk 결과 포함 |

제출 응답은 `POST /api/v1/ocr` 와 동일한 구조를 사용한다.

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `400` | `file is required` |
| `400` | `empty upload` |

#### 제출 응답 예시

```json
{
  "request_id": "7c1e59b48cd4428a85e45a9913a9ddef",
  "request_check_url": "http://127.0.0.1:18009/api/v1/marker/7c1e59b48cd4428a85e45a9913a9ddef",
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

#### 오류 응답 예시: 파일 누락

```json
{
  "detail": "file is required"
}
```

### 7.2 `GET /api/v1/marker/{request_id}`

비동기 Marker 결과를 조회한다.

#### Response 스키마

상위 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `status` | string | `processing`, `complete`, `failed` |
| `success` | boolean \| null | 처리 상태 |
| `error` | string \| null | 오류 메시지 |
| `page_count` | integer \| null | 페이지 수 |
| `output_format` | string | 요청 출력 포맷 |
| `markdown` | string \| null | markdown 결과 |
| `html` | string \| null | html 결과 |
| `json` | object \| null | 구조화 JSON 결과 |
| `chunks` | array \| null | chunk 결과 |
| `checkpoint_id` | string \| null | 결과 체크포인트 ID |
| `total_cost` | number | 현재 항상 `0` |
| `cost_breakdown` | object | 현재 `{ "credits": 0 }` |
| `versions` | object | 버전 정보 |

`json` 객체:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `request_id` | string | 요청 ID |
| `file_name` | string | 원본 파일명 |
| `page_count` | integer | 전체 페이지 수 |
| `pages` | array | 페이지별 구조화 결과 |

`json.pages[]` 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `page_number` | integer | 페이지 번호 |
| `width` | integer | 페이지 폭 |
| `height` | integer | 페이지 높이 |
| `text` | string | 페이지 전체 텍스트 |
| `blocks` | array | 블록 목록 |
| `articles` | array | 기사 후보 목록 |
| `unassigned` | array | 미배정 블록 목록 |

`json.pages[].articles[]` 필드:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `title` | string | 기사 제목 |
| `body_text` | string | 기사 본문 |
| `title_bbox` | number[4] | 제목 좌표 |
| `article_bbox` | number[4] | 기사 전체 좌표 |
| `confidence` | number | 신뢰도 |
| `layout_type` | string | 현재 예: `article` |
| `images` | array | 기사 이미지 목록 |

#### 완료 응답 예시

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

#### 완료 응답 예시: `output_format=markdown`

```json
{
  "status": "complete",
  "success": true,
  "error": null,
  "page_count": 1,
  "output_format": "markdown",
  "markdown": "# Page 1\n\n## 국방 일일 브리핑\n\n훈련 결과와 장비 점검 내용을 정리했다.",
  "html": "<html><body><section data-page='1'><h2>Page 1</h2><article><h3>국방 일일 브리핑</h3><p>훈련 결과와 장비 점검 내용을 정리했다.</p></article></section></body></html>",
  "json": {
    "request_id": "7c1e59b48cd4428a85e45a9913a9ddef",
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
  "chunks": [
    {
      "page_number": 1,
      "file_name": "page.png",
      "block_id": "title-1",
      "label": "title",
      "bbox": [40, 40, 320, 92],
      "text": "국방 일일 브리핑",
      "confidence": 0.98,
      "metadata": {}
    }
  ],
  "checkpoint_id": "7c1e59b48cd4428a85e45a9913a9ddef",
  "total_cost": 0,
  "cost_breakdown": {
    "credits": 0
  },
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  },
  "result": "# Page 1\n\n## 국방 일일 브리핑\n\n훈련 결과와 장비 점검 내용을 정리했다."
}
```

#### 완료 응답 예시: 기사 이미지 포함

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
            "images": [
              {
                "bbox": [360, 80, 620, 260],
                "confidence": 0.88,
                "captions": [
                  {
                    "text": "훈련 장면",
                    "bbox": [360, 262, 620, 292],
                    "confidence": 0.86
                  }
                ]
              }
            ]
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

#### 실패 응답

| 필드 | 값 예시 |
| --- | --- |
| `status` | `failed` |
| `success` | `false` |
| `error` | 예외 메시지 |
| `markdown` | `null` |
| `html` | `null` |
| `json` | `null` |
| `chunks` | `null` |

#### 실패 응답 예시

```json
{
  "status": "failed",
  "success": false,
  "error": "output_format must be one of: json, markdown, html, chunks",
  "page_count": null,
  "markdown": null,
  "html": null,
  "json": null,
  "chunks": null,
  "versions": {
    "service": "a-cong-ocr",
    "compat_mode": "datalab-like-v1",
    "ocr_backend": "chandra",
    "chandra_model": "datalab-to/chandra-ocr-2"
  }
}
```

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `404` | `request not found` |

#### 오류 응답 예시: 존재하지 않는 request_id

```json
{
  "detail": "request not found"
}
```

## 8. Thumbnail API

### 8.1 `GET /api/v1/thumbnails/{lookup_key}`

기존 OCR/Marker 요청 결과에 대해 페이지 이미지 썸네일을 생성한다.

#### Request

Path params:

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `lookup_key` | string | 일반적으로 `request_id` |

Query params:

| 필드 | 타입 | 필수 | 기본값 | 설명 |
| --- | --- | --- | --- | --- |
| `page_range` | string | N | 없음 | zero-based page index 범위 |
| `thumb_width` | integer | N | `300` | 썸네일 폭 |

#### Response 스키마

| 필드 | 타입 | 설명 |
| --- | --- | --- |
| `thumbnails` | array[string] | base64 JPEG 배열 |
| `success` | boolean | 성공 여부 |
| `error` | string \| null | 오류 메시지 |

#### 성공 응답 예시

```json
{
  "thumbnails": ["<base64-jpeg>"],
  "success": true,
  "error": null
}
```

#### 성공 응답 예시: 다중 썸네일

```json
{
  "thumbnails": [
    "/9j/4AAQSkZJRgABAQAAAQABAAD...",
    "/9j/4AAQSkZJRgABAQAAAQABAAD..."
  ],
  "success": true,
  "error": null
}
```

#### 오류 응답

| HTTP Status | detail |
| --- | --- |
| `400` | `thumb_width must be positive` |
| `400` | `page_range must use zero-based page indexes` |
| `400` | `page_range must use ascending zero-based page indexes` |
| `404` | `lookup_key not found` |

#### 오류 응답 예시: 잘못된 page_range

```json
{
  "detail": "page_range must use zero-based page indexes"
}
```

#### 오류 응답 예시: 존재하지 않는 lookup_key

```json
{
  "detail": "lookup_key not found"
}
```

## 9. 빠른 호출 예시

### 9.1 이미지 1장 동기 OCR

```bash
curl -X POST "http://127.0.0.1:18009/api/v1/ocr/image" \
  -F "file=@page.png" \
  -F "page_number=1"
```

### 9.2 PDF 동기 OCR

```bash
curl -X POST "http://127.0.0.1:18009/api/v1/ocr/pdf" \
  -F "file=@report.pdf" \
  -F "dpi=300"
```

### 9.3 비동기 OCR 제출

```bash
curl -X POST "http://127.0.0.1:18009/api/v1/ocr" \
  -F "file=@report.pdf" \
  -F "page_range=0-2"
```

### 9.4 비동기 OCR 결과 조회

```bash
curl "http://127.0.0.1:18009/api/v1/ocr/<request_id>"
```

### 9.5 Marker 제출

```bash
curl -X POST "http://127.0.0.1:18009/api/v1/marker" \
  -F "file=@sample.pdf" \
  -F "output_format=json"
```

### 9.6 Marker 결과 조회

```bash
curl "http://127.0.0.1:18009/api/v1/marker/<request_id>"
```

## 10. 외부 시스템 연동 권장안

### 10.1 단순 OCR 연동

| 상황 | 권장 방식 |
| --- | --- |
| 응답 시간이 짧고 단건 처리 | 동기 API 사용 |
| 내부 배치/서비스 간 단순 연동 | `ocr/image`, `ocr/pdf` 사용 |

### 10.2 운영형 연동

| 상황 | 권장 방식 |
| --- | --- |
| 처리 시간이 길 수 있음 | 비동기 OCR 사용 |
| 페이지 수가 많음 | 비동기 OCR 또는 Marker 사용 |
| 구조화 결과 필요 | Marker 사용 |
| 디버깅/화면 검수 필요 | thumbnails 함께 사용 |

### 10.3 연동 시 주의사항

| 항목 | 설명 |
| --- | --- |
| 모델 warm-up | 서버 기동 직후 첫 요청은 느릴 수 있음 |
| 동기 PDF API | 페이지 수가 많으면 응답 시간이 길어질 수 있음 |
| 인증 부재 | 외부망 공개 전 별도 보안 계층 필요 |
| raw payload | `raw_vl`, `raw_structure`, `raw_fallback_ocr`는 안정 계약 필드로 보기 어려움 |
| 비용 필드 | 현재 모두 `0` 고정 |

## 11. 관련 문서

- 빠른 실행: [ocr_quick_guide.md](./ocr_quick_guide.md)
- 외부 연동 전체 명세: [external_api_spec.md](./external_api_spec.md)
- Datalab 호환 상세: [datalab_compat_api.md](./datalab_compat_api.md)
- 국방망 운영 가이드: [defense_network_ocr_guide.md](./defense_network_ocr_guide.md)
