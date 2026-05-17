@echo off
chcp 65001 >nul
title 训练脚本启动器

REM ============================================================
REM   run_all_trains.bat — 训练脚本批量启动器
REM   用法:
REM     run_all_trains.bat              # 默认：8 个窗口并行执行
REM     run_all_trains.bat --sequential # 在当前窗口顺序执行
REM     run_all_trains.bat --help       # 显示帮助
REM ============================================================

set MODE=parallel
if /i "%1"=="--sequential" set MODE=sequential
if /i "%1"=="-s"          set MODE=sequential
if /i "%1"=="--help"      goto :HELP
if /i "%1"=="-h"          goto :HELP
if /i "%1"=="/?"          goto :HELP

cd /d "%~dp0"
echo.
echo  ╔══════════════════════════════════════════════╗
echo  ║        MovieRecommendSystem 训练启动器        ║
echo  ║        模式: %MODE%                     ║
echo  ╚══════════════════════════════════════════════╝
echo.

set SCRIPTS=^
    train_svd.py ^
    train_slopeone_traditional.py ^
    train_slopeone_improved.py ^
    train_turbocf.py ^
    train_usercf_traditional.py ^
    train_usercf_improved.py ^
    train_itemcf_traditional.py ^
    train_itemcf_improved.py

if /i "%MODE%"=="parallel" goto :PARALLEL
goto :SEQUENTIAL

:PARALLEL
    echo [启动] 并行模式 — 将在 8 个独立窗口中同时启动训练...
    echo.
    for %%s in (%SCRIPTS%) do (
        echo   打开窗口: %%s
        start "训练 - %%s" cmd /c "cd /d %CD% && python.exe %%s --verbose ^& pause"
    )
    echo.
    echo [完毕] 8 个训练窗口已启动。
    echo.
    pause
    exit /b 0

:SEQUENTIAL
    echo [启动] 串行模式 — 将按顺序执行以下脚本:
    echo.
    for %%s in (%SCRIPTS%) do (
        echo   - %%s
    )
    echo.
    echo ⚠  按任意键开始顺序执行...
    pause >nul
    echo.

    for %%s in (%SCRIPTS%) do (
        echo ============================================================
        echo [开始] %%s
        echo 时间: %DATE% %TIME%
        echo ============================================================
        echo.
        python.exe %%s --verbose
        if errorlevel 1 (
            echo [失败] %%s 返回错误代码 %errorlevel%
        ) else (
            echo [完成] %%s 执行成功
        )
        echo.
        echo 等待 3 秒后继续下一个...
        timeout /t 3 /nobreak >nul
        echo.
    )

    echo ============================================================
    echo  全部训练脚本执行完毕！
    echo  时间: %DATE% %TIME%
    echo ============================================================
    pause
    exit /b 0

:HELP
    echo.
    echo 用法: %~nx0 [选项]
    echo.
    echo 选项:
    echo   (无参数)       8 个独立终端窗口并行启动所有训练脚本
    echo   --sequential   在当前窗口按顺序执行所有训练脚本
    echo   -s             同上（简写）
    echo   --help         显示本帮助信息
    echo.
    pause
    exit /b 0