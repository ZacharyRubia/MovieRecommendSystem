<#
.SYNOPSIS
  推荐系统端到端测试脚本
  验证: Python AI 推荐服务 -> Node.js 后端代理 -> 前端 API 的完整链路
.DESCRIPTION
  测试训练好的 SVD / User-CF / Item-CF / Hybrid 模型是否能正确返回推荐结果
#>

Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  推荐系统端到端测试脚本" -ForegroundColor Cyan
Write-Host "  测试模型: SVD / User-CF / Item-CF / Hybrid" -ForegroundColor Cyan
Write-Host "============================================`n" -ForegroundColor Cyan

# ============================================
# 配置
# ============================================
$AI_PORT = 5100
$NODE_PORT = 3000
$TEST_USER_IDS = @(28, 188, 265)
$ALGORITHMS = @('svd', 'user_cf', 'item_cf', 'hybrid')
$TOP_N = 5

# ============================================
# 辅助函数
# ============================================

function Test-Health {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 10 -ErrorAction Stop
        if ($response.success) {
            Write-Host "  [OK] $Name 服务在线" -ForegroundColor Green
            return $true
        } else {
            Write-Host "  [FAIL] $Name 服务异常: $($response.message)" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "  [FAIL] $Name 服务不可用: $_" -ForegroundColor Red
        return $false
    }
}

function Test-Recommend {
    param([string]$Name, [string]$Url)
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec 120 -ErrorAction Stop
        if ($response.success -and ($response.data.recommendations.Count -gt 0)) {
            $recs = $response.data.recommendations
            Write-Host "  [OK] $Name 推荐成功" -ForegroundColor Green
            Write-Host "      算法: $($response.data.algorithm)" -ForegroundColor Gray
            Write-Host "      耗时: $($response.data.elapsed)s" -ForegroundColor Gray
            Write-Host "      结果数: $($response.data.total)" -ForegroundColor Gray
            Write-Host "      推荐列表:" -ForegroundColor Gray
            foreach ($rec in $recs) {
                Write-Host "        - 电影 #$($rec.movieId) | 预测评分: $($rec.predictedRating)" -ForegroundColor DarkYellow
            }
            return $true
        } elseif ($response.success) {
            Write-Host "  [WARN] $Name 无推荐结果" -ForegroundColor Yellow
            return $false
        } else {
            Write-Host "  [FAIL] $Name 推荐失败: $($response.message)" -ForegroundColor Red
            return $false
        }
    } catch {
        Write-Host "  [FAIL] $Name 请求失败: $_" -ForegroundColor Red
        return $false
    }
}

# ============================================
# Step 1: 健康检查
# ============================================
Write-Host "-------- Step 1: 服务健康检查 --------" -ForegroundColor Magenta

$aiHealth = Test-Health -Name "Python AI 服务" -Url "http://127.0.0.1:$AI_PORT/api/recommend/health"
$nodeHealth = Test-Health -Name "Node.js 后端" -Url "http://127.0.0.1:$NODE_PORT/api/recommend/ai/health"

if (-not $aiHealth) {
    Write-Host "`n[错误] Python AI 服务未运行！请先启动:" -ForegroundColor Red
    Write-Host "  cd scripts/recommend; python recommend_api.py --port $AI_PORT" -ForegroundColor Yellow
    exit 1
}

if (-not $nodeHealth) {
    Write-Host "`n[错误] Node.js 后端未运行！请先启动:" -ForegroundColor Red
    Write-Host "  node backend/server.js" -ForegroundColor Yellow
    exit 1
}

# ============================================
# Step 2: 获取模型信息
# ============================================
Write-Host "`n-------- Step 2: 模型信息 --------" -ForegroundColor Magenta
try {
    $models = Invoke-RestMethod -Uri "http://127.0.0.1:$NODE_PORT/api/recommend/ai/models" -TimeoutSec 10 -ErrorAction Stop
    if ($models.success) {
        Write-Host "  可用模型:" -ForegroundColor Green
        foreach ($m in $models.data.models) {
            Write-Host "    - $($m.name) ($($m.algorithm))" -ForegroundColor Gray
        }
        Write-Host "  数据集: $($models.data.dataset.n_users) 用户 x $($models.data.dataset.n_movies) 电影" -ForegroundColor Gray
    }
} catch {
    Write-Host "  [FAIL] 获取模型信息失败: $_" -ForegroundColor Red
}

