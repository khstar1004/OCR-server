from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.db.models import Article, Job, Page, PdfFile, ProcessingLog
from app.schemas.job import (
    ArticleCaptionResponse,
    ArticleImageResponse,
    ArticleResponse,
    ArticleSourceMetadataResponse,
    FileResultResponse,
    JobDetailResponse,
    JobResultResponse,
    JobStageResponse,
    JobStatusResponse,
    PageProgressResponse,
    PdfProgressResponse,
    ProcessingLogEntryResponse,
)
from app.services.captions import caption_entries_by_image_order
from app.services.storage import OutputStorage


def get_pipeline_stage_labels() -> tuple[tuple[str, str], ...]:
    settings = get_settings()
    source = settings.chandra_model_dir or settings.chandra_model_id
    model_name = Path(source).name if source else "chandra-ocr-2"
    primary_label = f"Chandra OCR ({model_name})"
    structure_label = "보조 레이아웃 (미사용)"
    fallback_label = "Fallback OCR (미사용)"

    return (
        ("scan", "PDF 탐색 / 해시"),
        ("render", "PDF 렌더링"),
        ("ocr_vl", primary_label),
        ("ocr_structure", structure_label),
        ("ocr_fallback", fallback_label),
        ("ocr_retry", "저품질 재시도"),
        ("cluster", "기사 군집화"),
        ("relevance", "국회 유사도 판단 / 문맥 보정"),
        ("crop", "이미지 crop"),
        ("persist", "DB 저장"),
    )


def build_job_status(db: Session, job: Job) -> JobStatusResponse:
    processed = db.scalar(
        select(func.count(PdfFile.id)).where(
            PdfFile.job_id == job.id,
            PdfFile.status.in_(["completed", "completed_with_errors", "failed", "skipped"]),
        )
    )
    return JobStatusResponse(
        job_id=job.job_key,
        status=job.status,
        total_pdfs=job.total_files,
        processed_pdfs=int(processed or 0),
        total_articles=job.total_articles,
    )


