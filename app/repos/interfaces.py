from __future__ import annotations

from typing import Protocol

from app.models import (
    ArticleRecord,
    ArticleStatus,
    DeliveryStatus,
    JobRecord,
    JobStatus,
    NewArticlePlaceholder,
    NewJob,
)


class JobRepository(Protocol):
    def list(self) -> list[JobRecord]:
        ...

    def get(self, job_id: int) -> JobRecord | None:
        ...

    def get_by_source_hash(self, source_path: str, file_hash: str) -> JobRecord | None:
        ...

    def create(self, payload: NewJob) -> JobRecord:
        ...

    def update_status(
        self,
        job_id: int,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> JobRecord | None:
        ...


class ArticleRepository(Protocol):
    def list(self, *, job_id: int | None = None) -> list[ArticleRecord]:
        ...

    def get(self, article_id: int) -> ArticleRecord | None:
        ...

    def get_by_job_sequence(self, job_id: int, sequence_no: int) -> ArticleRecord | None:
        ...

    def create_placeholder(self, payload: NewArticlePlaceholder) -> ArticleRecord:
        ...

    def update_status(
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
