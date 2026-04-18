param(
    [string]$ImageTag = "a-cong-vllm-openai:chandra",
    [string]$ArchivePath = ".\\dist\\a-cong-vllm-openai_chandra.tar",
    [string]$DockerfilePath = "Dockerfile.vllm",
    [string]$ModelDir = ".\\news_models\\chandra-ocr-2",
    [string]$ExpectedModelType = "qwen3_5"
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
$archiveInputPath = if ([System.IO.Path]::IsPathRooted($ArchivePath)) {
    $ArchivePath
}
else {
    Join-Path $repoRoot $ArchivePath
}
$archiveFullPath = [System.IO.Path]::GetFullPath($archiveInputPath)
$archiveDir = Split-Path -Parent $archiveFullPath
$modelInputPath = if ([System.IO.Path]::IsPathRooted($ModelDir)) {
    $ModelDir
}
else {
    Join-Path $repoRoot $ModelDir
}
$resolvedModelDir = [System.IO.Path]::GetFullPath($modelInputPath)

if (-not (Test-Path $archiveDir)) {
    New-Item -ItemType Directory -Path $archiveDir | Out-Null
}

if (-not (Test-Path $resolvedModelDir)) {
    throw "Model directory not found: $resolvedModelDir"
}

Push-Location $repoRoot
try {
    & $dockerPath build `
        -f $DockerfilePath `
        -t $ImageTag `
        .

    & $dockerPath run --rm `
        --entrypoint python3 `
        -v "${resolvedModelDir}:/models/chandra-ocr-2:ro" `
        $ImageTag `
        /opt/a-cong/check_vllm_qwen35_runtime.py `
        --expect-model-type $ExpectedModelType `
        --model-dir /models/chandra-ocr-2

    & $dockerPath save -o $archiveFullPath $ImageTag
}
finally {
    Pop-Location
}
