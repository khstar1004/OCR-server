from io import BytesIO
import shutil
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Article, ArticleImage, Job, Page, PdfFile
from app.db.session import get_db
from app.schemas.job import (
    JobCreatedResponse,
    JobDeliveryRunResponse,
    JobDetailResponse,
    JobNewsPayloadResponse,
    JobResultResponse,
    JobRunDailyRequest,
    JobStatusResponse,
    PagePreviewResponse,
)
from app.services.file_scanner import IMAGE_INPUT_SUFFIXES, PDF_INPUT_SUFFIXES, SUPPORTED_INPUT_SUFFIXES
from app.services.job_runner import JobRunner
from app.services.job_options import normalize_job_ocr_options
from app.services.news_delivery import NewsDeliveryClient, NewsDeliveryError
from app.services.preview_builder import VALID_PREVIEW_OVERLAYS, build_page_preview, get_page_for_job
from app.services.job_scheduler import get_job_scheduler
from app.services.result_builder import build_job_detail, build_job_result, build_job_status
from app.utils.geometry import normalize_bbox_to_page, should_scale_bboxes_to_page

router = APIRouter(tags=["jobs"])
MAX_RUN_SINGLE_UPLOAD_BYTES = 512 * 1024 * 1024
HEADER_PROBE_BYTES = 1024


def _get_job_by_key(db: Session, job_key: str) -> Job:
    job = db.scalar(select(Job).where(Job.job_key == job_key))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


@router.post("/jobs/run-daily", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_daily(request: JobRunDailyRequest, db: Session = Depends(get_db)) -> JobCreatedResponse:
    runner = JobRunner(db)
    try:
        job = runner.create_job(request)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    db.commit()
    db.refresh(job)
    await get_job_scheduler().schedule(job.id)
    return JobCreatedResponse(job_id=job.job_key, status=job.status)


@router.post("/jobs/run-single", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_single_pdf(
    request: Request,
    file_name: str = Query(..., min_length=1),
    force_reprocess: bool = Query(default=True),
    ocr_mode: str = Query(default="balanced"),
    page_range: str | None = Query(default=None),
    max_pages: int | None = Query(default=None),
    output_format: str = Query(default="markdown"),
    paginate: bool = Query(default=False),
    add_block_ids: bool = Query(default=False),
    include_markdown_in_chunks: bool = Query(default=False),
    skip_cache: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> JobCreatedResponse:
    safe_name = Path(file_name).name.strip()
    if not safe_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_name is required")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_INPUT_SUFFIXES:
        allowed = ", ".join(sorted(SUPPORTED_INPUT_SUFFIXES))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"only these file types are supported: {allowed}")

    settings = get_settings()
    try:
        ocr_options = normalize_job_ocr_options(
            {
                "ocr_mode": ocr_mode,
                "page_range": page_range,
                "max_pages": max_pages,
                "output_format": output_format,
                "paginate": paginate,
                "add_block_ids": add_block_ids,
                "include_markdown_in_chunks": include_markdown_in_chunks,
                "skip_cache": skip_cache,
            }
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    upload_root = settings.output_root / "_uploaded_inputs"
    upload_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix="single_source_", dir=str(upload_root)))
    staged_path = staging_dir / safe_name
    written_bytes = 0
    header_probe = bytearray()

    try:
        with staged_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written_bytes += len(chunk)
                if written_bytes > MAX_RUN_SINGLE_UPLOAD_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file upload is too large")
                if len(header_probe) < HEADER_PROBE_BYTES:
                    header_probe.extend(chunk[: HEADER_PROBE_BYTES - len(header_probe)])
                handle.write(chunk)
        if written_bytes == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty request body")
        _validate_run_single_payload(staged_path, bytes(header_probe))

        runner = JobRunner(db)
        job = runner.create_job(
            JobRunDailyRequest(
                source_dir=str(staging_dir),
                force_reprocess=force_reprocess,
                ocr_mode=ocr_options["ocr_mode"],
                page_range=ocr_options["page_range"],
                max_pages=ocr_options["max_pages"],
                output_format=ocr_options["output_format"],
                paginate=ocr_options["paginate"],
                add_block_ids=ocr_options["add_block_ids"],
                include_markdown_in_chunks=ocr_options["include_markdown_in_chunks"],
                skip_cache=ocr_options["skip_cache"],
            )
        )
        db.commit()
        db.refresh(job)
    except HTTPException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise

    await get_job_scheduler().schedule(job.id)
    return JobCreatedResponse(job_id=job.job_key, status=job.status)


def _validate_run_single_payload(path: Path, header_probe: bytes) -> None:
    suffix = path.suffix.lower()
    if suffix in PDF_INPUT_SUFFIXES:
        if b"%PDF-" not in header_probe:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request body is not a PDF")
        return
    if suffix in IMAGE_INPUT_SUFFIXES:
        try:
            with Image.open(path) as image:
                image.verify()
        except OSError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="request body is not a supported image") from exc
        return
    allowed = ", ".join(sorted(SUPPORTED_INPUT_SUFFIXES))
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"only these file types are supported: {allowed}")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
def get_job_status(job_id: str, db: Session = Depends(get_db)) -> JobStatusResponse:
    job = _get_job_by_key(db, job_id)
    return build_job_status(db, job)


