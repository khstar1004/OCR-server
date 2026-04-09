from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path


@dataclass
class DiscoveredPdf:
    file_name: str
    file_path: Path
    file_hash: str
    file_date: date | None
    skip_reason: str | None = None


class FileScanner:
    def __init__(self, source_dir: str | Path):
        self.source_dir = Path(source_dir)

    def scan(self, requested_date: date | None, existing_hashes: set[str], force_reprocess: bool) -> list[DiscoveredPdf]:
        items: list[DiscoveredPdf] = []
        seen_hashes: set[str] = set()
        for file_path in sorted(self.source_dir.rglob("*.[Pp][Dd][Ff]")):
            file_hash = self._sha256(file_path)
            skip_reason = None
            if not force_reprocess and (file_hash in existing_hashes or file_hash in seen_hashes):
                skip_reason = "duplicate_hash"
            file_date = requested_date or date.fromtimestamp(file_path.stat().st_mtime)
            items.append(
                DiscoveredPdf(
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
