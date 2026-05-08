param(
    [string]$BundleDir = ".\\dist\\defense-k8s-public-ocr-carry-in",
    [string]$ImageDistSource = ".\\dist\\defense-k8s-existing-vllm-carry-in\\dist",
    [string]$AppUiTarName = "a-cong-ocr-app-ui_chandra.tar",
    [string]$PlaygroundTarName = "a-cong-ocr-playground_chandra.tar",
    [string]$OcrApiTarName = "a-cong-ocr-api_chandra.tar",
    [string]$VllmTarName = "a-cong-vllm-openai_chandra.tar",
    [switch]$Clean,
    [switch]$SkipModelCopy
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bundleRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BundleDir))
$imageDistRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $ImageDistSource))
$bundleDistDir = Join-Path $bundleRoot "dist"
$bundleDocsDir = Join-Path $bundleRoot "docs"
$bundleK8sDir = Join-Path $bundleRoot "k8s"
$bundleScriptsDir = Join-Path $bundleRoot "scripts"
$bundleModelsDir = Join-Path $bundleRoot "news_models"
$sourceModelDir = Join-Path $repoRoot "news_models\\chandra-ocr-2"

function Assert-UnderRepo {
    param([string]$PathValue)

    $fullPath = [System.IO.Path]::GetFullPath($PathValue)
    if (-not $fullPath.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to operate outside repo root: $fullPath"
    }
}

function Copy-FileIntoBundle {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Source file not found: $SourcePath"
    }

    $destinationDir = Split-Path -Parent $DestinationPath
    if ($destinationDir -and -not (Test-Path $destinationDir)) {
        New-Item -ItemType Directory -Force -Path $destinationDir | Out-Null
    }

    Copy-Item -LiteralPath $SourcePath -Destination $DestinationPath -Force
}

function Copy-DirectoryTree {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

    if (-not (Test-Path $SourcePath)) {
        throw "Source directory not found: $SourcePath"
    }

    Assert-UnderRepo -PathValue $DestinationPath
    New-Item -ItemType Directory -Force -Path $DestinationPath | Out-Null

    $robocopyCommand = Get-Command robocopy -ErrorAction SilentlyContinue
    $robocopyPath = if ($robocopyCommand) {
        $robocopyCommand.Source
    }
    else {
        "C:\\Windows\\System32\\robocopy.exe"
    }

    if (Test-Path $robocopyPath) {
        & $robocopyPath $SourcePath $DestinationPath /MIR /R:2 /W:1 /NFL /NDL /NJH /NJS /NC /NS | Out-Null
        $robocopyExitCode = $LASTEXITCODE
        if ($robocopyExitCode -gt 7) {
            throw "robocopy failed with exit code $robocopyExitCode"
        }
    }
    else {
        Copy-Item -LiteralPath (Join-Path $SourcePath "*") -Destination $DestinationPath -Recurse -Force
    }
}

