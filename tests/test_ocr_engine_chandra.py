from __future__ import annotations

import importlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

from app.domain.types import BlockLabel
from app.ocr.chandra import ChandraHFConfig, normalize_chandra_page_output
from app.ocr.types import PageImageArtifact
from app.services.article_cluster import ArticleClusterer


def _reset_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def test_chandra_engine_normalizes_blocks_into_page_layout(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    image_path = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), color="white").save(image_path)

    class FakeRunner:
        def __call__(self, pages):
            assert len(pages) == 1
            return [
                {
                    "markdown": "# 국방 뉴스\n\n첫 번째 문단입니다.",
                    "json": {
                        "blocks": [
                            {
                                "id": "headline-1",
                                "type": "headline",
                                "bbox": [40, 40, 520, 120],
                                "text": "국방 뉴스",
                            },
                            {
                                "id": "body-1",
                                "type": "paragraph",
                                "bbox": [48, 160, 620, 820],
                                "text": "첫 번째 문단입니다.",
                            },
                            {
                                "id": "image-1",
                                "type": "image",
                                "bbox": [660, 180, 940, 520],
                            },
                        ]
                    },
                }
            ]

    engine = engine_module.OCREngine()
    monkeypatch.setattr(engine, "_get_chandra_runner", lambda config: FakeRunner())
    stage_logs: list[tuple[str, str, str]] = []

    layout = engine.parse_page(
        image_path=image_path,
        page_number=1,
        width=1000,
        height=1400,
        stage_callback=lambda step, status, message: stage_logs.append((step, status, message)),
    )

    labels = {block.label.value for block in layout.blocks}
    assert labels >= {"title", "text", "image"}
    assert layout.raw_vl["backend"] == "chandra"
    assert layout.raw_structure == {}
    assert layout.raw_fallback_ocr == {}
    assert any(step == "ocr_structure" and status == "skipped" for step, status, _ in stage_logs)
    assert any(step == "ocr_fallback" and status == "skipped" for step, status, _ in stage_logs)


def test_chandra_engine_builds_vllm_config_and_runner(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "")
    monkeypatch.setenv("CHANDRA_METHOD", "vllm")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("VLLM_API_BASE", "http://vllm-ocr:8000/v1")
    monkeypatch.setenv("VLLM_MODEL_NAME", "chandra-ocr-2")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    engine = engine_module.OCREngine()
    config = engine._build_chandra_config()
    runner = engine._get_chandra_runner(config)

    assert config.method == "vllm"
    assert config.model_id == "chandra-ocr-2"
    assert config.vllm_api_base == "http://vllm-ocr:8000/v1"
    assert runner.__class__.__name__ == "ChandraVLLMRunner"


def test_chandra_engine_uses_runtime_config_overrides(tmp_path: Path, monkeypatch) -> None:
    runtime_path = tmp_path / "runtime" / "settings.json"
    runtime_path.parent.mkdir(parents=True)
    runtime_path.write_text(
        json.dumps(
            {
                "values": {
                    "chandra_prompt_type": "ocr_layout_runtime",
                    "chandra_batch_size": 3,
                    "ocr_service_url": "http://runtime-ocr:8000",
                    "ocr_service_mode": "datalab_marker",
                    "vllm_api_base": "http://runtime-vllm:8000/v1",
                    "vllm_model_name": "runtime-model",
                    "vllm_max_retries": 2,
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(runtime_path))
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "")
    monkeypatch.setenv("CHANDRA_METHOD", "vllm")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("VLLM_API_BASE", "http://vllm-ocr:8000/v1")
    monkeypatch.setenv("VLLM_MODEL_NAME", "chandra-ocr-2")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    engine = engine_module.OCREngine()
    config = engine._build_chandra_config()

    assert config.model_id == "runtime-model"
    assert config.prompt_type == "ocr_layout_runtime"
    assert config.batch_size == 3
    assert config.vllm_api_base == "http://runtime-vllm:8000/v1"
    assert config.vllm_max_retries == 2
    assert engine._should_use_remote_service() is True
    assert engine._remote_service_mode() == "datalab_marker"
    assert engine._resolve_ocr_service_url(service_kind="marker") == "http://runtime-ocr:8000/api/v1/marker"


def test_chandra_engine_serializes_concurrent_inference(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "")
    monkeypatch.setenv("OCR_RETRY_LOW_QUALITY", "false")
    monkeypatch.setenv("OCR_MAX_CONCURRENT_REQUESTS", "1")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    image_path = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), color="white").save(image_path)

    class SlowRunner:
        def __init__(self):
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def __call__(self, pages):
            assert len(pages) == 1
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.05)
                return [
                    {
                        "json": {
                            "blocks": [
                                {
                                    "id": f"title-{pages[0].page_no}",
                                    "type": "headline",
                                    "bbox": [40, 40, 520, 120],
                                    "text": f"국방 뉴스 {pages[0].page_no}",
                                }
                            ]
                        }
                    }
                ]
            finally:
                with self.lock:
                    self.active -= 1

    engine = engine_module.OCREngine()
    runner = SlowRunner()
    monkeypatch.setattr(engine, "_get_chandra_runner", lambda config: runner)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(engine.parse_page, image_path, page_number, 1000, 1400)
            for page_number in (1, 2)
        ]
        layouts = [future.result(timeout=5) for future in futures]

    assert [layout.page_number for layout in layouts] == [1, 2]
    assert runner.max_active == 1


