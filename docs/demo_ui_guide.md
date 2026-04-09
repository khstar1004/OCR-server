# Demo UI 확인 가이드

이 문서는 오퍼레이터 데모 UI를 실제로 어떻게 띄우고 확인하는지에만 집중한 절차서입니다.

## 확인 전에 알아둘 점

- 데모 UI는 `GET /demo/jobs`에서 시작합니다.
- 화면 데이터는 DB와 기사 번들 파일을 함께 읽습니다.
- 그래서 `news_output` 폴더만 있어서는 부족할 수 있습니다.
- 최소 한 번은 OCR 작업이 실행되어 DB에 job / pdf / page / article 레코드가 있어야 합니다.

## 가장 빠른 확인 절차

1. 서버 실행
2. [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs)에 PDF 넣기
3. PDF 1건 처리
4. `/demo/jobs` 접속
5. 기사 선택
6. `Reprocess`, `Redeliver` 동작 확인

기본 입력 폴더:

- [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs)

## 1. 서버 실행

### 로컬 Python

```powershell
.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

브라우저 기본 주소:

- `http://127.0.0.1:8000/demo/jobs`

### Docker

```powershell
$env:WATCH_DIR='C:\news\watch'
$env:DATA_DIR='C:\news\data'
$env:MODELS_DIR='C:\news\models'
docker compose up -d
```

브라우저 기본 주소:

- `http://127.0.0.1:18000/demo/jobs`

## 2. PDF 1건 처리

가장 확실한 방법은 `run-single` API로 샘플 PDF를 직접 넣는 것입니다.

`run-daily`를 쓰는 경우에는 먼저 PDF를 [news_pdfs](C:\Users\USER\Desktop\a-cong-OCR-V2\news_pdfs)에 넣으면 됩니다.

### 로컬 서버 포트 8000 기준

```powershell
Invoke-WebRequest `
  -Method Post `
  -InFile .\sample.pdf `
  -ContentType "application/pdf" `
  -Uri "http://127.0.0.1:8000/api/v1/jobs/run-single?file_name=sample.pdf&force_reprocess=true"
```

### Docker 포트 18000 기준

```powershell
Invoke-WebRequest `
  -Method Post `
  -InFile .\sample.pdf `
  -ContentType "application/pdf" `
  -Uri "http://127.0.0.1:18000/api/v1/jobs/run-single?file_name=sample.pdf&force_reprocess=true"
```

성공하면 `job_id`가 반환됩니다.

## 3. 데모 화면에서 보는 순서

### `/demo/jobs`

- 최근 job 목록이 좌측에 보입니다.
- 새로 만든 `job_id`가 보여야 정상입니다.

### 중앙 트리

- PDF 단위 그룹이 보입니다.
- page 단위 하위 항목이 보입니다.
- article 링크를 누르면 우측 상세 패널이 갱신됩니다.

### 우측 상세 패널

반드시 확인할 항목:

- source PDF / job 정보
- page image preview
- article bounding box overlay
- raw OCR output
- corrected article text
- relevance score / reason
- delivery status / last error

## 4. 액션 확인

### Reprocess

- 버튼: `Reprocess`
- 기대 결과:
  - 성공 플래시 메시지 표시
  - 새 `job_id`가 큐에 들어감
  - 이후 좌측 job 목록에 새 작업이 생김

### Redeliver

- 버튼: `Redeliver`
- 기대 결과:
  - delivery hook 또는 callback URL로 재전송 시도
  - `demo_delivery.json` 또는 delivery state가 갱신됨
  - 마지막 오류가 있으면 상세 화면에 표시

## 5. 파일 기준으로 같이 보면 좋은 것

기사 상세 패널과 아래 파일을 같이 비교하면 상태 확인이 쉽습니다.

- 기사 메타데이터: `article.json`
- 기사 텍스트: `article.md`
- 교정 상태: `annotation.json`
- 점수/사유: `enrichment.json`
- 재전송 상태: `demo_delivery.json` 또는 `delivery.json`

## 자주 헷갈리는 부분

### `/demo/jobs`가 열리지만 목록이 비는 경우

가능성이 큰 원인:

- DB에 아직 작업이 없음
- 기존 산출물만 있고 job / article 레코드가 없음
- 다른 `DATA_DIR` 또는 다른 SQLite 파일을 보고 있음

가장 빠른 해결:

1. 현재 서버가 보는 DB 경로를 확인
2. `run-single`로 PDF 한 건 다시 실행
3. `/demo/jobs` 새로고침

### 기사 상세는 뜨는데 correction / score / delivery가 비는 경우

가능성이 큰 원인:

- `annotation.json` 없음
- `enrichment.json` 없음
- `demo_delivery.json` 또는 `delivery.json` 없음
- Worker 3 훅 미연결

이 경우 UI는 빈 값 대신 fallback 상태를 보여줍니다.

## 점검 체크리스트

- job 목록이 보인다
- article 선택 시 우측 패널이 갱신된다
- bbox overlay가 기사 위치와 맞는다
- raw OCR와 corrected text를 둘 다 볼 수 있다
- score / reason이 있으면 표시된다
- delivery status / last error가 있으면 표시된다
- `Reprocess` 후 새 job이 생긴다
- `Redeliver` 후 delivery 상태 파일이 갱신된다
