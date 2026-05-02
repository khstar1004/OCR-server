from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, TypeVar

from PIL import Image

from app.domain.types import normalize_block_label_value
from app.ocr.types import OCRDocumentResult, OCRPageArtifacts, PageImageArtifact, RenderedPdf
from app.services.artifacts import (
    JobArtifactLayout,
    load_json,
    make_json_safe,
    write_json,
    write_text,
)
from app.utils.geometry import bbox_from_any, normalize_bboxes_to_page

T = TypeVar("T")


class ChandraRunner(Protocol):
    def __call__(self, pages: Sequence[PageImageArtifact]) -> Sequence[Any]:
        ...


@dataclass(frozen=True, slots=True)
class ChandraHFConfig:
    model_id: str = "datalab-to/chandra-ocr-2"
    prompt_type: str = "ocr_layout"
    method: str = "hf"
    device_map: str = "auto"
    dtype_name: str = "bfloat16"
    batch_size: int = 1
    vllm_api_base: str | None = None
    vllm_max_retries: int | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _chunked(items: Sequence[T], chunk_size: int) -> list[Sequence[T]]:
    if chunk_size <= 0:
        raise ValueError("Batch size must be greater than zero.")
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]


def _best_effort_markdown_to_html(markdown: str) -> str:
    lines = [line.rstrip() for line in markdown.splitlines()]
    fragments: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            level = min(len(stripped) - len(stripped.lstrip("#")), 6)
            content = html.escape(stripped[level:].strip())
            fragments.append(f"<h{level}>{content}</h{level}>")
        else:
            fragments.append(f"<p>{html.escape(stripped)}</p>")
    if not fragments:
        fragments.append("<p></p>")
    return "\n".join(fragments)


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "__dict__"):
        return {
            key: attr
            for key, attr in vars(value).items()
            if not key.startswith("_") and not callable(attr)
        }
    return {}


def _first_present(container: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in container and container[key] not in (None, ""):
            return container[key]
    return None


def _iter_structured_text_candidates(value: Any, keys: tuple[str, ...] = ("markdown", "html", "text", "raw")) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(text: str) -> None:
        stripped = text.strip()
        if stripped and stripped not in seen:
            seen.add(stripped)
            candidates.append(stripped)

    def visit(node: Any) -> None:
        if isinstance(node, Mapping):
            for key in keys:
                candidate = node.get(key)
                if isinstance(candidate, str):
                    add(candidate)
            for child in node.values():
                if isinstance(child, (Mapping, list, tuple)):
                    visit(child)
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                visit(child)
            return
        if isinstance(node, str):
            add(node)

    visit(value)
    return candidates


def _ensure_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {"raw": value}
        return _ensure_mapping(decoded)
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, list):
        return {"items": make_json_safe(value)}
    return _as_mapping(value)


