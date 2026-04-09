from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

BBox = tuple[float, float, float, float]


def bbox_width(bbox: BBox) -> float:
    return max(0.0, float(bbox[2]) - float(bbox[0]))


def bbox_height(bbox: BBox) -> float:
    return max(0.0, float(bbox[3]) - float(bbox[1]))


def bbox_area(bbox: BBox) -> float:
    return bbox_width(bbox) * bbox_height(bbox)


def bbox_center_x(bbox: BBox) -> float:
    return float(bbox[0]) + bbox_width(bbox) / 2.0


def bbox_center_y(bbox: BBox) -> float:
    return float(bbox[1]) + bbox_height(bbox) / 2.0


def clamp_bbox(
    bbox: BBox,
    *,
    max_width: float | None = None,
    max_height: float | None = None,
) -> BBox:
    x0, y0, x1, y1 = [float(value) for value in bbox]
    if max_width is not None:
        x0 = min(max(x0, 0.0), float(max_width))
        x1 = min(max(x1, 0.0), float(max_width))
    if max_height is not None:
        y0 = min(max(y0, 0.0), float(max_height))
        y1 = min(max(y1, 0.0), float(max_height))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return (x0, y0, x1, y1)


def union_bboxes(boxes: Sequence[BBox]) -> BBox:
    if not boxes:
        raise ValueError("At least one bounding box is required.")
    x0 = min(box[0] for box in boxes)
    y0 = min(box[1] for box in boxes)
    x1 = max(box[2] for box in boxes)
    y1 = max(box[3] for box in boxes)
    return (float(x0), float(y0), float(x1), float(y1))


@dataclass(frozen=True, slots=True)
class PageImageArtifact:
    page_no: int
    image_path: Path
    width: int
    height: int
    source_pdf: Path
    dpi: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "image_path": str(self.image_path),
            "width": self.width,
            "height": self.height,
            "source_pdf": str(self.source_pdf),
            "dpi": self.dpi,
        }


@dataclass(frozen=True, slots=True)
class RenderedPdf:
    pdf_path: Path
    job_id: str
    source_key: str
    artifact_root: Path
    page_dir: Path
    pages: tuple[PageImageArtifact, ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    def page(self, page_no: int) -> PageImageArtifact:
        for item in self.pages:
            if item.page_no == page_no:
                return item
        raise KeyError(f"Rendered page {page_no} was not found.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_path": str(self.pdf_path),
            "job_id": self.job_id,
            "source_key": self.source_key,
            "artifact_root": str(self.artifact_root),
            "page_dir": str(self.page_dir),
            "pages": [page.to_dict() for page in self.pages],
        }


@dataclass(frozen=True, slots=True)
class OCRPageArtifacts:
    page_no: int
    image_path: Path
    markdown_path: Path
    html_path: Path
    json_path: Path
    metadata_path: Path
    raw_payload: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "image_path": str(self.image_path),
            "markdown_path": str(self.markdown_path),
            "html_path": str(self.html_path),
            "json_path": str(self.json_path),
            "metadata_path": str(self.metadata_path),
            "raw_payload": dict(self.raw_payload),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class OCRDocumentResult:
    pdf_path: Path
    job_id: str
    source_key: str
    method: str
    model_id: str
    artifact_root: Path
    pages: tuple[OCRPageArtifacts, ...]

    def page(self, page_no: int) -> OCRPageArtifacts:
        for item in self.pages:
            if item.page_no == page_no:
                return item
        raise KeyError(f"OCR page {page_no} was not found.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_path": str(self.pdf_path),
            "job_id": self.job_id,
            "source_key": self.source_key,
            "method": self.method,
            "model_id": self.model_id,
            "artifact_root": str(self.artifact_root),
            "pages": [page.to_dict() for page in self.pages],
        }
