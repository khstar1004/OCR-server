from __future__ import annotations

import json
import os
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

from fastapi import FastAPI, Request

from app.core.config import Settings, get_settings
from app.services.auth_store import AUTH_COOKIE_NAME, get_auth_store

SENSITIVE_QUERY_KEYS = ("password", "passwd", "token", "secret", "api_key", "apikey", "authorization", "cookie")


class AuditLogStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.path = self._resolve_path(self.settings)
        self.backup_dir = self._resolve_backup_dir(self.settings, self.path)
        self.state_path = self.path.with_name("audit_state.json")
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_path(settings: Settings) -> Path:
        configured = str(getattr(settings, "audit_log_path", None) or os.getenv("AUDIT_LOG_PATH") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()

        auth_path = str(getattr(settings, "auth_store_path", None) or os.getenv("AUTH_STORE_PATH") or "").strip()
        if auth_path:
            return (Path(auth_path).expanduser().resolve().parent / "audit" / "api_calls.jsonl").resolve()

        runtime_path = getattr(settings, "runtime_config_path", None) or os.getenv("RUNTIME_CONFIG_PATH")
        if runtime_path:
            return (Path(runtime_path).expanduser().resolve().parent / "audit" / "api_calls.jsonl").resolve()

        base_root = getattr(settings, "output_root", None) or getattr(settings, "data_dir", None) or Path("./data")
        return (Path(base_root) / "_runtime_config" / "audit" / "api_calls.jsonl").expanduser().resolve()

    @staticmethod
    def _resolve_backup_dir(settings: Settings, path: Path) -> Path:
        configured = str(getattr(settings, "audit_backup_dir", None) or os.getenv("AUDIT_BACKUP_DIR") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return path.parent / "backups"

    def record_api_call(self, request: Request, *, status_code: int, duration_ms: float, service: str) -> None:
        now = datetime.now(timezone.utc)
        entry = {
            "timestamp": now.isoformat(),
            "event": "api_call",
            "service": service,
            "method": request.method,
            "path": request.url.path,
            "query": _redacted_query_string(str(request.url.query or "")),
            "status_code": int(status_code),
            "duration_ms": round(float(duration_ms), 2),
            "client": request.client.host if request.client else "",
            "user_agent": str(request.headers.get("user-agent") or "")[:300],
        }
        user = _user_from_cookie(request)
        if user:
            entry["user_id"] = user.get("id")
            entry["username"] = user.get("username")
            entry["role"] = user.get("role")
        self.append(entry, now=now)

    def record_event(self, event: str, *, request: Request | None = None, user: dict[str, Any] | None = None, **extra: Any) -> None:
        now = datetime.now(timezone.utc)
        entry: dict[str, Any] = {
            "timestamp": now.isoformat(),
            "event": event,
        }
        if request is not None:
            entry.update(
                {
                    "method": request.method,
                    "path": request.url.path,
                    "query": _redacted_query_string(str(request.url.query or "")),
                    "client": request.client.host if request.client else "",
                }
            )
        if user:
            entry["user_id"] = user.get("id")
            entry["username"] = user.get("username")
            entry["role"] = user.get("role")
        entry.update({key: value for key, value in extra.items() if value is not None})
        self.append(entry, now=now)

    def append(self, entry: dict[str, Any], *, now: datetime | None = None) -> None:
        current = now or datetime.now(timezone.utc)
        with self._lock:
            self._ensure_weekly_backup(current)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")

    def read_entries(self, *, limit: int = 200) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit), 1000))
        lines: list[str] = []
        try:
            with self.path.open("r", encoding="utf-8") as stream:
                lines = stream.readlines()
        except FileNotFoundError:
            lines = []
        entries: list[dict[str, Any]] = []
        for line in reversed(lines):
            if len(entries) >= safe_limit:
                break
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                entries.append(payload)
        return {
            "success": True,
            "path": str(self.path),
            "backup_dir": str(self.backup_dir),
            "count": len(entries),
            "limit": safe_limit,
            "entries": entries,
            "backups": self.list_backups(),
        }

    def list_backups(self) -> list[dict[str, Any]]:
        if not self.backup_dir.exists():
            return []
        items = []
        for path in sorted(self.backup_dir.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True):
            stat = path.stat()
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "updated_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                }
            )
        return items

    def force_backup(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        with self._lock:
            backup_path = self._backup_path(f"manual-{now.strftime('%Y%m%dT%H%M%SZ')}")
            created = self._copy_active_log(backup_path)
            return {
                "success": True,
                "created": created,
                "backup_path": str(backup_path),
                "path": str(self.path),
                "backups": self.list_backups(),
            }

    def _ensure_weekly_backup(self, now: datetime) -> None:
        current_week = _iso_week_key(now)
        state = self._read_state()
        previous_week = str(state.get("active_week") or "")
        if not previous_week:
            self._write_state({"active_week": current_week, "updated_at": now.isoformat()})
            return
        if previous_week == current_week:
            return
        backup_path = self._backup_path(previous_week)
        if self.path.exists() and self.path.stat().st_size > 0:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            if not backup_path.exists():
                shutil.copy2(self.path, backup_path)
            self.path.write_text("", encoding="utf-8")
        self._write_state({"active_week": current_week, "last_backup_week": previous_week, "updated_at": now.isoformat()})

    def _backup_path(self, label: str) -> Path:
        return self.backup_dir / f"api_calls-{label}.jsonl"

    def _copy_active_log(self, backup_path: Path) -> bool:
        if not self.path.exists() or self.path.stat().st_size <= 0:
            return False
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(self.path, backup_path)
        return True

    def _read_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.state_path)


def install_audit_logging(app: FastAPI, *, service_name: str) -> None:
    @app.middleware("http")
    async def audit_api_calls(request: Request, call_next):  # type: ignore[no-untyped-def]
        if not _is_api_path(request.url.path):
            return await call_next(request)
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = int(response.status_code)
            return response
        finally:
            duration_ms = (time.perf_counter() - started) * 1000
            try:
                AuditLogStore(get_settings()).record_api_call(
                    request,
                    status_code=status_code,
                    duration_ms=duration_ms,
                    service=service_name,
                )
            except Exception:
                pass


def _is_api_path(path: str) -> bool:
    return path.startswith("/api/") or "/api/" in path


def _redacted_query_string(query: str) -> str:
    if not query:
        return ""
    pairs = []
    for key, value in parse_qsl(query, keep_blank_values=True):
        if any(token in key.lower() for token in SENSITIVE_QUERY_KEYS):
            pairs.append((key, "***"))
        else:
            pairs.append((key, value))
    return urlencode(pairs, doseq=True)


def _user_from_cookie(request: Request) -> dict[str, Any] | None:
    session_id = request.cookies.get(AUTH_COOKIE_NAME)
    if not session_id:
        return None
    try:
        return get_auth_store(get_settings()).user_for_session(session_id, touch=False)
    except Exception:
        return None


def _iso_week_key(value: datetime) -> str:
    iso = value.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"