def test_normalize_chandra_page_output_extracts_structured_blocks_from_markdown(tmp_path: Path) -> None:
    page = PageImageArtifact(
        page_no=1,
        image_path=tmp_path / "page.png",
        width=2480,
        height=3509,
        source_pdf=tmp_path / "sample.pdf",
        dpi=200,
    )
    raw_result = {
        "markdown": (
            '<div data-bbox="46 239 165 263" data-label="Page-Header"><p>문화일보</p></div>\n'
            '<div data-bbox="82 270 920 301" data-label="Section-Header"><h1>기사 제목</h1></div>\n'
            '<div data-bbox="60 334 333 355" data-label="Text"><p>전문가 <br/> 눈치보기</p></div>'
        )
    }

    markdown, html_output, normalized_json, metadata = normalize_chandra_page_output(
        raw_result=raw_result,
        page=page,
        config=ChandraHFConfig(),
    )

    assert "문화일보" in markdown
    assert "문화일보" in html_output
    assert metadata["page_no"] == 1
    assert normalized_json["blocks"][0]["type"] == "header"
    assert normalized_json["blocks"][0]["label"] == "Page-Header"
    assert normalized_json["blocks"][0]["content"] == "문화일보"
    assert normalized_json["blocks"][1]["type"] == "title"
    assert normalized_json["blocks"][1]["content"] == "기사 제목"
    assert normalized_json["blocks"][2]["content"] == "전문가\n눈치보기"


def test_normalize_chandra_page_output_scales_1000_space_bboxes_for_large_pages(tmp_path: Path) -> None:
    page = PageImageArtifact(
        page_no=1,
        image_path=tmp_path / "page.png",
        width=2480,
        height=3509,
        source_pdf=tmp_path / "sample.pdf",
        dpi=200,
    )
    raw_result = {
        "markdown": '<div data-bbox="100 100 900 900" data-label="Section-Header"><h1>확대 제목</h1></div>'
    }

    _, _, normalized_json, _ = normalize_chandra_page_output(
        raw_result=raw_result,
        page=page,
        config=ChandraHFConfig(),
    )

    assert normalized_json["blocks"][0]["bbox"] == [248, 351, 2232, 3158]


def test_chandra_engine_recovers_title_and_images_from_nested_raw_output(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    image_path = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), color="white").save(image_path)

    structured_html = (
        '<div data-bbox="40 40 180 68" data-label="Page-Header"><p>문화일보</p></div>\n'
        '<div data-bbox="60 90 430 128" data-label="Section-Header"><h1>기사 제목</h1></div>\n'
        '<div data-bbox="60 150 430 182" data-label="Text"><p>첫 번째 본문입니다.</p></div>\n'
        '<div data-bbox="500 140 840 360" data-label="Image"><img alt="image"/></div>'
    )

    class FakeRunner:
        def __call__(self, pages):
            assert len(pages) == 1
            return [
                {
                    "raw": {
                        "markdown": structured_html,
                        "token_count": 321,
                        "error": False,
                    }
                }
            ]

    engine = engine_module.OCREngine()
    monkeypatch.setattr(engine, "_get_chandra_runner", lambda config: FakeRunner())

    layout = engine.parse_page(
        image_path=image_path,
        page_number=1,
        width=1000,
        height=1400,
    )

    labels = [block.label for block in layout.blocks]
    assert BlockLabel.HEADER in labels
    assert BlockLabel.TITLE in labels
    assert BlockLabel.IMAGE in labels

    articles, unassigned = ArticleClusterer().cluster_page(layout)
    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "기사 제목"
    assert article.images
    assert article.images[0].bbox == [500, 140, 840, 360]


