from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from pydantic import BaseModel, Field


class JobRunDailyRequest(BaseModel):
    source_dir: str | None = Field(default=None)
    date: date_type | None = Field(default=None)
    callback_url: str | None = Field(default=None)
    force_reprocess: bool = Field(default=False)
    ocr_mode: str = Field(default="balanced")
    page_range: str | None = Field(default=None)
    max_pages: int | None = Field(default=None)
    output_format: str = Field(default="markdown")
    paginate: bool = Field(default=False)
    add_block_ids: bool = Field(default=False)
    include_markdown_in_chunks: bool = Field(default=False)
    skip_cache: bool = Field(default=False)


class JobCreatedResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    total_pdfs: int = 0
    processed_pdfs: int = 0
    total_articles: int = 0


class ArticleCaptionResponse(BaseModel):
    text: str
    bbox: list[int] | None = None
    confidence: float | None = None


class ArticleImageResponse(BaseModel):
    image_id: int
    image_path: str
    bbox: list[int]
    captions: list[ArticleCaptionResponse] = Field(default_factory=list)


class ArticleSourceMetadataResponse(BaseModel):
    publication: str | None = None
    issue_date: str | None = None
    issue_date_text: str | None = None
    issue_weekday: str | None = None
    issue_page: str | None = None
    issue_page_label: str | None = None
    issue_section: str | None = None
    raw_publication_text: str | None = None
    raw_issue_text: str | None = None
    publication_bbox: list[int] | None = None
    issue_bbox: list[int] | None = None


class OcrQualityResponse(BaseModel):
    status: str
    score: float
    char_count: int = 0
    korean_ratio: float = 0.0
    average_confidence: float = 0.0
    block_count: int = 0
    image_count: int = 0
    article_count: int = 0
    needs_review: bool = False
    reasons: list[str] = Field(default_factory=list)


class ArticleResponse(BaseModel):
    article_id: int
    page_number: int
    article_order: int
    title: str
    body_text: str
    original_title: str | None = None
    original_body_text: str | None = None
    corrected_title: str | None = None
    corrected_body_text: str | None = None
    correction_source: str | None = None
    correction_model: str | None = None
    title_bbox: list[int] | None
    article_bbox: list[int] | None
    relevance_score: float | None = None
    relevance_reason: str | None = None
    relevance_label: str | None = None
    relevance_model: str | None = None
    relevance_source: str | None = None
    source_metadata: ArticleSourceMetadataResponse | None = None
    ocr_quality: OcrQualityResponse | None = None
    delivery_status: str | None = None
    delivery_response_code: int | None = None
    delivery_last_error: str | None = None
    delivery_updated_at: str | None = None
    delivery_request_available: bool = False
    images: list[ArticleImageResponse]
    bundle_dir: str | None = None
    markdown_path: str | None = None
    metadata_path: str | None = None


class FileResultResponse(BaseModel):
    pdf_file: str
    pages: int
    articles: list[ArticleResponse]


class JobResultResponse(BaseModel):
    job_id: str
    status: str
    files: list[FileResultResponse]


class NewsPayloadImageCheckResponse(BaseModel):
    image_id: int
    image_path: str | None = None
    src: str | None = None
    included: bool
    reason: str | None = None
    size_bytes: int | None = None
    caption: str = ""


class NewsPayloadArticleResponse(BaseModel):
    article_id: int
    page_number: int
    article_order: int
    validation_status: str = "unknown"
    validation_reasons: list[str] = Field(default_factory=list)
    title: str
    body_text_length: int
    relevance_score: float
    publication: str
    issue_date: str
    included_image_count: int
    skipped_image_count: int
    images: list[NewsPayloadImageCheckResponse] = Field(default_factory=list)
    request_article: dict


class JobNewsPayloadResponse(BaseModel):
    job_id: str
    status: str
    target_url: str | None = None
    target_configured: bool
    delivery_status: str = "unknown"
    ready_count: int = 0
    warning_count: int = 0
    blocked_count: int = 0
    validation_summary: list[str] = Field(default_factory=list)
    article_count: int
    included_image_count: int
    skipped_image_count: int
    articles: list[NewsPayloadArticleResponse]
    body: list[dict]


class JobDeliveryRunResponse(BaseModel):
    job_id: str
    target_url: str
    delivered: int
    failed: int
    skipped: int = 0


class JobQualityResponse(BaseModel):
    status: str = "unknown"
    average_score: float | None = None
    ready_pages: int = 0
    warning_pages: int = 0
    blocked_pages: int = 0
    review_pages: int = 0
    measured_pages: int = 0
    top_reasons: list[str] = Field(default_factory=list)


class JobStageResponse(BaseModel):
    stage_key: str
    label: str
    status: str
    message: str | None = None
    updated_at: datetime | None = None


class PageProgressResponse(BaseModel):
    page_id: int
    page_number: int
    status: str
    article_count: int = 0
    quality_status: str | None = None
    quality_score: float | None = None
    quality_reasons: list[str] = Field(default_factory=list)


class PdfProgressResponse(BaseModel):
    pdf_file_id: int
    file_name: str
    status: str
    page_count: int = 0
    parsed_pages: int = 0
    failed_pages: int = 0
    article_count: int = 0
    skip_reason: str | None = None
    processed_at: datetime | None = None
    pages: list[PageProgressResponse] = Field(default_factory=list)


class ProcessingLogEntryResponse(BaseModel):
    created_at: datetime
    step_name: str
    status: str
    message: str
    pdf_file: str | None = None
    page_number: int | None = None


class JobDetailResponse(BaseModel):
    job_id: str
    status: str
    source_dir: str
    requested_date: date_type | None = None
    requested_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    total_pdfs: int = 0
    processed_pdfs: int = 0
    success_pdfs: int = 0
    failed_pdfs: int = 0
    total_articles: int = 0
    progress_percent: float = 0.0
    quality: JobQualityResponse | None = None
    stages: list[JobStageResponse]
    pdf_files: list[PdfProgressResponse]
    recent_logs: list[ProcessingLogEntryResponse]


class PreviewRegionResponse(BaseModel):
    label: str
    bbox: list[int]
    text: str | None = None
    confidence: float | None = None
    color: str | None = None


class PreviewArticleImageResponse(BaseModel):
    image_id: int
    image_url: str
    bbox: list[int]
    captions: list[ArticleCaptionResponse] = Field(default_factory=list)


class PreviewArticleResponse(BaseModel):
    article_id: int
    title: str
    body_text: str
    title_bbox: list[int] | None = None
    article_bbox: list[int] | None = None
    relevance_score: float | None = None
    relevance_reason: str | None = None
    relevance_label: str | None = None
    relevance_model: str | None = None
    relevance_source: str | None = None
    images: list[PreviewArticleImageResponse] = Field(default_factory=list)


class PagePreviewResponse(BaseModel):
    page_id: int
    pdf_file: str
    page_number: int
    parse_status: str
    width: int
    height: int
    image_url: str
    overlay_type: str
    regions: list[PreviewRegionResponse]
    articles: list[PreviewArticleResponse]
    raw_payload: dict | list | str | None = None
