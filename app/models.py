from __future__ import annotations

from datetime import datetime
from enum import Enum
try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    class StrEnum(str, Enum):
        """Backport for environments running Python 3.10."""

from pydantic import BaseModel, ConfigDict


class JobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ArticleStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    DELIVERED = "delivered"
    FAILED = "failed"


class DeliveryStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class JobRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_path: str
    file_name: str
    file_hash: str
    file_size: int
    status: JobStatus
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    queued_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ArticleRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    sequence_no: int
    status: ArticleStatus
    delivery_status: DeliveryStatus
    delivery_attempts: int
    title: str | None = None
    body: str | None = None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None = None


class NewJob(BaseModel):
    source_path: str
    file_name: str
    file_hash: str
    file_size: int
    status: JobStatus = JobStatus.QUEUED


class NewArticlePlaceholder(BaseModel):
    job_id: int
    sequence_no: int
    status: ArticleStatus = ArticleStatus.PENDING
    delivery_status: DeliveryStatus = DeliveryStatus.PENDING


class JobRegistrationResult(BaseModel):
    job: JobRecord
    created: bool


class JobListResponse(BaseModel):
    items: list[JobRecord]
    count: int


class ArticleListResponse(BaseModel):
    items: list[ArticleRecord]
    count: int


class HealthResponse(BaseModel):
    status: str
    database_path: str
    watch_dir: str
    watcher_running: bool
    watcher_last_error: str | None = None
    auto_deliver: bool
