param(
    [string]$ImageTag = "a-cong-ocr:chandra",
    [string]$ArchivePath = ".\\dist\\a-cong-ocr_chandra.tar",
    [string]$ModelId = "datalab-to/chandra-ocr-2",
    [string]$DockerfilePath = "Dockerfile"
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$archiveFullPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $ArchivePath))
$archiveDir = Split-Path -Parent $archiveFullPath

if (-not (Test-Path $archiveDir)) {
    New-Item -ItemType Directory -Path $archiveDir | Out-Null
}

Push-Location $repoRoot
try {
    docker build `
        -f $DockerfilePath `
        --build-arg PRELOAD_CHANDRA=true `
        --build-arg CHANDRA_MODEL_ID=$ModelId `
        -t $ImageTag `
        .

    docker save -o $archiveFullPath $ImageTag
}
finally {
    Pop-Location
}
