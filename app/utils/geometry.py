from __future__ import annotations

from math import sqrt
from typing import Any


def bbox_from_any(raw: Any) -> list[int] | None:
    if raw is None:
        return None
    if isinstance(raw, dict):
        for key in ["bbox", "coordinate", "box", "poly", "points"]:
            if key in raw:
                return bbox_from_any(raw[key])
        return None
    if not isinstance(raw, (list, tuple)):
        return None
    if len(raw) == 4 and all(isinstance(value, (int, float)) for value in raw):
        x0, y0, x1, y1 = raw
        return [int(min(x0, x1)), int(min(y0, y1)), int(max(x0, x1)), int(max(y0, y1))]
    if raw and isinstance(raw[0], (list, tuple)):
        xs: list[float] = []
        ys: list[float] = []
        for point in raw:
            if len(point) >= 2:
                xs.append(float(point[0]))
                ys.append(float(point[1]))
        if xs and ys:
            return [int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))]
    return None


def bbox_union(boxes: list[list[int]]) -> list[int]:
    return [
        min(box[0] for box in boxes),
        min(box[1] for box in boxes),
        max(box[2] for box in boxes),
        max(box[3] for box in boxes),
    ]


def bbox_center(bbox: list[int]) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)


def bbox_area(bbox: list[int]) -> int:
    return max(0, bbox[2] - bbox[0]) * max(0, bbox[3] - bbox[1])


def bbox_height(bbox: list[int]) -> int:
    return max(0, bbox[3] - bbox[1])


def clamp_bbox(bbox: list[int], width: int, height: int) -> list[int]:
    x0 = max(0, min(width, bbox[0]))
    y0 = max(0, min(height, bbox[1]))
    x1 = max(0, min(width, bbox[2]))
    y1 = max(0, min(height, bbox[3]))
    if x1 <= x0:
        x1 = min(width, x0 + 1)
    if y1 <= y0:
        y1 = min(height, y0 + 1)
    return [x0, y0, x1, y1]


def should_scale_bboxes_to_page(
    bboxes: list[list[int]],
    width: int,
    height: int,
    *,
    normalized_max: int = 1000,
    min_page_edge: int = 1500,
    max_normalized_coverage: float = 0.6,
) -> bool:
    if not bboxes:
        return False
    if min(width, height) < min_page_edge:
        return False

    max_x = max(box[2] for box in bboxes)
    max_y = max(box[3] for box in bboxes)
    if max_x > int(normalized_max * 1.1) or max_y > int(normalized_max * 1.1):
        return False

    return (max_x / max(width, 1)) <= max_normalized_coverage and (max_y / max(height, 1)) <= max_normalized_coverage


def scale_bbox_to_page(
    bbox: list[int],
    width: int,
    height: int,
    *,
    normalized_max: int = 1000,
) -> list[int]:
    scaled = [
        round((bbox[0] / normalized_max) * width),
        round((bbox[1] / normalized_max) * height),
        round((bbox[2] / normalized_max) * width),
        round((bbox[3] / normalized_max) * height),
    ]
    return clamp_bbox(scaled, width, height)


def normalize_bbox_to_page(
    bbox: list[int] | None,
    width: int,
    height: int,
    *,
    normalized_max: int = 1000,
) -> list[int] | None:
    if bbox is None:
        return None
    if should_scale_bboxes_to_page([bbox], width, height, normalized_max=normalized_max):
        return scale_bbox_to_page(bbox, width, height, normalized_max=normalized_max)
    return clamp_bbox(bbox, width, height)


def normalize_bboxes_to_page(
    bboxes: list[list[int]],
    width: int,
    height: int,
    *,
    normalized_max: int = 1000,
) -> list[list[int]]:
    if not bboxes:
        return []
    if should_scale_bboxes_to_page(bboxes, width, height, normalized_max=normalized_max):
        return [scale_bbox_to_page(bbox, width, height, normalized_max=normalized_max) for bbox in bboxes]
    return [clamp_bbox(bbox, width, height) for bbox in bboxes]


def box_contains(outer: list[int], inner: list[int]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def box_intersection_area(a: list[int], b: list[int]) -> int:
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return (x1 - x0) * (y1 - y0)


def box_horizontal_overlap_ratio(a: list[int], b: list[int]) -> float:
    x0 = max(a[0], b[0])
    x1 = min(a[2], b[2])
    overlap = max(0, x1 - x0)
    denom = min(max(1, a[2] - a[0]), max(1, b[2] - b[0]))
    return overlap / denom


def bbox_distance(a: list[int], b: list[int]) -> float:
    ax, ay = bbox_center(a)
    bx, by = bbox_center(b)
    return sqrt((ax - bx) ** 2 + (ay - by) ** 2)
