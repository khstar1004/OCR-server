from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.container import ApplicationContainer, get_container
from app.models import HealthResponse


router = APIRouter()


@router.get("/healthz", response_model=HealthResponse, status_code=status.HTTP_200_OK)
def healthz(container: ApplicationContainer = Depends(get_container)) -> HealthResponse:
    return HealthResponse(
        status="ok" if container.database.ping() else "degraded",
        database_path=str(container.settings.database_path),
        watch_dir=str(container.settings.watch_dir),
        watcher_running=container.watcher.is_running,
        watcher_last_error=container.watcher.last_error,
        auto_deliver=container.settings.auto_deliver,
    )
