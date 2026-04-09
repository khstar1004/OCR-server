from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings
from app.core.files import FileFingerprint, compute_file_hash, fingerprint_for, iter_pdf_files
from app.models import JobRegistrationResult
from app.services.jobs import JobsPort


@dataclass(slots=True)
class TrackedFile:
    fingerprint: FileFingerprint
    stable_count: int = 0
    processed_hash: str | None = None


class PollingWatcher:
    def __init__(self, *, settings: Settings, jobs: JobsPort) -> None:
        self._settings = settings
        self._jobs = jobs
        self._tracked: dict[str, TrackedFile] = {}
        self._stop_event = asyncio.Event()
        self._running = False
        self._last_error: str | None = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_error(self) -> str | None:
        return self._last_error

    def scan_once(self) -> list[JobRegistrationResult]:
        registrations: list[JobRegistrationResult] = []
        active_paths: set[str] = set()

        for path in iter_pdf_files(self._settings.watch_dir):
            try:
                fingerprint = fingerprint_for(path)
            except OSError:
                continue

            resolved_path = str(path.resolve())
            active_paths.add(resolved_path)
            tracked = self._tracked.get(resolved_path)

            if tracked is None:
                self._tracked[resolved_path] = TrackedFile(fingerprint=fingerprint)
                continue

            if tracked.fingerprint != fingerprint:
                tracked.fingerprint = fingerprint
                tracked.stable_count = 0
                tracked.processed_hash = None
                continue

            if tracked.processed_hash is not None:
                continue

            tracked.stable_count += 1
            if tracked.stable_count < self._settings.stable_scan_count:
                continue

            try:
                file_hash = compute_file_hash(path)
            except OSError:
                continue

            registrations.append(self._jobs.register_pdf(path, file_hash, fingerprint.size))
            tracked.processed_hash = file_hash

        for missing_path in set(self._tracked) - active_paths:
            self._tracked.pop(missing_path, None)

        return registrations

    async def run(self) -> None:
        self._stop_event = asyncio.Event()
        self._running = True
        self._last_error = None
        interval = self._settings.poll_interval_sec if self._settings.poll_interval_sec > 0 else 0.1

        try:
            while not self._stop_event.is_set():
                try:
                    self.scan_once()
                    self._last_error = None
                except Exception as exc:  # pragma: no cover
                    self._last_error = str(exc)

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False

    def request_stop(self) -> None:
        self._stop_event.set()
