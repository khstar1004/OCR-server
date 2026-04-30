from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


def normalize_root_path(value: str | None) -> str:
    raw_value = (value or "").strip()
    if not raw_value or raw_value == "/":
        return ""
    return "/" + raw_value.strip("/")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = Field(default="news-ocr", alias="APP_NAME")
    app_env: str = Field(default="development", alias="APP_ENV")
    root_path: str = Field(default="", alias="ROOT_PATH")
    api_prefix: str = Field(default="/api/v1", alias="API_PREFIX")
    database_url: str = Field(default="sqlite:///./news_ocr.db", alias="DATABASE_URL")
    auto_create_tables: bool = Field(default=True, alias="AUTO_CREATE_TABLES")
    input_root_host: str | None = Field(default=None, alias="INPUT_ROOT_HOST")
    output_root_host: str | None = Field(default=None, alias="OUTPUT_ROOT_HOST")
    models_root_host: str | None = Field(default=None, alias="MODELS_ROOT_HOST")
    input_root: Path = Field(default=Path("./news_pdfs"), alias="INPUT_ROOT")
    output_root: Path = Field(default=Path("./news_output"), alias="OUTPUT_ROOT")
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    models_root: Path = Field(default=Path("./news_models"), alias="MODELS_ROOT")
    pdf_render_dpi: int = Field(default=300, alias="PDF_RENDER_DPI")
    ocr_backend: str = Field(default="chandra", alias="OCR_BACKEND")
    ocr_offline: bool = Field(default=False, alias="OCR_OFFLINE")
    ocr_device: str = Field(default="cpu", alias="OCR_DEVICE")
    ocr_service_url: str | None = Field(default=None, alias="OCR_SERVICE_URL")
    ocr_service_mode: str = Field(default="native", alias="OCR_SERVICE_MODE")
    ocr_service_api_key: str | None = Field(default=None, alias="OCR_SERVICE_API_KEY")
    ocr_service_timeout_sec: float = Field(default=30.0, alias="OCR_SERVICE_TIMEOUT_SEC")
    ocr_service_poll_interval_sec: float = Field(default=2.0, alias="OCR_SERVICE_POLL_INTERVAL_SEC")
    ocr_service_marker_mode: str = Field(default="accurate", alias="OCR_SERVICE_MARKER_MODE")
    ocr_max_concurrent_requests: int = Field(default=1, alias="OCR_MAX_CONCURRENT_REQUESTS")
    ocr_retry_low_quality: bool = Field(default=True, alias="OCR_RETRY_LOW_QUALITY")
    ocr_quality_min_chars: int = Field(default=80, alias="OCR_QUALITY_MIN_CHARS")
    ocr_quality_min_korean_ratio: float = Field(default=0.35, alias="OCR_QUALITY_MIN_KOREAN_RATIO")
    poll_interval_sec: float = Field(default=1.0, alias="POLL_INTERVAL_SEC")
    stable_scan_count: int = Field(default=2, alias="STABLE_SCAN_COUNT")
    llm_base_url: str | None = Field(default="http://183.107.244.138:8000/v1", alias="LLM_BASE_URL")
    llm_model: str | None = Field(default="gpt-oss-20b", alias="LLM_MODEL")
    llm_api_key: str | None = Field(default=None, alias="LLM_API_KEY")
    llm_timeout_sec: float = Field(default=20.0, alias="LLM_TIMEOUT_SEC")
    chandra_method: str = Field(default="hf", alias="CHANDRA_METHOD")
    chandra_model_id: str = Field(default="datalab-to/chandra-ocr-2", alias="CHANDRA_MODEL_ID")
    chandra_model_dir: str | None = Field(default=None, alias="CHANDRA_MODEL_DIR")
    chandra_prompt_type: str = Field(default="ocr_layout", alias="CHANDRA_PROMPT_TYPE")
    chandra_batch_size: int = Field(default=1, alias="CHANDRA_BATCH_SIZE")
    chandra_device_map: str = Field(default="auto", alias="CHANDRA_DEVICE_MAP")
    chandra_dtype: str = Field(default="bfloat16", alias="CHANDRA_DTYPE")
    vllm_api_base: str | None = Field(default=None, alias="VLLM_API_BASE")
    vllm_api_key: str | None = Field(default=None, alias="VLLM_API_KEY")
    vllm_model_name: str | None = Field(default=None, alias="VLLM_MODEL_NAME")
    vllm_model_path: str | None = Field(default="/models/chandra-ocr-2", alias="VLLM_MODEL_PATH")
    vllm_max_retries: int = Field(default=6, alias="MAX_VLLM_RETRIES")
    vllm_max_model_len: int = Field(default=18000, alias="VLLM_MAX_MODEL_LEN")
    vllm_gpu_memory_utilization: float = Field(default=0.8, alias="VLLM_GPU_MEMORY_UTILIZATION")
    vllm_mm_processor_kwargs: str | None = Field(
        default='{"min_pixels":3136,"max_pixels":6291456}',
        alias="VLLM_MM_PROCESSOR_KWARGS",
    )
    vllm_max_num_seqs: int = Field(default=1, alias="VLLM_MAX_NUM_SEQS")
    callback_timeout_seconds: int = Field(default=30, alias="CALLBACK_TIMEOUT_SECONDS")
    target_api_base_url: str | None = Field(default=None, alias="TARGET_API_BASE_URL")
    target_api_token: str | None = Field(default=None, alias="TARGET_API_TOKEN")
    target_api_timeout_sec: float = Field(default=30.0, alias="TARGET_API_TIMEOUT_SEC")
    playground_upstream_base_url: str | None = Field(default=None, alias="PLAYGROUND_UPSTREAM_BASE_URL")
    playground_operator_demo_url: str | None = Field(default=None, alias="PLAYGROUND_OPERATOR_DEMO_URL")
    playground_default_max_pages: int = Field(default=10, alias="PLAYGROUND_DEFAULT_MAX_PAGES")
    playground_max_upload_mb: int = Field(default=512, alias="PLAYGROUND_MAX_UPLOAD_MB")
    runtime_config_path: Path | None = Field(default=None, alias="RUNTIME_CONFIG_PATH")
    auth_store_path: Path | None = Field(default=None, alias="AUTH_STORE_PATH")
    playground_admin_username: str = Field(default="admin", alias="PLAYGROUND_ADMIN_USERNAME")
    playground_admin_password: str = Field(default="admin123!", alias="PLAYGROUND_ADMIN_PASSWORD")
    playground_admin_email: str = Field(default="admin@local", alias="PLAYGROUND_ADMIN_EMAIL")
    playground_session_days: int = Field(default=7, alias="PLAYGROUND_SESSION_DAYS")

    @property
    def normalized_root_path(self) -> str:
        return normalize_root_path(self.root_path)

    @property
    def watch_poll_interval_sec(self) -> float:
        return self.poll_interval_sec

    @property
    def watch_stable_scan_count(self) -> int:
        return self.stable_scan_count

    def ensure_directories(self) -> None:
        self.input_root.mkdir(parents=True, exist_ok=True)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.models_root.mkdir(parents=True, exist_ok=True)

    def translate_source_dir(self, source_dir: str | None) -> str:
        if not source_dir:
            return str(self.input_root)
        host_root = (self.input_root_host or "").replace("\\", "/").rstrip("/")
        requested = source_dir.replace("\\", "/").rstrip("/")
        if host_root and requested.lower().startswith(host_root.lower()):
            suffix = requested[len(host_root) :].lstrip("/")
            translated = self.input_root / suffix if suffix else self.input_root
            return str(translated)
        return source_dir

    def input_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        if self.input_root_host:
            roots.append(Path(self.input_root_host).expanduser().resolve())
        roots.append(self.input_root.expanduser().resolve())
        db_derived = self._database_sibling_root("watch")
        if db_derived is not None:
            roots.append(db_derived)
        return self._dedupe_paths(roots)

    def output_roots(self) -> tuple[Path, ...]:
        roots: list[Path] = []
        if self.output_root_host:
            roots.append(Path(self.output_root_host).expanduser().resolve())
        roots.append(self.output_root.expanduser().resolve())
        legacy_output = self._legacy_data_output_root()
        if legacy_output is not None:
            roots.append(legacy_output)
        db_derived = self._database_sibling_root("output")
        if db_derived is not None:
            roots.append(db_derived)
        return self._dedupe_paths(roots)

    def _legacy_data_output_root(self) -> Path | None:
        legacy_root = (self.data_dir.expanduser().resolve() / "output").resolve()
        default_output_root = Path("./news_output").expanduser().resolve()
        if os.getenv("DATA_DIR") or self.output_root.expanduser().resolve() == default_output_root:
            return legacy_root
        return None

    def resolve_input_path(self, path_value: str | Path | None) -> Path | None:
        return self._resolve_path(
            path_value,
            roots=self.input_roots(),
            container_prefixes=("/data/watch",),
        )

    def resolve_output_path(self, path_value: str | Path | None) -> Path | None:
        return self._resolve_path(
            path_value,
            roots=self.output_roots(),
            container_prefixes=("/data/runtime/output",),
        )

    def _database_sibling_root(self, sibling_name: str) -> Path | None:
        db_path = self._sqlite_database_path()
        if db_path is None or db_path.parent.name.lower() != "db":
            return None
        return (db_path.parent.parent / sibling_name).resolve()

    def _sqlite_database_path(self) -> Path | None:
        if not self.database_url.startswith("sqlite:"):
            return None
        try:
            database = make_url(self.database_url).database
        except Exception:  # noqa: BLE001
            return None
        if not database:
            return None
        return Path(database).expanduser().resolve()

    def _resolve_path(
        self,
        path_value: str | Path | None,
        *,
        roots: tuple[Path, ...],
        container_prefixes: tuple[str, ...],
    ) -> Path | None:
        candidates = self._resolve_path_candidates(
            path_value,
            roots=roots,
            container_prefixes=container_prefixes,
        )
        if not candidates:
            return None
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _resolve_path_candidates(
        self,
        path_value: str | Path | None,
        *,
        roots: tuple[Path, ...],
        container_prefixes: tuple[str, ...],
    ) -> tuple[Path, ...]:
        if path_value is None:
            return ()
        raw_value = str(path_value).strip()
        if not raw_value:
            return ()

        source = Path(raw_value).expanduser()
        candidates: list[Path] = [source]
        if not source.is_absolute():
            candidates.extend(root / source for root in roots)

        normalized_value = raw_value.replace("\\", "/").rstrip("/")
        normalized_prefixes = [root.as_posix().rstrip("/") for root in roots]
        normalized_prefixes.extend(prefix.rstrip("/") for prefix in container_prefixes if prefix)
        for prefix in self._dedupe_strings(normalized_prefixes):
            if not prefix:
                continue
            if normalized_value == prefix:
                suffix = ""
            elif normalized_value.startswith(f"{prefix}/"):
                suffix = normalized_value[len(prefix) + 1 :]
            else:
                continue
            for root in roots:
                candidate = root / Path(suffix) if suffix else root
                candidates.append(candidate)
        return self._dedupe_paths(candidates)

    @staticmethod
    def _dedupe_paths(paths: list[Path]) -> tuple[Path, ...]:
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return tuple(unique)

    @staticmethod
    def _dedupe_strings(values: list[str]) -> tuple[str, ...]:
        unique: list[str] = []
        seen: set[str] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            unique.append(value)
        return tuple(unique)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
