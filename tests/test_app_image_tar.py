from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_chandra_app_image_tar_builder_records_traceability_metadata() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder_text = (REPO_ROOT / "scripts" / "build_chandra_offline_image.ps1").read_text(encoding="utf-8")

    assert "ARG ACONG_BUILD_VERSION=local" in dockerfile_text
    assert "ARG ACONG_BUILD_DATE=unknown" in dockerfile_text
    assert 'org.opencontainers.image.title="army-ocr App"' in dockerfile_text
    assert 'org.opencontainers.image.version="${ACONG_BUILD_VERSION}"' in dockerfile_text
    assert "--build-arg ACONG_BUILD_VERSION=$buildVersion" in builder_text
    assert "--build-arg ACONG_BUILD_DATE=$buildStartedUtc" in builder_text
    assert "$archiveFullPath.manifest.json" in builder_text
    assert "archive_size_bytes" in builder_text
    assert "archive_sha256" in builder_text
    assert "git_diff_stat" in builder_text
    assert "git_changed_files" in builder_text
    assert "api_capabilities" in builder_text
    assert "national_assembly_payload_validation" in builder_text
    assert "request_retention_cleanup" in builder_text
    assert "/api/v1/marker" in builder_text
    assert "/api/v1/jobs/{job_id}/news-payload" in builder_text
    assert "validation_commands" in builder_text
    assert "Get-FileHash -Algorithm SHA256" in builder_text
    assert "[switch]$SkipManifest" in builder_text
    assert "[switch]$SkipArchiveHash" in builder_text


def test_ui_image_tar_builder_and_k8s_scripts_support_split_images() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile.ui").read_text(encoding="utf-8")
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    requirements_text = (REPO_ROOT / "requirements.ui.txt").read_text(encoding="utf-8")
    builder_text = (REPO_ROOT / "scripts" / "build_ui_offline_image.ps1").read_text(encoding="utf-8")
    preview_text = (REPO_ROOT / "scripts" / "start_playground_preview.ps1").read_text(encoding="utf-8")
    deploy_text = (REPO_ROOT / "scripts" / "deploy_public_ocr_closed_network.sh").read_text(encoding="utf-8")
    replace_text = (REPO_ROOT / "scripts" / "replace_public_ocr_app_image.sh").read_text(encoding="utf-8")
    migrate_text = (REPO_ROOT / "scripts" / "migrate_public_ocr_split_ui.sh").read_text(encoding="utf-8")

    assert 'org.opencontainers.image.title="army-ocr UI"' in dockerfile_text
    assert "app.playground_proxy:app" in builder_text
    assert "image_role = \"web-and-playground\"" in builder_text
    assert "torch" not in requirements_text
    assert "transformers" not in requirements_text
    assert "chandra" not in requirements_text
    assert "requirements.ui.txt" in preview_text
    assert "import fitz, pypdfium2, multipart" in preview_text
    assert "-m pip install -r $Requirements" in preview_text

    assert "a-cong-ocr-ui:chandra" in deploy_text
    assert "OCR_API_IMAGE" in deploy_text
    assert "VLLM_IMAGE" in deploy_text
    assert "ensure_image_tag" in deploy_text
    assert "ensure_image_tag" in replace_text
    assert "UPDATE_OCR_API_IMAGE" in replace_text
    assert "UPDATE_VLLM_IMAGE" in replace_text
    assert "a-cong-vllm-ocr" in migrate_text
    assert "Done. vLLM was not restarted by this script." in migrate_text
    assert "a-cong-ocr-playground" in migrate_text

    assert "playground:" in compose_text
    assert "Dockerfile.ui" in compose_text
    assert "${UI_IMAGE:-a-cong-ocr-ui:chandra}" in compose_text
    assert "app.playground_proxy:app" in compose_text
    assert "${PLAYGROUND_HOST_PORT:-18109}:5000" in compose_text
    assert "AUTH_STORE_PATH" in compose_text
    assert "PLAYGROUND_ADMIN_PASSWORD" in compose_text


def test_vllm_runtime_settings_are_read_after_container_restart() -> None:
    compose_text = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    k8s_text = (REPO_ROOT / "k8s" / "defense-remote-ocr.nocodeaidev.yaml").read_text(encoding="utf-8")
    entrypoint_text = (REPO_ROOT / "docker" / "vllm-qwen35" / "run_vllm_serve_validated.sh").read_text(encoding="utf-8")

    assert "RUNTIME_CONFIG_PATH" in entrypoint_text
    assert "runtime_setting vllm_model_path" in entrypoint_text
    assert "runtime_setting vllm_max_num_seqs" in entrypoint_text
    assert "runtime_setting vllm_mm_processor_kwargs" in entrypoint_text
    assert "/data/runtime/runtime-config/settings.json" in compose_text
    assert "${DATA_DIR:-./news_data}:/data/runtime:ro" in compose_text
    assert "$(VLLM_MAX_NUM_SEQS)" in k8s_text
    assert "mountPath: /data/runtime" in k8s_text
