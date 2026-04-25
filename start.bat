@echo off
title Movie Recommendation System - Launcher

echo ========================================
echo    Movie Recommendation System - Launcher
echo ========================================
echo.

:: Check if Node.js is installed
node --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Node.js not found. Please install Node.js first.
    pause
    exit /b 1
)

echo [1/4] Node.js detected
node --version
echo.

:: Check if npm is installed
npm --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] npm not found.
    pause
    exit /b 1
)

echo [2/4] npm detected
npm --version
echo.

:: Check if root dependencies are installed
if not exist "node_modules" (
    echo [3/4] Installing root dependencies...
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install root dependencies.
        pause
        exit /b 1
    )
) else (
    echo [3/4] Root dependencies already installed
)
echo.

:: Check backend dependencies
if not exist "backend\node_modules" (
    echo Installing backend dependencies...
    cd backend
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install backend dependencies.
        pause
        exit /b 1
    )
    cd ..
)
echo.

:: Check frontend dependencies
if not exist "frontend\node_modules" (
    echo Installing frontend dependencies...
    cd frontend
    call npm install
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install frontend dependencies.
        pause
        exit /b 1
    )
    cd ..
)
echo.

echo [4/4] Starting services...
echo.
echo ========================================
echo  Services are starting, please wait...
echo.
echo  Backend:  http://localhost:3000
echo  Frontend: http://localhost:8080
echo ========================================
echo.
echo  Press Ctrl+C to stop all services
echo.

:: Start both backend and frontend using concurrently
call npm run dev

pause