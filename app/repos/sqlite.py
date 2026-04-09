from __future__ import annotations

from datetime import datetime
import sqlite3

from app.core.database import AppDatabase
from app.core.time import utc_now
from app.models import (
    ArticleRecord,
    ArticleStatus,
    DeliveryStatus,
    JobRecord,
    JobStatus,
    NewArticlePlaceholder,
    NewJob,
)


JOB_SELECT = """
SELECT
    id,
    source_path,
    file_name,
    file_hash,
    file_size,
    status,
    error_message,
    created_at,
    updated_at,
    queued_at,
    started_at,
    completed_at
FROM jobs
"""


ARTICLE_SELECT = """
SELECT
    id,
    job_id,
    sequence_no,
    status,
    delivery_status,
    delivery_attempts,
    title,
    body,
    created_at,
    updated_at,
    delivered_at
FROM articles
"""


def _parse_timestamp(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=row["id"],
        source_path=row["source_path"],
        file_name=row["file_name"],
        file_hash=row["file_hash"],
        file_size=row["file_size"],
        status=JobStatus(row["status"]),
        error_message=row["error_message"],
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        queued_at=_parse_timestamp(row["queued_at"]),
        started_at=_parse_timestamp(row["started_at"]),
        completed_at=_parse_timestamp(row["completed_at"]),
    )


def _article_from_row(row: sqlite3.Row) -> ArticleRecord:
    return ArticleRecord(
        id=row["id"],
        job_id=row["job_id"],
        sequence_no=row["sequence_no"],
        status=ArticleStatus(row["status"]),
        delivery_status=DeliveryStatus(row["delivery_status"]),
        delivery_attempts=row["delivery_attempts"],
        title=row["title"],
        body=row["body"],
        created_at=_parse_timestamp(row["created_at"]),
        updated_at=_parse_timestamp(row["updated_at"]),
        delivered_at=_parse_timestamp(row["delivered_at"]),
    )


class SQLiteJobRepository:
    def __init__(self, database: AppDatabase) -> None:
        self._database = database

    def list(self) -> list[JobRecord]:
        with self._database.session() as connection:
            rows = connection.execute(f"{JOB_SELECT} ORDER BY created_at DESC, id DESC").fetchall()
        return [_job_from_row(row) for row in rows]

    def get(self, job_id: int) -> JobRecord | None:
        with self._database.session() as connection:
            row = connection.execute(f"{JOB_SELECT} WHERE id = ?", (job_id,)).fetchone()
        return _job_from_row(row) if row else None

    def get_by_source_hash(self, source_path: str, file_hash: str) -> JobRecord | None:
        with self._database.session() as connection:
            row = connection.execute(
                f"{JOB_SELECT} WHERE source_path = ? AND file_hash = ?",
                (source_path, file_hash),
            ).fetchone()
        return _job_from_row(row) if row else None

    def create(self, payload: NewJob) -> JobRecord:
        timestamp = utc_now().isoformat()
        with self._database.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO jobs (
                    source_path,
                    file_name,
                    file_hash,
                    file_size,
                    status,
                    error_message,
                    created_at,
                    updated_at,
                    queued_at,
                    started_at,
                    completed_at
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL, NULL)
                """,
                (
                    payload.source_path,
                    payload.file_name,
                    payload.file_hash,
                    payload.file_size,
                    payload.status.value,
                    timestamp,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(f"{JOB_SELECT} WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read the inserted job record.")
        return _job_from_row(row)

    def update_status(
        self,
        job_id: int,
        status: JobStatus,
        *,
        error_message: str | None = None,
    ) -> JobRecord | None:
        current = self.get(job_id)
        if current is None:
            return None

        now = utc_now().isoformat()
        started_at = current.started_at.isoformat() if current.started_at else None
        completed_at = current.completed_at.isoformat() if current.completed_at else None

        if status == JobStatus.PROCESSING and started_at is None:
            started_at = now
        if status in {JobStatus.COMPLETED, JobStatus.FAILED}:
            completed_at = now
        if status in {JobStatus.QUEUED, JobStatus.PROCESSING}:
            completed_at = None

        with self._database.session() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?, error_message = ?, updated_at = ?, started_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    error_message,
                    now,
                    started_at,
                    completed_at,
                    job_id,
                ),
            )
        return self.get(job_id)


class SQLiteArticleRepository:
    def __init__(self, database: AppDatabase) -> None:
        self._database = database

    def list(self, *, job_id: int | None = None) -> list[ArticleRecord]:
        query = ARTICLE_SELECT
        parameters: tuple[object, ...] = ()
        if job_id is not None:
            query += " WHERE job_id = ?"
            parameters = (job_id,)
        query += " ORDER BY created_at DESC, id DESC"
        with self._database.session() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [_article_from_row(row) for row in rows]

    def get(self, article_id: int) -> ArticleRecord | None:
        with self._database.session() as connection:
            row = connection.execute(f"{ARTICLE_SELECT} WHERE id = ?", (article_id,)).fetchone()
        return _article_from_row(row) if row else None

    def get_by_job_sequence(self, job_id: int, sequence_no: int) -> ArticleRecord | None:
        with self._database.session() as connection:
            row = connection.execute(
                f"{ARTICLE_SELECT} WHERE job_id = ? AND sequence_no = ?",
                (job_id, sequence_no),
            ).fetchone()
        return _article_from_row(row) if row else None

    def create_placeholder(self, payload: NewArticlePlaceholder) -> ArticleRecord:
        timestamp = utc_now().isoformat()
        with self._database.session() as connection:
            cursor = connection.execute(
                """
                INSERT INTO articles (
                    job_id,
                    sequence_no,
                    status,
                    delivery_status,
                    delivery_attempts,
                    title,
                    body,
                    created_at,
                    updated_at,
                    delivered_at
                )
                VALUES (?, ?, ?, ?, 0, NULL, NULL, ?, ?, NULL)
                """,
                (
                    payload.job_id,
                    payload.sequence_no,
                    payload.status.value,
                    payload.delivery_status.value,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(f"{ARTICLE_SELECT} WHERE id = ?", (cursor.lastrowid,)).fetchone()
        if row is None:
            raise RuntimeError("Failed to read the inserted article record.")
        return _article_from_row(row)

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
        current = self.get(article_id)
        if current is None:
            return None

        now = utc_now().isoformat()
        resolved_delivery_status = delivery_status or current.delivery_status
        resolved_delivery_attempts = (
            delivery_attempts if delivery_attempts is not None else current.delivery_attempts
        )
        resolved_title = title if title is not None else current.title
        resolved_body = body if body is not None else current.body
        delivered_at = current.delivered_at.isoformat() if current.delivered_at else None

        if resolved_delivery_status == DeliveryStatus.SENT:
            delivered_at = now
        if resolved_delivery_status != DeliveryStatus.SENT:
            delivered_at = None

        with self._database.session() as connection:
            connection.execute(
                """
                UPDATE articles
                SET
                    status = ?,
                    delivery_status = ?,
                    delivery_attempts = ?,
                    title = ?,
                    body = ?,
                    updated_at = ?,
                    delivered_at = ?
                WHERE id = ?
                """,
                (
                    status.value,
                    resolved_delivery_status.value,
                    resolved_delivery_attempts,
                    resolved_title,
                    resolved_body,
                    now,
                    delivered_at,
                    article_id,
                ),
            )
        return self.get(article_id)
