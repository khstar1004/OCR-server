from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate the sealed-network carry-in bundle contents."
    )
    parser.add_argument(
        "--bundle-dir",
        default=".",
        help="Bundle root directory to validate.",
    )
    parser.add_argument(
        "--expected-vllm-image",
        default="a-cong-vllm-openai:chandra",
        help="Expected custom vLLM image tag inside compose/env docs.",
    )
    parser.add_argument(
        "--expected-vllm-tar",
        default="a-cong-vllm-openai_chandra.tar",
        help="Expected custom vLLM image tar filename.",
    )
    parser.add_argument(
        "--expected-model-type",
        default="qwen3_5",
        help="Expected HF model_type in news_models/chandra-ocr-2/config.json.",
    )
    return parser.parse_args()


def require_exists(path: Path) -> None:
    if not path.exists():
        raise SystemExit(f"Missing required path: {path}")


def require_contains(path: Path, needle: str) -> None:
    text = path.read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"Expected to find {needle!r} in {path}")


def main() -> None:
    args = parse_args()
    bundle_dir = Path(args.bundle_dir).expanduser().resolve()

    compose_path = bundle_dir / "docker-compose.defense-remote-ocr.yml"
    env_path = bundle_dir / ".env"
    model_config_path = bundle_dir / "news_models" / "chandra-ocr-2" / "config.json"
    app_tar_path = bundle_dir / "dist" / "a-cong-ocr_chandra.tar"
    vllm_tar_path = bundle_dir / "dist" / args.expected_vllm_tar
    validator_path = bundle_dir / "scripts" / "validate_defense_remote_ocr_bundle.py"
    vllm_build_script_path = bundle_dir / "scripts" / "build_vllm_offline_image.ps1"
    load_sh_path = bundle_dir / "scripts" / "load_offline_images.sh"
    start_sh_path = bundle_dir / "scripts" / "start_defense_remote_ocr.sh"

    for path in (
        compose_path,
        env_path,
        model_config_path,
        app_tar_path,
        vllm_tar_path,
        validator_path,
        vllm_build_script_path,
        load_sh_path,
        start_sh_path,
    ):
        require_exists(path)

    require_contains(compose_path, args.expected_vllm_image)
    require_contains(compose_path, "runtime: nvidia")
    require_contains(env_path, f"VLLM_IMAGE={args.expected_vllm_image}")

    compose_text = compose_path.read_text(encoding="utf-8")
    if "gpus:" in compose_text:
        raise SystemExit(f"Unsupported gpus compose key still present in {compose_path}")

    model_config = json.loads(model_config_path.read_text(encoding="utf-8"))
    model_type = str(model_config.get("model_type", "")).strip()
    if model_type != args.expected_model_type:
        raise SystemExit(
            f"Unexpected model_type in {model_config_path}: {model_type!r} != {args.expected_model_type!r}"
        )

    result = {
        "bundle_dir": str(bundle_dir),
        "expected_vllm_image": args.expected_vllm_image,
        "expected_vllm_tar": args.expected_vllm_tar,
        "model_type": model_type,
        "status": "ok",
    }
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