def test_chandra_engine_can_parse_remote_service_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "http://ocr-service:8000")
    monkeypatch.setenv("OCR_SERVICE_TIMEOUT_SEC", "5")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    image_path = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), color="white").save(image_path)

    remote_payload = {
        "page_number": 1,
        "width": 1000,
        "height": 1400,
        "image_path": str(image_path),
        "raw_vl": {"engine": "chandra", "backend": "chandra", "raw": {}},
        "raw_structure": {},
        "raw_fallback_ocr": {},
        "blocks": [
            {
                "block_id": "remote-1",
                "page_number": 1,
                "label": "title",
                "bbox": [40, 40, 520, 120],
                "text": "원격 OCR 제목",
                "confidence": 0.93,
            },
            {
                "block_id": "remote-2",
                "page_number": 1,
                "label": "image",
                "bbox": [60, 160, 520, 300],
                "text": "",
                "confidence": 0.55,
            },
        ],
    }

    engine = engine_module.OCREngine()
    monkeypatch.setattr(engine, "_post_remote_ocr_request", lambda **kwargs: remote_payload)

    stage_logs: list[tuple[str, str, str]] = []
    layout = engine.parse_page(
        image_path=image_path,
        page_number=1,
        width=1000,
        height=1400,
        stage_callback=lambda step, status, message: stage_logs.append((step, status, message)),
    )

    assert layout.raw_vl == remote_payload["raw_vl"]
    labels = {block.label.value for block in layout.blocks}
    assert labels == {"title", "image"}
    assert any(step == "ocr_vl" and status == "running" for step, status, _ in stage_logs)
    assert any(step == "ocr_vl" and status == "completed" for step, status, _ in stage_logs)


def test_chandra_engine_can_parse_remote_marker_payload(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "https://marker.example.com")
    monkeypatch.setenv("OCR_SERVICE_MODE", "datalab_marker")
    monkeypatch.setenv("OCR_SERVICE_MARKER_MODE", "accurate")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    image_path = tmp_path / "page.png"
    Image.new("RGB", (1000, 1400), color="white").save(image_path)

    marker_payload = {
        "status": "complete",
        "output_format": "json",
        "parse_quality_score": 4.6,
        "json": {
            "blocks": [
                {
                    "id": "marker-title",
                    "type": "Section-Header",
                    "bbox": [40, 40, 520, 120],
                    "text": "원격 Marker 제목",
                },
                {
                    "id": "marker-text",
                    "type": "Text",
                    "bbox": [60, 160, 920, 232],
                    "text": "원격 Marker 본문",
                },
                {
                    "id": "marker-image",
                    "type": "Image",
                    "bbox": [560, 150, 860, 360],
                    "text": "",
                },
                {
                    "id": "marker-caption",
                    "type": "Caption",
                    "bbox": [560, 370, 860, 430],
                    "text": "원격 Marker 캡션",
                },
            ]
        },
    }

    engine = engine_module.OCREngine()
    monkeypatch.setattr(engine, "_request_remote_marker_result", lambda **kwargs: marker_payload)

    layout = engine.parse_page(
        image_path=image_path,
        page_number=1,
        width=1000,
        height=1400,
    )

    assert layout.raw_vl["backend"] == "datalab_marker"
    labels = [block.label for block in layout.blocks]
    assert BlockLabel.TITLE in labels
    assert BlockLabel.TEXT in labels
    assert BlockLabel.IMAGE in labels
    assert BlockLabel.CAPTION in labels

    articles, unassigned = ArticleClusterer().cluster_page(layout)
    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "원격 Marker 제목"
    assert "원격 Marker 본문" in article.body_text
    assert len(article.images) == 1
    assert [caption.text for caption in article.images[0].captions] == ["원격 Marker 캡션"]


def test_remote_service_timeout_disables_read_timeout_when_env_is_zero(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("OCR_BACKEND", "chandra")
    monkeypatch.setenv("OCR_SERVICE_URL", "http://ocr-service:8000")
    monkeypatch.setenv("OCR_SERVICE_TIMEOUT_SEC", "0")
    monkeypatch.setenv("CHANDRA_MODEL_ID", "datalab-to/chandra-ocr-2")
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))

    _reset_app_modules()
    engine_module = importlib.import_module("app.services.ocr_engine")

    timeout = engine_module.OCREngine()._remote_service_timeout()

    assert timeout.connect == 30.0
    assert timeout.pool == 30.0
    assert timeout.read is None
    assert timeout.write is None
