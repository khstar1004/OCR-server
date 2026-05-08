param(
    [string]$BundleDir = ".\\dist\\defense-k8s-existing-vllm-carry-in",
    [string]$AppUiImage = "a-cong-ocr-app-ui:chandra",
    [string]$PlaygroundImage = "a-cong-ocr-playground:chandra",
    [string]$OcrApiImage = "a-cong-ocr-api:chandra",
    [string]$AppUiTarName = "a-cong-ocr-app-ui_chandra.tar",
    [string]$PlaygroundTarName = "a-cong-ocr-playground_chandra.tar",
    [string]$OcrApiTarName = "a-cong-ocr-api_chandra.tar",
    [switch]$Clean,
    [switch]$SkipBuild,
    [switch]$SkipImageExport
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$bundleRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BundleDir))
$bundleDistDir = Join-Path $bundleRoot "dist"
$bundleDocsDir = Join-Path $bundleRoot "docs"
$bundleK8sDir = Join-Path $bundleRoot "k8s"
$bundleScriptsDir = Join-Path $bundleRoot "scripts"
$uiDockerfile = Join-Path $repoRoot "Dockerfile.ui"
$apiDockerfile = Join-Path $repoRoot "Dockerfile"

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
    $shortTargets = @(
        (Join-Path $bundleRoot "START_HERE_EXISTING_VLLM_K8S.txt"),
        (Join-Path $bundleRoot "CARRY_IN_MANIFEST.txt"),
        (Join-Path $bundleRoot "SERVICE_IMAGE_MAP.txt"),
        (Join-Path $bundleDocsDir "defense_k8s_existing_vllm_carry_in.md"),
        (Join-Path $bundleK8sDir "defense-remote-ocr.nocodeaidev.yaml"),
        (Join-Path $bundleScriptsDir "deploy_public_ocr_existing_vllm.sh"),
        (Join-Path $bundleScriptsDir "check_existing_vllm_k8s.sh"),
        (Join-Path $bundleScriptsDir "check_k8s_public_ocr.sh"),
        (Join-Path $bundleScriptsDir "preflight_k8s_hami_public_ocr.sh"),
        (Join-Path $bundleDistDir $AppUiTarName),
        (Join-Path $bundleDistDir $PlaygroundTarName),
        (Join-Path $bundleDistDir $OcrApiTarName)
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

Push-Location $repoRoot
try {
    if (-not $SkipBuild) {
        & $dockerPath build -f $uiDockerfile -t $AppUiImage .
        & $dockerPath build -f $uiDockerfile -t $PlaygroundImage .
        & $dockerPath build -f $apiDockerfile --build-arg PRELOAD_CHANDRA=false -t $OcrApiImage .
    }

    if (-not $SkipImageExport) {
        & $dockerPath save -o (Join-Path $bundleDistDir $AppUiTarName) $AppUiImage
        & $dockerPath save -o (Join-Path $bundleDistDir $PlaygroundTarName) $PlaygroundImage
        & $dockerPath save -o (Join-Path $bundleDistDir $OcrApiTarName) $OcrApiImage
    }
}
finally {
    Pop-Location
}

Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\defense_k8s_existing_vllm_carry_in.md") -DestinationPath (Join-Path $bundleDocsDir "defense_k8s_existing_vllm_carry_in.md")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\defense_k8s_nocodeaidev_runbook.md") -DestinationPath (Join-Path $bundleDocsDir "defense_k8s_nocodeaidev_runbook.md")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\START_HERE_PUBLIC_K8S.txt") -DestinationPath (Join-Path $bundleDocsDir "START_HERE_PUBLIC_K8S.txt")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "k8s\\defense-remote-ocr.nocodeaidev.yaml") -DestinationPath (Join-Path $bundleK8sDir "defense-remote-ocr.nocodeaidev.yaml")

foreach ($scriptName in @(
    "deploy_public_ocr_existing_vllm.sh",
    "check_existing_vllm_k8s.sh",
    "check_k8s_public_ocr.sh",
    "preflight_k8s_hami_public_ocr.sh"
)) {
    Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "scripts\\$scriptName") -DestinationPath (Join-Path $bundleScriptsDir $scriptName)
}

