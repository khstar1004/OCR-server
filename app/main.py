from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Sequence

from fastapi import APIRouter, FastAPI

from app.api.demo import router as demo_router
from app.api.jobs import router as jobs_router
from app.api.routes.health import router as health_router
from app.api.routes.jobs import router as runtime_jobs_router
from app.api.system import router as system_router
from app.db.session import initialize_schema
from app.config import Settings
from app.core.config import get_settings as get_runtime_settings
from app.core.container import ApplicationContainer


def mount_extension_router(app: FastAPI, router: APIRouter) -> None:
    app.include_router(router)


def create_app(
    *,
    settings: Settings | None = None,
    container: ApplicationContainer | None = None,
    extra_routers: Sequence[APIRouter] | None = None,
) -> FastAPI:
    resolved_container = container or ApplicationContainer.build(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_container.initialize()
        initialize_schema()
        app.state.container = resolved_container
        watcher_task = asyncio.create_task(resolved_container.watcher.run())
        try:
            yield
        finally:
            resolved_container.watcher.request_stop()
            await watcher_task

    runtime_settings = get_runtime_settings()
    app = FastAPI(
        title="army-ocr Core Service",
        version="0.1.0",
        root_path=runtime_settings.normalized_root_path,
        lifespan=lifespan,
    )
    app.state.container = resolved_container
    app.include_router(system_router)
    app.include_router(jobs_router)
    app.include_router(health_router, prefix=runtime_settings.api_prefix)
    app.include_router(runtime_jobs_router, prefix=runtime_settings.api_prefix)
    app.include_router(demo_router)

    for router in extra_routers or ():
        mount_extension_router(app, router)

    return app


app = create_app()
