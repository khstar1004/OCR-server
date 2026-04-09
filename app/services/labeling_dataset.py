from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from app.utils.geometry import bbox_from_any, bbox_union, clamp_bbox

LABEL_STATUSES = ("pending", "accepted", "needs_review", "rejected")


@dataclass(slots=True)
class ArticleRecord:
    output_root: Path
    job_key: str
    pdf_slug: str
    article_json_path: Path
    article_dir: Path
    page_dir: Path
    page_number: int
    article_order: int
    pdf_file: str
    title: str
    body_text: str
    title_bbox: list[int] | None
    article_bbox: list[int] | None
    page_image_path: Path
    image_entries: list[dict[str, Any]]

    @property
    def relative_key(self) -> Path:
        return self.article_json_path.relative_to(self.output_root)


def discover_article_records(output_root: Path) -> list[ArticleRecord]:
    records: list[ArticleRecord] = []
    output_root = output_root.resolve()
    search_roots = _resolve_search_roots(output_root)
    for root in search_roots:
        for article_json_path in sorted(root.rglob("article.json")):
            record = _article_record_from_path(output_root, article_json_path)
            if record is not None:
                records.append(record)
    records.sort(key=lambda item: (item.job_key, item.pdf_slug, item.page_number, item.article_order))
    return records


