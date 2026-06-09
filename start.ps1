# Movie Recommendation System - One-click Start Script
# Launches backend and frontend in separate PowerShell windows

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Movie Recommendation System - Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

$projectRoot = $PSScriptRoot

# Check if Node.js is installed
try {
    $nodeVersion = node --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[1/4] Node.js detected: $nodeVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Node.js not found. Please install Node.js first." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Check if npm is installed
try {
    $npmVersion = npm --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[2/4] npm detected: $npmVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] npm not found." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# Check and install root dependencies
if (-not (Test-Path "$projectRoot\node_modules")) {
    Write-Host "[3/4] Installing root dependencies..." -ForegroundColor Yellow
    Push-Location $projectRoot
    npm install
    Pop-Location
}

# Check and install backend dependencies
if (-not (Test-Path "$projectRoot\backend\node_modules")) {
    Write-Host "Installing backend dependencies..." -ForegroundColor Yellow
    Push-Location "$projectRoot\backend"
    npm install
    Pop-Location
}

# Check and install frontend dependencies
if (-not (Test-Path "$projectRoot\frontend\node_modules")) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location "$projectRoot\frontend"
    npm install
    Pop-Location
}

Write-Host "[3/4] Dependencies ready" -ForegroundColor Green
Write-Host ""
Write-Host "[4/4] Launching services in separate windows..." -ForegroundColor Green
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  Backend:   http://localhost:3000" -ForegroundColor White
Write-Host "  Frontend:  http://localhost:8080" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Launch backend in a new PowerShell window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Write-Host '=== Backend (Port 3000) ===' -ForegroundColor Cyan; Set-Location '$projectRoot\backend'; node server.js"
)

# Launch frontend in a new PowerShell window
Start-Process powershell -ArgumentList @(
    "-NoExit",
    "-Command",
    "Write-Host '=== Frontend (Port 8080) ===' -ForegroundColor Cyan; Set-Location '$projectRoot\frontend'; npx http-server public -p 8080 -c-1"
)

Write-Host "Backend and Frontend windows have been launched." -ForegroundColor Green
Write-Host "Close each window individually to stop the services." -ForegroundColor Gray
Write-Host ""

Read-Host "Press Enter to close this launcher window"
