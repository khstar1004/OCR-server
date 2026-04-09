from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from app.ocr.types import BBox


@dataclass(frozen=True, slots=True)
class LayoutBlock:
    block_id: str
    page_no: int
    kind: str
    bbox: BBox
    text: str = ""
    score: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "block_id": self.block_id,
            "page_no": self.page_no,
            "kind": self.kind,
            "bbox": list(self.bbox),
            "text": self.text,
            "score": self.score,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class ColumnGroup:
    page_no: int
    column_index: int
    bbox: BBox
    blocks: tuple[LayoutBlock, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "column_index": self.column_index,
            "bbox": list(self.bbox),
            "blocks": [block.to_dict() for block in self.blocks],
        }


@dataclass(frozen=True, slots=True)
class ArticleCandidate:
    article_id: str
    page_no: int
    article_bbox: BBox
    article_image_path: Path
    raw_ocr_path: Path
    preliminary_text: str
    preliminary_blocks: tuple[LayoutBlock, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "article_id": self.article_id,
            "page_no": self.page_no,
            "article_bbox": list(self.article_bbox),
            "article_image_path": str(self.article_image_path),
            "raw_ocr_path": str(self.raw_ocr_path),
            "preliminary_text": self.preliminary_text,
            "preliminary_blocks": [block.to_dict() for block in self.preliminary_blocks],
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True, slots=True)
class PageSegmentationResult:
    page_no: int
    page_image_path: Path
    raw_ocr_path: Path
    blocks: tuple[LayoutBlock, ...]
    columns: tuple[ColumnGroup, ...]
    articles: tuple[ArticleCandidate, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_no": self.page_no,
            "page_image_path": str(self.page_image_path),
            "raw_ocr_path": str(self.raw_ocr_path),
            "blocks": [block.to_dict() for block in self.blocks],
            "columns": [column.to_dict() for column in self.columns],
            "articles": [article.to_dict() for article in self.articles],
        }
