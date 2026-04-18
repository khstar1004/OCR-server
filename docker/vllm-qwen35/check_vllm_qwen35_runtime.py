from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate that the vLLM runtime recognizes the qwen3_5 model family."
    )
    parser.add_argument(
        "--model-dir",
        help="Optional local model directory to validate with AutoConfig/AutoProcessor and vLLM config loading.",
    )
    parser.add_argument(
        "--expect-model-type",
        default="qwen3_5",
        help="Expected top-level model_type in config.json.",
    )
    return parser.parse_args()


def package_version(name: str) -> str:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return "missing"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    args = parse_args()

    from transformers import AutoConfig, AutoProcessor
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

    if args.expect_model_type not in CONFIG_MAPPING:
        available_sample = sorted(str(key) for key in CONFIG_MAPPING.keys())[:20]
        raise SystemExit(
            f"transformers does not recognize {args.expect_model_type!r}. "
            f"Sample known model types: {available_sample}"
        )

    if not args.model_dir:
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
    if model_type != args.expect_model_type:
        raise SystemExit(
            f"Unexpected model_type in {config_path}: {model_type!r} != {args.expect_model_type!r}"
        )

    hf_config = AutoConfig.from_pretrained(str(model_dir), trust_remote_code=True)
    processor = AutoProcessor.from_pretrained(str(model_dir), trust_remote_code=True)
    vllm_config = get_config(str(model_dir), trust_remote_code=True)

    result = {
        "status": "model-load-ok",
        "model_dir": str(model_dir),
        "model_type": model_type,
        "hf_config_class": type(hf_config).__name__,
        "processor_class": type(processor).__name__,
        "vllm_config_class": type(vllm_config).__name__,
    }
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
