from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import Article, ArticleImage, Job, Page, PdfFile, ProcessingLog
from app.schemas.job import JobRunDailyRequest
from app.services.article_cluster import ArticleClusterer
from app.services.news_delivery import NewsDeliveryClient
from app.services.file_scanner import FileScanner
from app.services.job_options import normalize_job_ocr_options, select_items_by_job_page_options
from app.services.ocr_engine import OCREngine
from app.services.pdf_renderer import PdfRenderer
from app.services.relevance_scorer import NationalAssemblyRelevanceScorer
from app.services.result_builder import build_job_result
from app.services.storage import OutputStorage
from app.utils.json_utils import dataclass_to_dict

logger = logging.getLogger(__name__)


class JobRunner:
    def __init__(self, db: Session):
        self.db = db
        self.settings = get_settings()
        self.storage = OutputStorage()
        self.renderer = PdfRenderer(self.settings.pdf_render_dpi)
        self.ocr_engine = OCREngine()
        self.clusterer = ArticleClusterer()
        self.relevance_scorer = NationalAssemblyRelevanceScorer(self.settings)
        self.delivery = NewsDeliveryClient()

    def create_job(self, request: JobRunDailyRequest) -> Job:
        ocr_options = normalize_job_ocr_options(request)
        requested_date = request.date
        date_token = requested_date.strftime("%Y%m%d") if requested_date else datetime.now().strftime("%Y%m%d")
        job_key = f"job_{date_token}_{datetime.now().strftime('%H%M%S')}"
        source_dir = self.settings.translate_source_dir(request.source_dir)
        job = Job(
            job_key=job_key,
            source_dir=source_dir,
            requested_date=requested_date,
            callback_url=None,
            force_reprocess=request.force_reprocess,
            status="queued",
        )
        self.db.add(job)
        self.storage.save_job_config(job_key, {"ocr_options": ocr_options})
        return job

    def execute(self, job_id: int) -> None:
        job = self.db.get(Job, job_id)
        if job is None:
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        self.db.commit()

        try:
            self._run_job(job)
            if job.failed_files > 0 and job.success_files > 0:
                job.status = "completed_with_errors"
            elif job.failed_files > 0 and job.success_files == 0:
                job.status = "failed"
            else:
                job.status = "completed"
        except Exception as exc:
            logger.exception("job execution failed: %s", exc)
            job.status = "failed"
            self._log(job.id, None, None, "job", "failed", str(exc))
        finally:
            job.finished_at = datetime.now(timezone.utc)
            self.db.commit()
            if job.status in {"completed", "completed_with_errors"} and job.total_articles > 0:
                target_url = self.delivery.resolve_target_url(None)
                if target_url:
                    self._log_and_commit(job.id, None, None, "deliver", "running", f"sending articles to {target_url}")
                    try:
                        result = self.delivery.deliver_job_result(build_job_result(self.db, job))
                        self._log_and_commit(
                            job.id,
                            None,
                            None,
                            "deliver",
                            "completed",
                            f"delivered={result.delivered} failed={result.failed}",
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("delivery failed: %s", exc)
                        self._log_and_commit(job.id, None, None, "deliver", "failed", str(exc))

    def _run_job(self, job: Job) -> None:
        source_dir = Path(job.source_dir)
        job_config = self.storage.load_job_config(job.job_key)
        ocr_options = normalize_job_ocr_options(job_config.get("ocr_options") if isinstance(job_config, dict) else {})
        if not source_dir.exists():
            raise FileNotFoundError(f"source_dir not found: {source_dir}")
        if not source_dir.is_dir():
            raise NotADirectoryError(f"source_dir is not a directory: {source_dir}")

        self._log_and_commit(job.id, None, None, "scan", "running", f"scanning input files in {source_dir}")
        existing_hashes = {
            value
            for value in self.db.scalars(
                select(PdfFile.file_hash).where(PdfFile.status.in_(["completed", "completed_with_errors"]))
            )
            if value
        }
        scanned = FileScanner(source_dir).scan(job.requested_date, existing_hashes, job.force_reprocess)
        job.total_files = len(scanned)
        self.db.commit()
        self._log_and_commit(job.id, None, None, "scan", "completed", f"discovered={len(scanned)}")

        for discovered in scanned:
            pdf_row = PdfFile(
                job_id=job.id,
                file_name=discovered.file_name,
                file_path=str(discovered.file_path),
                file_hash=discovered.file_hash,
                file_date=discovered.file_date,
                status="queued",
                skip_reason=discovered.skip_reason,
            )
            self.db.add(pdf_row)
            self.db.commit()
            self.db.refresh(pdf_row)

            if discovered.skip_reason:
                pdf_row.status = "skipped"
                pdf_row.processed_at = datetime.now(timezone.utc)
                job.success_files += 1
                self._log_and_commit(job.id, pdf_row.id, None, "scan", "skipped", discovered.skip_reason)
                continue

            try:
                self._process_pdf(job, pdf_row, ocr_options=ocr_options)
                if pdf_row.status == "completed":
                    job.success_files += 1
                else:
                    job.failed_files += 1
            except Exception as exc:
                logger.exception("pdf processing failed: %s", exc)
                pdf_row.status = "failed"
                pdf_row.processed_at = datetime.now(timezone.utc)
                job.failed_files += 1
                self._log(job.id, pdf_row.id, None, "pdf", "failed", str(exc))
                self.db.commit()

    def _process_pdf(self, job: Job, pdf_row: PdfFile, *, ocr_options: dict[str, Any]) -> None:
        pdf_row.status = "running"
        self.db.commit()
        current_pdf_step = "render"
        self._log_and_commit(job.id, pdf_row.id, None, "render", "running", "page rendering or image normalization started")

        try:
            page_output_dir = self.storage.page_dir(job.job_key, pdf_row.file_name)
            rendered_pages = self.renderer.render(Path(pdf_row.file_path), page_output_dir)
            pdf_row.page_count = len(rendered_pages)
            self.db.commit()
            self._log_and_commit(job.id, pdf_row.id, None, "render", "completed", f"pages={len(rendered_pages)}")
        except Exception as exc:
            self._log(job.id, pdf_row.id, None, current_pdf_step, "failed", str(exc))
            self.db.commit()
            raise

        selected_pages = select_items_by_job_page_options(rendered_pages, ocr_options)
        if len(selected_pages) != len(rendered_pages):
            self._log_and_commit(
                job.id,
                pdf_row.id,
                None,
                "render",
                "completed",
                f"selected_pages={len(selected_pages)} source_pages={len(rendered_pages)} page_range={ocr_options.get('page_range') or 'all'}",
            )

        page_failures = 0
        for rendered_page in selected_pages:
            page_row = Page(
                pdf_file_id=pdf_row.id,
                page_number=rendered_page.page_number,
                page_image_path=str(rendered_page.image_path),
                width=rendered_page.width,
                height=rendered_page.height,
                parse_status="running",
            )
            self.db.add(page_row)
            self.db.commit()
            self.db.refresh(page_row)

            current_step = "ocr_vl"

            def stage_callback(step_name: str, status: str, message: str) -> None:
                nonlocal current_step
                current_step = step_name
                self._log_and_commit(job.id, pdf_row.id, page_row.id, step_name, status, message)

            try:
                layout = self.ocr_engine.parse_page(
                    image_path=rendered_page.image_path,
                    page_number=rendered_page.page_number,
                    width=rendered_page.width,
                    height=rendered_page.height,
                    stage_callback=stage_callback,
                )
                current_step = "persist"
                self._log_and_commit(job.id, pdf_row.id, page_row.id, "persist", "running", "saving raw OCR payloads")
                page_row.raw_vl_json_path = str(
                    self.storage.save_raw_json(job.job_key, pdf_row.file_name, rendered_page.page_number, "vl", layout.raw_vl)
                )
                page_row.raw_structure_json_path = str(
                    self.storage.save_raw_json(job.job_key, pdf_row.file_name, rendered_page.page_number, "structure", layout.raw_structure)
                )
                page_row.raw_fallback_json_path = str(
                    self.storage.save_raw_json(job.job_key, pdf_row.file_name, rendered_page.page_number, "fallback_ocr", layout.raw_fallback_ocr)
                )

                current_step = "cluster"
                self._log_and_commit(job.id, pdf_row.id, page_row.id, "cluster", "running", "clustering article candidates")
                articles, unassigned = self.clusterer.cluster_page(layout)
                ocr_quality = self._build_page_quality(layout, article_count=len(articles))
                page_row.unassigned_payload = [dataclass_to_dict(block) for block in unassigned]
                self._log_and_commit(
                    job.id,
                    pdf_row.id,
                    page_row.id,
                    "cluster",
                    "completed",
                    f"articles={len(articles)} unassigned={len(unassigned)}",
                )

                current_step = "relevance"
                self._log_and_commit(
                    job.id,
                    pdf_row.id,
                    page_row.id,
                    "relevance",
                    "running",
                    f"scoring/correcting {len(articles)} articles with {self.relevance_scorer.model_name}",
                )
                relevance_result = self.relevance_scorer.score_page_articles(
                    pdf_name=pdf_row.file_name,
                    page_number=rendered_page.page_number,
                    articles=articles,
                )
                self._log_and_commit(
                    job.id,
                    pdf_row.id,
                    page_row.id,
                    "relevance",
                    "completed",
                    f"articles={len(relevance_result.assessments)} source={relevance_result.source}",
                )

                current_step = "persist"
                self._log_and_commit(job.id, pdf_row.id, page_row.id, "persist", "running", f"writing {len(articles)} articles")
                self._log_and_commit(job.id, pdf_row.id, page_row.id, "crop", "running", "cropping article images")
                current_step = "persist"

                cropped_images = 0
                page_article_entries: list[dict[str, Any]] = []
                for order, article in enumerate(articles, start=1):
                    relevance = relevance_result.assessments.get(order)
                    corrected_title = relevance.corrected_title if relevance is not None else None
                    corrected_body_text = relevance.corrected_body_text if relevance is not None else None
                    source_metadata = article.metadata.get("source_metadata") if isinstance(article.metadata, dict) else None
                    article_row = Article(
                        pdf_file_id=pdf_row.id,
                        page_id=page_row.id,
                        article_order=order,
                        title=article.title,
                        body_text=article.body_text,
                        title_bbox=article.title_bbox,
                        article_bbox=article.article_bbox,
                        confidence=article.confidence,
                        layout_type=article.layout_type,
                    )
                    self.db.add(article_row)
                    self.db.flush()
                    bundle_dir = self.storage.article_bundle_dir(
                        job.job_key,
                        pdf_row.file_name,
                        rendered_page.page_number,
                        order,
                        article.title,
                    )
                    article_caption_entries = self._article_caption_entries(article)
                    image_entries: list[dict[str, Any]] = []

                    for image_order, image in enumerate(article.images, start=1):
                        current_step = "crop"
                        output_path = self.storage.article_image_path(
                            job.job_key,
                            pdf_row.file_name,
                            rendered_page.page_number,
                            order,
                            article.title,
                            image_order,
                        )
                        img_width, img_height = self.storage.crop_image(rendered_page.image_path, image.bbox, output_path)
                        cropped_images += 1
                        image_entries.append(
                            {
                                "image_order": image_order,
                                "file_name": output_path.name,
                                "relative_path": str(output_path.relative_to(bundle_dir)).replace("\\", "/"),
                                "image_path": str(output_path),
                                "bbox": image.bbox,
                                "width": img_width,
                                "height": img_height,
                                "captions": self._caption_entries(image.captions),
                            }
                        )
                        self.db.add(
                            ArticleImage(
                                article_id=article_row.id,
                                page_id=page_row.id,
                                image_order=image_order,
                                image_path=str(output_path),
                                image_bbox=image.bbox,
                                width=img_width,
                                height=img_height,
                            )
                        )

                    bundle_dir = self.storage.save_article_bundle(
                        job_key=job.job_key,
                        pdf_name=pdf_row.file_name,
                        page_number=rendered_page.page_number,
                        article_order=order,
                        article_id=article_row.id,
                        title=article.title,
                        body_text=article.body_text,
                        title_bbox=article.title_bbox,
                        article_bbox=article.article_bbox,
                        image_entries=image_entries,
                        caption_entries=article_caption_entries,
                        relevance_score=relevance.score if relevance is not None else None,
                        relevance_reason=relevance.reason if relevance is not None else None,
                        relevance_label=relevance.label if relevance is not None else None,
                        relevance_model=relevance.model if relevance is not None else None,
                        relevance_source=relevance.source if relevance is not None else None,
                        corrected_title=corrected_title,
                        corrected_body_text=corrected_body_text,
                        correction_source=relevance.correction_source if relevance is not None else None,
                        correction_model=relevance.correction_model if relevance is not None else None,
                        source_metadata=source_metadata,
                        ocr_quality=ocr_quality,
                    )
                    page_article_entries.append(
                        {
                            "article_id": article_row.id,
                            "article_order": order,
                            "title": corrected_title or article.title,
                            "bundle_dir": str(bundle_dir),
                            "markdown_path": str(bundle_dir / "article.md"),
                            "metadata_path": str(bundle_dir / "article.json"),
                            "images": image_entries,
                            "captions": article_caption_entries,
                            "corrected_title": corrected_title,
                            "corrected_body_text": corrected_body_text,
                            "source_metadata": source_metadata,
                            "relevance_score": relevance.score if relevance is not None else None,
                            "relevance_reason": relevance.reason if relevance is not None else None,
                            "relevance_label": relevance.label if relevance is not None else None,
                            "relevance_model": relevance.model if relevance is not None else None,
                            "relevance_source": relevance.source if relevance is not None else None,
                            "ocr_quality": ocr_quality,
                        }
                    )
                    job.total_articles += 1

                current_step = "persist"
                self.storage.save_page_manifest(
                    job_key=job.job_key,
                    pdf_name=pdf_row.file_name,
                    page_number=rendered_page.page_number,
                    article_entries=page_article_entries,
                    ocr_quality=ocr_quality,
                )
                self._log_and_commit(job.id, pdf_row.id, page_row.id, "crop", "completed", f"images={cropped_images}")
                page_row.parse_status = "parsed"
                self._log(job.id, pdf_row.id, page_row.id, "persist", "completed", f"articles={len(articles)} images={cropped_images}")
                self._log(job.id, pdf_row.id, page_row.id, "page", "completed", f"articles={len(articles)}")
                self.db.commit()
            except Exception as exc:
                page_failures += 1
                page_row.parse_status = "failed"
                page_row.unassigned_payload = []
                self._log(job.id, pdf_row.id, page_row.id, current_step, "failed", str(exc))
                self._log(job.id, pdf_row.id, page_row.id, "page", "failed", str(exc))
                self.db.commit()

        pdf_row.status = "completed_with_errors" if page_failures else "completed"
        pdf_row.processed_at = datetime.now(timezone.utc)
        self._log(job.id, pdf_row.id, None, "persist", pdf_row.status, f"pages={pdf_row.page_count}")
        self.db.commit()

    def _build_page_quality(self, layout: Any, *, article_count: int) -> dict[str, Any]:
        text_chunks = [
            str(getattr(block, "text", "") or "").strip()
            for block in getattr(layout, "blocks", []) or []
            if str(getattr(block, "text", "") or "").strip()
        ]
        text = "\n".join(text_chunks)
        compact = "".join(ch for ch in text if not ch.isspace())
        korean_count = sum(1 for ch in text if "\uac00" <= ch <= "\ud7a3")
        text_count = sum(1 for ch in text if not ch.isspace())
        korean_ratio = round(korean_count / text_count, 4) if text_count else 0.0
        blocks = list(getattr(layout, "blocks", []) or [])
        confidences = [float(getattr(block, "confidence", 0.0) or 0.0) for block in blocks]
        average_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        image_count = sum(1 for block in blocks if str(getattr(getattr(block, "label", ""), "value", getattr(block, "label", ""))).lower() == "image")
        char_score = min(len(compact) / max(self.settings.ocr_quality_min_chars, 1), 2.0) / 2.0
        score = round(max(0.0, min(1.0, (char_score * 0.45) + (korean_ratio * 0.3) + (average_confidence * 0.25))), 4)
        reasons: list[str] = []
        if len(compact) == 0:
            reasons.append("empty_text")
        elif len(compact) < self.settings.ocr_quality_min_chars:
            reasons.append("low_text")
        if text_count and korean_ratio < self.settings.ocr_quality_min_korean_ratio:
            reasons.append("low_korean_ratio")
        if average_confidence and average_confidence < 0.75:
            reasons.append("low_confidence")
        if article_count == 0:
            reasons.append("no_articles")
        if image_count > 0 and len(compact) < 20:
            reasons.append("image_only")
        status = "ready"
        if score < 0.45 or "empty_text" in reasons or "no_articles" in reasons:
            status = "blocked"
        elif score < 0.7 or reasons:
            status = "warning"
        return {
            "status": status,
            "score": score,
            "char_count": len(compact),
            "korean_ratio": korean_ratio,
            "average_confidence": average_confidence,
            "block_count": len(blocks),
            "image_count": image_count,
            "article_count": article_count,
            "needs_review": status != "ready",
            "reasons": reasons,
        }

    def _log(self, job_id: int, pdf_file_id: int | None, page_id: int | None, step_name: str, status: str, message: str) -> None:
        self.db.add(
            ProcessingLog(
                job_id=job_id,
                pdf_file_id=pdf_file_id,
                page_id=page_id,
                step_name=step_name,
                status=status,
                message=message,
            )
        )

    def _log_and_commit(
        self,
        job_id: int,
        pdf_file_id: int | None,
        page_id: int | None,
        step_name: str,
        status: str,
        message: str,
    ) -> None:
        self._log(job_id, pdf_file_id, page_id, step_name, status, message)
        self.db.commit()

    @staticmethod
    def _caption_entries(captions: list[Any]) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, int, int]] = set()
        for caption in captions:
            text = str(getattr(caption, "text", "") or "").strip()
            bbox = getattr(caption, "bbox", None)
            if not text or not isinstance(bbox, list) or len(bbox) != 4:
                continue
            key = (text, *bbox)
            if key in seen:
                continue
            seen.add(key)
            entries.append(
                {
                    "text": text,
                    "bbox": bbox[:],
                    "confidence": float(getattr(caption, "confidence", 0.0) or 0.0),
                }
            )
        return entries

    @classmethod
    def _article_caption_entries(cls, article: Any) -> list[dict[str, Any]]:
        flattened: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, int, int]] = set()
        for image in getattr(article, "images", []):
            for caption in cls._caption_entries(getattr(image, "captions", [])):
                bbox = caption.get("bbox") or [-1, -1, -1, -1]
                key = (str(caption.get("text") or "").strip(), *bbox)
                if key in seen:
                    continue
                seen.add(key)
                flattened.append(caption)
        return flattened
