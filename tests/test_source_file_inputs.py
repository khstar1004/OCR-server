from __future__ import annotations

import sys
from types import SimpleNamespace

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


def test_pdf_renderer_uses_pymupdf_before_pdfium_for_encrypted_pdfs(tmp_path, monkeypatch):
    class FakeMatrix:
        def __init__(self, scale_x: float, scale_y: float) -> None:
            self.scale_x = scale_x
            self.scale_y = scale_y

    class FakePixmap:
        width = 240
        height = 120

        def save(self, path: str) -> None:
            Image.new("RGB", (self.width, self.height), color="white").save(path)

    class FakePage:
        def get_pixmap(self, *, matrix: FakeMatrix, alpha: bool = False) -> FakePixmap:
            return FakePixmap()

    class FakeDocument:
        page_count = 1

        def load_page(self, index: int) -> FakePage:
            if index != 0:
                raise IndexError(index)
            return FakePage()

        def close(self) -> None:
            return None

    def fail_pdfium(path: str):
        raise RuntimeError("PDFium should not be used when PyMuPDF succeeds")

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(Matrix=FakeMatrix, open=lambda path: FakeDocument()))
    monkeypatch.setattr("app.services.pdf_renderer.pdfium.PdfDocument", fail_pdfium)

    pdf_path = tmp_path / "encrypted.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n% encrypted sample")

    rendered = PdfRenderer(300).render(pdf_path, tmp_path / "pages")

    assert len(rendered) == 1
    assert rendered[0].page_number == 1
    assert rendered[0].image_path.exists()


def test_pdf_renderer_uses_playground_pdfium_fallback_when_pymupdf_fails(tmp_path, monkeypatch):
    class FakeMatrix:
        def __init__(self, scale_x: float, scale_y: float) -> None:
            self.scale_x = scale_x
            self.scale_y = scale_y

    class FakeBitmap:
        width = 200
        height = 100

        def to_pil(self) -> Image.Image:
            return Image.new("RGB", (self.width, self.height), color="white")

    class FakePage:
        def render(self, *, scale: float) -> FakeBitmap:
            return FakeBitmap()

        def close(self) -> None:
            return None

    class FakeDocument:
        def __init__(self, path: str) -> None:
            self.path = path

        def __len__(self) -> int:
            return 1

        def __getitem__(self, index: int) -> FakePage:
            if index != 0:
                raise IndexError(index)
            return FakePage()

        def close(self) -> None:
            return None

    def fail_pymupdf(path: str):
        raise RuntimeError("PyMuPDF failed to open encrypted PDF")

    monkeypatch.setitem(sys.modules, "fitz", SimpleNamespace(Matrix=FakeMatrix, open=fail_pymupdf))
    monkeypatch.setattr("app.services.pdf_renderer.pdfium.PdfDocument", FakeDocument)

    pdf_path = tmp_path / "encrypted.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n% encrypted sample")

    rendered = PdfRenderer(300).render(pdf_path, tmp_path / "pages")

    assert len(rendered) == 1
    assert rendered[0].page_number == 1
    assert rendered[0].image_path.name == "page_0001.png"
    assert rendered[0].image_path.exists()
