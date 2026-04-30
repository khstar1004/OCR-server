param(
    [int]$Port = 18109,
    [string]$BindHost = "127.0.0.1",
    [string]$UpstreamOcrUrl = "http://127.0.0.1:18009",
    [int]$UpstreamTimeoutSec = 180,
    [string]$OperatorDemoUrl = "http://127.0.0.1:18007/demo/jobs",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Python = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) {
    throw "Python venv not found: $Python"
}
$Requirements = Join-Path $RepoRoot "requirements.ui.txt"
if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "UI requirements file not found: $Requirements"
}
& $Python -c "import fitz, pypdfium2, multipart" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing missing UI dependencies from requirements.ui.txt..."
    & $Python -m pip install -r $Requirements
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install UI dependencies from $Requirements"
    }
}
$PowerShellExe = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShellExe)) {
    $PowerShellExe = "powershell.exe"
}

$OutputRoot = Join-Path $RepoRoot "news_output\playground_preview"
$InputRoot = Join-Path $RepoRoot "news_pdfs"
$ModelsRoot = Join-Path $RepoRoot "news_models"
$LogRoot = Join-Path $RepoRoot "logs"
$TmpRoot = Join-Path $RepoRoot ".tmp"
New-Item -ItemType Directory -Force -Path $OutputRoot, $LogRoot, $TmpRoot | Out-Null

$RunnerPath = Join-Path $TmpRoot "playground-preview-run.ps1"
$Runner = @"
`$ErrorActionPreference = "Stop"
Set-Location '$RepoRoot'
`$env:OCR_SERVICE_URL = '$UpstreamOcrUrl'
`$env:OCR_SERVICE_MODE = 'native'
`$env:OCR_SERVICE_TIMEOUT_SEC = '$UpstreamTimeoutSec'
`$env:OCR_MAX_CONCURRENT_REQUESTS = '1'
`$env:OCR_RETRY_LOW_QUALITY = 'false'
`$env:CHANDRA_MODEL_DIR = ''
`$env:OUTPUT_ROOT = '$OutputRoot'
`$env:RUNTIME_CONFIG_PATH = '$(Join-Path $OutputRoot "runtime-config\settings.json")'
`$env:INPUT_ROOT = '$InputRoot'
`$env:MODELS_ROOT = '$ModelsRoot'
`$env:ROOT_PATH = ''
`$env:PLAYGROUND_OPERATOR_DEMO_URL = '$OperatorDemoUrl'
& '$Python' -m uvicorn app.ocr_service:app --host '$BindHost' --port $Port
"@
Set-Content -LiteralPath $RunnerPath -Value $Runner -Encoding UTF8

if ($Foreground) {
    & $PowerShellExe -NoProfile -ExecutionPolicy Bypass -File $RunnerPath
    exit $LASTEXITCODE
}

$OutPath = Join-Path $LogRoot "playground-preview-$Port.out"
$ErrPath = Join-Path $LogRoot "playground-preview-$Port.err"
$Process = Start-Process `
    -FilePath $PowerShellExe `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $RunnerPath) `
    -WorkingDirectory $RepoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $OutPath `
    -RedirectStandardError $ErrPath `
    -PassThru

[pscustomobject]@{
    Id = $Process.Id
    Url = "http://$BindHost`:$Port/playground/"
    HealthUrl = "http://$BindHost`:$Port/health"
    UpstreamOcrUrl = $UpstreamOcrUrl
    UpstreamTimeoutSec = $UpstreamTimeoutSec
    OperatorDemoUrl = $OperatorDemoUrl
    Stdout = $OutPath
    Stderr = $ErrPath
}
