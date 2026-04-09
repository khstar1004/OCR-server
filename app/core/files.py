from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


@dataclass(frozen=True, slots=True)
class FileFingerprint:
    size: int
    mtime_ns: int


def compute_file_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint_for(path: Path) -> FileFingerprint:
    stat = path.stat()
    return FileFingerprint(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def iter_pdf_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file() and candidate.suffix.lower() == ".pdf"
    )
