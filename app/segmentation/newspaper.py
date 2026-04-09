from __future__ import annotations

import math
from pathlib import Path
from statistics import median
from typing import Any, Mapping, Sequence

from PIL import Image

from app.ocr.types import (
    BBox,
    OCRDocumentResult,
    OCRPageArtifacts,
    PageImageArtifact,
    RenderedPdf,
    bbox_area,
    bbox_center_x,
    bbox_height,
    bbox_width,
    clamp_bbox,
    union_bboxes,
)
from app.segmentation.models import ArticleCandidate, ColumnGroup, LayoutBlock, PageSegmentationResult
from app.services.artifacts import JobArtifactLayout, load_json, make_json_safe, write_json

HEADLINE_KINDS = {"headline", "title", "header", "section_header", "subheadline"}
IMAGE_KINDS = {"image", "figure", "photo", "graphic", "diagram"}
CAPTION_KINDS = {"caption", "image_caption"}
BLOCK_COLLECTION_KEYS = ("blocks", "children", "items", "elements", "lines", "paragraphs", "regions")


def _parse_bbox(raw_bbox: Any) -> BBox | None:
    if raw_bbox is None:
        return None
    if isinstance(raw_bbox, Mapping):
        if {"x0", "y0", "x1", "y1"} <= raw_bbox.keys():
            return (
                float(raw_bbox["x0"]),
                float(raw_bbox["y0"]),
                float(raw_bbox["x1"]),
                float(raw_bbox["y1"]),
            )
        if {"left", "top", "right", "bottom"} <= raw_bbox.keys():
            return (
                float(raw_bbox["left"]),
                float(raw_bbox["top"]),
                float(raw_bbox["right"]),
                float(raw_bbox["bottom"]),
            )
        if {"x", "y", "width", "height"} <= raw_bbox.keys():
            x = float(raw_bbox["x"])
            y = float(raw_bbox["y"])
            width = float(raw_bbox["width"])
            height = float(raw_bbox["height"])
            return (x, y, x + width, y + height)
        if "points" in raw_bbox:
            return _parse_bbox(raw_bbox["points"])
    if isinstance(raw_bbox, Sequence) and not isinstance(raw_bbox, (str, bytes, bytearray)):
        values = list(raw_bbox)
        if len(values) == 4 and all(isinstance(item, (int, float)) for item in values):
            x0, y0, x1, y1 = [float(item) for item in values]
            return (x0, y0, x1, y1)
        if values and all(isinstance(item, Mapping) for item in values):
            xs = [float(item["x"]) for item in values if "x" in item]
            ys = [float(item["y"]) for item in values if "y" in item]
            if xs and ys:
                return (min(xs), min(ys), max(xs), max(ys))
    return None


def _extract_text(node: Mapping[str, Any]) -> str:
    for key in ("text", "content", "markdown", "label", "title", "caption"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            parts = [str(item).strip() for item in value if str(item).strip()]
            if parts:
                return " ".join(parts)
    return ""


def _infer_kind(node: Mapping[str, Any], path: tuple[str, ...]) -> str:
    for key in ("type", "block_type", "kind", "role", "category"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower().replace(" ", "_")

    joined_path = ".".join(path).lower()
    if "caption" in joined_path:
        return "caption"
    if "image" in joined_path or "figure" in joined_path:
        return "image"
    if "headline" in joined_path or "title" in joined_path or "header" in joined_path:
        return "headline"
    return "text"


def _node_is_block(node: Mapping[str, Any], kind: str, text: str, bbox: BBox | None) -> bool:
    if bbox is None or bbox_area(bbox) <= 0:
        return False
    if text:
        return True
    if kind in IMAGE_KINDS or kind in CAPTION_KINDS:
        return True
    return not any(key in node for key in BLOCK_COLLECTION_KEYS)


def _normalize_layout_blocks(payload: Mapping[str, Any], page_no: int) -> tuple[LayoutBlock, ...]:
    blocks: list[LayoutBlock] = []
    seen: set[tuple[float, float, float, float, str, str]] = set()

    def visit(node: Any, path: tuple[str, ...]) -> None:
        if isinstance(node, Mapping):
            bbox = _parse_bbox(
                node.get("bbox")
                or node.get("box")
                or node.get("bounds")
                or node.get("bounding_box")
                or node.get("polygon")
            )
            text = _extract_text(node)
            kind = _infer_kind(node, path)

            explicit_page = node.get("page_no") or node.get("page")
            if explicit_page not in (None, "", page_no):
                for key, value in node.items():
                    if isinstance(value, (Mapping, list, tuple)):
                        visit(value, path + (str(key),))
                return

            if _node_is_block(node, kind, text, bbox):
                block_id = str(node.get("id") or f"block-{len(blocks) + 1:04d}")
                rounded_bbox = tuple(round(value, 2) for value in bbox)
                signature = (*rounded_bbox, kind, text)
                if signature not in seen:
                    seen.add(signature)
                    blocks.append(
                        LayoutBlock(
                            block_id=block_id,
                            page_no=page_no,
                            kind=kind,
                            bbox=rounded_bbox,
                            text=text,
                            score=(
                                float(node["score"])
                                if isinstance(node.get("score"), (int, float))
                                else None
                            ),
                            metadata=make_json_safe(
                                {
                                    key: value
                                    for key, value in node.items()
                                    if key not in {"bbox", "box", "bounds", "bounding_box", "polygon", "text"}
                                }
                            ),
                        )
                    )
                if text and any(key in node for key in ("lines", "spans", "words")):
                    return

            for key, value in node.items():
                if isinstance(value, (Mapping, list, tuple)):
                    visit(value, path + (str(key),))
            return

        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for item in node:
                visit(item, path)

    visit(payload, ())
    return tuple(sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0], block.block_id)))