def _extract_blocks(payload: Mapping[str, Any], json_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    candidate = _first_present(
        payload,
        "blocks",
        "layout",
        "elements",
        "regions",
        "chunks",
    )
    if candidate is None:
        candidate = _first_present(json_payload, "blocks", "layout", "elements", "regions", "chunks")
    if isinstance(candidate, list) and candidate:
        return [make_json_safe(item) for item in candidate]

    seen: set[tuple[str, tuple[int, ...], str]] = set()
    blocks: list[dict[str, Any]] = []
    for source in (payload, json_payload):
        if not isinstance(source, Mapping):
            continue
        for text in _iter_structured_text_candidates(source):
            for block in _extract_blocks_from_structured_text(text):
                signature = (
                    str(block.get("type") or block.get("label") or ""),
                    tuple(block.get("bbox") or ()),
                    str(block.get("content") or block.get("text") or ""),
                )
                if signature in seen:
                    continue
                seen.add(signature)
                blocks.append(block)
        if blocks:
            return blocks
    return blocks


def _maybe_scale_block_bboxes(blocks: list[dict[str, Any]], page: PageImageArtifact) -> list[dict[str, Any]]:
    block_boxes = [bbox for bbox in (bbox_from_any(block.get("bbox")) for block in blocks) if bbox is not None]
    if not block_boxes:
        return blocks

    normalized_boxes = normalize_bboxes_to_page(block_boxes, page.width, page.height)
    if normalized_boxes == block_boxes:
        return blocks

    scaled_blocks: list[dict[str, Any]] = []
    box_iter = iter(normalized_boxes)
    for block in blocks:
        scaled = dict(block)
        bbox = bbox_from_any(block.get("bbox"))
        if bbox is not None:
            scaled["bbox"] = next(box_iter)
        scaled_blocks.append(scaled)
    return scaled_blocks


_STRUCTURED_BLOCK_PATTERN = re.compile(r"<div\b(?P<attrs>[^>]*)>(?P<body>.*?)</div>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_BR_TAG_PATTERN = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_TABLE_ROW_PATTERN = re.compile(r"<tr\b[^>]*>(?P<body>.*?)</tr>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL_PATTERN = re.compile(r"<(?:td|th)\b[^>]*>(?P<body>.*?)</(?:td|th)>", re.IGNORECASE | re.DOTALL)
_TABLE_CELL_BOUNDARY_PATTERN = re.compile(
    r"</\s*(?:td|th)\s*>\s*<\s*(?:td|th)\b[^>]*>",
    re.IGNORECASE,
)
_TABLE_ROW_BREAK_PATTERN = re.compile(r"</\s*tr\s*>", re.IGNORECASE)


def _extract_blocks_from_structured_text(text: str) -> list[dict[str, Any]]:
    decoded = html.unescape(text)
    blocks: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[int, int, int, int], str]] = set()

    for index, match in enumerate(_STRUCTURED_BLOCK_PATTERN.finditer(decoded), start=1):
        attrs = match.group("attrs")
        bbox_match = re.search(r'data-bbox="(?P<bbox>[^"]+)"', attrs, re.IGNORECASE)
        label_match = re.search(r'data-label="(?P<label>[^"]+)"', attrs, re.IGNORECASE)
        if bbox_match is None or label_match is None:
            continue
        bbox = _parse_structured_bbox(bbox_match.group("bbox"))
        if bbox is None:
            continue
        raw_label = html.unescape(label_match.group("label")).strip()
        body = match.group("body")
        table_rows = _extract_table_rows_from_html(body)
        content = _table_rows_to_text(table_rows) if table_rows else _html_fragment_to_text(body)
        kind = _classify_structured_label(raw_label)
        if not content and kind != "image":
            continue
        signature = (kind, tuple(bbox), content)
        if signature in seen:
            continue
        seen.add(signature)
        block = {
            "id": f"block-{index:04d}",
            "type": kind,
            "label": raw_label,
            "bbox": bbox,
            "content": content,
            "text": content,
            "score": 0.0,
        }
        if table_rows:
            block["table_rows"] = table_rows
            block["html"] = body.strip()
        blocks.append(block)

    return blocks


def _parse_structured_bbox(raw_bbox: str) -> list[int] | None:
    values = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", raw_bbox)]
    if len(values) == 4:
        x0, y0, x1, y1 = values
        return [round(min(x0, x1)), round(min(y0, y1)), round(max(x0, x1)), round(max(y0, y1))]
    if len(values) >= 6 and len(values) % 2 == 0:
        xs = values[0::2]
        ys = values[1::2]
        return [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]
    return None


def _html_fragment_to_text(fragment: str) -> str:
    fragment = html.unescape(fragment)
    fragment = _BR_TAG_PATTERN.sub("\n", fragment)
    fragment = _TABLE_CELL_BOUNDARY_PATTERN.sub("\t", fragment)
    fragment = _TABLE_ROW_BREAK_PATTERN.sub("\n", fragment)
    fragment = _HTML_TAG_PATTERN.sub("", fragment)
    fragment = html.unescape(fragment)
    fragment = re.sub(r"\r\n?", "\n", fragment)
    fragment = re.sub(r"[ \t]+\n", "\n", fragment)
    fragment = re.sub(r"\n[ \t]+", "\n", fragment)
    fragment = re.sub(r"\n{3,}", "\n\n", fragment)
    return fragment.strip()