function Get-RelativeBundlePath {
    param([string]$PathValue)

    $relativePath = $PathValue.Substring($bundleRoot.Length).TrimStart('\', '/')
    if (-not $relativePath) {
        return "."
    }

    return $relativePath.Replace('\', '/')
}

function Format-HashEntry {
    param([string]$PathValue)

    if (-not (Test-Path $PathValue)) {
        return $null
    }

    $item = Get-Item -LiteralPath $PathValue
    if ($item.PSIsContainer) {
        return $null
    }

    $hash = Get-FileHash -LiteralPath $PathValue -Algorithm SHA256
    return "$($hash.Hash.ToLowerInvariant())  $(Get-RelativeBundlePath -PathValue $PathValue)"
}

function Write-HashManifests {
    $bundleChandraModelDir = Join-Path $bundleModelsDir "chandra-ocr-2"
    $shortTargets = @(
        (Join-Path $bundleRoot "START_HERE_PUBLIC_K8S.txt"),
        (Join-Path $bundleRoot "CARRY_IN_MANIFEST.txt"),
        (Join-Path $bundleRoot "SERVICE_IMAGE_MAP.txt"),
        (Join-Path $bundleDocsDir "defense_k8s_nocodeaidev_runbook.md"),
        (Join-Path $bundleK8sDir "defense-remote-ocr.nocodeaidev.yaml"),
        (Join-Path $bundleScriptsDir "deploy_public_ocr_closed_network.sh"),
        (Join-Path $bundleScriptsDir "preflight_k8s_hami_public_ocr.sh"),
        (Join-Path $bundleScriptsDir "check_k8s_public_ocr.sh"),
        (Join-Path $bundleScriptsDir "replace_public_ocr_ui_image.sh"),
        (Join-Path $bundleScriptsDir "replace_public_ocr_app_image.sh"),
        (Join-Path $bundleScriptsDir "migrate_public_ocr_split_ui.sh"),
        (Join-Path $bundleScriptsDir "validate_vllm_image_offline.sh"),
        (Join-Path $bundleDistDir $AppUiTarName),
        (Join-Path $bundleDistDir $PlaygroundTarName),
        (Join-Path $bundleDistDir $OcrApiTarName),
        (Join-Path $bundleDistDir $VllmTarName),
        (Join-Path $bundleChandraModelDir "config.json"),
        (Join-Path $bundleChandraModelDir "model.safetensors")
    )

    $shortLines = foreach ($target in $shortTargets) {
        Format-HashEntry -PathValue $target
    }
    Set-Content -LiteralPath (Join-Path $bundleRoot "SHA256SUMS.txt") -Value ($shortLines | Where-Object { $_ })

    $fullLines = Get-ChildItem -LiteralPath $bundleRoot -Recurse -File |
        Where-Object { $_.Name -notin @("SHA256SUMS.txt", "SHA256SUMS_FULL.txt") } |
        Sort-Object FullName |
        ForEach-Object { Format-HashEntry -PathValue $_.FullName }
    Set-Content -LiteralPath (Join-Path $bundleRoot "SHA256SUMS_FULL.txt") -Value ($fullLines | Where-Object { $_ })
}

function Require-Text {
    param(
        [string]$PathValue,
        [string]$Needle
    )

    $text = Get-Content -LiteralPath $PathValue -Raw
    if (-not $text.Contains($Needle)) {
        throw "Expected to find '$Needle' in $PathValue"
    }
}

Assert-UnderRepo -PathValue $bundleRoot
if ($Clean -and (Test-Path $bundleRoot)) {
    $resolvedBundleRoot = (Resolve-Path $bundleRoot).Path
    Assert-UnderRepo -PathValue $resolvedBundleRoot
    Remove-Item -LiteralPath $resolvedBundleRoot -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $bundleRoot, $bundleDistDir, $bundleDocsDir, $bundleK8sDir, $bundleScriptsDir | Out-Null

Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\START_HERE_PUBLIC_K8S.txt") -DestinationPath (Join-Path $bundleRoot "START_HERE_PUBLIC_K8S.txt")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\defense_k8s_nocodeaidev_runbook.md") -DestinationPath (Join-Path $bundleDocsDir "defense_k8s_nocodeaidev_runbook.md")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\open_source_intake_list.md") -DestinationPath (Join-Path $bundleDocsDir "open_source_intake_list.md")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\open_source_intake_list.csv") -DestinationPath (Join-Path $bundleDocsDir "open_source_intake_list.csv")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "k8s\\defense-remote-ocr.nocodeaidev.yaml") -DestinationPath (Join-Path $bundleK8sDir "defense-remote-ocr.nocodeaidev.yaml")

foreach ($scriptName in @(
    "deploy_public_ocr_closed_network.sh",
    "deploy_public_ocr_existing_vllm.sh",
    "check_existing_vllm_k8s.sh",
    "preflight_k8s_hami_public_ocr.sh",
    "check_k8s_public_ocr.sh",
    "replace_public_ocr_ui_image.sh",
    "replace_public_ocr_app_image.sh",
    "migrate_public_ocr_split_ui.sh",
    "validate_vllm_image_offline.sh"
)) {
    Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "scripts\\$scriptName") -DestinationPath (Join-Path $bundleScriptsDir $scriptName)
}

