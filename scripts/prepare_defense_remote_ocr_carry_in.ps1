param(
    [string]$BundleDir = ".\\dist\\defense-remote-ocr-carry-in",
    [string]$AppImage = "a-cong-ocr:chandra",
    [string]$VllmImage = "a-cong-vllm-openai:chandra",
    [string]$AppTarName = "a-cong-ocr_chandra.tar",
    [string]$VllmTarName = "a-cong-vllm-openai_chandra.tar",
    [switch]$Clean,
    [switch]$RebuildAppImage,
    [switch]$RebuildVllmImage,
    [switch]$SkipImageExport,
    [switch]$SkipModelCopy
)

$ErrorActionPreference = "Stop"

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
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

$bundleRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot $BundleDir))
$bundleDistDir = Join-Path $bundleRoot "dist"
$bundleScriptsDir = Join-Path $bundleRoot "scripts"
$bundleDocsDir = Join-Path $bundleRoot "docs"
$bundleModelsDir = Join-Path $bundleRoot "news_models"
$bundleWatchDir = Join-Path $bundleRoot "news_pdfs"
$bundleDataDir = Join-Path $bundleRoot "news_data"
$bundleCacheDir = Join-Path $bundleRoot "model_cache"
$sourceModelDir = Join-Path $repoRoot "news_models\\chandra-ocr-2"
$vllmBuildScript = Join-Path $repoRoot "scripts\\build_vllm_offline_image.ps1"
$bundleValidatorScript = Join-Path $repoRoot "scripts\\validate_defense_remote_ocr_bundle.py"

function Copy-FileIntoBundle {
    param(
        [string]$SourcePath,
        [string]$DestinationPath
    )

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

function Format-ManifestEntry {
    param([string]$PathValue)

    if (-not (Test-Path $PathValue)) {
        return "$(Get-RelativeBundlePath -PathValue $PathValue) [missing]"
    }

    $item = Get-Item -LiteralPath $PathValue
    $sizeSuffix = ""

    if (-not $item.PSIsContainer) {
        $sizeSuffix = " ($($item.Length) bytes)"
    }

    return "$(Get-RelativeBundlePath -PathValue $PathValue)$sizeSuffix"
}

if ($Clean -and (Test-Path $bundleRoot)) {
    $resolvedBundleRoot = (Resolve-Path $bundleRoot).Path
    if (-not $resolvedBundleRoot.StartsWith($repoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to delete bundle path outside repo root: $resolvedBundleRoot"
    }

    Remove-Item -LiteralPath $resolvedBundleRoot -Recurse -Force
}

New-Item -ItemType Directory -Force -Path $bundleRoot | Out-Null
New-Item -ItemType Directory -Force -Path $bundleDistDir | Out-Null
New-Item -ItemType Directory -Force -Path $bundleScriptsDir | Out-Null
New-Item -ItemType Directory -Force -Path $bundleDocsDir | Out-Null
New-Item -ItemType Directory -Force -Path $bundleWatchDir | Out-Null
New-Item -ItemType Directory -Force -Path $bundleDataDir | Out-Null
New-Item -ItemType Directory -Force -Path $bundleCacheDir | Out-Null

Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docker-compose.defense-remote-ocr.yml") -DestinationPath (Join-Path $bundleRoot "docker-compose.defense-remote-ocr.yml")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot ".env.example") -DestinationPath (Join-Path $bundleRoot ".env.example")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot ".env.example") -DestinationPath (Join-Path $bundleRoot ".env")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "scripts\\load_offline_images.ps1") -DestinationPath (Join-Path $bundleScriptsDir "load_offline_images.ps1")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "scripts\\load_offline_images.sh") -DestinationPath (Join-Path $bundleScriptsDir "load_offline_images.sh")
Copy-FileIntoBundle -SourcePath $vllmBuildScript -DestinationPath (Join-Path $bundleScriptsDir "build_vllm_offline_image.ps1")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "scripts\\start_defense_remote_ocr.ps1") -DestinationPath (Join-Path $bundleScriptsDir "start_defense_remote_ocr.ps1")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "scripts\\start_defense_remote_ocr.sh") -DestinationPath (Join-Path $bundleScriptsDir "start_defense_remote_ocr.sh")
Copy-FileIntoBundle -SourcePath $bundleValidatorScript -DestinationPath (Join-Path $bundleScriptsDir "validate_defense_remote_ocr_bundle.py")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\defense_remote_ocr_bundle.md") -DestinationPath (Join-Path $bundleDocsDir "defense_remote_ocr_bundle.md")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\open_source_intake_list.md") -DestinationPath (Join-Path $bundleDocsDir "open_source_intake_list.md")
Copy-FileIntoBundle -SourcePath (Join-Path $repoRoot "docs\\open_source_intake_list.csv") -DestinationPath (Join-Path $bundleDocsDir "open_source_intake_list.csv")

