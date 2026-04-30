from __future__ import annotations

from PIL import Image

from app.services.file_scanner import FileScanner
from app.services.pdf_renderer import PdfRenderer


def test_scanner_and_renderer_accept_image_inputs(tmp_path):
    source_dir = tmp_path / "inputs"
    source_dir.mkdir()
    image_path = source_dir / "encrypted-page.jpg"
    Image.new("RGB", (320, 180), color="white").save(image_path)
    (source_dir / "ignore.txt").write_text("ignore", encoding="utf-8")

    discovered = FileScanner(source_dir).scan(None, set(), True)
    rendered = PdfRenderer(300).render(discovered[0].file_path, tmp_path / "pages")

    assert [item.file_name for item in discovered] == ["encrypted-page.jpg"]
    assert len(rendered) == 1
    assert rendered[0].page_number == 1
    assert rendered[0].width == 320
    assert rendered[0].height == 180
    assert rendered[0].image_path.name == "page_0001.png"
