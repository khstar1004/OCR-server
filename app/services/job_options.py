from __future__ import annotations

from typing import Any

from app.services.datalab_compat import normalize_marker_mode, normalize_marker_output_formats, parse_page_range


DEFAULT_JOB_OCR_OPTIONS: dict[str, Any] = {
    "ocr_mode": "balanced",
    "page_range": None,
    "max_pages": None,
    "output_format": "markdown",
    "paginate": False,
    "add_block_ids": False,
    "include_markdown_in_chunks": False,
    "skip_cache": False,
}


def normalize_job_ocr_options(value: Any) -> dict[str, Any]:
    source = _to_mapping(value)
    max_pages = _optional_int(source.get("max_pages"))
    if max_pages is not None and max_pages <= 0:
        raise ValueError("max_pages must be a positive integer")

    page_range = _optional_text(source.get("page_range"))
    if page_range:
        parse_page_range(page_range)

    output_formats = normalize_marker_output_formats(_optional_text(source.get("output_format")) or "markdown")
    return {
        "ocr_mode": normalize_marker_mode(_optional_text(source.get("ocr_mode")) or _optional_text(source.get("mode"))),
        "page_range": page_range,
        "max_pages": max_pages,
        "output_format": ",".join(output_formats),
        "output_formats": output_formats,
        "paginate": _as_bool(source.get("paginate")),
        "add_block_ids": _as_bool(source.get("add_block_ids")),
        "include_markdown_in_chunks": _as_bool(source.get("include_markdown_in_chunks")),
        "skip_cache": _as_bool(source.get("skip_cache")),
    }


def select_items_by_job_page_options(items: list[Any], options: dict[str, Any]) -> list[Any]:
    selected = list(items)
    indices = parse_page_range(_optional_text(options.get("page_range")))
    if indices is not None:
        selected = [item for index, item in enumerate(selected) if index in indices]
    max_pages = _optional_int(options.get("max_pages"))
    if max_pages is not None:
        selected = selected[:max_pages]
    return selected


def _to_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return dict(DEFAULT_JOB_OCR_OPTIONS)
    if isinstance(value, dict):
        return {**DEFAULT_JOB_OCR_OPTIONS, **value}
    return {
        **DEFAULT_JOB_OCR_OPTIONS,
        "ocr_mode": getattr(value, "ocr_mode", None),
        "page_range": getattr(value, "page_range", None),
        "max_pages": getattr(value, "max_pages", None),
        "output_format": getattr(value, "output_format", None),
        "paginate": getattr(value, "paginate", False),
        "add_block_ids": getattr(value, "add_block_ids", False),
        "include_markdown_in_chunks": getattr(value, "include_markdown_in_chunks", False),
        "skip_cache": getattr(value, "skip_cache", False),
    }


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
