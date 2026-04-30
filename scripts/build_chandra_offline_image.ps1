param(
    [string]$ImageTag = "a-cong-ocr:chandra",
    [string]$ArchivePath = ".\\dist\\a-cong-ocr_chandra.tar",
    [string]$ModelId = "datalab-to/chandra-ocr-2",
    [string]$DockerfilePath = "Dockerfile",
    [switch]$SkipManifest,
    [switch]$SkipArchiveHash
)

$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
$archiveFullPath = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $ArchivePath))
$archiveDir = Split-Path -Parent $archiveFullPath
$buildStartedUtc = (Get-Date).ToUniversalTime().ToString("o")

function Get-GitValue {
    param([string[]]$Arguments)

    try {
        $output = & git @Arguments 2>$null
        if ($LASTEXITCODE -eq 0) {
            return ($output -join "`n").Trim()
        }
    }
    catch {
        return $null
    }

    return $null
}

$gitCommit = Get-GitValue -Arguments @("rev-parse", "HEAD")
$gitShortCommit = Get-GitValue -Arguments @("rev-parse", "--short", "HEAD")
$gitStatus = Get-GitValue -Arguments @("status", "--short")
$gitDiffStat = Get-GitValue -Arguments @("diff", "--stat")
$gitChangedFilesRaw = Get-GitValue -Arguments @("diff", "--name-only")
$buildVersion = if ($gitShortCommit) { $gitShortCommit } else { "local" }
if ($gitStatus) {
    $buildVersion = "$buildVersion-dirty"
}
$gitChangedFiles = @()
if ($gitChangedFilesRaw) {
    $gitChangedFiles = @(($gitChangedFilesRaw -split "`n") | Where-Object { $_ })
}

if (-not (Test-Path $archiveDir)) {
    New-Item -ItemType Directory -Path $archiveDir | Out-Null
}

Push-Location $repoRoot
try {
    docker build `
        -f $DockerfilePath `
        --build-arg PRELOAD_CHANDRA=true `
        --build-arg CHANDRA_MODEL_ID=$ModelId `
        --build-arg ACONG_BUILD_VERSION=$buildVersion `
        --build-arg ACONG_BUILD_DATE=$buildStartedUtc `
        -t $ImageTag `
        .

    docker save -o $archiveFullPath $ImageTag

    if (-not $SkipManifest) {
        $archiveItem = Get-Item -LiteralPath $archiveFullPath
        $archiveSha256 = $null
        if (-not $SkipArchiveHash) {
            $archiveSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $archiveFullPath).Hash.ToLowerInvariant()
        }

        $manifestPath = "$archiveFullPath.manifest.json"
        $manifest = [ordered]@{
            image_tag = $ImageTag
            archive_name = $archiveItem.Name
            archive_path = $archiveFullPath
            archive_size_bytes = $archiveItem.Length
            archive_sha256 = $archiveSha256
            model_id = $ModelId
            dockerfile = $DockerfilePath
            preload_chandra = $true
            build_version = $buildVersion
            build_started_utc = $buildStartedUtc
            build_finished_utc = (Get-Date).ToUniversalTime().ToString("o")
            git_commit = $gitCommit
            git_status_short = $gitStatus
            git_diff_stat = $gitDiffStat
            git_changed_files = $gitChangedFiles
            api_capabilities = [ordered]@{
                endpoints = @(
                    "/api/v1/ocr/image",
                    "/api/v1/ocr/pdf",
                    "/api/v1/ocr",
                    "/api/v1/marker",
                    "/api/v1/requests",
                    "/api/v1/jobs/{job_id}/news-payload",
                    "/api/v1/jobs/{job_id}/deliver"
                )
                features = @(
                    "chandra_ocr",
                    "datalab_marker_compat",
                    "page_range",
                    "marker_modes",
                    "multi_output_format",
                    "request_runtime_metadata",
                    "request_retention_cleanup",
                    "national_assembly_payload_validation"
                )
            }
            validation_commands = @(
                ".\.venv\Scripts\python.exe -m pytest tests",
                "python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
            )
        }
        $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
        Write-Host "Wrote app image manifest: $manifestPath"
    }
}
finally {
    Pop-Location
}
