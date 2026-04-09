from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.container import get_jobs_port
from app.models import ArticleListResponse, ArticleRecord, JobListResponse, JobRecord
from app.services.jobs import JobsPort


router = APIRouter(prefix="/api")


@router.get("/jobs", response_model=JobListResponse, status_code=status.HTTP_200_OK)
def list_jobs(jobs: JobsPort = Depends(get_jobs_port)) -> JobListResponse:
    items = jobs.list_jobs()
    return JobListResponse(items=items, count=len(items))


@router.get("/jobs/{job_id}", response_model=JobRecord, status_code=status.HTTP_200_OK)
def get_job(job_id: int, jobs: JobsPort = Depends(get_jobs_port)) -> JobRecord:
    record = jobs.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return record


@router.get("/articles", response_model=ArticleListResponse, status_code=status.HTTP_200_OK)
def list_articles(
    job_id: int | None = Query(default=None),
    jobs: JobsPort = Depends(get_jobs_port),
) -> ArticleListResponse:
    items = jobs.list_articles(job_id=job_id)
    return ArticleListResponse(items=items, count=len(items))


@router.get("/articles/{article_id}", response_model=ArticleRecord, status_code=status.HTTP_200_OK)
def get_article(article_id: int, jobs: JobsPort = Depends(get_jobs_port)) -> ArticleRecord:
    record = jobs.get_article(article_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    return record
