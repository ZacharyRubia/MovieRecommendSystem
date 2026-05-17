<#
.SYNOPSIS
    Run all 8 training scripts in parallel or sequential mode.

.DESCRIPTION
    Parallel mode (default): Open 8 independent cmd windows to run all scripts simultaneously.
    Sequential mode:        Run all scripts one by one in the current window.

.PARAMETER Sequential
    Switch to sequential mode (run scripts one by one).

.PARAMETER Help
    Show help message.

.EXAMPLE
    .\run_all_trains.ps1              # Parallel mode (8 windows)
    .\run_all_trains.ps1 -Sequential  # Sequential mode
    .\run_all_trains.ps1 -Help        # Show help
#>

param(
    [switch]$Sequential,
    [switch]$Help
)

# ── Help ──
if ($Help) {
    Write-Host ""
    Write-Host "Usage: $($MyInvocation.MyCommand.Name) [options]" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Options:" -ForegroundColor Green
    Write-Host "  (no arg)       Open 8 independent cmd windows, run all scripts in parallel"
    Write-Host "  -Sequential    Run scripts one by one in the current window"
    Write-Host "  -Help          Show this help message"
    Write-Host ""
    exit 0
}

# ── Working directory ──
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $ScriptDir

# ── Detect conda environment ──
$CondaEnv = $env:CONDA_DEFAULT_ENV

# ── 8 training scripts ──
$Scripts = @(
    "train_svd.py",
    "train_slopeone_traditional.py",
    "train_slopeone_improved.py",
    "train_turbocf.py",
    "train_usercf_traditional.py",
    "train_usercf_improved.py",
    "train_itemcf_traditional.py",
    "train_itemcf_improved.py"
)

# ── Title ──
Write-Host ""
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host "  MovieRecommendSystem - Training Launcher" -ForegroundColor Cyan
Write-Host "  Directory: $ScriptDir" -ForegroundColor Cyan
if ($CondaEnv) {
    Write-Host "  Conda Env: $CondaEnv" -ForegroundColor Cyan
}
Write-Host "=============================================" -ForegroundColor Cyan
Write-Host ""

# ──────────────────────────────────────
#  PARALLEL MODE
# ──────────────────────────────────────
if (-not $Sequential) {
    Write-Host "[START] Parallel mode - opening 8 independent windows..." -ForegroundColor Yellow
    Write-Host ""

    foreach ($s in $Scripts) {
        $WindowTitle = "Training - $s"
        Write-Host "    Launching: $s" -ForegroundColor Green

        if ($CondaEnv) {
            $cmdArgs = "/c title $WindowTitle & python.exe $s --verbose & pause"
        } else {
            $cmdArgs = "/c title $WindowTitle & cd /d $ScriptDir & python.exe $s --verbose & pause"
        }
        Start-Process cmd -ArgumentList $cmdArgs
    }

    Write-Host ""
    Write-Host "[DONE] All 8 training windows have been launched." -ForegroundColor Yellow
    Write-Host ""
    Read-Host "Press Enter to exit..."
    exit 0
}

# ──────────────────────────────────────
#  SEQUENTIAL MODE
# ──────────────────────────────────────
Write-Host "[START] Sequential mode - running scripts one by one:" -ForegroundColor Yellow
Write-Host ""
foreach ($s in $Scripts) {
    Write-Host "  - $s" -ForegroundColor Green
}
Write-Host ""
Write-Host "WARNING: Press any key to start sequential execution..." -ForegroundColor Red
$null = $Host.UI.RawUI.ReadKey("NoEcho,IncludeKeyDown")
Write-Host ""

# ── Global timer ──
$GlobalStart = Get-Date

foreach ($s in $Scripts) {
    $Start = Get-Date

    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host "[BEGIN] $s" -ForegroundColor Cyan
    Write-Host "Time: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor Cyan
    Write-Host ("=" * 60) -ForegroundColor Cyan
    Write-Host ""

    try {
        & python.exe "$ScriptDir\$s" --verbose 2>&1 | Out-Host
        $exitCode = $LASTEXITCODE

        if ($exitCode -ne 0) {
            Write-Host "[FAIL] $s exited with code $exitCode" -ForegroundColor Red
        } else {
            Write-Host "[OK]   $s completed successfully" -ForegroundColor Green
        }

        $Elapsed = (Get-Date) - $Start
        Write-Host ("Elapsed: {0:N2} sec" -f $Elapsed.TotalSeconds) -ForegroundColor Gray
    }
    catch {
        Write-Host "[ERROR] $s threw an exception: $_" -ForegroundColor Red
    }

    Write-Host ""
    Write-Host "Waiting 3 seconds before next script..."
    Start-Sleep -Seconds 3
    Write-Host ""
}

# ── Summary ──
$GlobalElapsed = (Get-Date) - $GlobalStart
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host " ALL TRAINING SCRIPTS COMPLETED!" -ForegroundColor Cyan
Write-Host (" Total time: {0:N2} sec" -f $GlobalElapsed.TotalSeconds) -ForegroundColor Cyan
Write-Host (" Time: {0}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')) -ForegroundColor Cyan
Write-Host ("=" * 60) -ForegroundColor Cyan
Write-Host ""
Read-Host "Press Enter to exit..."