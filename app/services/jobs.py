from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Protocol

from app.models import (
    ArticleRecord,
    ArticleStatus,
    DeliveryStatus,
    JobRecord,
    JobRegistrationResult,
    JobStatus,
    NewArticlePlaceholder,
    NewJob,
)
from app.repos.interfaces import ArticleRepository, JobRepository


class JobsPort(Protocol):
    def register_pdf(self, source_path: Path, file_hash: str, file_size: int) -> JobRegistrationResult:
        ...

    def list_jobs(self) -> list[JobRecord]:
        ...

    def get_job(self, job_id: int) -> JobRecord | None:
        ...

    def update_job_status(
        self,
        job_id: int,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> JobRecord | None:
        ...

    def list_articles(self, *, job_id: int | None = None) -> list[ArticleRecord]:
        ...

    def get_article(self, article_id: int) -> ArticleRecord | None:
        ...

    def ensure_article_placeholders(self, job_id: int, article_count: int) -> list[ArticleRecord]:
        ...

    def update_article_status(
        self,
        article_id: int,
        status: ArticleStatus,
        *,
        delivery_status: DeliveryStatus | None = None,
        delivery_attempts: int | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> ArticleRecord | None:
        ...


class JobsService:
    def __init__(
        self,
        *,
        job_repository: JobRepository,
        article_repository: ArticleRepository,
    ) -> None:
        self._job_repository = job_repository
        self._article_repository = article_repository

    def register_pdf(self, source_path: Path, file_hash: str, file_size: int) -> JobRegistrationResult:
        resolved_path = str(source_path.resolve())
        existing = self._job_repository.get_by_source_hash(resolved_path, file_hash)
        if existing is not None:
            return JobRegistrationResult(job=existing, created=False)

        try:
            created = self._job_repository.create(
                NewJob(
                    source_path=resolved_path,
                    file_name=source_path.name,
                    file_hash=file_hash,
                    file_size=file_size,
                    status=JobStatus.QUEUED,
                )
            )
        except sqlite3.IntegrityError:
            existing = self._job_repository.get_by_source_hash(resolved_path, file_hash)
            if existing is None:
                raise
            return JobRegistrationResult(job=existing, created=False)

        return JobRegistrationResult(job=created, created=True)

    def list_jobs(self) -> list[JobRecord]:
        return self._job_repository.list()

    def get_job(self, job_id: int) -> JobRecord | None:
        return self._job_repository.get(job_id)

    def update_job_status(
        self,
        job_id: int,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> JobRecord | None:
        return self._job_repository.update_status(job_id, status, error_message=error_message)

    def list_articles(self, *, job_id: int | None = None) -> list[ArticleRecord]:
        return self._article_repository.list(job_id=job_id)

    def get_article(self, article_id: int) -> ArticleRecord | None:
        return self._article_repository.get(article_id)

    def ensure_article_placeholders(self, job_id: int, article_count: int) -> list[ArticleRecord]:
        if article_count < 0:
            raise ValueError("article_count must be non-negative")

        placeholders: list[ArticleRecord] = []
        for sequence_no in range(1, article_count + 1):
            existing = self._article_repository.get_by_job_sequence(job_id, sequence_no)
            if existing is not None:
                placeholders.append(existing)
                continue
            placeholders.append(
                self._article_repository.create_placeholder(
                    NewArticlePlaceholder(job_id=job_id, sequence_no=sequence_no)
                )
            )
        return placeholders

    def update_article_status(
        self,
        article_id: int,
        status: ArticleStatus,
        *,
        delivery_status: DeliveryStatus | None = None,
        delivery_attempts: int | None = None,
        title: str | None = None,
        body: str | None = None,
    ) -> ArticleRecord | None:
        return self._article_repository.update_status(
            article_id,
            status,
            delivery_status=delivery_status,
            delivery_attempts=delivery_attempts,
            title=title,
            body=body,
        )
