from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Article, ArticleImage, Job, Page, PdfFile, ProcessingLog
from app.services.storage import OutputStorage


def sync_archived_job_index(db: Session, storage: OutputStorage, *, limit: int = 24) -> int:
    imported = 0
    existing_keys = {key for key in db.scalars(select(Job.job_key))}
    for job_dir in _iter_archived_job_dirs(storage, limit=limit):
        job_key = job_dir.name
        if job_key in existing_keys:
            continue
        if _import_archived_job(db, job_dir, job_key):
            existing_keys.add(job_key)
            imported += 1
    if imported:
        db.commit()
    return imported


def _iter_archived_job_dirs(storage: OutputStorage, *, limit: int) -> list[Path]:
    candidates: dict[str, Path] = {}
    for root in storage.settings.output_roots():
        if not root.exists():
            continue
        for job_dir in root.glob("job_*"):
            if not job_dir.is_dir():
                continue
            if not any(job_dir.glob("*/parsed/page_*/article_*/article.json")):
                continue
            candidates.setdefault(job_dir.name, job_dir)
    return sorted(candidates.values(), key=lambda path: path.stat().st_mtime, reverse=True)[:limit]


def _import_archived_job(db: Session, job_dir: Path, job_key: str) -> bool:
    page_records = _collect_archived_pages(job_dir)
    if not page_records:
        return False

    requested_at = datetime.fromtimestamp(job_dir.stat().st_mtime, timezone.utc)
    job = Job(
        job_key=job_key,
        source_dir=f"archived:{job_dir}",
        requested_date=_date_from_job_key(job_key),
        requested_at=requested_at,
        started_at=requested_at,
        finished_at=requested_at,
        status="completed",
        total_files=len(page_records),
        success_files=len(page_records),
        failed_files=0,
        total_articles=sum(len(page_items) for page_items in page_records.values()),
    )
    db.add(job)
    db.flush()

    for pdf_index, (pdf_name, pages) in enumerate(sorted(page_records.items()), start=1):
        pdf_root = pages[0]["pdf_root"]
        pdf = PdfFile(
            job_id=job.id,
            file_name=pdf_name,
            file_path=str(pdf_root / f"{Path(pdf_name).stem}.pdf"),
            file_hash=f"archive:{job_key}:{pdf_index}:{pdf_root.name}",
            file_date=job.requested_date,
            page_count=len(pages),
            status="completed",
            processed_at=requested_at,
        )
        db.add(pdf)
        db.flush()

        for page_record in sorted(pages, key=lambda item: item["page_number"]):
            page = _create_page(db, pdf, page_record)
            for article_payload, article_dir in page_record["articles"]:
                article = _create_article(db, pdf, page, article_payload)
                for image_payload in article_payload.get("images", []) or []:
                    if isinstance(image_payload, dict):
                        _create_article_image(db, article, page, article_dir, image_payload)

    db.add(
        ProcessingLog(
            job_id=job.id,
            pdf_file_id=None,
            page_id=None,
            step_name="archive",
            status="completed",
            message=f"restored archived OCR result index from {job_dir}",
        )
    )
    return True


def _collect_archived_pages(job_dir: Path) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for page_dir in sorted(job_dir.glob("*/parsed/page_*")):
        if not page_dir.is_dir():
            continue
        articles = _read_page_articles(page_dir)
        if not articles:
            continue
        first_payload = articles[0][0]
        pdf_name = str(first_payload.get("pdf_file") or _pdf_name_from_page_dir(page_dir))
        page_number = _as_int(first_payload.get("page_number")) or _page_number_from_dir(page_dir)
        if page_number <= 0:
            continue
        pdf_root = page_dir.parent.parent
        grouped[pdf_name].append(
            {
                "pdf_root": pdf_root,
                "page_dir": page_dir,
                "page_number": page_number,
                "articles": articles,
            }
        )
    return dict(grouped)


