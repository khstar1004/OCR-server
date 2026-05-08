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

function Assert-DefensePassword {
    param([string]$Value)

    $password = if ($null -eq $Value) { "" } else { $Value.Trim() }
    $blocked = @("admin123!", "roqkfrhk1!", "CHANGE_ME_STRONG_ADMIN_PASSWORD", "CHANGE_ME", "")
    if ($blocked -contains $password -or $password.StartsWith("CHANGE_ME", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "PLAYGROUND_ADMIN_PASSWORD must be changed in .env before starting the defense-network stack."
    }
    if ($password.Length -lt 12) {
        throw "PLAYGROUND_ADMIN_PASSWORD must be at least 12 characters for defense-network startup."
    }
}

function Assert-NoKnownPublicEndpoint {
    param(
        [string]$Key,
        [string]$Value
    )

    $endpoint = if ($null -eq $Value) { "" } else { $Value.Trim() }
    foreach ($blocked in @("121.153.7.193", "14.50.225.74", "183.107.244.138")) {
        if ($endpoint.Contains($blocked)) {
            throw "$Key still points to a known public/test endpoint ($blocked). Clear it or replace it with the approved internal address in .env."
        }
    }
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

$playgroundAdminPassword = Get-EnvValue -EnvFile $resolvedEnvFile -Key "PLAYGROUND_ADMIN_PASSWORD" -DefaultValue ""
Assert-DefensePassword -Value $playgroundAdminPassword
Assert-NoKnownPublicEndpoint -Key "TARGET_API_BASE_URL" -Value (Get-EnvValue -EnvFile $resolvedEnvFile -Key "TARGET_API_BASE_URL" -DefaultValue "")
Assert-NoKnownPublicEndpoint -Key "LLM_BASE_URL" -Value (Get-EnvValue -EnvFile $resolvedEnvFile -Key "LLM_BASE_URL" -DefaultValue "")

if (-not (Test-Path $resolvedModelDir)) {
    throw "Model directory not found: $resolvedModelDir"
}

$uiImageRef = Get-EnvValue -EnvFile $resolvedEnvFile -Key "UI_IMAGE" -DefaultValue "a-cong-ocr-ui:chandra"
$ocrImageRef = Get-EnvValue -EnvFile $resolvedEnvFile -Key "OCR_IMAGE" -DefaultValue "a-cong-ocr:chandra"
$vllmImageRef = Get-EnvValue -EnvFile $resolvedEnvFile -Key "VLLM_IMAGE" -DefaultValue "a-cong-vllm-openai:chandra"

& $dockerPath image inspect $uiImageRef *> $null
if ($LASTEXITCODE -ne 0) {
    throw "UI image tag not found locally: $uiImageRef"
}

& $dockerPath image inspect $ocrImageRef *> $null
if ($LASTEXITCODE -ne 0) {
    throw "OCR image tag not found locally: $ocrImageRef"
}

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
