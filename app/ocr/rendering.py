from __future__ import annotations

from pathlib import Path

from PIL import Image

from app.ocr.types import PageImageArtifact, RenderedPdf
from app.services.artifacts import JobArtifactLayout


def _load_pymupdf():
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError(
            "PyMuPDF is required to render PDFs. Install the 'PyMuPDF' package."
        ) from exc
    return fitz


def _read_existing_png_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return int(image.width), int(image.height)


def render_pdf_document(
    pdf_path: str | Path,
    artifact_layout: JobArtifactLayout,
    *,
    dpi: int = 200,
) -> RenderedPdf:
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF file was not found: {pdf_path}")
    if dpi <= 0:
        raise ValueError("DPI must be a positive integer.")

    artifact_layout.ensure()
    fitz = _load_pymupdf()
    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)

    document = fitz.open(pdf_path)
    try:
        pages: list[PageImageArtifact] = []
        for index in range(document.page_count):
            page_no = index + 1
            image_path = artifact_layout.page_image_path(page_no)
            if image_path.exists():
                width, height = _read_existing_png_size(image_path)
            else:
                page = document.load_page(index)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image_path.parent.mkdir(parents=True, exist_ok=True)
                pixmap.save(str(image_path))
                width = int(pixmap.width)
                height = int(pixmap.height)

            pages.append(
                PageImageArtifact(
                    page_no=page_no,
                    image_path=image_path,
                    width=width,
                    height=height,
                    source_pdf=pdf_path,
                    dpi=dpi,
                )
            )
    finally:
        document.close()

    return RenderedPdf(
        pdf_path=pdf_path,
        job_id=artifact_layout.job_id,
        source_key=artifact_layout.source_key,
        artifact_root=artifact_layout.document_dir,
        page_dir=artifact_layout.pages_dir,
        pages=tuple(pages),
    )
