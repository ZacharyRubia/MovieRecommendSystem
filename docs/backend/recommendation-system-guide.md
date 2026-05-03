# 推荐系统指南

> 本文档详细说明 MovieRecommendSystem 的推荐算法实现、架构设计、API 接口及测试方法。

---

## 目录

1. [系统架构](#1-系统架构)
2. [推荐算法详解](#2-推荐算法详解)
3. [数据源说明](#3-数据源说明)
4. [API 接口文档](#4-api-接口文档)
5. [测试指南](#5-测试指南)
6. [性能优化策略](#6-性能优化策略)
7. [常见问题](#7-常见问题)

---

## 1. 系统架构

### 1.1 整体架构图

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                            │
│  /api/recommend/user-based/:userId   (User-Based CF)        │
│  /api/recommend/item-based/:userId   (Item-Based CF)        │
│  /api/recommend/hybrid/:userId       (Hybrid)               │
│  /api/recommend/neighbors/:userId    (邻居查询)             │
│  /api/recommend/clear-cache          (清除缓存)             │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                   Controller Layer                           │
│  backend/src/controllers/recommendController.js             │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                   Service Layer (算法核心)                   │
│  backend/src/services/recommendService.js                   │
│                                                              │
│  ┌─────────────────┐  ┌─────────────────┐                   │
│  │  User-Based CF   │  │  Item-Based CF   │                   │
│  │  Pearson 相关系数│  │  Cosine 相似度   │                   │
│  └────────┬────────┘  └────────┬────────┘                   │
│           │                    │                             │
│           └─────┬──────────────┘                             │
│                  ▼                                            │
│           ┌─────────────┐                                    │
│           │   Hybrid     │                                    │
│           │ (加权融合)    │                                    │
│           └─────────────┘                                    │
└──────────────────────┬──────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────┐
│                    Data Layer                                 │
│                                                              │
│  ┌─────────────────────────┐  ┌───────────────────────────┐ │
│  │  MySQL (User/Item CF)   │  │  Qdrant (Content-Based)    │ │
│  │  - users_movies_        │  │  - movies 集合             │ │
│  │    behaviors (2500万行)  │  │  - 384维语义向量          │ │
│  │  - movies (87585部)      │  │  - Cosine 相似度          │ │
│  └─────────────────────────┘  └───────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 1.2 文件结构

| 文件 | 说明 |
|------|------|
| `backend/src/services/recommendService.js` | 推荐算法核心实现（SQL 驱动版） |
| `backend/src/controllers/recommendController.js` | API 请求处理与数据组装 |
| `backend/src/routes/recommend.js` | 路由注册 |
| `backend/server.js` | 服务入口，挂载 `/api/recommend` 路由 |

---

## 2. 推荐算法详解

### 2.1 User-Based Collaborative Filtering（基于用户的协同过滤）

#### 算法原理

找到与目标用户**评分模式最相似**的 K 个邻居用户，综合邻居用户对某一电影的评分来预测目标用户对该电影的评分。

#### 执行流程

```
用户 A (目标用户)
      │
      ▼
Step 1: 获取目标用户的所有评分记录
      │
      ▼
Step 2: SQL 筛选候选邻居
        SELECT u2.user_id, COUNT(*) AS common_count
        FROM users_movies_behaviors u1
        JOIN users_movies_behaviors u2
          ON u1.movie_id = u2.movie_id AND u2.user_id != u1.user_id
        WHERE u1.user_id = ? AND ...
        GROUP BY u2.user_id
        HAVING common_count >= 3
        ORDER BY common_count DESC
        LIMIT 500
      │
      ▼
Step 3: 加载候选用户评分数据（分批加载）
      │
      ▼
Step 4: 计算 Pearson 相关系数
        r = Σ( (x_i - x̄)(y_i - ȳ) ) / √( Σ(x_i - x̄)² · Σ(y_i - ȳ)² )
        r ∈ [0, 1]，值越大表示偏好越相似
      │
      ▼
Step 5: 取 Top-K 最相似用户作为邻居
      │
      ▼
Step 6: 聚合邻居评分预测目标用户的未评分电影
        predicted(u, m) = Σ( sim(u, v) × r(v, m) ) / Σ( sim(u, v) )
      │
      ▼
Step 7: 排序，取 Top-N 推荐
```

#### 关键参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `MIN_COMMON_ITEMS` | 3 | 用户间至少共同评分数，低于此不计算相似度 |
| `MIN_RATINGS_FOR_USER` | 5 | 用户最少评分数，低于此无法计算推荐 |
| 候选用户上限 | 500 | SQL LIMIT，限制候选邻居数量 |
| 分批加载大小 | 100 | 每批加载 100 个候选用户的评分数据 |

#### 数据流

```
目标用户评分(内存)  ────┐
                        ├──→ Pearson 计算 ──→ 相似度列表 ──→ 选 Top-K 邻居 ──→ 聚合预测
候选用户评分(分批加载) ──┘
```

---

### 2.2 Item-Based Collaborative Filtering（基于物品的协同过滤）

#### 算法原理

对用户评分过的每部电影 i，找出**其他也评过 i 的用户评分过的其他电影 j**，通过 Cosine 相似度计算 i 与 j 的相关性，聚合所有候选电影 j 的加权评分。

#### 执行流程

```
用户 A (目标用户)
      │
      ▼
Step 1: 获取目标用户评分过的所有电影
      │
      ▼
Step 2: 找出评过这些电影的所有"共同评分用户"
        SELECT DISTINCT user_id
        FROM users_movies_behaviors
        WHERE movie_id IN (用户评过的电影)
          AND user_id != 目标用户
      │
      ▼
Step 3: 获取共同用户评过的其他电影（排除目标用户已看过的）
        SELECT um.user_id, um.movie_id, um.rating
        FROM users_movies_behaviors um
        WHERE um.user_id IN (共同用户)
          AND um.movie_id NOT IN (目标用户已看过的)
      │
      ▼
Step 4: 对用户评过的每部电影 i，与每部候选电影 j 计算 Cosine 相似度
        cosine(i, j) = Σ(r_ui × r_uj) / √(Σr_ui²) × √(Σr_uj²)
      │
      ▼
Step 5: 聚合所有候选电影的加权预测评分
        predicted(u, j) = Σ( cosine(i, j) × r(u, i) ) / Σ( cosine(i, j) )
      │
      ▼
Step 6: 排序，取 Top-N 推荐
```

#### 与 User-Based 的区别

| 维度 | User-Based | Item-Based |
|------|-----------|------------|
| 相似度对象 | 用户与用户 | 物品与物品 |
| 相似度算法 | Pearson 相关系数 | Cosine 余弦相似度 |
| 候选生成 | 通过共同评分用户 | 通过共同评分用户的其它电影 |
| 适用场景 | 用户群体偏好差异明显 | 物品间关联性强 |

---

### 2.3 Hybrid（混合推荐）

#### 算法原理

将 User-Based 和 Item-Based 的结果按照权重线性融合。

```javascript
final_score = userWeight × userCF_score + (1 - userWeight) × itemCF_score
```

#### 执行策略

1. 分别调用 User-CF 和 Item-CF，各自取 `TopN × 2` 个候选（扩大候选池）
2. 用加权融合公式合并两个结果集
3. 按融合后分数排序，取最终 Top-N

#### 权重建议

| userWeight | 说明 |
|------------|------|
| 1.0 | 纯 User-Based |
| 0.5 (默认) | 两者均衡 |
| 0.0 | 纯 Item-Based |

---

### 2.4 Content-Based（基于内容的推荐 — Qdrant 向量检索）

#### 算法原理

利用 sentence-transformers 将电影信息（标题 + 发行年份 + 题材）编码为 **384 维语义向量**，存储在 Qdrant 向量数据库中，通过余弦相似度进行语义搜索。

#### 向量化文本模板

```
Title: {title}. Release Year: {year}. Genres: {genre1, genre2, ...}
```

例如:
```
Title: The Dark Knight. Release Year: 2008. Genres: Action, Crime, Drama.
```

#### Qdrant 集合配置

| 配置项 | 值 |
|--------|-----|
| 集合名称 | `movies` |
| 向量维度 | 384 |
| 距离算法 | Cosine |
| 数据量 | 87,585 条 |
| 模型 | `all-MiniLM-L6-v2` |
| 服务器 | `192.168.1.38:6333` |

#### 数据导入流程

```
MySQL movies 表 ──→ 读取电影数据 ──→ sentence-transformers 编码 ──→ Qdrant upsert
```

导入脚本: `scripts/import_qdrant.py`

---

## 3. 数据源说明

### 3.1 MySQL 数据库 `MovieRecommendSystem`

| 表名 | 记录数 | 用途 |
|------|--------|------|
| `movies` | 87,585 | 电影信息（id, title, release_year, avg_rating, cover_url） |
| `movies_genres` | 关联表 | 电影与题材关联 |
| `genres` | ~20 | 题材字典（id, code, name） |
| `users_movies_behaviors` | ~2500万 | 用户行为（评分、收藏、浏览等），CF 算法的核心数据表 |

### 3.2 Qdrant 向量数据库

| 集合 | 点数 | 用途 |
|------|------|------|
| `movies` | 87,585 | 电影语义向量，用于 Content-Based 相似搜索 |

---

## 4. API 接口文档

### 4.1 接口列表

| 方法 | URL | 说明 |
|------|-----|------|
| GET | `/api/recommend/user-based/:userId` | User-Based CF 推荐 |
| GET | `/api/recommend/item-based/:userId` | Item-Based CF 推荐 |
| GET | `/api/recommend/hybrid/:userId` | 混合推荐 |
| GET | `/api/recommend/neighbors/:userId` | 获取最相似邻居用户 |
| POST | `/api/recommend/clear-cache` | 清除推荐缓存 |

### 4.2 公共参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `k` | query | 20 | KNN 邻居数 |
| `topN` | query | 20 | 返回推荐结果数 |
| `userWeight` | query(hybrid) | 0.5 | User-CF 权重 (0~1) |

### 4.3 接口示例

#### User-Based CF 推荐

```bash
curl "http://localhost:3000/api/recommend/user-based/1?k=10&topN=5"
```

**响应示例：**
```json
{
  "success": true,
  "data": {
    "userId": 1,
    "algorithm": "user-based-cf",
    "k": 10,
    "total": 5,
    "recommendations": [
      {
        "movieId": 25750,
        "title": "Sherlock Jr.",
        "releaseYear": 1924,
        "avgRating": 3.98,
        "predictedRating": 5,
        "coverUrl": ""
      }
    ]
  }
}
```

#### Item-Based CF 推荐

```bash
curl "http://localhost:3000/api/recommend/item-based/1?k=15&topN=10"
```

#### 混合推荐

```bash
curl "http://localhost:3000/api/recommend/hybrid/1?k=10&topN=5&userWeight=0.6"
```

#### 获取邻居

```bash
curl "http://localhost:3000/api/recommend/neighbors/1?k=5"
```

**响应示例：**
```json
{
  "success": true,
  "data": {
    "userId": 1,
    "total": 5,
    "neighbors": [
      { "userId": 38396, "similarity": 0.3822 },
      { "userId": 14027, "similarity": 0.3114 }
    ]
  }
}
```

#### 清除缓存

```bash
curl -X POST "http://localhost:3000/api/recommend/clear-cache"
```

---

## 5. 测试指南

### 5.1 前提条件

确保服务正在运行：

```bash
# Windows
cd d:\Code\MovieRecommendSystem
node --max-old-space-size=4096 backend/server.js
```

> `--max-old-space-size=4096` 参数为 Node.js 分配 4GB 堆内存，防止大数据量处理时 OOM。

### 5.2 快速测试命令

#### 测试所有推荐接口（Windows cmd）

```batch
@echo off
echo ====== User-Based CF ======
curl -s --max-time 120 "http://localhost:3000/api/recommend/user-based/1?k=10&topN=5" | python -m json.tool
echo.
echo ====== Item-Based CF ======
curl -s --max-time 120 "http://localhost:3000/api/recommend/item-based/1?k=10&topN=5" | python -m json.tool
echo.
echo ====== Hybrid ======
curl -s --max-time 120 "http://localhost:3000/api/recommend/hybrid/1?k=10&topN=5&userWeight=0.5" | python -m json.tool
echo.
echo ====== Neighbors ======
curl -s --max-time 120 "http://localhost:3000/api/recommend/neighbors/1?k=5" | python -m json.tool
```

#### 测试 Content-Based（Qdrant 语义搜索）

```python
# test_qdrant.py
from qdrant_client import QdrantClient

client = QdrantClient(host="192.168.1.38", port=6333)

# 方法1: 直接向量搜索
results = client.query(
    collection_name="movies",
    query_text="Action adventure superhero",
    limit=5
)
for r in results:
    print(f"ID={r.id} | {r.payload.get('title','')} | score={r.score:.3f}")

# 方法2: 按电影 ID 查找相似电影
similar = client.recommend(
    collection_name="movies",
    positive=[1],  # 电影 ID=1 的向量
    limit=5
)
for r in similar:
    print(f"ID={r.id} | {r.payload.get('title','')} | score={r.score:.3f}")
```

### 5.3 性能测试

| 算法 | 数据集大小 | 预期耗时 | 说明 |
|------|-----------|---------|------|
| User-Based CF | 2500万行为数据 | 30~60秒 | 首请求，后续缓存命中则 < 1ms |
| Item-Based CF | 2500万行为数据 | 60~120秒 | 计算量较大 |
| Hybrid | 同上 | 两者之和 | 同时执行两个算法 |
| Content-Based | 87,585向量 | < 1秒 | 基于 Qdrant 向量检索 |

### 5.4 缓存说明

- 邻居计算结果会缓存 **30 分钟**
- 缓存基于内存的 `Map` 实现
- 通过 `POST /api/recommend/clear-cache` 手动清除
- 服务重启后缓存自动清空

### 5.5 监控推荐结果

```bash
# 查看服务端日志
# 后端日志输出格式：
[User-Based CF] 用户 1, K=10, TopN=5
[User-Based CF] 候选邻居: 500 个用户
[User-Based CF] 最相似用户: 38396 (相似度: 0.3822)
[User-Based CF] 完成: 5 个推荐, 耗时 43443ms
```

---

## 6. 性能优化策略

### 6.1 SQL 驱动（解决 OOM）

原始方案将 2500 万行评分数据全量加载到内存（约 2~3GB），导致 Node.js OOM。

**优化方案：**

1. **SQL JOIN 筛选** — 用 `JOIN + GROUP BY + HAVING` 在数据库端筛选候选用户，只返回符合条件的用户 ID，无需加载全量数据
2. **分批加载** — 候选用户评分数据按 100 个用户一批加载，大幅降低单次内存占用
3. **轻量缓存** — 邻居计算结果缓存 30 分钟，避免重复计算

### 6.2 数据库索引优化

确保以下索引存在以加速查询：

```sql
-- 协同过滤核心查询索引
ALTER TABLE users_movies_behaviors ADD INDEX idx_user_behavior (user_id, behavior_type, rating);
ALTER TABLE users_movies_behaviors ADD INDEX idx_movie_behavior (movie_id, behavior_type, rating);

-- 电影元数据查询
ALTER TABLE movies ADD INDEX idx_movies_id (id);
ALTER TABLE movies ADD INDEX idx_movies_avg_rating (avg_rating DESC);
```

### 6.3 Node.js 内存配置

```bash
node --max-old-space-size=4096 backend/server.js
```

---

## 7. 常见问题

### Q: 推荐 API 返回空列表？

**可能原因：**

1. 目标用户评分记录太少（少于 5 条），低于 `MIN_RATINGS_FOR_USER` 阈值
2. 没有找到共同评分用户（共同评分项少于 3 个）
3. 请求超时（默认 120 秒），需要增大 curl 的 `--max-time` 参数

### Q: 请求超时怎么办？

- 增大 curl 超时时间: `curl --max-time 300 "http://localhost:3000/api/recommend/user-based/1"`
- 减少 K 和 TopN 值: `?k=5&topN=3`
- 检查服务器日志确认算法执行进度

### Q: Node.js 报 OOM 错误？

- 确认启动时使用了 `--max-old-space-size=4096` 参数
- 当前 SQL 驱动版已大幅降低内存占用，正常情况下不应再出现 OOM

### Q: Qdrant 连接失败？

- 确认 Docker 容器运行中：`docker ps | grep qdrant`
- 确认端口可访问：`curl http://192.168.1.38:6333/healthz`
- 确认防火墙允许 6333 端口

### Q: How to reimport data to Qdrant?

```bash
# 先删除原有集合再重新导入
python scripts/import_qdrant.py
```

---

## 附录

### A. 相关文件索引

| 文件 | 说明 |
|------|------|
| `backend/src/services/recommendService.js` | 算法核心实现 |
| `backend/src/controllers/recommendController.js` | API 控制器 |
| `backend/src/routes/recommend.js` | 路由定义 |
| `backend/server.js` | 服务入口 |
| `scripts/import_qdrant.py` | Qdrant 数据导入脚本 |
| `scripts/import_qdrant_remaining.py` | 补录脚本 |

### B. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-04-30 | v1.0 | 初始版本 — SQL 驱动版 User-CF + Item-CF + Hybrid + Qdrant Content-Based |