def build_job_detail(db: Session, job: Job) -> JobDetailResponse:
    pipeline_stage_labels = get_pipeline_stage_labels()
    valid_stage_keys = [stage_key for stage_key, _ in pipeline_stage_labels]
    pdf_files = list(db.scalars(select(PdfFile).where(PdfFile.job_id == job.id).order_by(PdfFile.id)))
    processed = db.scalar(
        select(func.count(PdfFile.id)).where(
            PdfFile.job_id == job.id,
            PdfFile.status.in_(["completed", "completed_with_errors", "failed", "skipped"]),
        )
    )
    page_stats = {
        pdf_file_id: {
            "page_count": int(page_count or 0),
            "parsed_pages": int(parsed_pages or 0),
            "failed_pages": int(failed_pages or 0),
        }
        for pdf_file_id, page_count, parsed_pages, failed_pages in db.execute(
            select(
                Page.pdf_file_id,
                func.count(Page.id),
                func.count(Page.id).filter(Page.parse_status == "parsed"),
                func.count(Page.id).filter(Page.parse_status == "failed"),
            )
            .join(PdfFile, PdfFile.id == Page.pdf_file_id)
            .where(PdfFile.job_id == job.id)
            .group_by(Page.pdf_file_id)
        )
    }
    article_counts = {
        pdf_file_id: int(article_count or 0)
        for pdf_file_id, article_count in db.execute(
            select(Article.pdf_file_id, func.count(Article.id))
            .join(PdfFile, PdfFile.id == Article.pdf_file_id)
            .where(PdfFile.job_id == job.id)
            .group_by(Article.pdf_file_id)
        )
    }
    page_rows = list(
        db.scalars(
            select(Page)
            .join(PdfFile, PdfFile.id == Page.pdf_file_id)
            .where(PdfFile.job_id == job.id)
            .order_by(Page.pdf_file_id, Page.page_number)
        )
    )
    page_article_counts = {
        page_id: int(article_count or 0)
        for page_id, article_count in db.execute(
            select(Article.page_id, func.count(Article.id))
            .join(Page, Page.id == Article.page_id)
            .join(PdfFile, PdfFile.id == Page.pdf_file_id)
            .where(PdfFile.job_id == job.id)
            .group_by(Article.page_id)
        )
    }
    pages_by_pdf: dict[int, list[PageProgressResponse]] = {}
    for page in page_rows:
        pages_by_pdf.setdefault(page.pdf_file_id, []).append(
            PageProgressResponse(
                page_id=page.id,
                page_number=page.page_number,
                status=page.parse_status,
                article_count=page_article_counts.get(page.id, 0),
            )
        )
    stage_logs = list(
        db.scalars(
            select(ProcessingLog)
            .where(ProcessingLog.job_id == job.id, ProcessingLog.step_name.in_(valid_stage_keys))
            .order_by(ProcessingLog.created_at, ProcessingLog.id)
        )
    )
    recent_logs = list(
        reversed(
            list(
                db.scalars(
                    select(ProcessingLog)
                    .where(ProcessingLog.job_id == job.id)
                    .order_by(ProcessingLog.created_at.desc(), ProcessingLog.id.desc())
                    .limit(200)
                )
            )
        )
    )
    page_ids = [log.page_id for log in recent_logs if log.page_id is not None]
    page_numbers = (
        {
            page_id: page_number
            for page_id, page_number in db.execute(
                select(Page.id, Page.page_number).where(Page.id.in_(page_ids))
            )
        }
        if page_ids
        else {}
    )
    pdf_names = {pdf_file.id: pdf_file.file_name for pdf_file in pdf_files}
    processed_count = int(processed or 0)

    if job.total_files > 0:
        progress_percent = round((processed_count / job.total_files) * 100, 1)
    elif job.status in {"completed", "completed_with_errors"}:
        progress_percent = 100.0
    else:
        progress_percent = 0.0

    return JobDetailResponse(
        job_id=job.job_key,
        status=job.status,
        source_dir=job.source_dir,
        requested_date=job.requested_date,
        requested_at=job.requested_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        total_pdfs=job.total_files,
        processed_pdfs=processed_count,
        success_pdfs=job.success_files,
        failed_pdfs=job.failed_files,
        total_articles=job.total_articles,
        progress_percent=progress_percent,
        stages=_build_stage_progress(stage_logs, pipeline_stage_labels),
        pdf_files=[
            PdfProgressResponse(
                pdf_file_id=pdf_file.id,
                file_name=pdf_file.file_name,
                status=pdf_file.status,
                page_count=max(
                    int(page_stats.get(pdf_file.id, {}).get("page_count", 0)),
                    int(pdf_file.page_count or 0),
                ),
                parsed_pages=int(page_stats.get(pdf_file.id, {}).get("parsed_pages", 0)),
                failed_pages=int(page_stats.get(pdf_file.id, {}).get("failed_pages", 0)),
                article_count=article_counts.get(pdf_file.id, 0),
                skip_reason=pdf_file.skip_reason,
                processed_at=pdf_file.processed_at,
                pages=pages_by_pdf.get(pdf_file.id, []),
            )
            for pdf_file in pdf_files
        ],
        recent_logs=[
            ProcessingLogEntryResponse(
                created_at=log.created_at,
                step_name=log.step_name,
                status=log.status,
                message=log.message,
                pdf_file=pdf_names.get(log.pdf_file_id),
                page_number=page_numbers.get(log.page_id) if log.page_id is not None else None,
            )
            for log in recent_logs
        ],
    )


def build_job_result(db: Session, job: Job) -> JobResultResponse:
    storage = OutputStorage()
    pdf_files = list(db.scalars(select(PdfFile).where(PdfFile.job_id == job.id).order_by(PdfFile.id)))
    files: list[FileResultResponse] = []
    for pdf_file in pdf_files:
        articles = list(
            db.scalars(
                select(Article)
                .where(Article.pdf_file_id == pdf_file.id)
                .options(selectinload(Article.images), selectinload(Article.page))
                .order_by(Article.page_id, Article.article_order)
            )
        )
        files.append(
            FileResultResponse(
                pdf_file=pdf_file.file_name,
                pages=pdf_file.page_count or 0,
                articles=[_build_article_response(storage, job.job_key, pdf_file.file_name, article) for article in articles],
            )
        )
    return JobResultResponse(job_id=job.job_key, status=job.status, files=files)