def _extract_table_rows_from_html(fragment: str) -> list[list[str]]:
    decoded = html.unescape(str(fragment or ""))
    rows: list[list[str]] = []
    for row_match in _TABLE_ROW_PATTERN.finditer(decoded):
        cells = [
            _html_fragment_to_text(cell_match.group("body"))
            for cell_match in _TABLE_CELL_PATTERN.finditer(row_match.group("body"))
        ]
        cleaned = [cell for cell in cells if cell]
        if len(cleaned) > 1:
            rows.append(cleaned)
    return rows


def _table_rows_to_text(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row if cell) for row in rows).strip()


def _classify_structured_label(label: str) -> str:
    normalized = normalize_block_label_value(label)
    return "text" if normalized == "unknown" else normalized


def _normalize_page_output(
    raw_result: Any,
    page: PageImageArtifact,
    config: ChandraHFConfig,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    payload = _as_mapping(raw_result)
    raw_markdown = _first_present(payload, "markdown", "raw", "text")

    json_payload = _ensure_mapping(
        _first_present(
            payload,
            "json",
            "json_output",
            "json_data",
            "document",
            "response",
        )
        or {}
    )
    if not isinstance(raw_markdown, str):
        payload_candidates = _iter_structured_text_candidates(payload)
        raw_markdown = payload_candidates[0] if payload_candidates else ""
    if not raw_markdown:
        json_markdown = _first_present(json_payload, "markdown", "text")
        if isinstance(json_markdown, str) and json_markdown.strip():
            raw_markdown = json_markdown
        else:
            json_candidates = _iter_structured_text_candidates(json_payload)
            raw_markdown = json_candidates[0] if json_candidates else ""
    markdown = str(raw_markdown or "")

    raw_html = _first_present(payload, "html") or _first_present(json_payload, "html")
    if isinstance(raw_html, str) and raw_html.strip():
        html_output = raw_html
    elif "<div" in markdown and "data-bbox" in markdown:
        html_output = markdown
    else:
        html_output = _best_effort_markdown_to_html(markdown)

    blocks = _maybe_scale_block_bboxes(_extract_blocks(payload, json_payload), page)

    normalized_json = dict(json_payload)
    normalized_json.setdefault("page_no", page.page_no)
    normalized_json.setdefault("image_path", str(page.image_path))
    normalized_json.setdefault("width", page.width)
    normalized_json.setdefault("height", page.height)
    if markdown and "markdown" not in normalized_json:
        normalized_json["markdown"] = markdown
    if html_output and "html" not in normalized_json:
        normalized_json["html"] = html_output
    if blocks and "blocks" not in normalized_json and "pages" not in normalized_json:
        normalized_json["blocks"] = blocks
    normalized_json.setdefault("raw", make_json_safe(payload))

    provided_metadata = _ensure_mapping(_first_present(payload, "metadata") or {})
    metadata = {
        "page_no": page.page_no,
        "image_path": str(page.image_path),
        "page_size": {"width": page.width, "height": page.height},
        "model_id": config.model_id,
        "method": config.method,
        "prompt_type": config.prompt_type,
        "created_utc": _utc_now(),
        "source_fields": sorted(payload.keys()),
    }
    metadata.update(make_json_safe(provided_metadata))

    return markdown, html_output, make_json_safe(normalized_json), make_json_safe(metadata)


def normalize_chandra_page_output(
    raw_result: Any,
    page: PageImageArtifact,
    config: ChandraHFConfig,
) -> tuple[str, str, dict[str, Any], dict[str, Any]]:
    return _normalize_page_output(raw_result=raw_result, page=page, config=config)


@dataclass(slots=True)
class ChandraHFLocalRunner:
    config: ChandraHFConfig = field(default_factory=ChandraHFConfig)
    _model: Any = field(default=None, init=False, repr=False)

    def _load_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            import torch
            from chandra.model.hf import generate_hf
            from chandra.model.schema import BatchInputItem
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "Chandra HuggingFace mode requires 'chandra-ocr[hf]', 'transformers', and 'torch'."
            ) from exc

        dtype = getattr(torch, self.config.dtype_name, None) if self.config.dtype_name else None
        model_kwargs: dict[str, Any] = {"device_map": self.config.device_map}
        if dtype is not None:
            model_kwargs["dtype"] = dtype

        try:
            model = AutoModelForImageTextToText.from_pretrained(self.config.model_id, **model_kwargs)
        except TypeError:
            if "dtype" in model_kwargs:
                model_kwargs["torch_dtype"] = model_kwargs.pop("dtype")
            model = AutoModelForImageTextToText.from_pretrained(self.config.model_id, **model_kwargs)

        model.eval()
        model.processor = AutoProcessor.from_pretrained(self.config.model_id)
        tokenizer = getattr(model.processor, "tokenizer", None)
        if tokenizer is not None:
            tokenizer.padding_side = "left"

        self._model = (model, generate_hf, BatchInputItem)
        return self._model

    def __call__(self, pages: Sequence[PageImageArtifact]) -> Sequence[Any]:
        model, generate_hf, batch_input_item = self._load_model()
        batch = []
        opened_images: list[Image.Image] = []
        try:
            for page in pages:
                image = Image.open(page.image_path)
                opened_images.append(image)
                batch.append(batch_input_item(image=image, prompt_type=self.config.prompt_type))
            return list(generate_hf(batch, model))
        finally:
            for image in opened_images:
                image.close()