def load_annotation(label_root: Path, reviewer: str, record: ArticleRecord) -> dict[str, Any]:
    annotation_path = annotation_file_path(label_root, reviewer, record)
    if annotation_path.exists():
        try:
            payload = json.loads(annotation_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        return {**payload, **normalize_annotation_payload(record, payload, reviewer)}
    return build_default_annotation(record, reviewer)


def save_annotation(label_root: Path, reviewer: str, record: ArticleRecord, payload: dict[str, Any]) -> Path:
    reviewer_key = normalize_reviewer_name(reviewer)
    annotation_path = annotation_file_path(label_root, reviewer_key, record)
    annotation_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        **payload,
        **normalize_annotation_payload(record, payload, reviewer_key),
        "reviewer": reviewer_key,
        "source_article_json": str(record.article_json_path),
        "page_image_path": str(record.page_image_path),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    annotation_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return annotation_path


def annotation_file_path(label_root: Path, reviewer: str, record: ArticleRecord) -> Path:
    return label_root / normalize_reviewer_name(reviewer) / record.relative_key.with_name("annotation.json")


def build_default_annotation(record: ArticleRecord, reviewer: str) -> dict[str, Any]:
    payload = {
        "reviewer": normalize_reviewer_name(reviewer),
        "status": "pending",
        "corrected_title": record.title,
        "corrected_body_text": record.body_text,
        "notes": "",
        "tags": [],
        "title_bbox": record.title_bbox,
        "article_bbox": record.article_bbox,
        "image_bboxes": [entry.get("bbox") for entry in record.image_entries if entry.get("bbox")],
    }
    return normalize_annotation_payload(record, payload, reviewer)


def normalize_annotation_payload(record: ArticleRecord, payload: dict[str, Any], reviewer: str) -> dict[str, Any]:
    reviewer_key = normalize_reviewer_name(reviewer)
    has_title_text = "corrected_title" in payload
    has_body_text = "corrected_body_text" in payload
    has_title_bbox = "title_bbox" in payload
    has_article_bbox = "article_bbox" in payload
    has_title_regions = "title_regions" in payload
    has_article_regions = "article_regions" in payload
    has_image_bboxes = "image_bboxes" in payload
    fallback_title = str(payload.get("corrected_title") if has_title_text else record.title or "").strip()
    fallback_body = str(payload.get("corrected_body_text") if has_body_text else record.body_text or "").strip()
    title_bbox = _normalize_bbox(payload.get("title_bbox")) if has_title_bbox else _normalize_bbox(record.title_bbox)
    article_bbox = _normalize_bbox(payload.get("article_bbox")) if has_article_bbox else _normalize_bbox(record.article_bbox)
    title_regions = _normalize_regions(
        payload.get("title_regions"),
        fallback_bbox=title_bbox,
        fallback_text=fallback_title,
        use_fallback=not has_title_regions,
    )
    article_regions = _normalize_regions(
        payload.get("article_regions"),
        fallback_bbox=article_bbox,
        fallback_text=fallback_body,
        use_fallback=not has_article_regions,
    )
    image_bboxes = _normalize_bbox_list(
        payload.get("image_bboxes") if has_image_bboxes else None,
        [entry.get("bbox") for entry in record.image_entries if entry.get("bbox")] if not has_image_bboxes else None,
    )
    normalized_title = _compose_region_text(title_regions, fallback_title, separator="\n")
    normalized_body = _compose_region_text(article_regions, fallback_body, separator="\n\n")
    title_bbox = _bbox_union_from_regions(title_regions) or title_bbox
    article_bbox = _bbox_union_from_regions(article_regions) or article_bbox
    status = str(payload.get("status") or "pending")
    if status not in LABEL_STATUSES:
        status = "pending"
    return {
        "reviewer": reviewer_key,
        "status": status,
        "corrected_title": normalized_title,
        "corrected_body_text": normalized_body,
        "notes": str(payload.get("notes") or "").strip(),
        "tags": _normalize_tags(payload.get("tags")),
        "title_bbox": title_bbox,
        "article_bbox": article_bbox,
        "image_bboxes": image_bboxes,
        "title_regions": title_regions,
        "article_regions": article_regions,
    }


def export_fine_tuning_dataset(
    *,
    label_root: Path,
    reviewer: str,
    records: list[ArticleRecord],
    export_root: Path,
) -> dict[str, Any]:
    reviewer_key = normalize_reviewer_name(reviewer)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dataset_dir = export_root / f"fine_tune_{reviewer_key}_{timestamp}"
    title_dir = dataset_dir / "recognition_title"
    article_dir = dataset_dir / "document_article"
    label_dump_dir = dataset_dir / "labels"
    title_dir.mkdir(parents=True, exist_ok=True)
    article_dir.mkdir(parents=True, exist_ok=True)
    label_dump_dir.mkdir(parents=True, exist_ok=True)

    title_lines: list[str] = []
    article_lines: list[str] = []
    layout_lines: list[str] = []
    accepted = 0
    exported_title_crops = 0
    exported_article_crops = 0

    for index, record in enumerate(records, start=1):
        annotation = load_annotation(label_root, reviewer_key, record)
        if annotation.get("status") != "accepted":
            continue

        accepted += 1
        annotation_path = annotation_file_path(label_root, reviewer_key, record)
        relative_label_path = label_dump_dir / record.relative_key.with_name("annotation.json")
        relative_label_path.parent.mkdir(parents=True, exist_ok=True)
        relative_label_path.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")

        corrected_title = str(annotation.get("corrected_title") or "").strip()
        corrected_body = str(annotation.get("corrected_body_text") or "").strip()
        title_bbox = annotation.get("title_bbox") if "title_bbox" in annotation else record.title_bbox
        article_bbox = annotation.get("article_bbox") if "article_bbox" in annotation else record.article_bbox
        image_bboxes = [bbox for bbox in annotation.get("image_bboxes", []) if isinstance(bbox, list) and len(bbox) == 4]
        title_regions = list(annotation.get("title_regions", []) or [])
        article_regions = list(annotation.get("article_regions", []) or [])

        if corrected_title and isinstance(title_bbox, list) and len(title_bbox) == 4:
            title_path = title_dir / f"title_{index:06d}.png"
            crop_bbox_to_file(record.page_image_path, title_bbox, title_path)
            title_lines.append(f"{title_path.relative_to(dataset_dir).as_posix()}\t{corrected_title}")
            exported_title_crops += 1

        if (corrected_title or corrected_body) and isinstance(article_bbox, list) and len(article_bbox) == 4:
            article_path = article_dir / f"article_{index:06d}.png"
            crop_bbox_to_file(record.page_image_path, article_bbox, article_path)
            article_payload = {
                "image_path": article_path.relative_to(dataset_dir).as_posix(),
                "job_key": record.job_key,
                "pdf_slug": record.pdf_slug,
                "pdf_file": record.pdf_file,
                "page_number": record.page_number,
                "article_order": record.article_order,
                "title": corrected_title,
                "body_text": corrected_body,
                "notes": annotation.get("notes") or "",
                "tags": annotation.get("tags") or [],
                "title_regions": title_regions,
                "article_regions": article_regions,
            }
            article_lines.append(json.dumps(article_payload, ensure_ascii=False))
            exported_article_crops += 1

        layout_lines.append(
            json.dumps(
                {
                    "page_image_path": str(record.page_image_path),
                    "job_key": record.job_key,
                    "pdf_slug": record.pdf_slug,
                    "page_number": record.page_number,
                    "article_order": record.article_order,
                    "title_bbox": title_bbox,
                    "article_bbox": article_bbox,
                    "image_bboxes": image_bboxes,
                    "title_regions": title_regions,
                    "article_regions": article_regions,
                    "title": corrected_title,
                    "body_text": corrected_body,
                    "status": annotation.get("status"),
                },
                ensure_ascii=False,
            )
        )

    (dataset_dir / "recognition_title.tsv").write_text("\n".join(title_lines).strip() + ("\n" if title_lines else ""), encoding="utf-8")
    (dataset_dir / "document_article.jsonl").write_text("\n".join(article_lines).strip() + ("\n" if article_lines else ""), encoding="utf-8")
    (dataset_dir / "layout_annotations.jsonl").write_text("\n".join(layout_lines).strip() + ("\n" if layout_lines else ""), encoding="utf-8")
    (dataset_dir / "README.txt").write_text(
        "\n".join(
            [
                f"reviewer={reviewer_key}",
                f"accepted={accepted}",
                f"title_crops={exported_title_crops}",
                f"article_crops={exported_article_crops}",
                "recognition_title.tsv: title crop path + corrected title",
                "document_article.jsonl: article crop path + corrected title/body",
                "layout_annotations.jsonl: page-level bbox metadata",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "dataset_dir": dataset_dir,
        "accepted_count": accepted,
        "title_crop_count": exported_title_crops,
        "article_crop_count": exported_article_crops,
    }


def crop_bbox_to_file(source_image_path: Path, bbox: list[int], output_path: Path) -> Path:
    with Image.open(source_image_path) as image:
        x0, y0, x1, y1 = clamp_bbox(bbox, image.width, image.height)
        crop = image.crop((x0, y0, x1, y1))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(output_path)
    return output_path


def normalize_reviewer_name(value: str) -> str:
    normalized = re.sub(r"[^0-9A-Za-z가-힣._-]+", "_", (value or "").strip())
    return normalized.strip("._-") or "reviewer"


def _normalize_regions(raw: Any, *, fallback_bbox: list[int] | None, fallback_text: str, use_fallback: bool = True) -> list[dict[str, Any]]:
    regions: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            bbox = _normalize_bbox(item.get("bbox")) if isinstance(item, dict) else _normalize_bbox(item)
            text = str(item.get("text") or "").strip() if isinstance(item, dict) else ""
            if bbox is None and not text:
                continue
            regions.append({"bbox": bbox, "text": text})
    if use_fallback and not regions and (fallback_bbox is not None or fallback_text):
        regions.append({"bbox": fallback_bbox, "text": fallback_text})
    return regions


def _compose_region_text(regions: list[dict[str, Any]], fallback_text: str, *, separator: str) -> str:
    texts = [str(region.get("text") or "").strip() for region in regions if str(region.get("text") or "").strip()]
    if texts:
        return separator.join(texts).strip()
    return fallback_text.strip()


def _bbox_union_from_regions(regions: list[dict[str, Any]]) -> list[int] | None:
    boxes = [bbox for bbox in (_normalize_bbox(region.get("bbox")) for region in regions) if bbox is not None]
    if not boxes:
        return None
    return bbox_union(boxes)


def _normalize_bbox_list(raw: Any, fallback: list[Any] | None = None) -> list[list[int]]:
    values = raw if isinstance(raw, list) else (fallback or [])
    boxes: list[list[int]] = []
    for item in values:
        bbox = _normalize_bbox(item)
        if bbox is not None:
            boxes.append(bbox)
    return boxes


def _normalize_bbox(raw: Any) -> list[int] | None:
    bbox = bbox_from_any(raw)
    if bbox is None or len(bbox) != 4:
        return None
    return [int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3])]


def _normalize_tags(raw: Any) -> list[str]:
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, list):
        values = raw
    else:
        values = []
    return [str(item).strip() for item in values if str(item).strip()]


