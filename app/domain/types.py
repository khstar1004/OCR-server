from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class BlockLabel(str, Enum):
    TITLE = "title"
    TEXT = "text"
    TABLE = "table"
    FOOTNOTE = "footnote"
    EQUATION_BLOCK = "equation_block"
    LIST_GROUP = "list_group"
    CODE_BLOCK = "code_block"
    FORM = "form"
    TABLE_OF_CONTENTS = "table_of_contents"
    CHEMICAL_BLOCK = "chemical_block"
    BIBLIOGRAPHY = "bibliography"
    BLANK_PAGE = "blank_page"
    COMPLEX_BLOCK = "complex_block"
    HANDWRITING = "handwriting"
    TEXT_INLINE_MATH = "text_inline_math"
    TABLE_CELL = "table_cell"
    REFERENCE = "reference"
    PAGE = "page"
    DOCUMENT = "document"
    LINE = "line"
    SPAN = "span"
    CHAR = "char"
    CAPTION = "caption"
    IMAGE = "image"
    HEADER = "header"
    FOOTER = "footer"
    ADVERTISEMENT = "advertisement"
    UNKNOWN = "unknown"


SUPPORTED_BLOCK_LABELS = tuple(label.value for label in BlockLabel if label != BlockLabel.UNKNOWN)


_DATALAB_LABEL_ALIASES = {
    "ad": BlockLabel.ADVERTISEMENT,
    "advert": BlockLabel.ADVERTISEMENT,
    "advertisement": BlockLabel.ADVERTISEMENT,
    "bibliography": BlockLabel.BIBLIOGRAPHY,
    "blank_page": BlockLabel.BLANK_PAGE,
    "blankpage": BlockLabel.BLANK_PAGE,
    "caption": BlockLabel.CAPTION,
    "char": BlockLabel.CHAR,
    "chemical": BlockLabel.CHEMICAL_BLOCK,
    "chemical_block": BlockLabel.CHEMICAL_BLOCK,
    "chemicalblock": BlockLabel.CHEMICAL_BLOCK,
    "code": BlockLabel.CODE_BLOCK,
    "code_block": BlockLabel.CODE_BLOCK,
    "codeblock": BlockLabel.CODE_BLOCK,
    "complex_block": BlockLabel.COMPLEX_BLOCK,
    "complex_region": BlockLabel.COMPLEX_BLOCK,
    "complexblock": BlockLabel.COMPLEX_BLOCK,
    "complexregion": BlockLabel.COMPLEX_BLOCK,
    "diagram": BlockLabel.IMAGE,
    "doc_title": BlockLabel.TITLE,
    "document": BlockLabel.DOCUMENT,
    "equation": BlockLabel.EQUATION_BLOCK,
    "equation_block": BlockLabel.EQUATION_BLOCK,
    "equationblock": BlockLabel.EQUATION_BLOCK,
    "figure": BlockLabel.IMAGE,
    "figure_group": BlockLabel.IMAGE,
    "figuregroup": BlockLabel.IMAGE,
    "footnote": BlockLabel.FOOTNOTE,
    "form": BlockLabel.FORM,
    "formula": BlockLabel.EQUATION_BLOCK,
    "graphic": BlockLabel.IMAGE,
    "handwriting": BlockLabel.HANDWRITING,
    "header": BlockLabel.HEADER,
    "headline": BlockLabel.TITLE,
    "image": BlockLabel.IMAGE,
    "illustration": BlockLabel.IMAGE,
    "line": BlockLabel.LINE,
    "list": BlockLabel.LIST_GROUP,
    "list_group": BlockLabel.LIST_GROUP,
    "list_item": BlockLabel.LIST_GROUP,
    "listgroup": BlockLabel.LIST_GROUP,
    "listitem": BlockLabel.LIST_GROUP,
    "math": BlockLabel.EQUATION_BLOCK,
    "page": BlockLabel.PAGE,
    "page_footer": BlockLabel.FOOTER,
    "page_header": BlockLabel.HEADER,
    "pagefooter": BlockLabel.FOOTER,
    "pageheader": BlockLabel.HEADER,
    "paragraph": BlockLabel.TEXT,
    "photo": BlockLabel.IMAGE,
    "picture": BlockLabel.IMAGE,
    "picture_group": BlockLabel.IMAGE,
    "picturegroup": BlockLabel.IMAGE,
    "reference": BlockLabel.REFERENCE,
    "section": BlockLabel.TITLE,
    "section_header": BlockLabel.TITLE,
    "sectionheader": BlockLabel.TITLE,
    "span": BlockLabel.SPAN,
    "subheadline": BlockLabel.TITLE,
    "table": BlockLabel.TABLE,
    "table_cell": BlockLabel.TABLE_CELL,
    "table_group": BlockLabel.TABLE,
    "table_of_contents": BlockLabel.TABLE_OF_CONTENTS,
    "tablecell": BlockLabel.TABLE_CELL,
    "tablegroup": BlockLabel.TABLE,
    "tableofcontents": BlockLabel.TABLE_OF_CONTENTS,
    "tabular": BlockLabel.TABLE,
    "text": BlockLabel.TEXT,
    "text_inline_math": BlockLabel.TEXT_INLINE_MATH,
    "textinlinemath": BlockLabel.TEXT_INLINE_MATH,
    "title": BlockLabel.TITLE,
    "toc": BlockLabel.TABLE_OF_CONTENTS,
}


def normalize_block_label_value(value: Any) -> str:
    if isinstance(value, BlockLabel):
        return value.value
    raw = str(value or "").strip()
    if not raw:
        return BlockLabel.UNKNOWN.value
    camel_split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", raw)
    normalized = re.sub(r"[\s\-]+", "_", camel_split)
    normalized = re.sub(r"[^0-9A-Za-z_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_").lower()
    return _DATALAB_LABEL_ALIASES.get(normalized, BlockLabel.UNKNOWN).value


def block_label_from_value(value: Any, *, default: BlockLabel = BlockLabel.UNKNOWN) -> BlockLabel:
    normalized = normalize_block_label_value(value)
    if normalized == BlockLabel.UNKNOWN.value:
        return default
    return BlockLabel(normalized)


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