def _horizontal_overlap_ratio(left: BBox, right: BBox) -> float:
    overlap = max(0.0, min(left[2], right[2]) - max(left[0], right[0]))
    baseline = min(bbox_width(left), bbox_width(right)) or 1.0
    return overlap / baseline


def _seed_columns(blocks: Sequence[LayoutBlock], page_width: float) -> list[list[LayoutBlock]]:
    regular_blocks = [block for block in blocks if bbox_width(block.bbox) <= page_width * 0.78]
    if not regular_blocks:
        regular_blocks = list(blocks)

    tolerance = max(24.0, page_width * 0.06)
    columns: list[list[LayoutBlock]] = []
    for block in sorted(regular_blocks, key=lambda item: (item.bbox[0], item.bbox[1], item.block_id)):
        placed = False
        for column_blocks in columns:
            column_bbox = union_bboxes([item.bbox for item in column_blocks])
            if (
                abs(block.bbox[0] - column_bbox[0]) <= tolerance
                or _horizontal_overlap_ratio(block.bbox, column_bbox) >= 0.35
            ):
                column_blocks.append(block)
                placed = True
                break
        if not placed:
            columns.append([block])
    return columns


def _best_column_index(block: LayoutBlock, columns: Sequence[list[LayoutBlock]]) -> int:
    best_index = 0
    best_score = -1.0
    for index, column_blocks in enumerate(columns):
        column_bbox = union_bboxes([item.bbox for item in column_blocks])
        overlap = _horizontal_overlap_ratio(block.bbox, column_bbox)
        distance_penalty = abs(bbox_center_x(block.bbox) - bbox_center_x(column_bbox)) / max(
            bbox_width(column_bbox), 1.0
        )
        score = overlap - distance_penalty
        if score > best_score:
            best_score = score
            best_index = index
    return best_index


def _build_columns(blocks: Sequence[LayoutBlock], page_width: float) -> tuple[ColumnGroup, ...]:
    if not blocks:
        return ()

    seeded = _seed_columns(blocks, page_width)
    assigned: list[list[LayoutBlock]] = [[] for _ in seeded]
    for block in sorted(blocks, key=lambda item: (item.bbox[1], item.bbox[0], item.block_id)):
        column_index = _best_column_index(block, seeded)
        assigned[column_index].append(block)

    groups = []
    for column_index, column_blocks in enumerate(assigned):
        ordered = tuple(sorted(column_blocks, key=lambda item: (item.bbox[1], item.bbox[0], item.block_id)))
        groups.append(
            ColumnGroup(
                page_no=ordered[0].page_no,
                column_index=column_index,
                bbox=union_bboxes([block.bbox for block in ordered]),
                blocks=ordered,
            )
        )
    return tuple(sorted(groups, key=lambda item: item.bbox[0]))


def _headline_score(block: LayoutBlock, median_height: float) -> float:
    if block.kind in HEADLINE_KINDS:
        return 2.0
    words = [word for word in block.text.split() if word]
    if not words:
        return 0.0
    if block.text.rstrip().endswith((".", "!", "?", ";")):
        return 0.0
    if len(words) > 10:
        return 0.0
    uppercase_ratio = (
        sum(1 for character in block.text if character.isupper()) / max(len(block.text.strip()), 1)
    )
    title_case_ratio = sum(1 for word in words if word[:1].isupper()) / max(len(words), 1)
    score = 0.0
    if len(words) <= 10:
        score += 0.6
    if bbox_height(block.bbox) >= median_height * 1.35:
        score += 0.8
    if uppercase_ratio >= 0.4:
        score += 0.4
    if title_case_ratio >= 0.5:
        score += 0.3
    return score


def _is_headline_like(block: LayoutBlock, median_height: float) -> bool:
    if block.kind in IMAGE_KINDS:
        return False
    return _headline_score(block, median_height) >= 1.0


def _merge_small_groups(groups: list[list[LayoutBlock]]) -> list[list[LayoutBlock]]:
    merged: list[list[LayoutBlock]] = []
    for group in groups:
        textual_blocks = [block for block in group if block.text and block.kind not in IMAGE_KINDS]
        if merged and len(textual_blocks) <= 1 and sum(len(block.text.split()) for block in textual_blocks) < 12:
            merged[-1].extend(group)
            continue
        merged.append(group)
    return merged