def _resolve_search_roots(output_root: Path) -> list[Path]:
    if output_root.name.startswith("job_") and output_root.is_dir():
        return [output_root]
    return [path for path in sorted(output_root.iterdir()) if path.is_dir() and path.name.startswith("job_")]


def _article_record_from_path(output_root: Path, article_json_path: Path) -> ArticleRecord | None:
    try:
        payload = json.loads(article_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    article_dir = article_json_path.parent
    page_dir = article_dir.parent
    parsed_dir = page_dir.parent
    pdf_root = parsed_dir.parent
    job_root = pdf_root.parent
    page_number = int(payload.get("page_number") or _extract_number(page_dir.name))
    article_order = int(payload.get("article_order") or _extract_number(article_dir.name))
    page_image_path = pdf_root / "pages" / f"page_{page_number:04d}.png"

    return ArticleRecord(
        output_root=output_root,
        job_key=job_root.name,
        pdf_slug=pdf_root.name,
        article_json_path=article_json_path,
        article_dir=article_dir,
        page_dir=page_dir,
        page_number=page_number,
        article_order=article_order,
        pdf_file=str(payload.get("pdf_file") or pdf_root.name),
        title=str(payload.get("title") or ""),
        body_text=str(payload.get("body_text") or ""),
        title_bbox=payload.get("title_bbox"),
        article_bbox=payload.get("article_bbox"),
        page_image_path=page_image_path,
        image_entries=list(payload.get("images", []) or []),
    )


def _extract_number(value: str) -> int:
    matched = re.search(r"(\d+)", value or "")
    return int(matched.group(1)) if matched else 0
