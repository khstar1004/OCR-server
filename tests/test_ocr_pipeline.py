from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from app.services.artifacts import build_job_artifact_layout
from app.services.ocr_pipeline import render_pdf, run_chandra_ocr


def _install_fake_fitz(monkeypatch) -> None:
    class FakeMatrix:
        def __init__(self, scale_x: float, scale_y: float) -> None:
            self.scale_x = scale_x
            self.scale_y = scale_y

    class FakePixmap:
        def __init__(self, width: int, height: int) -> None:
            self.width = width
            self.height = height

        def save(self, path: str) -> None:
            Image.new("RGB", (self.width, self.height), color="white").save(path)

    class FakePage:
        def __init__(self, index: int) -> None:
            self.index = index

        def get_pixmap(self, *, matrix: FakeMatrix, alpha: bool = False) -> FakePixmap:
            width = int(600 * matrix.scale_x)
            height = int(900 * matrix.scale_y)
            return FakePixmap(width=width, height=height)

    class FakeDocument:
        def __init__(self, path: Path) -> None:
            self.path = path
            self.page_count = 2

        def load_page(self, index: int) -> FakePage:
            return FakePage(index)

        def close(self) -> None:
            return None

    monkeypatch.setitem(
        sys.modules,
        "fitz",
        SimpleNamespace(Matrix=FakeMatrix, open=lambda path: FakeDocument(Path(path))),
    )


def test_render_pdf_and_reuse_existing_raw_ocr_outputs(tmp_path, monkeypatch) -> None:
    _install_fake_fitz(monkeypatch)

    pdf_path = tmp_path / "sample-news.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    data_dir = tmp_path / "data"

    rendered = render_pdf(pdf_path, data_dir, job_id="job-001", dpi=144)
    layout = build_job_artifact_layout(data_dir, "job-001", pdf_path)

    assert rendered.page_count == 2
    assert rendered.pages[0].image_path == layout.page_image_path(1)
    assert rendered.pages[0].image_path.exists()
    assert rendered.pages[1].image_path.exists()

    runner_calls: list[list[int]] = []

    def fake_runner(pages):
        runner_calls.append([page.page_no for page in pages])
        payloads = []
        for page in pages:
            payloads.append(
                {
                    "markdown": f"# Headline {page.page_no}\nBody paragraph for page {page.page_no}.",
                    "html": f"<h1>Headline {page.page_no}</h1><p>Body paragraph for page {page.page_no}.</p>",
                    "json": {
                        "blocks": [
                            {
                                "id": f"headline-{page.page_no}",
                                "type": "headline",
                                "bbox": [40, 40, 500, 120],
                                "text": f"Headline {page.page_no}",
                            },
                            {
                                "id": f"paragraph-{page.page_no}",
                                "type": "paragraph",
                                "bbox": [40, 132, 500, 260],
                                "text": f"Body paragraph for page {page.page_no}.",
                            },
                        ]
                    },
                }
            )
        return payloads

    ocr_result = run_chandra_ocr(rendered, data_dir, "job-001", batch_size=2, runner=fake_runner)
    assert runner_calls == [[1, 2]]
    assert ocr_result.pages[0].markdown_path.exists()
    assert ocr_result.pages[0].html_path.exists()
    assert ocr_result.pages[0].json_path.exists()
    assert ocr_result.pages[0].metadata_path.exists()

    first_markdown = layout.ocr_markdown_path(1).read_text(encoding="utf-8")
    first_json = json.loads(layout.ocr_json_path(1).read_text(encoding="utf-8"))
    metadata = json.loads(layout.ocr_metadata_path(1).read_text(encoding="utf-8"))

    assert first_json["page_no"] == 1
    assert metadata["method"] == "hf"
    assert metadata["model_id"] == "datalab-to/chandra-ocr-2"

    def should_not_run(_pages):
        raise AssertionError("Existing raw OCR artifacts should be reused instead of overwritten.")

    rerun_result = run_chandra_ocr(rendered, data_dir, "job-001", batch_size=2, runner=should_not_run)
    assert rerun_result.pages[0].json_path == layout.ocr_json_path(1)
    assert layout.ocr_markdown_path(1).read_text(encoding="utf-8") == first_markdown
