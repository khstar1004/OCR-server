from __future__ import annotations

import json
import mimetypes
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.runtime_config import runtime_config_value
from app.schemas.job import ArticleResponse, JobResultResponse
from app.utils.json_utils import dump_json


MAX_TITLE_LENGTH = 30
MAX_CAPTION_LENGTH = 30
MAX_PUBLICATION_LENGTH = 20
MAX_IMAGE_BYTES = 10 * 1024 * 1024


class NewsDeliveryError(Exception):
    def __init__(self, message: str, *, status_code: int = 502, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass(slots=True)
class NewsImageRecord:
    caption: str | None
    path: Path


@dataclass(slots=True)
class NewsImageCheck:
    image_id: int
    image_path: str | None
    src: str | None
    included: bool
    reason: str | None
    size_bytes: int | None
    caption: str


@dataclass(slots=True)
class NewsArticleRecord:
    article_id: int
    title: str
    body_text: str
    relevance_score: float
    publication: str
    issue_date: str
    bundle_dir: Path
    images: list[NewsImageRecord]


@dataclass(slots=True)
class NewsDeliveryResult:
    target_url: str
    delivered: int
    failed: int
    skipped: int = 0


class NewsDeliveryClient:
    def __init__(self) -> None:
        self.settings = get_settings()

    def has_default_target(self) -> bool:
        return bool(self.resolve_target_url(None))

    def resolve_target_url(self, _target_url: str | None) -> str | None:
        configured = str(runtime_config_value("target_api_base_url", self.settings.target_api_base_url or "", self.settings) or "").strip()
        if not configured:
            return None
        if configured.rstrip("/").endswith("/news"):
            return configured.rstrip("/")
        return f"{configured.rstrip('/')}/news"

    def deliver_job_result(
        self,
        job_result: JobResultResponse,
        *,
        target_url: str | None = None,
        state_filename: str = "delivery.json",
        raise_on_failure: bool = False,
    ) -> NewsDeliveryResult:
        articles = [article for file_result in job_result.files for article in file_result.articles]
        return self.deliver_articles(
            articles,
            target_url=target_url,
            state_filename=state_filename,
            raise_on_failure=raise_on_failure,
        )

    def deliver_articles(
        self,
        articles: list[ArticleResponse],
        *,
        target_url: str | None = None,
        state_filename: str = "delivery.json",
        raise_on_failure: bool = False,
    ) -> NewsDeliveryResult:
        resolved_target_url = self.resolve_target_url(target_url)
        if not resolved_target_url:
            raise NewsDeliveryError("delivery URL is not configured", status_code=409)

        if not articles:
            raise NewsDeliveryError("no articles are available for delivery", status_code=409)

        prepared_records: list[NewsArticleRecord] = []
        failed = 0
        attempted_at = self._utcnow()

        for article in articles:
            prepared_records.append(self._prepare_article_record(article))
        request_body = self._build_request_body(prepared_records)

        try:
            response = self._post_batch(resolved_target_url, prepared_records, request_body)
        except Exception as exc:  # noqa: BLE001
            state = self._failure_state(
                target_url=resolved_target_url,
                attempted_at=attempted_at,
                response_code=502,
                last_error=str(exc),
            )
            for article_index, article in enumerate(articles):
                self._write_state(article, state_filename, self._attach_request_metadata(state, request_body, article_index))
            raise NewsDeliveryError(f"delivery failed: {exc}", status_code=502, details=state) from exc

        if 200 <= response.status_code < 300:
            success_state = self._success_state(
                target_url=resolved_target_url,
                attempted_at=attempted_at,
                response_code=response.status_code,
                batch_size=len(prepared_records),
            )
            for article_index, article in enumerate(articles):
                self._write_state(article, state_filename, self._attach_request_metadata(success_state, request_body, article_index))
            return NewsDeliveryResult(
                target_url=resolved_target_url,
                delivered=len(articles),
                failed=failed,
            )

        if response.status_code == 400 and len(prepared_records) > 1:
            return self._deliver_individually_after_batch_failure(
                prepared_records,
                resolved_target_url,
                state_filename=state_filename,
                attempted_at=attempted_at,
                already_failed=failed,
                raise_on_failure=raise_on_failure,
            )

        failure_state = self._failure_state(
            target_url=resolved_target_url,
            attempted_at=attempted_at,
            response_code=response.status_code,
            last_error=self._error_message(response),
            details=self._response_details(response),
        )
        for article_index, article in enumerate(articles):
            self._write_state(article, state_filename, self._attach_request_metadata(failure_state, request_body, article_index))

        if raise_on_failure:
            raise NewsDeliveryError(
                failure_state["last_error"],
                status_code=response.status_code,
                details=failure_state,
            )

        return NewsDeliveryResult(
            target_url=resolved_target_url,
            delivered=0,
            failed=len(articles),
        )

    def build_payload_preview(
        self,
        articles: list[ArticleResponse],
        *,
        target_url: str | None = None,
    ) -> dict[str, Any]:
        resolved_target_url = self.resolve_target_url(target_url)
        records: list[NewsArticleRecord] = []
        checks_by_article: list[list[NewsImageCheck]] = []

        for article_index, article in enumerate(articles):
            record, image_checks = self._prepare_article_record_with_checks(article, article_index=article_index)
            records.append(record)
            checks_by_article.append(image_checks)

        request_body = self._build_request_body(records)
        article_previews: list[dict[str, Any]] = []
        included_image_count = 0
        skipped_image_count = 0
        ready_count = 0
        warning_count = 0
        blocked_count = 0
        summary_reasons: dict[str, int] = {}

        for article_index, (article, record, image_checks) in enumerate(zip(articles, records, checks_by_article, strict=True)):
            included = [item for item in image_checks if item.included]
            skipped = [item for item in image_checks if not item.included]
            validation_status, validation_reasons = self._validate_prepared_article(record, image_checks)
            if validation_status == "blocked":
                blocked_count += 1
            elif validation_status == "warning":
                warning_count += 1
            else:
                ready_count += 1
            for reason in validation_reasons:
                summary_reasons[reason] = summary_reasons.get(reason, 0) + 1
            included_image_count += len(included)
            skipped_image_count += len(skipped)
            article_previews.append(
                {
                    "article_id": article.article_id,
                    "page_number": article.page_number,
                    "article_order": article.article_order,
                    "validation_status": validation_status,
                    "validation_reasons": validation_reasons,
                    "title": record.title,
                    "body_text_length": len(record.body_text),
                    "relevance_score": record.relevance_score,
                    "publication": record.publication,
                    "issue_date": record.issue_date,
                    "included_image_count": len(included),
                    "skipped_image_count": len(skipped),
                    "images": [
                        {
                            "image_id": item.image_id,
                            "image_path": item.image_path,
                            "src": item.src,
                            "included": item.included,
                            "reason": item.reason,
                            "size_bytes": item.size_bytes,
                            "caption": item.caption,
                        }
                        for item in image_checks
                    ],
                    "request_article": request_body[article_index],
                }
            )

        delivery_status = "ready"
        if not resolved_target_url or blocked_count:
            delivery_status = "blocked"
        elif warning_count or skipped_image_count:
            delivery_status = "warning"
        if not resolved_target_url:
            summary_reasons["target_not_configured"] = summary_reasons.get("target_not_configured", 0) + 1
        return {
            "target_url": resolved_target_url,
            "target_configured": bool(resolved_target_url),
            "delivery_status": delivery_status,
            "ready_count": ready_count,
            "warning_count": warning_count,
            "blocked_count": blocked_count,
            "validation_summary": [
                reason for reason, _count in sorted(summary_reasons.items(), key=lambda item: (-item[1], item[0]))[:8]
            ],
            "article_count": len(article_previews),
            "included_image_count": included_image_count,
            "skipped_image_count": skipped_image_count,
            "articles": article_previews,
            "body": request_body,
        }

    def _deliver_individually_after_batch_failure(
        self,
        records: list[NewsArticleRecord],
        target_url: str,
        *,
        state_filename: str,
        attempted_at: datetime,
        already_failed: int,
        raise_on_failure: bool,
    ) -> NewsDeliveryResult:
        delivered = 0
        failed = already_failed

        for record in records:
            request_body = self._build_request_body([record])
            response = self._post_batch(target_url, [record], request_body)
            if 200 <= response.status_code < 300:
                self._write_state_for_record(
                    record,
                    state_filename,
                    self._attach_request_metadata(
                        self._success_state(
                            target_url=target_url,
                            attempted_at=attempted_at,
                            response_code=response.status_code,
                            batch_size=1,
                        ),
                        request_body,
                        0,
                    ),
                )
                delivered += 1
                continue

            failed += 1
            state = self._failure_state(
                target_url=target_url,
                attempted_at=attempted_at,
                response_code=response.status_code,
                last_error=self._error_message(response),
                details=self._response_details(response),
            )
            self._write_state_for_record(record, state_filename, self._attach_request_metadata(state, request_body, 0))

        if raise_on_failure and failed > already_failed:
            raise NewsDeliveryError("delivery failed for one or more articles", status_code=400)

        return NewsDeliveryResult(
            target_url=target_url,
            delivered=delivered,
            failed=failed,
        )

    def _post_batch(
        self,
        target_url: str,
        records: list[NewsArticleRecord],
        request_body: list[dict[str, Any]],
    ) -> httpx.Response:
        headers = {}
        if self.settings.target_api_token:
            headers["Authorization"] = f"Bearer {self.settings.target_api_token}"

        with ExitStack() as stack:
            files: list[tuple[str, tuple[str | None, Any, str]]] = []
            for article_index, record in enumerate(records):
                for image_index, image in enumerate(record.images):
                    part_name = f"file_{article_index}_{image_index}"
                    mime_type = mimetypes.guess_type(image.path.name)[0] or "application/octet-stream"
                    handle = stack.enter_context(image.path.open("rb"))
                    files.append((part_name, (image.path.name, handle, mime_type)))

            files.insert(
                0,
                (
                    "body",
                    (None, json.dumps(request_body, ensure_ascii=False), "application/json"),
                ),
            )

            return httpx.post(
                target_url,
                files=files,
                headers=headers,
                timeout=float(runtime_config_value("target_api_timeout_sec", self.settings.target_api_timeout_sec, self.settings)),
            )

    @staticmethod
    def _build_request_body(records: list[NewsArticleRecord]) -> list[dict[str, Any]]:
        request_body: list[dict[str, Any]] = []
        for article_index, record in enumerate(records):
            images = [
                {
                    "caption": str(image.caption or "").strip(),
                    "src": f"file_{article_index}_{image_index}",
                }
                for image_index, image in enumerate(record.images)
            ]
            request_body.append(
                {
                    "title": record.title,
                    "body_text": record.body_text,
                    "imgs": images,
                    "relevance_score": record.relevance_score,
                    "publication": record.publication,
                    "issue_date": record.issue_date,
                }
            )
        return request_body

    @staticmethod
    def _attach_request_metadata(
        state: dict[str, Any],
        request_body: list[dict[str, Any]],
        article_index: int,
    ) -> dict[str, Any]:
        payload = dict(state)
        payload["request_batch_size"] = len(request_body)
        payload["request_article_index"] = article_index
        if 0 <= article_index < len(request_body):
            payload["request_article"] = request_body[article_index]
        return payload

    def _prepare_article_record(self, article: ArticleResponse) -> NewsArticleRecord:
        record, _image_checks = self._prepare_article_record_with_checks(article)
        return record

    def _prepare_article_record_with_checks(
        self,
        article: ArticleResponse,
        *,
        article_index: int | None = None,
    ) -> tuple[NewsArticleRecord, list[NewsImageCheck]]:
        source_metadata = article.source_metadata
        publication = self._truncate_text(getattr(source_metadata, "publication", None), MAX_PUBLICATION_LENGTH)
        issue_date = self._normalize_issue_date(getattr(source_metadata, "issue_date", None))
        title = self._truncate_text(article.title, MAX_TITLE_LENGTH)
        body_text = self._truncate_text(article.body_text, 2000)

        images, image_checks = self._prepare_article_images(article, article_index=article_index)

        bundle_dir = self._resolve_bundle_dir(article)
        score = article.relevance_score if article.relevance_score is not None else 0.0
        normalized_score = self._coerce_score(score)
        return (
            NewsArticleRecord(
                article_id=article.article_id,
                title=title,
                body_text=body_text,
                relevance_score=normalized_score,
                publication=publication,
                issue_date=issue_date,
                bundle_dir=bundle_dir,
                images=images,
            ),
            image_checks,
        )

    def _prepare_article_images(
        self,
        article: ArticleResponse,
        *,
        article_index: int | None = None,
    ) -> tuple[list[NewsImageRecord], list[NewsImageCheck]]:
        images: list[NewsImageRecord] = []
        image_checks: list[NewsImageCheck] = []

        for image in article.images:
            raw_path = str(image.image_path or "").strip()
            caption = self._truncate_text(self._join_caption_lines(image), MAX_CAPTION_LENGTH)
            resolved_path = self._resolve_output_path(raw_path)

            if resolved_path is None or not resolved_path.exists() or not resolved_path.is_file():
                image_checks.append(
                    NewsImageCheck(
                        image_id=image.image_id,
                        image_path=raw_path or None,
                        src=None,
                        included=False,
                        reason="missing_file",
                        size_bytes=None,
                        caption=caption,
                    )
                )
                continue

            size_bytes = resolved_path.stat().st_size
            if size_bytes > MAX_IMAGE_BYTES:
                image_checks.append(
                    NewsImageCheck(
                        image_id=image.image_id,
                        image_path=str(resolved_path),
                        src=None,
                        included=False,
                        reason="too_large",
                        size_bytes=size_bytes,
                        caption=caption,
                    )
                )
                continue

            included_index = len(images)
            src = f"file_{article_index}_{included_index}" if article_index is not None else None
            images.append(NewsImageRecord(caption=caption or None, path=resolved_path))
            image_checks.append(
                NewsImageCheck(
                    image_id=image.image_id,
                    image_path=str(resolved_path),
                    src=src,
                    included=True,
                    reason=None,
                    size_bytes=size_bytes,
                    caption=caption,
                )
            )

        return images, image_checks

    @staticmethod
    def _validate_prepared_article(
        record: NewsArticleRecord,
        image_checks: list[NewsImageCheck],
    ) -> tuple[str, list[str]]:
        blocked_reasons: list[str] = []
        warning_reasons: list[str] = []
        if not record.title:
            blocked_reasons.append("missing_title")
        if not record.body_text:
            blocked_reasons.append("missing_body")
        if not record.publication:
            warning_reasons.append("missing_publication")
        if not record.issue_date:
            warning_reasons.append("missing_issue_date")
        skipped = [item for item in image_checks if not item.included]
        if skipped:
            warning_reasons.extend(sorted({f"image_{item.reason or 'skipped'}" for item in skipped}))
        if blocked_reasons:
            return "blocked", blocked_reasons + warning_reasons
        if warning_reasons:
            return "warning", warning_reasons
        return "ready", []

    @staticmethod
    def _join_caption_lines(article_image: Any) -> str:
        lines = [str(caption.text or "").strip() for caption in getattr(article_image, "captions", []) if str(caption.text or "").strip()]
        return " ".join(lines).strip()

    def _resolve_bundle_dir(self, article: ArticleResponse) -> Path | None:
        candidates = [article.bundle_dir, article.metadata_path, article.markdown_path]
        for value in candidates:
            resolved = self._resolve_output_path(value)
            if resolved is None:
                continue
            if resolved.name in {"article.json", "article.md"}:
                resolved = resolved.parent
            return resolved
        return None

    def _resolve_output_path(self, value: str | None) -> Path | None:
        if not value:
            return None
        direct = Path(value).expanduser()
        if direct.exists():
            return direct
        resolved = self.settings.resolve_output_path(value)
        if resolved is not None:
            return resolved
        return direct

    @staticmethod
    def _truncate_text(value: Any, limit: int) -> str:
        if value is None:
            return ""
        text = str(value).strip()
        if not text:
            return ""
        return text[:limit].strip()

    @staticmethod
    def _normalize_issue_date(value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return text[:10]

    @staticmethod
    def _coerce_score(value: Any) -> float:
        if value in (None, ""):
            return 0.0
        try:
            score = float(value)
        except (TypeError, ValueError):
            return 0.0
        if score > 1.0 and score <= 100.0:
            score /= 100.0
        return max(0.0, min(score, 1.0))

    @staticmethod
    def _response_details(response: httpx.Response) -> dict[str, Any]:
        try:
            payload = response.json()
        except Exception:  # noqa: BLE001
            return {}
        if not isinstance(payload, dict):
            return {}
        nested = payload.get("body")
        if isinstance(nested, dict):
            return nested
        return payload

    @classmethod
    def _error_message(cls, response: httpx.Response) -> str:
        details = cls._response_details(response)
        error_code = str(details.get("error_code") or "").strip()
        index = details.get("index")
        child_index = details.get("child_index")
        if error_code:
            location = ""
            if index is not None:
                location = f" article[{index}]"
                if child_index is not None:
                    location += f" image[{child_index}]"
            return f"target rejected{location}: {error_code}"
        text = getattr(response, "text", "") or f"HTTP {response.status_code}"
        return str(text).strip() or f"HTTP {response.status_code}"

    def _write_state(self, article: ArticleResponse, state_filename: str, payload: dict[str, Any]) -> None:
        bundle_dir = self._resolve_bundle_dir(article)
        if bundle_dir is None:
            return
        dump_json(bundle_dir / state_filename, payload)

    @staticmethod
    def _write_state_for_record(record: NewsArticleRecord, state_filename: str, payload: dict[str, Any]) -> None:
        dump_json(record.bundle_dir / state_filename, payload)

    @classmethod
    def _success_state(
        cls,
        *,
        target_url: str,
        attempted_at: datetime,
        response_code: int,
        batch_size: int,
    ) -> dict[str, Any]:
        timestamp = cls._format_timestamp(attempted_at)
        return {
            "delivery_status": "delivered",
            "transport": "multipart_news",
            "endpoint": target_url,
            "request_format": "multipart/form-data",
            "attempted_at": timestamp,
            "updated_at": timestamp,
            "delivered_at": timestamp,
            "response_code": response_code,
            "batch_size": batch_size,
            "delivered_articles": batch_size,
        }

    @classmethod
    def _failure_state(
        cls,
        *,
        target_url: str,
        attempted_at: datetime,
        response_code: int,
        last_error: str,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "delivery_status": "failed",
            "transport": "multipart_news",
            "endpoint": target_url,
            "request_format": "multipart/form-data",
            "attempted_at": cls._format_timestamp(attempted_at),
            "updated_at": cls._format_timestamp(cls._utcnow()),
            "response_code": response_code,
            "last_error": last_error,
        }
        if details:
            payload.update({key: value for key, value in details.items() if value is not None})
        return payload

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _utcnow() -> datetime:
        return datetime.now(timezone.utc)
