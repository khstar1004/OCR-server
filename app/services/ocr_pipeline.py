from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.ocr.chandra import ChandraHFConfig, ChandraHFLocalRunner, run_chandra_hf
from app.ocr.rendering import render_pdf_document
from app.ocr.types import OCRDocumentResult, RenderedPdf
from app.segmentation.models import ArticleCandidate, PageSegmentationResult
from app.segmentation.newspaper import segment_newspaper_pages
from app.services.artifacts import JobArtifactLayout, build_job_artifact_layout


@dataclass(frozen=True, slots=True)
class OCRPipelineArtifacts:
    layout: JobArtifactLayout
    rendered_pdf: RenderedPdf
    ocr_result: OCRDocumentResult
    page_results: tuple[PageSegmentationResult, ...]
    article_candidates: tuple[ArticleCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "layout": {
                "data_dir": str(self.layout.data_dir),
                "job_id": self.layout.job_id,
                "source_key": self.layout.source_key,
                "document_dir": str(self.layout.document_dir),
            },
            "rendered_pdf": self.rendered_pdf.to_dict(),
            "ocr_result": self.ocr_result.to_dict(),
            "page_results": [page.to_dict() for page in self.page_results],
            "article_candidates": [article.to_dict() for article in self.article_candidates],
        }


def _artifact_layout(
    pdf_path: str | Path,
    data_dir: str | Path,
    job_id: str,
    *,
    source_key: str | None = None,
) -> JobArtifactLayout:
    return build_job_artifact_layout(data_dir, job_id, pdf_path, source_key=source_key)


def render_pdf(
    pdf_path: str | Path,
    data_dir: str | Path,
    job_id: str,
    *,
    source_key: str | None = None,
    dpi: int = 200,
) -> RenderedPdf:
    layout = _artifact_layout(pdf_path, data_dir, job_id, source_key=source_key)
    return render_pdf_document(pdf_path, layout, dpi=dpi)


def run_chandra_ocr(
    rendered_pdf: RenderedPdf,
    data_dir: str | Path,
    job_id: str,
    *,
    source_key: str | None = None,
    model_id: str = "datalab-to/chandra-ocr-2",
    prompt_type: str = "ocr_layout",
    batch_size: int = 1,
    runner: Any | None = None,
) -> OCRDocumentResult:
    layout = _artifact_layout(rendered_pdf.pdf_path, data_dir, job_id, source_key=source_key or rendered_pdf.source_key)
    config = ChandraHFConfig(model_id=model_id, prompt_type=prompt_type, batch_size=batch_size)
    active_runner = runner or ChandraHFLocalRunner(config=config)
    return run_chandra_hf(rendered_pdf, layout, config=config, runner=active_runner)


def segment_pages(
    rendered_pdf: RenderedPdf,
    ocr_result: OCRDocumentResult,
    data_dir: str | Path,
    job_id: str,
    *,
    source_key: str | None = None,
) -> tuple[PageSegmentationResult, ...]:
    layout = _artifact_layout(rendered_pdf.pdf_path, data_dir, job_id, source_key=source_key or rendered_pdf.source_key)
    return segment_newspaper_pages(rendered_pdf, ocr_result, layout)


def segment_articles(
    rendered_pdf: RenderedPdf,
    ocr_result: OCRDocumentResult,
    data_dir: str | Path,
    job_id: str,
    *,
    source_key: str | None = None,
) -> tuple[ArticleCandidate, ...]:
    page_results = segment_pages(
        rendered_pdf=rendered_pdf,
        ocr_result=ocr_result,
        data_dir=data_dir,
        job_id=job_id,
        source_key=source_key,
    )
    return tuple(article for page in page_results for article in page.articles)


def process_pdf(
    pdf_path: str | Path,
    data_dir: str | Path,
    job_id: str,
    *,
    source_key: str | None = None,
    dpi: int = 200,
    model_id: str = "datalab-to/chandra-ocr-2",
    prompt_type: str = "ocr_layout",
    batch_size: int = 1,
    runner: Any | None = None,
) -> OCRPipelineArtifacts:
    layout = _artifact_layout(pdf_path, data_dir, job_id, source_key=source_key)
    rendered_pdf = render_pdf_document(pdf_path, layout, dpi=dpi)
    config = ChandraHFConfig(model_id=model_id, prompt_type=prompt_type, batch_size=batch_size)
    ocr_result = run_chandra_hf(
        rendered_pdf=rendered_pdf,
        artifact_layout=layout,
        config=config,
        runner=runner or ChandraHFLocalRunner(config=config),
    )
    page_results = segment_newspaper_pages(rendered_pdf, ocr_result, layout)
    return OCRPipelineArtifacts(
        layout=layout,
        rendered_pdf=rendered_pdf,
        ocr_result=ocr_result,
        page_results=page_results,
        article_candidates=tuple(article for page in page_results for article in page.articles),
    )
