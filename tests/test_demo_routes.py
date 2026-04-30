from __future__ import annotations

import base64
import importlib
import json
import sys
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select


PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


def _fresh_import(module_name: str):
    return importlib.import_module(module_name)


def _as_container_output_path(actual_path: Path, output_root: Path) -> str:
    return f"/data/runtime/output/{actual_path.relative_to(output_root).as_posix()}"


def _bootstrap_app(tmp_path: Path, monkeypatch, *, root_path: str = ""):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'news_ocr.db').as_posix()}")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))
    monkeypatch.setenv("CALLBACK_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("TARGET_API_BASE_URL", "http://env.test/news")

    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)

    base = _fresh_import("app.db.base")
    session_module = _fresh_import("app.db.session")
    models = _fresh_import("app.db.models")
    storage_module = _fresh_import("app.services.storage")
    jobs_routes = _fresh_import("app.api.routes.jobs")
    demo_module = _fresh_import("app.api.demo")

    base.Base.metadata.create_all(bind=session_module.engine)
    db = session_module.SessionLocal()

    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    models_dir = tmp_path / "models"
    for directory in [input_dir, output_dir, models_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    source_pdf = input_dir / "demo.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 demo")

    page_image = output_dir / "pages" / "page_0001.png"
    page_image.parent.mkdir(parents=True, exist_ok=True)
    page_image.write_bytes(PNG_1X1)

    page_image_2 = output_dir / "pages" / "page_0002.png"
    page_image_2.write_bytes(PNG_1X1)

    article_image = output_dir / "images" / "image_0001.png"
    article_image.parent.mkdir(parents=True, exist_ok=True)
    article_image.write_bytes(PNG_1X1)

    raw_vl = output_dir / "raw" / "page_0001_vl.json"
    raw_vl.parent.mkdir(parents=True, exist_ok=True)
    raw_vl.write_text(
        json.dumps(
            {
                "parsing_res_list": [
                    {"label": "title", "bbox": [20, 20, 400, 120], "content": "OCR Title"},
                    {"label": "text", "bbox": [30, 160, 620, 820], "content": "raw line one\nraw line two"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    raw_vl_2 = output_dir / "raw" / "page_0002_vl.json"
    raw_vl_2.write_text(
        json.dumps(
            {
                "parsing_res_list": [
                    {"label": "text", "bbox": [40, 80, 600, 320], "content": "second page body"},
                    {"label": "text", "bbox": [610, 90, 980, 330], "content": "neighbor noise"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    job = models.Job(
        job_key="job_20260331_101010",
        source_dir=str(input_dir),
        callback_url="https://callback.test/demo",
        status="completed",
        total_files=1,
        success_files=1,
        failed_files=0,
        total_articles=2,
    )
    db.add(job)
    db.flush()

    pdf_file = models.PdfFile(
        job_id=job.id,
        file_name="demo.pdf",
        file_path=str(source_pdf),
        file_hash="hash-demo",
        page_count=2,
        status="completed",
    )
    db.add(pdf_file)
    db.flush()

    page = models.Page(
        pdf_file_id=pdf_file.id,
        page_number=1,
        page_image_path=str(page_image),
        raw_vl_json_path=str(raw_vl),
        raw_structure_json_path=str(raw_vl),
        raw_fallback_json_path=str(raw_vl),
        width=1000,
        height=1400,
        parse_status="parsed",
    )
    db.add(page)
    db.flush()

    page_2 = models.Page(
        pdf_file_id=pdf_file.id,
        page_number=2,
        page_image_path=str(page_image_2),
        raw_vl_json_path=str(raw_vl_2),
        raw_structure_json_path=str(raw_vl_2),
        raw_fallback_json_path=str(raw_vl_2),
        width=1000,
        height=1400,
        parse_status="parsed",
    )
    db.add(page_2)
    db.flush()

    article = models.Article(
        pdf_file_id=pdf_file.id,
        page_id=page.id,
        article_order=1,
        title="OCR Title",
        body_text="raw line one\nraw line two",
        title_bbox=[20, 20, 400, 120],
        article_bbox=[18, 18, 650, 920],
        confidence=0.913,
        layout_type="article",
    )
    db.add(article)
    db.flush()

    article_2 = models.Article(
        pdf_file_id=pdf_file.id,
        page_id=page_2.id,
        article_order=1,
        title="Second Page Headline",
        body_text="second page body",
        title_bbox=[40, 40, 500, 120],
        article_bbox=[35, 35, 650, 720],
        confidence=0.821,
        layout_type="article",
    )
    db.add(article_2)
    db.flush()

    image = models.ArticleImage(
        article_id=article.id,
        page_id=page.id,
        image_order=1,
        image_path=str(article_image),
        image_bbox=[60, 240, 260, 520],
        width=200,
        height=280,
    )
    db.add(image)
    db.commit()

    storage = storage_module.OutputStorage()
    bundle_dir = storage.article_bundle_dir(job.job_key, pdf_file.file_name, page.page_number, article.article_order, article.title)
    (bundle_dir / "article.json").write_text(
        json.dumps(
            {
                "job_id": job.job_key,
                "pdf_file": pdf_file.file_name,
                "page_number": page.page_number,
                "article_id": article.id,
                "article_order": article.article_order,
                "title": article.title,
                "body_text": article.body_text,
                "article_bbox": article.article_bbox,
                "relevance_score": 0.982,
                "relevance_reason": "국회 질의와 자료제출 맥락이 직접 보입니다.",
                "relevance_label": "high",
                "relevance_model": "gpt-oss-20b",
                "relevance_source": "llm",
                "source_metadata": {
                    "publication": "한겨레",
                    "issue_date": "2026-01-02",
                    "issue_date_text": "2026년 1월 2일 금요일",
                    "issue_weekday": "금요일",
                    "issue_page": "019",
                    "issue_page_label": "019면",
                    "issue_section": "사람",
                    "raw_publication_text": "한겨레",
                    "raw_issue_text": "2026년 1월 2일 금요일 019면 사람",
                    "publication_bbox": [24, 18, 140, 52],
                    "issue_bbox": [180, 18, 430, 52],
                },
                "ocr_quality": {
                    "status": "warning",
                    "score": 0.68,
                    "char_count": 220,
                    "korean_ratio": 0.73,
                    "average_confidence": 0.91,
                    "block_count": 8,
                    "image_count": 1,
                    "article_count": 1,
                    "needs_review": True,
                    "reasons": ["low_text"],
                },
                "caption_count": 1,
                "captions": [
                    {
                        "text": "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다.",
                        "bbox": [70, 530, 340, 590],
                        "confidence": 0.91,
                    }
                ],
                "images": [
                    {
                        "image_order": 1,
                        "image_path": str(article_image),
                        "relative_path": "images/image_0001.png",
                        "bbox": image.image_bbox,
                        "captions": [
                            {
                                "text": "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다.",
                                "bbox": [70, 530, 340, 590],
                                "confidence": 0.91,
                            }
                        ],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    page_bundle = storage.page_bundle_dir(job.job_key, pdf_file.file_name, page.page_number)
    (page_bundle / "page.json").write_text(
        json.dumps(
            {
                "job_id": job.job_key,
                "pdf_file": pdf_file.file_name,
                "page_number": page.page_number,
                "ocr_quality": {
                    "status": "warning",
                    "score": 0.68,
                    "char_count": 220,
                    "korean_ratio": 0.73,
                    "average_confidence": 0.91,
                    "block_count": 8,
                    "image_count": 1,
                    "article_count": 1,
                    "needs_review": True,
                    "reasons": ["low_text"],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "article.md").write_text("# OCR Title\n\nraw line one\n", encoding="utf-8")
    (bundle_dir / "annotation.json").write_text(
        json.dumps(
            {
                "status": "accepted",
                "corrected_title": "Edited headline",
                "corrected_body_text": "Edited body",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "enrichment.json").write_text(
        json.dumps(
            {
                "relevance_score": 0.982,
                "relevance_reason": "국회 질의와 자료제출 맥락이 직접 보입니다.",
                "relevance_label": "high",
                "relevance_model": "gpt-oss-20b",
                "relevance_source": "llm",
                "corrected_title": "LLM headline",
                "corrected_body_text": "LLM body",
                "correction_source": "llm",
                "correction_model": "gpt-oss-20b",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (bundle_dir / "demo_delivery.json").write_text(
        json.dumps(
            {
                "delivery_status": "failed",
                "last_error": "connection refused",
                "updated_at": "2026-03-31T10:10:10+00:00",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    app = FastAPI()
    app.include_router(demo_module.router)
    app.include_router(jobs_routes.router, prefix=demo_module.service.settings.api_prefix)

    return {
        "app": app,
        "client": TestClient(app, root_path=root_path),
        "db": db,
        "bundle_dir": bundle_dir,
        "input_dir": input_dir,
        "source_pdf": source_pdf,
        "article_id": article.id,
        "article_2_id": article_2.id,
        "page_2_id": page_2.id,
        "job_id": job.id,
        "job_key": job.job_key,
        "demo_module": demo_module,
        "session_module": session_module,
    }


def test_demo_jobs_renders_article_detail(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    response = ctx["client"].get("/demo/jobs")

    assert response.status_code == 200
    assert "작업 화면" in response.text
    assert "Edited headline" in response.text
    assert "국회 유사도" in response.text
    assert "연결 실패" in response.text
    assert "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다." in response.text
    assert "/demo/jobs/start-dir" in response.text
    assert "/demo/jobs/start-file" in response.text
    assert "폴더 처리 시작" in response.text
    assert "단일 파일 처리" in response.text
    assert "작업 찾기" in response.text
    assert 'data-job-search' in response.text
    assert 'data-preset="quick"' in response.text
    assert "빠른 확인" in response.text
    assert "확인 필요한 쪽만 보기" in response.text
    assert "confirmJobDelete" in response.text
    assert "http://env.test/news" not in response.text
    assert "국회로 보내기" in response.text
    assert "읽기 품질" in response.text
    assert "검사 확인 필요" in response.text
    assert "읽기 0.68" in response.text
    assert f"/api/v1/jobs/{ctx['job_key']}/news-payload" in response.text
    assert f"/demo/jobs/{ctx['job_key']}/deliver" in response.text
    assert "<details" in response.text
    assert 'type="file"' in response.text
    assert 'name="pdf_file"' in response.text
    assert 'name="pdf_files"' in response.text
    assert 'name="callback_url"' not in response.text
    assert 'data-upload-input="pdf_file"' in response.text
    assert 'data-upload-input="pdf_files"' in response.text
    assert 'data-upload-preview="pdf_file"' in response.text
    assert 'data-upload-preview="pdf_files"' in response.text
    assert "webkitdirectory" in response.text
    assert "window.__demoJobsPageState" in response.text
    assert "window.location.reload" not in response.text
    assert f'href="/demo/jobs?job_id={ctx["job_key"]}&amp;article_id={ctx["article_id"]}&amp;view=json"' in response.text
    assert 'id="jobs-content"' in response.text
    assert 'hx-target="#jobs-content"' in response.text
    assert 'hx-push-url="true"' in response.text
    assert 'hx-get="/demo/articles/' not in response.text

    result_response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/result")
    assert result_response.status_code == 200
    payload = result_response.json()
    assert payload["files"][0]["articles"][0]["relevance_score"] == 0.982
    assert payload["files"][0]["articles"][0]["relevance_model"] == "gpt-oss-20b"
    assert payload["files"][0]["articles"][0]["title"] == "LLM headline"
    assert payload["files"][0]["articles"][0]["body_text"] == "LLM body"
    assert payload["files"][0]["articles"][0]["original_title"] == "OCR Title"
    assert payload["files"][0]["articles"][0]["corrected_title"] == "LLM headline"
    assert payload["files"][0]["articles"][0]["delivery_status"] == "failed"
    assert payload["files"][0]["articles"][0]["delivery_last_error"] == "connection refused"
    assert payload["files"][0]["articles"][0]["source_metadata"]["publication"] == "한겨레"
    assert payload["files"][0]["articles"][0]["source_metadata"]["issue_page_label"] == "019면"
    assert payload["files"][0]["articles"][0]["ocr_quality"]["status"] == "warning"
    assert payload["files"][0]["articles"][0]["ocr_quality"]["reasons"] == ["low_text"]
    assert payload["files"][0]["articles"][0]["images"][0]["captions"][0]["text"] == "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다."

    detail_response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/detail")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["quality"]["status"] == "warning"
    assert detail_payload["quality"]["review_pages"] == 1
    assert detail_payload["pdf_files"][0]["pages"][0]["quality_status"] == "warning"


def test_demo_jobs_restores_archived_output_without_db_rows(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    output_root = tmp_path / "output"
    archive_root = output_root / "job_20260422_101010" / "archive-pdf"
    page_dir = archive_root / "parsed" / "page_0001"
    article_dir = page_dir / "article_01_archive-headline"
    image_dir = article_dir / "images"
    raw_dir = archive_root / "raw"
    pages_dir = archive_root / "pages"
    for directory in [image_dir, raw_dir, pages_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (800, 1100), color="white").save(pages_dir / "page_0001.png")
    Image.new("RGB", (120, 80), color="black").save(image_dir / "image_01.png")
    (raw_dir / "page_0001_vl.json").write_text(
        json.dumps(
            {
                "parsing_res_list": [
                    {"label": "title", "bbox": [40, 50, 500, 120], "content": "Archive Headline"},
                    {"label": "text", "bbox": [40, 140, 650, 460], "content": "archive body text"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (raw_dir / "page_0001_structure.json").write_text("{}", encoding="utf-8")
    (raw_dir / "page_0001_fallback_ocr.json").write_text("{}", encoding="utf-8")
    article_payload = {
        "job_id": "job_20260422_101010",
        "pdf_file": "archive pdf.pdf",
        "page_number": 1,
        "article_id": 900,
        "article_order": 1,
        "title": "Archive Headline",
        "body_text": "archive body text",
        "title_bbox": [40, 50, 500, 120],
        "article_bbox": [35, 45, 700, 600],
        "confidence": 0.88,
        "images": [
            {
                "image_order": 1,
                "relative_path": "images/image_01.png",
                "bbox": [100, 200, 220, 280],
                "width": 120,
                "height": 80,
                "captions": [{"text": "archive caption", "bbox": [100, 285, 220, 320]}],
            }
        ],
        "captions": [{"text": "archive caption", "bbox": [100, 285, 220, 320]}],
        "relevance_score": 0.77,
        "relevance_reason": "archived result",
    }
    article_dir.mkdir(parents=True, exist_ok=True)
    (article_dir / "article.json").write_text(json.dumps(article_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (article_dir / "article.md").write_text("# Archive Headline\n\narchive body text\n", encoding="utf-8")
    (page_dir / "page.json").write_text(
        json.dumps(
            {
                "job_id": "job_20260422_101010",
                "pdf_file": "archive pdf.pdf",
                "page_number": 1,
                "articles": [
                    {
                        "article_order": 1,
                        "metadata_path": str(article_dir / "article.json"),
                        "bundle_dir": str(article_dir),
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    response = ctx["client"].get("/demo/jobs?job_id=job_20260422_101010&view=render")

    assert response.status_code == 200
    assert "Archive Headline" in response.text
    assert "archive body text" in response.text
    assert "최근 작업" in response.text
    assert "결과 복구" in response.text
    db = ctx["session_module"].SessionLocal()
    imported_job = db.scalar(select(_fresh_import("app.db.models").Job).where(_fresh_import("app.db.models").Job.job_key == "job_20260422_101010"))
    imported_article = db.scalar(select(_fresh_import("app.db.models").Article).where(_fresh_import("app.db.models").Article.title == "Archive Headline"))
    db.close()
    assert imported_job is not None
    assert imported_article is not None


def test_job_news_payload_preview_api_shows_congress_delivery_contract(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/news-payload")

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == ctx["job_key"]
    assert payload["target_configured"] is True
    assert payload["target_url"] == "http://env.test/news"
    assert payload["delivery_status"] == "warning"
    assert payload["ready_count"] == 1
    assert payload["warning_count"] == 1
    assert payload["blocked_count"] == 0
    assert payload["article_count"] == 2
    assert payload["included_image_count"] == 1
    first_article = payload["articles"][0]
    assert first_article["validation_status"] == "ready"
    assert first_article["title"] == "LLM headline"
    assert first_article["request_article"]["imgs"][0]["src"] == "file_0_0"
    assert first_article["images"][0]["included"] is True
    assert first_article["images"][0]["caption"] == "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다"
    assert payload["body"][0] == first_article["request_article"]


def test_job_deliver_api_updates_delivery_state(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 200
        text = "ok"

        def json(self):
            return {}

    def fake_post(url, *, data=None, files=None, headers=None, timeout=None):
        captured["url"] = url
        for field_name, file_payload in files:
            if field_name == "body":
                captured["body"] = json.loads(file_payload[1])
                break
        return DummyResponse()

    monkeypatch.setattr(_fresh_import("app.services.news_delivery").httpx, "post", fake_post)

    response = ctx["client"].post(f"/api/v1/jobs/{ctx['job_key']}/deliver")

    assert response.status_code == 200
    payload = response.json()
    assert payload["target_url"] == "http://env.test/news"
    assert payload["delivered"] == 2
    assert payload["failed"] == 0
    assert captured["url"] == "http://env.test/news"
    state = json.loads((ctx["bundle_dir"] / "delivery.json").read_text(encoding="utf-8"))
    assert state["delivery_status"] == "delivered"
    assert state["request_article"]["title"] == "LLM headline"


def test_run_single_rejects_non_pdf_body(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    response = ctx["client"].post(
        "/api/v1/jobs/run-single?file_name=not-a-pdf.pdf",
        content=b"this is not a pdf",
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "request body is not a PDF"


def test_run_single_accepts_image_body(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.api.routes.jobs"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        "/api/v1/jobs/run-single?file_name=page.png",
        content=PNG_1X1,
        headers={"content-type": "image/png"},
    )

    assert response.status_code == 202
    assert response.json()["job_id"].startswith("job_")
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    created_job = next(job for job in jobs if job.job_key != ctx["job_key"])
    staged_files = list(Path(created_job.source_dir).glob("*"))
    db.close()
    assert [path.name for path in staged_files] == ["page.png"]
    assert scheduled


def test_demo_static_assets_are_root_path_aware_and_cache_busted(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch, root_path="/a-cong-ocr")

    response = ctx["client"].get("/demo/jobs")
    static_response = ctx["client"].get("/static/demo.css")

    assert response.status_code == 200
    assert 'href="/a-cong-ocr/static/demo.css?v=' in response.text
    assert 'src="/a-cong-ocr/static/htmx-lite.js?v=' in response.text
    assert "?v=20260402" not in response.text
    assert static_response.status_code == 200
    assert static_response.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert static_response.headers["x-content-type-options"] == "nosniff"


def test_demo_jobs_render_panel_shows_all_articles_on_preview_page(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    storage_module = _fresh_import("app.services.storage")
    db = ctx["db"]

    page = db.scalar(select(models.Page).where(models.Page.page_number == 1))
    pdf_file = db.scalar(select(models.PdfFile).where(models.PdfFile.job_id == ctx["job_id"]))
    assert page is not None
    assert pdf_file is not None

    sibling_article = models.Article(
        pdf_file_id=pdf_file.id,
        page_id=page.id,
        article_order=2,
        title="Second Column Headline",
        body_text="same page sibling article body",
        title_bbox=[680, 180, 940, 260],
        article_bbox=[660, 180, 960, 860],
        confidence=0.744,
        layout_type="article",
    )
    db.add(sibling_article)
    db.commit()
    db.refresh(sibling_article)

    storage = storage_module.OutputStorage()
    sibling_bundle = storage.article_bundle_dir(
        ctx["job_key"],
        pdf_file.file_name,
        page.page_number,
        sibling_article.article_order,
        sibling_article.title,
    )
    (sibling_bundle / "article.json").write_text(
        json.dumps(
            {
                "job_id": ctx["job_key"],
                "pdf_file": pdf_file.file_name,
                "page_number": page.page_number,
                "article_id": sibling_article.id,
                "article_order": sibling_article.article_order,
                "title": sibling_article.title,
                "body_text": sibling_article.body_text,
                "article_bbox": sibling_article.article_bbox,
                "images": [],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (sibling_bundle / "article.md").write_text(
        "# Second Column Headline\n\nsame page sibling article body\n",
        encoding="utf-8",
    )

    response = ctx["client"].get(f"/demo/jobs?job_id={ctx['job_key']}&article_id={ctx['article_id']}&view=render")

    assert response.status_code == 200
    assert "현재 미리보기 페이지의 기사 2개" in response.text
    assert "Edited headline" in response.text
    assert "Second Column Headline" in response.text
    assert "same page sibling article body" in response.text


def test_start_dir_job_route_queues_new_job(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.web.demo_service"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        "/demo/jobs/start-dir",
        data={
            "source_dir": str(ctx["input_dir"]),
            "view": "render",
            "ocr_mode": "accurate",
            "page_range": "0",
            "max_pages": "1",
            "output_format": "json,markdown,html,chunks",
            "paginate": "true",
            "add_block_ids": "true",
            "include_markdown_in_chunks": "true",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/demo/jobs?job_id=job_" in response.headers["location"]
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    db.close()
    assert len(jobs) == 2
    assert scheduled
    created_job = next(job for job in jobs if job.job_key != ctx["job_key"])
    config = _fresh_import("app.services.storage").OutputStorage().load_job_config(created_job.job_key)
    assert config["ocr_options"]["ocr_mode"] == "accurate"
    assert config["ocr_options"]["page_range"] == "0"
    assert config["ocr_options"]["max_pages"] == 1
    assert config["ocr_options"]["output_formats"] == ["json", "markdown", "html", "chunks"]
    assert config["ocr_options"]["paginate"] is True
    assert config["ocr_options"]["add_block_ids"] is True
    assert config["ocr_options"]["include_markdown_in_chunks"] is True


def test_start_file_job_route_queues_new_job(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.web.demo_service"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        "/demo/jobs/start-file",
        data={"pdf_path": str(ctx["source_pdf"]), "view": "render"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/demo/jobs?job_id=job_" in response.headers["location"]
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    db.close()
    assert len(jobs) == 2
    assert scheduled


def test_start_dir_job_route_accepts_uploaded_pdf_files(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.web.demo_service"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        "/demo/jobs/start-dir",
        data={"view": "render"},
        files=[
            ("pdf_files", ("batch/first.pdf", b"%PDF-1.4 first", "application/pdf")),
            ("pdf_files", ("batch/second.pdf", b"%PDF-1.4 second", "application/pdf")),
        ],
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/demo/jobs?job_id=job_" in response.headers["location"]
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    db.close()
    assert len(jobs) == 2
    assert scheduled


def test_start_file_job_route_accepts_uploaded_pdf(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.web.demo_service"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        "/demo/jobs/start-file",
        data={"view": "render"},
        files={"pdf_file": ("upload.pdf", b"%PDF-1.4 upload", "application/pdf")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/demo/jobs?job_id=job_" in response.headers["location"]
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    db.close()
    assert len(jobs) == 2
    assert scheduled


def test_start_file_job_route_accepts_uploaded_image(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.web.demo_service"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        "/demo/jobs/start-file",
        data={"view": "render"},
        files={"pdf_file": ("page.png", PNG_1X1, "image/png")},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "/demo/jobs?job_id=job_" in response.headers["location"]
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    created_job = next(job for job in jobs if job.job_key != ctx["job_key"])
    staged_files = list(Path(created_job.source_dir).glob("*"))
    db.close()
    assert [path.name for path in staged_files] == ["page.png"]
    assert scheduled


def test_demo_jobs_falls_back_when_requested_job_is_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    page = ctx["demo_module"].service.build_jobs_page(ctx["db"], selected_job_key="job_missing_20260401")

    response = ctx["client"].get("/demo/jobs?job_id=job_missing_20260401&view=render")

    assert page["selected_job_key"] == ctx["job_key"]
    assert response.status_code == 200
    assert "표시할 기사 데이터가 없습니다." not in response.text
    assert "Edited headline" in response.text


def test_demo_jobs_list_recent_jobs_dedupes_job_keys(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    service = ctx["demo_module"].service

    first = models.Job(
        job_key="job_20260401_095533",
        source_dir=str(tmp_path / "input-a"),
        status="queued",
        total_files=0,
        success_files=0,
        failed_files=0,
        total_articles=0,
    )
    second = models.Job(
        job_key="job_20260401_095533",
        source_dir=str(tmp_path / "input-b"),
        status="queued",
        total_files=0,
        success_files=0,
        failed_files=0,
        total_articles=0,
    )
    third = models.Job(
        job_key="job_20260401_053740",
        source_dir=str(tmp_path / "input-c"),
        status="queued",
        total_files=0,
        success_files=0,
        failed_files=0,
        total_articles=0,
    )

    class FakeScalars:
        def __init__(self, rows):
            self._rows = rows

        def __iter__(self):
            return iter(self._rows)

    class FakeDB:
        def scalars(self, _statement):
            return FakeScalars([first, second, third])

    jobs = service.list_recent_jobs(FakeDB(), limit=12)

    assert [job.job_key for job in jobs] == ["job_20260401_095533", "job_20260401_053740"]


def test_demo_jobs_falls_back_when_requested_article_is_missing(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    response = ctx["client"].get(f"/demo/jobs?job_id={ctx['job_key']}&article_id=999999&view=render")

    assert response.status_code == 200
    assert "표시할 기사 데이터가 없습니다." not in response.text
    assert "Edited headline" in response.text


def test_demo_jobs_hx_request_returns_workspace_partial(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    response = ctx["client"].get(
        f"/demo/jobs?job_id={ctx['job_key']}&article_id={ctx['article_id']}&view=render",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert 'id="jobs-content"' in response.text
    assert '<header class="hero demo-hero">' not in response.text
    assert 'hx-get="/demo/jobs?' in response.text


def test_demo_detail_prefers_clustered_body_before_raw_ocr_fallback(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    detail = ctx["demo_module"].service.get_article_detail(ctx["db"], ctx["article_2_id"])

    assert detail is not None
    assert detail.raw_ocr_text == "second page body\nneighbor noise"
    assert detail.corrected_body_text == "second page body"


def test_demo_article_view_tabs_use_standalone_article_route(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    response = ctx["client"].get(f"/demo/articles/{ctx['article_id']}?view=render")

    assert response.status_code == 200
    assert f'href="/demo/articles/{ctx["article_id"]}?view=json"' in response.text
    assert f'href="/demo/articles/{ctx["article_id"]}?view=html"' in response.text
    assert f'href="/demo/articles/{ctx["article_id"]}?view=markdown"' in response.text


def test_demo_jobs_preview_navigation_can_open_next_pdf_page(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    response = ctx["client"].get(f"/demo/jobs?job_id={ctx['job_key']}&article_id={ctx['article_id']}&view=render")

    assert response.status_code == 200
    assert (
        f'hx-get="/demo/jobs?job_id={ctx["job_key"]}&amp;article_id={ctx["article_2_id"]}&amp;view=render"'
        in response.text
    )

    next_page = ctx["client"].get(f"/demo/jobs?job_id={ctx['job_key']}&article_id={ctx['article_2_id']}&view=render")

    assert next_page.status_code == 200
    assert "2쪽" in next_page.text
    assert 'alt="2쪽"' in next_page.text
    assert "Second Page Headline" in next_page.text


def test_demo_jobs_show_live_running_page_and_stage(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    db = ctx["db"]

    job = db.scalar(select(models.Job).where(models.Job.id == ctx["job_id"]))
    pdf_file = db.scalar(select(models.PdfFile).where(models.PdfFile.job_id == ctx["job_id"]))
    page_2 = db.scalar(select(models.Page).where(models.Page.id == ctx["page_2_id"]))
    assert job is not None
    assert pdf_file is not None
    assert page_2 is not None

    job.status = "running"
    job.success_files = 0
    pdf_file.status = "running"
    page_2.parse_status = "running"
    db.add(
        models.ProcessingLog(
            job_id=job.id,
            pdf_file_id=pdf_file.id,
            page_id=page_2.id,
            step_name="ocr_vl",
            status="running",
            message="calling remote OCR service",
        )
    )
    db.commit()

    response = ctx["client"].get(f"/demo/jobs?job_id={ctx['job_key']}&article_id={ctx['article_id']}&view=render")

    assert response.status_code == 200
    assert "처리 상태" in response.text
    assert "작업 요약" in response.text
    assert "글자 읽는 중" in response.text
    assert "2쪽" in response.text
    assert "처리중" in response.text
    assert 'data-auto-refresh-seconds="2"' in response.text


def test_redeliver_updates_delivery_state(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)

    class DummyResponse:
        status_code = 202

    def fake_post(*args, **kwargs):
        return DummyResponse()

    monkeypatch.setattr(_fresh_import("app.services.news_delivery").httpx, "post", fake_post)

    response = ctx["client"].post(
        f"/api/articles/{ctx['article_id']}/redeliver",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "multipart /news" in response.text

    payload = json.loads((ctx["bundle_dir"] / "demo_delivery.json").read_text(encoding="utf-8"))
    assert payload["delivery_status"] == "delivered"
    assert payload["transport"] == "multipart_news"
    assert payload["request_batch_size"] == 1
    assert payload["request_article"]["title"] == "LLM headline"
    assert payload["request_article"]["publication"] == "한겨레"


def test_demo_jobs_json_view_shows_delivery_request_payload(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    delivery_path = ctx["bundle_dir"] / "demo_delivery.json"
    delivery_path.write_text(
        json.dumps(
            {
                "delivery_status": "delivered",
                "endpoint": "https://api.test/news",
                "response_code": 201,
                "request_batch_size": 1,
                "request_article_index": 0,
                "request_article": {
                    "title": "LLM headline",
                    "body_text": "LLM body",
                    "imgs": [
                        {
                            "caption": "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다",
                            "src": "file_0_0",
                        }
                    ],
                    "relevance_score": 0.982,
                    "publication": "한겨레",
                    "issue_date": "2026-01-02",
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    response = ctx["client"].get(f"/demo/jobs?job_id={ctx['job_key']}&article_id={ctx['article_id']}&view=json")

    assert response.status_code == 200
    assert "전송값" in response.text
    assert "작업값" in response.text
    assert "file_0_0" in response.text
    assert "한겨레" in response.text
    assert "https://api.test/news" not in response.text


def test_news_delivery_client_builds_multipart_request(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    schemas = _fresh_import("app.schemas.job")
    result_builder = _fresh_import("app.services.result_builder")
    delivery_module = _fresh_import("app.services.news_delivery")
    db = ctx["db"]

    job = db.scalar(select(models.Job).where(models.Job.job_key == ctx["job_key"]))
    assert job is not None

    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 201

    def fake_post(url, *, data=None, files=None, headers=None, timeout=None):
        captured["url"] = url
        captured["files"] = []
        for field_name, file_payload in files:
            if field_name == "body":
                captured["body"] = json.loads(file_payload[1])
                captured["files"].append((field_name, file_payload[0], file_payload[2]))
                continue
            captured["files"].append((field_name, file_payload[0], file_payload[2]))
        captured["headers"] = headers
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(delivery_module.httpx, "post", fake_post)

    job_result = result_builder.build_job_result(db, job)
    article_payload = job_result.files[0].articles[0]
    article_without_images = schemas.ArticleResponse(
        article_id=article_payload.article_id + 999,
        page_number=article_payload.page_number,
        article_order=article_payload.article_order + 1,
        title="Empty image article",
        body_text="No images here",
        original_title="Empty image article",
        original_body_text="No images here",
        title_bbox=[10, 10, 20, 20],
        article_bbox=[10, 10, 20, 20],
        relevance_score=None,
        source_metadata=schemas.ArticleSourceMetadataResponse(),
        images=[],
        bundle_dir=str(ctx["bundle_dir"]),
        markdown_path=str(ctx["bundle_dir"] / "article.md"),
        metadata_path=str(ctx["bundle_dir"] / "article.json"),
    )
    client = delivery_module.NewsDeliveryClient()
    result = client.deliver_articles(
        [article_payload, article_without_images],
        state_filename="demo_delivery.json",
        raise_on_failure=True,
    )

    assert result.delivered == 2
    assert captured["url"] == "http://env.test/news"
    assert captured["headers"] == {}
    assert captured["timeout"] == 30.0
    assert captured["files"] == [
        ("body", None, "application/json"),
        ("file_0_0", "image_0001.png", "image/png"),
    ]

    multipart_body = captured["body"]
    assert multipart_body == [
        {
            "title": "LLM headline",
            "body_text": "LLM body",
            "imgs": [
                {
                    "caption": "부두에 정박한 상륙함을 향해 장병들이 손을 흔들고 있다",
                    "src": "file_0_0",
                }
            ],
            "relevance_score": 0.982,
            "publication": "한겨레",
            "issue_date": "2026-01-02",
        },
        {
            "title": "Empty image article",
            "body_text": "No images here",
            "imgs": [],
            "relevance_score": 0.0,
            "publication": "",
            "issue_date": "",
        }
    ]


def test_news_delivery_client_sends_empty_caption_for_missing_image_caption(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    schemas = _fresh_import("app.schemas.job")
    result_builder = _fresh_import("app.services.result_builder")
    delivery_module = _fresh_import("app.services.news_delivery")
    db = ctx["db"]

    job = db.scalar(select(models.Job).where(models.Job.job_key == ctx["job_key"]))
    assert job is not None

    captured: dict[str, object] = {}

    class DummyResponse:
        status_code = 201

    def fake_post(url, *, data=None, files=None, headers=None, timeout=None):
        for field_name, file_payload in files:
            if field_name == "body":
                captured["body"] = json.loads(file_payload[1])
                break
        return DummyResponse()

    monkeypatch.setattr(delivery_module.httpx, "post", fake_post)

    job_result = result_builder.build_job_result(db, job)
    article_payload = job_result.files[0].articles[0]
    assert article_payload.images

    captionless_article = schemas.ArticleResponse(
        article_id=article_payload.article_id + 1000,
        page_number=article_payload.page_number,
        article_order=article_payload.article_order + 10,
        title="Captionless image article",
        body_text="Image without caption",
        original_title="Captionless image article",
        original_body_text="Image without caption",
        title_bbox=article_payload.title_bbox,
        article_bbox=article_payload.article_bbox,
        relevance_score=None,
        source_metadata=schemas.ArticleSourceMetadataResponse(),
        images=[
            schemas.ArticleImageResponse(
                image_id=article_payload.images[0].image_id,
                image_path=article_payload.images[0].image_path,
                bbox=article_payload.images[0].bbox,
                captions=[],
            )
        ],
        bundle_dir=str(ctx["bundle_dir"]),
        markdown_path=str(ctx["bundle_dir"] / "article.md"),
        metadata_path=str(ctx["bundle_dir"] / "article.json"),
    )

    client = delivery_module.NewsDeliveryClient()
    client.deliver_articles(
        [captionless_article],
        state_filename="demo_delivery.json",
        raise_on_failure=True,
    )

    assert captured["body"] == [
        {
            "title": "Captionless image article",
            "body_text": "Image without caption",
            "imgs": [
                {
                    "caption": "",
                    "src": "file_0_0",
                }
            ],
            "relevance_score": 0.0,
            "publication": "",
            "issue_date": "",
        }
    ]


def test_page_preview_scales_normalized_bboxes_for_large_pages(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    db = ctx["db"]

    page = db.scalar(select(models.Page))
    article = db.scalar(select(models.Article))
    image = db.scalar(select(models.ArticleImage))
    assert page is not None
    assert article is not None
    assert image is not None

    page.width = 2480
    page.height = 3509
    article.title_bbox = [82, 270, 920, 301]
    article.article_bbox = [55, 270, 952, 753]
    image.image_bbox = [507, 196, 741, 332]
    db.commit()

    response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/pages/{page.id}/preview?overlay=merged")

    assert response.status_code == 200
    payload = response.json()
    assert payload["articles"][0]["title_bbox"] == [203, 947, 2282, 1056]
    assert payload["articles"][0]["article_bbox"] == [136, 947, 2361, 2642]
    assert payload["articles"][0]["images"][0]["bbox"] == [1257, 688, 1838, 1165]
    assert payload["articles"][0]["images"][0]["captions"][0]["bbox"] == [174, 1860, 843, 2070]


def test_page_preview_resolves_container_output_paths(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    db = ctx["db"]

    page = db.scalar(select(models.Page))
    assert page is not None

    output_root = tmp_path / "output"
    page.raw_vl_json_path = _as_container_output_path(Path(page.raw_vl_json_path), output_root)
    page.raw_structure_json_path = _as_container_output_path(Path(page.raw_structure_json_path), output_root)
    page.raw_fallback_json_path = _as_container_output_path(Path(page.raw_fallback_json_path), output_root)
    db.commit()

    response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/pages/{page.id}/preview?overlay=vl")

    assert response.status_code == 200
    payload = response.json()
    assert payload["raw_payload"]["parsing_res_list"][0]["content"] == "OCR Title"


def test_article_image_route_recrops_normalized_bboxes_for_large_pages(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    db = ctx["db"]

    page = db.scalar(select(models.Page))
    image = db.scalar(select(models.ArticleImage))
    assert page is not None
    assert image is not None

    page.width = 2480
    page.height = 3509
    image.image_bbox = [60, 240, 260, 520]
    page_image_path = Path(page.page_image_path)
    image_path = Path(image.image_path)
    Image.new("RGB", (2480, 3509), color="white").save(page_image_path)
    Image.new("RGB", (1, 1), color="black").save(image_path)
    output_root = tmp_path / "output"
    page.page_image_path = _as_container_output_path(page_image_path, output_root)
    image.image_path = _as_container_output_path(image_path, output_root)
    db.commit()

    response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/article-images/{image.id}")

    assert response.status_code == 200
    served = Image.open(BytesIO(response.content))
    assert served.size == (496, 983)


def test_article_image_route_falls_back_to_saved_crop_when_page_geometry_is_not_scalable(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    models = _fresh_import("app.db.models")
    db = ctx["db"]

    page = db.scalar(select(models.Page))
    image = db.scalar(select(models.ArticleImage))
    assert page is not None
    assert image is not None

    page.width = 1000
    page.height = 1400
    image.image_bbox = [1582, 323, 2272, 804]
    image_path = Path(image.image_path)
    Image.new("RGB", (83, 47), color="black").save(image_path)
    db.commit()

    response = ctx["client"].get(f"/api/v1/jobs/{ctx['job_key']}/article-images/{image.id}")

    assert response.status_code == 200
    served = Image.open(BytesIO(response.content))
    assert served.size == (83, 47)


def test_delete_job_route_removes_job_bundle_and_db_rows(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    job_root = ctx["bundle_dir"].parents[3]

    response = ctx["client"].post(f"/demo/jobs/{ctx['job_key']}/delete?view=render", follow_redirects=False)

    assert response.status_code == 303
    assert not job_root.exists()

    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    db.close()
    assert jobs == []


def test_reprocess_queues_new_job(tmp_path: Path, monkeypatch) -> None:
    ctx = _bootstrap_app(tmp_path, monkeypatch)
    scheduled: list[int] = []

    class StubScheduler:
        async def schedule(self, job_id: int) -> None:
            scheduled.append(job_id)

    monkeypatch.setattr(
        _fresh_import("app.web.demo_service"),
        "get_job_scheduler",
        lambda: StubScheduler(),
    )

    response = ctx["client"].post(
        f"/api/articles/{ctx['article_id']}/reprocess",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert "재처리 작업을 큐에 넣었습니다" in response.text
    db = ctx["session_module"].SessionLocal()
    jobs = list(db.scalars(select(_fresh_import("app.db.models").Job)))
    db.close()
    assert len(jobs) == 2
    assert scheduled
