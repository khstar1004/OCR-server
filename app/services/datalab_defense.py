from __future__ import annotations

import base64
import difflib
import hashlib
import json
import mimetypes
import re
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from app.core.config import Settings
from app.services.artifacts import load_json, write_json
from app.services.datalab_compat import (
    DatalabCompatService,
    normalize_marker_mode,
    normalize_marker_output_formats,
    is_pdf_filename,
    safe_filename,
    utcnow_iso,
)


def _guess_content_type(file_name: str, default: str = "application/octet-stream") -> str:
    guessed, _ = mimetypes.guess_type(file_name)
    return guessed or default


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _normalize_json_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _slug_field_name(name: str) -> str:
    return re.sub(r"[^0-9a-z_]+", "_", name.strip().lower()).strip("_") or "field"


class DefenseDataService:
    def __init__(self, settings: Settings, compat: DatalabCompatService):
        self.settings = settings
        self.compat = compat
        self.root_dir = compat.root_dir
        self.uploads_dir = self.root_dir / "uploads"
        self.files_dir = self.root_dir / "files"
        self.documents_dir = self.root_dir / "documents"
        self.collections_dir = self.root_dir / "collections"
        self.templates_dir = self.root_dir / "templates"
        self.eval_rubrics_dir = self.root_dir / "eval_rubrics"
        self.batch_runs_dir = self.root_dir / "batch_runs"
        self._lock = threading.RLock()
        self.ensure_directories()

    def ensure_directories(self) -> None:
        for directory in (
            self.uploads_dir,
            self.files_dir,
            self.documents_dir,
            self.collections_dir,
            self.templates_dir,
            self.eval_rubrics_dir,
            self.batch_runs_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def create_upload_slot(self, file_name: str, content_type: str | None = None) -> dict[str, Any]:
        resolved_name = safe_filename(file_name, "upload.bin")
        upload_id = f"upload_{uuid4().hex}"
        slot = {
            "upload_id": upload_id,
            "file_name": resolved_name,
            "content_type": content_type or _guess_content_type(resolved_name),
            "created_at": utcnow_iso(),
            "uploaded_at": None,
            "status": "pending",
            "size_bytes": 0,
        }
        write_json(self._upload_record_path(upload_id), slot)
        return slot

    def put_upload_payload(self, upload_id: str, payload: bytes, content_type: str | None = None) -> dict[str, Any]:
        slot = self.get_upload_slot(upload_id)
        upload_dir = self._upload_dir(upload_id)
        upload_dir.mkdir(parents=True, exist_ok=True)
        payload_path = upload_dir / "payload.bin"
        payload_path.write_bytes(payload)
        slot["content_type"] = content_type or slot.get("content_type") or _guess_content_type(slot["file_name"])
        slot["uploaded_at"] = utcnow_iso()
        slot["status"] = "uploaded"
        slot["size_bytes"] = len(payload)
        write_json(self._upload_record_path(upload_id), slot)
        return slot

    def get_upload_slot(self, upload_id: str) -> dict[str, Any]:
        path = self._upload_record_path(upload_id)
        if not path.exists():
            raise KeyError(f"upload slot not found: {upload_id}")
        return load_json(path)

    def confirm_upload(self, upload_id: str) -> dict[str, Any]:
        slot = self.get_upload_slot(upload_id)
        payload_path = self._upload_dir(upload_id) / "payload.bin"
        if not payload_path.exists():
            raise ValueError("upload payload not found")
        file_record = self.create_file_from_bytes(
            file_name=str(slot["file_name"]),
            payload=payload_path.read_bytes(),
            content_type=str(slot.get("content_type") or _guess_content_type(str(slot["file_name"]))),
            source={"upload_id": upload_id},
        )
        slot["status"] = "confirmed"
        slot["confirmed_at"] = utcnow_iso()
        slot["file_id"] = file_record["file_id"]
        write_json(self._upload_record_path(upload_id), slot)
        return file_record

    def create_file_from_bytes(
        self,
        *,
        file_name: str,
        payload: bytes,
        content_type: str | None = None,
        source: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_name = safe_filename(file_name, "document.bin")
        file_id = f"file_{uuid4().hex}"
        file_dir = self._file_dir(file_id)
        file_dir.mkdir(parents=True, exist_ok=True)
        suffix = Path(resolved_name).suffix or ".bin"
        content_path = file_dir / f"content{suffix}"
        content_path.write_bytes(payload)
        record = {
            "file_id": file_id,
            "file_name": resolved_name,
            "content_type": content_type or _guess_content_type(resolved_name),
            "size_bytes": len(payload),
            "sha256": _sha256_bytes(payload),
            "file_kind": self._detect_file_kind(resolved_name),
            "created_at": utcnow_iso(),
            "source": source or {},
            "storage_path": str(content_path),
            "download_url": f"/api/v1/files/{file_id}/download",
            "metadata_url": f"/api/v1/files/{file_id}/metadata",
        }
        write_json(self._file_record_path(file_id), record)
        return record

    def get_file(self, file_id: str) -> dict[str, Any]:
        path = self._file_record_path(file_id)
        if not path.exists():
            raise KeyError(f"file not found: {file_id}")
        return load_json(path)

    def list_files(self) -> dict[str, Any]:
        items = [load_json(path) for path in sorted(self.files_dir.glob("*/meta.json"), key=lambda item: item.stat().st_mtime, reverse=True)]
        return {"files": items, "count": len(items)}

    def file_download_info(self, file_id: str) -> dict[str, Any]:
        record = self.get_file(file_id)
        return {
            "file_id": file_id,
            "file_name": record["file_name"],
            "download_url": record["download_url"],
            "content_type": record["content_type"],
            "size_bytes": record["size_bytes"],
        }

    def delete_file(self, file_id: str) -> dict[str, Any]:
        record = self.get_file(file_id)
        shutil.rmtree(self._file_dir(file_id), ignore_errors=True)
        self._remove_file_from_all_collections(file_id)
        return {"success": True, "file_id": file_id, "file_name": record["file_name"]}

    def get_file_payload_path(self, file_id: str) -> Path:
        record = self.get_file(file_id)
        return Path(record["storage_path"])

    def _upload_dir(self, upload_id: str) -> Path:
        return self.uploads_dir / upload_id

    def _upload_record_path(self, upload_id: str) -> Path:
        return self._upload_dir(upload_id) / "slot.json"

    def _file_dir(self, file_id: str) -> Path:
        return self.files_dir / file_id

    def _file_record_path(self, file_id: str) -> Path:
        return self._file_dir(file_id) / "meta.json"

    @staticmethod
    def _detect_file_kind(file_name: str) -> str:
        suffix = Path(file_name).suffix.lower()
        if suffix == ".pdf":
            return "pdf"
        if suffix in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            return "image"
        if suffix in {".json"}:
            return "json"
        if suffix in {".txt", ".md"}:
            return "text"
        return "binary"

    def _remove_file_from_all_collections(self, file_id: str) -> None:
        for path in self.collections_dir.glob("*.json"):
            collection = load_json(path)
            file_ids = [value for value in collection.get("file_ids", []) if value != file_id]
            if file_ids != collection.get("file_ids", []):
                collection["file_ids"] = file_ids
                collection["updated_at"] = utcnow_iso()
                write_json(path, collection)

    def list_collections(self) -> dict[str, Any]:
        items = [load_json(path) for path in sorted(self.collections_dir.glob("*.json"), key=lambda item: item.stem)]
        return {"collections": items, "count": len(items)}

    def create_collection(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        collection_id = self._next_numeric_id(self.collections_dir)
        record = {
            "collection_id": collection_id,
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "file_ids": [str(item) for item in payload.get("file_ids", []) or []],
            "created_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        }
        write_json(self._collection_path(collection_id), record)
        return record

    def get_collection(self, collection_id: int) -> dict[str, Any]:
        path = self._collection_path(collection_id)
        if not path.exists():
            raise KeyError(f"collection not found: {collection_id}")
        return load_json(path)

    def update_collection(self, collection_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.get_collection(collection_id)
        if "name" in payload:
            name = str(payload.get("name") or "").strip()
            if not name:
                raise ValueError("name cannot be empty")
            record["name"] = name
        if "description" in payload:
            record["description"] = str(payload.get("description") or "").strip()
        if "file_ids" in payload:
            record["file_ids"] = [str(item) for item in payload.get("file_ids", []) or []]
        record["updated_at"] = utcnow_iso()
        write_json(self._collection_path(collection_id), record)
        return record

    def delete_collection(self, collection_id: int) -> dict[str, Any]:
        self.get_collection(collection_id)
        self._collection_path(collection_id).unlink()
        return {"success": True, "collection_id": collection_id}

    def add_files_to_collection(self, collection_id: int, file_ids: list[str]) -> dict[str, Any]:
        record = self.get_collection(collection_id)
        merged = list(dict.fromkeys([*record.get("file_ids", []), *[str(item) for item in file_ids]]))
        record["file_ids"] = merged
        record["updated_at"] = utcnow_iso()
        write_json(self._collection_path(collection_id), record)
        return record

    def remove_file_from_collection(self, collection_id: int, file_id: str) -> dict[str, Any]:
        record = self.get_collection(collection_id)
        record["file_ids"] = [value for value in record.get("file_ids", []) if value != file_id]
        record["updated_at"] = utcnow_iso()
        write_json(self._collection_path(collection_id), record)
        return record

    def list_templates(self) -> dict[str, Any]:
        items = [load_json(path) for path in sorted(self.templates_dir.glob("*.json"), key=lambda item: item.stem)]
        return {"templates": items, "count": len(items)}

    def promote_to_template(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        template_id = self._next_numeric_id(self.templates_dir)
        content = payload.get("content")
        source_request_id = payload.get("source_request_id")
        if content is None and source_request_id:
            content = self.compat.get_request_result(str(source_request_id))
        if content is None:
            raise ValueError("content or source_request_id is required")
        record = {
            "template_id": template_id,
            "name": name,
            "kind": str(payload.get("kind") or "generic"),
            "description": str(payload.get("description") or "").strip(),
            "content": content,
            "examples": payload.get("examples") or [],
            "created_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        }
        write_json(self._template_path(template_id), record)
        return record

    def get_template(self, template_id: int) -> dict[str, Any]:
        path = self._template_path(template_id)
        if not path.exists():
            raise KeyError(f"template not found: {template_id}")
        return load_json(path)

    def update_template(self, template_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.get_template(template_id)
        for key in ("name", "kind", "description", "content"):
            if key in payload:
                record[key] = payload[key]
        record["updated_at"] = utcnow_iso()
        write_json(self._template_path(template_id), record)
        return record

    def remove_template(self, template_id: int) -> dict[str, Any]:
        self.get_template(template_id)
        self._template_path(template_id).unlink()
        return {"success": True, "template_id": template_id}

    def clone_template(self, template_id: int, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        source = self.get_template(template_id)
        cloned = dict(source)
        new_id = self._next_numeric_id(self.templates_dir)
        cloned["template_id"] = new_id
        cloned["name"] = str((payload or {}).get("name") or f"{source['name']} Clone").strip()
        cloned["created_at"] = utcnow_iso()
        cloned["updated_at"] = utcnow_iso()
        write_json(self._template_path(new_id), cloned)
        return cloned

    def add_template_examples(self, template_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.get_template(template_id)
        examples = list(record.get("examples", []))
        for item in payload.get("examples", []) or []:
            example = dict(item)
            example.setdefault("example_id", f"example_{uuid4().hex[:10]}")
            examples.append(example)
        for file_id in payload.get("file_ids", []) or []:
            examples.append({"example_id": f"example_{uuid4().hex[:10]}", "file_id": str(file_id)})
        record["examples"] = examples
        record["updated_at"] = utcnow_iso()
        write_json(self._template_path(template_id), record)
        return record

    def get_template_example(self, template_id: int, example_id: str) -> dict[str, Any]:
        record = self.get_template(template_id)
        for example in record.get("examples", []):
            if str(example.get("example_id")) == example_id:
                return example
        raise KeyError(f"template example not found: {example_id}")

    def remove_template_example(self, template_id: int, example_id: str) -> dict[str, Any]:
        record = self.get_template(template_id)
        examples = [item for item in record.get("examples", []) if str(item.get("example_id")) != example_id]
        record["examples"] = examples
        record["updated_at"] = utcnow_iso()
        write_json(self._template_path(template_id), record)
        return {"success": True, "template_id": template_id, "example_id": example_id}

    def download_template_example(self, template_id: int, example_id: str) -> dict[str, Any]:
        example = self.get_template_example(template_id, example_id)
        if example.get("file_id"):
            file_record = self.get_file(str(example["file_id"]))
            return {
                "example_id": example_id,
                "file_id": file_record["file_id"],
                "file_name": file_record["file_name"],
                "download_url": file_record["download_url"],
            }
        return {"example_id": example_id, "content": example}

    def template_example_thumbnail(self, template_id: int, example_id: str, thumb_width: int = 300) -> dict[str, Any]:
        example = self.get_template_example(template_id, example_id)
        file_id = example.get("file_id")
        if not file_id:
            raise ValueError("example has no file_id")
        image_b64 = self._thumbnail_for_file(str(file_id), thumb_width=thumb_width)
        return {"example_id": example_id, "thumbnail": image_b64}

    def list_eval_rubrics(self) -> dict[str, Any]:
        items = [load_json(path) for path in sorted(self.eval_rubrics_dir.glob("*.json"), key=lambda item: item.stem)]
        return {"eval_rubrics": items, "count": len(items)}

    def create_eval_rubric(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("name is required")
        rubric_id = self._next_numeric_id(self.eval_rubrics_dir)
        record = {
            "eval_rubric_id": rubric_id,
            "name": name,
            "description": str(payload.get("description") or "").strip(),
            "fields": payload.get("fields") or [],
            "weights": payload.get("weights") or {},
            "created_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
        }
        write_json(self._eval_rubric_path(rubric_id), record)
        return record

    def get_eval_rubric(self, rubric_id: int) -> dict[str, Any]:
        path = self._eval_rubric_path(rubric_id)
        if not path.exists():
            raise KeyError(f"eval rubric not found: {rubric_id}")
        return load_json(path)

    def update_eval_rubric(self, rubric_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        record = self.get_eval_rubric(rubric_id)
        for key in ("name", "description", "fields", "weights"):
            if key in payload:
                record[key] = payload[key]
        record["updated_at"] = utcnow_iso()
        write_json(self._eval_rubric_path(rubric_id), record)
        return record

    def delete_eval_rubric(self, rubric_id: int) -> dict[str, Any]:
        self.get_eval_rubric(rubric_id)
        self._eval_rubric_path(rubric_id).unlink()
        return {"success": True, "eval_rubric_id": rubric_id}

    def _collection_path(self, collection_id: int) -> Path:
        return self.collections_dir / f"{collection_id}.json"

    def _template_path(self, template_id: int) -> Path:
        return self.templates_dir / f"{template_id}.json"

    def _eval_rubric_path(self, rubric_id: int) -> Path:
        return self.eval_rubrics_dir / f"{rubric_id}.json"

    def create_document(self, file_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        file_record = self.get_file(file_id)
        document_id = f"doc_{uuid4().hex}"
        page_count = self._estimate_page_count(Path(file_record["storage_path"]), file_record["file_kind"])
        record = {
            "document_id": document_id,
            "file_id": file_record["file_id"],
            "file_name": file_record["file_name"],
            "file_kind": file_record["file_kind"],
            "page_count": page_count,
            "metadata": dict((payload or {}).get("metadata") or {}),
            "created_at": utcnow_iso(),
        }
        write_json(self._document_path(document_id), record)
        return record

    def get_document(self, document_id: str) -> dict[str, Any]:
        path = self._document_path(document_id)
        if not path.exists():
            raise KeyError(f"document not found: {document_id}")
        return load_json(path)

    def process_create_document(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            file_record = self._resolve_file_record(payload)
            document = self.create_document(file_record["file_id"], payload)
            self.compat._update_request_record(
                request_id,
                status="complete",
                result={
                    "status": "complete",
                    "success": True,
                    "error": None,
                    "document": document,
                    "versions": self.compat.versions(),
                },
            )
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_convert_document(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            file_record, document = self._resolve_file_and_document(payload)
            result = self._run_convert(
                file_record=file_record,
                document=document,
                output_format=str(payload.get("output_format") or "json"),
                max_pages=self._optional_int(payload.get("max_pages")),
                page_range=self._optional_text(payload.get("page_range")),
            )
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_segment_document(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            file_record, document = self._resolve_file_and_document(payload)
            result = self._run_segment(
                file_record=file_record,
                document=document,
                max_pages=self._optional_int(payload.get("max_pages")),
                page_range=self._optional_text(payload.get("page_range")),
            )
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_generate_extraction_schemas(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            schema = self._generate_schema(payload)
            result = {
                "status": "complete",
                "success": True,
                "error": None,
                "schema": schema,
                "versions": self.compat.versions(),
            }
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_extract_structured_data(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            file_record, document = self._resolve_file_and_document(payload)
            schema = self._resolve_schema(payload)
            segment_result = self._run_segment(
                file_record=file_record,
                document=document,
                max_pages=self._optional_int(payload.get("max_pages")),
                page_range=self._optional_text(payload.get("page_range")),
            )
            extracted = self._extract_structured_data(schema, segment_result)
            result = {
                "status": "complete",
                "success": True,
                "error": None,
                "document_id": document.get("document_id") if document else None,
                "file_id": file_record["file_id"],
                "schema": schema,
                "structured_data": extracted["values"],
                "field_results": extracted["field_results"],
                "segment": segment_result,
                "versions": self.compat.versions(),
            }
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_score_extraction_results(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            predicted = self._resolve_scoring_source(payload, "predicted")
            reference = self._resolve_scoring_source(payload, "reference")
            rubric = self._resolve_rubric(payload)
            result = self._score_structured_data(predicted, reference, rubric)
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_form_filling(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            result = self._run_form_filling(payload)
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def process_track_changes(self, request_id: str, payload: dict[str, Any]) -> None:
        try:
            result = self._run_track_changes(payload)
            self.compat._update_request_record(request_id, status="complete", result=result, error=None)
        except Exception as exc:
            self.compat._update_request_record(
                request_id,
                status="failed",
                error=str(exc),
                result={"status": "failed", "success": False, "error": str(exc), "versions": self.compat.versions()},
            )

    def _document_path(self, document_id: str) -> Path:
        return self.documents_dir / f"{document_id}.json"

    def list_batch_runs(self) -> dict[str, Any]:
        items = [load_json(path) for path in sorted(self.batch_runs_dir.glob("*.json"), key=lambda item: item.stem)]
        return {"batch_runs": items, "count": len(items)}

    def get_batch_run(self, batch_run_id: int) -> dict[str, Any]:
        path = self._batch_run_path(batch_run_id)
        if not path.exists():
            raise KeyError(f"batch run not found: {batch_run_id}")
        return load_json(path)

    def start_batch_run(self, payload: dict[str, Any]) -> dict[str, Any]:
        collection_id = int(payload.get("collection_id"))
        operation = str(payload.get("operation") or "").strip()
        if operation not in {"convert_document", "segment_document", "extract_structured_data"}:
            raise ValueError("operation must be one of: convert_document, segment_document, extract_structured_data")
        batch_run_id = self._next_numeric_id(self.batch_runs_dir)
        record = {
            "batch_run_id": batch_run_id,
            "collection_id": collection_id,
            "operation": operation,
            "status": "queued",
            "created_at": utcnow_iso(),
            "updated_at": utcnow_iso(),
            "params": payload.get("params") or {},
            "results": {},
        }
        write_json(self._batch_run_path(batch_run_id), record)
        return record

    def process_batch_run(self, batch_run_id: int) -> None:
        record = self.get_batch_run(batch_run_id)
        collection = self.get_collection(int(record["collection_id"]))
        params = dict(record.get("params") or {})
        results: dict[str, Any] = {}
        success_count = 0
        failure_count = 0
        record["status"] = "running"
        record["updated_at"] = utcnow_iso()
        write_json(self._batch_run_path(batch_run_id), record)

        for file_id in collection.get("file_ids", []):
            try:
                file_record = self.get_file(str(file_id))
                document = None
                max_pages = self._optional_int(params.get("max_pages"))
                page_range = self._optional_text(params.get("page_range"))
                if record["operation"] == "convert_document":
                    results[str(file_id)] = self._run_convert(
                        file_record=file_record,
                        document=document,
                        output_format=str(params.get("output_format") or "json"),
                        max_pages=max_pages,
                        page_range=page_range,
                    )
                elif record["operation"] == "segment_document":
                    results[str(file_id)] = self._run_segment(
                        file_record=file_record,
                        document=document,
                        max_pages=max_pages,
                        page_range=page_range,
                    )
                else:
                    schema = self._resolve_schema(params)
                    segment_result = self._run_segment(
                        file_record=file_record,
                        document=document,
                        max_pages=max_pages,
                        page_range=page_range,
                    )
                    extracted = self._extract_structured_data(schema, segment_result)
                    results[str(file_id)] = {
                        "status": "complete",
                        "success": True,
                        "file_id": file_record["file_id"],
                        "schema": schema,
                        "structured_data": extracted["values"],
                        "field_results": extracted["field_results"],
                        "versions": self.compat.versions(),
                    }
                success_count += 1
            except Exception as exc:
                failure_count += 1
                results[str(file_id)] = {"status": "failed", "success": False, "error": str(exc)}

        record["status"] = "completed_with_errors" if failure_count else "completed"
        record["updated_at"] = utcnow_iso()
        record["success_count"] = success_count
        record["failure_count"] = failure_count
        record["results"] = results
        write_json(self._batch_run_path(batch_run_id), record)

    def get_batch_run_results(self, batch_run_id: int) -> dict[str, Any]:
        record = self.get_batch_run(batch_run_id)
        return {
            "batch_run_id": batch_run_id,
            "status": record["status"],
            "results": record.get("results", {}),
            "success_count": record.get("success_count", 0),
            "failure_count": record.get("failure_count", 0),
        }

    def check_pipeline_access(self) -> dict[str, Any]:
        return {
            "access": True,
            "available_operations": [
                "ocr",
                "marker",
                "create_document",
                "convert_document",
                "segment_document",
                "extract_structured_data",
                "form_filling",
                "track_changes",
                "batch_runs",
            ],
            "versions": self.compat.versions(),
        }

    def list_custom_pipelines(self) -> dict[str, Any]:
        workflows = self.compat.list_workflows()
        return {
            "pipelines": [
                {
                    "pipeline_id": item["workflow_id"],
                    "name": item["name"],
                    "steps": item["steps"],
                    "created": item["created"],
                    "updated": item["updated"],
                }
                for item in workflows.get("workflows", [])
            ],
            "count": workflows.get("count", 0),
            "versions": self.compat.versions(),
        }

    def _batch_run_path(self, batch_run_id: int) -> Path:
        return self.batch_runs_dir / f"{batch_run_id}.json"

    def _thumbnail_for_file(self, file_id: str, thumb_width: int = 300) -> str:
        file_record = self.get_file(file_id)
        source_path = Path(file_record["storage_path"])
        kind = str(file_record["file_kind"])
        if kind == "pdf":
            with tempfile.TemporaryDirectory(prefix="template-thumb-") as temp_dir:
                pages = self.compat._process_document_pages(
                    request_id=f"thumb_{uuid4().hex}",
                    file_bytes=source_path.read_bytes(),
                    file_name=file_record["file_name"],
                    dpi=200,
                )[1]
                page_path = pages[0]
                return self._encode_thumbnail(Path(page_path), thumb_width)
        if kind != "image":
            raise ValueError("thumbnail generation is only supported for image/pdf files")
        return self._encode_thumbnail(source_path, thumb_width)

    @staticmethod
    def _encode_thumbnail(image_path: Path, thumb_width: int) -> str:
        with Image.open(image_path) as image:
            target_height = max(1, round((thumb_width / max(image.width, 1)) * image.height))
            rendered = image.convert("RGB").resize((thumb_width, target_height))
            buffer = BytesIO()
            rendered.save(buffer, format="JPEG", quality=85)
            return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _resolve_file_record(self, payload: dict[str, Any]) -> dict[str, Any]:
        file_id = payload.get("file_id")
        if file_id:
            return self.get_file(str(file_id))
        document_id = payload.get("document_id")
        if document_id:
            document = self.get_document(str(document_id))
            return self.get_file(str(document["file_id"]))
        raise ValueError("file_id or document_id is required")

    def _resolve_file_and_document(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        document_id = payload.get("document_id")
        if document_id:
            document = self.get_document(str(document_id))
            return self.get_file(str(document["file_id"])), document
        return self._resolve_file_record(payload), None

    def _estimate_page_count(self, path: Path, file_kind: str) -> int:
        if file_kind == "image":
            return 1
        if file_kind != "pdf":
            return 1
        try:
            import fitz

            with fitz.open(path) as document:
                return int(document.page_count or 1)
        except Exception:
            return 1

    @staticmethod
    def _next_numeric_id(directory: Path) -> int:
        values = [int(path.stem) for path in directory.glob("*.json") if path.stem.isdigit()]
        return max(values, default=0) + 1

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        return int(value)

    @staticmethod
    def _optional_text(value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    def _run_convert(
        self,
        *,
        file_record: dict[str, Any],
        document: dict[str, Any] | None,
        output_format: str = "json",
        max_pages: int | None = None,
        page_range: str | None = None,
    ) -> dict[str, Any]:
        temp_request_id = f"convert_{uuid4().hex}"
        pages, _ = self.compat._process_document_pages(
            request_id=temp_request_id,
            file_bytes=Path(file_record["storage_path"]).read_bytes(),
            file_name=file_record["file_name"],
            max_pages=max_pages,
            page_range=page_range,
        )
        result = self.compat._build_marker_result(
            temp_request_id,
            file_record["file_name"],
            pages,
            output_formats=normalize_marker_output_formats(output_format),
            mode=normalize_marker_mode(None),
            max_pages=max_pages,
            page_range=page_range,
            paginate=False,
            add_block_ids=False,
            include_markdown_in_chunks=False,
            skip_cache=False,
            extras=None,
            additional_config=None,
        )
        result["file_id"] = file_record["file_id"]
        result["document_id"] = document.get("document_id") if document else None
        return result

    def _run_segment(
        self,
        *,
        file_record: dict[str, Any],
        document: dict[str, Any] | None,
        max_pages: int | None = None,
        page_range: str | None = None,
    ) -> dict[str, Any]:
        temp_request_id = f"segment_{uuid4().hex}"
        pages, _ = self.compat._process_document_pages(
            request_id=temp_request_id,
            file_bytes=Path(file_record["storage_path"]).read_bytes(),
            file_name=file_record["file_name"],
            max_pages=max_pages,
            page_range=page_range,
        )
        payload_pages: list[dict[str, Any]] = []
        total_articles = 0
        for page in pages:
            articles, unassigned = self.compat.clusterer.cluster_page(page)
            payload_pages.append(
                {
                    "page_number": page.page_number,
                    "width": page.width,
                    "height": page.height,
                    "text": self.compat._page_text(page),
                    "blocks": [self.compat._serialize_block(block) for block in page.blocks],
                    "articles": [self.compat._serialize_article(article) for article in articles],
                    "unassigned": [self.compat._serialize_block(block) for block in unassigned],
                }
            )
            total_articles += len(articles)
        return {
            "status": "complete",
            "success": True,
            "error": None,
            "file_id": file_record["file_id"],
            "document_id": document.get("document_id") if document else None,
            "page_count": len(payload_pages),
            "total_articles": total_articles,
            "pages": payload_pages,
            "full_text": "\n\n".join(page["text"] for page in payload_pages if str(page.get("text") or "").strip()),
            "versions": self.compat.versions(),
        }

    def _generate_schema(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = str(payload.get("name") or "generated_schema").strip()
        examples = payload.get("examples")
        fields: list[dict[str, Any]] = []

        if isinstance(examples, list) and examples and all(isinstance(item, dict) for item in examples):
            field_names = sorted({str(key) for item in examples for key in item.keys()})
            for field_name in field_names:
                values = [item.get(field_name) for item in examples if field_name in item]
                fields.append(
                    {
                        "name": _slug_field_name(field_name),
                        "label": field_name,
                        "type": self._infer_field_type(values),
                        "required": all(field_name in item for item in examples),
                    }
                )
        else:
            field_names = payload.get("field_names") or payload.get("fields") or []
            if isinstance(field_names, list) and field_names:
                for value in field_names:
                    if isinstance(value, dict):
                        fields.append(
                            {
                                "name": _slug_field_name(str(value.get("name") or value.get("label") or "field")),
                                "label": str(value.get("label") or value.get("name") or "field"),
                                "type": str(value.get("type") or "string"),
                                "required": bool(value.get("required", False)),
                                "pattern": value.get("pattern"),
                            }
                        )
                    else:
                        fields.append({"name": _slug_field_name(str(value)), "label": str(value), "type": "string", "required": False})

        if not fields:
            sample_text = str(payload.get("sample_text") or payload.get("example_text") or "").strip()
            if re.search(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", sample_text):
                fields.append({"name": "document_date", "label": "문서일자", "type": "date", "required": False})
            if re.search(r"(사단|여단|대대|중대|사령부|본부|부대)", sample_text):
                fields.append({"name": "unit_name", "label": "부대명", "type": "string", "required": False})
            if re.search(r"(문서번호|보고번호|ID)", sample_text, re.IGNORECASE):
                fields.append({"name": "document_id", "label": "문서번호", "type": "string", "required": False})
            fields.append({"name": "title", "label": "제목", "type": "string", "required": False})
            fields.append({"name": "summary", "label": "요약", "type": "string", "required": False})

        return {"name": name, "fields": fields, "generated_at": utcnow_iso()}

    def _resolve_schema(self, payload: dict[str, Any]) -> dict[str, Any]:
        if isinstance(payload.get("schema"), dict):
            return dict(payload["schema"])
        if payload.get("template_id"):
            template = self.get_template(int(payload["template_id"]))
            content = template.get("content")
            if isinstance(content, dict) and isinstance(content.get("schema"), dict):
                return dict(content["schema"])
            if isinstance(content, dict) and content.get("fields"):
                return dict(content)
            raise ValueError("template content does not contain a schema")
        if payload.get("schema_request_id"):
            result = self.compat.get_request_result(str(payload["schema_request_id"]))
            schema = result.get("schema")
            if isinstance(schema, dict):
                return schema
        generated = self._generate_schema(payload)
        if generated.get("fields"):
            return generated
        raise ValueError("schema could not be resolved")

    def _extract_structured_data(self, schema: dict[str, Any], segment_result: dict[str, Any]) -> dict[str, Any]:
        full_text = str(segment_result.get("full_text") or "")
        values: dict[str, Any] = {}
        field_results: list[dict[str, Any]] = []
        for field in schema.get("fields", []) or []:
            value, source, confidence = self._extract_field_value(field, segment_result, full_text)
            field_name = str(field.get("name") or field.get("label") or "field")
            values[field_name] = value
            field_results.append(
                {
                    "name": field_name,
                    "label": str(field.get("label") or field_name),
                    "type": str(field.get("type") or "string"),
                    "value": value,
                    "source": source,
                    "confidence": round(confidence, 3),
                }
            )
        return {"values": values, "field_results": field_results}

    def _extract_field_value(
        self,
        field: dict[str, Any],
        segment_result: dict[str, Any],
        full_text: str,
    ) -> tuple[Any, str, float]:
        pages = segment_result.get("pages", []) or []
        name = _slug_field_name(str(field.get("name") or field.get("label") or "field"))
        field_type = str(field.get("type") or "string").lower()
        label = str(field.get("label") or "").strip()
        pattern = field.get("pattern")

        if isinstance(pattern, str) and pattern.strip():
            match = re.search(pattern, full_text, re.IGNORECASE | re.MULTILINE)
            if match:
                raw = next((group for group in match.groups() if group), match.group(0))
                return self._cast_field_value(raw.strip(), field_type), "pattern", 0.96

        if label:
            label_match = re.search(rf"{re.escape(label)}\s*[:：]?\s*(.+)", full_text, re.IGNORECASE)
            if label_match:
                raw = label_match.group(1).splitlines()[0].strip()
                return self._cast_field_value(raw, field_type), "label", 0.92

        if "title" in name:
            for page in pages:
                articles = page.get("articles", []) or []
                if articles and articles[0].get("title"):
                    return articles[0]["title"], "article_title", 0.9
                for block in page.get("blocks", []) or []:
                    if block.get("label") == "title" and str(block.get("text") or "").strip():
                        return str(block["text"]).strip(), "title_block", 0.88

        if "date" in name or field_type == "date":
            date_match = re.search(r"\b(\d{4}[./-]\d{1,2}[./-]\d{1,2})\b", full_text)
            if date_match:
                return date_match.group(1), "date_regex", 0.87

        if any(token in name for token in ("document_id", "report_id", "doc_id", "number")):
            id_match = re.search(r"(?:문서번호|보고번호|ID)\s*[:：]?\s*([A-Za-z0-9._/-]+)", full_text, re.IGNORECASE)
            if id_match:
                return id_match.group(1), "id_regex", 0.88

        if any(token in name for token in ("unit", "budae", "unit_name")):
            unit_match = re.search(r"([가-힣A-Za-z0-9]+(?:사단|여단|대대|중대|사령부|본부|부대))", full_text)
            if unit_match:
                return unit_match.group(1), "unit_regex", 0.82

        if any(token in name for token in ("phone", "contact", "tel")):
            phone_match = re.search(r"(\d{2,3}-\d{3,4}-\d{4})", full_text)
            if phone_match:
                return phone_match.group(1), "phone_regex", 0.9

        if any(token in name for token in ("author", "writer")):
            author_match = re.search(r"(?:작성자|기안자|담당자)\s*[:：]?\s*([^\n]+)", full_text)
            if author_match:
                return author_match.group(1).strip(), "author_regex", 0.84

        if any(token in name for token in ("summary", "body", "content")):
            articles = [article for page in pages for article in page.get("articles", []) or []]
            if articles and str(articles[0].get("body_text") or "").strip():
                return str(articles[0]["body_text"]).strip(), "article_body", 0.74
            return full_text[:500].strip(), "full_text", 0.62

        if field_type in {"number", "integer"}:
            number_match = re.search(r"(-?\d+(?:\.\d+)?)", full_text)
            if number_match:
                return self._cast_field_value(number_match.group(1), field_type), "number_regex", 0.76

        if field_type == "boolean":
            bool_match = re.search(r"\b(yes|no|true|false|예|아니오)\b", full_text, re.IGNORECASE)
            if bool_match:
                return self._cast_field_value(bool_match.group(1), field_type), "boolean_regex", 0.78

        lines = [line.strip() for line in full_text.splitlines() if line.strip()]
        if lines:
            return self._cast_field_value(lines[0], field_type), "first_line", 0.35
        return None, "none", 0.0

    @staticmethod
    def _cast_field_value(value: Any, field_type: str) -> Any:
        if value is None:
            return None
        text = str(value).strip()
        if field_type == "integer":
            try:
                return int(float(text))
            except ValueError:
                return None
        if field_type == "number":
            try:
                return float(text)
            except ValueError:
                return None
        if field_type == "boolean":
            return text.lower() in {"1", "true", "yes", "예"}
        return text

    @staticmethod
    def _infer_field_type(values: list[Any]) -> str:
        filtered = [value for value in values if value is not None]
        if filtered and all(isinstance(value, bool) for value in filtered):
            return "boolean"
        if filtered and all(isinstance(value, int) and not isinstance(value, bool) for value in filtered):
            return "integer"
        if filtered and all(isinstance(value, (int, float)) and not isinstance(value, bool) for value in filtered):
            return "number"
        if filtered and all(isinstance(value, str) and re.fullmatch(r"\d{4}[./-]\d{1,2}[./-]\d{1,2}", value.strip()) for value in filtered):
            return "date"
        return "string"

    def _resolve_scoring_source(self, payload: dict[str, Any], prefix: str) -> dict[str, Any]:
        candidate = payload.get(prefix)
        if isinstance(candidate, dict):
            return candidate
        request_id = payload.get(f"{prefix}_request_id")
        if request_id:
            result = self.compat.get_request_result(str(request_id))
            data = result.get("structured_data")
            if isinstance(data, dict):
                return data
        raise ValueError(f"{prefix} data is required")

    def _resolve_rubric(self, payload: dict[str, Any]) -> dict[str, Any]:
        rubric = payload.get("rubric")
        if isinstance(rubric, dict):
            return rubric
        rubric_id = payload.get("rubric_id")
        if rubric_id:
            return self.get_eval_rubric(int(rubric_id))
        return {"fields": [], "weights": {}}

    def _score_structured_data(self, predicted: dict[str, Any], reference: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
        configured_fields = rubric.get("fields") or []
        if configured_fields:
            field_names = [str(item.get("name") or item.get("label") or item) for item in configured_fields]
        else:
            field_names = sorted({*predicted.keys(), *reference.keys()})
        weights = {str(key): float(value) for key, value in (rubric.get("weights") or {}).items()}
        details: list[dict[str, Any]] = []
        total_weight = 0.0
        total_score = 0.0
        for field_name in field_names:
            predicted_value = predicted.get(field_name)
            reference_value = reference.get(field_name)
            score = self._field_similarity(predicted_value, reference_value)
            weight = weights.get(field_name, 1.0)
            total_weight += weight
            total_score += score * weight
            details.append(
                {
                    "field": field_name,
                    "predicted": predicted_value,
                    "reference": reference_value,
                    "score": round(score, 4),
                    "weight": weight,
                }
            )
        overall = total_score / total_weight if total_weight > 0 else 0.0
        return {
            "status": "complete",
            "success": True,
            "error": None,
            "overall_score": round(overall, 4),
            "field_scores": details,
            "rubric": rubric,
            "versions": self.compat.versions(),
        }

    @staticmethod
    def _field_similarity(left: Any, right: Any) -> float:
        if left is None and right is None:
            return 1.0
        if left is None or right is None:
            return 0.0
        if isinstance(left, (int, float)) and isinstance(right, (int, float)):
            return 1.0 if float(left) == float(right) else 0.0
        left_text = str(left).strip()
        right_text = str(right).strip()
        if left_text == right_text:
            return 1.0
        return difflib.SequenceMatcher(a=left_text.lower(), b=right_text.lower()).ratio()

    def _run_form_filling(self, payload: dict[str, Any]) -> dict[str, Any]:
        template = payload.get("template")
        if template is None and payload.get("template_id"):
            template_record = self.get_template(int(payload["template_id"]))
            template = template_record.get("content")
        if template is None:
            template = payload.get("template_text")
        if template is None:
            raise ValueError("template or template_id is required")

        values = payload.get("values")
        if not isinstance(values, dict) and payload.get("extract_request_id"):
            result = self.compat.get_request_result(str(payload["extract_request_id"]))
            values = result.get("structured_data")
        if not isinstance(values, dict):
            raise ValueError("values or extract_request_id is required")

        missing: set[str] = set()
        filled = self._fill_placeholders(template, values, missing)
        return {
            "status": "complete",
            "success": True,
            "error": None,
            "filled_output": filled,
            "missing_fields": sorted(missing),
            "versions": self.compat.versions(),
        }

    def _fill_placeholders(self, node: Any, values: dict[str, Any], missing: set[str]) -> Any:
        if isinstance(node, str):
            def replace(match: re.Match[str]) -> str:
                key = _slug_field_name(match.group(1))
                for candidate_key, candidate_value in values.items():
                    if _slug_field_name(str(candidate_key)) == key:
                        return str(candidate_value)
                missing.add(key)
                return match.group(0)

            return re.sub(r"{{\s*([^{}]+?)\s*}}", replace, node)
        if isinstance(node, list):
            return [self._fill_placeholders(item, values, missing) for item in node]
        if isinstance(node, dict):
            return {key: self._fill_placeholders(value, values, missing) for key, value in node.items()}
        return node

    def _run_track_changes(self, payload: dict[str, Any]) -> dict[str, Any]:
        before = payload.get("before")
        after = payload.get("after")
        if before is None and payload.get("before_request_id"):
            before = self.compat.get_request_result(str(payload["before_request_id"]))
        if after is None and payload.get("after_request_id"):
            after = self.compat.get_request_result(str(payload["after_request_id"]))
        if before is None or after is None:
            raise ValueError("before and after values are required")

        before_text = _normalize_json_text(before)
        after_text = _normalize_json_text(after)
        diff_lines = list(
            difflib.unified_diff(
                before_text.splitlines(),
                after_text.splitlines(),
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
        changed_fields: list[str] = []
        if isinstance(before, dict) and isinstance(after, dict):
            changed_fields = sorted({*before.keys(), *after.keys()})
            changed_fields = [field for field in changed_fields if before.get(field) != after.get(field)]
        return {
            "status": "complete",
            "success": True,
            "error": None,
            "similarity": round(difflib.SequenceMatcher(a=before_text, b=after_text).ratio(), 4),
            "diff": "\n".join(diff_lines),
            "changed_fields": changed_fields,
            "versions": self.compat.versions(),
        }
