from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_chandra_app_image_tar_builder_records_traceability_metadata() -> None:
    dockerfile_text = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    builder_text = (REPO_ROOT / "scripts" / "build_chandra_offline_image.ps1").read_text(encoding="utf-8")

    assert "ARG ACONG_BUILD_VERSION=local" in dockerfile_text
    assert "ARG ACONG_BUILD_DATE=unknown" in dockerfile_text
    assert 'org.opencontainers.image.title="Army-OCR App"' in dockerfile_text
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

    assert 'org.opencontainers.image.title="Army-OCR UI"' in dockerfile_text
    assert "app.playground_proxy:app" in builder_text
    assert "image_role = \"web-and-playground\"" in builder_text
    assert "torch" not in requirements_text
    assert "transformers" not in requirements_text
    assert "chandra" not in requirements_text
    assert "requirements.ui.txt" in preview_text
    assert "import fitz, pypdfium2, multipart" in preview_text
    assert "-m pip install -r $Requirements" in preview_text

    assert "a-cong-ocr-ui:chandra" in deploy_text
    assert "a-cong-ocr-app-ui:chandra" in deploy_text
    assert "a-cong-ocr-playground:chandra" in deploy_text
    assert "a-cong-ocr-api:chandra" in deploy_text
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
    assert "${DATA_DIR:-./news_data}:/data/runtime" in compose_text


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


def test_public_k8s_manifest_matches_nocodeaidev_hami_contract() -> None:
    k8s_text = (REPO_ROOT / "k8s" / "defense-remote-ocr.nocodeaidev.yaml").read_text(encoding="utf-8")

    assert "namespace: nocodeaidev" in k8s_text
    assert "host: nocodeaidev.army.mil" in k8s_text
    assert "host: nocodeaidev.army.mil:20443" not in k8s_text
    assert "ingressClassName: nginx" in k8s_text
    assert "storageClassName: local-path" in k8s_text
    assert "name: harbor-reg-cred" in k8s_text
    assert "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-app-ui:chandra" in k8s_text
    assert "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-playground:chandra" in k8s_text
    assert "nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-api:chandra" in k8s_text
    assert "imagePullPolicy: IfNotPresent" in k8s_text
    assert "imagePullPolicy: Always" not in k8s_text

    assert "ROOT_PATH: \"/a-cong-ocr\"" in k8s_text
    assert "value: /a-cong-ocr-api" in k8s_text
    assert "value: /a-cong-ocr-playground" in k8s_text
    assert "path: /a-cong-ocr(/|$)(.*)" in k8s_text
    assert "path: /a-cong-ocr-api(/|$)(.*)" in k8s_text
    assert "path: /a-cong-ocr-playground(/|$)(.*)" in k8s_text
    assert "nginx.ingress.kubernetes.io/x-forwarded-prefix: /a-cong-ocr" in k8s_text
    assert "nginx.ingress.kubernetes.io/x-forwarded-prefix: /a-cong-ocr-api" in k8s_text
    assert "nginx.ingress.kubernetes.io/x-forwarded-prefix: /a-cong-ocr-playground" in k8s_text

    assert "name: a-cong-vllm-ocr" in k8s_text
    assert "type: Recreate" in k8s_text
    assert "schedulerName: hami-scheduler" in k8s_text
    assert "hami.io/node-scheduler-policy: binpack" in k8s_text
    assert "hami.io/gpu-scheduler-policy: spread" in k8s_text
    assert "nvidia.com/vgpu-mode: hami-core" in k8s_text
    assert "nvidia.com/gpu: \"1\"" in k8s_text
    assert "nvidia.com/gpumem-percentage: \"30\"" in k8s_text
    assert "nvidia.com/gpucores: \"30\"" in k8s_text

    assert "VLLM_API_BASE: \"http://a-cong-vllm-ocr:5000/v1\"" in k8s_text
    assert "VLLM_EXPECT_MODEL_TYPE: \"qwen3_5\"" in k8s_text
    assert "VLLM_MAX_MODEL_LEN: \"16384\"" in k8s_text
    assert "VLLM_GPU_MEMORY_UTILIZATION: \"0.80\"" in k8s_text
    assert "VLLM_MM_PROCESSOR_KWARGS: \"{\\\"min_pixels\\\":3136,\\\"max_pixels\\\":6291456}\"" in k8s_text
    for flag in (
        "--trust-remote-code",
        "--port",
        "--max-model-len",
        "--gpu-memory-utilization",
        "--distributed-executor-backend",
        "--disable-custom-all-reduce",
        "--enforce-eager",
        "-O0",
        "--max-num-seqs",
    ):
        assert flag in k8s_text

    assert "name: a-cong-vllm-ocr\n  namespace: nocodeaidev\n  annotations:" not in k8s_text


