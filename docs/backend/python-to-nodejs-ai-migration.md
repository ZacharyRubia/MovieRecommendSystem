# Python AI 推荐引擎 → Node.js 原生集成迁移报告

## 一、背景

原推荐系统架构中，`/api/recommend/ai` 端点通过 Node.js HTTP 代理转发到独立的 Python Flask 微服务（端口 `:5100`），该微服务由 `scripts/recommend/recommend_api.py` 提供。这种架构存在以下问题：

1. **多进程运维负担** — 需要单独维护 Python 进程，启动脚本需同时启动两个服务
2. **数据序列化开销** — Node.js ↔ Python 之间通过 HTTP + JSON 传递数据，增加延迟
3. **依赖复杂性** — Python 侧依赖 scikit-surprise / pandas / flask 等库，环境配置繁琐
4. **单点故障** — Python 进程崩溃后 AI 推荐不可用，降级逻辑不完善

## 二、迁移目标

| 目标 | 说明 |
|------|------|
| 移除 Python Flask 依赖 | 不再需要单独启动 `recommend_api.py` |
| 保持算法一致性 | SVD / User-CF / Item-CF / Hybrid 四种算法全量移植 |
| 性能不劣化 | 首次预测耗时 ≤ Python 版本，缓存命中后 < 10ms |
| 完善降级策略 | AI 无结果 / 异常时自动降级为热门推荐 |
| 保持 API 兼容 | 前端无需任何修改 |

## 三、技术方案

### 3.1 模型文件桥接

Python 训练的 pickle 模型通过 `scripts/recommend/export_models_to_json.py` 导出为 JSON 文件：

```
backend/
  models/
    svd_model.json       ← SVD 矩阵分解模型
    user_cf_model.json   ← User-Based CF 相似度矩阵
    item_cf_model.json   ← Item-Based CF 相似度矩阵
```

**JSON 模型文件结构示例（SVD）：**

```json
{
  "global_mean": 3.53,
  "user_factors": { "1": [0.12, -0.34, ...], "2": [...] },
  "item_factors": { "1": [0.56, 0.78, ...], "541": [...] },
  "user_bias": { "1": 0.05, "188": -0.12 },
  "item_bias": { "1": 0.23, "541": -0.08 },
  "algorithm": "svd",
  "trained_at": "2026-05-08T12:00:00"
}
```

### 3.2 Node.js 引擎实现

新增文件：`backend/src/services/recommendEngine.js`

**核心接口：**

```javascript
// 获取推荐结果
const result = await recommendEngine.getRecommendations(userId, algorithm, topN);
// 返回: { recommendations: [{ movieId, predictedRating }], elapsed, fromCache }
```

**实现的 4 种算法：**

| 算法 | 模型文件 | 计算方式 |
|------|----------|----------|
| `svd` | `svd_model.json` | 预测分 = μ + bᵤ + bᵢ + pᵤ · qᵢᵀ |
| `user_cf` | `user_cf_model.json` | 基于相似用户的加权平均 |
| `item_cf` | `item_cf_model.json` | 基于已评分相似物品的加权平均 |
| `hybrid` | 三者融合 | SVD × 0.3 + UserCF × 0.35 + ItemCF × 0.35 |

**缓存策略：**

```
请求 → MySQL 查询缓存 → 命中？→ 返回缓存结果
                           ↓ 未命中
                    加载 JSON 模型 → 计算预测分 → TOP-N 排序
                           ↓
                    MySQL 写回缓存 → 返回结果
```

缓存表结构复用原有 `hybrid_recommendations` 表（已兼容全部算法类型）。

### 3.3 控制器改造

修改文件：`backend/src/controllers/recommendController.js`

**核心降级逻辑：**

```javascript
// AI 引擎无结果时 → 自动降级为热门推荐
if (enriched.length === 0) {
  const popular = await recommendService.getPopularRecommendations(1, topN);
  enriched = await recommendService.enrichRecommendations(popular);
  degraded = true;
}

// AI 引擎抛异常时 → 也降级为热门推荐
catch (error) {
  const popular = await recommendService.getPopularRecommendations(1, topN);
  return res.json({ data: { algorithm: 'popular', degraded: true, ... } });
}
```

