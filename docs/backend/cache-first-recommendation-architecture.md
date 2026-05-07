# 缓存优先推荐架构 — 技术路线与前后端交互逻辑

## 一、概述

为解决推荐系统实时计算性能瓶颈，实施了 **「缓存优先读 → 未命中时实时计算 → 异步写回缓存」** 的缓存优先架构。该架构覆盖 Node.js 后端（SQL 协同过滤）和 Python Flask AI API（SVD/User-CF/Item-CF/Hybrid）两个推荐通路，共用同一套 MySQL 缓存表。

## 二、技术架构图

```
┌─────────────────────────────────────────────────────────────────────┐
│                         前端 (user-dashboard.html)                    │
│  GET /api/recommend/user-based/28  GET /api/recommend/ai?user_id=28 │
└─────────────────────────┬───────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    Node.js 后端 (端口 3000)                          │
│                                                                     │
│  ┌─────────────────────────────────────────────┐                    │
│  │         recommendService.js                 │                    │
│  │                                             │                    │
│  │  userBasedCF(userId)                        │                    │
│  │    ├─ ① getCachedUserRecommend(userId)      │ ← 查缓存表          │
│  │    ├─ ② 命中 → 直接返回                     │                    │
│  │    ├─ ③ 未命中 → KNN 实时计算               │                    │
│  │    └─ ④ saveCacheUserRecommend(async)       │ → 异步写回缓存表    │
│  │                                             │                    │
│  │  itemBasedCF(userId)                        │                    │
│  │    ├─ ① getItemBasedFromCache(ratingMap)    │ ← 查缓存表          │
│  │    ├─ ② 命中 → 直接返回                     │                    │
│  │    ├─ ③ 未命中 → Cosine 实时计算            │                    │
│  │    └─ ④ saveCacheUserRecommend(async)       │ → 异步写回缓存表    │
│  └─────────────────────────────────────────────┘                    │
│                          │                                          │
│  ┌─────────────────────────────────────────────┐                    │
│  │       recommendController.js                │                    │
│  │  /api/recommend/ai → 代理 Python Flask API  │                    │
│  └─────────────────────────────────────────────┘                    │
└─────────────────────────┬───────────────────────────────────────────┘
                          │ HTTP (127.0.0.1:5100)
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                  Python Flask AI API (端口 5100)                      │
│                                                                     │
│  /api/recommend/ai?user_id=&algorithm=&top_n=                       │
│                                                                     │
│  ① get_cached_recommendation(user_id, algorithm)  ← 查 users_recommendations │
│     ├─ 命中 → 直接返回 { fromCache: true }                          │
│     └─ 未命中 → 进入步骤 ②                                        │
│                                                                     │
│  ② 实时加载模型计算 (SVD / User-CF / Item-CF / Hybrid)              │
│                                                                     │
│  ③ save_result_to_cache(user_id, results, algorithm) → 异步写回     │
│     (结果 ≥ top_n/2 时才写，避免缓存无效结果)                        │
│                                                                     │
│  ④ 返回 { fromCache: false, recommendations: [...] }                │
└─────────────────────────────────────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    MySQL 缓存层                                     │
│                                                                     │
│  ┌─────────────────────────────────────┐                           │
│  │  users_recommendations              │                           │
│  │  ├─ user_id (BIGINT PK)            │ ← 用户推荐结果缓存         │
│  │  ├─ algorithm (VARCHAR)            │ ← 算法标识                 │
│  │  ├─ recommend_movies (JSON)        │ ← [{movie_id, score}]      │
│  │  └─ updated_at (TIMESTAMP)         │ ← 1小时 TTL 过期检查       │
│  └─────────────────────────────────────┘                           │
│                                                                     │
│  ┌─────────────────────────────────────┐                           │
│  │  movies_similarities                │                           │
│  │  ├─ movie_id (BIGINT PK)           │ ← 电影相似度缓存           │
│  │  ├─ similar_movies (JSON)          │ ← [{movie_id, similarity}] │
│  │  └─ updated_at (TIMESTAMP)         │ ← 1小时 TTL 过期检查       │
│  └─────────────────────────────────────┘                           │
└─────────────────────────────────────────────────────────────────────┘
```

## 三、技术路线

### 3.1 缓存策略（TTL 管理）