def _build_article_response(storage: OutputStorage, job_key: str, pdf_name: str, article: Article) -> ArticleResponse:
    bundle_dir = storage.resolve_article_bundle_path(
        job_key,
        pdf_name,
        article.page.page_number,
        article.article_order,
        article.title,
    )
    metadata = storage.load_article_metadata(bundle_dir)
    caption_map = caption_entries_by_image_order(metadata)
    corrected_title = _clean_text(metadata.get("corrected_title")) or None
    corrected_body_text = _clean_text(metadata.get("corrected_body_text")) or None
    final_title = corrected_title or article.title
    final_body = corrected_body_text or article.body_text
    return ArticleResponse(
        article_id=article.id,
        page_number=article.page.page_number,
        article_order=article.article_order,
        title=final_title,
        body_text=final_body,
        original_title=article.title,
        original_body_text=article.body_text,
        corrected_title=corrected_title,
        corrected_body_text=corrected_body_text,
        correction_source=_clean_text(metadata.get("correction_source")) or None,
        correction_model=_clean_text(metadata.get("correction_model")) or None,
        title_bbox=article.title_bbox,
        article_bbox=article.article_bbox,
        relevance_score=_as_float(metadata.get("relevance_score")),
        relevance_reason=_clean_text(metadata.get("relevance_reason")) or None,
        relevance_label=_clean_text(metadata.get("relevance_label")) or None,
        relevance_model=_clean_text(metadata.get("relevance_model")) or None,
        relevance_source=_clean_text(metadata.get("relevance_source")) or None,
        source_metadata=_build_source_metadata(metadata.get("source_metadata")),
        images=[
            ArticleImageResponse(
                image_id=image.id,
                image_path=image.image_path,
                bbox=image.image_bbox,
                captions=[
                    ArticleCaptionResponse(
                        text=str(caption.get("text") or "").strip(),
                        bbox=caption.get("bbox"),
                        confidence=_as_float(caption.get("confidence")),
                    )
                    for caption in caption_map.get(image.image_order, [])
                    if str(caption.get("text") or "").strip()
                ],
            )
            for image in sorted(article.images, key=lambda item: item.image_order)
        ],
        bundle_dir=str(bundle_dir),
        markdown_path=str(bundle_dir / "article.md"),
        metadata_path=str(bundle_dir / "article.json"),
    )


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_bbox(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        return None


def _build_source_metadata(value: Any) -> ArticleSourceMetadataResponse | None:
    if not isinstance(value, dict):
        return None
    payload = ArticleSourceMetadataResponse(
        publication=_clean_text(value.get("publication")) or None,
        issue_date=_clean_text(value.get("issue_date")) or None,
        issue_date_text=_clean_text(value.get("issue_date_text")) or None,
        issue_weekday=_clean_text(value.get("issue_weekday")) or None,
        issue_page=_clean_text(value.get("issue_page")) or None,
        issue_page_label=_clean_text(value.get("issue_page_label")) or None,
        issue_section=_clean_text(value.get("issue_section")) or None,
        raw_publication_text=_clean_text(value.get("raw_publication_text")) or None,
        raw_issue_text=_clean_text(value.get("raw_issue_text")) or None,
        publication_bbox=_clean_bbox(value.get("publication_bbox")),
        issue_bbox=_clean_bbox(value.get("issue_bbox")),
    )
    if not any(
        [
            payload.publication,
            payload.issue_date,
            payload.issue_date_text,
            payload.issue_weekday,
            payload.issue_page,
            payload.issue_page_label,
            payload.issue_section,
            payload.raw_publication_text,
            payload.raw_issue_text,
            payload.publication_bbox,
            payload.issue_bbox,
        ]
    ):
        return None
    return payload


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _build_stage_progress(
    logs: Iterable[ProcessingLog],
    pipeline_stage_labels: tuple[tuple[str, str], ...] | None = None,
) -> list[JobStageResponse]:
    pipeline_stage_labels = pipeline_stage_labels or get_pipeline_stage_labels()
    latest_by_stage: dict[str, ProcessingLog] = {}
    failed_stage_keys: set[str] = set()
    valid_stage_keys = {stage_key for stage_key, _ in pipeline_stage_labels}

    for log in logs:
        if log.step_name not in valid_stage_keys:
            continue
        latest_by_stage[log.step_name] = log
        if log.status == "failed":
            failed_stage_keys.add(log.step_name)

    stages: list[JobStageResponse] = []
    for stage_key, label in pipeline_stage_labels:
        latest = latest_by_stage.get(stage_key)
        status = "queued"
        message = None
        updated_at = None
        if latest is not None:
            status = latest.status
            message = latest.message
            updated_at = latest.created_at
            if status == "completed" and stage_key in failed_stage_keys:
                status = "completed_with_errors"
        stages.append(
            JobStageResponse(
                stage_key=stage_key,
                label=label,
                status=status,
                message=message,
                updated_at=updated_at,
            )
        )
    return stages
