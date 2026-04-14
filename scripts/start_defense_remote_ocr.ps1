param(
    [string]$ComposeFile = ".\\docker-compose.defense-remote-ocr.yml",
    [string]$EnvTemplate = ".\\.env.example",
    [switch]$ForceEnvCopy
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Resolve-RepoPath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }

    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $PathValue))
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

foreach ($dir in @("news_pdfs", "news_data", "model_cache")) {
    $target = Join-Path $repoRoot $dir
    New-Item -ItemType Directory -Force -Path $target | Out-Null
}

Push-Location $repoRoot
try {
    docker compose -f $resolvedComposeFile up -d
}
finally {
    Pop-Location
}

Write-Host "Defense remote-ocr stack started."