| 缓存类型 | 更新时机 | TTL | 检查方式 |
|---------|---------|-----|---------|
| `users_recommendations` | 实时计算后异步写回 / 离线批量导入 | 1 小时 | `updated_at` + `CACHE_TTL_MS` (3600000ms) |
| `movies_similarities` | 离线 Item-CF 导入 | 1 小时 | 同上 |

缓存过期后自动视为"未命中"，降级为实时计算。新计算结果会通过 `REPLACE INTO` 覆盖写入。

### 3.2 写回策略

- **Node.js 后端**：`saveCacheUserRecommend()` 为 **async/await** 但不被 `await`（fire-and-forget），不阻塞主请求链路
- **Python API**：`save_result_to_cache()` 使用独立线程池异步执行，不阻塞 Flask 请求线程
- **写回阈值**：仅当结果数 ≥ `top_n / 2` 时才写回，避免缓存空结果或过低质量的推荐

### 3.3 数据一致性

- 使用 `REPLACE INTO` 保证原子覆盖，导入期间不影响正在进行的查询
- 缓存过期后自动降级实时计算，保证新用户也能获得推荐
- 离线批量导入与实时写回共存，以最新 `updated_at` 为准

### 3.4 支持的缓存表写入方式

| 方式 | 工具/函数 | 适用场景 |
|------|----------|---------|
| 实时计算异步写回 | `saveCacheUserRecommend()` / `save_result_to_cache()` | 每次实时推荐后 |
| 离线批量导出导入 | `export_recommendations.py` + `import_recommendations.py` | 每日全量预热 |
| 独立脚本写回 | `save_to_cache.py` (支持 stdin/JSON文件) | 运维调试 |
| 模型训练后写回 | `train_recommend.py` → 导出 → 导入 | 模型更新后预热 |

## 四、前后端交互逻辑

### 4.1 Node.js 后端推荐 API 请求流程

```
用户请求 GET /api/recommend/user-based/:userId
                           │
                    recommendController.userBasedRecommend()
                           │
                    recommendService.userBasedCF(userId)
                           │
                    ┌──────▼──────┐
                    │  查缓存表?    │
                    │ getCached    │
                    │ UserRecommend│
                    └──┬───┬──────┘
                 命中   │   │  未命中
              ┌─────────┘   └─────────┐
              ▼                        ▼
    返回缓存结果              KNN 实时计算
    (fromCache=true)        findKNearestUsers()
                                  │
                            聚合邻居评分
                                  │
                        排序取 Top-N
                                  │
                    ┌─────────────▼──────────┐
                    │ 异步写回缓存表          │
                    │ saveCacheUserRecommend │
                    └─────────────┬──────────┘
                                  │
                         返回计算结果
                        (fromCache=false)
```

### 4.2 Python AI API 请求流程

```
用户请求 GET /api/recommend/ai?user_id=28&algorithm=hybrid&top_n=10
                           │
              recommendController.aiModelRecommend()
                           │
                    代理转发到 Flask (127.0.0.1:5100)
                           │
                    ┌──────▼──────┐
                    │  查缓存表?    │
                    │ get_cached_  │
                    │ recommendation│
                    └──┬───┬──────┘
                 命中   │   │  未命中
              ┌─────────┘   └─────────┐
              ▼                        ▼
    返回缓存结果              加载模型实时计算
    {fromCache: true}         SVD / User-CF / Item-CF / Hybrid
                                    │
                       ┌────────────▼──────────┐
                       │ 异步写回缓存 (线程池)   │
                       │ save_result_to_cache  │
                       └────────────┬──────────┘
                                    │
                           返回计算结果
                          {fromCache: false}
```

### 4.3 前端降级策略

```
"推荐给你" 区域加载流程:
  loadRecommendations()
    ├─ tryAiRecommendation('hybrid', timeout=10s)
    │    ├─ 成功 → 展示 AI 混合推荐
    │    └─ 超时/失败 → 降级
    │
    └─ fetch('/api/recommend/popular')
         ├─ 成功 → 展示热门推荐
         └─ 失败 → 空状态兜底

"AI 智能推荐" 区域加载流程:
  延迟 500ms 后 loadAiRecommendations()
    └─ tryAiRecommendation(selectedAlgorithm, timeout=30s)
         ├─ 成功 → 展示 AI 推荐 + 算法切换按钮
         └─ 失败 → 显示"AI 引擎不可用"提示

注：两种降级策略互不阻塞，可同时展示不同来源的推荐
```

