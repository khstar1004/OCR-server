param(
    [string]$ImageName = "a-cong-ocr:chandra",
    [string]$ImageTarPath = "",
    [string]$ModelDir = ".\\news_models",
    [string]$DataDir = ".\\news_data",
    [string]$WatchDir = ".\\news_pdfs",
    [ValidateSet("7z", "zip")]
    [string]$ArchiveKind = "7z",
    [switch]$SkipModel,
    [switch]$SkipData,
    [switch]$SkipWatch
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Resolve-RepoPath {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }

    if ([System.IO.Path]::IsPathRooted($Value)) {
        return [System.IO.Path]::GetFullPath($Value)
    }

    return [System.IO.Path]::GetFullPath((Join-Path $repoRoot $Value))
}

function Get-PathBytes {
    param([string]$PathValue)

    if ([string]::IsNullOrWhiteSpace($PathValue) -or -not (Test-Path $PathValue)) {
        return [int64]0
    }

    $item = Get-Item -LiteralPath $PathValue
    if (-not $item.PSIsContainer) {
        return [int64]$item.Length
    }

    $result = Get-ChildItem -LiteralPath $PathValue -Recurse -Force -File -ErrorAction SilentlyContinue |
        Measure-Object -Property Length -Sum
    if ($null -eq $result.Sum) {
        return [int64]0
    }
    return [int64]$result.Sum
}

function Convert-SizeTextToBytes {
    param([string]$Value)

    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }

    $normalized = $Value.Trim().ToUpperInvariant().Replace("IB", "B")
    if ($normalized -notmatch '^([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?B)$') {
        return $null
    }

    $number = [double]$Matches[1]
    $unit = $Matches[2]
    $multiplier = switch ($unit) {
        "B" { 1 }
        "KB" { 1000 }
        "MB" { 1000 * 1000 }
        "GB" { 1000 * 1000 * 1000 }
        "TB" { 1000 * 1000 * 1000 * 1000 }
        "PB" { 1000 * 1000 * 1000 * 1000 * 1000 }
        default { throw "Unsupported size unit: $unit" }
    }

    return [int64][math]::Round($number * $multiplier)
}

function Get-DockerUniqueImageBytes {
    param([string]$ImageRef)

    $repo = $ImageRef
    $tag = "latest"
    if ($ImageRef.Contains(":")) {
        $parts = $ImageRef.Split(":", 2)
        $repo = $parts[0]
        $tag = $parts[1]
    }

    $dockerDf = docker system df -v 2>$null
    if ($LASTEXITCODE -ne 0) {
        return $null
    }

    foreach ($line in $dockerDf) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        if ($line.StartsWith("REPOSITORY")) {
            continue
        }
        if ($line.StartsWith("Containers space usage:")) {
            break
        }

        $columns = $line -split '\s{2,}'
        if ($columns.Count -lt 7) {
            continue
        }

        if ($columns[0] -ne $repo -or $columns[1] -ne $tag) {
            continue
        }

        $uniqueBytes = Convert-SizeTextToBytes $columns[6]
        if ($null -ne $uniqueBytes) {
            return $uniqueBytes
        }

        $sizeBytes = Convert-SizeTextToBytes $columns[4]
        if ($null -ne $sizeBytes) {
            return $sizeBytes
        }
    }

    return $null
}

function Get-ImageBytes {
    param(
        [string]$ImageRef,
        [string]$TarPath
    )

    $resolvedTar = Resolve-RepoPath $TarPath
    if ($resolvedTar -and (Test-Path $resolvedTar)) {
        return [PSCustomObject]@{
            Bytes = [int64](Get-Item -LiteralPath $resolvedTar).Length
            Source = "image tar"
            Path = $resolvedTar
        }
    }

    $dockerBytes = Get-DockerUniqueImageBytes $ImageRef
    if ($null -ne $dockerBytes) {
        return [PSCustomObject]@{
            Bytes = [int64]$dockerBytes
            Source = "docker system df -v unique size"
            Path = $null
        }
    }

    $inspectBytes = docker image inspect $ImageRef --format '{{.Size}}' 2>$null
    if ($LASTEXITCODE -eq 0 -and $inspectBytes -match '^\d+$') {
        return [PSCustomObject]@{
            Bytes = [int64]$inspectBytes
            Source = "docker image inspect size"
            Path = $null
        }
    }

    throw "Unable to resolve image size for '$ImageRef'. Export the image first and pass -ImageTarPath."
}

function Get-CompressionProfile {
    param([string]$Kind)

    switch ($Kind) {
        "7z" {
            return [PSCustomObject]@{
                Name = "7z realistic"
                ImageRatio = 0.75
                ModelRatio = 0.95
                DataRatio = 0.85
                WatchRatio = 0.99
            }
        }
        "zip" {
            return [PSCustomObject]@{
                Name = "zip realistic"
                ImageRatio = 0.82
                ModelRatio = 0.97
                DataRatio = 0.90
                WatchRatio = 1.00
            }
        }
        default {
            throw "Unsupported archive kind: $Kind"
        }
    }
}

