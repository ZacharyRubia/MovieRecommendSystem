@echo off

:: run_all_trains.bat
:: 训练脚本批量启动器
::
:: 用法:
::   run_all_trains.bat              并行模式
::   run_all_trains.bat --sequential 顺序模式
::   run_all_trains.bat --help       帮助

cd /d "%~dp0"

:: 参数解析
if /i "%~1"=="-s"           goto :SEQUENTIAL
if /i "%~1"=="--sequential" goto :SEQUENTIAL
if /i "%~1"=="-h"           goto :HELP
if /i "%~1"=="--help"       goto :HELP
goto :PARALLEL

:: ── 帮助 ──────────────────────────────────────────────────────
:HELP
    echo.
    echo 用法: %~nx0 [选项]
    echo.
    echo 选项:
    echo   (无参数)       8 个独立窗口并行启动
    echo   --sequential   顺序执行（等上一个完成再启动下一个）
    echo   -s             同上（简写）
    echo   --help         显示本帮助
    echo.
    pause
    exit /b 0

:: ── 并行模式 ──────────────────────────────────────────────────
:PARALLEL
    echo.
    echo === MovieRecommendSystem 训练启动器 ===
    echo 模式: 并行模式
    echo.
    echo 正在 8 个独立窗口中启动训练...
    echo.

    call :LAUNCH "train_svd.py"                    "1/8"
    call :LAUNCH "train_turbocf.py"                "2/8"
    call :LAUNCH "train_usercf_traditional.py"     "3/8"
    call :LAUNCH "train_usercf_improved.py"        "4/8"
    call :LAUNCH "train_itemcf_traditional.py"     "5/8"
    call :LAUNCH "train_itemcf_improved.py"        "6/8"
    call :LAUNCH "train_slopeone_traditional.py"   "7/8"
    call :LAUNCH "train_slopeone_improved.py"      "8/8"

    echo.
    echo === 8 个训练窗口已全部启动 ===
    echo.
    echo 提示：关闭窗口后可通过以下命令顺序执行：
    echo   run_all_trains.bat --sequential
    echo.
    pause
    exit /b 0

:LAUNCH
    set SCRIPT=%~1
    set NUM=%~2
    echo   [!NUM!] 启动: !SCRIPT!
    start "[!NUM!] !SCRIPT!" cmd /k "cd /d "%~dp0" && python.exe !SCRIPT! --verbose"
    ping -n 2 127.0.0.1 >nul 2>&1
    goto :eof

:: ── 顺序模式 ──────────────────────────────────────────────────
:SEQUENTIAL
    echo.
    echo === MovieRecommendSystem 训练启动器 ===
    echo 模式: 顺序执行（等上一个完成后再启动下一个）
    echo.
    echo 执行顺序（slope one 自动跳过 RMSE）：
    echo   [1/8] train_svd.py
    echo   [2/8] train_turbocf.py
    echo   [3/8] train_usercf_traditional.py
    echo   [4/8] train_usercf_improved.py
    echo   [5/8] train_itemcf_traditional.py
    echo   [6/8] train_itemcf_improved.py
    echo   [7/8] train_slopeone_traditional.py   (跳过 RMSE)
    echo   [8/8] train_slopeone_improved.py       (跳过 RMSE)
    echo.
    echo 按任意键开始...
    pause >nul
    echo.

    :: 脚本 1: train_svd.py
    :S1
    echo --- [1/8] train_svd.py 开始 ---
    echo 配置: 计算 RMSE
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_svd.py --verbose
    if not errorlevel 1 goto :S1_OK
    echo [失败] train_svd.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S1_OK
    echo [完成] train_svd.py
    echo.

    :: 脚本 2: train_turbocf.py
    :S2
    echo --- [2/8] train_turbocf.py 开始 ---
    echo 配置: 计算 RMSE
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_turbocf.py --verbose
    if not errorlevel 1 goto :S2_OK
    echo [失败] train_turbocf.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S2_OK
    echo [完成] train_turbocf.py
    echo.

    :: 脚本 3: train_usercf_traditional.py
    :S3
    echo --- [3/8] train_usercf_traditional.py 开始 ---
    echo 配置: 计算 RMSE
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_usercf_traditional.py --verbose
    if not errorlevel 1 goto :S3_OK
    echo [失败] train_usercf_traditional.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S3_OK
    echo [完成] train_usercf_traditional.py
    echo.

    :: 脚本 4: train_usercf_improved.py
    :S4
    echo --- [4/8] train_usercf_improved.py 开始 ---
    echo 配置: 计算 RMSE
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_usercf_improved.py --verbose
    if not errorlevel 1 goto :S4_OK
    echo [失败] train_usercf_improved.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S4_OK
    echo [完成] train_usercf_improved.py
    echo.

    :: 脚本 5: train_itemcf_traditional.py
    :S5
    echo --- [5/8] train_itemcf_traditional.py 开始 ---
    echo 配置: 计算 RMSE
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_itemcf_traditional.py --verbose
    if not errorlevel 1 goto :S5_OK
    echo [失败] train_itemcf_traditional.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S5_OK
    echo [完成] train_itemcf_traditional.py
    echo.

    :: 脚本 6: train_itemcf_improved.py
    :S6
    echo --- [6/8] train_itemcf_improved.py 开始 ---
    echo 配置: 计算 RMSE
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_itemcf_improved.py --verbose
    if not errorlevel 1 goto :S6_OK
    echo [失败] train_itemcf_improved.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S6_OK
    echo [完成] train_itemcf_improved.py
    echo.

    :: 脚本 7: train_slopeone_traditional.py
    :S7
    echo --- [7/8] train_slopeone_traditional.py 开始 ---
    echo 配置: 跳过 RMSE 计算
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_slopeone_traditional.py --verbose --skip-rmse
    if not errorlevel 1 goto :S7_OK
    echo [失败] train_slopeone_traditional.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1
    :S7_OK
    echo [完成] train_slopeone_traditional.py
    echo.

    :: 脚本 8: train_slopeone_improved.py
    :S8
    echo --- [8/8] train_slopeone_improved.py 开始 ---
    echo 配置: 跳过 RMSE 计算
    echo 开始时间: %DATE% %TIME%
    echo.
    python.exe train_slopeone_improved.py --verbose --skip-rmse
    if not errorlevel 1 goto :FINISH
    echo [失败] train_slopeone_improved.py
    choice /c CQ /n /m "[Q=退出, 其他=继续下一个]: "
    if not errorlevel 2 exit /b 1

    :FINISH
    echo.
    echo === 全部 8 个训练脚本执行完毕 ===
    echo 完成时间: %DATE% %TIME%
    echo.
    echo --- 导出 JSON 模型供后端使用 ---
    cd /d "%~dp0..\export"
    python.exe export_models_to_json.py --model-dir ../models --output-dir ../../backend/models
    if errorlevel 1 (
        echo [警告] 模型导出失败
    ) else (
        echo [完成] JSON 模型导出成功
    )
    cd /d "%~dp0"
    echo.
    pause
    exit /b 0
