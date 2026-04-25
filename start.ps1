# Movie Recommendation System - One-click Start Script (PowerShell version)

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "   Movie Recommendation System - Launcher" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# Check if Node.js is installed
try {
    $nodeVersion = node --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[1/4] Node.js detected" -ForegroundColor Green
    Write-Host "      Version: $nodeVersion" -ForegroundColor Gray
} catch {
    Write-Host "[ERROR] Node.js not found. Please install Node.js first." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# Check if npm is installed
try {
    $npmVersion = npm --version 2>$null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[2/4] npm detected" -ForegroundColor Green
    Write-Host "      Version: $npmVersion" -ForegroundColor Gray
} catch {
    Write-Host "[ERROR] npm not found." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}
Write-Host ""

# Check if root dependencies are installed
if (-not (Test-Path "node_modules")) {
    Write-Host "[3/4] Installing root dependencies..." -ForegroundColor Yellow
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install root dependencies" -ForegroundColor Red
        Read-Host "Press Enter to exit"
        exit 1
    }
} else {
    Write-Host "[3/4] Root dependencies already installed" -ForegroundColor Green
}
Write-Host ""

# Check backend dependencies
if (-not (Test-Path "backend\node_modules")) {
    Write-Host "Installing backend dependencies..." -ForegroundColor Yellow
    Push-Location backend
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install backend dependencies" -ForegroundColor Red
        Pop-Location
        Read-Host "Press Enter to exit"
        exit 1
    }
    Pop-Location
}
Write-Host ""

# Check frontend dependencies
if (-not (Test-Path "frontend\node_modules")) {
    Write-Host "Installing frontend dependencies..." -ForegroundColor Yellow
    Push-Location frontend
    npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to install frontend dependencies" -ForegroundColor Red
        Pop-Location
        Read-Host "Press Enter to exit"
        exit 1
    }
    Pop-Location
}
Write-Host ""

Write-Host "[4/4] Starting services..." -ForegroundColor Green
Write-Host ""
Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Services are starting, please wait..." -ForegroundColor Yellow
Write-Host ""
Write-Host " Backend:  http://localhost:3000" -ForegroundColor White
Write-Host " Frontend: http://localhost:8080" -ForegroundColor White
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host " Press Ctrl+C to stop all services" -ForegroundColor Gray
Write-Host ""

# Start both backend and frontend using concurrently
npm run dev

Read-Host "Press Enter to exit"