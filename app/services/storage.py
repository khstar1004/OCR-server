from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image
from slugify import slugify

from app.core.config import get_settings
from app.utils.geometry import clamp_bbox
from app.utils.json_utils import dump_json


class OutputStorage:
    def __init__(self):
        self.settings = get_settings()

    def job_pdf_path(self, job_key: str, pdf_name: str) -> Path:
        return self._job_pdf_path_for_root(self._primary_output_root(), job_key, pdf_name)

    def job_artifact_roots(self, job_key: str) -> tuple[Path, ...]:
        return tuple(root / job_key for root in self.settings.output_roots())

    def resolve_article_bundle_path(
        self,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_order: int,
        title: str,
    ) -> Path:
        candidates = [
            self._article_bundle_path_for_root(root, job_key, pdf_name, page_number, article_order, title)
            for root in self.settings.output_roots()
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _primary_output_root(self) -> Path:
        return self.settings.output_root.expanduser().resolve()

    @staticmethod
    def _pdf_slug(pdf_name: str) -> str:
        slug = slugify(Path(pdf_name).stem) or "pdf"
        return slug

    def _job_pdf_path_for_root(self, root: Path, job_key: str, pdf_name: str) -> Path:
        return root / job_key / self._pdf_slug(pdf_name)

    def job_pdf_root(self, job_key: str, pdf_name: str) -> Path:
        root = self.job_pdf_path(job_key, pdf_name)
        root.mkdir(parents=True, exist_ok=True)
        return root

    def page_dir(self, job_key: str, pdf_name: str) -> Path:
        path = self.job_pdf_root(job_key, pdf_name) / "pages"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def raw_dir(self, job_key: str, pdf_name: str) -> Path:
        path = self.job_pdf_root(job_key, pdf_name) / "raw"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def image_dir(self, job_key: str, pdf_name: str) -> Path:
        path = self.job_pdf_root(job_key, pdf_name) / "article_images"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def parsed_dir(self, job_key: str, pdf_name: str) -> Path:
        path = self.parsed_path(job_key, pdf_name)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def parsed_path(self, job_key: str, pdf_name: str) -> Path:
        return self.job_pdf_path(job_key, pdf_name) / "parsed"

    def page_bundle_dir(self, job_key: str, pdf_name: str, page_number: int) -> Path:
        path = self.page_bundle_path(job_key, pdf_name, page_number)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def page_bundle_path(self, job_key: str, pdf_name: str, page_number: int) -> Path:
        return self.parsed_path(job_key, pdf_name) / f"page_{page_number:04d}"

    def article_bundle_dir(
        self,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_order: int,
        title: str,
    ) -> Path:
        path = self.article_bundle_path(job_key, pdf_name, page_number, article_order, title)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def article_bundle_path(
        self,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_order: int,
        title: str,
    ) -> Path:
        return self._article_bundle_path_for_root(
            self._primary_output_root(),
            job_key,
            pdf_name,
            page_number,
            article_order,
            title,
        )

    def _article_bundle_path_for_root(
        self,
        root: Path,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_order: int,
        title: str,
    ) -> Path:
        title_slug = slugify(title[:80]) or "untitled"
        return self._page_bundle_path_for_root(root, job_key, pdf_name, page_number) / f"article_{article_order:02d}_{title_slug}"

    def _page_bundle_path_for_root(self, root: Path, job_key: str, pdf_name: str, page_number: int) -> Path:
        return self._job_pdf_path_for_root(root, job_key, pdf_name) / "parsed" / f"page_{page_number:04d}"

    def article_image_path(
        self,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_order: int,
        title: str,
        image_order: int,
    ) -> Path:
        path = self.article_bundle_dir(job_key, pdf_name, page_number, article_order, title) / "images"
        path.mkdir(parents=True, exist_ok=True)
        return path / f"image_{image_order:02d}.png"

    def save_raw_json(self, job_key: str, pdf_name: str, page_number: int, suffix: str, payload: dict[str, Any]) -> Path:
        path = self.raw_dir(job_key, pdf_name) / f"page_{page_number:04d}_{suffix}.json"
        dump_json(path, payload)
        return path

    def save_article_bundle(
        self,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_order: int,
        article_id: int,
        title: str,
        body_text: str,
        title_bbox: list[int] | None,
        article_bbox: list[int] | None,
        image_entries: list[dict[str, Any]],
        caption_entries: list[dict[str, Any]] | None = None,
        relevance_score: float | None = None,
        relevance_reason: str | None = None,
        relevance_label: str | None = None,
        relevance_model: str | None = None,
        relevance_source: str | None = None,
        corrected_title: str | None = None,
        corrected_body_text: str | None = None,
        correction_source: str | None = None,
        correction_model: str | None = None,
        source_metadata: dict[str, Any] | None = None,
    ) -> Path:
        bundle_dir = self.article_bundle_dir(job_key, pdf_name, page_number, article_order, title)
        caption_entries = list(caption_entries or [])
        metadata = {
            "job_id": job_key,
            "pdf_file": pdf_name,
            "page_number": page_number,
            "article_id": article_id,
            "article_order": article_order,
            "title": title,
            "body_text": body_text,
            "title_bbox": title_bbox,
            "article_bbox": article_bbox,
            "image_count": len(image_entries),
            "images": image_entries,
            "caption_count": len(caption_entries),
            "captions": caption_entries,
            "relevance_score": relevance_score,
            "relevance_reason": relevance_reason,
            "relevance_label": relevance_label,
            "relevance_model": relevance_model,
            "relevance_source": relevance_source,
            "corrected_title": corrected_title,
            "corrected_body_text": corrected_body_text,
            "correction_source": correction_source,
            "correction_model": correction_model,
            "source_metadata": source_metadata or None,
        }
        dump_json(bundle_dir / "article.json", metadata)
        dump_json(
            bundle_dir / "enrichment.json",
            {
                "relevance_score": relevance_score,
                "relevance_reason": relevance_reason,
                "relevance_label": relevance_label,
                "relevance_model": relevance_model,
                "relevance_source": relevance_source,
                "corrected_title": corrected_title,
                "corrected_body_text": corrected_body_text,
                "correction_source": correction_source,
                "correction_model": correction_model,
            },
        )
        (bundle_dir / "article.md").write_text(
            self._build_article_markdown(
                pdf_name=pdf_name,
                page_number=page_number,
                article_order=article_order,
                title=title,
                body_text=body_text,
                image_entries=image_entries,
                caption_entries=caption_entries,
            ),
            encoding="utf-8",
        )
        return bundle_dir

    def load_article_metadata(self, bundle_dir: Path) -> dict[str, Any]:
        metadata = self._read_json(bundle_dir / "article.json")
        enrichment = self._read_json(bundle_dir / "enrichment.json")
        for key, value in enrichment.items():
            if key not in metadata or metadata[key] is None:
                metadata[key] = value
        return metadata

    def save_page_manifest(
        self,
        job_key: str,
        pdf_name: str,
        page_number: int,
        article_entries: list[dict[str, Any]],
    ) -> Path:
        page_dir = self.page_bundle_dir(job_key, pdf_name, page_number)
        payload = {
            "job_id": job_key,
            "pdf_file": pdf_name,
            "page_number": page_number,
            "article_count": len(article_entries),
            "articles": article_entries,
        }
        dump_json(page_dir / "page.json", payload)
        return page_dir / "page.json"

    def crop_image(self, source_page: Path, bbox: list[int], output_path: Path) -> tuple[int, int]:
        with Image.open(source_page) as image:
            x0, y0, x1, y1 = clamp_bbox(bbox, image.width, image.height)
            crop = image.crop((x0, y0, x1, y1))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            crop.save(output_path)
            return crop.width, crop.height

    @staticmethod
    def _build_article_markdown(
        *,
        pdf_name: str,
        page_number: int,
        article_order: int,
        title: str,
        body_text: str,
        image_entries: list[dict[str, Any]],
        caption_entries: list[dict[str, Any]],
    ) -> str:
        lines = [f"# {title or 'Untitled'}", "", f"- PDF: {pdf_name}", f"- Page: {page_number}", f"- Article: {article_order}"]
        lines.append("")
        for paragraph in [item.strip() for item in body_text.replace("\r", "").split("\n") if item.strip()]:
            lines.append(paragraph)
            lines.append("")

        if image_entries:
            lines.append("## Images")
            lines.append("")
            for image in image_entries:
                relative_path = str(image.get("relative_path") or "").replace("\\", "/")
                lines.append(f"![{image.get('file_name', 'image')}]({relative_path})")
                for caption in image.get("captions") or []:
                    if not isinstance(caption, dict):
                        continue
                    caption_text = str(caption.get("text") or "").strip()
                    if not caption_text:
                        continue
                    lines.append(f"Caption: {caption_text}")
                lines.append("")
        elif caption_entries:
            lines.append("## Captions")
            lines.append("")
            for caption in caption_entries:
                if not isinstance(caption, dict):
                    continue
                caption_text = str(caption.get("text") or "").strip()
                if caption_text:
                    lines.append(caption_text)
                    lines.append("")

        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}