function Format-GiB {
    param([int64]$Bytes)
    return "{0:N2}" -f ($Bytes / 1GB)
}

function Get-DiscCount {
    param(
        [int64]$Bytes,
        [int64]$CapacityBytes
    )

    if ($Bytes -le 0) {
        return 0
    }

    return [math]::Ceiling($Bytes / $CapacityBytes)
}

$imageInfo = Get-ImageBytes -ImageRef $ImageName -TarPath $ImageTarPath
$modelPath = Resolve-RepoPath $ModelDir
$dataPath = Resolve-RepoPath $DataDir
$watchPath = Resolve-RepoPath $WatchDir
$profile = Get-CompressionProfile $ArchiveKind

$entries = @(
    [PSCustomObject]@{
        Name = "docker-image"
        Path = $(if ($null -ne $imageInfo.Path) { $imageInfo.Path } else { $ImageName })
        RawBytes = [int64]$imageInfo.Bytes
        Ratio = $profile.ImageRatio
        Included = $true
        Notes = $imageInfo.Source
    }
)

if (-not $SkipModel) {
    $entries += [PSCustomObject]@{
        Name = "model-dir"
        Path = $modelPath
        RawBytes = Get-PathBytes $modelPath
        Ratio = $profile.ModelRatio
        Included = $true
        Notes = "safetensors usually compresses poorly"
    }
}

if (-not $SkipData) {
    $entries += [PSCustomObject]@{
        Name = "runtime-data"
        Path = $dataPath
        RawBytes = Get-PathBytes $dataPath
        Ratio = $profile.DataRatio
        Included = $true
        Notes = "PNG plus JSON mix"
    }
}

if (-not $SkipWatch) {
    $entries += [PSCustomObject]@{
        Name = "input-pdfs"
        Path = $watchPath
        RawBytes = Get-PathBytes $watchPath
        Ratio = $profile.WatchRatio
        Included = $true
        Notes = "PDF usually barely shrinks"
    }
}

$rows = foreach ($entry in $entries) {
    $estimatedBytes = [int64][math]::Round($entry.RawBytes * $entry.Ratio)
    [PSCustomObject]@{
        Artifact = $entry.Name
        RawGiB = Format-GiB $entry.RawBytes
        EstimatedCompressedGiB = Format-GiB $estimatedBytes
        RawBytes = $entry.RawBytes
        EstimatedCompressedBytes = $estimatedBytes
        Path = $entry.Path
        Notes = $entry.Notes
    }
}

$rawSum = ($rows | Measure-Object -Property RawBytes -Sum).Sum
$estimatedSum = ($rows | Measure-Object -Property EstimatedCompressedBytes -Sum).Sum
$totalRaw = [int64]$(if ($null -ne $rawSum) { $rawSum } else { 0 })
$totalEstimated = [int64]$(if ($null -ne $estimatedSum) { $estimatedSum } else { 0 })

$media = @(
    [PSCustomObject]@{ Name = "CD-700MB"; CapacityBytes = [int64]700000000 },
    [PSCustomObject]@{ Name = "DVD-4.7GB"; CapacityBytes = [int64]4700000000 },
    [PSCustomObject]@{ Name = "DVD-DL-8.5GB"; CapacityBytes = [int64]8500000000 },
    [PSCustomObject]@{ Name = "BD-25GB"; CapacityBytes = [int64]25000000000 }
)

Write-Host ""
Write-Host "Offline bundle estimate"
Write-Host "  image:   $ImageName"
Write-Host "  archive: $($profile.Name)"
Write-Host ""

$rows |
    Select-Object Artifact, RawGiB, EstimatedCompressedGiB, Path, Notes |
    Format-Table -AutoSize

Write-Host ""
Write-Host ("Raw total:                 {0} GiB" -f (Format-GiB $totalRaw))
Write-Host ("Estimated compressed total {0} GiB" -f (Format-GiB $totalEstimated))
Write-Host ""

Write-Host "Media count estimate"
foreach ($item in $media) {
    $rawCount = Get-DiscCount -Bytes $totalRaw -CapacityBytes $item.CapacityBytes
    $estimatedCount = Get-DiscCount -Bytes $totalEstimated -CapacityBytes $item.CapacityBytes
    Write-Host ("  {0,-14} raw={1,3} compressed={2,3}" -f $item.Name, $rawCount, $estimatedCount)
}

Write-Host ""
Write-Host "Notes"
Write-Host "  - For exact image transfer size, export first with docker save and rerun with -ImageTarPath."
Write-Host "  - The model directory is dominated by model.safetensors, so compression gains are small."
Write-Host "  - If CD-700MB count is greater than 1, optical transfer should use split volumes or larger media."
