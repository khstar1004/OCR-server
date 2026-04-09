from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pypdfium2 as pdfium


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
        pdf = pdfium.PdfDocument(str(pdf_path))
        rendered: list[RenderedPage] = []
        scale = self.dpi / 72
        for idx in range(len(pdf)):
            page = pdf[idx]
            bitmap = page.render(scale=scale)
            image = bitmap.to_pil()
            image_path = output_dir / f"page_{idx + 1:04d}.png"
            image.save(image_path)
            rendered.append(
                RenderedPage(
                    page_number=idx + 1,
                    image_path=image_path,
                    width=image.width,
                    height=image.height,
                )
            )
        return rendered