def _read_page_articles(page_dir: Path) -> list[tuple[dict[str, Any], Path]]:
    items: list[tuple[dict[str, Any], Path]] = []
    page_payload = _read_json(page_dir / "page.json")
    for article_entry in page_payload.get("articles", []) or []:
        if not isinstance(article_entry, dict):
            continue
        metadata_path = _resolve_existing_path(article_entry.get("metadata_path"))
        if metadata_path is None:
            bundle_path = _resolve_existing_path(article_entry.get("bundle_dir"))
            metadata_path = bundle_path / "article.json" if bundle_path is not None else None
        if metadata_path is None or not metadata_path.exists():
            continue
        payload = _read_json(metadata_path)
        if payload:
            items.append((payload, metadata_path.parent))
    if items:
        return items

    for metadata_path in sorted(page_dir.glob("article_*/article.json")):
        payload = _read_json(metadata_path)
        if payload:
            items.append((payload, metadata_path.parent))
    return items


def _create_page(db: Session, pdf: PdfFile, page_record: dict[str, Any]) -> Page:
    pdf_root = page_record["pdf_root"]
    page_number = int(page_record["page_number"])
    page_image_path = pdf_root / "pages" / f"page_{page_number:04d}.png"
    width, height = _image_size(page_image_path)
    raw_root = pdf_root / "raw"
    page = Page(
        pdf_file_id=pdf.id,
        page_number=page_number,
        page_image_path=str(page_image_path),
        raw_vl_json_path=str(raw_root / f"page_{page_number:04d}_vl.json"),
        raw_structure_json_path=str(raw_root / f"page_{page_number:04d}_structure.json"),
        raw_fallback_json_path=str(raw_root / f"page_{page_number:04d}_fallback_ocr.json"),
        width=width,
        height=height,
        parse_status="parsed",
        unassigned_payload=[],
    )
    db.add(page)
    db.flush()
    return page


def _create_article(db: Session, pdf: PdfFile, page: Page, payload: dict[str, Any]) -> Article:
    article = Article(
        pdf_file_id=pdf.id,
        page_id=page.id,
        article_order=_as_int(payload.get("article_order")) or 1,
        title=_text(payload.get("title")) or "Untitled",
        body_text=_text(payload.get("body_text")),
        title_bbox=_bbox(payload.get("title_bbox")),
        article_bbox=_bbox(payload.get("article_bbox")),
        confidence=_as_float(payload.get("confidence")) or 0.0,
        layout_type=_text(payload.get("layout_type")) or "article",
    )
    db.add(article)
    db.flush()
    return article


def _create_article_image(db: Session, article: Article, page: Page, article_dir: Path, payload: dict[str, Any]) -> None:
    image_path = _resolve_existing_path(payload.get("image_path"))
    if image_path is None:
        relative = _text(payload.get("relative_path"))
        image_path = article_dir / relative if relative else article_dir / "images" / f"image_{_as_int(payload.get('image_order')) or 1:02d}.png"
    width = _as_int(payload.get("width"))
    height = _as_int(payload.get("height"))
    if width is None or height is None:
        width, height = _image_size(image_path)
    db.add(
        ArticleImage(
            article_id=article.id,
            page_id=page.id,
            image_order=_as_int(payload.get("image_order")) or 1,
            image_path=str(image_path),
            image_bbox=_bbox(payload.get("bbox")) or [0, 0, width, height],
            width=width,
            height=height,
        )
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _resolve_existing_path(value: Any) -> Path | None:
    text = _text(value)
    if not text:
        return None
    path = Path(text)
    if path.exists():
        return path
    return None


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            return image.width, image.height
    except OSError:
        return 1, 1


def _pdf_name_from_page_dir(page_dir: Path) -> str:
    return f"{page_dir.parent.parent.name}.pdf"


def _page_number_from_dir(page_dir: Path) -> int:
    match = re.search(r"(\d+)$", page_dir.name)
    return int(match.group(1)) if match else 0


def _date_from_job_key(job_key: str) -> date | None:
    match = re.match(r"job_(\d{8})_", job_key)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d").date()
    except ValueError:
        return None


def _bbox(value: Any) -> list[int] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    return str(value or "").strip()