@dataclass(slots=True)
class ChandraVLLMRunner:
    config: ChandraHFConfig = field(default_factory=ChandraHFConfig)
    _manager: Any = field(default=None, init=False, repr=False)

    def _load_model(self) -> Any:
        if self._manager is not None:
            return self._manager

        try:
            from chandra.model import InferenceManager
            from chandra.model.schema import BatchInputItem
        except ImportError as exc:
            raise RuntimeError(
                "Chandra vLLM mode requires 'chandra-ocr' plus a reachable vLLM OpenAI-compatible server."
            ) from exc

        self._manager = (InferenceManager(method="vllm"), BatchInputItem)
        return self._manager

    def __call__(self, pages: Sequence[PageImageArtifact]) -> Sequence[Any]:
        manager, batch_input_item = self._load_model()
        batch = []
        opened_images: list[Image.Image] = []
        try:
            for page in pages:
                image = Image.open(page.image_path)
                opened_images.append(image)
                batch.append(batch_input_item(image=image, prompt_type=self.config.prompt_type))

            generate_kwargs: dict[str, Any] = {}
            if self.config.vllm_api_base:
                generate_kwargs["vllm_api_base"] = self.config.vllm_api_base
            if self.config.vllm_max_retries is not None:
                generate_kwargs["max_retries"] = self.config.vllm_max_retries
            return list(manager.generate(batch, **generate_kwargs))
        finally:
            for image in opened_images:
                image.close()


def _load_existing_page(
    page: PageImageArtifact,
    artifact_layout: JobArtifactLayout,
) -> OCRPageArtifacts | None:
    markdown_path = artifact_layout.ocr_markdown_path(page.page_no)
    html_path = artifact_layout.ocr_html_path(page.page_no)
    json_path = artifact_layout.ocr_json_path(page.page_no)
    metadata_path = artifact_layout.ocr_metadata_path(page.page_no)
    if not all(path.exists() for path in (markdown_path, html_path, json_path, metadata_path)):
        return None
    return OCRPageArtifacts(
        page_no=page.page_no,
        image_path=page.image_path,
        markdown_path=markdown_path,
        html_path=html_path,
        json_path=json_path,
        metadata_path=metadata_path,
        raw_payload=load_json(json_path),
        metadata=load_json(metadata_path),
    )