def _group_column_blocks(column: ColumnGroup, page_height: float) -> list[list[LayoutBlock]]:
    ordered = list(column.blocks)
    textual_heights = [bbox_height(block.bbox) for block in ordered if block.text]
    median_height = median(textual_heights) if textual_heights else 24.0
    gaps = [
        max(0.0, ordered[index + 1].bbox[1] - ordered[index].bbox[3])
        for index in range(len(ordered) - 1)
    ]
    positive_gaps = [gap for gap in gaps if gap > 0]
    median_gap = median(positive_gaps) if positive_gaps else page_height * 0.01

    groups: list[list[LayoutBlock]] = []
    current: list[LayoutBlock] = []
    for block in ordered:
        if not current:
            current = [block]
            continue

        previous = current[-1]
        gap = max(0.0, block.bbox[1] - previous.bbox[3])
        layout_break = gap > max(median_gap * 1.8, page_height * 0.035)
        headline_start = _is_headline_like(block, median_height) and any(item.text for item in current)

        if headline_start or (layout_break and block.text and block.kind not in CAPTION_KINDS):
            groups.append(current)
            current = [block]
            continue

        current.append(block)

    if current:
        groups.append(current)
    return _merge_small_groups(groups)


def _expand_bbox(bbox: BBox, margin: float, page_width: int, page_height: int) -> BBox:
    expanded = (bbox[0] - margin, bbox[1] - margin, bbox[2] + margin, bbox[3] + margin)
    return clamp_bbox(expanded, max_width=page_width, max_height=page_height)


def _crop_article_image(page_image_path: Path, article_bbox: BBox, target_path: Path) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    left = int(math.floor(article_bbox[0]))
    top = int(math.floor(article_bbox[1]))
    right = int(math.ceil(article_bbox[2]))
    bottom = int(math.ceil(article_bbox[3]))
    with Image.open(page_image_path) as image:
        image.crop((left, top, right, bottom)).save(target_path)


def _create_article_candidate(
    page: PageImageArtifact,
    ocr_page: OCRPageArtifacts,
    group: Sequence[LayoutBlock],
    column_index: int,
    article_index: int,
    artifact_layout: JobArtifactLayout,
) -> ArticleCandidate:
    group_bbox = union_bboxes([block.bbox for block in group])
    article_bbox = _expand_bbox(group_bbox, margin=12.0, page_width=page.width, page_height=page.height)
    image_path = artifact_layout.article_image_path(page.page_no, article_index)
    _crop_article_image(page.image_path, article_bbox, image_path)

    preliminary_text = "\n\n".join(block.text for block in group if block.text).strip()
    candidate = ArticleCandidate(
        article_id=f"page-{page.page_no:04d}-article-{article_index:04d}",
        page_no=page.page_no,
        article_bbox=article_bbox,
        article_image_path=image_path,
        raw_ocr_path=ocr_page.json_path,
        preliminary_text=preliminary_text,
        preliminary_blocks=tuple(group),
        metadata={
            "column_index": column_index,
            "page_image_path": str(page.image_path),
            "block_count": len(group),
            "headline_like_block_ids": [
                block.block_id for block in group if _is_headline_like(block, max(bbox_height(block.bbox), 1.0))
            ],
        },
    )

    write_json(
        artifact_layout.article_metadata_path(page.page_no, article_index),
        candidate.to_dict(),
        overwrite=True,
    )
    return candidate


def _segment_single_page(
    page: PageImageArtifact,
    ocr_page: OCRPageArtifacts,
    artifact_layout: JobArtifactLayout,
) -> PageSegmentationResult:
    raw_payload = ocr_page.raw_payload or load_json(ocr_page.json_path)
    blocks = _normalize_layout_blocks(raw_payload, page.page_no)
    columns = _build_columns(blocks, page.width)

    articles: list[ArticleCandidate] = []
    article_index = 1
    for column in columns:
        for group in _group_column_blocks(column, float(page.height)):
            if not group:
                continue
            if not any(block.text or block.kind in IMAGE_KINDS for block in group):
                continue
            articles.append(
                _create_article_candidate(
                    page=page,
                    ocr_page=ocr_page,
                    group=group,
                    column_index=column.column_index,
                    article_index=article_index,
                    artifact_layout=artifact_layout,
                )
            )
            article_index += 1

    result = PageSegmentationResult(
        page_no=page.page_no,
        page_image_path=page.image_path,
        raw_ocr_path=ocr_page.json_path,
        blocks=blocks,
        columns=columns,
        articles=tuple(articles),
    )
    write_json(
        artifact_layout.page_segmentation_path(page.page_no),
        result.to_dict(),
        overwrite=True,
    )
    return result


def segment_newspaper_pages(
    rendered_pdf: RenderedPdf,
    ocr_result: OCRDocumentResult,
    artifact_layout: JobArtifactLayout,
) -> tuple[PageSegmentationResult, ...]:
    page_lookup = {page.page_no: page for page in rendered_pdf.pages}
    results: list[PageSegmentationResult] = []
    for ocr_page in ocr_result.pages:
        page = page_lookup.get(ocr_page.page_no)
        if page is None:
            continue
        results.append(_segment_single_page(page=page, ocr_page=ocr_page, artifact_layout=artifact_layout))
    return tuple(results)
