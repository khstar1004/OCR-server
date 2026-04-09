from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import os


def _env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped or default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path(value: str) -> Path:
    return Path(value).expanduser().resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    watch_dir: Path
    data_dir: Path
    poll_interval_sec: float
    stable_scan_count: int
    llm_base_url: str | None
    llm_model: str | None
    llm_api_key: str | None
    llm_timeout_sec: float
    target_api_base_url: str | None
    target_api_token: str | None
    target_api_timeout_sec: float
    auto_deliver: bool
    delivery_retry_max: int
    database_path: Path

    @classmethod
    def from_env(cls) -> Settings:
        cwd = Path.cwd()
        data_dir = _resolve_path(_env_str("DATA_DIR", str(cwd / "data")) or str(cwd / "data"))
        watch_dir_value = _env_str("WATCH_DIR")
        watch_dir = _resolve_path(watch_dir_value) if watch_dir_value else data_dir / "watch"
        return cls(
            watch_dir=watch_dir,
            data_dir=data_dir,
            poll_interval_sec=_env_float("POLL_INTERVAL_SEC", 1.0),
            stable_scan_count=max(1, _env_int("STABLE_SCAN_COUNT", 2)),
            llm_base_url=_env_str("LLM_BASE_URL"),
            llm_model=_env_str("LLM_MODEL"),
            llm_api_key=_env_str("LLM_API_KEY"),
            llm_timeout_sec=_env_float("LLM_TIMEOUT_SEC", 30.0),
            target_api_base_url=_env_str("TARGET_API_BASE_URL"),
            target_api_token=_env_str("TARGET_API_TOKEN"),
            target_api_timeout_sec=_env_float("TARGET_API_TIMEOUT_SEC", 30.0),
            auto_deliver=_env_bool("AUTO_DELIVER", False),
            delivery_retry_max=max(0, _env_int("DELIVERY_RETRY_MAX", 3)),
            database_path=data_dir / "app.sqlite3",
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.watch_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
