param(
    [string]$AppTar = ".\\dist\\a-cong-ocr_chandra.tar",
    [string]$VllmTar = ".\\dist\\vllm-vllm-openai_v0.17.0.tar"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")

function Resolve-BundlePath {
    param([string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return $PathValue
    }

    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $PathValue))
}

$resolvedAppTar = Resolve-BundlePath -PathValue $AppTar
$resolvedVllmTar = Resolve-BundlePath -PathValue $VllmTar

if (-not (Test-Path $resolvedAppTar)) {
    throw "App image tar not found: $resolvedAppTar"
}

docker load -i $resolvedAppTar

if (Test-Path $resolvedVllmTar) {
    docker load -i $resolvedVllmTar
}
else {
    Write-Warning "vLLM image tar not found: $resolvedVllmTar"
}

Write-Host "Offline image load complete."