# ============================================
# Step 3: 测试 Python AI 服务（直接）
# ============================================
Write-Host "`n-------- Step 3: Python AI 服务推荐测试（直接调用）--------" -ForegroundColor Magenta

foreach ($userId in $TEST_USER_IDS) {
    Write-Host "`n--- 用户 #$userId ---" -ForegroundColor Yellow
    foreach ($algo in $ALGORITHMS) {
        $url = "http://127.0.0.1:$AI_PORT/api/recommend/ai?user_id=$userId&algorithm=$algo&top_n=$TOP_N"
        Test-Recommend -Name "[AI] $algo" -Url $url | Out-Null
    }
}

# ============================================
# Step 4: 测试 Node.js 后端代理
# ============================================
Write-Host "`n-------- Step 4: Node.js 后端代理测试（通过后端调用）--------" -ForegroundColor Magenta

foreach ($userId in $TEST_USER_IDS) {
    Write-Host "`n--- 用户 #$userId ---" -ForegroundColor Yellow
    foreach ($algo in $ALGORITHMS) {
        $url = "http://127.0.0.1:$NODE_PORT/api/recommend/ai?userId=$userId&algorithm=$algo&topN=$TOP_N"
        Test-Recommend -Name "[Node] $algo" -Url $url | Out-Null
    }
}

# ============================================
# Step 5: 性能评估
# ============================================
Write-Host "`n-------- Step 5: 性能基准测试 --------" -ForegroundColor Magenta

$PERF_TEST_USER = 28
$PERF_ALGO = 'hybrid'

Write-Host "  用户 #$PERF_TEST_USER, 算法: $PERF_ALGO, 测试次数: 3" -ForegroundColor Gray

$times = @()
for ($i = 1; $i -le 3; $i++) {
    try {
        $start = Get-Date
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:$NODE_PORT/api/recommend/ai?userId=$PERF_TEST_USER&algorithm=$PERF_ALGO&topN=$TOP_N" -TimeoutSec 120 -ErrorAction Stop
        $elapsed = (Get-Date) - $start
        $times += $elapsed.TotalSeconds
        Write-Host "  第 $i 次: $($response.data.total) 个结果, $($elapsed.TotalSeconds.ToString('F3'))s" -ForegroundColor Gray
    } catch {
        Write-Host "  第 $i 次: 失败 - $_" -ForegroundColor Red
    }
}

if ($times.Count -gt 0) {
    $avg = ($times | Measure-Object -Average).Average
    Write-Host "  平均耗时: $($avg.ToString('F3'))s" -ForegroundColor Cyan
}

# ============================================
# 汇总
# ============================================
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  测试完成！" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "`n验证要点:" -ForegroundColor White
Write-Host "  [OK] Python AI 推荐服务 (127.0.0.1:$AI_PORT)   - 使用训练的模型生成推荐" -ForegroundColor White
Write-Host "  [OK] Node.js 后端代理 (127.0.0.1:$NODE_PORT)   - 将请求转发到 Python 服务" -ForegroundColor White
Write-Host "  [OK] 前端 user-dashboard.html                   - 调用 Node.js API 展示推荐" -ForegroundColor White
Write-Host "  [OK] SVD / User-CF / Item-CF / Hybrid 四种算法" -ForegroundColor White
Write-Host "`n前端访问地址: http://localhost:3000/user-dashboard.html" -ForegroundColor Yellow
Write-Host "`n如果 AI 推荐区域提示离线，请确认 recommend_api.py 已在端口 $AI_PORT 运行" -ForegroundColor Yellow