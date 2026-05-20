#!/bin/bash
# run_all_trains.sh
# 训练脚本批量启动器（Linux/macOS 版）
#
# 用法:
#   ./run_all_trains.sh              并行模式
#   ./run_all_trains.sh --sequential 顺序模式

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Python 检测
PYTHON="${PYTHON:-python3}"
if ! command -v $PYTHON &>/dev/null; then
    PYTHON="python"
fi

# 参数解析
MODE="parallel"
case "${1:-}" in
    --sequential|-s)  MODE="sequential" ;;
    --help|-h)        echo ""; echo "用法: $0 [--sequential]"; echo ""; echo "  (无参数)       并行模式"; echo "  --sequential   顺序模式"; echo ""; exit 0 ;;
esac

# 脚本列表
SCRIPTS=(
    "train_svd.py"
    "train_turbocf.py"
    "train_usercf_traditional.py"
    "train_usercf_improved.py"
    "train_itemcf_traditional.py"
    "train_itemcf_improved.py"
    "train_slopeone_traditional.py"
    "train_slopeone_improved.py"
)

TOTAL=${#SCRIPTS[@]}

echo ""
echo "=== MovieRecommendSystem 训练启动器 ==="
echo "路径:   $SCRIPT_DIR"
echo "Python: $PYTHON"
echo "模式:   $([ "$MODE" = "sequential" ] && echo "顺序执行" || echo "并行模式")"
echo ""

# ── 并行模式 ──────────────────────────────────────────────────
if [ "$MODE" = "parallel" ]; then
    echo "[模式] 并行模式 - 启动 $TOTAL 个后台进程..."
    echo ""

    for s in "${SCRIPTS[@]}"; do
        echo "  启动: $s"
        $PYTHON "$s" --verbose &
    done

    echo ""
    echo "[完毕] 全部 $TOTAL 个进程已在后台启动"
    echo ""
    echo "监控: ps aux | grep train"
    echo "停止: kill \$(jobs -p) 或 kill %%"
    echo ""
    wait
    echo "[完成] 全部训练进程已结束"
    exit 0
fi

# ── 顺序模式 ──────────────────────────────────────────────────
echo "[模式] 顺序模式 - 执行顺序："
echo "        slope one 自动跳过 RMSE"
echo ""

for i in "${!SCRIPTS[@]}"; do
    idx=$((i + 1))
    s="${SCRIPTS[$i]}"
    if echo "$s" | grep -qi "slopeone"; then
        echo "  [$idx/$TOTAL] $s (跳过 RMSE)"
    else
        echo "  [$idx/$TOTAL] $s"
    fi
done
echo ""
echo "按 Enter 开始..."
read

echo ""
echo "============================================================"
echo " 开始顺序执行"
echo "============================================================"
echo ""

for i in "${!SCRIPTS[@]}"; do
    idx=$((i + 1))
    s="${SCRIPTS[$i]}"

    if echo "$s" | grep -qi "slopeone"; then
        ARGS="--verbose --skip-rmse"
        CFG="跳过 RMSE"
    else
        ARGS="--verbose"
        CFG="计算 RMSE"
    fi

    echo "--- [$idx/$TOTAL] $s 开始 ---"
    echo "配置: $CFG"
    echo "时间: $(date '+%Y-%m-%d %H:%M:%S')"
    echo ""

    $PYTHON "$s" $ARGS
    EXIT=$?

    if [ $EXIT -ne 0 ]; then
        echo ""
        echo "[失败] $s 退出码: $EXIT"
        echo -n "按 Q 退出，其他键继续: "
        read CHOICE
        if [ "${CHOICE:-}" = "Q" ] || [ "${CHOICE:-}" = "q" ]; then
            exit 1
        fi
    else
        echo ""
        echo "[完成] $s"
    fi
    echo ""
done

echo "============================================================"
echo " 全部 $TOTAL 个训练脚本执行完毕！"
echo " 完成时间: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo ""
echo "--- 导出 JSON 模型供后端使用 ---"
EXPORT_SCRIPT="$SCRIPT_DIR/../export/export_models_to_json.py"
if [ -f "$EXPORT_SCRIPT" ]; then
    $PYTHON "$EXPORT_SCRIPT" --model-dir "$SCRIPT_DIR/../models" --output-dir "$SCRIPT_DIR/../../backend/models"
    if [ $? -eq 0 ]; then
        echo "[完成] JSON 模型导出成功"
    else
        echo "[警告] 模型导出失败"
    fi
else
    echo "[警告] 导出脚本未找到: $EXPORT_SCRIPT"
fi
echo ""
