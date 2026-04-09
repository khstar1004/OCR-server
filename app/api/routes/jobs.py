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
    JobDetailResponse,
    JobResultResponse,
    JobRunDailyRequest,
    JobStatusResponse,
    PagePreviewResponse,
)
from app.services.job_runner import JobRunner
from app.services.preview_builder import VALID_PREVIEW_OVERLAYS, build_page_preview, get_page_for_job
from app.services.job_scheduler import get_job_scheduler
from app.services.result_builder import build_job_detail, build_job_result, build_job_status
from app.utils.geometry import normalize_bbox_to_page, should_scale_bboxes_to_page

router = APIRouter(tags=["jobs"])


def _get_job_by_key(db: Session, job_key: str) -> Job:
    job = db.scalar(select(Job).where(Job.job_key == job_key))
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return job


@router.post("/jobs/run-daily", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_daily(request: JobRunDailyRequest, db: Session = Depends(get_db)) -> JobCreatedResponse:
    runner = JobRunner(db)
    job = runner.create_job(request)
    db.commit()
    db.refresh(job)
    await get_job_scheduler().schedule(job.id)
    return JobCreatedResponse(job_id=job.job_key, status=job.status)


@router.post("/jobs/run-single", response_model=JobCreatedResponse, status_code=status.HTTP_202_ACCEPTED)
async def run_single_pdf(
    request: Request,
    file_name: str = Query(..., min_length=1),
    force_reprocess: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> JobCreatedResponse:
    safe_name = Path(file_name).name.strip()
    if not safe_name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file_name is required")
    if Path(safe_name).suffix.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only .pdf files are supported")

    settings = get_settings()
    upload_root = settings.output_root / "_uploaded_inputs"
    upload_root.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(tempfile.mkdtemp(prefix="single_pdf_", dir=str(upload_root)))
    staged_path = staging_dir / safe_name
    written_bytes = 0

    try:
        with staged_path.open("wb") as handle:
            async for chunk in request.stream():
                if not chunk:
                    continue
                written_bytes += len(chunk)
                handle.write(chunk)
        if written_bytes == 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty request body")

        runner = JobRunner(db)
        job = runner.create_job(JobRunDailyRequest(source_dir=str(staging_dir), force_reprocess=force_reprocess))
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
