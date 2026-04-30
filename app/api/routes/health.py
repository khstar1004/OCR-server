from pathlib import Path

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services.runtime_config import runtime_config_value

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, object]:
    settings = get_settings()
    checks: dict[str, dict[str, object]] = {}

    try:
        db.execute(text("select 1"))
        checks["database"] = {"ok": True}
    except Exception as exc:  # noqa: BLE001
        checks["database"] = {"ok": False, "error": str(exc)}

    for name, path in (
        ("input_root", settings.input_root),
        ("output_root", settings.output_root),
        ("models_root", settings.models_root),
    ):
        resolved = Path(path)
        checks[name] = {
            "ok": resolved.exists(),
            "path": str(resolved),
        }

    target_api_base_url = str(
        runtime_config_value("target_api_base_url", settings.target_api_base_url or "", settings) or ""
    ).strip()
    checks["delivery_target"] = {
        "ok": bool(target_api_base_url),
        "configured": bool(target_api_base_url),
    }

    status = "ready" if all(bool(check.get("ok")) for check in checks.values()) else "degraded"
    return {
        "status": status,
        "service": "army-ocr",
        "checks": checks,
    }
