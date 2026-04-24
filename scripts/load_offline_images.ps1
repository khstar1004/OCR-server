param(
    [string]$AppTar = ".\\dist\\a-cong-ocr_chandra.tar",
    [string]$VllmTar = ".\\dist\\a-cong-vllm-openai_chandra.tar",
    [string]$VllmImageTag = "a-cong-vllm-openai:chandra",
    [switch]$SkipRuntimeValidation,
    [switch]$SkipGpuRuntimeValidation
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$dockerCommand = Get-Command docker -ErrorAction SilentlyContinue
$dockerPath = if ($dockerCommand) {
    $dockerCommand.Source
}
else {
    "C:\\Program Files\\Docker\\Docker\\resources\\bin\\docker.exe"
}

if (-not (Test-Path $dockerPath)) {
    throw "docker executable not found. Expected at: $dockerPath"
}

function Resolve-BundlePath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }

    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $PathValue))
}

function Test-DockerImageExists {
    param([string]$ImageRef)

    & $dockerPath image inspect $ImageRef *> $null
    return $LASTEXITCODE -eq 0
}

$resolvedAppTar = Resolve-BundlePath -PathValue $AppTar
$resolvedVllmTar = Resolve-BundlePath -PathValue $VllmTar

if (-not (Test-Path $resolvedAppTar)) {
    throw "App image tar not found: $resolvedAppTar"
}

& $dockerPath load -i $resolvedAppTar

if (Test-Path $resolvedVllmTar) {
    & $dockerPath load -i $resolvedVllmTar
}
else {
    throw "vLLM image tar not found: $resolvedVllmTar"
}

if (-not $SkipRuntimeValidation) {
    if (-not (Test-DockerImageExists -ImageRef $VllmImageTag)) {
        throw "vLLM image tag not found after load: $VllmImageTag"
    }

    $resolvedModelDir = Resolve-BundlePath -PathValue ".\\news_models\\chandra-ocr-2"
    if (-not (Test-Path $resolvedModelDir)) {
        throw "Model directory not found for runtime validation: $resolvedModelDir"
    }

    if (-not $SkipGpuRuntimeValidation) {
        & $dockerPath run --rm `
            --runtime=nvidia `
            --entrypoint python3 `
            $VllmImageTag `
            -c "import json, torch; info = {'cuda_available': torch.cuda.is_available(), 'device_count': torch.cuda.device_count(), 'device_name': torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}; print(json.dumps(info, ensure_ascii=True)); raise SystemExit(0 if torch.cuda.is_available() else 1)"
    }

    & $dockerPath run --rm `
        --entrypoint python3 `
        -v "${resolvedModelDir}:/models/chandra-ocr-2:ro" `
        $VllmImageTag `
        /opt/a-cong/check_vllm_qwen35_runtime.py `
        --expect-model-type qwen3_5 `
        --model-dir /models/chandra-ocr-2
}

Write-Host "Offline image load complete."
