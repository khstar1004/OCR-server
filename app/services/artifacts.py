from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^0-9A-Za-z._-]+", "-", value.strip())
    cleaned = re.sub(r"-{2,}", "-", cleaned)
    cleaned = cleaned.strip("-._")
    return cleaned or "document"


def make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return make_json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): make_json_safe(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [make_json_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return {
            key: make_json_safe(item)
            for key, item in vars(value).items()
            if not key.startswith("_") and not callable(item)
        }
    return repr(value)


def _write_text(path: Path, content: str, *, overwrite: bool) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    if overwrite:
        path.write_text(content, encoding="utf-8")
        return True
    if path.exists():
        return False
    with path.open("x", encoding="utf-8") as handle:
        handle.write(content)
    return True


def write_text(path: str | Path, content: str, *, overwrite: bool = True) -> bool:
    return _write_text(Path(path), content, overwrite=overwrite)


def write_json(path: str | Path, payload: Any, *, overwrite: bool = True) -> bool:
    path = Path(path)
    serialized = json.dumps(make_json_safe(payload), indent=2, ensure_ascii=False, sort_keys=True)
    return _write_text(path, serialized, overwrite=overwrite)


def load_json(path: str | Path) -> dict[str, Any]:
    content = Path(path).read_text(encoding="utf-8")
    return json.loads(content)


@dataclass(frozen=True, slots=True)
class JobArtifactLayout:
    data_dir: Path
    job_id: str
    source_key: str
    jobs_dir: Path
    job_dir: Path
    document_dir: Path
    pages_dir: Path
    ocr_dir: Path
    articles_dir: Path
    manifests_dir: Path

    def ensure(self) -> "JobArtifactLayout":
        for directory in (
            self.data_dir,
            self.jobs_dir,
            self.job_dir,
            self.document_dir,
            self.pages_dir,
            self.ocr_dir,
            self.articles_dir,
            self.manifests_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def page_image_path(self, page_no: int) -> Path:
        return self.pages_dir / f"page-{page_no:04d}.png"

    def ocr_markdown_path(self, page_no: int) -> Path:
        return self.ocr_dir / f"page-{page_no:04d}.md"

    def ocr_html_path(self, page_no: int) -> Path:
        return self.ocr_dir / f"page-{page_no:04d}.html"

    def ocr_json_path(self, page_no: int) -> Path:
        return self.ocr_dir / f"page-{page_no:04d}.json"

    def ocr_metadata_path(self, page_no: int) -> Path:
        return self.ocr_dir / f"page-{page_no:04d}.metadata.json"

    def article_page_dir(self, page_no: int) -> Path:
        return self.articles_dir / f"page-{page_no:04d}"

    def article_image_path(self, page_no: int, article_index: int) -> Path:
        return self.article_page_dir(page_no) / f"article-{article_index:04d}.png"

    def article_metadata_path(self, page_no: int, article_index: int) -> Path:
        return self.article_page_dir(page_no) / f"article-{article_index:04d}.json"

    def page_segmentation_path(self, page_no: int) -> Path:
        return self.article_page_dir(page_no) / "segmentation.json"

    def manifest_path(self, name: str) -> Path:
        return self.manifests_dir / name


def build_job_artifact_layout(
    data_dir: str | Path,
    job_id: str,
    source_path: str | Path,
    *,
    source_key: str | None = None,
) -> JobArtifactLayout:
    data_dir = Path(data_dir)
    source_path = Path(source_path)
    resolved_source_key = slugify(source_key or source_path.stem or source_path.name)
    resolved_job_id = slugify(job_id)
    jobs_dir = data_dir / "jobs"
    job_dir = jobs_dir / resolved_job_id
    document_dir = job_dir / resolved_source_key
    return JobArtifactLayout(
        data_dir=data_dir,
        job_id=resolved_job_id,
        source_key=resolved_source_key,
        jobs_dir=jobs_dir,
        job_dir=job_dir,
        document_dir=document_dir,
        pages_dir=document_dir / "pages",
        ocr_dir=document_dir / "ocr",
        articles_dir=document_dir / "articles",
        manifests_dir=document_dir / "manifests",
    )
