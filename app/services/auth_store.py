from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request, status

from app.core.config import Settings, get_settings

AUTH_COOKIE_NAME = "army_ocr_session"
_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_.@-]{3,64}$")
_HASH_ITERATIONS = 260_000


@dataclass(frozen=True, slots=True)
class AuthSession:
    session_id: str
    user: dict[str, Any]
    expires_at: str


class AuthStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.path = self._resolve_path(self.settings)
        self._lock = threading.RLock()
        self._ensure_bootstrap_admin()

    @staticmethod
    def _resolve_path(settings: Settings) -> Path:
        configured = str(getattr(settings, "auth_store_path", None) or os.getenv("AUTH_STORE_PATH") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()

        runtime_path = getattr(settings, "runtime_config_path", None) or os.getenv("RUNTIME_CONFIG_PATH")
        if runtime_path:
            return (Path(runtime_path).expanduser().resolve().parent / "auth.json").resolve()

        base_root = getattr(settings, "output_root", None) or getattr(settings, "data_dir", None) or Path("./data")
        return (Path(base_root) / "_runtime_config" / "auth.json").expanduser().resolve()

    def snapshot(self) -> dict[str, Any]:
        payload = self._read_payload()
        users = payload.get("users") if isinstance(payload.get("users"), dict) else {}
        sessions = payload.get("sessions") if isinstance(payload.get("sessions"), dict) else {}
        pending_count = sum(1 for user in users.values() if user.get("status") == "pending")
        active_count = sum(1 for user in users.values() if user.get("status") == "active")
        return {
            "path": str(self.path),
            "user_count": len(users),
            "active_count": active_count,
            "pending_count": pending_count,
            "session_count": len(sessions),
            "updated_at": payload.get("updated_at"),
        }

    def request_account(
        self,
        *,
        username: str,
        password: str,
        display_name: str = "",
        email: str = "",
        reason: str = "",
    ) -> dict[str, Any]:
        username = self._normalize_username(username)
        self._validate_password(password)
        now = _utc_now()
        with self._lock:
            payload = self._read_payload()
            users = self._users(payload)
            if self._find_user_by_username(users, username):
                raise ValueError("이미 존재하는 계정입니다.")
            user_id = secrets.token_hex(12)
            users[user_id] = {
                "id": user_id,
                "username": username,
                "display_name": str(display_name or username).strip()[:100],
                "email": str(email or "").strip()[:200],
                "reason": str(reason or "").strip()[:1000],
                "password_hash": self._hash_password(password),
                "role": "user",
                "status": "pending",
                "created_at": now,
                "updated_at": now,
            }
            self._write_payload(payload)
            return self._public_user(users[user_id])

    def authenticate(self, username: str, password: str) -> dict[str, Any]:
        username = self._normalize_username(username)
        with self._lock:
            payload = self._read_payload()
            users = self._users(payload)
            user = self._find_user_by_username(users, username)
            if not user or not self._verify_password(password, str(user.get("password_hash") or "")):
                raise ValueError("아이디 또는 비밀번호가 맞지 않습니다.")
            if user.get("status") != "active":
                raise PermissionError("관리자 승인 후 로그인할 수 있습니다.")
            now = _utc_now()
            user["last_login_at"] = now
            user["updated_at"] = now
            self._write_payload(payload)
            return self._public_user(user)

    def create_session(self, user_id: str) -> AuthSession:
        session_id = secrets.token_urlsafe(32)
        days = self._session_days()
        expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()
        with self._lock:
            payload = self._read_payload()
            users = self._users(payload)
            user = users.get(user_id)
            if not user or user.get("status") != "active":
                raise ValueError("active user not found")
            sessions = self._sessions(payload)
            sessions[session_id] = {
                "id": session_id,
                "user_id": user_id,
                "created_at": _utc_now(),
                "expires_at": expires_at,
            }
            self._write_payload(payload)
            return AuthSession(session_id=session_id, user=self._public_user(user), expires_at=expires_at)

    def user_for_session(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        now = datetime.now(timezone.utc)
        with self._lock:
            payload = self._read_payload()
            sessions = self._sessions(payload)
            session = sessions.get(session_id)
            if not session:
                return None
            expires_at = _parse_dt(session.get("expires_at"))
            if expires_at is None or expires_at <= now:
                sessions.pop(session_id, None)
                self._write_payload(payload)
                return None
            users = self._users(payload)
            user = users.get(str(session.get("user_id") or ""))
            if not user or user.get("status") != "active":
                sessions.pop(session_id, None)
                self._write_payload(payload)
                return None
            return self._public_user(user)

    def delete_session(self, session_id: str | None) -> None:
        if not session_id:
            return
        with self._lock:
            payload = self._read_payload()
            sessions = self._sessions(payload)
            if session_id in sessions:
                sessions.pop(session_id, None)
                self._write_payload(payload)

    def list_users(self) -> list[dict[str, Any]]:
        payload = self._read_payload()
        users = self._users(payload)
        return sorted(
            (self._public_user(user) for user in users.values()),
            key=lambda item: (status_sort_key(str(item.get("status") or "")), str(item.get("created_at") or "")),
        )

    def approve_user(self, user_id: str, *, approved_by: str) -> dict[str, Any]:
        with self._lock:
            payload = self._read_payload()
            users = self._users(payload)
            user = users.get(user_id)
            if not user:
                raise KeyError("user not found")
            now = _utc_now()
            user["status"] = "active"
            user["approved_at"] = now
            user["approved_by"] = approved_by
            user["updated_at"] = now
            self._write_payload(payload)
            return self._public_user(user)

    def reject_user(self, user_id: str, *, rejected_by: str) -> dict[str, Any]:
        with self._lock:
            payload = self._read_payload()
            users = self._users(payload)
            sessions = self._sessions(payload)
            user = users.get(user_id)
            if not user:
                raise KeyError("user not found")
            now = _utc_now()
            user["status"] = "rejected"
            user["rejected_at"] = now
            user["rejected_by"] = rejected_by
            user["updated_at"] = now
            for session_id, session in list(sessions.items()):
                if session.get("user_id") == user_id:
                    sessions.pop(session_id, None)
            self._write_payload(payload)
            return self._public_user(user)

    def _ensure_bootstrap_admin(self) -> None:
        with self._lock:
            payload = self._read_payload()
            users = self._users(payload)
            if any(user.get("role") == "admin" for user in users.values()):
                return
            username = self._normalize_username(
                str(getattr(self.settings, "playground_admin_username", None) or os.getenv("PLAYGROUND_ADMIN_USERNAME") or "admin")
            )
            password = str(getattr(self.settings, "playground_admin_password", None) or os.getenv("PLAYGROUND_ADMIN_PASSWORD") or "admin123!")
            email = str(getattr(self.settings, "playground_admin_email", None) or os.getenv("PLAYGROUND_ADMIN_EMAIL") or "admin@local")
            now = _utc_now()
            user_id = secrets.token_hex(12)
            users[user_id] = {
                "id": user_id,
                "username": username,
                "display_name": "관리자",
                "email": email,
                "reason": "bootstrap admin",
                "password_hash": self._hash_password(password),
                "role": "admin",
                "status": "active",
                "created_at": now,
                "approved_at": now,
                "approved_by": "system",
                "updated_at": now,
                "bootstrap": True,
            }
            self._write_payload(payload)

    def _read_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"users": {}, "sessions": {}}
        except json.JSONDecodeError:
            return {"users": {}, "sessions": {}}
        if not isinstance(payload, dict):
            return {"users": {}, "sessions": {}}
        payload.setdefault("users", {})
        payload.setdefault("sessions", {})
        return payload

    def _write_payload(self, payload: dict[str, Any]) -> None:
        payload["updated_at"] = _utc_now()
        payload.setdefault("users", {})
        payload.setdefault("sessions", {})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        temp_path.replace(self.path)

    @staticmethod
    def _users(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        users = payload.setdefault("users", {})
        return users if isinstance(users, dict) else {}

    @staticmethod
    def _sessions(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
        sessions = payload.setdefault("sessions", {})
        return sessions if isinstance(sessions, dict) else {}

    @staticmethod
    def _find_user_by_username(users: dict[str, dict[str, Any]], username: str) -> dict[str, Any] | None:
        folded = username.casefold()
        for user in users.values():
            if str(user.get("username") or "").casefold() == folded:
                return user
        return None

    @staticmethod
    def _public_user(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": user.get("id"),
            "username": user.get("username"),
            "display_name": user.get("display_name") or user.get("username"),
            "email": user.get("email") or "",
            "reason": user.get("reason") or "",
            "role": user.get("role") or "user",
            "status": user.get("status") or "pending",
            "created_at": user.get("created_at"),
            "updated_at": user.get("updated_at"),
            "approved_at": user.get("approved_at"),
            "approved_by": user.get("approved_by"),
            "rejected_at": user.get("rejected_at"),
            "rejected_by": user.get("rejected_by"),
            "last_login_at": user.get("last_login_at"),
            "bootstrap": bool(user.get("bootstrap")),
        }

    @staticmethod
    def _normalize_username(username: str) -> str:
        cleaned = str(username or "").strip()
        if not _USERNAME_RE.match(cleaned):
            raise ValueError("아이디는 3-64자의 영문/숫자/._@-만 사용할 수 있습니다.")
        return cleaned

    @staticmethod
    def _validate_password(password: str) -> None:
        if len(str(password or "")) < 8:
            raise ValueError("비밀번호는 8자 이상이어야 합니다.")

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _HASH_ITERATIONS)
        return "pbkdf2_sha256${}${}${}".format(
            _HASH_ITERATIONS,
            base64.b64encode(salt).decode("ascii"),
            base64.b64encode(digest).decode("ascii"),
        )

    @staticmethod
    def _verify_password(password: str, encoded: str) -> bool:
        try:
            algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
            if algorithm != "pbkdf2_sha256":
                return False
            salt = base64.b64decode(salt_b64.encode("ascii"))
            expected = base64.b64decode(digest_b64.encode("ascii"))
            actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        except Exception:
            return False
        return hmac.compare_digest(actual, expected)

    def _session_days(self) -> int:
        raw = getattr(self.settings, "playground_session_days", None) or os.getenv("PLAYGROUND_SESSION_DAYS") or 7
        try:
            return max(1, min(int(raw), 60))
        except (TypeError, ValueError):
            return 7


def get_auth_store(settings: Settings | None = None) -> AuthStore:
    return AuthStore(settings)


def current_user_from_request(request: Request) -> dict[str, Any] | None:
    return get_auth_store(get_settings()).user_for_session(request.cookies.get(AUTH_COOKIE_NAME))


def require_authenticated_user(request: Request) -> dict[str, Any]:
    user = current_user_from_request(request)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
    return user


def require_admin_user(request: Request) -> dict[str, Any]:
    user = require_authenticated_user(request)
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin required")
    return user


def status_sort_key(value: str) -> int:
    return {"pending": 0, "active": 1, "rejected": 2}.get(value, 9)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
