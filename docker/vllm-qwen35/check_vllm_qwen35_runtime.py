from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
from typing import Any


QWEN_COMPAT_MODEL_TYPES = (
    "qwen2",
    "qwen2_5",
    "qwen2_vl",
    "qwen2_5_vl",
    "qwen2_5_text",
    "qwen3",
    "qwen3_5",
    "qwen3_5_text",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate that the vLLM runtime can load the configured Chandra model family."
    )
    parser.add_argument(
        "--model-dir",
        help="Optional local model directory to validate with AutoConfig/AutoProcessor and vLLM config loading.",
    )
    parser.add_argument(
        "--expect-model-type",
        default=None,
        help="Optional expected top-level model_type in config.json.",
    )
    return parser.parse_args()


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def nested_model_types(config_json: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("model_type",):
        value = str(config_json.get(key) or "").strip()
        if value:
            values.append(value)
    for section_name in ("text_config", "vision_config"):
        section = config_json.get(section_name)
        if isinstance(section, dict):
            value = str(section.get("model_type") or "").strip()
            if value:
                values.append(value)
    return sorted(set(values))


def model_file_summary(model_dir: Path) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    processor_config = model_dir / "processor_config.json"
    preprocessor_config = model_dir / "preprocessor_config.json"
    tokenizer_config = model_dir / "tokenizer_config.json"
    if processor_config.is_file():
        data = load_json(processor_config)
        image_processor = data.get("image_processor")
        video_processor = data.get("video_processor")
        summary["processor_class"] = data.get("processor_class")
        if isinstance(image_processor, dict):
            summary["image_processor_type"] = image_processor.get("image_processor_type")
        if isinstance(video_processor, dict):
            summary["video_processor_type"] = video_processor.get("video_processor_type")
    if preprocessor_config.is_file():
        data = load_json(preprocessor_config)
        summary["preprocessor_image_processor_type"] = data.get("image_processor_type")
    if tokenizer_config.is_file():
        data = load_json(tokenizer_config)
        summary["tokenizer_class"] = data.get("tokenizer_class")
    return summary


def main() -> None:
    args = parse_args()

    from transformers import AutoConfig, AutoProcessor, AutoTokenizer
    from transformers.models.auto.configuration_auto import CONFIG_MAPPING
    from vllm.transformers_utils.config import get_config

    versions = {
        "vllm": package_version("vllm"),
        "transformers": package_version("transformers"),
        "huggingface_hub": package_version("huggingface_hub"),
        "tokenizers": package_version("tokenizers"),
        "accelerate": package_version("accelerate"),
    }
    print(json.dumps({"versions": versions}, ensure_ascii=True))
    compat = {model_type: (model_type in CONFIG_MAPPING) for model_type in QWEN_COMPAT_MODEL_TYPES}
    print(json.dumps({"qwen_compat_mapping": compat}, ensure_ascii=True))

    if not args.model_dir:
        if not args.expect_model_type:
            print(json.dumps({"status": "runtime-version-ok"}, ensure_ascii=True))
            return
        if args.expect_model_type not in CONFIG_MAPPING:
            available_sample = sorted(str(key) for key in CONFIG_MAPPING.keys())[:20]
            raise SystemExit(
                f"transformers does not recognize {args.expect_model_type!r}. "
                f"Sample known model types: {available_sample}"
            )
        print(json.dumps({"status": "mapping-ok", "model_type": args.expect_model_type}, ensure_ascii=True))
        return

    model_dir = Path(args.model_dir).expanduser().resolve()
    if not model_dir.is_dir():
        raise SystemExit(f"Model directory not found: {model_dir}")

    config_path = model_dir / "config.json"
    if not config_path.is_file():
        raise SystemExit(f"config.json not found: {config_path}")

    config_json = load_json(config_path)
    model_type = str(config_json.get("model_type", "")).strip()
    model_types = nested_model_types(config_json)
    if args.expect_model_type and model_type != args.expect_model_type:
        raise SystemExit(
            f"Unexpected model_type in {config_path}: {model_type!r} != {args.expect_model_type!r}"
        )
    missing_model_types = [value for value in model_types if value not in CONFIG_MAPPING]
    if missing_model_types:
        auto_map = config_json.get("auto_map") or {}
        print(
            json.dumps(
                {
                    "status": "model-type-not-in-transformers-mapping",
                    "model_type": model_type,
                    "missing_model_types": missing_model_types,
                    "auto_map_keys": sorted(str(key) for key in auto_map.keys()) if isinstance(auto_map, dict) else [],
                    "trust_remote_code": True,
                },
                ensure_ascii=True,
            )
        )

    hf_config = AutoConfig.from_pretrained(str(model_dir), trust_remote_code=True)
    processor = AutoProcessor.from_pretrained(str(model_dir), trust_remote_code=True)
    tokenizer = AutoTokenizer.from_pretrained(str(model_dir), trust_remote_code=True)
    vllm_config = get_config(str(model_dir), trust_remote_code=True)

    result = {
        "status": "model-load-ok",
        "model_dir": str(model_dir),
        "model_type": model_type,
        "all_model_types": model_types,
        "model_file_summary": model_file_summary(model_dir),
        "hf_config_class": type(hf_config).__name__,
        "processor_class": type(processor).__name__,
        "tokenizer_class": type(tokenizer).__name__,
        "vllm_config_class": type(vllm_config).__name__,
    }
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
