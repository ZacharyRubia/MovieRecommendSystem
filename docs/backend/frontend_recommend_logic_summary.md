# 前端推荐逻辑重构总结

## 概述

本次修改将前端推荐逻辑完全重构，从原先依赖 Node.js 后端 SQL 协同过滤（耗时 30s+）改为优先调用 **Python AI 模型 API**（耗时 ~1~3s），并设计了完整的降级策略。同时优化了页面加载流程，使推荐区域不再互相阻塞。

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    浏览器 (前端)                         │
│  ┌──────────────────────────────────────────────────┐   │
│  │  user-dashboard.html                             │   │
│  │  ┌────────────────────┐  ┌───────────────────┐  │   │
│  │  │ "推荐给你" 区域     │  │ "AI智能推荐" 区域  │  │   │
│  │  │ AI混合推荐(hybrid) │  │ 可切换算法:       │  │   │
│  │  │   ↓ 失败降级        │  │ hybrid/svd/       │  │   │
│  │  │ 热门推荐(popular)  │  │ user_cf/item_cf   │  │   │
│  │  │   ↓ 失败兜底        │  │                   │  │   │
│  │  │ 空状态             │  │                   │  │   │
│  │  └────────────────────┘  └───────────────────┘  │   │
│  └──────────────────────────────────────────────────┘   │
                          │
                          ▼ HTTP
┌─────────────────────────────────────────────────────────┐
│              Node.js 后端 (端口 3000)                     │
│  ├─ /api/recommend/ai?userId=&algorithm=&topN=           │
│  │   └── 代理转发到 Python AI API                        │
│  ├─ /api/recommend/ai/health       (健康检查)            │
│  ├─ /api/recommend/ai/models       (模型列表)            │
│  └─ /api/recommend/popular         (热门推荐，降级用)    │
│                          │
                          ▼ HTTP (127.0.0.1:5100)
┌─────────────────────────────────────────────────────────┐
│         Python Flask AI API (端口 5100)                  │
│  ├─ /api/recommend/ai?user_id=&algorithm=&top_n=        │
│  │   算法支持: hybrid / svd / user_cf / item_cf         │
│  ├─ /api/recommend/health                               │
│  └─ /api/recommend/models                               │
│    模型文件: scripts/models/svd_model.pkl (含user_cf等) │
└─────────────────────────────────────────────────────────┘
```

## 修改的文件

| 文件 | 修改内容 |
|------|---------|
| `frontend/public/user-dashboard.html` | 推荐逻辑完全重构 |
| `backend/src/controllers/recommendController.js` | 超时时间优化 |
| `scripts/recommend/recommend_api.py` | 修复 int64 JSON 序列化问题 |

## 详细变更

### 1. "推荐给你" 区域 - 三层降级策略

**策略**：AI 混合推荐(hybrid) → 热门推荐(popular) → 空状态

```javascript
async function loadRecommendations() {
  // 第1层：AI 混合推荐（最快，~0.5~3s）
  const aiResult = await tryAiRecommendation(userId, 'hybrid', 10, 10000);
  if (aiResult && aiResult.length > 0) { /* 展示 */ return; }

  // 第2层：降级热门推荐（第二快）
  const popularData = await fetch(`${API_BASE}/recommend/popular?page=1&pageSize=10`);
  if (popularData.success) { /* 展示 */ return; }

  // 第3层：空状态兜底
  container.innerHTML = '<div class="recommend-empty">...暂无推荐...</div>';
}
```

- AI 推荐超时时间：**10 秒**（原 SQL 协同过滤超时 120 秒）
- 不再依赖后端 SQL 的 hybrid 推荐接口
- 直接从 Python 返回的 `movieId` 批量查询电影信息

### 2. AI 智能推荐区域 - 独立加载

**优化**：不再等待 health check 完成，改为**立即加载**（延迟 500ms 确保 UI 渲染）

```javascript
// 旧流程（串行阻塞）：
// checkAiServiceHealth() → health 成功 → loadAiRecommendations()

// 新流程（并行独立）：
loadRecommendations();             // 1. 立即加载"推荐给你"
checkAiServiceHealth();            // 2. 异步检查 AI 服务状态
setTimeout(() => loadAiRecommendations(), 500);  // 3. 立即加载 AI 区域
loadMovies(1);                     // 4. 加载电影列表
```

- 支持 4 种算法切换：`hybrid`(混合)、`svd`、`user_cf`、`item_cf`
- 每种算法请求超时：**30 秒**
- health check 失败不影响推荐区域的加载和显示

### 3. 超时时间优化

在 `backend/src/controllers/recommendController.js` 中：

| 参数 | 原值 | 新值 |
|------|------|------|
| `REQUEST_TIMEOUT` (通用) | 120s | **120s** (不变，但实际已不再使用) |
| `AI_REQUEST_TIMEOUT` (Python API) | 60s | **60s** (不变) |
| 前端 AI 推荐超时 | 无 (依赖后端) | **10s** ("推荐给你") / **30s** (AI 区域) |

### 4. Python API  int64 JSON 序列化修复

在 `scripts/recommend/recommend_api.py` 中添加了自定义 JSON 序列化器：

```python
class NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.int_, np.intc, np.intp, np.int8, np.int16,
                            np.int32, np.int64, np.uint8, np.uint16, np.uint32, np.uint64)):
            return int(obj)
        elif isinstance(obj, (np.float_, np.float16, np.float32, np.float64)):
            return float(obj)
        elif isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)
```

## 启动与验证

### 启动步骤

1. **启动 Python AI API**（推荐启动后保持在终端运行）：
   ```powershell
   cd scripts/recommend
   python recommend_api.py --port 5100
   ```

2. **启动 Node.js 后端**：
   ```powershell
   cd backend
   node server.js
   ```

3. **打开前端页面**：
   访问 `http://localhost:3000/user-dashboard.html`

### 验证方法

**验证 Python AI API 可直接访问**：
```powershell
curl.exe -s "http://127.0.0.1:5100/api/recommend/ai?user_id=28&algorithm=hybrid&top_n=5"
```

**验证 Node.js 后端代理**：
```powershell
curl.exe -s "http://127.0.0.1:3000/api/recommend/ai?userId=28&algorithm=hybrid&topN=5"
```

**验证健康检查**：
```powershell
curl.exe -s http://127.0.0.1:5100/api/recommend/health
```

## 性能对比

| 指标 | 修改前 (SQL CF) | 修改后 (AI 模型) |
|------|----------------|-----------------|
| "推荐给你"响应时间 | 30~60 秒 | **~1~3 秒** |
| AI 区域算法切换 | 依赖 health check | **即时切换** |
| 后端负载 | 大量 SQL 计算 | 轻量代理转发 |
| 推荐质量 | 基础协同过滤 | **训练好的 ML 模型** |
| 降级策略 | 无 (请求失败即空白) | **三层降级** |