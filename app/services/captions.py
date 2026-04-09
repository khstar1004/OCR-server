from __future__ import annotations

from typing import Any

from app.utils.geometry import bbox_from_any, normalize_bbox_to_page


def normalize_caption_entries(
    raw_value: Any,
    *,
    width: int | None = None,
    height: int | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_value, list):
        return []

    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for item in raw_value:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("content") or "").strip()
        if not text:
            continue
        bbox = bbox_from_any(item.get("bbox"))
        if bbox is not None and width is not None and height is not None:
            bbox = normalize_bbox_to_page(bbox, width, height)
        key = (text, *(bbox or [-1, -1, -1, -1]))
        if key in seen:
            continue
        seen.add(key)
        entry: dict[str, Any] = {
            "text": text,
            "bbox": bbox,
        }
        confidence = _as_float(item.get("confidence"))
        if confidence is not None:
            entry["confidence"] = confidence
        entries.append(entry)
    return entries


def caption_entries_by_image_order(
    metadata: dict[str, Any],
    *,
    width: int | None = None,
    height: int | None = None,
) -> dict[int, list[dict[str, Any]]]:
    images = metadata.get("images")
    if not isinstance(images, list):
        return {}

    caption_map: dict[int, list[dict[str, Any]]] = {}
    for index, image in enumerate(images, start=1):
        if not isinstance(image, dict):
            continue
        image_order = _coerce_positive_int(image.get("image_order")) or index
        captions = normalize_caption_entries(image.get("captions"), width=width, height=height)
        if captions:
            caption_map[image_order] = captions
    return caption_map


def flatten_caption_entries(caption_map: dict[int, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int, int, int]] = set()
    for captions in caption_map.values():
        for caption in captions:
            text = str(caption.get("text") or "").strip()
            if not text:
                continue
            bbox = bbox_from_any(caption.get("bbox"))
            key = (text, *(bbox or [-1, -1, -1, -1]))
            if key in seen:
                continue
            seen.add(key)
            entry: dict[str, Any] = {"text": text, "bbox": bbox}
            confidence = _as_float(caption.get("confidence"))
            if confidence is not None:
                entry["confidence"] = confidence
            flattened.append(entry)
    return flattened


def _coerce_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