if (-not $SkipModelCopy) {
    $bundleModelDir = Join-Path $bundleModelsDir "chandra-ocr-2"
    Copy-DirectoryTree -SourcePath $sourceModelDir -DestinationPath $bundleModelDir
}

if (-not $SkipImageExport) {
    Push-Location $repoRoot
    try {
        if ($RebuildAppImage) {
            & $dockerPath build `
                -f Dockerfile `
                --build-arg PRELOAD_CHANDRA=false `
                -t $AppImage `
                .
        }

        & $dockerPath save -o (Join-Path $bundleDistDir $AppTarName) $AppImage

        if ($RebuildVllmImage -or -not (& $dockerPath image inspect $VllmImage 2>$null)) {
            & $vllmBuildScript `
                -ImageTag $VllmImage `
                -ArchivePath (Join-Path $bundleDistDir $VllmTarName)
        }
        else {
            & $dockerPath save -o (Join-Path $bundleDistDir $VllmTarName) $VllmImage
        }
    }
    finally {
        Pop-Location
    }
}

$serviceImageMapLines = @(
    "Service -> Image -> Carry-in artifact"
    "app -> a-cong-ocr:chandra -> dist/$AppTarName"
    "ocr-service -> a-cong-ocr:chandra -> dist/$AppTarName"
    "vllm-ocr -> $VllmImage -> dist/$VllmTarName"
)

Set-Content -LiteralPath (Join-Path $bundleRoot "SERVICE_IMAGE_MAP.txt") -Value $serviceImageMapLines

