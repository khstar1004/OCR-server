from __future__ import annotations

import html
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.models import Article, Job, Page
from app.core.config import get_settings
from app.schemas.job import (
    ArticleCaptionResponse,
    PagePreviewResponse,
    PreviewArticleImageResponse,
    PreviewArticleResponse,
    PreviewRegionResponse,
)
from app.services.captions import caption_entries_by_image_order
from app.services.storage import OutputStorage
from app.utils.geometry import bbox_from_any, normalize_bbox_to_page

VALID_PREVIEW_OVERLAYS = {"merged", "vl", "structure", "fallback"}

_REGION_COLORS = {
    "article": "#00B894",
    "title": "#FDCB6E",
    "image": "#0984E3",
    "unassigned": "#E17055",
    "layout": "#6C5CE7",
    "text": "#00CEC9",
}


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = re.sub(r"<\s*br\s*/?\s*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_page_preview(db: Session, job: Job, page: Page, overlay: str, api_prefix: str) -> PagePreviewResponse:
    storage = OutputStorage()
    articles = list(
        db.scalars(
            select(Article)
            .where(Article.page_id == page.id)
            .options(selectinload(Article.images))
            .order_by(Article.article_order)
        )
    )
    pdf_file = page.pdf_file
    image_url = f"{api_prefix}/jobs/{job.job_key}/pages/{page.id}/image"
    raw_payload = _load_raw_payload(page, overlay, len(articles))
    if overlay == "merged":
        regions = _build_merged_regions(page, articles)
    else:
        regions = _build_raw_regions(raw_payload, page.width, page.height)

    article_cards: list[PreviewArticleResponse] = []
    for article in articles:
        bundle_dir = storage.resolve_article_bundle_path(
            job.job_key,
            pdf_file.file_name,
            page.page_number,
            article.article_order,
            article.title,
        )
        metadata = storage.load_article_metadata(bundle_dir)
        caption_map = caption_entries_by_image_order(metadata, width=page.width, height=page.height)
        corrected_title = _clean_text(metadata.get("corrected_title")) or article.title
        corrected_body_text = _clean_text(metadata.get("corrected_body_text")) or article.body_text
        article_cards.append(
            PreviewArticleResponse(
                article_id=article.id,
                title=corrected_title,
                body_text=corrected_body_text,
                title_bbox=normalize_bbox_to_page(article.title_bbox, page.width, page.height),
                article_bbox=normalize_bbox_to_page(article.article_bbox, page.width, page.height),
                relevance_score=_as_float(metadata.get("relevance_score")),
                relevance_reason=_clean_text(metadata.get("relevance_reason")) or None,
                relevance_label=_clean_text(metadata.get("relevance_label")) or None,
                relevance_model=_clean_text(metadata.get("relevance_model")) or None,
                relevance_source=_clean_text(metadata.get("relevance_source")) or None,
                images=[
                    PreviewArticleImageResponse(
                        image_id=image.id,
                        image_url=f"{api_prefix}/jobs/{job.job_key}/article-images/{image.id}",
                        bbox=normalize_bbox_to_page(image.image_bbox, page.width, page.height) or image.image_bbox,
                        captions=[
                            ArticleCaptionResponse(
                                text=str(caption.get("text") or "").strip(),
                                bbox=caption.get("bbox"),
                                confidence=_as_float(caption.get("confidence")),
                            )
                            for caption in caption_map.get(image.image_order, [])
                            if str(caption.get("text") or "").strip()
                        ],
                    )
                    for image in sorted(article.images, key=lambda item: item.image_order)
                ],
            )
        )

    return PagePreviewResponse(
        page_id=page.id,
        pdf_file=pdf_file.file_name,
        page_number=page.page_number,
        parse_status=page.parse_status,
        width=page.width,
        height=page.height,
        image_url=image_url,
        overlay_type=overlay,
        regions=regions,
        articles=article_cards,
        raw_payload=raw_payload,
    )


def get_page_for_job(db: Session, job: Job, page_id: int) -> Page | None:
    return db.scalar(
        select(Page)
        .where(Page.id == page_id, Page.pdf_file.has(job_id=job.id))
        .options(selectinload(Page.pdf_file))
    )


def _load_raw_payload(page: Page, overlay: str, article_count: int) -> dict | list | str | None:
    if overlay == "merged":
        return {
            "title": "Merged article view",
            "articles": article_count,
            "unassigned_blocks": len(page.unassigned_payload or []),
        }
    path_value = {
        "vl": page.raw_vl_json_path,
        "structure": page.raw_structure_json_path,
        "fallback": page.raw_fallback_json_path,
    }.get(overlay)
    if not path_value:
        return None
    path = get_settings().resolve_output_path(path_value)
    if path is None:
        return None
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return path.read_text(encoding="utf-8")


def _build_merged_regions(page: Page, articles: list[Article]) -> list[PreviewRegionResponse]:
    regions: list[PreviewRegionResponse] = []
    for article in articles:
        article_bbox = normalize_bbox_to_page(article.article_bbox, page.width, page.height)
        title_bbox = normalize_bbox_to_page(article.title_bbox, page.width, page.height)
        if article_bbox:
            regions.append(
                PreviewRegionResponse(
                    label="article",
                    bbox=article_bbox,
                    text=_clean_text(article.title),
                    confidence=article.confidence,
                    color=_REGION_COLORS["article"],
                )
            )
        if title_bbox:
            regions.append(
                PreviewRegionResponse(
                    label="title",
                    bbox=title_bbox,
                    text=_clean_text(article.title),
                    confidence=article.confidence,
                    color=_REGION_COLORS["title"],
                )
            )
        for image in article.images:
            image_bbox = normalize_bbox_to_page(image.image_bbox, page.width, page.height)
            if image_bbox is None:
                continue
            regions.append(
                PreviewRegionResponse(
                    label="image",
                    bbox=image_bbox,
                    text=f"article #{article.article_order}",
                    color=_REGION_COLORS["image"],
                )
            )

    for block in page.unassigned_payload or []:
        bbox = bbox_from_any(block.get("bbox"))
        if bbox is None:
            continue
        bbox = normalize_bbox_to_page(bbox, page.width, page.height)
        if bbox is None:
            continue
        regions.append(
            PreviewRegionResponse(
                label=str(block.get("label") or "unassigned"),
                bbox=bbox,
                text=str(block.get("text") or ""),
                confidence=float(block.get("confidence", 0.0) or 0.0),
                color=_REGION_COLORS["unassigned"],
            )
        )
    return regions


def _build_raw_regions(raw_payload: dict | list | str | None, width: int, height: int) -> list[PreviewRegionResponse]:
    if not isinstance(raw_payload, dict):
        return []

    regions: list[PreviewRegionResponse] = []
    for box in raw_payload.get("layout_det_res", {}).get("boxes", []) or []:
        bbox = bbox_from_any(box)
        if bbox is None:
            continue
        bbox = normalize_bbox_to_page(bbox, width, height)
        if bbox is None:
            continue
        label = (
            box.get("label")
            or box.get("type")
            or box.get("cls_name")
            or box.get("category_name")
            or box.get("label_name")
            or "layout"
        )
        regions.append(
            PreviewRegionResponse(
                label=f"layout:{label}",
                bbox=bbox,
                confidence=float(box.get("score", 0.0) or 0.0),
                color=_REGION_COLORS["layout"],
            )
        )

    parsing_items = list(raw_payload.get("parsing_res_list", []) or [])
    if parsing_items:
        for item in parsing_items:
            if not isinstance(item, dict):
                continue
            bbox = bbox_from_any(item.get("bbox") or item.get("polygon_points") or item.get("ori_bbox"))
            if bbox is None:
                continue
            bbox = normalize_bbox_to_page(bbox, width, height)
            if bbox is None:
                continue
            label = str(item.get("label") or "block")
            lowered = label.lower()
            if any(tag in lowered for tag in ["image", "figure", "photo", "picture", "illustration", "chart", "graphic"]):
                color = _REGION_COLORS["image"]
                text = None
            elif any(tag in lowered for tag in ["title", "headline"]):
                color = _REGION_COLORS["title"]
                text = str(item.get("content") or "").strip()
            else:
                color = _REGION_COLORS["text"]
                text = str(item.get("content") or "").strip()
            regions.append(
                PreviewRegionResponse(
                    label=label,
                    bbox=bbox,
                    text=text or None,
                    confidence=float(item.get("score", 0.0) or 0.0) if item.get("score") is not None else None,
                    color=color,
                )
            )
        return regions

    ocr_res = raw_payload.get("overall_ocr_res", raw_payload)
    texts = list(ocr_res.get("rec_texts", []) or [])
    boxes = (
        list(ocr_res.get("rec_boxes", []) or [])
        or list(ocr_res.get("rec_polys", []) or [])
        or list(ocr_res.get("dt_polys", []) or [])
    )
    scores = list(ocr_res.get("rec_scores", []) or [])
    for index, text in enumerate(texts):
        bbox = bbox_from_any(boxes[index]) if index < len(boxes) else None
        if bbox is None:
            continue
        bbox = normalize_bbox_to_page(bbox, width, height)
        if bbox is None:
            continue
        regions.append(
            PreviewRegionResponse(
                label="text",
                bbox=bbox,
                text=str(text).strip(),
                confidence=float(scores[index]) if index < len(scores) else None,
                color=_REGION_COLORS["text"],
            )
        )
    return regions
