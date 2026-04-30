from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.config import Settings, get_settings


@dataclass(frozen=True, slots=True)
class RuntimeSettingSpec:
    key: str
    env: str
    label: str
    group: str
    value_type: str
    default: Any
    description: str
    restart_required: bool = False
    min_value: float | None = None
    max_value: float | None = None
    choices: tuple[str, ...] = ()


RUNTIME_SETTING_SPECS: tuple[RuntimeSettingSpec, ...] = (
    RuntimeSettingSpec(
        key="ocr_service_url",
        env="OCR_SERVICE_URL",
        label="OCR API upstream",
        group="ocr_api",
        value_type="string",
        default="",
        description="app/playground가 호출할 OCR API base URL입니다. 비워두면 같은 프로세스의 로컬 OCR 엔진을 사용합니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_service_mode",
        env="OCR_SERVICE_MODE",
        label="OCR upstream 모드",
        group="ocr_api",
        value_type="string",
        default="native",
        choices=("native", "datalab_marker"),
        description="원격 OCR API 호출 방식을 선택합니다. army-ocr native API 또는 Datalab Marker 호환 API를 선택할 수 있습니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_service_timeout_sec",
        env="OCR_SERVICE_TIMEOUT_SEC",
        label="OCR 호출 timeout",
        group="ocr_api",
        value_type="float",
        default=180.0,
        min_value=0,
        max_value=3600,
        description="국회 앱이나 playground가 OCR API/worker 응답을 기다리는 최대 시간입니다. 0은 read timeout 없음입니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_service_poll_interval_sec",
        env="OCR_SERVICE_POLL_INTERVAL_SEC",
        label="OCR 결과 조회 간격",
        group="ocr_api",
        value_type="float",
        default=2.0,
        min_value=0.1,
        max_value=30,
        description="비동기 OCR 결과를 polling할 때 기다리는 간격입니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_service_marker_mode",
        env="OCR_SERVICE_MARKER_MODE",
        label="기본 OCR 모드",
        group="ocr_api",
        value_type="string",
        default="accurate",
        choices=("fast", "balanced", "accurate"),
        description="원격 marker API를 호출할 때 기본으로 사용할 OCR 모드입니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_max_concurrent_requests",
        env="OCR_MAX_CONCURRENT_REQUESTS",
        label="동시 OCR 추론 수",
        group="ocr_api",
        value_type="int",
        default=1,
        min_value=1,
        max_value=8,
        description="OCR 모델 진입 gate입니다. 새 요청부터 반영되며 단일 GPU/vLLM 기본값은 1입니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_retry_low_quality",
        env="OCR_RETRY_LOW_QUALITY",
        label="저품질 재시도",
        group="ocr_api",
        value_type="bool",
        default=True,
        description="OCR 결과 품질이 낮을 때 전처리 이미지를 만들어 한 번 더 시도합니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_quality_min_chars",
        env="OCR_QUALITY_MIN_CHARS",
        label="최소 글자 수",
        group="ocr_api",
        value_type="int",
        default=80,
        min_value=0,
        max_value=2000,
        description="저품질 OCR 판정에 사용하는 최소 글자 수 기준입니다.",
    ),
    RuntimeSettingSpec(
        key="ocr_quality_min_korean_ratio",
        env="OCR_QUALITY_MIN_KOREAN_RATIO",
        label="최소 한글 비율",
        group="ocr_api",
        value_type="float",
        default=0.35,
        min_value=0,
        max_value=1,
        description="저품질 OCR 판정에 사용하는 한글 비율 기준입니다.",
    ),
    RuntimeSettingSpec(
        key="pdf_render_dpi",
        env="PDF_RENDER_DPI",
        label="PDF 렌더링 DPI",
        group="ocr_api",
        value_type="int",
        default=300,
        min_value=72,
        max_value=600,
        description="PDF를 이미지로 바꿀 때 쓰는 해상도입니다. 새 요청부터 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="chandra_prompt_type",
        env="CHANDRA_PROMPT_TYPE",
        label="Chandra prompt",
        group="chandra",
        value_type="string",
        default="ocr_layout",
        description="Chandra OCR에 전달하는 prompt type입니다. 기본값은 layout OCR에 맞춘 ocr_layout입니다.",
    ),
    RuntimeSettingSpec(
        key="chandra_batch_size",
        env="CHANDRA_BATCH_SIZE",
        label="Chandra batch size",
        group="chandra",
        value_type="int",
        default=1,
        min_value=1,
        max_value=16,
        description="HF/local runner에서 한 번에 처리할 page image 수입니다. vLLM remote 호출은 보통 1을 권장합니다.",
    ),
    RuntimeSettingSpec(
        key="playground_default_max_pages",
        env="PLAYGROUND_DEFAULT_MAX_PAGES",
        label="Playground 기본 최대 쪽수",
        group="playground",
        value_type="int",
        default=10,
        min_value=1,
        max_value=200,
        description="Playground에서 사용자가 값을 바꾸지 않았을 때 적용할 기본 최대 쪽수입니다.",
    ),
    RuntimeSettingSpec(
        key="playground_max_upload_mb",
        env="PLAYGROUND_MAX_UPLOAD_MB",
        label="Playground 업로드 제한",
        group="playground",
        value_type="int",
        default=512,
        min_value=1,
        max_value=4096,
        description="Playground 파일 업로드 최대 크기입니다.",
    ),
    RuntimeSettingSpec(
        key="playground_operator_demo_url",
        env="PLAYGROUND_OPERATOR_DEMO_URL",
        label="운영 화면 URL",
        group="playground",
        value_type="string",
        default="",
        description="Playground 좌측 운영 화면 링크입니다.",
    ),
    RuntimeSettingSpec(
        key="playground_upstream_base_url",
        env="PLAYGROUND_UPSTREAM_BASE_URL",
        label="Playground upstream",
        group="playground",
        value_type="string",
        default="",
        description="분리 UI가 호출할 OCR API upstream base URL입니다. proxy 모드에서 새 요청부터 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="target_api_base_url",
        env="TARGET_API_BASE_URL",
        label="국회 API 주소",
        group="national_assembly",
        value_type="string",
        default="",
        description="국회 OCR 결과를 전송할 대상 API base URL입니다.",
    ),
    RuntimeSettingSpec(
        key="llm_base_url",
        env="LLM_BASE_URL",
        label="국회 후처리 LLM 주소",
        group="national_assembly",
        value_type="string",
        default="",
        description="국회 기사 관련도 판단/텍스트 정리에 사용할 OpenAI-compatible LLM base URL입니다. 비우면 휴리스틱만 사용합니다.",
    ),
    RuntimeSettingSpec(
        key="llm_model",
        env="LLM_MODEL",
        label="국회 후처리 LLM 모델",
        group="national_assembly",
        value_type="string",
        default="gpt-oss-20b",
        description="국회 기사 관련도 판단/텍스트 정리에 사용할 LLM 모델명입니다.",
    ),
    RuntimeSettingSpec(
        key="llm_timeout_sec",
        env="LLM_TIMEOUT_SEC",
        label="국회 후처리 LLM timeout",
        group="national_assembly",
        value_type="float",
        default=20.0,
        min_value=5,
        max_value=600,
        description="국회 후처리 LLM 호출 timeout입니다.",
    ),
    RuntimeSettingSpec(
        key="target_api_timeout_sec",
        env="TARGET_API_TIMEOUT_SEC",
        label="국회 API timeout",
        group="national_assembly",
        value_type="float",
        default=30.0,
        min_value=1,
        max_value=600,
        description="국회 API로 결과를 전송할 때 사용하는 timeout입니다.",
    ),
    RuntimeSettingSpec(
        key="callback_timeout_seconds",
        env="CALLBACK_TIMEOUT_SECONDS",
        label="콜백 timeout",
        group="national_assembly",
        value_type="int",
        default=30,
        min_value=1,
        max_value=600,
        description="작업 완료 callback URL로 결과를 보낼 때 사용하는 timeout입니다.",
    ),
    RuntimeSettingSpec(
        key="watch_poll_interval_sec",
        env="POLL_INTERVAL_SEC",
        label="감시 폴링 간격",
        group="national_assembly",
        value_type="float",
        default=1.0,
        min_value=0.1,
        max_value=60,
        description="국회 OCR 감시 폴더를 다시 스캔하는 간격입니다. 실행 중인 watcher의 다음 loop부터 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="watch_stable_scan_count",
        env="STABLE_SCAN_COUNT",
        label="안정화 스캔 횟수",
        group="national_assembly",
        value_type="int",
        default=2,
        min_value=1,
        max_value=20,
        description="파일 크기/mtime이 몇 번 연속 동일해야 OCR 작업으로 등록할지 정합니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_api_base",
        env="VLLM_API_BASE",
        label="vLLM API 주소",
        group="vllm",
        value_type="string",
        default="http://localhost:8000/v1",
        description="OCR 서비스가 호출할 vLLM OpenAI-compatible base URL입니다. 새 요청부터 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_model_name",
        env="VLLM_MODEL_NAME",
        label="vLLM 모델 이름",
        group="vllm",
        value_type="string",
        default="chandra-ocr-2",
        restart_required=True,
        description="vLLM served-model-name입니다. OCR client도 새 요청부터 읽지만, vLLM server의 served name은 vLLM 재시작 후 바뀝니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_model_path",
        env="VLLM_MODEL_PATH",
        label="vLLM 모델 경로",
        group="vllm",
        value_type="string",
        default="/models/chandra-ocr-2",
        restart_required=True,
        description="vLLM serve 첫 번째 인자인 로컬 모델 경로입니다. 저장 후 vLLM Pod/컨테이너 재시작 때 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_max_retries",
        env="MAX_VLLM_RETRIES",
        label="vLLM 재시도 횟수",
        group="vllm",
        value_type="int",
        default=6,
        min_value=0,
        max_value=20,
        description="vLLM 요청이 일시 실패했을 때 OCR runner가 재시도하는 횟수입니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_max_num_seqs",
        env="VLLM_MAX_NUM_SEQS",
        label="vLLM max num seqs",
        group="vllm",
        value_type="int",
        default=1,
        min_value=1,
        max_value=16,
        restart_required=True,
        description="vLLM 동시 sequence 수입니다. 단일 GPU 안정 운영 기본값은 1이며 저장 후 vLLM 재시작 때 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_max_model_len",
        env="VLLM_MAX_MODEL_LEN",
        label="vLLM max model len",
        group="vllm",
        value_type="int",
        default=18000,
        min_value=1024,
        max_value=65536,
        restart_required=True,
        description="vLLM 실행 인자입니다. 저장은 가능하지만 vLLM Pod/컨테이너 재시작 후 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_gpu_memory_utilization",
        env="VLLM_GPU_MEMORY_UTILIZATION",
        label="vLLM GPU memory",
        group="vllm",
        value_type="float",
        default=0.8,
        min_value=0.1,
        max_value=0.95,
        restart_required=True,
        description="vLLM GPU 메모리 사용률입니다. vLLM 재시작 후 적용됩니다.",
    ),
    RuntimeSettingSpec(
        key="vllm_mm_processor_kwargs",
        env="VLLM_MM_PROCESSOR_KWARGS",
        label="vLLM image pixel limit",
        group="vllm",
        value_type="string",
        default='{"min_pixels":3136,"max_pixels":6291456}',
        restart_required=True,
        description="vLLM --mm-processor-kwargs JSON 문자열입니다. 큰 이미지 OOM/품질 튜닝용이며 vLLM 재시작 후 적용됩니다.",
    ),
)

