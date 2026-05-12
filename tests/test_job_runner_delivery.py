from __future__ import annotations

import importlib
import json
import sys
from datetime import date
from pathlib import Path

from PIL import Image


def _fresh_import(module_name: str):
    return importlib.import_module(module_name)


def _clear_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_job_runner_delivers_each_page_as_it_finishes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'news_ocr.db').as_posix()}")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))
    monkeypatch.setenv("TARGET_API_BASE_URL", "http://env.test/news")
    _clear_app_modules()

    base = _fresh_import("app.db.base")
    session_module = _fresh_import("app.db.session")
    models = _fresh_import("app.db.models")
    domain = _fresh_import("app.domain.types")
    pdf_renderer = _fresh_import("app.services.pdf_renderer")
    relevance_module = _fresh_import("app.services.relevance_scorer")
    job_runner_module = _fresh_import("app.services.job_runner")
    schema_module = _fresh_import("app.schemas.job")
    delivery_module = _fresh_import("app.services.news_delivery")

    base.Base.metadata.create_all(bind=session_module.engine)
    db = session_module.SessionLocal()

    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)
    source_pdf = input_dir / "daily.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 demo")

    captured_bodies: list[list[dict[str, object]]] = []

    class DummyResponse:
        status_code = 200
        text = "ok"

        def json(self):
            return {}

    def fake_post(url, *, data=None, files=None, headers=None, timeout=None):
        assert url == "http://env.test/news"
        for field_name, file_payload in files:
            if field_name == "body":
                captured_bodies.append(json.loads(file_payload[1]))
                break
        return DummyResponse()

    class StubRenderer:
        def render(self, pdf_path: Path, output_dir: Path):
            output_dir.mkdir(parents=True, exist_ok=True)
            rendered = []
            for page_number in (1, 2):
                image_path = output_dir / f"page_{page_number:04d}.png"
                Image.new("RGB", (640, 900), color="white").save(image_path)
                rendered.append(pdf_renderer.RenderedPage(page_number, image_path, 640, 900))
            return rendered

    class StubOCREngine:
        def parse_page(self, image_path: Path, page_number: int, width: int, height: int, stage_callback=None):
            if stage_callback is not None:
                stage_callback("ocr_vl", "completed", f"page {page_number}")
            return domain.PageLayout(
                page_number=page_number,
                width=width,
                height=height,
                image_path=image_path,
                blocks=[
                    domain.OCRBlock(
                        block_id=f"p{page_number}-body",
                        page_number=page_number,
                        label=domain.BlockLabel.TEXT,
                        bbox=[10, 10, 600, 300],
                        text=f"{page_number}면 국회 기사 본문입니다",
                        confidence=0.95,
                    )
                ],
                raw_vl={},
                raw_structure={},
                raw_fallback_ocr={},
            )

    class StubClusterer:
        def cluster_page(self, page):
            article = domain.ArticleCandidate(
                page_number=page.page_number,
                column_index=0,
                title=f"Page {page.page_number} headline",
                body_text=f"Page {page.page_number} body",
                title_bbox=[10, 10, 600, 80],
                article_bbox=[10, 10, 600, 300],
                confidence=0.95,
                layout_type="single",
                metadata={"source_metadata": {"publication": "한겨레", "issue_date": "2026-05-12"}},
            )
            return [article], []

    class StubRelevanceScorer:
        model_name = "stub"

        def score_page_articles(self, *, pdf_name: str, page_number: int, articles):
            return relevance_module.PageRelevanceResult(
                assessments={
                    1: relevance_module.RelevanceAssessment(
                        article_order=1,
                        score=0.9,
                        reason="test",
                        label="relevant",
                        source="test",
                        corrected_title=f"Corrected page {page_number}",
                        corrected_body_text=f"Corrected body {page_number}",
                    )
                },
                source="test",
                model="stub",
            )

    monkeypatch.setattr(delivery_module.httpx, "post", fake_post)

    runner = job_runner_module.JobRunner(db)
    runner.renderer = StubRenderer()
    runner.ocr_engine = StubOCREngine()
    runner.clusterer = StubClusterer()
    runner.relevance_scorer = StubRelevanceScorer()

    job = runner.create_job(
        schema_module.JobRunDailyRequest(
            source_dir=str(input_dir),
            date=date(2026, 5, 12),
            force_reprocess=True,
        )
    )
    db.commit()
    runner.execute(job.id)

    assert len(captured_bodies) == 2
    assert [len(body) for body in captured_bodies] == [1, 1]
    assert captured_bodies[0][0]["title"] == "Corrected page 1"
    assert captured_bodies[1][0]["title"] == "Corrected page 2"

    refreshed_job = db.get(models.Job, job.id)
    assert refreshed_job is not None
    assert refreshed_job.status == "completed"
    assert refreshed_job.total_articles == 2

    logs = [
        (step_name, status, message)
        for step_name, status, message in db.execute(
            models.ProcessingLog.__table__.select()
            .with_only_columns(
                models.ProcessingLog.step_name,
                models.ProcessingLog.status,
                models.ProcessingLog.message,
            )
            .order_by(models.ProcessingLog.id)
        )
    ]
    running_deliveries = [message for step_name, status, message in logs if step_name == "deliver" and status == "running"]
    assert running_deliveries == [
        "sending page=1 articles=1 to http://env.test/news",
        "sending page=2 articles=1 to http://env.test/news",
    ]
