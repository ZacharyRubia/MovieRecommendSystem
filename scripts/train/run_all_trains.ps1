# run_all_trains.ps1
# Training script launcher (PowerShell version)

param(
    [switch]$Sequential,
    [switch]$Help
)

# Help
if ($Help) {
    Write-Host ""
    Write-Host "Usage: $($MyInvocation.MyCommand.Name) [options]"
    Write-Host ""
    Write-Host "Options:"
    Write-Host "  (no arg)       Launch 8 parallel windows (default)"
    Write-Host "  -Sequential    Run scripts one by one"
    Write-Host "  -Help          Show this help"
    Write-Host ""
    exit 0
}

# Initialize
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# Script list (fastest first, slope one last)
$Scripts = @(
    "train_svd.py",
    "train_turbocf.py",
    "train_usercf_traditional.py",
    "train_usercf_improved.py",
    "train_itemcf_traditional.py",
    "train_itemcf_improved.py",
    "train_slopeone_traditional.py",
    "train_slopeone_improved.py"
)

$TotalCount = $Scripts.Count

# Header
Write-Host ""
Write-Host "=== MovieRecommendSystem Training Launcher ==="
Write-Host "Path: $ScriptDir"
Write-Host "Mode: $(if ($Sequential) { 'Sequential' } else { 'Parallel' })"
Write-Host ""

# Parallel Mode
if (-not $Sequential) {
    Write-Host "[Mode] Parallel - Launching 8 independent windows"
    Write-Host ""

    foreach ($s in $Scripts) {
        Write-Host "    Launching: $s"
        $title = "Training - $s"
        $cmd = "cd /d `"$ScriptDir`"; python.exe `"$s`" --verbose"
        Start-Process cmd -ArgumentList "/k", "title `"$title`" && $cmd"
        Start-Sleep -Milliseconds 500
    }

    Write-Host ""
    Write-Host "[Done] All training windows launched"
    Write-Host ""
    exit 0
}

# Sequential Mode
Write-Host "[Mode] Sequential - Running scripts one by one"
Write-Host "       (slope one scripts will skip RMSE calculation)"
Write-Host ""

for ($i = 0; $i -lt $TotalCount; $i++) {
    $idx = $i + 1
    $s = $Scripts[$i]
    
    # Determine args
    $args = "--verbose"
    $cfg = "Calculate RMSE"
    if ($s -match "slopeone") {
        $args = "--verbose --skip-rmse"
        $cfg = "Skip RMSE"
    }

    Write-Host "--- [$idx/$TotalCount] Starting: $s ---"
    Write-Host "Config: $cfg"
    Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Host ""

    # Execute
    & python.exe "$s" $args.Split()
    $exitCode = $LASTEXITCODE

    Write-Host ""
    if ($exitCode -ne 0) {
        Write-Host "[FAILED] $s exited with code: $exitCode"
        $cont = Read-Host "Press Q to quit, other key to continue"
        if ($cont -eq 'Q' -or $cont -eq 'q') {
            exit 1
        }
    } else {
        Write-Host "[DONE] $s"
    }
    Write-Host ""
}

# Summary
Write-Host "=== All $TotalCount training scripts completed ==="
Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host ""
Write-Host "--- Exporting models to JSON for backend... ---"
$exportDir = Join-Path $ScriptDir "..\export"
$exportScript = Join-Path $exportDir "export_models_to_json.py"
if (Test-Path $exportScript) {
    & python.exe $exportScript --model-dir (Join-Path $ScriptDir "..\models") --output-dir (Join-Path $ScriptDir "..\..\backend\models")
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[DONE] Models exported to JSON successfully"
    } else {
        Write-Host "[WARN] Model export failed with code: $LASTEXITCODE"
    }
} else {
    Write-Host "[WARN] Export script not found: $exportScript"
}
Write-Host ""