$serviceImageMapLines = @(
    "Service -> Image -> Carry-in artifact",
    "app -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-app-ui:chandra -> dist/$AppUiTarName",
    "playground -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-playground:chandra -> dist/$PlaygroundTarName",
    "ocr-service -> nocodeaidev.army.mil:20443/nocodeaidev/a-cong-ocr-api:chandra -> dist/$OcrApiTarName",
    "vllm-ocr -> existing nocodeaidev.army.mil:20443/nocodeaidev/a-cong-vllm-openai:chandra -> not included"
)
Set-Content -LiteralPath (Join-Path $bundleRoot "SERVICE_IMAGE_MAP.txt") -Value $serviceImageMapLines

$startGuideLines = @(
    "START HERE - Existing vLLM K8s carry-in",
    "",
    "Use this bundle when a-cong-vllm-ocr is already healthy in namespace nocodeaidev.",
    "This bundle does not include the vLLM tar or the model folder.",
    "",
    "Run exactly this:",
    "",
    "  cd defense-k8s-existing-vllm-carry-in",
    "  chmod +x scripts/*.sh",
    "  ./scripts/deploy_public_ocr_existing_vllm.sh",
    "",
    "The script asks for PLAYGROUND_ADMIN_PASSWORD interactively.",
    "Use a site-specific password with at least 12 characters.",
    "",
    "Default behavior:",
    "- does not restart a-cong-vllm-ocr",
    "- does not require Harbor pull auth",
    "- docker-loads the 3 non-vLLM tar files",
    "- uses local images with imagePullPolicy IfNotPresent",
    "- simulates node pod slots, CPU/memory requests, and ResourceQuota",
    "- checks Kubernetes runtime and required PVC Bound state",
    "- scales old app/playground/OCR API pods down first",
    "- creates/updates the deployments with Recreate",
    "- scales service, playground, then app one by one",
    "",
    "Optional callback target:",
    "",
    "  TARGET_API_BASE_URL='http://<internal-target>:<PORT>/news' TARGET_API_TOKEN='<token>' ./scripts/deploy_public_ocr_existing_vllm.sh",
    "",
    "External URLs:",
    "- https://nocodeaidev.army.mil:20443/a-cong-ocr/demo/jobs",
    "- https://nocodeaidev.army.mil:20443/a-cong-ocr-api/health",
    "- https://nocodeaidev.army.mil:20443/a-cong-ocr-playground/"
)
Set-Content -LiteralPath (Join-Path $bundleRoot "START_HERE_EXISTING_VLLM_K8S.txt") -Value $startGuideLines

$manifestLines = @(
    "defense-k8s-existing-vllm-carry-in",
    "",
    "Purpose:",
    "- Update app/playground/OCR API while preserving the already-running vLLM deployment.",
    "",
    "Included tar files:",
    "- dist/$AppUiTarName",
    "- dist/$PlaygroundTarName",
    "- dist/$OcrApiTarName",
    "",
    "Not included:",
    "- vLLM image tar",
    "- news_models/chandra-ocr-2",
    "",
    "The deployment script skips Deployment/a-cong-vllm-ocr and recreates only:",
    "- a-cong-ocr-service",
    "- a-cong-ocr-playground",
    "- a-cong-ocr-app"
)
Set-Content -LiteralPath (Join-Path $bundleRoot "CARRY_IN_MANIFEST.txt") -Value $manifestLines

Write-HashManifests

$k8sManifest = Join-Path $bundleK8sDir "defense-remote-ocr.nocodeaidev.yaml"
Require-Text -PathValue $k8sManifest -Needle "a-cong-ocr-app-ui:chandra"
Require-Text -PathValue $k8sManifest -Needle "a-cong-ocr-playground:chandra"
Require-Text -PathValue $k8sManifest -Needle "a-cong-ocr-api:chandra"
Require-Text -PathValue $k8sManifest -Needle "a-cong-vllm-openai:chandra"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$AppUiTarName"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$PlaygroundTarName"
Require-Text -PathValue (Join-Path $bundleRoot "SHA256SUMS.txt") -Needle "dist/$OcrApiTarName"

Write-Host "Prepared existing-vLLM k8s carry-in bundle at $bundleRoot"
