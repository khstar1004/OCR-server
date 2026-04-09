from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    source_dir: Mapped[str] = mapped_column(String(1024))
    requested_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    callback_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    force_reprocess: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    total_files: Mapped[int] = mapped_column(Integer, default=0)
    success_files: Mapped[int] = mapped_column(Integer, default=0)
    failed_files: Mapped[int] = mapped_column(Integer, default=0)
    total_articles: Mapped[int] = mapped_column(Integer, default=0)

    pdf_files: Mapped[list["PdfFile"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    logs: Mapped[list["ProcessingLog"]] = relationship(back_populates="job", cascade="all, delete-orphan")


class PdfFile(Base):
    __tablename__ = "pdf_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    file_name: Mapped[str] = mapped_column(String(512))
    file_path: Mapped[str] = mapped_column(String(2048))
    file_hash: Mapped[str] = mapped_column(String(128), index=True)
    file_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="queued")
    skip_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    job: Mapped[Job] = relationship(back_populates="pdf_files")
    pages: Mapped[list["Page"]] = relationship(back_populates="pdf_file", cascade="all, delete-orphan")
    logs: Mapped[list["ProcessingLog"]] = relationship(back_populates="pdf_file", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pdf_file_id: Mapped[int] = mapped_column(ForeignKey("pdf_files.id"), index=True)
    page_number: Mapped[int] = mapped_column(Integer)
    page_image_path: Mapped[str] = mapped_column(String(2048))
    raw_vl_json_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    raw_structure_json_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    raw_fallback_json_path: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(32), default="queued")
    unassigned_payload: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    pdf_file: Mapped[PdfFile] = relationship(back_populates="pages")
    articles: Mapped[list["Article"]] = relationship(back_populates="page", cascade="all, delete-orphan")
    logs: Mapped[list["ProcessingLog"]] = relationship(back_populates="page", cascade="all, delete-orphan")


class Article(Base):
    __tablename__ = "articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pdf_file_id: Mapped[int] = mapped_column(ForeignKey("pdf_files.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    article_order: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    body_text: Mapped[str] = mapped_column(Text, default="")
    title_bbox: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    article_bbox: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    layout_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    page: Mapped[Page] = relationship(back_populates="articles")
    images: Mapped[list["ArticleImage"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class ArticleImage(Base):
    __tablename__ = "article_images"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id"), index=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"), index=True)
    image_order: Mapped[int] = mapped_column(Integer)
    image_path: Mapped[str] = mapped_column(String(2048))
    image_bbox: Mapped[list[int]] = mapped_column(JSON)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    article: Mapped[Article] = relationship(back_populates="images")


class ProcessingLog(Base):
    __tablename__ = "processing_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    pdf_file_id: Mapped[int | None] = mapped_column(ForeignKey("pdf_files.id"), nullable=True)
    page_id: Mapped[int | None] = mapped_column(ForeignKey("pages.id"), nullable=True)
    step_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[Job] = relationship(back_populates="logs")
    pdf_file: Mapped[PdfFile | None] = relationship(back_populates="logs")
    page: Mapped[Page | None] = relationship(back_populates="logs")
