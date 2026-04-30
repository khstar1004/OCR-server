from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from app.core.files import IMAGE_FILE_SUFFIXES, PDF_FILE_SUFFIXES, SUPPORTED_SOURCE_SUFFIXES


@dataclass
class DiscoveredSourceFile:
    file_name: str
    file_path: Path
    file_hash: str
    file_date: date | None
    skip_reason: str | None = None


PDF_INPUT_SUFFIXES = PDF_FILE_SUFFIXES
IMAGE_INPUT_SUFFIXES = IMAGE_FILE_SUFFIXES
SUPPORTED_INPUT_SUFFIXES = SUPPORTED_SOURCE_SUFFIXES
DiscoveredPdf = DiscoveredSourceFile


class FileScanner:
    def __init__(self, source_dir: str | Path):
        self.source_dir = Path(source_dir)

    def scan(self, requested_date: date | None, existing_hashes: set[str], force_reprocess: bool) -> list[DiscoveredSourceFile]:
        items: list[DiscoveredSourceFile] = []
        seen_hashes: set[str] = set()
        for file_path in sorted(self.source_dir.rglob("*")):
            if not file_path.is_file() or file_path.suffix.lower() not in SUPPORTED_INPUT_SUFFIXES:
                continue
            file_hash = self._sha256(file_path)
            skip_reason = None
            if not force_reprocess and (file_hash in existing_hashes or file_hash in seen_hashes):
                skip_reason = "duplicate_hash"
            file_date = requested_date or date.fromtimestamp(file_path.stat().st_mtime)
            items.append(
                DiscoveredSourceFile(
                    file_name=file_path.name,
                    file_path=file_path,
                    file_hash=file_hash,
                    file_date=file_date,
                    skip_reason=skip_reason,
                )
            )
            seen_hashes.add(file_hash)
        return items

    @staticmethod
    def _sha256(file_path: Path) -> str:
        hasher = hashlib.sha256()
        with file_path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        return hasher.hexdigest()
