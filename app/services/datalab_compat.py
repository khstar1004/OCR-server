from __future__ import annotations

import base64
import html
import shutil
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlparse
from uuid import uuid4

import httpx
from PIL import Image

from app.core.config import Settings
from app.domain.types import BlockLabel, OCRBlock, PageLayout
from app.ocr.rendering import render_pdf_document
from app.services.article_cluster import ArticleClusterer
from app.services.artifacts import build_job_artifact_layout, load_json, slugify, write_json
from app.services.ocr_engine import OCREngine
from app.services.runtime_config import runtime_config_value

MARKER_OUTPUT_FORMATS = {"json", "markdown", "html", "chunks"}
MARKER_MODES = {"fast", "balanced", "accurate"}


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_pdf_filename(filename: str) -> bool:
    return filename.lower().endswith(".pdf")


def safe_filename(filename: str | None, fallback: str) -> str:
    if not filename:
        return fallback
    candidate = Path(filename).name.strip()
    return candidate or fallback


def read_image_size(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        return image.width, image.height


def parse_page_range(page_range: str | None) -> set[int] | None:
    if not page_range:
        return None
    values: set[int] = set()
    for chunk in page_range.split(","):
        token = chunk.strip()
        if not token:
            continue
        if "-" in token:
            start_str, end_str = token.split("-", 1)
            start = int(start_str.strip())
            end = int(end_str.strip())
            if start < 0 or end < start:
                raise ValueError("page_range must use ascending zero-based page indexes")
            values.update(range(start, end + 1))
            continue
        value = int(token)
        if value < 0:
            raise ValueError("page_range must use zero-based page indexes")
        values.add(value)
    return values


def normalize_marker_output_formats(output_format: str | None) -> list[str]:
    raw_value = (output_format or "").strip().lower()
    if not raw_value:
        return ["markdown"]
    formats: list[str] = []
    for item in raw_value.split(","):
        value = item.strip().lower()
        if not value:
            continue
        if value not in MARKER_OUTPUT_FORMATS:
            allowed = ", ".join(sorted(MARKER_OUTPUT_FORMATS))
            raise ValueError(f"output_format must contain only: {allowed}")
        if value not in formats:
            formats.append(value)
    return formats or ["markdown"]


def normalize_marker_mode(mode: str | None) -> str:
    value = (mode or "balanced").strip().lower()
    if value not in MARKER_MODES:
        raise ValueError("mode must be one of: fast, balanced, accurate")
    return value


@dataclass(frozen=True, slots=True)
class ResolvedInputFile:
    source: str
    file_name: str
    content: bytes


class DatalabCompatService:
    def __init__(self, settings: Settings, engine: OCREngine):
        self.settings = settings
        self.engine = engine
        self.clusterer = ArticleClusterer()
        self.root_dir = settings.output_root / "_compat_api"
        self.requests_dir = self.root_dir / "requests"
        self.workflows_dir = self.root_dir / "workflows"
        self.executions_dir = self.root_dir / "executions"
        self._lock = threading.RLock()
        self.ensure_directories()

    def ensure_directories(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.workflows_dir.mkdir(parents=True, exist_ok=True)
        self.executions_dir.mkdir(parents=True, exist_ok=True)

    def versions(self) -> dict[str, Any]:
        model_source = self.settings.chandra_model_dir or self.settings.chandra_model_id
        return {
            "service": "army-ocr",
            "compat_mode": "datalab-like-v1",
            "ocr_backend": self.settings.ocr_backend,
            "chandra_model": str(model_source),
        }

    def list_step_types(self) -> dict[str, Any]:
        return {
            "step_types": [
                {
                    "step_key": "marker_parse",
                    "name": "Marker Parse",
                    "description": "Convert PDF/image inputs into page-level structured blocks and article candidates.",
                    "available": True,
                    "input_types": ["pdf", "png", "jpg", "jpeg", "webp"],
                },
                {
                    "step_key": "ocr",
                    "name": "OCR",
                    "description": "Run Chandra-backed OCR and return page-level text blocks.",
                    "available": True,
                    "input_types": ["pdf", "png", "jpg", "jpeg", "webp"],
                },
            ],
            "versions": self.versions(),
        }

    def create_request(
        self,
        request_kind: str,
        request_check_url: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        request_id = uuid4().hex
        resolved_check_url = request_check_url or f"/api/v1/{request_kind}/{request_id}"
        if "{request_id}" in resolved_check_url:
            resolved_check_url = resolved_check_url.format(request_id=request_id)
        record = {
            "request_id": request_id,
            "request_kind": request_kind,
            "request_check_url": resolved_check_url,
            "status": "processing",
            "created_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "meta": meta or {},
            "error": None,
            "page_image_paths": [],
            "result": None,
        }
        self._write_request_record(request_id, record)
        return request_id

    def submission_response(self, request_id: str) -> dict[str, Any]:
        record = self.get_request_record(request_id)
        return {
            "request_id": record["request_id"],
            "request_check_url": record["request_check_url"],
            "success": True,
            "error": None,
            "versions": self.versions(),
        }

    def get_request_result(self, request_id: str) -> dict[str, Any]:
        record = self.get_request_record(request_id)
        result = record.get("result")
        if isinstance(result, dict):
            return result
        return {
            "status": str(record.get("status") or "processing"),
            "success": False if record.get("status") == "failed" else None,
            "error": record.get("error"),
            "versions": self.versions(),
        }

    def get_request_record(self, request_id: str) -> dict[str, Any]:
        path = self._request_record_path(request_id)
        if not path.exists():
            raise KeyError(f"request not found: {request_id}")
        return load_json(path)

    def list_requests(
        self,
        *,
        limit: int = 50,
        playground_only: bool = False,
        request_kind: str | None = None,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 500))
        items: list[dict[str, Any]] = []
        for record_path in self.requests_dir.glob("*/record.json"):
            try:
                record = load_json(record_path)
            except Exception:
                continue
            meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
            if playground_only and meta.get("playground") is not True:
                continue
            if request_kind and str(record.get("request_kind") or "") != request_kind:
                continue
            items.append(self._request_history_item(record, fallback_request_id=record_path.parent.name))

        items.sort(key=lambda item: float(item.get("_sort_timestamp") or 0.0), reverse=True)
        visible_items = items[:safe_limit]
        for item in visible_items:
            item.pop("_sort_timestamp", None)
        return {
            "success": True,
            "count": len(visible_items),
            "total_count": len(items),
            "limit": safe_limit,
            "playground_only": playground_only,
            "request_kind": request_kind,
            "items": visible_items,
            "versions": self.versions(),
        }

    def process_ocr_request(
        self,
        request_id: str,
        *,
        file_bytes: bytes,
        file_name: str,
        page_number: int = 1,
        width: int | None = None,
        height: int | None = None,
        dpi: int = 300,
        max_pages: int | None = None,
        page_range: str | None = None,
    ) -> None:
        file_name = safe_filename(file_name, "document.bin")
        started_at = utcnow_iso()
        started_perf = time.perf_counter()
        try:
            pages, page_images = self._process_document_pages(
                request_id=request_id,
                file_bytes=file_bytes,
                file_name=file_name,
                page_number=page_number,
                width=width,
                height=height,
                dpi=dpi,
                max_pages=max_pages,
                page_range=page_range,
                page_callback=None,
            )
            result = {
                "status": "complete",
                "pages": [self._serialize_ocr_page(page) for page in pages],
                "success": True,
                "error": None,
                "page_count": len(pages),
                "total_cost": 0,
                "cost_breakdown": {"credits": 0},
                "versions": self.versions(),
            }
            result["runtime"] = self._runtime_metadata(
                request_id=request_id,
                request_kind="ocr",
                file_name=file_name,
                file_size_bytes=len(file_bytes),
                started_at=started_at,
                started_perf=started_perf,
                page_count=len(pages),
                status="complete",
            )
            self._update_request_record(
                request_id,
                status="complete",
                page_image_paths=[str(path) for path in page_images],
                result=result,
                error=None,
            )
        except Exception as exc:
            runtime = self._runtime_metadata(
                request_id=request_id,
                request_kind="ocr",
                file_name=file_name,
                file_size_bytes=len(file_bytes),
                started_at=started_at,
                started_perf=started_perf,
                page_count=None,
                status="failed",
                error_code=self._classify_error(exc),
            )
            self._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={
                    "status": "failed",
                    "pages": None,
                    "success": False,
                    "error": str(exc),
                    "page_count": None,
                    "total_cost": 0,
                    "cost_breakdown": {"credits": 0},
                    "versions": self.versions(),
                    "runtime": runtime,
                },
            )

    def process_marker_request(
        self,
        request_id: str,
        *,
        file_bytes: bytes,
        file_name: str,
        page_number: int = 1,
        width: int | None = None,
        height: int | None = None,
        dpi: int = 300,
        max_pages: int | None = None,
        page_range: str | None = None,
        output_format: str = "markdown",
        mode: str = "balanced",
        paginate: bool = False,
        add_block_ids: bool = False,
        include_markdown_in_chunks: bool = False,
        skip_cache: bool = False,
        extras: str | None = None,
        additional_config: str | None = None,
    ) -> None:
        file_name = safe_filename(file_name, "document.bin")
        started_at = utcnow_iso()
        started_perf = time.perf_counter()
        try:
            output_formats = normalize_marker_output_formats(output_format)
            normalized_mode = normalize_marker_mode(mode)

            progress_pages: list[PageLayout] = []
            progress_page_images: list[Path] = []

            def update_page_progress(page: PageLayout, page_image: Path, total_pages: int) -> None:
                progress_pages.append(page)
                progress_page_images.append(page_image)
                processed_count = len(progress_pages)
                partial_result = self._build_marker_result(
                    request_id,
                    file_name,
                    progress_pages,
                    output_formats=output_formats,
                    mode=normalized_mode,
                    max_pages=max_pages,
                    page_range=page_range,
                    paginate=paginate,
                    add_block_ids=add_block_ids,
                    include_markdown_in_chunks=include_markdown_in_chunks,
                    skip_cache=skip_cache,
                    extras=extras,
                    additional_config=additional_config,
                )
                progress = self._progress_metadata(
                    processed_pages=processed_count,
                    total_pages=total_pages,
                    status="processing",
                )
                partial_result["status"] = "processing"
                partial_result["success"] = None
                partial_result["page_count"] = total_pages
                partial_result["processed_page_count"] = processed_count
                partial_result["progress"] = progress
                partial_result["runtime"] = self._runtime_metadata(
                    request_id=request_id,
                    request_kind="marker",
                    file_name=file_name,
                    file_size_bytes=len(file_bytes),
                    started_at=started_at,
                    started_perf=started_perf,
                    page_count=total_pages,
                    status="processing",
                )
                partial_result["metadata"].update(
                    {
                        "processed_page_count": processed_count,
                        "total_page_count": total_pages,
                        "processing_complete": False,
                    }
                )
                partial_result["json"]["page_count"] = total_pages
                if isinstance(partial_result.get("result"), dict) and isinstance(partial_result["result"].get("json"), dict):
                    partial_result["result"]["json"]["page_count"] = total_pages
                self._update_request_record(
                    request_id,
                    status="processing",
                    page_image_paths=[str(path) for path in progress_page_images],
                    result=partial_result,
                    error=None,
                )

            pages, page_images = self._process_document_pages(
                request_id=request_id,
                file_bytes=file_bytes,
                file_name=file_name,
                page_number=page_number,
                width=width,
                height=height,
                dpi=dpi,
                max_pages=max_pages,
                page_range=page_range,
                page_callback=update_page_progress,
            )
            result = self._build_marker_result(
                request_id,
                file_name,
                pages,
                output_formats=output_formats,
                mode=normalized_mode,
                max_pages=max_pages,
                page_range=page_range,
                paginate=paginate,
                add_block_ids=add_block_ids,
                include_markdown_in_chunks=include_markdown_in_chunks,
                skip_cache=skip_cache,
                extras=extras,
                additional_config=additional_config,
            )
            result["runtime"] = self._runtime_metadata(
                request_id=request_id,
                request_kind="marker",
                file_name=file_name,
                file_size_bytes=len(file_bytes),
                started_at=started_at,
                started_perf=started_perf,
                page_count=len(pages),
                status="complete",
            )
            result["processed_page_count"] = len(pages)
            result["progress"] = self._progress_metadata(
                processed_pages=len(pages),
                total_pages=len(pages),
                status="complete",
            )
            result["metadata"].update(
                {
                    "processed_page_count": len(pages),
                    "total_page_count": len(pages),
                    "processing_complete": True,
                }
            )
            self._update_request_record(
                request_id,
                status="complete",
                page_image_paths=[str(path) for path in page_images],
                result=result,
                error=None,
            )
        except Exception as exc:
            runtime = self._runtime_metadata(
                request_id=request_id,
                request_kind="marker",
                file_name=file_name,
                file_size_bytes=len(file_bytes),
                started_at=started_at,
                started_perf=started_perf,
                page_count=None,
                status="failed",
                error_code=self._classify_error(exc),
            )
            self._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={
                    "status": "failed",
                    "success": False,
                    "error": str(exc),
                    "page_count": None,
                    "markdown": None,
                    "html": None,
                    "json": None,
                    "chunks": None,
                    "versions": self.versions(),
                    "runtime": runtime,
                },
            )

    def cleanup_requests(self, *, older_than_hours: float = 24.0, status_filter: str | None = None, dry_run: bool = True) -> dict[str, Any]:
        if older_than_hours <= 0:
            raise ValueError("older_than_hours must be positive")
        cutoff_timestamp = time.time() - (older_than_hours * 3600)
        deleted: list[str] = []
        candidates: list[str] = []
        for record_path in self.requests_dir.glob("*/record.json"):
            try:
                record = load_json(record_path)
                updated_at = str(record.get("updated_at") or record.get("created_at") or "")
                updated_ts = datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            if updated_ts > cutoff_timestamp:
                continue
            if status_filter and str(record.get("status") or "") != status_filter:
                continue
            request_id = str(record.get("request_id") or record_path.parent.name)
            candidates.append(request_id)
            if not dry_run:
                shutil.rmtree(record_path.parent, ignore_errors=True)
                deleted.append(request_id)
        return {
            "success": True,
            "dry_run": dry_run,
            "older_than_hours": older_than_hours,
            "status_filter": status_filter,
            "candidate_count": len(candidates),
            "deleted_count": len(deleted),
            "request_ids": candidates if dry_run else deleted,
        }

    @classmethod
    def _request_history_item(cls, record: dict[str, Any], *, fallback_request_id: str) -> dict[str, Any]:
        meta = record.get("meta") if isinstance(record.get("meta"), dict) else {}
        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        runtime = result.get("runtime") if isinstance(result.get("runtime"), dict) else {}
        progress = result.get("progress") if isinstance(result.get("progress"), dict) else {}
        request_id = str(record.get("request_id") or fallback_request_id)
        status_value = str(result.get("status") or record.get("status") or "processing")
        created_at = str(record.get("created_at") or "")
        updated_at = str(record.get("updated_at") or created_at)
        page_count = cls._first_non_empty(
            result.get("page_count"),
            metadata.get("total_page_count"),
            runtime.get("page_count"),
        )
        processed_page_count = cls._first_non_empty(
            result.get("processed_page_count"),
            metadata.get("processed_page_count"),
            progress.get("processed_pages"),
        )
        file_name = str(
            cls._first_non_empty(
                meta.get("file_name"),
                metadata.get("source_file"),
                runtime.get("file_name"),
                "",
            )
            or ""
        )
        return {
            "request_id": request_id,
            "request_kind": str(record.get("request_kind") or ""),
            "status": status_value,
            "created_at": created_at,
            "updated_at": updated_at,
            "file_name": file_name,
            "input_source": str(meta.get("input_source") or ""),
            "mode": str(metadata.get("mode") or meta.get("mode") or ""),
            "output_format": str(result.get("output_format") or meta.get("output_format") or ""),
            "page_count": page_count,
            "processed_page_count": processed_page_count,
            "progress": progress,
            "parse_quality_score": result.get("parse_quality_score") or metadata.get("parse_quality_score"),
            "duration_ms": runtime.get("duration_ms"),
            "error": result.get("error") or record.get("error"),
            "_sort_timestamp": cls._history_timestamp(updated_at or created_at),
        }

    @staticmethod
    def _first_non_empty(*values: Any) -> Any:
        for value in values:
            if value not in (None, ""):
                return value
        return None

    @staticmethod
    def _history_timestamp(value: str) -> float:
        if not value:
            return 0.0
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return 0.0

    def thumbnails(self, lookup_key: str, *, thumb_width: int = 300, page_range: str | None = None) -> dict[str, Any]:
        if thumb_width <= 0:
            raise ValueError("thumb_width must be positive")
        record = self.get_request_record(lookup_key)
        page_paths = [Path(path) for path in record.get("page_image_paths", []) if str(path).strip()]
        indices = parse_page_range(page_range)
        selected_paths = [path for index, path in enumerate(page_paths) if indices is None or index in indices]
        thumbnails: list[str] = []
        for image_path in selected_paths:
            if not image_path.exists():
                continue
            with Image.open(image_path) as image:
                target_height = max(1, round((thumb_width / max(image.width, 1)) * image.height))
                rendered = image.convert("RGB").resize((thumb_width, target_height))
                from io import BytesIO

                buffer = BytesIO()
                rendered.save(buffer, format="JPEG", quality=85)
                thumbnails.append(base64.b64encode(buffer.getvalue()).decode("ascii"))
        return {
            "thumbnails": thumbnails,
            "success": True,
            "error": None,
        }

    def list_workflows(self) -> dict[str, Any]:
        workflows = [load_json(path) for path in sorted(self.workflows_dir.glob("*.json"), key=lambda item: int(item.stem))]
        return {
            "workflows": workflows,
            "count": len(workflows),
            "versions": self.versions(),
        }

    def get_workflow(self, workflow_id: int) -> dict[str, Any]:
        path = self._workflow_path(workflow_id)
        if not path.exists():
            raise KeyError(f"workflow not found: {workflow_id}")
        return load_json(path)

    def create_workflow(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        raw_steps = payload.get("steps")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("steps must be a non-empty list")

        supported_step_keys = {step["step_key"] for step in self.list_step_types()["step_types"]}
        normalized_steps: list[dict[str, Any]] = []
        unique_names: set[str] = set()

        for index, step in enumerate(raw_steps, start=1):
            if not isinstance(step, dict):
                raise ValueError("steps must contain objects")
            step_key = str(step.get("step_key") or "").strip()
            unique_name = str(step.get("unique_name") or f"step_{index}").strip()
            depends_on = step.get("depends_on") or []
            settings = step.get("settings") or {}
            if step_key not in supported_step_keys:
                raise ValueError(f"unsupported step_key: {step_key}")
            if unique_name in unique_names:
                raise ValueError(f"duplicate unique_name: {unique_name}")
            if not isinstance(depends_on, list) or any(not str(item).strip() for item in depends_on):
                raise ValueError("depends_on must be a list of unique_name values")
            if not isinstance(settings, dict):
                raise ValueError("settings must be an object")
            unique_names.add(unique_name)
            normalized_steps.append(
                {
                    "step_key": step_key,
                    "unique_name": unique_name,
                    "depends_on": [str(item).strip() for item in depends_on],
                    "settings": settings,
                }
            )

        for step in normalized_steps:
            for dependency in step["depends_on"]:
                if dependency not in unique_names:
                    raise ValueError(f"unknown dependency: {dependency}")

        with self._lock:
            workflow_id = self._next_numeric_id(self.workflows_dir)
            created = utcnow_iso()
            workflow = {
                "id": workflow_id,
                "workflow_id": workflow_id,
                "name": name,
                "team_id": int(payload.get("team_id") or 0),
                "steps": normalized_steps,
                "created": created,
                "updated": created,
            }
            write_json(self._workflow_path(workflow_id), workflow)
            return workflow

    def delete_workflow(self, workflow_id: int) -> dict[str, Any]:
        path = self._workflow_path(workflow_id)
        if not path.exists():
            raise KeyError(f"workflow not found: {workflow_id}")
        path.unlink()
        return {"success": True, "workflow_id": workflow_id}

    def create_execution(self, workflow_id: int, input_config: dict[str, Any]) -> dict[str, Any]:
        workflow = self.get_workflow(workflow_id)
        with self._lock:
            execution_id = self._next_numeric_id(self.executions_dir)
            created = utcnow_iso()
            execution = {
                "execution_id": execution_id,
                "workflow_id": workflow["id"],
                "status": "PENDING",
                "created": created,
                "updated": created,
                "error": None,
                "input_config": input_config,
                "steps": {
                    step["unique_name"]: {
                        "status": "PENDING",
                        "step_key": step["step_key"],
                        "depends_on": step["depends_on"],
                    }
                    for step in workflow["steps"]
                },
                "step_outputs": {},
                "versions": self.versions(),
            }
            write_json(self._execution_path(execution_id), execution)
            return execution

    def get_execution(self, execution_id: int) -> dict[str, Any]:
        path = self._execution_path(execution_id)
        if not path.exists():
            raise KeyError(f"execution not found: {execution_id}")
        return load_json(path)

    def run_execution(self, execution_id: int) -> None:
        execution = self.get_execution(execution_id)
        workflow = self.get_workflow(int(execution["workflow_id"]))
        try:
            self._update_execution(execution_id, status="IN_PROGRESS", error=None)
            inputs = self._resolve_input_files(execution.get("input_config") or {})
            step_outputs: dict[str, dict[str, Any]] = {}

            for step in workflow["steps"]:
                unique_name = step["unique_name"]
                self._update_execution_step(execution_id, unique_name, status="IN_PROGRESS", started_at=utcnow_iso())
                per_file_outputs: dict[str, Any] = {}
                for index, item in enumerate(inputs, start=1):
                    file_key = f"file_{index:04d}_{slugify(Path(item.file_name).stem)}"
                    if step["step_key"] == "marker_parse":
                        request_id = self.create_request(
                            "marker",
                            meta={"workflow_execution_id": execution_id, "step_name": unique_name},
                        )
                        self.process_marker_request(
                            request_id,
                            file_bytes=item.content,
                            file_name=item.file_name,
                            output_format=str(step.get("settings", {}).get("output_format") or "json"),
                            max_pages=self._coerce_optional_int(step.get("settings", {}).get("max_pages")),
                            page_range=str(step.get("settings", {}).get("page_range") or "") or None,
                        )
                        marker_result = self.get_request_result(request_id)
                        per_file_outputs[file_key] = {
                            "status": "COMPLETED" if marker_result.get("success") else "FAILED",
                            "request_id": request_id,
                            "result": marker_result,
                            "source": item.source,
                            "file_name": item.file_name,
                        }
                    elif step["step_key"] == "ocr":
                        request_id = self.create_request(
                            "ocr",
                            meta={"workflow_execution_id": execution_id, "step_name": unique_name},
                        )
                        self.process_ocr_request(
                            request_id,
                            file_bytes=item.content,
                            file_name=item.file_name,
                            max_pages=self._coerce_optional_int(step.get("settings", {}).get("max_pages")),
                            page_range=str(step.get("settings", {}).get("page_range") or "") or None,
                        )
                        ocr_result = self.get_request_result(request_id)
                        per_file_outputs[file_key] = {
                            "status": "COMPLETED" if ocr_result.get("success") else "FAILED",
                            "request_id": request_id,
                            "result": ocr_result,
                            "source": item.source,
                            "file_name": item.file_name,
                        }
                    else:
                        raise ValueError(f"unsupported workflow step: {step['step_key']}")
                step_outputs[unique_name] = per_file_outputs
                self._update_execution_step(
                    execution_id,
                    unique_name,
                    status="COMPLETED",
                    completed_at=utcnow_iso(),
                    outputs=per_file_outputs,
                )

            self._update_execution(
                execution_id,
                status="COMPLETED",
                error=None,
                step_outputs=step_outputs,
            )
        except Exception as exc:
            self._update_execution(execution_id=execution_id, status="FAILED", error=str(exc))

    @staticmethod
    def _coerce_optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

    def _resolve_input_files(self, input_config: dict[str, Any]) -> list[ResolvedInputFile]:
        file_urls: list[str] = []
        if isinstance(input_config.get("file_urls"), list):
            file_urls = [str(item).strip() for item in input_config["file_urls"] if str(item).strip()]
        elif input_config.get("file_url"):
            file_urls = [str(input_config["file_url"]).strip()]
        if not file_urls:
            raise ValueError("input_config.file_url or input_config.file_urls is required")

        resolved: list[ResolvedInputFile] = []
        for source in file_urls:
            content, file_name = self._read_input_source(source)
            resolved.append(ResolvedInputFile(source=source, file_name=file_name, content=content))
        return resolved

    def _read_input_source(self, source: str) -> tuple[bytes, str]:
        candidate_path = Path(source)
        if candidate_path.exists() and candidate_path.is_file():
            return candidate_path.read_bytes(), candidate_path.name

        lowered = source.lower()
        if lowered.startswith("file://"):
            parsed = urlparse(source)
            path_text = unquote(parsed.path or "")
            if parsed.netloc:
                path_text = f"//{parsed.netloc}{path_text}"
            if path_text.startswith("/") and len(path_text) >= 3 and path_text[2] == ":":
                path_text = path_text.lstrip("/")
            path = Path(path_text)
            if not path.exists() or not path.is_file():
                raise FileNotFoundError(f"file_url not found: {source}")
            return path.read_bytes(), path.name

        if lowered.startswith(("http://", "https://")):
            timeout = float(runtime_config_value("ocr_service_timeout_sec", self.settings.ocr_service_timeout_sec, self.settings))
            response = httpx.get(source, timeout=timeout)
            response.raise_for_status()
            file_name = safe_filename(Path(urlparse(source).path).name, "downloaded.bin")
            return response.content, file_name

        raise ValueError(f"unsupported file source: {source}")

    def resolve_input_file_url(self, source: str) -> ResolvedInputFile:
        cleaned = str(source or "").strip()
        if not cleaned:
            raise ValueError("file_url is required")
        content, file_name = self._read_input_source(cleaned)
        return ResolvedInputFile(source=cleaned, file_name=file_name, content=content)

    def _process_document_pages(
        self,
        *,
        request_id: str,
        file_bytes: bytes,
        file_name: str,
        page_number: int = 1,
        width: int | None = None,
        height: int | None = None,
        dpi: int = 300,
        max_pages: int | None = None,
        page_range: str | None = None,
        page_callback: Callable[[PageLayout, Path, int], None] | None = None,
    ) -> tuple[list[PageLayout], list[Path]]:
        if max_pages is not None and max_pages <= 0:
            raise ValueError("max_pages must be greater than zero")
        request_dir = self._request_dir(request_id)
        request_dir.mkdir(parents=True, exist_ok=True)
        input_dir = request_dir / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        input_path = input_dir / file_name
        input_path.write_bytes(file_bytes)

        if is_pdf_filename(file_name):
            layout = build_job_artifact_layout(request_dir, request_id, input_path, source_key=file_name)
            layout.ensure()
            rendered = render_pdf_document(input_path, layout, dpi=dpi)
            selected = self._select_rendered_pages(rendered.pages, max_pages=max_pages, page_range=page_range)
            pages: list[PageLayout] = []
            total_pages = len(selected)
            for page in selected:
                parsed_page = self.engine.parse_page(
                    image_path=page.image_path,
                    page_number=page.page_no,
                    width=page.width,
                    height=page.height,
                )
                pages.append(parsed_page)
                if page_callback is not None:
                    page_callback(parsed_page, page.image_path, total_pages)
            return pages, [page.image_path for page in selected]

        resolved_width, resolved_height = width, height
        if resolved_width is None or resolved_height is None:
            image_width, image_height = read_image_size(input_path)
            resolved_width = image_width if resolved_width is None else resolved_width
            resolved_height = image_height if resolved_height is None else resolved_height
        if page_number <= 0:
            raise ValueError("page_number must be greater than zero")
        if (resolved_width or 0) <= 0 or (resolved_height or 0) <= 0:
            raise ValueError("unable to resolve image dimensions")
        page = self.engine.parse_page(
            image_path=input_path,
            page_number=page_number,
            width=int(resolved_width),
            height=int(resolved_height),
        )
        if page_callback is not None:
            page_callback(page, input_path, 1)
        return [page], [input_path]

    @staticmethod
    def _select_rendered_pages(pages: tuple[Any, ...], *, max_pages: int | None, page_range: str | None) -> list[Any]:
        selected = list(pages)
        indices = parse_page_range(page_range)
        if indices is not None:
            selected = [page for index, page in enumerate(selected) if index in indices]
        if max_pages is not None:
            selected = selected[:max_pages]
        return selected

    def _build_marker_result(
        self,
        request_id: str,
        file_name: str,
        pages: list[PageLayout],
        *,
        output_formats: list[str],
        mode: str,
        max_pages: int | None,
        page_range: str | None,
        paginate: bool,
        add_block_ids: bool,
        include_markdown_in_chunks: bool,
        skip_cache: bool,
        extras: str | None,
        additional_config: str | None,
    ) -> dict[str, Any]:
        page_payloads: list[dict[str, Any]] = []
        markdown_chunks: list[str] = []
        html_pages: list[str] = []
        chunk_list: list[dict[str, Any]] = []
        confidences: list[float] = []

        for page in pages:
            articles, unassigned = self.clusterer.cluster_page(page)
            article_payloads = [self._serialize_article(article) for article in articles]
            block_payloads = [
                self._serialize_block(block, include_markdown=include_markdown_in_chunks)
                for block in page.blocks
            ]
            confidences.extend(float(block.get("confidence") or 0.0) for block in block_payloads)
            chunk_list.extend(
                {
                    "page_number": page.page_number,
                    "file_name": file_name,
                    **block,
                }
                for block in block_payloads
            )
            page_payload = {
                "page_number": page.page_number,
                "width": page.width,
                "height": page.height,
                "text": self._page_text(page),
                "blocks": block_payloads,
                "articles": article_payloads,
                "unassigned": [
                    {
                        "label": getattr(block, "label", BlockLabel.UNKNOWN).value
                        if hasattr(getattr(block, "label", None), "value")
                        else str(getattr(block, "label", "unknown")),
                        "bbox": list(getattr(block, "bbox", []) or []),
                        "text": str(getattr(block, "text", "") or ""),
                    }
                    for block in unassigned
                ],
            }
            page_payloads.append(page_payload)
            markdown_chunks.append(self._page_markdown(page_payload))
            html_pages.append(self._page_html(page_payload, add_block_ids=add_block_ids))

        json_payload = {
            "request_id": request_id,
            "file_name": file_name,
            "page_count": len(page_payloads),
            "pages": page_payloads,
        }
        markdown_payload = self._join_page_markdown(markdown_chunks, page_payloads, paginate=paginate)
        html_payload = "<html><body>" + self._join_page_html(html_pages, page_payloads, paginate=paginate) + "</body></html>"
        parse_quality_score = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        normalized_output_format = ",".join(output_formats)
        output_payloads = {
            "json": json_payload,
            "markdown": markdown_payload,
            "html": html_payload,
            "chunks": chunk_list,
        }
        result_payload: Any
        if len(output_formats) == 1:
            result_payload = output_payloads[output_formats[0]]
        else:
            result_payload = {name: output_payloads[name] for name in output_formats}
        requested_extras = [value.strip() for value in str(extras or "").split(",") if value.strip()]

        result = {
            "status": "complete",
            "success": True,
            "error": None,
            "page_count": len(page_payloads),
            "output_format": normalized_output_format,
            "output_formats": output_formats,
            "markdown": markdown_payload,
            "html": html_payload,
            "json": json_payload,
            "chunks": chunk_list,
            "parse_quality_score": parse_quality_score,
            "metadata": {
                "engine": "chandra",
                "backend": self.settings.ocr_backend,
                "compat_mode": "datalab-like-v1",
                "datalab_compat": True,
                "mode": mode,
                "output_formats": output_formats,
                "max_pages": max_pages,
                "page_range": page_range,
                "paginate": paginate,
                "add_block_ids": add_block_ids,
                "include_markdown_in_chunks": include_markdown_in_chunks,
                "skip_cache": skip_cache,
                "extras": requested_extras,
                "additional_config": additional_config,
                "source_file": file_name,
                "processed_page_count": len(page_payloads),
                "parse_quality_score": parse_quality_score,
            },
            "checkpoint_id": request_id,
            "total_cost": 0,
            "cost_breakdown": {"credits": 0},
            "versions": self.versions(),
            "result": result_payload,
        }
        return result

    @staticmethod
    def _serialize_block(block: OCRBlock, *, include_markdown: bool = False) -> dict[str, Any]:
        payload = {
            "block_id": block.block_id,
            "page_number": block.page_number,
            "label": block.label.value,
            "bbox": block.bbox[:],
            "text": block.text,
            "confidence": float(block.confidence),
            "metadata": dict(block.metadata or {}),
        }
        if include_markdown:
            payload["markdown"] = DatalabCompatService._block_markdown(payload)
        return payload

    def _serialize_ocr_page(self, page: PageLayout) -> dict[str, Any]:
        line_payloads = [
            {
                "text": block.text,
                "bbox": block.bbox[:],
                "label": block.label.value,
                "confidence": float(block.confidence),
            }
            for block in page.blocks
            if block.label != BlockLabel.IMAGE
        ]
        return {
            "page_number": page.page_number,
            "width": page.width,
            "height": page.height,
            "text": self._page_text(page),
            "lines": line_payloads,
            "blocks": [self._serialize_block(block) for block in page.blocks],
            "raw_vl": page.raw_vl,
            "raw_structure": page.raw_structure,
            "raw_fallback_ocr": page.raw_fallback_ocr,
        }

    @staticmethod
    def _serialize_article(article: Any) -> dict[str, Any]:
        return {
            "title": str(getattr(article, "title", "") or ""),
            "body_text": str(getattr(article, "body_text", "") or ""),
            "title_bbox": list(getattr(article, "title_bbox", []) or []) or None,
            "article_bbox": list(getattr(article, "article_bbox", []) or []),
            "confidence": float(getattr(article, "confidence", 0.0) or 0.0),
            "layout_type": str(getattr(article, "layout_type", "") or ""),
            "images": [
                {
                    "bbox": list(getattr(image, "bbox", []) or []),
                    "confidence": float(getattr(image, "confidence", 0.0) or 0.0),
                    "captions": [
                        {
                            "text": str(getattr(caption, "text", "") or ""),
                            "bbox": list(getattr(caption, "bbox", []) or []),
                            "confidence": float(getattr(caption, "confidence", 0.0) or 0.0),
                        }
                        for caption in getattr(image, "captions", []) or []
                    ],
                }
                for image in getattr(article, "images", []) or []
            ],
        }

    @staticmethod
    def _page_text(page: PageLayout) -> str:
        chunks = [block.text.strip() for block in page.blocks if block.text.strip()]
        return "\n".join(chunks)

    @staticmethod
    def _page_markdown(page_payload: dict[str, Any]) -> str:
        article_chunks = []
        for article in page_payload.get("articles", []):
            title = str(article.get("title") or "").strip()
            body = str(article.get("body_text") or "").strip()
            if title:
                article_chunks.append(f"## {title}")
            if body:
                article_chunks.append(body)
        if not article_chunks:
            article_chunks.append(page_payload.get("text") or "")
        return f"# Page {page_payload['page_number']}\n\n" + "\n\n".join(chunk for chunk in article_chunks if chunk.strip())

    @staticmethod
    def _block_markdown(block_payload: dict[str, Any]) -> str:
        text = str(block_payload.get("text") or "").strip()
        label = str(block_payload.get("label") or "").strip().lower()
        if not text:
            return ""
        if label in {"title", "sectionheader"}:
            return f"## {text}"
        if label in {"caption", "pageheader", "pagefooter"}:
            return f"*{text}*"
        return text

    @staticmethod
    def _page_html(page_payload: dict[str, Any], *, add_block_ids: bool = False) -> str:
        parts = [f"<section data-page='{page_payload['page_number']}'>", f"<h2>Page {page_payload['page_number']}</h2>"]
        for block in page_payload.get("blocks", []):
            text = html.escape(str(block.get("text") or "")).replace("\n", "<br/>")
            label = html.escape(str(block.get("label") or "text"))
            block_id = html.escape(str(block.get("block_id") or ""))
            block_attr = f" data-block-id='{block_id}'" if add_block_ids and block_id else ""
            class_attr = f" class='block block-{label}'"
            if label in {"title", "sectionheader"} and text:
                parts.append(f"<h3{class_attr}{block_attr}>{text}</h3>")
            elif label == "image":
                parts.append(f"<figure{class_attr}{block_attr}></figure>")
            elif text:
                parts.append(f"<p{class_attr}{block_attr}>{text}</p>")
        if len(parts) == 2:
            fallback = html.escape(str(page_payload.get("text") or "")).replace("\n", "<br/>")
            parts.append(f"<p>{fallback}</p>")
        parts.append("</section>")
        return "".join(parts)

    @staticmethod
    def _join_page_markdown(chunks: list[str], page_payloads: list[dict[str, Any]], *, paginate: bool) -> str:
        pairs = [
            (page_payloads[index] if index < len(page_payloads) else {"page_number": index + 1}, chunk)
            for index, chunk in enumerate(chunks)
            if chunk.strip()
        ]
        if not paginate:
            return "\n\n".join(chunk for _, chunk in pairs)
        rendered: list[str] = []
        for index, (page_payload, chunk) in enumerate(pairs):
            if index:
                page_number = page_payload.get("page_number")
                rendered.append(f"{page_number}{'-' * 48}")
            rendered.append(chunk)
        return "\n\n".join(rendered)

    @staticmethod
    def _join_page_html(chunks: list[str], page_payloads: list[dict[str, Any]], *, paginate: bool) -> str:
        pairs = [
            (page_payloads[index] if index < len(page_payloads) else {"page_number": index + 1}, chunk)
            for index, chunk in enumerate(chunks)
            if chunk.strip()
        ]
        if not paginate:
            return "".join(chunk for _, chunk in pairs)
        rendered: list[str] = []
        for index, (page_payload, chunk) in enumerate(pairs):
            if index:
                page_number = html.escape(str(page_payload.get("page_number")))
                rendered.append(f"<hr data-page-break='{page_number}'/>")
            rendered.append(chunk)
        return "".join(rendered)

    @staticmethod
    def _runtime_metadata(
        *,
        request_id: str,
        request_kind: str,
        file_name: str,
        file_size_bytes: int,
        started_at: str,
        started_perf: float,
        page_count: int | None,
        status: str,
        error_code: str | None = None,
    ) -> dict[str, Any]:
        finished_at = utcnow_iso()
        payload = {
            "request_id": request_id,
            "request_kind": request_kind,
            "file_name": file_name,
            "file_size_bytes": file_size_bytes,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": round((time.perf_counter() - started_perf) * 1000, 2),
            "page_count": page_count,
            "status": status,
        }
        if error_code:
            payload["error_code"] = error_code
        return payload

    @staticmethod
    def _progress_metadata(*, processed_pages: int, total_pages: int, status: str) -> dict[str, Any]:
        safe_total = max(int(total_pages or 0), 0)
        safe_processed = max(int(processed_pages or 0), 0)
        percent = 100.0 if safe_total == 0 and status == "complete" else 0.0
        if safe_total > 0:
            percent = round(min(safe_processed, safe_total) / safe_total * 100, 1)
        return {
            "status": status,
            "processed_pages": safe_processed,
            "total_pages": safe_total,
            "percent": percent,
        }

    @staticmethod
    def _classify_error(exc: Exception) -> str:
        message = str(exc).lower()
        if "pdf" in message:
            return "invalid_pdf"
        if "page_range" in message or "max_pages" in message or "page_number" in message:
            return "invalid_request"
        if "not found" in message:
            return "input_not_found"
        return "processing_failed"

    def _request_dir(self, request_id: str) -> Path:
        return self.requests_dir / request_id

    def _request_record_path(self, request_id: str) -> Path:
        return self._request_dir(request_id) / "record.json"

    def _workflow_path(self, workflow_id: int) -> Path:
        return self.workflows_dir / f"{workflow_id}.json"

    def _execution_path(self, execution_id: int) -> Path:
        return self.executions_dir / f"{execution_id}.json"

    def _write_request_record(self, request_id: str, payload: dict[str, Any]) -> None:
        request_dir = self._request_dir(request_id)
        request_dir.mkdir(parents=True, exist_ok=True)
        write_json(self._request_record_path(request_id), payload)

    def _update_request_record(self, request_id: str, **changes: Any) -> None:
        with self._lock:
            record = self.get_request_record(request_id)
            record.update(changes)
            record["updated_at"] = utcnow_iso()
            self._write_request_record(request_id, record)

    def _update_execution(self, execution_id: int, **changes: Any) -> None:
        with self._lock:
            execution = self.get_execution(execution_id)
            execution.update(changes)
            execution["updated"] = utcnow_iso()
            write_json(self._execution_path(execution_id), execution)

    def _update_execution_step(self, execution_id: int, step_name: str, **changes: Any) -> None:
        with self._lock:
            execution = self.get_execution(execution_id)
            steps = execution.setdefault("steps", {})
            step = steps.setdefault(step_name, {"status": "PENDING"})
            step.update(changes)
            execution["updated"] = utcnow_iso()
            if "outputs" in changes:
                execution.setdefault("step_outputs", {})[step_name] = changes["outputs"]
            write_json(self._execution_path(execution_id), execution)

    @staticmethod
    def _next_numeric_id(directory: Path) -> int:
        values = [int(path.stem) for path in directory.glob("*.json") if path.stem.isdigit()]
        return max(values, default=0) + 1
