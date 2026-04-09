param(
    [string]$ImageName = "a-cong-ocr:offline",
    [string]$TarPath = "a-cong-ocr-offline.tar"
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$resolvedTar = Join-Path $repoRoot $TarPath

docker build -t $ImageName -f (Join-Path $repoRoot "Dockerfile") $repoRoot
docker save -o $resolvedTar $ImageName

Write-Host "Saved offline image bundle to $resolvedTar"
