from __future__ import annotations

import base64
from pathlib import Path
import time

from app.config import Settings
from app.core.database import AppDatabase
from app.repos.sqlite import SQLiteArticleRepository, SQLiteJobRepository
from app.services.jobs import JobsService
from app.services.watcher import PollingWatcher

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)


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


def build_watcher(tmp_path: Path) -> tuple[JobsService, PollingWatcher, Settings]:
    settings = build_settings(tmp_path)
    settings.ensure_directories()
    database = AppDatabase(settings.database_path)
    database.initialize()
    jobs = JobsService(
        job_repository=SQLiteJobRepository(database),
        article_repository=SQLiteArticleRepository(database),
    )
    watcher = PollingWatcher(settings=settings, jobs=jobs)
    return jobs, watcher, settings


def test_watcher_requires_two_stable_scans_before_job_creation(tmp_path: Path) -> None:
    jobs, watcher, settings = build_watcher(tmp_path)
    pdf_path = settings.watch_dir / "report.pdf"

    pdf_path.write_bytes(b"%PDF-1.4\npart-1")
    watcher.scan_once()

    time.sleep(0.01)
    pdf_path.write_bytes(b"%PDF-1.4\npart-1-part-2")
    watcher.scan_once()
    watcher.scan_once()

    assert jobs.list_jobs() == []

    watcher.scan_once()
    saved_jobs = jobs.list_jobs()

    assert len(saved_jobs) == 1
    assert saved_jobs[0].source_path == str(pdf_path.resolve())


def test_watcher_skips_duplicate_job_after_same_file_is_re_copied(tmp_path: Path) -> None:
    jobs, watcher, settings = build_watcher(tmp_path)
    pdf_path = settings.watch_dir / "signal.pdf"
    payload = b"%PDF-1.4\nsignal"

    pdf_path.write_bytes(payload)
    watcher.scan_once()
    watcher.scan_once()
    watcher.scan_once()
    assert len(jobs.list_jobs()) == 1

    time.sleep(0.01)
    pdf_path.write_bytes(payload)
    watcher.scan_once()
    watcher.scan_once()
    watcher.scan_once()

    saved_jobs = jobs.list_jobs()
    assert len(saved_jobs) == 1
    assert saved_jobs[0].file_name == "signal.pdf"


def test_watcher_registers_image_inputs(tmp_path: Path) -> None:
    jobs, watcher, settings = build_watcher(tmp_path)
    image_path = settings.watch_dir / "encrypted-page.png"

    image_path.write_bytes(PNG_1X1)
    watcher.scan_once()
    watcher.scan_once()
    watcher.scan_once()

    saved_jobs = jobs.list_jobs()
    assert len(saved_jobs) == 1
    assert saved_jobs[0].source_path == str(image_path.resolve())
    assert saved_jobs[0].file_name == "encrypted-page.png"
