from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_defense_bundle_defaults_use_custom_vllm_image() -> None:
    compose_text = (REPO_ROOT / "docker-compose.defense-remote-ocr.yml").read_text(encoding="utf-8")
    env_text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    loader_text = (REPO_ROOT / "scripts" / "load_offline_images.ps1").read_text(encoding="utf-8")
    loader_sh_text = (REPO_ROOT / "scripts" / "load_offline_images.sh").read_text(encoding="utf-8")
    builder_text = (REPO_ROOT / "scripts" / "build_vllm_offline_image.ps1").read_text(encoding="utf-8")
    start_sh_text = (REPO_ROOT / "scripts" / "start_defense_remote_ocr.sh").read_text(encoding="utf-8")

    assert "a-cong-vllm-openai:chandra" in compose_text
    assert "vllm/vllm-openai:v0.17.0" not in compose_text
    assert "runtime: nvidia" in compose_text
    assert "gpus:" not in compose_text
    assert "VLLM_IMAGE=a-cong-vllm-openai:chandra" in env_text
    assert "a-cong-vllm-openai_chandra.tar" in loader_text
    assert "check_vllm_qwen35_runtime.py" in loader_text
    assert "--runtime=nvidia" in loader_text
    assert "a-cong-vllm-openai_chandra.tar" in loader_sh_text
    assert "check_vllm_qwen35_runtime.py" in loader_sh_text
    assert "--runtime=nvidia" in loader_sh_text
    assert "compose -f" in start_sh_text
    assert "Dockerfile.vllm" in builder_text


def test_bundle_validator_accepts_custom_vllm_bundle(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    (bundle_dir / "dist").mkdir(parents=True)
    (bundle_dir / "news_models" / "chandra-ocr-2").mkdir(parents=True)
    (bundle_dir / "scripts").mkdir(parents=True)

    (bundle_dir / "docker-compose.defense-remote-ocr.yml").write_text(
        "services:\n  vllm-ocr:\n    image: a-cong-vllm-openai:chandra\n    runtime: nvidia\n",
        encoding="utf-8",
    )
    (bundle_dir / ".env").write_text("VLLM_IMAGE=a-cong-vllm-openai:chandra\n", encoding="utf-8")
    (bundle_dir / "dist" / "a-cong-ocr_chandra.tar").write_bytes(b"app")
    (bundle_dir / "dist" / "a-cong-vllm-openai_chandra.tar").write_bytes(b"vllm")
    (bundle_dir / "scripts" / "validate_defense_remote_ocr_bundle.py").write_text("# stub\n", encoding="utf-8")
    (bundle_dir / "scripts" / "build_vllm_offline_image.ps1").write_text("# stub\n", encoding="utf-8")
    (bundle_dir / "scripts" / "load_offline_images.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (bundle_dir / "scripts" / "start_defense_remote_ocr.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    (bundle_dir / "news_models" / "chandra-ocr-2" / "config.json").write_text(
        '{"model_type":"qwen3_5"}',
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "validate_defense_remote_ocr_bundle.py"),
            "--bundle-dir",
            str(bundle_dir),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert '"status": "ok"' in result.stdout
