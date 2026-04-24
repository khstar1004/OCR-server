param(
    [string]$ComposeFile = ".\\docker-compose.defense-remote-ocr.yml",
    [string]$EnvTemplate = ".\\.env.example",
    [switch]$ForceEnvCopy
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

function Resolve-RepoPath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }

    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $PathValue))
}

function Get-EnvValue {
    param(
        [string]$EnvFile,
        [string]$Key,
        [string]$DefaultValue
    )

    if (-not (Test-Path $EnvFile)) {
        return $DefaultValue
    }

    foreach ($line in Get-Content $EnvFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed.StartsWith("$Key=")) {
            return $trimmed.Substring($Key.Length + 1).Trim()
        }
    }

    return $DefaultValue
}

$resolvedComposeFile = Resolve-RepoPath -PathValue $ComposeFile
$resolvedEnvTemplate = Resolve-RepoPath -PathValue $EnvTemplate
$resolvedEnvFile = Join-Path $repoRoot ".env"
$resolvedModelDir = Join-Path $repoRoot "news_models\\chandra-ocr-2"

if (-not (Test-Path $resolvedComposeFile)) {
    throw "Compose file not found: $resolvedComposeFile"
}

if (-not (Test-Path $resolvedEnvFile) -or $ForceEnvCopy) {
    if (-not (Test-Path $resolvedEnvTemplate)) {
        throw "Env template not found: $resolvedEnvTemplate"
    }
    Copy-Item $resolvedEnvTemplate $resolvedEnvFile -Force
}

if (-not (Test-Path $resolvedModelDir)) {
    throw "Model directory not found: $resolvedModelDir"
}

$vllmImageRef = Get-EnvValue -EnvFile $resolvedEnvFile -Key "VLLM_IMAGE" -DefaultValue "a-cong-vllm-openai:chandra"

& $dockerPath image inspect $vllmImageRef *> $null
if ($LASTEXITCODE -ne 0) {
    throw "vLLM image tag not found locally: $vllmImageRef"
}

& $dockerPath run --rm `
    --entrypoint python3 `
    -v "${resolvedModelDir}:/models/chandra-ocr-2:ro" `
    $vllmImageRef `
    /opt/a-cong/check_vllm_qwen35_runtime.py `
    --expect-model-type qwen3_5 `
    --model-dir /models/chandra-ocr-2

foreach ($dir in @("news_pdfs", "news_data", "model_cache")) {
    $target = Join-Path $repoRoot $dir
    New-Item -ItemType Directory -Force -Path $target | Out-Null
}

Push-Location $repoRoot
try {
    & $dockerPath compose -f $resolvedComposeFile config | Out-Null
    & $dockerPath compose -f $resolvedComposeFile up -d --wait
}
finally {
    Pop-Location
}

Write-Host "Defense remote-ocr stack started."