foreach ($tarName in @($AppUiTarName, $PlaygroundTarName, $OcrApiTarName, $VllmTarName)) {
    Copy-FileIntoBundle -SourcePath (Join-Path $imageDistRoot $tarName) -DestinationPath (Join-Path $bundleDistDir $tarName)
}

if (-not $SkipModelCopy) {
    Copy-DirectoryTree -SourcePath $sourceModelDir -DestinationPath (Join-Path $bundleModelsDir "chandra-ocr-2")
}

$serviceImageMapLines = @(
    "Service -> Image -> Carry-in artifact",
    "app -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-app-ui:chandra -> dist/$AppUiTarName",
    "playground -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-playground:chandra -> dist/$PlaygroundTarName",
    "ocr-service -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-api:chandra -> dist/$OcrApiTarName",
    "vllm-ocr -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra -> dist/$VllmTarName"
)
Set-Content -LiteralPath (Join-Path $bundleRoot "SERVICE_IMAGE_MAP.txt") -Value $serviceImageMapLines

$manifestLines = @(
    "defense-k8s-public-ocr-carry-in",
    "",
    "Purpose:",
    "- Closed-network Kubernetes deployment for Army-OCR.",
    "- Public app/playground/OCR API are exposed through Ingress.",
    "- vLLM remains internal-only and uses Recreate strategy.",
    "",
    "Files:",
    "- START_HERE_PUBLIC_K8S.txt",
    "- docs/defense_k8s_nocodeaidev_runbook.md",
    "- k8s/defense-remote-ocr.nocodeaidev.yaml",
    "- scripts/deploy_public_ocr_closed_network.sh",
    "- scripts/deploy_public_ocr_existing_vllm.sh",
    "- scripts/check_existing_vllm_k8s.sh",
    "- scripts/preflight_k8s_hami_public_ocr.sh",
    "- scripts/check_k8s_public_ocr.sh",
    "- scripts/replace_public_ocr_ui_image.sh",
    "- scripts/migrate_public_ocr_split_ui.sh",
    "- scripts/validate_vllm_image_offline.sh",
    "- dist/$AppUiTarName",
    "- dist/$PlaygroundTarName",
    "- dist/$OcrApiTarName",
    "- dist/$VllmTarName",
    "- news_models/chandra-ocr-2/",
    "- SHA256SUMS.txt",
    "- SHA256SUMS_FULL.txt",
    "",
    "Critical checks:",
    "- Replace CHANGE_ME_STRONG_ADMIN_PASSWORD in k8s/defense-remote-ocr.nocodeaidev.yaml before deployment.",
    "- Keep VLLM_EXPECT_MODEL_TYPE=qwen3_5 for this model snapshot.",
    "- Keep a-cong-vllm-ocr internal-only; do not add an Ingress for vLLM.",
    "- Use scripts/deploy_public_ocr_closed_network.sh for first deployment."
)
Set-Content -LiteralPath (Join-Path $bundleRoot "CARRY_IN_MANIFEST.txt") -Value $manifestLines

Write-HashManifests

$k8sManifest = Join-Path $bundleK8sDir "defense-remote-ocr.nocodeaidev.yaml"
$modelConfig = Join-Path $bundleModelsDir "chandra-ocr-2\\config.json"
Require-Text -PathValue $k8sManifest -Needle "VLLM_EXPECT_MODEL_TYPE: `"qwen3_5`""
Require-Text -PathValue $k8sManifest -Needle "type: Recreate"
Require-Text -PathValue $k8sManifest -Needle "name: a-cong-ocr-playground"
Require-Text -PathValue $k8sManifest -Needle "PLAYGROUND_ADMIN_PASSWORD: `"CHANGE_ME_STRONG_ADMIN_PASSWORD`""
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$AppUiTarName"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$PlaygroundTarName"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$OcrApiTarName"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$VllmTarName"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "news_models/chandra-ocr-2/model.safetensors"
Require-Text -PathValue $modelConfig -Needle '"model_type": "qwen3_5"'

Write-Host "Prepared public k8s carry-in bundle at $bundleRoot"