def _write_page_outputs(
    page: PageImageArtifact,
    artifact_layout: JobArtifactLayout,
    markdown: str,
    html_output: str,
    json_payload: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> OCRPageArtifacts:
    markdown_path = artifact_layout.ocr_markdown_path(page.page_no)
    html_path = artifact_layout.ocr_html_path(page.page_no)
    json_path = artifact_layout.ocr_json_path(page.page_no)
    metadata_path = artifact_layout.ocr_metadata_path(page.page_no)

    write_text(markdown_path, markdown, overwrite=False)
    write_text(html_path, html_output, overwrite=False)
    write_json(json_path, json_payload, overwrite=False)
    write_json(metadata_path, metadata, overwrite=False)

    return OCRPageArtifacts(
        page_no=page.page_no,
        image_path=page.image_path,
        markdown_path=markdown_path,
        html_path=html_path,
        json_path=json_path,
        metadata_path=metadata_path,
        raw_payload=load_json(json_path),
        metadata=load_json(metadata_path),
    )


def run_chandra(
    rendered_pdf: RenderedPdf,
    artifact_layout: JobArtifactLayout,
    *,
    config: ChandraHFConfig | None = None,
    runner: ChandraRunner | None = None,
) -> OCRDocumentResult:
    config = config or ChandraHFConfig()
    if config.method not in {"hf", "vllm"}:
        raise ValueError(f"Unsupported Chandra OCR method: {config.method}")

    artifact_layout.ensure()

    completed_pages: dict[int, OCRPageArtifacts] = {}
    pending_pages: list[PageImageArtifact] = []
    for page in rendered_pdf.pages:
        existing = _load_existing_page(page, artifact_layout)
        if existing is not None:
            completed_pages[page.page_no] = existing
        else:
            pending_pages.append(page)

    active_runner = runner or (
        ChandraVLLMRunner(config=config) if config.method == "vllm" else ChandraHFLocalRunner(config=config)
    )
    for batch in _chunked(pending_pages, config.batch_size):
        raw_outputs = list(active_runner(batch))
        if len(raw_outputs) != len(batch):
            raise RuntimeError("Chandra runner returned an unexpected number of page outputs.")

        for page, raw_result in zip(batch, raw_outputs, strict=True):
            markdown, html_output, json_payload, metadata = normalize_chandra_page_output(
                raw_result=raw_result,
                page=page,
                config=config,
            )
            completed_pages[page.page_no] = _write_page_outputs(
                page=page,
                artifact_layout=artifact_layout,
                markdown=markdown,
                html_output=html_output,
                json_payload=json_payload,
                metadata=metadata,
            )

    ordered_pages = tuple(completed_pages[page.page_no] for page in rendered_pdf.pages)
    write_json(
        artifact_layout.manifest_path("ocr_manifest.json"),
        {
            "pdf_path": str(rendered_pdf.pdf_path),
            "job_id": rendered_pdf.job_id,
            "source_key": rendered_pdf.source_key,
            "model_id": config.model_id,
            "method": config.method,
            "page_count": rendered_pdf.page_count,
            "pages": [page.to_dict() for page in ordered_pages],
        },
        overwrite=True,
    )

    return OCRDocumentResult(
        pdf_path=rendered_pdf.pdf_path,
        job_id=rendered_pdf.job_id,
        source_key=rendered_pdf.source_key,
        method=config.method,
        model_id=config.model_id,
        artifact_root=artifact_layout.document_dir,
        pages=ordered_pages,
    )


def run_chandra_hf(
    rendered_pdf: RenderedPdf,
    artifact_layout: JobArtifactLayout,
    *,
    config: ChandraHFConfig | None = None,
    runner: ChandraRunner | None = None,
) -> OCRDocumentResult:
    return run_chandra(rendered_pdf=rendered_pdf, artifact_layout=artifact_layout, config=config, runner=runner)