_SPECS_BY_KEY = {spec.key: spec for spec in RUNTIME_SETTING_SPECS}


class RuntimeConfigStore:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self.path = self._resolve_path(self.settings)
        self._lock = threading.RLock()

    @staticmethod
    def _resolve_path(settings: Settings) -> Path:
        configured = str(getattr(settings, "runtime_config_path", None) or os.getenv("RUNTIME_CONFIG_PATH") or "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        base_root = getattr(settings, "output_root", None) or getattr(settings, "data_dir", None) or Path("./data")
        return (Path(base_root) / "_runtime_config" / "settings.json").expanduser().resolve()

    def snapshot(self) -> dict[str, Any]:
        overrides = self.read_overrides()
        values: dict[str, Any] = {}
        specs: list[dict[str, Any]] = []
        for spec in RUNTIME_SETTING_SPECS:
            env_value = getattr(self.settings, spec.key, None)
            if env_value is None:
                env_value = os.getenv(spec.env, spec.default)
            has_override = spec.key in overrides
            effective = overrides.get(spec.key, env_value)
            values[spec.key] = effective
            specs.append(
                {
                    "key": spec.key,
                    "env": spec.env,
                    "label": spec.label,
                    "group": spec.group,
                    "type": spec.value_type,
                    "default": spec.default,
                    "env_value": env_value,
                    "value": effective,
                    "has_override": has_override,
                    "restart_required": spec.restart_required,
                    "min": spec.min_value,
                    "max": spec.max_value,
                    "choices": list(spec.choices),
                    "description": spec.description,
                }
            )
        return {
            "path": str(self.path),
            "updated_at": self._read_payload().get("updated_at"),
            "values": values,
            "overrides": overrides,
            "specs": specs,
        }

    def read_overrides(self) -> dict[str, Any]:
        payload = self._read_payload()
        values = payload.get("values")
        if not isinstance(values, dict):
            return {}
        normalized: dict[str, Any] = {}
        for key, value in values.items():
            if key not in _SPECS_BY_KEY:
                continue
            normalized[key] = self._coerce_value(_SPECS_BY_KEY[key], value)
        return normalized

    def save(self, values: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate_values(values)
        with self._lock:
            current = self.read_overrides()
            for key, value in normalized.items():
                if value is None or value == "":
                    current.pop(key, None)
                else:
                    current[key] = value
            payload = {
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "values": current,
            }
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
            temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
            temp_path.replace(self.path)
        return self.snapshot()

    def validate_values(self, values: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in values.items():
            if key not in _SPECS_BY_KEY:
                raise ValueError(f"unknown runtime setting: {key}")
            spec = _SPECS_BY_KEY[key]
            if value is None or value == "":
                normalized[key] = None
                continue
            normalized[key] = self._coerce_value(spec, value)
        return normalized

    def value(self, key: str, fallback: Any = None) -> Any:
        if key not in _SPECS_BY_KEY:
            return fallback
        overrides = self.read_overrides()
        if key in overrides:
            return overrides[key]
        value = getattr(self.settings, key, None)
        if value is not None:
            return value
        env_value = os.getenv(_SPECS_BY_KEY[key].env)
        if env_value is not None:
            return self._coerce_value(_SPECS_BY_KEY[key], env_value)
        return fallback if fallback is not None else _SPECS_BY_KEY[key].default

    def _read_payload(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _coerce_value(spec: RuntimeSettingSpec, value: Any) -> Any:
        if spec.value_type == "bool":
            if isinstance(value, bool):
                coerced = value
            else:
                text = str(value).strip().lower()
                if text in {"1", "true", "yes", "y", "on"}:
                    coerced = True
                elif text in {"0", "false", "no", "n", "off"}:
                    coerced = False
                else:
                    raise ValueError(f"{spec.key} must be a boolean")
        elif spec.value_type == "int":
            coerced = int(value)
        elif spec.value_type == "float":
            coerced = float(value)
        else:
            coerced = str(value).strip()

        if spec.choices and str(coerced) not in spec.choices:
            allowed = ", ".join(spec.choices)
            raise ValueError(f"{spec.key} must be one of: {allowed}")
        if isinstance(coerced, (int, float)):
            if spec.min_value is not None and coerced < spec.min_value:
                raise ValueError(f"{spec.key} must be >= {spec.min_value}")
            if spec.max_value is not None and coerced > spec.max_value:
                raise ValueError(f"{spec.key} must be <= {spec.max_value}")
        return coerced


def get_runtime_config_store(settings: Settings | None = None) -> RuntimeConfigStore:
    return RuntimeConfigStore(settings)


def runtime_config_value(key: str, fallback: Any = None, settings: Settings | None = None) -> Any:
    return get_runtime_config_store(settings).value(key, fallback)
