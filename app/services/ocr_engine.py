from __future__ import annotations

import html
import json
import re
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.domain.types import BlockLabel, OCRBlock, PageLayout
from app.ocr import ChandraHFConfig, ChandraHFLocalRunner, ChandraVLLMRunner, PageImageArtifact, normalize_chandra_page_output
from app.services.image_preprocessor import RetryImagePreprocessor
from app.services.runtime_config import runtime_config_value
from app.utils.geometry import bbox_area, bbox_from_any, bbox_height, box_contains, box_intersection_area, clamp_bbox, normalize_bboxes_to_page

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_BR_TAG_PATTERN = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)
_BLOCK_BREAK_PATTERN = re.compile(r"</\s*(?:p|div|h[1-6]|li)\s*>", re.IGNORECASE)


class OCREngine:
    def __init__(self):
        self.settings = get_settings()
        self._chandra_runner: ChandraHFLocalRunner | ChandraVLLMRunner | None = None
        self._retry_preprocessor = RetryImagePreprocessor()
        self._runner_lock = threading.RLock()
        self._max_concurrent_requests = max(int(self.settings.ocr_max_concurrent_requests or 1), 1)
        self._inference_gate = threading.BoundedSemaphore(self._max_concurrent_requests)

    def parse_page(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> PageLayout:
        primary_layout = self._parse_once(image_path, page_number, width, height, stage_callback)
        if not self._runtime_bool("ocr_retry_low_quality", self.settings.ocr_retry_low_quality):
            self._notify(stage_callback, "ocr_retry", "skipped", "low-quality retry disabled")
            return primary_layout

        primary_score = self._layout_quality_score(primary_layout)
        if self._is_layout_acceptable(primary_layout):
            self._notify(stage_callback, "ocr_retry", "skipped", f"quality ok score={primary_score:.3f}")
            return primary_layout

        self._notify(stage_callback, "ocr_retry", "running", f"retrying low-quality page score={primary_score:.3f}")
        retry_layout = self._retry_with_preprocessed_image(image_path, page_number, width, height, stage_callback)
        if retry_layout is None:
            self._notify(stage_callback, "ocr_retry", "completed", f"retry skipped score={primary_score:.3f}")
            return primary_layout

        retry_score = self._layout_quality_score(retry_layout)
        if retry_score > primary_score:
            self._notify(
                stage_callback,
                "ocr_retry",
                "completed",
                f"retry improved score {primary_score:.3f} -> {retry_score:.3f}",
            )
            return retry_layout

        self._notify(
            stage_callback,
            "ocr_retry",
            "completed",
            f"retry kept original score {primary_score:.3f} >= {retry_score:.3f}",
        )
        return primary_layout

    def _parse_once(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> PageLayout:
        backend = self._backend_name()
        if backend != "chandra":
            raise ValueError(f"unsupported OCR_BACKEND: {backend}. only 'chandra' is supported.")
        if self._should_use_remote_service():
            if self._remote_service_mode() == "datalab_marker":
                return self._parse_with_remote_marker(
                    image_path=image_path,
                    page_number=page_number,
                    width=width,
                    height=height,
                    stage_callback=stage_callback,
                )
            return self._parse_with_remote_chandra(
                image_path=image_path,
                page_number=page_number,
                width=width,
                height=height,
                stage_callback=stage_callback,
            )
        return self._parse_with_chandra(image_path, page_number, width, height, stage_callback)

    def _parse_with_chandra(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> PageLayout:
        self._notify(stage_callback, "ocr_vl", "running", f"running {self._chandra_display_name()}")
        raw_vl = self._run_chandra(image_path, page_number, width, height, stage_callback)
        self._notify(stage_callback, "ocr_vl", "completed", f"completed {self._chandra_display_name()}")
        self._notify(stage_callback, "ocr_structure", "skipped", "Chandra-only pipeline")
        self._notify(stage_callback, "ocr_fallback", "skipped", "Chandra-only pipeline")
        blocks = self._merge_blocks(page_number, width, height, raw_vl)
        return PageLayout(
            page_number=page_number,
            width=width,
            height=height,
            image_path=image_path,
            blocks=blocks,
            raw_vl=raw_vl,
            raw_structure={},
            raw_fallback_ocr={},
        )

    def _parse_with_remote_chandra(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> PageLayout:
        self._notify(stage_callback, "ocr_vl", "running", "calling remote OCR service")
        payload = self._run_with_inference_slot(
            stage_callback,
            "remote OCR service",
            lambda: self._post_remote_ocr_request(image_path=image_path, page_number=page_number, width=width, height=height),
        )
        layout = self._parse_remote_layout(
            payload=payload,
            image_path=image_path,
            page_number=page_number,
            width=width,
            height=height,
        )
        self._notify(stage_callback, "ocr_vl", "completed", "remote OCR service call completed")
        self._notify(stage_callback, "ocr_structure", "skipped", "remote OCR service handles Chandra pipeline")
        self._notify(stage_callback, "ocr_fallback", "skipped", "remote OCR service handles Chandra pipeline")
        return layout

    def _parse_with_remote_marker(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> PageLayout:
        self._notify(stage_callback, "ocr_vl", "running", "calling remote Datalab Marker service")
        payload = self._run_with_inference_slot(
            stage_callback,
            "remote Datalab Marker service",
            lambda: self._request_remote_marker_result(image_path=image_path),
        )
        layout = self._parse_remote_marker_layout(
            payload=payload,
            image_path=image_path,
            page_number=page_number,
            width=width,
            height=height,
        )
        score = self._as_float(payload.get("parse_quality_score"))
        score_message = f" parse_quality_score={score:.2f}" if score is not None else ""
        self._notify(stage_callback, "ocr_vl", "completed", f"remote Datalab Marker conversion completed{score_message}")
        self._notify(stage_callback, "ocr_structure", "skipped", "remote Datalab Marker returns structured layout")
        self._notify(stage_callback, "ocr_fallback", "skipped", "remote Datalab Marker returns structured layout")
        return layout

    def _retry_with_preprocessed_image(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> PageLayout | None:
        with tempfile.TemporaryDirectory(prefix="ocr-retry-") as temp_dir:
            variant_paths = self._retry_preprocessor.build_retry_variants(image_path, Path(temp_dir))
            best_layout: PageLayout | None = None
            best_score = float("-inf")
            for index, retry_image_path in enumerate(variant_paths, start=1):
                self._notify(
                    stage_callback,
                    "ocr_retry",
                    "running",
                    f"retry variant {index}/{len(variant_paths)}: {retry_image_path.name}",
                )
                layout = self._parse_once(retry_image_path, page_number, width, height, stage_callback)
                score = self._layout_quality_score(layout)
                if score > best_score:
                    best_layout = layout
                    best_score = score
            return best_layout

    def _run_chandra(
        self,
        image_path: Path,
        page_number: int,
        width: int,
        height: int,
        stage_callback: Callable[[str, str, str], None] | None = None,
    ) -> dict[str, Any]:
        config = self._build_chandra_config()
        runner = self._get_chandra_runner(config)
        page = PageImageArtifact(
            page_no=page_number,
            image_path=image_path,
            width=width,
            height=height,
            source_pdf=image_path,
            dpi=self._runtime_int("pdf_render_dpi", self.settings.pdf_render_dpi),
        )
        outputs = self._run_with_inference_slot(stage_callback, "Chandra OCR", lambda: list(runner([page])))
        if len(outputs) != 1:
            raise RuntimeError("Chandra runner returned an unexpected number of page outputs.")
        _, _, normalized_json, metadata = normalize_chandra_page_output(outputs[0], page, config)
        return self._build_chandra_payload(normalized_json, metadata, width, height)

    def _post_remote_ocr_request(
        self,
        image_path: Path,
        *,
        page_number: int,
        width: int,
        height: int,
    ) -> Mapping[str, Any]:
        service_url = self._resolve_ocr_service_url()
        request_data = {
            "page_number": str(page_number),
            "width": str(width),
            "height": str(height),
        }
        timeout = self._remote_service_timeout()
        with httpx.Client(timeout=timeout) as client:
            with image_path.open("rb") as image_file:
                response = client.post(
                    service_url,
                    files={"file": (image_path.name, image_file, "application/octet-stream")},
                    data=request_data,
                )
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("OCR service response must be a JSON object.")
        return payload

    def _request_remote_marker_result(self, image_path: Path) -> Mapping[str, Any]:
        service_url = self._resolve_ocr_service_url(service_kind="marker")
        timeout = self._remote_service_timeout()
        headers = self._remote_service_headers()
        request_data = {
            "output_format": "json",
            "mode": self._runtime_str("ocr_service_marker_mode", self.settings.ocr_service_marker_mode),
            "additional_config": json.dumps(
                {
                    "keep_pageheader_in_output": True,
                    "keep_pagefooter_in_output": True,
                }
            ),
        }

        with httpx.Client(timeout=timeout, headers=headers) as client:
            with image_path.open("rb") as image_file:
                response = client.post(
                    service_url,
                    files={"file": (image_path.name, image_file, "application/octet-stream")},
                    data=request_data,
                )
            response.raise_for_status()
            submission = response.json()

            if not isinstance(submission, dict):
                raise ValueError("Datalab Marker submission response must be a JSON object.")
            check_url = submission.get("request_check_url") or submission.get("check_url")
            if not isinstance(check_url, str) or not check_url.strip():
                if str(submission.get("status") or "").lower() == "complete":
                    return submission
                raise ValueError("Datalab Marker response missing request_check_url.")

            resolved_check_url = self._resolve_remote_check_url(service_url, check_url)
            while True:
                poll_response = client.get(resolved_check_url)
                poll_response.raise_for_status()
                result = poll_response.json()
                if not isinstance(result, dict):
                    raise ValueError("Datalab Marker result response must be a JSON object.")
                status = str(result.get("status") or "").lower()
                if status == "complete":
                    return result
                if status == "failed":
                    error_message = str(result.get("error") or "remote Datalab Marker conversion failed")
                    raise ValueError(error_message)
                time.sleep(max(self._runtime_float("ocr_service_poll_interval_sec", self.settings.ocr_service_poll_interval_sec), 0.1))

    def _remote_service_timeout(self) -> httpx.Timeout:
        raw_timeout = self._runtime_float("ocr_service_timeout_sec", self.settings.ocr_service_timeout_sec)
        if raw_timeout > 0:
            connect_timeout = min(raw_timeout, 30.0)
            return httpx.Timeout(
                connect=connect_timeout,
                read=raw_timeout,
                write=raw_timeout,
                pool=connect_timeout,
            )
        return httpx.Timeout(
            connect=30.0,
            read=None,
            write=None,
            pool=30.0,
        )

    def _parse_remote_layout(
        self,
        payload: Mapping[str, Any],
        image_path: Path,
        *,
        page_number: int,
        width: int,
        height: int,
    ) -> PageLayout:
        resolved_payload = dict(payload)
        resolved_width = int(resolved_payload.get("width", width))
        resolved_height = int(resolved_payload.get("height", height))
        resolved_page_number = int(resolved_payload.get("page_number", page_number))
        if resolved_width <= 0 or resolved_height <= 0:
            raise ValueError("remote OCR service returned invalid page dimensions")

        blocks: list[OCRBlock] = []
        for index, block_payload in enumerate(resolved_payload.get("blocks", []) or []):
            if not isinstance(block_payload, Mapping):
                continue
            label_value = str(block_payload.get("label") or "unknown").strip().lower()
            try:
                label = BlockLabel(label_value)
            except ValueError:
                label = BlockLabel.UNKNOWN

            block_bbox = bbox_from_any(block_payload.get("bbox"))
            if block_bbox is None:
                continue
            block_text = str(block_payload.get("text") or "").strip()
            block_metadata = block_payload.get("metadata", {})
            if not isinstance(block_metadata, dict):
                block_metadata = {}
            block_id = str(block_payload.get("block_id") or f"remote-{resolved_page_number}-{index + 1}")

            blocks.append(
                OCRBlock(
                    block_id=block_id,
                    page_number=resolved_page_number,
                    label=label,
                    bbox=block_bbox,
                    text=block_text,
                    confidence=self._to_float(block_payload.get("confidence"), 0.0),
                    metadata=dict(block_metadata),
                )
            )

        raw_vl = resolved_payload.get("raw_vl")
        if not isinstance(raw_vl, dict):
            raise ValueError("remote OCR service returned missing or invalid raw_vl payload")
        raw_structure = resolved_payload.get("raw_structure", {})
        raw_fallback_ocr = resolved_payload.get("raw_fallback_ocr", {})

        remote_image_path = resolved_payload.get("image_path")
        resolved_image_path = image_path
        if isinstance(remote_image_path, str) and remote_image_path.strip():
            resolved_image_path = Path(remote_image_path)

        return PageLayout(
            page_number=resolved_page_number,
            width=resolved_width,
            height=resolved_height,
            image_path=resolved_image_path,
            blocks=blocks,
            raw_vl=raw_vl,
            raw_structure=self._ensure_dict(raw_structure, {}),
            raw_fallback_ocr=self._ensure_dict(raw_fallback_ocr, {}),
        )

    def _parse_remote_marker_layout(
        self,
        payload: Mapping[str, Any],
        image_path: Path,
        *,
        page_number: int,
        width: int,
        height: int,
    ) -> PageLayout:
        marker_json = payload.get("json")
        if not isinstance(marker_json, Mapping):
            raise ValueError("Datalab Marker response missing JSON output.")

        normalized_json = dict(marker_json)
        normalized_json.setdefault("page_no", page_number)
        normalized_json.setdefault("width", width)
        normalized_json.setdefault("height", height)

        metadata = {
            "page_no": page_number,
            "model_id": "datalab-marker",
            "method": "remote",
            "prompt_type": "convert_json",
            "output_format": payload.get("output_format") or "json",
            "parse_quality_score": self._as_float(payload.get("parse_quality_score")),
            "source": "datalab_marker",
        }

        raw_vl = self._build_chandra_payload(
            normalized_json,
            metadata,
            width,
            height,
            engine_name="marker",
            backend_name="datalab_marker",
        )
        blocks = self._merge_blocks(page_number, width, height, raw_vl)
        return PageLayout(
            page_number=page_number,
            width=width,
            height=height,
            image_path=image_path,
            blocks=blocks,
            raw_vl=raw_vl,
            raw_structure={},
            raw_fallback_ocr={},
        )

    def _build_chandra_config(self) -> ChandraHFConfig:
        method = (self.settings.chandra_method or "hf").strip().lower()
        source = self._resolve_chandra_source()
        model_id = source
        if method == "vllm":
            model_id = self._runtime_str(
                "vllm_model_name",
                self.settings.vllm_model_name or Path(source).name or "chandra-ocr-2",
            ).strip()
        vllm_api_base = self._runtime_str(
            "vllm_api_base",
            self.settings.vllm_api_base or "http://localhost:8000/v1",
        ).strip()
        prompt_type = self._runtime_str("chandra_prompt_type", self.settings.chandra_prompt_type or "ocr_layout").strip()
        batch_size = max(self._runtime_int("chandra_batch_size", self.settings.chandra_batch_size or 1), 1)
        return ChandraHFConfig(
            model_id=model_id,
            prompt_type=prompt_type or "ocr_layout",
            method=method,
            device_map=self.settings.chandra_device_map,
            dtype_name=self.settings.chandra_dtype,
            batch_size=batch_size,
            vllm_api_base=vllm_api_base or None,
            vllm_max_retries=max(self._runtime_int("vllm_max_retries", self.settings.vllm_max_retries), 0) or None,
        )

    def _get_chandra_runner(self, config: ChandraHFConfig) -> ChandraHFLocalRunner | ChandraVLLMRunner:
        with self._runner_lock:
            same_config = (
                self._chandra_runner is not None
                and self._chandra_runner.config.model_id == config.model_id
                and self._chandra_runner.config.method == config.method
                and self._chandra_runner.config.prompt_type == config.prompt_type
                and self._chandra_runner.config.batch_size == config.batch_size
                and self._chandra_runner.config.device_map == config.device_map
                and self._chandra_runner.config.dtype_name == config.dtype_name
                and self._chandra_runner.config.vllm_api_base == config.vllm_api_base
                and self._chandra_runner.config.vllm_max_retries == config.vllm_max_retries
            )
            if not same_config:
                if config.method == "vllm":
                    self._chandra_runner = ChandraVLLMRunner(config=config)
                else:
                    self._chandra_runner = ChandraHFLocalRunner(config=config)
            return self._chandra_runner

    def _run_with_inference_slot(
        self,
        stage_callback: Callable[[str, str, str], None] | None,
        operation_name: str,
        callback: Callable[[], Any],
    ) -> Any:
        self._sync_inference_gate()
        acquired = self._inference_gate.acquire(blocking=False)
        if not acquired:
            self._notify(
                stage_callback,
                "ocr_queue",
                "queued",
                f"waiting for {operation_name} slot; max_concurrent={self._max_concurrent_requests}",
            )
            self._inference_gate.acquire()
        try:
            return callback()
        finally:
            self._inference_gate.release()

    def _sync_inference_gate(self) -> None:
        desired = max(self._runtime_int("ocr_max_concurrent_requests", self.settings.ocr_max_concurrent_requests), 1)
        if desired == self._max_concurrent_requests:
            return
        with self._runner_lock:
            if desired != self._max_concurrent_requests:
                self._max_concurrent_requests = desired
                self._inference_gate = threading.BoundedSemaphore(desired)

    def _resolve_chandra_source(self) -> str:
        if self.settings.chandra_model_dir:
            model_dir = Path(self.settings.chandra_model_dir)
            if not model_dir.is_absolute():
                model_dir = Path(self.settings.models_root) / model_dir
            model_dir = model_dir.resolve()
            if not model_dir.exists():
                raise FileNotFoundError(f"CHANDRA_MODEL_DIR not found: {model_dir}")
            return str(model_dir)
        return self.settings.chandra_model_id

    def _build_chandra_payload(
        self,
        normalized_json: dict[str, Any],
        metadata: dict[str, Any],
        width: int,
        height: int,
        *,
        engine_name: str = "chandra",
        backend_name: str = "chandra",
    ) -> dict[str, Any]:
        parsing_items = self._extract_chandra_parsing_items(normalized_json, width, height)
        markdown = str(normalized_json.get("markdown") or "").strip()
        if not parsing_items and markdown:
            parsing_items = self._build_synthetic_chandra_items(markdown, width, height)
        parsing_items = self._normalize_parsing_item_bboxes(parsing_items, width, height)

        return {
            "engine": engine_name,
            "backend": backend_name,
            "model_id": metadata.get("model_id") or self._resolve_chandra_source(),
            "prompt_type": metadata.get("prompt_type")
            or self._runtime_str("chandra_prompt_type", self.settings.chandra_prompt_type or "ocr_layout"),
            "page_no": normalized_json.get("page_no"),
            "width": width,
            "height": height,
            "parsing_res_list": parsing_items,
            "raw": normalized_json,
            "metadata": metadata,
        }

    @staticmethod
    def _normalize_parsing_item_bboxes(
        parsing_items: list[dict[str, Any]],
        width: int,
        height: int,
    ) -> list[dict[str, Any]]:
        item_boxes = [bbox for bbox in (bbox_from_any(item.get("bbox")) for item in parsing_items) if bbox is not None]
        if not item_boxes:
            return parsing_items

        normalized_boxes = normalize_bboxes_to_page(item_boxes, width, height)
        if normalized_boxes == item_boxes:
            return parsing_items

        scaled_items: list[dict[str, Any]] = []
        box_iter = iter(normalized_boxes)
        for item in parsing_items:
            scaled = dict(item)
            bbox = bbox_from_any(item.get("bbox"))
            if bbox is not None:
                scaled["bbox"] = next(box_iter)
            scaled_items.append(scaled)
        return scaled_items

    def _extract_chandra_parsing_items(self, payload: Any, width: int, height: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, int, int, str]] = set()

        def visit(node: Any, inherited_kind: str | None = None) -> None:
            if isinstance(node, dict):
                explicit_page = node.get("page_no") or node.get("page")
                if explicit_page not in (None, "", payload.get("page_no") if isinstance(payload, dict) else None):
                    return

                kind = self._pick_chandra_kind(node, inherited_kind)
                text = self._pick_chandra_text(node)
                bbox = self._extract_chandra_bbox(node, width, height)
                if bbox is not None and (text or self._is_image_label(kind)):
                    label = self._normalize_chandra_label(kind, text)
                    signature = (label, bbox[0], bbox[1], bbox[2], bbox[3], text)
                    if signature not in seen:
                        seen.add(signature)
                        item: dict[str, Any] = {
                            "label": label,
                            "bbox": bbox,
                            "content": text,
                            "score": self._as_float(node.get("score") or node.get("confidence")),
                        }
                        if label == "image":
                            item["content"] = ""
                        results.append(item)

                for key, value in node.items():
                    if key in {"bbox", "box", "bounds", "bounding_box", "polygon", "points"}:
                        continue
                    if isinstance(value, (dict, list, tuple)):
                        visit(value, kind)
                return

            if isinstance(node, (list, tuple)):
                for item in node:
                    visit(item, inherited_kind)

        visit(payload)
        return sorted(results, key=lambda item: (item["bbox"][1], item["bbox"][0], item["label"], item["content"]))

    @staticmethod
    def _pick_chandra_kind(node: dict[str, Any], inherited_kind: str | None = None) -> str:
        for key in ("type", "block_type", "kind", "role", "category", "label", "name"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return re.sub(r"[\s-]+", "_", value.strip().lower())
        return re.sub(r"[\s-]+", "_", (inherited_kind or "text").strip().lower())

    @staticmethod
    def _pick_chandra_text(node: dict[str, Any]) -> str:
        for key in ("text", "content", "markdown", "title", "caption", "label_text"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, list):
                parts = [str(item).strip() for item in value if str(item).strip()]
                if parts:
                    return " ".join(parts)
        return ""

    def _extract_chandra_bbox(self, node: dict[str, Any], width: int, height: int) -> list[int] | None:
        direct = (
            node.get("bbox")
            or node.get("box")
            or node.get("bounds")
            or node.get("bounding_box")
            or node.get("polygon")
            or node.get("points")
        )
        bbox = self._bbox_from_chandra_value(direct, width, height)
        if bbox is not None:
            return bbox

        if {"x0", "y0", "x1", "y1"} <= node.keys():
            return self._bbox_from_chandra_value([node["x0"], node["y0"], node["x1"], node["y1"]], width, height)
        if {"left", "top", "right", "bottom"} <= node.keys():
            return self._bbox_from_chandra_value(
                [node["left"], node["top"], node["right"], node["bottom"]],
                width,
                height,
            )
        if {"x", "y", "width", "height"} <= node.keys():
            x = float(node["x"])
            y = float(node["y"])
            box_width = float(node["width"])
            box_height = float(node["height"])
            return self._bbox_from_chandra_value([x, y, x + box_width, y + box_height], width, height)
        return None

    def _bbox_from_chandra_value(self, raw: Any, width: int, height: int) -> list[int] | None:
        bbox = bbox_from_any(raw)
        if bbox is not None:
            return clamp_bbox(bbox, width, height)

        if not isinstance(raw, (list, tuple)) or len(raw) != 4:
            return None
        values = [float(value) for value in raw]
        max_abs = max(abs(value) for value in values)
        if max_abs <= 1.5:
            scaled = [
                round(values[0] * width),
                round(values[1] * height),
                round(values[2] * width),
                round(values[3] * height),
            ]
        elif max_abs <= 1000 and max(width, height) > 1200:
            scaled = [
                round((values[0] / 1000.0) * width),
                round((values[1] / 1000.0) * height),
                round((values[2] / 1000.0) * width),
                round((values[3] / 1000.0) * height),
            ]
        else:
            scaled = [round(value) for value in values]
        return clamp_bbox(scaled, width, height)

    def _build_synthetic_chandra_items(self, markdown: str, width: int, height: int) -> list[dict[str, Any]]:
        paragraphs = [segment.strip() for segment in markdown.replace("\r", "").split("\n") if segment.strip()]
        if not paragraphs:
            return []

        items: list[dict[str, Any]] = []
        left = max(int(width * 0.05), 24)
        right = min(width - left, width - 24)
        top = max(int(height * 0.05), 32)
        step = max(int(height * 0.08), 72)
        for index, paragraph in enumerate(paragraphs, start=1):
            label = "title" if index == 1 else "text"
            box_height = max(int(height * (0.05 if label == "title" else 0.04)), 42)
            y0 = top + ((index - 1) * step)
            y1 = min(height - 24, y0 + box_height)
            items.append(
                {
                    "label": label,
                    "bbox": [left, y0, right, y1],
                    "content": paragraph.lstrip("#").strip(),
                    "score": 0.0,
                    "synthetic": True,
                }
            )
        return items

    @staticmethod
    def _normalize_chandra_label(kind: str, text: str) -> str:
        lowered = re.sub(r"[\s-]+", "_", (kind or "").lower())
        if any(token in lowered for token in ("image", "figure", "photo", "graphic", "diagram")):
            return "image"
        if any(token in lowered for token in ("caption", "footnote")):
            return "caption"
        if lowered in {"header", "page_header", "pageheader"}:
            return "header"
        if any(token in lowered for token in ("headline", "title", "section_header", "doc_title", "subheadline")):
            return "title"
        if any(token in lowered for token in ("footer",)):
            return "footer"
        if any(token in lowered for token in ("advert", "ad")):
            return "advertisement"
        if text.strip().startswith("#"):
            return "title"
        return "text"

    def _is_layout_acceptable(self, layout: PageLayout) -> bool:
        text = self._layout_text(layout)
        compact = self._compact_text(text)
        if len(compact) < self._runtime_int("ocr_quality_min_chars", self.settings.ocr_quality_min_chars):
            return False
        return self._korean_ratio(text) >= self._runtime_float(
            "ocr_quality_min_korean_ratio",
            self.settings.ocr_quality_min_korean_ratio,
        )

    def _layout_quality_score(self, layout: PageLayout) -> float:
        text = self._layout_text(layout)
        compact = self._compact_text(text)
        char_score = min(
            len(compact) / max(self._runtime_int("ocr_quality_min_chars", self.settings.ocr_quality_min_chars), 1),
            2.0,
        ) / 2.0
        korean_score = min(self._korean_ratio(text), 1.0)
        image_bonus = 0.1 if any(block.label == BlockLabel.IMAGE for block in layout.blocks) else 0.0
        duplicate_penalty = self._duplicate_line_penalty(text)
        structure_penalty = self._layout_structure_penalty(layout)
        return round(
            max(
                0.0,
                (char_score * 0.55) + (korean_score * 0.35) + image_bonus - duplicate_penalty - structure_penalty,
            ),
            4,
        )

    @staticmethod
    def _layout_text(layout: PageLayout) -> str:
        chunks = [
            block.text
            for block in layout.blocks
            if block.label in {BlockLabel.TITLE, BlockLabel.TEXT, BlockLabel.CAPTION} and block.text.strip()
        ]
        return "\n".join(chunks).strip()

    @staticmethod
    def _compact_text(text: str) -> str:
        return "".join(ch for ch in text if not ch.isspace())

    @staticmethod
    def _korean_ratio(text: str) -> float:
        hangul_count = len(re.findall(r"[가-힣]", text))
        latin_count = len(re.findall(r"[A-Za-z]", text))
        cjk_count = len(re.findall(r"[\u4e00-\u9fff]", text))
        denominator = hangul_count + latin_count + cjk_count
        if denominator <= 0:
            return 0.0
        return hangul_count / denominator

    @staticmethod
    def _duplicate_line_penalty(text: str) -> float:
        lines = [OCREngine._compact_text(line) for line in text.splitlines() if OCREngine._compact_text(line)]
        if len(lines) < 3:
            return 0.0
        counts = Counter(lines)
        duplicated = sum(count - 1 for count in counts.values() if count > 1)
        short_duplicate_hits = sum(1 for line, count in counts.items() if count >= 3 and len(line) <= 40)
        duplicate_ratio = duplicated / len(lines)
        return min(0.35, (duplicate_ratio * 0.45) + (short_duplicate_hits * 0.05))

    @staticmethod
    def _layout_structure_penalty(layout: PageLayout) -> float:
        text_blocks = [block for block in layout.blocks if block.label in {BlockLabel.TITLE, BlockLabel.TEXT, BlockLabel.CAPTION}]
        if len(text_blocks) < 3:
            return 0.0

        title_blocks = [block for block in text_blocks if block.label == BlockLabel.TITLE]
        caption_blocks = [block for block in text_blocks if block.label == BlockLabel.CAPTION]
        penalty = 0.0

        if len(title_blocks) >= 2:
            title_ratio = len(title_blocks) / max(len(text_blocks), 1)
            if title_ratio > 0.35:
                penalty += min(0.14, (title_ratio - 0.35) * 0.45)

            oversized_titles = sum(1 for block in title_blocks if len(OCREngine._compact_text(block.text)) >= 60)
            if oversized_titles:
                penalty += min(0.12, oversized_titles * 0.04)

        if len(caption_blocks) >= 3 and not title_blocks:
            penalty += 0.06

        return min(0.22, penalty)

    @staticmethod
    def _notify(stage_callback: Callable[[str, str, str], None] | None, step_name: str, status: str, message: str) -> None:
        if stage_callback is not None:
            stage_callback(step_name, status, message)

    def _backend_name(self) -> str:
        return (self.settings.ocr_backend or "chandra").strip().lower()

    def _chandra_display_name(self) -> str:
        method = (self.settings.chandra_method or "hf").strip().lower()
        if method == "vllm":
            name = (self.settings.vllm_model_name or self.settings.chandra_model_dir or self.settings.chandra_model_id or "chandra-ocr-2")
            return f"Chandra OCR via vLLM ({Path(name).name})"
        source = self.settings.chandra_model_dir or self.settings.chandra_model_id
        name = Path(source).name if source else "chandra-ocr-2"
        return f"Chandra OCR ({name})"

    def _merge_blocks(
        self,
        page_number: int,
        page_width: int,
        page_height: int,
        raw_vl: dict[str, Any],
    ) -> list[OCRBlock]:
        text_lines = self._extract_text_lines(page_number, raw_vl)
        layout_regions = self._extract_layout_regions(raw_vl)
        heights = [bbox_height(block.bbox) for block in text_lines if block.text.strip()]
        median_height = sorted(heights)[len(heights) // 2] if heights else 20

        merged: list[OCRBlock] = []
        for line in text_lines:
            region = self._best_region_for_line(line, layout_regions)
            region_label = region["label"] if region else None
            line.metadata["layout_label"] = region_label
            line.label = self._infer_line_label(
                parser_label=line.label,
                text=line.text,
                bbox=line.bbox,
                region_label=region_label,
                median_height=median_height,
                page_width=page_width,
                page_height=page_height,
            )
            merged.append(line)

        seen_image_blocks: set[tuple[str, int, int, int, int]] = set()
        image_bboxes: list[list[int]] = []
        for idx, region in enumerate(layout_regions, start=1):
            region_label = str(region["label"]).lower()
            if not self._is_image_label(region_label):
                continue
            key = (region_label, *region["bbox"])
            if key in seen_image_blocks:
                continue
            if self._is_duplicate_image_bbox(region["bbox"], image_bboxes):
                continue
            seen_image_blocks.add(key)
            image_bboxes.append(region["bbox"])
            merged.append(
                OCRBlock(
                    block_id=f"image-{page_number}-{idx}",
                    page_number=page_number,
                    label=BlockLabel.IMAGE,
                    bbox=region["bbox"],
                    confidence=float(region.get("score", 0.0) or 0.0),
                    metadata={"layout_label": region_label},
                )
            )
        return merged

    def _extract_layout_regions(self, raw: dict[str, Any]) -> list[dict[str, Any]]:
        regions: list[dict[str, Any]] = []
        seen: set[tuple[str, int, int, int, int]] = set()
        for item in raw.get("parsing_res_list", []) or []:
            if not isinstance(item, dict):
                continue
            bbox = bbox_from_any(item.get("bbox") or item.get("polygon_points") or item.get("ori_bbox"))
            if bbox is None:
                continue
            label = str(item.get("label") or "unknown").lower()
            key = (label, *bbox)
            if key in seen:
                continue
            seen.add(key)
            regions.append({"bbox": bbox, "label": label, "score": float(item.get("score", 0.0) or 0.0)})
        return regions

    def _extract_text_lines(self, page_number: int, raw: dict[str, Any]) -> list[OCRBlock]:
        lines: list[OCRBlock] = []
        for idx, item in enumerate(raw.get("parsing_res_list", []) or [], start=1):
            if not isinstance(item, dict):
                continue
            parser_label = str(item.get("label") or "").lower()
            if self._is_image_label(parser_label):
                continue
            text = self._clean_extracted_text(item.get("content"))
            if not text:
                continue
            bbox = bbox_from_any(item.get("bbox") or item.get("polygon_points") or item.get("ori_bbox"))
            if bbox is None:
                continue
            lines.append(
                OCRBlock(
                    block_id=f"parsed-{page_number}-{idx}",
                    page_number=page_number,
                    label=self._label_from_parser_label(parser_label),
                    bbox=bbox,
                    text=text,
                    confidence=float(item.get("score", 0.0) or 0.0),
                    metadata={"parser_label": parser_label},
                )
            )
        return lines

    @staticmethod
    def _clean_extracted_text(value: Any) -> str:
        text = html.unescape(str(value or "")).replace("\r", "\n")
        if not text:
            return ""
        text = _BR_TAG_PATTERN.sub("\n", text)
        text = _BLOCK_BREAK_PATTERN.sub("\n", text)
        text = _HTML_TAG_PATTERN.sub("", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _label_from_parser_label(label: str) -> BlockLabel:
        lowered = label.lower()
        if any(tag in lowered for tag in ["title", "headline"]):
            return BlockLabel.TITLE
        if any(tag in lowered for tag in ["caption", "footnote"]):
            return BlockLabel.CAPTION
        if "header" in lowered:
            return BlockLabel.HEADER
        if "footer" in lowered:
            return BlockLabel.FOOTER
        if any(tag in lowered for tag in ["advert", "ad"]):
            return BlockLabel.ADVERTISEMENT
        return BlockLabel.TEXT

    @staticmethod
    def _best_region_for_line(line: OCRBlock, regions: list[dict[str, Any]]) -> dict[str, Any] | None:
        best = None
        best_score = 0
        for region in regions:
            if box_contains(region["bbox"], line.bbox):
                return region
            overlap = box_intersection_area(region["bbox"], line.bbox)
            if overlap > best_score:
                best_score = overlap
                best = region
        return best

    @staticmethod
    def _infer_line_label(
        parser_label: BlockLabel,
        text: str,
        bbox: list[int],
        region_label: str | None,
        median_height: int,
        page_width: int,
        page_height: int,
    ) -> BlockLabel:
        if parser_label in {
            BlockLabel.TITLE,
            BlockLabel.CAPTION,
            BlockLabel.HEADER,
            BlockLabel.FOOTER,
            BlockLabel.ADVERTISEMENT,
        }:
            return parser_label

        region = (region_label or "").lower()
        header_like = any(tag in region for tag in ["header", "page_header", "pageheader"])
        footer_like = any(tag in region for tag in ["footer", "page_footer", "pagefooter"])
        if any(tag in region for tag in ["title", "headline", "section", "doc_title", "subheadline"]):
            return BlockLabel.TITLE
        if any(tag in region for tag in ["caption", "vision_footnote"]):
            return BlockLabel.CAPTION
        if "advert" in region or re.search(r"(?:^|[_\-\s])ad(?:$|[_\-\s])", region):
            return BlockLabel.ADVERTISEMENT
        height = bbox_height(bbox)
        width = bbox[2] - bbox[0]
        text = text.strip()
        if not text:
            return BlockLabel.UNKNOWN
        if bbox[1] <= page_height * 0.04 and len(text) <= 50:
            return BlockLabel.HEADER
        if bbox[3] >= page_height * 0.97 and (len(text) <= 40 or any(ch.isdigit() for ch in text)):
            return BlockLabel.FOOTER
        if OCREngine._looks_like_advertisement(text):
            return BlockLabel.ADVERTISEMENT
        if (
            height <= max(12, int(median_height * 0.95))
            and len(text) <= 80
            and width <= page_width * 0.7
            and any(token in text for token in ["사진", "자료", "연합뉴스", "기자", "caption", "photo"])
        ):
            return BlockLabel.CAPTION
        if (
            parser_label in {BlockLabel.TEXT, BlockLabel.UNKNOWN}
            and height >= median_height * 1.75
            and len(text) <= 48
            and width < page_width * 0.85
            and bbox[1] <= page_height * 0.35
            and not text.rstrip().endswith((".", "!", "?", "다.", "했다.", "있다.", "였다."))
        ):
            return BlockLabel.TITLE
        if header_like:
            return BlockLabel.HEADER
        if footer_like:
            return BlockLabel.FOOTER
        if parser_label != BlockLabel.UNKNOWN:
            return parser_label
        return BlockLabel.TEXT

    @staticmethod
    def _is_image_label(label: str) -> bool:
        return any(tag in label for tag in ["image", "figure", "photo", "picture", "illustration", "chart", "graphic"])

    @staticmethod
    def _looks_like_advertisement(text: str) -> bool:
        lowered = text.lower()
        if any(token in lowered for token in ["advertisement", "sponsored", "paid content", "광고", "전면광고"]):
            return True
        if len(text) > 80:
            return False
        digit_count = sum(ch.isdigit() for ch in text)
        has_discount = any(token in text for token in ["할인", "특가", "이벤트", "분양"])
        has_contact = any(token in text for token in ["문의", "상담", "예약", "대표번호"])
        has_percent = "%" in text
        has_currency = re.search(r"\d[\d,\.\s]*(원|만원|억원|천원|달러)", text) is not None
        if digit_count >= 4 and (has_discount or has_contact or has_percent or has_currency):
            return True
        return False

    @staticmethod
    def _is_duplicate_image_bbox(candidate: list[int], existing: list[list[int]]) -> bool:
        candidate_area = max(1, bbox_area(candidate))
        for current in existing:
            overlap = box_intersection_area(candidate, current)
            if overlap / candidate_area >= 0.9:
                return True
        return False

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _to_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _ensure_dict(value: Any, default: dict[str, Any]) -> dict[str, Any]:
        return value if isinstance(value, dict) else default

    def _should_use_remote_service(self) -> bool:
        return bool(self._runtime_str("ocr_service_url", self.settings.ocr_service_url or "").strip())

    def _remote_service_mode(self) -> str:
        return self._runtime_str("ocr_service_mode", self.settings.ocr_service_mode or "native").strip().lower()

    def _remote_service_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.settings.ocr_service_api_key:
            headers["X-API-Key"] = self.settings.ocr_service_api_key
        return headers

    @staticmethod
    def _resolve_remote_check_url(service_url: str, check_url: str) -> str:
        if check_url.startswith("http://") or check_url.startswith("https://"):
            return check_url
        return str(httpx.URL(service_url).join(check_url))

    def _resolve_ocr_service_url(self, *, service_kind: str = "ocr_image") -> str:
        service_url = self._runtime_str("ocr_service_url", self.settings.ocr_service_url or "").strip()
        if not service_url:
            raise ValueError("OCR_SERVICE_URL is not configured.")
        normalized = service_url.rstrip("/")
        if service_kind == "marker":
            if normalized.endswith("/api/v1/marker"):
                return normalized
            if normalized.endswith("/api/v1"):
                return f"{normalized}/marker"
            return f"{normalized}/api/v1/marker"
        if normalized.endswith("/api/v1/ocr/image"):
            return normalized
        if normalized.endswith("/api/v1"):
            return f"{normalized}/ocr/image"
        return f"{normalized}/api/v1/ocr/image"

    def _runtime_bool(self, key: str, fallback: bool) -> bool:
        return bool(runtime_config_value(key, fallback, self.settings))

    def _runtime_int(self, key: str, fallback: int) -> int:
        try:
            return int(runtime_config_value(key, fallback, self.settings))
        except (TypeError, ValueError):
            return int(fallback)

    def _runtime_float(self, key: str, fallback: float) -> float:
        try:
            return float(runtime_config_value(key, fallback, self.settings))
        except (TypeError, ValueError):
            return float(fallback)

    def _runtime_str(self, key: str, fallback: str) -> str:
        value = runtime_config_value(key, fallback, self.settings)
        return str(value if value is not None else fallback)