### 4.4 缓存淘汰与刷新

| 事件 | 缓存行为 |
|------|---------|
| 用户新评分 | 缓存不立即失效，`updated_at` 超过 TTL 后自动淘汰 |
| 离线批量导入 | `REPLACE INTO` 覆盖已有记录，`updated_at` 刷新 |
| 实时计算写回 | 覆盖相同 `user_id + algorithm` 的记录 |
| 手动清除 | `POST /api/recommend/clear-cache` 清除内存缓存（不影响数据库缓存表） |

## 五、核心代码映射

### 5.1 缓存读取函数

| 函数 | 位置 | 查询表 | 超时检查 |
|------|------|--------|---------|
| `getCachedUserRecommend(userId)` | `recommendService.js:215` | `users_recommendations` | `updated_at + 1h` |
| `getItemBasedFromCache(ratingMap, topN)` | `recommendService.js:167` | `movies_similarities`（聚合查询） | 由调用方保证 |
| `getCachedSimilarMovies(movieId)` | `recommendService.js:141` | `movies_similarities` | `updated_at + 1h` |
| `get_cached_recommendation(user_id, algorithm)` | `recommend_api.py:400` | `users_recommendations` | `updated_at + 1h` |

### 5.2 缓存写入函数

| 函数 | 位置 | 目标表 | 执行方式 |
|------|------|--------|---------|
| `saveCacheUserRecommend(userId, recs, algorithm)` | `recommendService.js:26` | `users_recommendations` | async fire-and-forget |
| `saveCacheMovieSimilarity(movieId, sims)` | `recommendService.js:48` | `movies_similarities` | async fire-and-forget |
| `save_result_to_cache(user_id, results, algorithm)` | `recommend_api.py:428` | `users_recommendations` | 线程池异步 |
| `save_user_recommendation()` / `batch_save_*()` | `save_to_cache.py` | `users_recommendations` | 同步 CLI 工具 |

## 六、性能对比

| 指标 | 修改前（纯实时计算） | 修改后（缓存优先） |
|------|---------------------|-------------------|
| User-CF 缓存命中 | — | **~1ms**（SQL PK 查询 + JSON 解析） |
| Item-CF 缓存命中 | — | **~5ms**（批量相似度聚合） |
| User-CF 实时计算 | 30~60s | 30~60s（仅缓存未命中时） |
| Item-CF 实时计算 | 20~40s | 20~40s（仅缓存未命中时） |
| Python AI 缓存命中 | — | **~5ms** |
| Python AI 实时计算 | 1~3s | 1~3s（仅缓存未命中时） |
| 数据库负载 | 大量实时 JOIN 计算 | PK 查询为主，大幅降低 |
| 用户体验 | 每次等待 20~60s | 缓存命中时**秒级响应** |

## 七、配套脚本与运维

| 脚本 | 路径 | 功能 |
|------|------|------|
| 批量导出工具 | `scripts/recommend/export_recommendations.py` | 四种算法批量导出 + 评估报告 |
| 一键导入工具 | `scripts/recommend/import_recommendations.py` | 将导出的 JSON 导入缓存表 |
| 缓存写入工具 | `scripts/recommend/save_to_cache.py` | 通用缓存写入（CLI/stdin 都支持） |

### 启动命令

```powershell
# 步骤 1：启动 Python AI API（缓存优先 + 实时降级）
cd scripts/recommend
python recommend_api.py --port 5100

# 步骤 2：启动 Node.js 后端
cd backend
node server.js

# 步骤 3：验证缓存命中
curl.exe -s "http://127.0.0.1:3000/api/recommend/ai?userId=28&algorithm=hybrid&topN=5"
# 响应中 fromCache: true 表示缓存命中
```

### 批量预热缓存

```powershell
# 导出热门用户推荐（SVD 算法）
python scripts/recommend/export_recommendations.py --algorithm svd --max-users 1000 --output svd_export.json

# 导入缓存表
python scripts/recommend/import_recommendations.py --input svd_export.json