def test_k8s_scripts_guard_current_field_failure_modes() -> None:
    deploy_text = (REPO_ROOT / "scripts" / "deploy_public_ocr_closed_network.sh").read_text(encoding="utf-8")
    replace_text = (REPO_ROOT / "scripts" / "replace_public_ocr_app_image.sh").read_text(encoding="utf-8")
    migrate_text = (REPO_ROOT / "scripts" / "migrate_public_ocr_split_ui.sh").read_text(encoding="utf-8")
    preflight_text = (REPO_ROOT / "scripts" / "preflight_k8s_hami_public_ocr.sh").read_text(encoding="utf-8")
    check_text = (REPO_ROOT / "scripts" / "check_k8s_public_ocr.sh").read_text(encoding="utf-8")
    k8s_prepare_text = (REPO_ROOT / "scripts" / "prepare_defense_k8s_public_ocr_carry_in.ps1").read_text(encoding="utf-8")
    existing_vllm_deploy_text = (REPO_ROOT / "scripts" / "deploy_public_ocr_existing_vllm.sh").read_text(encoding="utf-8")
    existing_vllm_check_text = (REPO_ROOT / "scripts" / "check_existing_vllm_k8s.sh").read_text(encoding="utf-8")
    existing_vllm_prepare_text = (REPO_ROOT / "scripts" / "prepare_defense_k8s_existing_vllm_carry_in.ps1").read_text(encoding="utf-8")

    assert "STAGE_MODEL_BEFORE_VLLM" in deploy_text
    assert "replicas: 0" in deploy_text
    assert "Keeping vLLM paused while replacing the model PVC contents" in deploy_text
    assert "list[str]" not in deploy_text
    assert "list[str]" not in migrate_text
    assert "a-cong-ocr:chandra-cssfix-20260429" in deploy_text
    assert "a-cong-ocr:chandra-cssfix-20260429" in replace_text
    assert "a-cong-ocr:chandra-cssfix-20260429" in migrate_text
    assert "SKIP_HARBOR_PUSH" in replace_text

    assert "Ingress host in YAML must not include a port" in preflight_text
    assert "Manifest must use Recreate strategy for a-cong-vllm-ocr" in preflight_text
    assert "Manifest must set VLLM_EXPECT_MODEL_TYPE=qwen3_5" in preflight_text
    assert "Manifest must set VLLM_MAX_MODEL_LEN=16384" in preflight_text
    assert "nvidia.com/gpumem-percentage is not exposed" in preflight_text

    assert "VLLM_EXPECT_MODEL_TYPE must be qwen3_5" in check_text
    assert "VLLM_MAX_MODEL_LEN must be 16384" in check_text
    assert "curl --noproxy '*'" in check_text

    assert "Rendering manifest without touching the existing a-cong-vllm-ocr Deployment" in existing_vllm_deploy_text
    assert "a-cong-ocr-app-ui_chandra.tar" in existing_vllm_deploy_text
    assert "a-cong-ocr-playground_chandra.tar" in existing_vllm_deploy_text
    assert "a-cong-ocr-api_chandra.tar" in existing_vllm_deploy_text
    assert 'SKIP_HARBOR_PUSH="${SKIP_HARBOR_PUSH:-1}"' in existing_vllm_deploy_text
    assert 'SKIP_PREFLIGHT="${SKIP_PREFLIGHT:-1}"' in existing_vllm_deploy_text
    assert "imagePullPolicy: IfNotPresent" in existing_vllm_deploy_text
    assert 'RUN_CAPACITY_CHECK="${RUN_CAPACITY_CHECK:-1}"' in existing_vllm_deploy_text
    assert "simulate_cluster_capacity \"before cleanup\"" in existing_vllm_deploy_text
    assert "ResourceQuota simulation" in existing_vllm_deploy_text
    assert 'REQUIRE_DOCKER_RUNTIME="${REQUIRE_DOCKER_RUNTIME:-1}"' in existing_vllm_deploy_text
    assert "check_runtime_contract" in existing_vllm_deploy_text
    assert "required PVCs are missing" in existing_vllm_deploy_text
    assert "kubectl apply --dry-run=server" in existing_vllm_deploy_text
    assert "scale_down_if_exists \"a-cong-ocr-service\"" in existing_vllm_deploy_text
    assert "wait_non_vllm_pods_gone" in existing_vllm_deploy_text
    assert "scale_up_and_wait \"a-cong-ocr-service\" \"ocr-service\"" in existing_vllm_deploy_text
    assert 'kind == "Deployment" and name == "a-cong-vllm-ocr"' in existing_vllm_deploy_text
    assert "rollout restart deploy/a-cong-vllm-ocr" not in existing_vllm_deploy_text
    assert "check_existing_vllm_health" in existing_vllm_deploy_text

    assert "VLLM_MAX_MODEL_LEN must be 16384" in existing_vllm_check_text
    assert "a-cong-vllm-ocr" in existing_vllm_check_text

    assert "defense-k8s-existing-vllm-carry-in" in existing_vllm_prepare_text
    assert "a-cong-ocr-app-ui_chandra.tar" in existing_vllm_prepare_text
    assert "a-cong-ocr-playground_chandra.tar" in existing_vllm_prepare_text
    assert "a-cong-ocr-api_chandra.tar" in existing_vllm_prepare_text
    assert "a-cong-vllm-openai_chandra.tar" not in existing_vllm_prepare_text
    assert "Not included:" in existing_vllm_prepare_text
    assert '"- news_models/chandra-ocr-2"' in existing_vllm_prepare_text

    assert "defense-k8s-public-ocr-carry-in" in k8s_prepare_text
    assert "a-cong-ocr-app-ui_chandra.tar" in k8s_prepare_text
    assert "a-cong-ocr-playground_chandra.tar" in k8s_prepare_text
    assert "a-cong-ocr-api_chandra.tar" in k8s_prepare_text
    assert "a-cong-vllm-openai_chandra.tar" in k8s_prepare_text
    assert "SHA256SUMS_FULL.txt" in k8s_prepare_text
    assert "VLLM_EXPECT_MODEL_TYPE: `\"qwen3_5`\"" in k8s_prepare_text
    assert "Assert-UnderRepo" in k8s_prepare_text