$startGuideLines = @(
    "Defense Remote OCR Carry-in Guide"
    ""
    "Run everything from this folder root."
    ""
    "What must already exist on the defense-network host"
    "- Docker Engine"
    "- Docker Compose v2"
    "- NVIDIA Driver"
    "- NVIDIA Container Toolkit"
    "- GPU server compatible with the two images in dist/"
    "- Docker runtime configured for runtime: nvidia"
    ""
    "What is included in this folder"
    "- dist/$AppTarName"
    "- dist/$VllmTarName"
    "- news_models/chandra-ocr-2/"
    "- docker-compose.defense-remote-ocr.yml"
    "- .env"
    "- .env.example"
    "- scripts/load_offline_images.ps1"
    "- scripts/load_offline_images.sh"
    "- scripts/build_vllm_offline_image.ps1"
    "- scripts/start_defense_remote_ocr.ps1"
    "- scripts/start_defense_remote_ocr.sh"
    "- scripts/validate_defense_remote_ocr_bundle.py"
    ""
    "Container to image mapping"
    "- app -> a-cong-ocr:chandra -> dist/$AppTarName"
    "- ocr-service -> a-cong-ocr:chandra -> dist/$AppTarName"
    "- vllm-ocr -> $VllmImage -> dist/$VllmTarName"
    ""
    "First-time start order"
    "1. Open a shell in this folder."
    "2. On Fedora/Linux hosts, make the shell scripts executable once:"
    "   chmod +x ./scripts/load_offline_images.sh ./scripts/start_defense_remote_ocr.sh"
    "3. Load the two Docker images:"
    "   ./scripts/load_offline_images.sh"
    "4. Confirm that GPU runtime validation and Chandra runtime compatibility validation passed during image load."
    "5. Edit ./.env before starting."
    "6. Start the stack:"
    "   ./scripts/start_defense_remote_ocr.sh"
    ""
    "Windows alternative"
    "- powershell -ExecutionPolicy Bypass -File .\scripts\load_offline_images.ps1"
    "- powershell -ExecutionPolicy Bypass -File .\scripts\start_defense_remote_ocr.ps1"
    ""
    "Values to edit in .env"
    "- TARGET_API_BASE_URL=http://<DEFENSE_TARGET_HOST>:<PORT>/news"
    "- TARGET_API_TOKEN=<token if required>"
    "- API_HOST_PORT=18007"
    "- OCR_API_HOST_PORT=18009"
    "- WATCH_DIR=./news_pdfs"
    "- DATA_DIR=./news_data"
    "- MODELS_DIR=./news_models"
    "- MODEL_CACHE_DIR=./model_cache"
    ""
    "OCR routing notes"
    "- Leave OCR_SERVICE_URL blank to use the compose default route to ocr-service."
    "- If you want to make it explicit, set OCR_SERVICE_URL=http://ocr-service:8000"
    "- Keep VLLM_API_BASE=http://vllm-ocr:8000/v1 for this 3-container bundle."
    "- This compose file uses runtime: nvidia instead of gpus: all for restricted compose environments."
    ""
    "Health checks after startup"
    "- docker compose -f ./docker-compose.defense-remote-ocr.yml ps"
    "- curl http://127.0.0.1:18007/health"
    "- curl http://127.0.0.1:18009/health"
    "- Optional: python ./scripts/validate_defense_remote_ocr_bundle.py --bundle-dir ."
    ""
    "How to apply .env changes later"
    "- Edit ./.env"
    "- Recreate the containers:"
    "  docker compose -f ./docker-compose.defense-remote-ocr.yml up -d --no-build --force-recreate"
    ""
    "How to stop"
    "- docker compose -f ./docker-compose.defense-remote-ocr.yml down"
    ""
    "Important notes"
    "- Do not run docker build inside the defense network for this bundle."
    "- The tar files do not change when .env changes."
    "- app and ocr-service share the same app image tar."
)

Set-Content -LiteralPath (Join-Path $bundleRoot "START_HERE_DEFENSE.txt") -Value $startGuideLines

$manifestLines = @(
    "Defense Remote OCR Carry-in Bundle"
    ""
    "Bundle root: $bundleRoot"
    ""
    "[Required]"
    (Format-ManifestEntry -PathValue (Join-Path $bundleRoot "docker-compose.defense-remote-ocr.yml"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleRoot ".env.example"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleRoot ".env"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleRoot "SERVICE_IMAGE_MAP.txt"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleRoot "START_HERE_DEFENSE.txt"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleScriptsDir "load_offline_images.ps1"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleScriptsDir "load_offline_images.sh"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleScriptsDir "build_vllm_offline_image.ps1"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleScriptsDir "start_defense_remote_ocr.ps1"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleScriptsDir "start_defense_remote_ocr.sh"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleScriptsDir "validate_defense_remote_ocr_bundle.py"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleDocsDir "defense_remote_ocr_bundle.md"))
    (Format-ManifestEntry -PathValue (Join-Path $bundleDistDir $AppTarName))
    (Format-ManifestEntry -PathValue (Join-Path $bundleDistDir $VllmTarName))
    (Format-ManifestEntry -PathValue (Join-Path $bundleModelsDir "chandra-ocr-2"))
    (Format-ManifestEntry -PathValue $bundleWatchDir)
    (Format-ManifestEntry -PathValue $bundleDataDir)
    (Format-ManifestEntry -PathValue $bundleCacheDir)
    ""
    "[Start]"
    "chmod +x ./scripts/load_offline_images.sh ./scripts/start_defense_remote_ocr.sh"
    "./scripts/load_offline_images.sh"
    "Edit ./.env and set TARGET_API_BASE_URL"
    "./scripts/start_defense_remote_ocr.sh"
)

Set-Content -LiteralPath (Join-Path $bundleRoot "CARRY_IN_MANIFEST.txt") -Value $manifestLines

python $bundleValidatorScript --bundle-dir $bundleRoot

Write-Host "Prepared defense carry-in bundle at $bundleRoot"
