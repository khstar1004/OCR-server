from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

from app.services.file_scanner import IMAGE_INPUT_SUFFIXES, PDF_INPUT_SUFFIXES


@dataclass
class RenderedPage:
    page_number: int
    image_path: Path
    width: int
    height: int


class PdfRenderer:
    def __init__(self, dpi: int):
        self.dpi = dpi

    def render(self, pdf_path: Path, output_dir: Path) -> list[RenderedPage]:
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = pdf_path.suffix.lower()
        if suffix in IMAGE_INPUT_SUFFIXES:
            return [self._render_image(pdf_path, output_dir)]
        if suffix not in PDF_INPUT_SUFFIXES:
            raise ValueError(f"unsupported input file type: {suffix or pdf_path.name}")

        return self._render_pdf(pdf_path, output_dir)

    def _render_pdf(self, pdf_path: Path, output_dir: Path) -> list[RenderedPage]:
        try:
            rendered = self._render_pdf_with_pymupdf(pdf_path, output_dir)
            if rendered:
                return rendered
        except Exception as pymupdf_exc:
            try:
                return self._render_pdf_with_pdfium(pdf_path, output_dir)
            except Exception as pdfium_exc:
                raise RuntimeError(
                    f"failed to render PDF with PyMuPDF ({pymupdf_exc}) and PDFium ({pdfium_exc})"
                ) from pdfium_exc

        return self._render_pdf_with_pdfium(pdf_path, output_dir)

    def _render_pdf_with_pymupdf(self, pdf_path: Path, output_dir: Path) -> list[RenderedPage]:
        try:
            import fitz
        except ImportError:
            return []

        scale = self.dpi / 72
        matrix = fitz.Matrix(scale, scale)
        document = fitz.open(pdf_path)
        try:
            rendered: list[RenderedPage] = []
            for idx in range(document.page_count):
                image_path = output_dir / f"page_{idx + 1:04d}.png"
                page = document.load_page(idx)
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                pixmap.save(str(image_path))
                rendered.append(
                    RenderedPage(
                        page_number=idx + 1,
                        image_path=image_path,
                        width=int(pixmap.width),
                        height=int(pixmap.height),
                    )
                )
            return rendered
        finally:
            document.close()

    def _render_pdf_with_pdfium(self, pdf_path: Path, output_dir: Path) -> list[RenderedPage]:
        pdf = pdfium.PdfDocument(str(pdf_path))
        rendered: list[RenderedPage] = []
        scale = self.dpi / 72
        try:
            for idx in range(len(pdf)):
                page = pdf[idx]
                try:
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil()
                    if image.mode not in {"RGB", "RGBA"}:
                        image = image.convert("RGB")
                    image_path = output_dir / f"page_{idx + 1:04d}.png"
                    image.save(image_path)
                    rendered.append(
                        RenderedPage(
                            page_number=idx + 1,
                            image_path=image_path,
                            width=int(image.width),
                            height=int(image.height),
                        )
                    )
                finally:
                    close_page = getattr(page, "close", None)
                    if callable(close_page):
                        close_page()
        finally:
            close_document = getattr(pdf, "close", None)
            if callable(close_document):
                close_document()
        return rendered

    @staticmethod
    def _render_image(image_path: Path, output_dir: Path) -> RenderedPage:
        output_path = output_dir / "page_0001.png"
        with Image.open(image_path) as image:
            normalized = image.convert("RGB") if image.mode not in {"RGB", "RGBA"} else image.copy()
            normalized.save(output_path)
            width, height = normalized.width, normalized.height
        return RenderedPage(
            page_number=1,
            image_path=output_path,
            width=width,
            height=height,
        )
