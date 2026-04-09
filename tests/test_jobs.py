from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.config import Settings
from app.core.database import AppDatabase
from app.models import ArticleStatus, DeliveryStatus, JobStatus
from app.repos.sqlite import SQLiteArticleRepository, SQLiteJobRepository
from app.services.jobs import JobsService


def build_settings(tmp_path: Path) -> Settings:
    data_dir = tmp_path / "data"
    watch_dir = data_dir / "watch"
    return Settings(
        watch_dir=watch_dir,
        data_dir=data_dir,
        poll_interval_sec=0.1,
        stable_scan_count=2,
        llm_base_url=None,
        llm_model=None,
        llm_api_key=None,
        llm_timeout_sec=30.0,
        target_api_base_url=None,
        target_api_token=None,
        target_api_timeout_sec=30.0,
        auto_deliver=False,
        delivery_retry_max=3,
        database_path=data_dir / "app.sqlite3",
    )


def build_service(settings: Settings) -> JobsService:
    settings.ensure_directories()
    database = AppDatabase(settings.database_path)
    database.initialize()
    return JobsService(
        job_repository=SQLiteJobRepository(database),
        article_repository=SQLiteArticleRepository(database),
    )


def test_job_and_article_state_persist_across_service_rebuilds(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    service = build_service(settings)

    pdf_path = settings.watch_dir / "briefing.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nmission")

    registration = service.register_pdf(pdf_path, "hash-001", pdf_path.stat().st_size)
    assert registration.created is True

    job = service.update_job_status(registration.job.id, JobStatus.PROCESSING)
    assert job is not None
    assert job.status == JobStatus.PROCESSING
    assert job.started_at is not None

    placeholders = service.ensure_article_placeholders(job.id, 2)
    assert [placeholder.sequence_no for placeholder in placeholders] == [1, 2]

    updated_article = service.update_article_status(
        placeholders[0].id,
        ArticleStatus.READY,
        delivery_status=DeliveryStatus.PENDING,
        title="Alpha",
        body="Bravo",
    )
    assert updated_article is not None
    assert updated_article.status == ArticleStatus.READY
    assert updated_article.title == "Alpha"

    rebuilt_service = build_service(settings)
    jobs = rebuilt_service.list_jobs()
    articles = rebuilt_service.list_articles(job_id=job.id)

    assert len(jobs) == 1
    assert jobs[0].status == JobStatus.PROCESSING
    assert len(articles) == 2
    assert {article.sequence_no for article in articles} == {1, 2}


def test_register_pdf_returns_existing_job_for_duplicate_source_hash(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    service = build_service(settings)

    pdf_path = settings.watch_dir / "intel.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nduplicate")
    file_size = pdf_path.stat().st_size

    first = service.register_pdf(pdf_path, "same-hash", file_size)
    second = service.register_pdf(pdf_path, "same-hash", file_size)

    assert first.created is True
    assert second.created is False
    assert first.job.id == second.job.id
    assert len(service.list_jobs()) == 1


def test_app_boots_with_empty_database_and_serves_lookup_apis(tmp_path: Path) -> None:
    settings = build_settings(tmp_path)
    app = create_app(settings=settings)

    with TestClient(app) as client:
        health = client.get("/healthz")
        runtime_health = client.get("/api/v1/health")
        jobs = client.get("/api/jobs")
        articles = client.get("/api/articles")

    assert health.status_code == 200
    assert runtime_health.status_code == 200
    assert jobs.status_code == 200
    assert articles.status_code == 200
    assert settings.data_dir.exists()
    assert settings.watch_dir.exists()
    assert settings.database_path.exists()
    assert jobs.json()["count"] == 0
    assert articles.json()["count"] == 0