## 四、性能对比

测试环境：Windows 11, Node.js 20, MySQL 8.0 (Local)

| 场景 | 迁移前 (Python Flask) | 迁移后 (Node.js 原生) | 提升 |
|------|----------------------|----------------------|------|
| SVD 首次计算 (user=188) | ~0.8s (含 HTTP 序列化) | 0.10s (含模型加载) | **8x** |
| Hybrid 首次计算 (user=28) | ~0.9s | 0.56s | **1.6x** |
| 缓存命中 | ~0.05s (含 HTTP 转发) | 0.003s (无转发) | **16x** |
| 无数据用户降级 | ~0.8s (Python 返回空→前端再调热门) | 0.06s (后端一次性降级) | **13x** |
| 服务启动 | 需同时启动后端 + Python | 仅启动后端 | **简化运维** |

## 五、移除的文件

| 文件 | 状态 | 说明 |
|------|------|------|
| `scripts/recommend/recommend_api.py` | 不再需要 | Python Flask 服务，被 Node.js 引擎替代 |
| `scripts/recommend/requirements.txt` | 不再需要 | Python 依赖清单（flask, scikit-surprise 等） |

**保留的文件（仅用于模型训练，不参与推理）：**

| 文件 | 用途 |
|------|------|
| `scripts/recommend/recommend.py` | 模型训练脚本（用 scikit-surprise 训练 SVD/CF） |
| `scripts/recommend/export_models_to_json.py` | 将 pickle 模型导出为 JSON（在训练后运行） |

## 六、API 变更

**无变更。** 所有前端请求的 URL、参数、响应格式完全兼容：

```
# 请求（不变）
GET /api/recommend/ai?userId=28&algorithm=hybrid&topN=10

# 响应（向前兼容，新增 degraded 字段标记降级状态）
{
  "success": true,
  "source": "ai-model",
  "data": {
    "userId": 28,
    "algorithm": "hybrid",
    "topN": 10,
    "total": 10,
    "recommendations": [...],
    "degraded": false        // ← 新增：false=AI原生，true=已降级
  }
}
```

## 七、测试验证

```
user 2 (no data): total=10 degraded=true  algo=popular  ← 空用户正确降级
user 28:           total=10 degraded=false algo=hybrid   ← Blade Runner 5.73 等
user 188:          total=5  degraded=false algo=svd      ← 缓存命中 <0.01s
user 265:          total=5  degraded=false algo=hybrid   ← 缓存命中 <0.01s
```

## 八、架构对比

```
迁移前：
┌──────────┐   HTTP   ┌──────────┐   HTTP   ┌───────────────┐
│ Frontend │ ──────→  │ Backend  │ ──────→  │ Python Flask  │
│ (Vue)    │ ←──────  │ (Node.js)│ ←──────  │ (port 5100)   │
└──────────┘          └──────────┘          └───────────────┘
                               │                     │
                               └── MySQL 缓存 ←──────┘

迁移后：
┌──────────┐   HTTP   ┌──────────────────────────────┐
│ Frontend │ ──────→  │       Node.js Backend        │
│ (Vue)    │ ←──────  │                              │
└──────────┘          │  /api/recommend/ai           │
                      │     ├─ recommendEngine.js    │
                      │     │    ├─ svd_model.json   │
                      │     │    ├─ user_cf.json     │
                      │     │    └─ item_cf.json     │
                      │     ├─ MySQL 缓存            │
                      │     └─ 降级→热门推荐          │
                      └──────────────────────────────┘
```

## 九、启动方式

迁移后无需额外步骤：

```bash
# 仅需启动 Node.js 后端（与之前相同）
cd backend && node server.js

# 或使用根目录 start.bat / start.ps1（自动启动前后端）
# 不再需要先启动 Python 服务