from __future__ import annotations

import html
import importlib
import inspect
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.db.models import Article, Job, Page, PdfFile
from app.schemas.job import JobDetailResponse, JobRunDailyRequest
from app.services.captions import caption_entries_by_image_order, flatten_caption_entries
from app.services.job_runner import JobRunner
from app.services.job_scheduler import get_job_scheduler
from app.services.news_delivery import NewsDeliveryClient, NewsDeliveryError
from app.services.preview_builder import build_page_preview
from app.services.result_builder import build_job_detail, build_job_result
from app.services.storage import OutputStorage
from app.utils.geometry import bbox_from_any, box_intersection_area, normalize_bbox_to_page
from app.utils.json_utils import dump_json


class DemoServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class RedeliveryHook(Protocol):
    async def __call__(self, *, db: Session, article: Article, payload: dict[str, Any]) -> dict[str, Any] | None: ...


@dataclass(slots=True)
class DemoMessage:
    level: str
    text: str


@dataclass(slots=True)
class DemoJobSummary:
    job_key: str
    status: str
    requested_at: datetime | None
    source_dir: str
    total_pdfs: int
    total_articles: int
    failed_files: int
    progress_percent: float


@dataclass(slots=True)
class DemoJobActivity:
    status: str
    stage_key: str | None
    stage_label: str | None
    message: str | None
    pdf_file: str | None
    page_number: int | None
    updated_at: datetime | None


@dataclass(slots=True)
class DemoJobDeliverySummary:
    total_articles: int
    delivered_marks: int
    failed_marks: int
    pending_marks: int
    verified_payload_count: int
    endpoint: str | None
    response_code: int | None
    batch_size: int | None
    updated_at: str | None
    note: str | None


@dataclass(slots=True)
class DemoArticleLink:
    article_id: int
    article_order: int
    title: str
    body_length: int
    image_count: int
    page_id: int
    page_number: int
    confidence: float
    has_annotation: bool
    delivery_status: str
    relevance_score: float | None = None
    relevance_reason: str | None = None


@dataclass(slots=True)
class DemoPageGroup:
    page_id: int
    page_number: int
    status: str
    article_count: int
    articles: list[DemoArticleLink] = field(default_factory=list)


@dataclass(slots=True)
class DemoPdfGroup:
    pdf_file_id: int
    file_name: str
    status: str
    page_count: int
    article_count: int
    skip_reason: str | None
    pages: list[DemoPageGroup] = field(default_factory=list)


@dataclass(slots=True)
class DemoOverlayBox:
    label: str
    text: str | None
    style: str
    selected: bool = False
    muted: bool = False


@dataclass(slots=True)
class DemoImageCard:
    image_id: int
    image_url: str
    width: int
    height: int
    bbox: list[int]
    captions: list["DemoCaptionLine"] = field(default_factory=list)


@dataclass(slots=True)
class DemoCaptionLine:
    text: str
    bbox: list[int] | None = None
    confidence: float | None = None


@dataclass(slots=True)
class DemoPreviewPageLink:
    page_id: int
    page_number: int
    pdf_file: str
    status: str
    article_count: int
    article_id: int | None = None
    is_current: bool = False
    has_selected_article: bool = False


@dataclass(slots=True)
class DemoArticleDetail:
    article_id: int
    job_key: str
    job_status: str
    pdf_file: str
    source_pdf_path: str
    source_dir: str
    page_id: int
    page_number: int
    page_status: str
    preview_page_id: int
    preview_page_number: int
    preview_page_status: str
    preview_page_image_url: str
    preview_page_width: int
    preview_page_height: int
    preview_page_index: int
    preview_page_total: int
    preview_previous_page_id: int | None
    preview_next_page_id: int | None
    preview_previous_page_article_id: int | None
    preview_next_page_article_id: int | None
    preview_pages: list[DemoPreviewPageLink]
    article_order: int
    title: str
    raw_ocr_text: str
    corrected_title: str
    corrected_body_text: str
    corrected_text: str
    relevance_score: float | None
    relevance_reason: str | None
    relevance_label: str | None
    relevance_model: str | None
    relevance_source: str | None
    delivery_status: str
    delivery_endpoint: str | None = None
    delivery_response_code: int | None = None
    delivery_batch_size: int | None = None
    delivery_last_error: str | None = None
    delivery_updated_at: str | None = None
    delivery_request_source: str = "reconstructed"
    delivery_request_note: str | None = None
    review_status: str | None = None
    correction_source: str = "ocr"
    annotation_path: str | None = None
    bundle_dir: str = ""
    metadata_path: str | None = None
    markdown_path: str | None = None
    callback_url: str | None = None
    article_bbox: list[int] | None = None
    title_bbox: list[int] | None = None
    confidence: float = 0.0
    preview_overlay_boxes: list[DemoOverlayBox] = field(default_factory=list)
    images: list[DemoImageCard] = field(default_factory=list)
    article_body_html: str = ""
    raw_payload_text: str = ""
    metadata_json_text: str = ""
    article_markdown_text: str = ""
    article_payload: dict[str, Any] = field(default_factory=dict)
    article_payload_text: str = ""
    delivery_request_text: str = ""
    page_articles: list["DemoArticleDetail"] = field(default_factory=list)


