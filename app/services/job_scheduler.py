from __future__ import annotations

import asyncio

from app.db.session import SessionLocal
from app.services.job_runner import JobRunner


class JobScheduler:
    def __init__(self):
        self._tasks: dict[int, asyncio.Task] = {}

    async def schedule(self, job_id: int) -> None:
        existing = self._tasks.get(job_id)
        if existing is not None and not existing.done():
            return
        task = asyncio.create_task(asyncio.to_thread(self._run_sync, job_id))
        self._tasks[job_id] = task

    @staticmethod
    def _run_sync(job_id: int) -> None:
        db = SessionLocal()
        try:
            JobRunner(db).execute(job_id)
        finally:
            db.close()


_scheduler = JobScheduler()


def get_job_scheduler() -> JobScheduler:
    return _scheduler

