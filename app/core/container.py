from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from app.config import Settings, get_settings
from app.core.database import AppDatabase
from app.repos.sqlite import SQLiteArticleRepository, SQLiteJobRepository
from app.services.jobs import JobsPort, JobsService
from app.services.watcher import PollingWatcher


@dataclass(slots=True)
class ApplicationContainer:
    settings: Settings
    database: AppDatabase
    jobs: JobsPort
    watcher: PollingWatcher

    @classmethod
    def build(cls, settings: Settings | None = None) -> ApplicationContainer:
        resolved_settings = settings or get_settings()
        database = AppDatabase(resolved_settings.database_path)
        jobs = JobsService(
            job_repository=SQLiteJobRepository(database),
            article_repository=SQLiteArticleRepository(database),
        )
        watcher = PollingWatcher(settings=resolved_settings, jobs=jobs)
        return cls(settings=resolved_settings, database=database, jobs=jobs, watcher=watcher)

    def initialize(self) -> None:
        self.settings.ensure_directories()
        self.database.initialize()


def get_container(request: Request) -> ApplicationContainer:
    return request.app.state.container


def get_jobs_port(request: Request) -> JobsPort:
    return get_container(request).jobs
