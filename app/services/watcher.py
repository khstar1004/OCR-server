from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.config import Settings
from app.core.files import FileFingerprint, compute_file_hash, fingerprint_for, iter_source_files
from app.models import JobRegistrationResult
from app.services.jobs import JobsPort
from app.services.runtime_config import runtime_config_value


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

        for path in iter_source_files(self._settings.watch_dir):
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
            if tracked.stable_count < self._stable_scan_count():
                continue

            try:
                file_hash = compute_file_hash(path)
            except OSError:
                continue

            registrations.append(self._jobs.register_source_file(path, file_hash, fingerprint.size))
            tracked.processed_hash = file_hash

        for missing_path in set(self._tracked) - active_paths:
            self._tracked.pop(missing_path, None)

        return registrations

    async def run(self) -> None:
        self._stop_event = asyncio.Event()
        self._running = True
        self._last_error = None
        try:
            while not self._stop_event.is_set():
                try:
                    self.scan_once()
                    self._last_error = None
                except Exception as exc:  # pragma: no cover
                    self._last_error = str(exc)

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_sec())
                except asyncio.TimeoutError:
                    continue
        finally:
            self._running = False

    def request_stop(self) -> None:
        self._stop_event.set()

    def _poll_interval_sec(self) -> float:
        try:
            value = float(runtime_config_value("watch_poll_interval_sec", self._settings.poll_interval_sec))
        except (TypeError, ValueError):
            value = float(self._settings.poll_interval_sec)
        return value if value > 0 else 0.1

    def _stable_scan_count(self) -> int:
        try:
            value = int(runtime_config_value("watch_stable_scan_count", self._settings.stable_scan_count))
        except (TypeError, ValueError):
            value = int(self._settings.stable_scan_count)
        return max(value, 1)