@router.get("/jobs/{job_id}/detail", response_model=JobDetailResponse)
def get_job_detail(job_id: str, db: Session = Depends(get_db)) -> JobDetailResponse:
    job = _get_job_by_key(db, job_id)
    return build_job_detail(db, job)


@router.get("/jobs/{job_id}/result", response_model=JobResultResponse)
def get_job_result(job_id: str, db: Session = Depends(get_db)) -> JobResultResponse:
    job = _get_job_by_key(db, job_id)
    return build_job_result(db, job)


@router.get("/jobs/{job_id}/news-payload", response_model=JobNewsPayloadResponse)
def get_job_news_payload(job_id: str, db: Session = Depends(get_db)) -> JobNewsPayloadResponse:
    job = _get_job_by_key(db, job_id)
    result = build_job_result(db, job)
    articles = [article for file_result in result.files for article in file_result.articles]
    preview = NewsDeliveryClient().build_payload_preview(articles)
    return JobNewsPayloadResponse(job_id=result.job_id, status=result.status, **preview)


@router.post("/jobs/{job_id}/deliver", response_model=JobDeliveryRunResponse)
def deliver_job(job_id: str, db: Session = Depends(get_db)) -> JobDeliveryRunResponse:
    job = _get_job_by_key(db, job_id)
    result = build_job_result(db, job)
    try:
        delivery_result = NewsDeliveryClient().deliver_job_result(result)
    except NewsDeliveryError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return JobDeliveryRunResponse(
        job_id=result.job_id,
        target_url=delivery_result.target_url,
        delivered=delivery_result.delivered,
        failed=delivery_result.failed,
        skipped=delivery_result.skipped,
    )


@router.get("/jobs/{job_id}/pages/{page_id}/preview", response_model=PagePreviewResponse)
def get_page_preview(
    job_id: str,
    page_id: int,
    overlay: str = Query(default="merged"),
    db: Session = Depends(get_db),
) -> PagePreviewResponse:
    job = _get_job_by_key(db, job_id)
    if overlay not in VALID_PREVIEW_OVERLAYS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid overlay type")
    page = get_page_for_job(db, job, page_id)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page not found")
    return build_page_preview(db, job, page, overlay, get_settings().api_prefix)


@router.get("/jobs/{job_id}/pages/{page_id}/image")
def get_page_image(job_id: str, page_id: int, db: Session = Depends(get_db)) -> FileResponse:
    job = _get_job_by_key(db, job_id)
    page = get_page_for_job(db, job, page_id)
    if page is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page not found")
    image_path = get_settings().resolve_output_path(page.page_image_path)
    if image_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page image not found")
    if not image_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page image not found")
    return FileResponse(image_path)


@router.get("/jobs/{job_id}/article-images/{image_id}")
def get_article_image(job_id: str, image_id: int, db: Session = Depends(get_db)) -> FileResponse:
    job = _get_job_by_key(db, job_id)
    article_image = db.scalar(
        select(ArticleImage)
        .join(Article, Article.id == ArticleImage.article_id)
        .join(Page, Page.id == Article.page_id)
        .join(PdfFile, PdfFile.id == Page.pdf_file_id)
        .where(ArticleImage.id == image_id, PdfFile.job_id == job.id)
    )
    if article_image is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article image not found")
    page = db.get(Page, article_image.page_id)
    if page is not None:
        page_image_path = get_settings().resolve_output_path(page.page_image_path)
        should_recrop = should_scale_bboxes_to_page([article_image.image_bbox], page.width, page.height)
        normalized_bbox = normalize_bbox_to_page(article_image.image_bbox, page.width, page.height) if should_recrop else None
        if (
            normalized_bbox is not None
            and page_image_path is not None
            and page_image_path.exists()
        ):
            with Image.open(page_image_path) as source:
                crop = source.crop(tuple(normalized_bbox))
                buffer = BytesIO()
                crop.save(buffer, format="PNG")
                buffer.seek(0)
            return StreamingResponse(buffer, media_type="image/png")
    image_path = get_settings().resolve_output_path(article_image.image_path)
    if image_path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article image file not found")
    if not image_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="article image file not found")
    return FileResponse(image_path)
