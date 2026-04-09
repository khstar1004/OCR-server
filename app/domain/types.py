from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class BlockLabel(str, Enum):
    TITLE = "title"
    TEXT = "text"
    CAPTION = "caption"
    IMAGE = "image"
    HEADER = "header"
    FOOTER = "footer"
    ADVERTISEMENT = "advertisement"
    UNKNOWN = "unknown"


@dataclass
class OCRBlock:
    block_id: str
    page_number: int
    label: BlockLabel
    bbox: list[int]
    text: str = ""
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaptionCandidate:
    block_id: str
    page_number: int
    bbox: list[int]
    text: str
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageCandidate:
    block_id: str
    page_number: int
    bbox: list[int]
    confidence: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    captions: list[CaptionCandidate] = field(default_factory=list)


@dataclass
class ArticleCandidate:
    page_number: int
    column_index: int | None
    title: str
    body_text: str
    title_bbox: list[int] | None
    article_bbox: list[int]
    confidence: float
    layout_type: str
    blocks: list[OCRBlock] = field(default_factory=list)
    images: list[ImageCandidate] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PageLayout:
    page_number: int
    width: int
    height: int
    image_path: Path
    blocks: list[OCRBlock]
    raw_vl: dict[str, Any]
    raw_structure: dict[str, Any]
    raw_fallback_ocr: dict[str, Any]