class DemoService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.storage = OutputStorage()
        self.delivery = NewsDeliveryClient()

    def build_jobs_page(
        self,
        db: Session,
        *,
        selected_job_key: str | None = None,
        selected_article_id: int | None = None,
        selected_preview_page_id: int | None = None,
        limit: int = 12,
    ) -> dict[str, Any]:
        jobs = self.list_recent_jobs(db, limit=limit)
        selected_job = self._resolve_selected_job(db, jobs, selected_job_key, selected_article_id)
        browser = self.build_job_browser(db, selected_job) if selected_job is not None else []
        selected_job_detail = build_job_detail(db, selected_job) if selected_job is not None else None
        selected_job_activity = self._build_job_activity(selected_job_detail)
        selected_job_delivery = self._build_job_delivery_summary(db, selected_job) if selected_job is not None else None
        selected_job_summary = None
        if selected_job is not None:
            selected_job_summary = next((item for item in jobs if item.job_key == selected_job.job_key), None)
        active_article_id = selected_article_id or self._first_article_id(browser)
        detail = self.get_article_detail(db, active_article_id, preview_page_id=selected_preview_page_id) if active_article_id is not None else None
        if detail is None and selected_article_id is not None:
            fallback_article_id = self._first_article_id(browser)
            if fallback_article_id is not None and fallback_article_id != selected_article_id:
                detail = self.get_article_detail(db, fallback_article_id, preview_page_id=selected_preview_page_id)
        return {
            "jobs": jobs,
            "selected_job_key": selected_job.job_key if selected_job is not None else None,
            "selected_job_summary": selected_job_summary,
            "selected_job_detail": selected_job_detail,
            "selected_job_activity": selected_job_activity,
            "selected_job_delivery": selected_job_delivery,
            "job_browser": browser,
            "article_detail": detail,
            "auto_refresh_seconds": 2 if selected_job is not None and selected_job.status in {"queued", "running"} else None,
        }

    def list_recent_jobs(self, db: Session, *, limit: int = 12) -> list[DemoJobSummary]:
        rows = list(db.scalars(select(Job).order_by(Job.requested_at.desc(), Job.id.desc())))
        summaries: list[DemoJobSummary] = []
        seen_job_keys: set[str] = set()
        for job in rows:
            if job.job_key in seen_job_keys:
                continue
            if not self._job_should_be_visible(job):
                continue
            seen_job_keys.add(job.job_key)
            processed = job.success_files + job.failed_files
            if job.total_files > 0:
                progress = round((processed / job.total_files) * 100, 1)
            elif job.status in {"completed", "completed_with_errors"}:
                progress = 100.0
            else:
                progress = 0.0
            summaries.append(
                DemoJobSummary(
                    job_key=job.job_key,
                    status=job.status,
                    requested_at=job.requested_at,
                    source_dir=job.source_dir,
                    total_pdfs=job.total_files,
                    total_articles=job.total_articles,
                    failed_files=job.failed_files,
                    progress_percent=progress,
                )
            )
            if len(summaries) >= limit:
                break
        return summaries

    def _job_artifact_roots(self, job_key: str) -> tuple[Path, ...]:
        return self.storage.job_artifact_roots(job_key)

    def _job_has_artifacts(self, job_key: str) -> bool:
        return any(root.exists() for root in self._job_artifact_roots(job_key))

    def _job_should_be_visible(self, job: Job) -> bool:
        if job.status in {"queued", "running"}:
            return True
        return self._job_has_artifacts(job.job_key)

    def build_job_browser(self, db: Session, job: Job | None) -> list[DemoPdfGroup]:
        if job is None:
            return []

        detail = build_job_detail(db, job)
        result = build_job_result(db, job)
        page_lookup: dict[tuple[str, int], tuple[int, str, int]] = {}
        pdf_lookup = {pdf.file_name: pdf for pdf in detail.pdf_files}

        for pdf in detail.pdf_files:
            for page in pdf.pages:
                page_lookup[(pdf.file_name, page.page_number)] = (page.page_id, page.status, page.article_count)

        groups: list[DemoPdfGroup] = []
        for file_result in result.files:
            pdf_detail = pdf_lookup.get(file_result.pdf_file)
            pages_by_number: dict[int, DemoPageGroup] = {}
            for article in file_result.articles:
                page_id, page_status, page_article_count = page_lookup.get(
                    (file_result.pdf_file, article.page_number),
                    (0, "unknown", 0),
                )
                page_group = pages_by_number.setdefault(
                    article.page_number,
                    DemoPageGroup(
                        page_id=page_id,
                        page_number=article.page_number,
                        status=page_status,
                        article_count=page_article_count,
                    ),
                )
                bundle_dir = Path(article.bundle_dir or "")
                sidecar_state = self._read_state_sidecars(bundle_dir) if bundle_dir else {}
                display_title = self._display_title(
                    sidecar_state.get("annotation_payload", {}).get("corrected_title"),
                    sidecar_state.get("enrichment_payload", {}).get("corrected_title"),
                    article.title,
                )
                page_group.articles.append(
                    DemoArticleLink(
                        article_id=article.article_id,
                        article_order=article.article_order,
                        title=display_title,
                        body_length=len(self._clean_display_text(article.body_text)),
                        image_count=len(article.images),
                        page_id=page_id,
                        page_number=article.page_number,
                        confidence=0.0,
                        has_annotation=bool(sidecar_state.get("annotation_payload")),
                        delivery_status=str(sidecar_state.get("delivery_status") or "not_configured"),
                        relevance_score=article.relevance_score,
                        relevance_reason=article.relevance_reason,
                    )
                )
            groups.append(
                DemoPdfGroup(
                    pdf_file_id=pdf_detail.pdf_file_id if pdf_detail is not None else 0,
                    file_name=file_result.pdf_file,
                    status=pdf_detail.status if pdf_detail is not None else "unknown",
                    page_count=pdf_detail.page_count if pdf_detail is not None else file_result.pages,
                    article_count=sum(len(page.articles) for page in pages_by_number.values()),
                    skip_reason=pdf_detail.skip_reason if pdf_detail is not None else None,
                    pages=sorted(pages_by_number.values(), key=lambda item: item.page_number),
                )
            )
        return groups

    def _build_job_delivery_summary(self, db: Session, job: Job | None) -> DemoJobDeliverySummary | None:
        if job is None:
            return None

        result = build_job_result(db, job)
        articles = [article for file_result in result.files for article in file_result.articles]
        if not articles:
            return None

        delivered_marks = 0
        failed_marks = 0
        pending_marks = 0
        verified_payload_count = 0
        endpoint: str | None = None
        response_code: int | None = None
        batch_size: int | None = None
        latest_timestamp: str | None = None

        for article in articles:
            bundle_dir = Path(article.bundle_dir or "")
            state = self._read_state_sidecars(bundle_dir) if bundle_dir else {}
            delivery_payload = state.get("delivery_payload", {}) if isinstance(state.get("delivery_payload"), dict) else {}
            delivery_status = str(state.get("delivery_status") or "not_configured")

            if delivery_status == "delivered":
                delivered_marks += 1
            elif delivery_status == "failed":
                failed_marks += 1
            else:
                pending_marks += 1

            if isinstance(delivery_payload.get("request_article"), dict):
                verified_payload_count += 1

            endpoint = endpoint or (self._pick_first_text(delivery_payload.get("endpoint")) or None)
            candidate_response_code = self._as_int(delivery_payload.get("response_code"))
            if response_code is None and candidate_response_code is not None:
                response_code = candidate_response_code

            candidate_batch_size = self._as_int(
                self._pick_first(
                    delivery_payload.get("request_batch_size"),
                    delivery_payload.get("delivered_articles"),
                    delivery_payload.get("batch_size"),
                )
            )
            if candidate_batch_size is not None:
                batch_size = max(batch_size or candidate_batch_size, candidate_batch_size)

            candidate_timestamp = self._pick_first_text(
                delivery_payload.get("updated_at"),
                delivery_payload.get("delivered_at"),
                delivery_payload.get("attempted_at"),
            )
            if candidate_timestamp and (latest_timestamp is None or candidate_timestamp > latest_timestamp):
                latest_timestamp = candidate_timestamp

        note: str | None = None
        total_articles = len(articles)
        if delivered_marks == total_articles and verified_payload_count == total_articles:
            note = "stored /news payload is available for every article"
        elif delivered_marks == total_articles and batch_size is not None and batch_size < total_articles and verified_payload_count == 0:
            note = f"legacy delivery record: article folders are marked delivered, but the saved batch size is {batch_size}"
        elif failed_marks > 0:
            note = f"{failed_marks} article deliveries are marked failed"

        return DemoJobDeliverySummary(
            total_articles=total_articles,
            delivered_marks=delivered_marks,
            failed_marks=failed_marks,
            pending_marks=pending_marks,
            verified_payload_count=verified_payload_count,
            endpoint=endpoint,
            response_code=response_code,
            batch_size=batch_size,
            updated_at=self._format_timestamp(latest_timestamp),
            note=note,
        )

    def get_article_detail(
        self,
        db: Session,
        article_id: int | None,
        *,
        preview_page_id: int | None = None,
        include_page_articles: bool = True,
    ) -> DemoArticleDetail | None:
        if article_id is None:
            return None

        article = db.scalar(
            select(Article)
            .where(Article.id == article_id)
            .options(
                selectinload(Article.images),
                selectinload(Article.page).selectinload(Page.pdf_file).selectinload(PdfFile.job),
            )
        )
        if article is None or article.page is None or article.page.pdf_file is None or article.page.pdf_file.job is None:
            return None

        page = article.page
        pdf_file = page.pdf_file
        job = pdf_file.job
        job_pages = list(
            db.scalars(
                select(Page)
                .join(PdfFile, PdfFile.id == Page.pdf_file_id)
                .where(PdfFile.job_id == job.id)
                .options(selectinload(Page.articles).selectinload(Article.images), selectinload(Page.pdf_file))
                .order_by(PdfFile.id, Page.page_number, Page.id)
            )
        )
        preview_page = next((candidate for candidate in job_pages if candidate.id == preview_page_id), page)
        if preview_page not in job_pages:
            job_pages.append(preview_page)
            job_pages.sort(key=lambda item: (item.page_number, item.id))

        preview = build_page_preview(db, job, preview_page, "merged", self.settings.api_prefix)
        detail = self._compose_article_detail(
            article=article,
            page=page,
            pdf_file=pdf_file,
            job=job,
            preview_page=preview_page,
            job_pages=job_pages,
            preview=preview,
        )
        if include_page_articles:
            preview_page_articles = sorted(preview_page.articles or [], key=lambda item: (item.article_order, item.id))
            detail.page_articles = [
                self._compose_article_detail(
                    article=preview_article,
                    page=preview_page,
                    pdf_file=preview_page.pdf_file or pdf_file,
                    job=job,
                    preview_page=preview_page,
                    job_pages=job_pages,
                    preview=preview,
                )
                for preview_article in preview_page_articles
            ]
        return detail

    def _compose_article_detail(
        self,
        *,
        article: Article,
        page: Page,
        pdf_file: PdfFile,
        job: Job,
        preview_page: Page,
        job_pages: list[Page],
        preview: Any,
    ) -> DemoArticleDetail:
        bundle_dir = self.storage.resolve_article_bundle_path(
            job.job_key,
            pdf_file.file_name,
            page.page_number,
            article.article_order,
            article.title,
        )
        metadata_path = bundle_dir / "article.json"
        markdown_path = bundle_dir / "article.md"
        metadata = self.storage.load_article_metadata(bundle_dir)
        state = self._read_state_sidecars(bundle_dir)
        caption_map = caption_entries_by_image_order(metadata, width=page.width, height=page.height)
        flattened_captions = flatten_caption_entries(caption_map)
        display_title_bbox = normalize_bbox_to_page(article.title_bbox, page.width, page.height)
        display_article_bbox = normalize_bbox_to_page(article.article_bbox, page.width, page.height)
        raw_payload = self._load_raw_payload(page.raw_vl_json_path) or self._load_raw_payload(page.raw_fallback_json_path)
        raw_ocr_text = self._extract_raw_ocr_text(raw_payload, display_article_bbox) or article.body_text or ""
        corrected_title = self._pick_first_text(
            metadata.get("corrected_title"),
            state.get("annotation_payload", {}).get("corrected_title"),
            state.get("enrichment_payload", {}).get("corrected_title"),
            article.title,
        )
        corrected_body = self._pick_first_text(
            metadata.get("corrected_body_text"),
            state.get("annotation_payload", {}).get("corrected_body_text"),
            state.get("enrichment_payload", {}).get("corrected_body_text"),
            article.body_text,
            raw_ocr_text,
        )
        corrected_text = "\n\n".join(part for part in [corrected_title, corrected_body] if part.strip())
        delivery_updated_at = self._format_timestamp(
            self._pick_first(
                state.get("delivery_payload", {}).get("updated_at"),
                state.get("delivery_payload", {}).get("delivered_at"),
                state.get("delivery_payload", {}).get("attempted_at"),
            )
        )
        preview_overlay_boxes = self._build_overlay_boxes(preview.regions, article.id, preview.width, preview.height, preview.articles)
        preview_page_index = next(
            (index for index, candidate in enumerate(job_pages) if candidate.id == preview_page.id),
            0,
        )
        preview_pages = [
            DemoPreviewPageLink(
                page_id=candidate.id,
                page_number=candidate.page_number,
                pdf_file=candidate.pdf_file.file_name if candidate.pdf_file is not None else pdf_file.file_name,
                status=candidate.parse_status,
                article_count=len(candidate.articles),
                article_id=self._preview_target_article_id(candidate, selected_article=article),
                is_current=candidate.id == preview_page.id,
                has_selected_article=candidate.id == page.id,
            )
            for candidate in job_pages
        ]
        preview_previous_page = job_pages[preview_page_index - 1] if preview_page_index > 0 else None
        preview_next_page = job_pages[preview_page_index + 1] if preview_page_index + 1 < len(job_pages) else None
        preview_previous_page_id = preview_previous_page.id if preview_previous_page is not None else None
        preview_next_page_id = preview_next_page.id if preview_next_page is not None else None
        preview_previous_page_article_id = (
            self._preview_target_article_id(preview_previous_page, selected_article=article)
            if preview_previous_page is not None
            else None
        )
        preview_next_page_article_id = (
            self._preview_target_article_id(preview_next_page, selected_article=article)
            if preview_next_page is not None
            else None
        )
        images = [
            DemoImageCard(
                image_id=image.id,
                image_url=f"{self.settings.api_prefix}/jobs/{job.job_key}/article-images/{image.id}",
                width=(
                    max(1, normalized_bbox[2] - normalized_bbox[0])
                    if (normalized_bbox := (normalize_bbox_to_page(image.image_bbox, page.width, page.height) or image.image_bbox)) != image.image_bbox
                    else image.width
                ),
                height=(
                    max(1, normalized_bbox[3] - normalized_bbox[1])
                    if normalized_bbox != image.image_bbox
                    else image.height
                ),
                bbox=normalized_bbox,
                captions=[
                    DemoCaptionLine(
                        text=str(caption.get("text") or "").strip(),
                        bbox=caption.get("bbox"),
                        confidence=self._as_float(caption.get("confidence")),
                    )
                    for caption in caption_map.get(image.image_order, [])
                    if str(caption.get("text") or "").strip()
                ],
            )
            for image in sorted(article.images, key=lambda item: item.image_order)
        ]
        try:
            markdown_text = markdown_path.read_text(encoding="utf-8") if markdown_path.exists() else corrected_text
        except OSError:
            markdown_text = corrected_text
        delivery_payload = state.get("delivery_payload", {}) if isinstance(state.get("delivery_payload"), dict) else {}
        stored_delivery_request = delivery_payload.get("request_article")
        if isinstance(stored_delivery_request, dict):
            delivery_request_payload = stored_delivery_request
            delivery_request_source = "stored"
            delivery_request_note = None
        else:
            delivery_request_payload = self._build_delivery_request_preview(
                title=self._display_title(
                    corrected_title,
                    metadata.get("title"),
                    metadata.get("corrected_title"),
                    article.title,
                ),
                body_text=corrected_body or article.body_text or raw_ocr_text,
                metadata=metadata,
            )
            delivery_request_source = "reconstructed"
            delivery_request_note = "stored request payload was not recorded for this delivery; this preview was reconstructed from the article bundle"
        article_payload = {
            "article_id": article.id,
            "job_key": job.job_key,
            "pdf_file": pdf_file.file_name,
            "page_number": page.page_number,
            "article_order": article.article_order,
            "title": self._display_title(
                corrected_title,
                metadata.get("title"),
                metadata.get("corrected_title"),
                article.title,
            ),
            "body_text": corrected_body or article.body_text or raw_ocr_text,
            "corrected_title": corrected_title,
            "corrected_body_text": corrected_body,
            "page_status": page.parse_status,
            "delivery_status": state.get("delivery_status") or "not_configured",
            "relevance_score": self._as_float(
                self._pick_first(
                    state.get("enrichment_payload", {}).get("relevance_score"),
                    state.get("enrichment_payload", {}).get("score"),
                    metadata.get("relevance_score"),
                )
            ),
            "confidence": self._as_float(
                self._pick_first(metadata.get("confidence"), metadata.get("score"), 0.0)
            ),
            "captions": flattened_captions,
            "source_metadata": metadata.get("source_metadata") if isinstance(metadata.get("source_metadata"), dict) else None,
            "images": [
                {
                    "image_id": image.image_id,
                    "image_url": image.image_url,
                    "width": image.width,
                    "height": image.height,
                    "bbox": image.bbox,
                    "captions": [
                        {
                            "text": caption.text,
                            "bbox": caption.bbox,
                            "confidence": caption.confidence,
                        }
                        for caption in image.captions
                    ],
                }
                for image in images
            ],
            "source": {
                "metadata_path": str(metadata_path),
                "markdown_path": str(markdown_path),
                "bundle_dir": str(bundle_dir),
            },
        }
        article_body_html = self._build_article_body_html(
            corrected_body,
            article.body_text,
            metadata.get("body_text"),
            raw_ocr_text,
            markdown_path=markdown_path if markdown_path.exists() else None,
        )
        return DemoArticleDetail(
            article_id=article.id,
            job_key=job.job_key,
            job_status=job.status,
            pdf_file=pdf_file.file_name,
            source_pdf_path=pdf_file.file_path,
            source_dir=job.source_dir,
            page_id=page.id,
            page_number=page.page_number,
            page_status=page.parse_status,
            preview_page_id=preview.page_id,
            preview_page_number=preview.page_number,
            preview_page_status=preview.parse_status,
            preview_page_image_url=preview.image_url,
            preview_page_width=preview.width,
            preview_page_height=preview.height,
            preview_page_index=preview_page_index,
            preview_page_total=len(job_pages),
            preview_previous_page_id=preview_previous_page_id,
            preview_next_page_id=preview_next_page_id,
            preview_previous_page_article_id=preview_previous_page_article_id,
            preview_next_page_article_id=preview_next_page_article_id,
            preview_pages=preview_pages,
            article_order=article.article_order,
            title=self._display_title(corrected_title, metadata.get("title"), metadata.get("corrected_title"), article.title),
            raw_ocr_text=raw_ocr_text,
            corrected_title=corrected_title,
            corrected_body_text=corrected_body,
            corrected_text=corrected_text,
            relevance_score=self._as_float(
                self._pick_first(
                    metadata.get("relevance_score"),
                    state.get("enrichment_payload", {}).get("relevance_score"),
                    state.get("enrichment_payload", {}).get("score"),
                    metadata.get("score"),
                )
            ),
            relevance_reason=self._pick_first_text(
                metadata.get("relevance_reason"),
                state.get("enrichment_payload", {}).get("relevance_reason"),
                state.get("enrichment_payload", {}).get("reason"),
                metadata.get("reason"),
            ),
            relevance_label=self._pick_first_text(
                metadata.get("relevance_label"),
                state.get("enrichment_payload", {}).get("relevance_label"),
            )
            or None,
            relevance_model=self._pick_first_text(
                metadata.get("relevance_model"),
                state.get("enrichment_payload", {}).get("relevance_model"),
            )
            or None,
            relevance_source=self._pick_first_text(
                metadata.get("relevance_source"),
                state.get("enrichment_payload", {}).get("relevance_source"),
            )
            or None,
            delivery_status=str(state.get("delivery_status") or "not_configured"),
            delivery_endpoint=self._pick_first_text(delivery_payload.get("endpoint")) or None,
            delivery_response_code=self._as_int(delivery_payload.get("response_code")),
            delivery_batch_size=self._as_int(
                self._pick_first(
                    delivery_payload.get("request_batch_size"),
                    delivery_payload.get("delivered_articles"),
                    delivery_payload.get("batch_size"),
                )
            ),
            delivery_last_error=self._pick_first_text(
                delivery_payload.get("last_error"),
                delivery_payload.get("error"),
            )
            or None,
            delivery_updated_at=delivery_updated_at,
            delivery_request_source=delivery_request_source,
            delivery_request_note=delivery_request_note,
            review_status=self._pick_first_text(
                state.get("annotation_payload", {}).get("status"),
                state.get("enrichment_payload", {}).get("review_status"),
            )
            or None,
            correction_source=str(state.get("correction_source") or "ocr"),
            annotation_path=str(state.get("annotation_path")) if state.get("annotation_path") else None,
            bundle_dir=str(bundle_dir),
            metadata_path=str(metadata_path) if metadata_path.exists() else None,
            markdown_path=str(markdown_path) if markdown_path.exists() else None,
            callback_url=job.callback_url,
            article_bbox=display_article_bbox,
            title_bbox=display_title_bbox,
            confidence=article.confidence,
            preview_overlay_boxes=preview_overlay_boxes,
            images=images,
            article_body_html=article_body_html,
            raw_payload_text=self._json_text(raw_payload),
            metadata_json_text=self._json_text(metadata),
            article_markdown_text=markdown_text,
            article_payload=article_payload,
            article_payload_text=self._json_text(article_payload),
            delivery_request_text=self._json_text(delivery_request_payload),
        )

    async def queue_reprocess(self, db: Session, article_id: int) -> DemoMessage:
        article = self._load_article_context(db, article_id)
        page = article.page
        assert page is not None
        pdf_file = page.pdf_file
        assert pdf_file is not None
        job = pdf_file.job
        assert job is not None

        source_pdf = self.settings.resolve_input_path(pdf_file.file_path) or Path(pdf_file.file_path)
        if not source_pdf.exists():
            raise DemoServiceError(f"source PDF not found: {source_pdf}", status_code=404)

        staging_root = self.settings.output_root / "_operator_reprocess"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=f"article_{article.id}_", dir=str(staging_root)))
        staged_pdf = staging_dir / source_pdf.name
        shutil.copy2(source_pdf, staged_pdf)

        runner = JobRunner(db)
        new_job = runner.create_job(
            JobRunDailyRequest(
                source_dir=str(staging_dir),
                callback_url=job.callback_url,
                force_reprocess=True,
            )
        )
        db.commit()
        db.refresh(new_job)
        await get_job_scheduler().schedule(new_job.id)
        return DemoMessage(level="success", text=f"재처리 작업을 큐에 넣었습니다: {new_job.job_key}")

    async def queue_source_dir_job(
        self,
        db: Session,
        *,
        source_dir: str | None = None,
        callback_url: str | None = None,
    ) -> Job:
        requested_source_dir = (source_dir or "").strip()
        translated_source_dir = self.settings.translate_source_dir(requested_source_dir or None)
        resolved_source_dir = Path(translated_source_dir)
        if not resolved_source_dir.exists():
            raise DemoServiceError(f"source directory not found: {resolved_source_dir}", status_code=404)
        if not resolved_source_dir.is_dir():
            raise DemoServiceError(f"source directory is not a directory: {resolved_source_dir}", status_code=400)

        runner = JobRunner(db)
        job = runner.create_job(
            JobRunDailyRequest(
                source_dir=str(resolved_source_dir),
                callback_url=(callback_url or "").strip() or None,
                force_reprocess=True,
            )
        )
        db.commit()
        db.refresh(job)
        await get_job_scheduler().schedule(job.id)
        return job

    async def queue_single_pdf_job(
        self,
        db: Session,
        *,
        pdf_path: str,
        callback_url: str | None = None,
    ) -> Job:
        requested_pdf_path = Path((pdf_path or "").strip()).expanduser()
        if not str(requested_pdf_path).strip():
            raise DemoServiceError("pdf path is required", status_code=400)
        if not requested_pdf_path.exists():
            raise DemoServiceError(f"pdf file not found: {requested_pdf_path}", status_code=404)
        if not requested_pdf_path.is_file():
            raise DemoServiceError(f"pdf path is not a file: {requested_pdf_path}", status_code=400)
        if requested_pdf_path.suffix.lower() != ".pdf":
            raise DemoServiceError("only .pdf files are supported", status_code=400)

        staging_root = self.settings.output_root / "_operator_manual"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix="manual_pdf_", dir=str(staging_root)))
        staged_pdf = staging_dir / requested_pdf_path.name
        shutil.copy2(requested_pdf_path, staged_pdf)

        runner = JobRunner(db)
        job = runner.create_job(
            JobRunDailyRequest(
                source_dir=str(staging_dir),
                callback_url=(callback_url or "").strip() or None,
                force_reprocess=True,
            )
        )
        db.commit()
        db.refresh(job)
        await get_job_scheduler().schedule(job.id)
        return job

    async def queue_uploaded_pdf_job(
        self,
        db: Session,
        *,
        filename: str,
        content: bytes,
        callback_url: str | None = None,
    ) -> Job:
        staging_dir = self._stage_uploaded_pdfs([(filename, content)], prefix="manual_upload_")
        runner = JobRunner(db)
        job = runner.create_job(
            JobRunDailyRequest(
                source_dir=str(staging_dir),
                callback_url=(callback_url or "").strip() or None,
                force_reprocess=True,
            )
        )
        db.commit()
        db.refresh(job)
        await get_job_scheduler().schedule(job.id)
        return job

    async def queue_uploaded_pdf_batch_job(
        self,
        db: Session,
        *,
        files: list[tuple[str, bytes]],
        callback_url: str | None = None,
    ) -> Job:
        staging_dir = self._stage_uploaded_pdfs(files, prefix="manual_folder_")
        runner = JobRunner(db)
        job = runner.create_job(
            JobRunDailyRequest(
                source_dir=str(staging_dir),
                callback_url=(callback_url or "").strip() or None,
                force_reprocess=True,
            )
        )
        db.commit()
        db.refresh(job)
        await get_job_scheduler().schedule(job.id)
        return job

    async def redeliver_article(self, db: Session, article_id: int) -> DemoMessage:
        article = self._load_article_context(db, article_id)
        detail = self.get_article_detail(db, article_id)
        if detail is None:
            raise DemoServiceError("article not found", status_code=404)

        bundle_dir = Path(detail.bundle_dir)
        delivery_path = bundle_dir / "demo_delivery.json"
        payload = {
            "job_id": detail.job_key,
            "article_id": detail.article_id,
            "pdf_file": detail.pdf_file,
            "page_number": detail.page_number,
            "article_order": detail.article_order,
            "title": detail.corrected_title,
            "body_text": detail.corrected_body_text,
            "raw_ocr_text": detail.raw_ocr_text,
            "relevance_score": detail.relevance_score,
            "relevance_reason": detail.relevance_reason,
            "relevance_label": detail.relevance_label,
            "relevance_model": detail.relevance_model,
            "relevance_source": detail.relevance_source,
            "source_pdf_path": detail.source_pdf_path,
            "bundle_dir": detail.bundle_dir,
        }

        job = article.page.pdf_file.job  # type: ignore[union-attr]
        callback_url = job.callback_url if job is not None else None
        hook = self._resolve_redelivery_hook()

        try:
            if hook is not None:
                hook_result = await self._invoke_redelivery_hook(hook, db=db, article=article, payload=payload)
                state = {
                    "delivery_status": str(self._pick_first(hook_result.get("delivery_status"), hook_result.get("status")) or "delivered"),
                    "last_error": self._pick_first_text(hook_result.get("last_error"), hook_result.get("error")),
                    "transport": hook_result.get("transport") or "hook",
                    "attempted_at": self._utcnow().isoformat(),
                    "updated_at": self._utcnow().isoformat(),
                }
                dump_json(delivery_path, state)
                return DemoMessage(level="success", text="전송 훅을 통해 기사 재전송을 요청했습니다.")

            if job is None:
                raise DemoServiceError("job context is missing for redelivery", status_code=409)

            result = build_job_result(db, job)
            delivery_article = next(
                (
                    item
                    for file_result in result.files
                    for item in file_result.articles
                    if item.article_id == article_id
                ),
                None,
            )
            if delivery_article is None:
                raise DemoServiceError("article payload not found for redelivery", status_code=404)

            self.delivery.deliver_articles(
                [delivery_article],
                target_url=callback_url,
                state_filename="demo_delivery.json",
                raise_on_failure=True,
            )
            return DemoMessage(level="success", text="기사 payload를 multipart /news로 재전송했습니다.")
        except DemoServiceError:
            raise
        except NewsDeliveryError as exc:
            raise DemoServiceError(exc.message, status_code=exc.status_code) from exc
        except Exception as exc:  # noqa: BLE001
            dump_json(
                delivery_path,
                {
                    "delivery_status": "failed",
                    "transport": "hook" if hook is not None else "multipart_news",
                    "attempted_at": self._utcnow().isoformat(),
                    "updated_at": self._utcnow().isoformat(),
                    "last_error": str(exc),
                },
            )
            raise DemoServiceError(f"redelivery failed: {exc}", status_code=502) from exc

    def delete_job(self, db: Session, job_key: str) -> DemoMessage:
        job = db.scalar(select(Job).where(Job.job_key == job_key))
        if job is None:
            raise DemoServiceError("job not found", status_code=404)

        artifact_roots = self._job_artifact_roots(job.job_key)
        db.delete(job)
        db.commit()
        for artifact_root in artifact_roots:
            try:
                shutil.rmtree(artifact_root, ignore_errors=True)
            except OSError:
                continue
        return DemoMessage(level="success", text=f"작업을 삭제했습니다: {job_key}")

    def _stage_uploaded_pdfs(self, files: list[tuple[str, bytes]], *, prefix: str) -> Path:
        staging_root = self.settings.output_root / "_operator_uploads"
        staging_root.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(tempfile.mkdtemp(prefix=prefix, dir=str(staging_root)))

        written = 0
        used_names: set[str] = set()
        for index, (raw_name, content) in enumerate(files, start=1):
            if not content:
                continue
            safe_name = self._safe_uploaded_pdf_name(raw_name, index=index, used_names=used_names)
            if safe_name is None:
                continue
            (staging_dir / safe_name).write_bytes(content)
            written += 1

        if written <= 0:
            shutil.rmtree(staging_dir, ignore_errors=True)
            raise DemoServiceError("업로드된 PDF가 없습니다.", status_code=400)
        return staging_dir

    @staticmethod
    def _safe_uploaded_pdf_name(raw_name: str, *, index: int, used_names: set[str]) -> str | None:
        normalized = str(raw_name or "").replace("\\", "/").strip()
        parts = [part for part in normalized.split("/") if part not in {"", ".", ".."}]
        candidate = "__".join(parts) if parts else f"upload_{index:02d}.pdf"
        parsed = Path(candidate)
        if parsed.suffix.lower() != ".pdf":
            return None

        stem = parsed.stem.strip() or f"upload_{index:02d}"
        suffix = ".pdf"
        safe_name = f"{stem}{suffix}"
        duplicate_index = 2
        while safe_name.lower() in used_names:
            safe_name = f"{stem}_{duplicate_index}{suffix}"
            duplicate_index += 1
        used_names.add(safe_name.lower())
        return safe_name

    def _resolve_selected_job(
        self,
        db: Session,
        jobs: list[DemoJobSummary],
        selected_job_key: str | None,
        selected_article_id: int | None,
    ) -> Job | None:
        if selected_article_id is not None:
            article = self._load_article_context(db, selected_article_id, raise_on_missing=False)
            if article is not None and article.page is not None and article.page.pdf_file is not None and article.page.pdf_file.job is not None:
                job = article.page.pdf_file.job
                if self._job_has_artifacts(job.job_key):
                    return job

        candidate_job_keys: list[str] = []
        if selected_job_key:
            candidate_job_keys.append(selected_job_key)
        candidate_job_keys.extend(job.job_key for job in jobs if job.job_key != selected_job_key)

        for job_key in candidate_job_keys:
            job = db.scalar(select(Job).where(Job.job_key == job_key))
            if job is None:
                continue
            if not self._job_should_be_visible(job):
                continue
            return job
        return None

    def _build_job_activity(self, detail: JobDetailResponse | None) -> DemoJobActivity | None:
        if detail is None:
            return None

        stage_labels = {stage.stage_key: stage.label for stage in detail.stages}
        running_pdf = next((pdf for pdf in detail.pdf_files if pdf.status == "running"), None)
        running_page = (
            next((page for page in running_pdf.pages if page.status == "running"), None)
            if running_pdf is not None
            else None
        )
        running_stage = next((stage for stage in detail.stages if stage.status == "running"), None)
        active_log = next((log for log in reversed(detail.recent_logs) if log.status == "running"), None)
        latest_log = detail.recent_logs[-1] if detail.recent_logs else None

        stage_key = (
            active_log.step_name
            if active_log is not None
            else (running_stage.stage_key if running_stage is not None else (latest_log.step_name if latest_log is not None else None))
        )
        stage_label = (
            stage_labels.get(stage_key)
            if stage_key is not None
            else (running_stage.label if running_stage is not None else None)
        )
        message = (
            active_log.message
            if active_log is not None
            else (running_stage.message if running_stage is not None else (latest_log.message if latest_log is not None else None))
        )
        pdf_file = (
            active_log.pdf_file
            if active_log is not None and active_log.pdf_file
            else (running_pdf.file_name if running_pdf is not None else (latest_log.pdf_file if latest_log is not None else None))
        )
        page_number = (
            active_log.page_number
            if active_log is not None and active_log.page_number is not None
            else (running_page.page_number if running_page is not None else (latest_log.page_number if latest_log is not None else None))
        )
        updated_at = (
            active_log.created_at
            if active_log is not None
            else (running_stage.updated_at if running_stage is not None else (latest_log.created_at if latest_log is not None else None))
        )
        status = (
            active_log.status
            if active_log is not None
            else (running_stage.status if running_stage is not None else detail.status)
        )

        if not any(value is not None for value in [stage_key, stage_label, message, pdf_file, page_number, updated_at]):
            return None

        return DemoJobActivity(
            status=status,
            stage_key=stage_key,
            stage_label=stage_label,
            message=message,
            pdf_file=pdf_file,
            page_number=page_number,
            updated_at=updated_at,
        )

    def _first_article_id(self, browser: list[DemoPdfGroup] | None) -> int | None:
        if not browser:
            return None
        best_article_id: int | None = None
        best_score: tuple[int, int, int, int, int] | None = None
        for pdf in browser:
            for page in pdf.pages:
                for article in page.articles:
                    title = self._clean_display_text(article.title)
                    header_like = self._is_likely_header_title(title, article.body_length)
                    relevance_value = article.relevance_score if article.relevance_score is not None else -1.0
                    score = (
                        1 if article.relevance_score is not None else 0,
                        int(relevance_value * 1000),
                        article.body_length,
                        article.image_count,
                        0 if not header_like else -1,
                    )
                    if best_score is None or score > best_score:
                        best_score = score
                        best_article_id = article.article_id
        return best_article_id

    def _load_article_context(self, db: Session, article_id: int, *, raise_on_missing: bool = True) -> Article | None:
        article = db.scalar(
            select(Article)
            .where(Article.id == article_id)
            .options(
                selectinload(Article.images),
                selectinload(Article.page).selectinload(Page.pdf_file).selectinload(PdfFile.job),
            )
        )
        if article is None and raise_on_missing:
            raise DemoServiceError("article not found", status_code=404)
        return article

    def _build_overlay_boxes(
        self,
        regions: list[Any],
        selected_article_id: int,
        width: int,
        height: int,
        preview_articles: list[Any],
    ) -> list[DemoOverlayBox]:
        selected_bbox = None
        selected_title_bbox = None
        for preview_article in preview_articles:
            if preview_article.article_id == selected_article_id:
                selected_bbox = preview_article.article_bbox
                selected_title_bbox = preview_article.title_bbox
                break

        boxes: list[DemoOverlayBox] = []
        for region in regions:
            bbox = list(region.bbox or [])
            if len(bbox) != 4:
                continue
            selected = bbox == selected_bbox or bbox == selected_title_bbox
            label = "selected" if selected and region.label == "article" else region.label
            boxes.append(
                DemoOverlayBox(
                    label=label,
                    text=region.text,
                    style=self._bbox_style(bbox, width, height),
                    selected=selected,
                    muted=not selected and region.label in {"article", "title", "image"},
                )
            )
        return boxes

    @staticmethod
    def _preview_target_article_id(page: Page | None, *, selected_article: Article) -> int | None:
        if page is None:
            return None
        if page.id == selected_article.page_id:
            return selected_article.id
        articles = sorted(page.articles or [], key=lambda item: (item.article_order, item.id))
        return articles[0].id if articles else None

    @staticmethod
    def _bbox_style(bbox: list[int], width: int, height: int) -> str:
        left = max((bbox[0] / max(width, 1)) * 100, 0.0)
        top = max((bbox[1] / max(height, 1)) * 100, 0.0)
        box_width = max(((bbox[2] - bbox[0]) / max(width, 1)) * 100, 0.1)
        box_height = max(((bbox[3] - bbox[1]) / max(height, 1)) * 100, 0.1)
        return f"left:{left:.4f}%;top:{top:.4f}%;width:{box_width:.4f}%;height:{box_height:.4f}%;"

    def _load_raw_payload(self, path_value: str | None) -> dict[str, Any] | list[Any] | str | None:
        if not path_value:
            return None
        path = self.settings.resolve_output_path(path_value)
        if path is None:
            return None
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return path.read_text(encoding="utf-8")

    def _extract_raw_ocr_text(self, raw_payload: dict[str, Any] | list[Any] | str | None, article_bbox: list[int] | None) -> str:
        if article_bbox is None:
            return ""
        candidates: list[tuple[int, int, str]] = []
        if isinstance(raw_payload, dict):
            parsing_items = list(raw_payload.get("parsing_res_list", []) or [])
            for item in parsing_items:
                if not isinstance(item, dict):
                    continue
                bbox = bbox_from_any(item.get("bbox") or item.get("polygon_points") or item.get("ori_bbox"))
                text = str(item.get("content") or "").strip()
                if bbox is None or not text:
                    continue
                if box_intersection_area(bbox, article_bbox) <= 0:
                    continue
                candidates.append((bbox[1], bbox[0], text))

            if not candidates:
                ocr_res = raw_payload.get("overall_ocr_res", raw_payload)
                texts = list(ocr_res.get("rec_texts", []) or [])
                boxes = (
                    list(ocr_res.get("rec_boxes", []) or [])
                    or list(ocr_res.get("rec_polys", []) or [])
                    or list(ocr_res.get("dt_polys", []) or [])
                )
                for index, text in enumerate(texts):
                    bbox = bbox_from_any(boxes[index]) if index < len(boxes) else None
                    content = str(text or "").strip()
                    if bbox is None or not content:
                        continue
                    if box_intersection_area(bbox, article_bbox) <= 0:
                        continue
                    candidates.append((bbox[1], bbox[0], content))

        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return "\n".join(text for _, _, text in candidates).strip()

    def _build_article_body_html(
        self,
        *fallback_texts: Any,
        markdown_path: Path | None = None,
    ) -> str:
        body_source = self._pick_first_text(*fallback_texts)
        if not body_source.strip() and markdown_path is not None:
            markdown_text = ""
            try:
                markdown_text = markdown_path.read_text(encoding="utf-8")
            except OSError:
                markdown_text = ""
            body_source = self._extract_article_body_markdown(markdown_text) if markdown_text.strip() else ""

        rendered = self._render_markdown_fragment(body_source)
        return rendered or '<p class="empty-inline">본문이 없습니다.</p>'

    @staticmethod
    def _display_title(*candidates: Any) -> str:
        for candidate in candidates:
            cleaned = DemoService._clean_display_text(candidate)
            if cleaned:
                return cleaned
        return "Untitled"

    @staticmethod
    def _clean_display_text(value: Any) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        text = html.unescape(text)
        text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"^[#>\-\s]+", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _is_likely_header_title(title: str, body_length: int) -> bool:
        normalized = DemoService._clean_display_text(title).lower()
        if not normalized:
            return True
        if body_length <= 80 and len(normalized) <= 18:
            return True
        return any(token in normalized for token in ("문화일보", "아시아경제", "헤럴드", "내일신문", "중앙일보", "조선일보", "동아일보"))

    @staticmethod
    def _strip_html_markup(value: str) -> str:
        text = html.unescape(value)
        text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    @staticmethod
    def _extract_article_body_markdown(markdown_text: str) -> str:
        lines = markdown_text.replace("\r", "").splitlines()
        if not lines:
            return ""

        seen_title = False
        collecting_metadata = False
        body_lines: list[str] = []

        for line in lines:
            stripped = line.strip()
            if not seen_title:
                if stripped.startswith("#"):
                    seen_title = True
                    collecting_metadata = True
                continue

            if stripped == "## Images":
                break

            if collecting_metadata:
                if not stripped:
                    continue
                if DemoService._is_metadata_line(stripped):
                    continue
                collecting_metadata = False
                body_lines.append(line)
                continue

            body_lines.append(line)

        if not seen_title:
            return markdown_text.strip()
        return "\n".join(body_lines).strip()

    @staticmethod
    def _is_metadata_line(text: str) -> bool:
        return text.startswith("- ") and ":" in text[2:]

    @staticmethod
    def _render_markdown_fragment(markdown_text: str) -> str:
        lines = markdown_text.replace("\r", "").splitlines()
        if not lines:
            return ""

        fragments: list[str] = []
        list_type: str | None = None
        list_items: list[str] = []

        def flush_list() -> None:
            nonlocal list_type
            if not list_items:
                return
            tag = "ol" if list_type == "ol" else "ul"
            items_html = "".join(f"<li>{DemoService._render_inline_markdown(item)}</li>" for item in list_items)
            fragments.append(f"<{tag}>{items_html}</{tag}>")
            list_items.clear()
            list_type = None

        for raw_line in lines:
            stripped = raw_line.strip()
            if not stripped:
                flush_list()
                continue

            if stripped == "## Images":
                flush_list()
                break

            if stripped.startswith("#"):
                flush_list()
                level = min(len(stripped) - len(stripped.lstrip("#")), 6)
                content = DemoService._strip_html_markup(stripped[level:].strip())
                if content:
                    fragments.append(f"<h{level}>{DemoService._render_inline_markdown(content)}</h{level}>")
                continue

            if stripped.startswith("> "):
                flush_list()
                fragments.append(f"<blockquote>{DemoService._render_inline_markdown(DemoService._strip_html_markup(stripped[2:].strip()))}</blockquote>")
                continue

            if stripped.startswith("![") and "](" in stripped:
                flush_list()
                continue

            ordered_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
            if stripped.startswith("- ") and not DemoService._is_metadata_line(stripped):
                if list_type == "ol":
                    flush_list()
                list_type = "ul"
                list_items.append(DemoService._strip_html_markup(stripped[2:].strip()))
                continue
            if ordered_match:
                if list_type == "ul":
                    flush_list()
                list_type = "ol"
                list_items.append(DemoService._strip_html_markup(ordered_match.group(2).strip()))
                continue

            flush_list()
            fragments.append(f"<p>{DemoService._render_inline_markdown(DemoService._strip_html_markup(stripped))}</p>")

        flush_list()
        return "\n".join(fragments).strip()

    @staticmethod
    def _render_inline_markdown(text: str) -> str:
        escaped = html.escape(text)
        escaped = re.sub(r"`([^`]+)`", lambda match: f"<code>{match.group(1)}</code>", escaped)
        escaped = re.sub(r"\*\*([^*]+)\*\*", lambda match: f"<strong>{match.group(1)}</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", lambda match: f"<em>{match.group(1)}</em>", escaped)
        return escaped

    def _read_state_sidecars(self, bundle_dir: Path) -> dict[str, Any]:
        metadata = self._read_json(bundle_dir / "article.json")
        enrichment_payload = self._read_json(bundle_dir / "enrichment.json")
        delivery_payload = self._read_json(bundle_dir / "demo_delivery.json")
        if not delivery_payload:
            delivery_payload = self._read_json(bundle_dir / "delivery.json")

        annotation_path = self._find_annotation_path(bundle_dir)
        annotation_payload = self._read_json(annotation_path) if annotation_path is not None else {}
        correction_source = "annotation" if annotation_payload else (
            self._pick_first_text(
                enrichment_payload.get("correction_source"),
                metadata.get("correction_source"),
            )
            or "ocr"
        )

        delivery_status = self._pick_first(
            delivery_payload.get("delivery_status"),
            delivery_payload.get("status"),
            metadata.get("delivery_status"),
            metadata.get("status") if "delivery" in metadata else None,
        )
        return {
            "metadata": metadata,
            "annotation_payload": annotation_payload,
            "annotation_path": annotation_path,
            "enrichment_payload": enrichment_payload,
            "delivery_payload": delivery_payload,
            "delivery_status": delivery_status,
            "correction_source": correction_source,
        }

    def _find_annotation_path(self, bundle_dir: Path) -> Path | None:
        direct = bundle_dir / "annotation.json"
        if direct.exists():
            return direct

        relative: Path | None = None
        for output_root in self.settings.output_roots():
            try:
                relative = bundle_dir.relative_to(output_root)
                break
            except ValueError:
                continue
        if relative is None:
            return None

        extra_roots = [value.strip() for value in os.getenv("DEMO_LABEL_ROOTS", "").split(os.pathsep) if value.strip()]
        candidate_roots = [Path(value) for value in extra_roots] if extra_roots else [
            self.settings.output_root.parent / "_tmp_labels",
            self.settings.output_root.parent / "_tmp_labels_bbox",
            self.settings.output_root.parent / "labels",
        ]
        for root in candidate_roots:
            for candidate in self._iter_annotation_candidates(root, relative):
                if candidate.exists():
                    return candidate
        return None

    @staticmethod
    def _iter_annotation_candidates(root: Path, relative: Path) -> list[Path]:
        candidates = [root / relative / "annotation.json"]
        if root.exists():
            for child in root.iterdir():
                if child.is_dir():
                    candidates.append(child / relative / "annotation.json")
        return candidates

    @staticmethod
    def _read_json(path: Path | None) -> dict[str, Any]:
        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _pick_first(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    @staticmethod
    def _pick_first_text(*values: Any) -> str:
        for value in values:
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
        return ""

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _as_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def _build_delivery_request_preview(
        self,
        *,
        title: str,
        body_text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        source_metadata = metadata.get("source_metadata") if isinstance(metadata.get("source_metadata"), dict) else {}
        metadata_images = metadata.get("images") if isinstance(metadata.get("images"), list) else []
        images: list[dict[str, Any]] = []
        for image_index, image in enumerate(metadata_images):
            if not isinstance(image, dict):
                continue
            image_path = str(image.get("image_path") or image.get("relative_path") or f"image_{image_index + 1}")
            captions = image.get("captions") if isinstance(image.get("captions"), list) else []
            caption_text = " ".join(
                str(caption.get("text") or "").strip()
                for caption in captions
                if isinstance(caption, dict) and str(caption.get("text") or "").strip()
            ).strip()
            images.append(
                {
                    "caption": caption_text[:30] or None,
                    "src": Path(image_path).name or image_path,
                }
            )

        return {
            "title": title[:30].strip(),
            "body_text": (body_text or "")[:2000].strip(),
            "imgs": images,
            "relevance_score": max(
                0.0,
                min(
                    self._as_float(
                        self._pick_first(
                            metadata.get("relevance_score"),
                            metadata.get("score"),
                        )
                    )
                    or 0.0,
                    1.0,
                ),
            ),
            "publication": str(source_metadata.get("publication") or "").strip()[:20],
            "issue_date": str(source_metadata.get("issue_date") or "").strip()[:10],
        }

    @staticmethod
    def _json_text(payload: Any) -> str:
        if payload in (None, ""):
            return ""
        if isinstance(payload, str):
            return payload
        return json.dumps(payload, ensure_ascii=False, indent=2)

    @staticmethod
    def _format_timestamp(value: Any) -> str | None:
        if value in (None, ""):
            return None
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _resolve_redelivery_hook() -> RedeliveryHook | None:
        env_specs = [value.strip() for value in [os.getenv("DEMO_REDELIVER_HOOK"), os.getenv("ARTICLE_REDELIVER_HOOK")] if value]
        candidate_specs = env_specs or [
            "app.services.delivery:redeliver_article",
            "app.services.delivery_service:redeliver_article",
            "app.services.article_delivery:redeliver_article",
            "app.services.article_actions:redeliver_article",
        ]
        for spec in candidate_specs:
            hook = DemoService._import_callable(spec)
            if hook is not None:
                return hook
        return None

    @staticmethod
    def _import_callable(spec: str) -> RedeliveryHook | None:
        if ":" not in spec:
            return None
        module_name, attr_name = spec.split(":", 1)
        try:
            module = importlib.import_module(module_name)
            value = getattr(module, attr_name)
        except Exception:  # noqa: BLE001
            return None
        return value if callable(value) else None

    @staticmethod
    async def _invoke_redelivery_hook(
        hook: RedeliveryHook,
        *,
        db: Session,
        article: Article,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result = hook(db=db, article=article, payload=payload)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, dict):
            return result
        return {}
