# 推荐算法修改计划

> 本文档基于当前代码分析，总结推荐系统存在的问题及修改方案，指导下一步修改避免编译/运行错误。
> 最后更新: 2026-05-03

---

## 目录

1. [当前代码分析](#1-当前代码分析)
2. [存在问题清单](#2-存在问题清单)
3. [修改计划与方案](#3-修改计划与方案)
4. [修改步骤与注意事项](#4-修改步骤与注意事项)
5. [风险与回退方案](#5-风险与回退方案)

---

## 1. 当前代码分析

### 1.1 核心文件

| 文件 | 行数 | 功能 |
|------|------|------|
| `backend/src/services/recommendService.js` | 488 | 算法核心：User-CF、Item-CF、Hybrid 实现 |
| `backend/src/controllers/recommendController.js` | 156 | API 控制器：参数校验、结果返回 |
| `backend/src/routes/recommend.js` | 20 | 路由注册：5个 API 端点 |

### 1.2 现有 API 端点

```javascript
// routes/recommend.js
GET  /api/recommend/user-based/:userId   // User-Based CF
GET  /api/recommend/item-based/:userId   // Item-Based CF
GET  /api/recommend/hybrid/:userId       // 混合推荐
GET  /api/recommend/neighbors/:userId    // 邻居查询
POST /api/recommend/clear-cache          // 清除缓存
```

### 1.3 现有算法实现

| 算法 | 函数 | 实现方式 | 复杂度 |
|------|------|----------|--------|
| User-Based CF | `userBasedCF()` | SQL 驱动 + Pearson 相关系数 | O(n × m)，n=候选用户，m=共同评分电影 |
| Item-Based CF | `itemBasedCF()` | SQL 驱动 + Cosine 相似度 | O(m × p)，m=用户评分数，p=候选电影数 |
| 混合推荐 | `hybridRecommendation()` | 加权融合（userWeight=0.5） | = User-CF + Item-CF |
| 邻居查询 | `findKNearestUsers()` | 与 User-CF 共用邻居逻辑 | 与 User-CF 相同 |

---

## 2. 存在问题清单

### 2.1 功能缺失（Bug）

| 编号 | 问题 | 严重性 | 涉及文件 | 说明 |
|------|------|--------|----------|------|
| P0-1 | **热门/新片/趋势推荐 API 未实现** | 🔴 高 | recommendService、recommendController、routes | 前端 user-dashboard.html 引用了 `/api/recommend/popular`、`/api/recommend/new-releases`、`/api/recommend/trending`，但后端没有这些端点 |
| P0-2 | **基于内容推荐 API 未实现** | 🔴 高 | recommendService、recommendController、routes | Qdrant 向量数据库已部署（87,585 条向量），但后端 service 没有调用 Qdrant 的逻辑 |
| P0-3 | **请求超时未设置** | 🟡 中 | recommendController | 无请求超时保护，大数据量计算可能导致 HTTP 连接挂起 |

### 2.2 算法缺陷

| 编号 | 问题 | 严重性 | 说明 |
|------|------|--------|------|
| P1-1 | **数据稀疏时无回退** | 🔴 高 | 当用户评分 < 5 条时直接返回空数组，没有任何兜底推荐 |
| P1-2 | **混合权重固定** | 🟡 中 | `userWeight=0.5` 硬编码，无法根据用户活跃度动态调整 |
| P1-3 | **推荐结果无去重** | 🟡 中 | 不同算法结果可能包含同一部电影，但没有去重逻辑 |
| P1-4 | **低相似度过滤阈值** | 🟡 中 | `sim <= 0.01` 过滤阈值太低，可能包含大量低质量推荐 |
| P1-5 | **Pearson 相关系数为负时丢弃** | 🟡 中 | `if (sim > 0)` 丢弃了负相关用户，但负相关用户可以提供反向推荐 |

### 2.3 性能与缓存问题

| 编号 | 问题 | 严重性 | 说明 |
|------|------|--------|------|
| P2-1 | **Item-Based CF 嵌套循环** | 🔴 高 | 双重 for 循环遍历 `sourceMovieRatings × candidateRatings`，当用户评分电影多时计算量爆炸（O(m × p)） |
| P2-2 | **只缓存邻居，不缓存结果** | 🟡 中 | 缓存粒度太粗，相同参数组合无法复用推荐结果 |
| P2-3 | **无 Redis 缓存集成** | 🟢 低 | 现有缓存用 Map 实现，服务重启丢失；cacheMiddleware.js 的 Redis 缓存未在推荐模块使用 |

### 2.4 代码质量问题

| 编号 | 问题 | 严重性 | 说明 |
|------|------|--------|------|
| P3-1 | **无输入参数范围校验** | 🟡 中 | k、topN 无上限限制，恶意请求可导致 OOM |
| P3-2 | **错误处理不完善** | 🟡 中 | 所有错误统一 catch，没有区分业务错误和系统错误 |
| P3-3 | **日志过于简单** | 🟢 低 | 只有 `console.log`，无结构化日志、无耗时告警 |

---

## 3. 修改计划与方案

### 3.1 P0 级别：功能补齐（必须修改）

#### 3.1.1 新增热门推荐 `getPopularRecommendations()`

**作用**: 根据评分数量和评分均值计算热门电影

**需修改文件**:
- `backend/src/services/recommendService.js` — 新增 `getPopularRecommendations()`
- `backend/src/controllers/recommendController.js` — 新增 `popularRecommend()`
- `backend/src/routes/recommend.js` — 新增 `GET /api/recommend/popular`

**实现方案**:
```javascript
async function getPopularRecommendations(page = 1, pageSize = 20, genre = null) {
  const offset = (page - 1) * pageSize;
  let sql = `
    SELECT m.id, m.title, m.release_year, m.avg_rating, 
           COUNT(r.movie_id) AS rating_count
    FROM movies m
    LEFT JOIN users_movies_behaviors r 
      ON m.id = r.movie_id AND r.behavior_type = 'rate'
  `;
  const params = [];
  if (genre) {
    sql += ` JOIN movies_genres mg ON m.id = mg.movie_id
             JOIN genres g ON mg.genre_id = g.id AND g.code = ?`;
    params.push(genre);
  }
  sql += ` GROUP BY m.id
           ORDER BY rating_count DESC, m.avg_rating DESC
           LIMIT ? OFFSET ?`;
  params.push(pageSize, offset);

  const results = await query(sql, params);
  return results.map(r => ({
    movieId: r.id,
    title: r.title,
    releaseYear: r.release_year,
    avgRating: parseFloat(r.avg_rating) || 0,
    predictedRating: Math.min(5, (parseFloat(r.avg_rating) || 0) + 
                              Math.log10(parseInt(r.rating_count) + 1) * 0.5),
    coverUrl: ''
  }));
}
```

> ⚠️ **注意**: 大表 `users_movies_behaviors` 有 2500 万行，`COUNT(*)` 聚合可能很慢。确保有 `idx_movie_behavior` 索引。

#### 3.1.2 新增新片推荐 `getNewReleaseRecommendations()`

**作用**: 按发布日期推荐最新电影

**需修改文件**:
- `backend/src/services/recommendService.js` — 新增 `getNewReleaseRecommendations()`
- `backend/src/controllers/recommendController.js` — 新增 `newReleaseRecommend()`
- `backend/src/routes/recommend.js` — 新增 `GET /api/recommend/new-releases`

```javascript
async function getNewReleaseRecommendations(page = 1, pageSize = 20) {
  const offset = (page - 1) * pageSize;
  const results = await query(`
    SELECT id, title, release_year, avg_rating, cover_url
    FROM movies
    ORDER BY release_year DESC, avg_rating DESC
    LIMIT ? OFFSET ?
  `, [pageSize, offset]);
  
  return results.map(m => ({
    movieId: m.id,
    title: m.title,
    releaseYear: m.release_year,
    avgRating: parseFloat(m.avg_rating) || 0,
    predictedRating: parseFloat(m.avg_rating) || 0,
    coverUrl: m.cover_url || ''
  }));
}
```

> ⚠️ **注意**: 如果没有 `release_year` 索引，`ORDER BY release_year DESC` 会导致文件排序，效率低。需要确认索引或限制数据量。

#### 3.1.3 新增趋势推荐 `getTrendingRecommendations()`

**作用**: 基于近期评分活跃度推荐

**需修改文件**:
- `backend/src/services/recommendService.js` — 新增 `getTrendingRecommendations()`
- `backend/src/controllers/recommendController.js` — 新增 `trendingRecommend()`
- `backend/src/routes/recommend.js` — 新增 `GET /api/recommend/trending`

```javascript
async function getTrendingRecommendations(page = 1, pageSize = 20, timeRange = '7d') {
  const offset = (page - 1) * pageSize;
  const daysMap = { '7d': 7, '30d': 30, '90d': 90 };
  const days = daysMap[timeRange] || 7;
  
  const results = await query(`
    SELECT m.id, m.title, m.release_year, m.avg_rating, m.cover_url,
           COUNT(r.movie_id) AS recent_count
    FROM movies m
    JOIN users_movies_behaviors r ON m.id = r.movie_id
    WHERE r.behavior_type = 'rate'
      AND r.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)
    GROUP BY m.id
    HAVING recent_count >= 5
    ORDER BY recent_count DESC, m.avg_rating DESC
    LIMIT ? OFFSET ?
  `, [days, pageSize, offset]);
  
  return results.map(m => ({
    movieId: m.id,
    title: m.title,
    releaseYear: m.release_year,
    avgRating: parseFloat(m.avg_rating) || 0,
    predictedRating: Math.min(5, (parseFloat(m.avg_rating) || 0) + 
                              Math.log10(parseInt(m.recent_count)) * 0.3),
    coverUrl: m.cover_url || ''
  }));
}
```

> ⚠️ **注意**: `users_movies_behaviors` 表需要有 `created_at` 字段（或 `rated_at` 时间戳字段）。数据库 schema 需要确认。

#### 3.1.4 新增基于内容推荐 `getContentBasedRecommendations()`（Qdrant 方式）

**作用**: 利用 Qdrant 向量检索做基于内容的推荐

**需修改文件**:
- `backend/src/services/recommendService.js` — 新增 `getContentBasedRecommendations()`，引入 `@qdrant/js-client-rest`
- `backend/src/controllers/recommendController.js` — 新增 `contentBasedRecommend()`
- `backend/src/routes/recommend.js` — 新增 `GET /api/recommend/content-based/:userId`
- `backend/package.json` — 确认安装 `@qdrant/js-client-rest`

```javascript
const { QdrantClient } = require('@qdrant/js-client-rest');

const qdrantClient = new QdrantClient({ 
  host: process.env.QDRANT_HOST || '192.168.1.38', 
  port: parseInt(process.env.QDRANT_PORT) || 6333 
});

async function getContentBasedRecommendations(userId, page = 1, pageSize = 20) {
  // 1. 获取用户评分最高的电影
  const topMovies = await query(`
    SELECT movie_id, rating 
    FROM users_movies_behaviors 
    WHERE user_id = ? AND behavior_type = 'rate' AND rating IS NOT NULL
    ORDER BY rating DESC
    LIMIT 5
  `, [userId]);
  
  if (topMovies.length === 0) {
    // 用户无评分时返回热门推荐
    return getPopularRecommendations(page, pageSize);
  }
  
  // 2. 使用最高评分电影作为 positive 向量查询 Qdrant
  const positiveIds = topMovies.filter(r => r.rating >= 4).map(r => r.movie_id);
  const negativeIds = topMovies.filter(r => r.rating <= 2).map(r => r.movie_id);
  
  const offset = (page - 1) * pageSize;
  const searchResult = await qdrantClient.recommend('movies', {
    positive: positiveIds.length > 0 ? positiveIds : [topMovies[0].movie_id],
    negative: negativeIds.length > 0 ? negativeIds : undefined,
    limit: pageSize,
    offset: offset
  });
  
  // 3. 组装返回结果
  return searchResult.map(r => ({
    movieId: r.id,
    title: r.payload?.title || '',
    releaseYear: r.payload?.release_year || 0,
    avgRating: r.payload?.avg_rating || 0,
    predictedRating: parseFloat(r.score.toFixed(2)),
    coverUrl: r.payload?.cover_url || ''
  }));
}
```

> ⚠️ **注意**: 需要先确认 `@qdrant/js-client-rest` 包的安装情况。Qdrant 导航栏需确保 `recommend` API 的输入参数兼容。

---

### 3.2 P1 级别：算法优化（建议修改）

#### 3.2.1 稀疏数据回退机制

**作用**: 当用户评分数据不足时，自动降级到热门/新片推荐

**修改文件**: `backend/src/services/recommendService.js`

```javascript
// 在 userBasedCF 和 itemBasedCF 中增加回退
const MIN_RATINGS_FOR_USER = 5;
const FALLBACK_POPULAR_COUNT = 10;

// userBasedCF 中:
if (targetRatings.length < MIN_RATINGS_FOR_USER) {
  console.log(`[User-Based CF] 用户 ${userId} 评分数不足，回退到内容推荐`);
  return [];  // 改为 return getFallbackRecommendations(targetRatings);
}

// 新增回退函数
async function getFallbackRecommendations(limitedRatings, topN) {
  if (limitedRatings.length === 0) {
    // 完全没有评分：返回热门推荐
    return (await getPopularRecommendations(1, topN))
      .map(r => ({ movieId: r.movieId, predictedRating: r.predictedRating }));
  }
  // 有少量评分：使用这些评分电影的题材作为推荐依据
  const movieIds = limitedRatings.map(r => r.movie_id);
  const placeholders = movieIds.map(() => '?').join(',');
  // ... 通过题材相似性找同类电影
}
```

> ⚠️ **注意**: 此处需要改为非空返回而非空数组，否则前端会一直显示"无推荐"。

#### 3.2.2 自适应混合权重

**作用**: 根据用户活跃度动态调整 User-CF 和 Item-CF 权重

**修改文件**: `backend/src/services/recommendService.js`

```javascript
// 在 hybridRecommendation 中增加
async function getAdaptiveWeight(userId) {
  const stats = await query(`
    SELECT COUNT(*) AS rating_count, 
           COUNT(DISTINCT movie_id) AS unique_movies
    FROM users_movies_behaviors 
    WHERE user_id = ? AND behavior_type = 'rate'
  `, [userId]);
  
  const count = stats[0]?.rating_count || 0;
  const uniqueMovies = stats[0]?.unique_movies || 0;
  
  if (count < 10) return 0.3;    // 新用户 → 倾向 Item-CF
  if (count < 50) return 0.5;    // 一般用户 → 均衡
  return 0.7;                     // 活跃用户 → 倾向 User-CF
}
```

#### 3.2.3 推荐结果去重

**作用**: 保证推荐列表中电影不重复

**修改文件**: `backend/src/services/recommendService.js`

```javascript
// 在 enrichRecommendations 中或在返回前增加去重
function deduplicateAndStabilize(recommendations) {
  const seen = new Set();
  const result = [];
  for (const item of recommendations) {
    if (!seen.has(item.movieId)) {
      seen.add(item.movieId);
      result.push(item);
    }
  }
  return result;
}
```

#### 3.2.4 增加请求超时保护

**作用**: 防止长时间运行导致 HTTP 连接挂起

**修改文件**: `backend/src/controllers/recommendController.js`

```javascript
// 在每个 handler 中增加超时控制
const REQUEST_TIMEOUT = 120000; // 120秒

async function withTimeout(promise, ms = REQUEST_TIMEOUT) {
  const timeoutPromise = new Promise((_, reject) => 
    setTimeout(() => reject(new Error('请求超时')), ms)
  );
  return Promise.race([promise, timeoutPromise]);
}

// 使用:
const recommendations = await withTimeout(
  recommendService.userBasedCF(userId, k, topN)
);
```

---

### 3.3 P2 级别：性能优化

#### 3.3.1 Item-Based CF 循环优化

**作用**: 减少 Item-Based CF 的嵌套循环计算量

**修改文件**: `backend/src/services/recommendService.js`

**当前问题**:
```javascript
// 双重循环: O(用户评分电影数 × 候选电影数)
for (const [ratedMovieId, userRating] of targetRatingMap) {
  for (const [candidateMovieId, candidateUserRatings] of candidateRatings) {
    // 计算 Cosine 相似度...
  }
}
```

**优化方案**: 使用矩阵转置或预计算相似度缓存

```javascript
// 方案：先计算用户评分电影之间的相似度矩阵并缓存
// 推荐使用 Qdrant 替代这部分逻辑（参考 3.1.4）
```

#### 3.3.2 引入 Redis 缓存推荐结果

**作用**: 加速重复查询，减少数据库负载

**修改文件**: `backend/src/middleware/cacheMiddleware.js` 集成到推荐流程

```javascript
// 在 recommendService 中新增
async function getCachedOrCompute(cacheKey, computeFn, ttl = 600) {
  // 1. 尝试从 Redis 获取
  // 2. 如果命中，直接返回
  // 3. 如果未命中，执行 computeFn 并缓存结果
}
```

---

## 4. 修改步骤与注意事项

### 4.1 推荐修改顺序

```
Step 1: 新增热门/新片/趋势推荐 （P0-1、P0-2）
   ├── 先确认 database/init.sql 中 users_movies_behaviors 表结构
   ├── 先确认是否有 created_at/rated_at 字段
   ├── 在 recommendService.js 添加 3 个新函数
   ├── 在 recommendController.js 添加 3 个新 handler
   └── 在 routes/recommend.js 添加 3 个新路由

Step 2: 新增基于内容推荐（Qdrant）（P0-2）
   ├── 检查 @qdrant/js-client-rest 版本兼容性
   ├── 确认 Qdrant 集合字段名（payload 中的字段名）
   ├── 在 recommendService.js 添加函数
   └── 新增 controller 和 route

Step 3: 稀疏数据回退 + 自适应权重（P1-1、P1-2）
   ├── 修改 userBasedCF 的早期返回逻辑
   ├── 修改 hybridRecommendation 的权重策略
   └── 注意测试边缘情况（新用户、低活跃用户）

Step 4: 去重 + 超时保护（P1-3、P0-3）
   ├── 在 enrichRecommendations 或各 handler 中加去重
   └── 在 controller 中加超时包装

Step 5: 性能优化（P2-1、P2-2、P2-3）
   ├── Item-Based CF 循环优化
   └── Redis 缓存集成
```

### 4.2 修改前检查清单

- [ ] 确认 `users_movies_behaviors` 表结构，特别是时间字段名
  ```sql
  DESCRIBE users_movies_behaviors;
  -- 确认存在: created_at / rated_at / updated_at
  ```
- [ ] 确认 `@qdrant/js-client-rest` 是否已安装
  ```bash
  npm list @qdrant/js-client-rest --prefix backend
  ```
- [ ] 确认 Qdrant 集合 payload 字段名
  ```python
  # 用 Python 检查
  from qdrant_client import QdrantClient
  client = QdrantClient("192.168.1.38", port=6333)
  points = client.retrieve("movies", [1])
  print(points[0].payload)  # 查看字段
  ```
- [ ] 确认 `movies_genres` 和 `genres` 表结构（用于按题材过滤热门推荐）
  ```sql
  DESCRIBE movies_genres;
  DESCRIBE genres;
  ```
- [ ] 确认数据库索引状态
  ```sql
  SHOW INDEX FROM users_movies_behaviors;
  -- 确保有 idx_movie_behavior 和 idx_user_behavior
  ```

### 4.3 修改时的注意事项

#### 4.3.1 避免破坏现有功能

1. **不要修改现有函数的签名** — 保持 `userBasedCF(userId, k, topN)` 签名不变，只增不删
2. **不要改变现有函数的默认行为** — 新增函数用新的函数名，不要修改旧函数逻辑
3. **新增路由不要与现有路由冲突** — 使用清晰的路径名

#### 4.3.2 常见编译/运行错误

| 错误场景 | 原因 | 避免方式 |
|----------|------|----------|
| `Cannot find module '@qdrant/js-client-rest'` | 包未安装 | 先 `npm install @qdrant/js-client-rest --prefix backend` |
| `TypeError: qdrantClient.recommend is not a function` | API 版本不兼容 | 检查 qdrant-client 版本，`v1.7+` 语法可能有变化 |
| `MySQL Unknown column 'created_at'` | 表无此字段 | 先查询表结构确认字段名 |
| `RangeError: Invalid array length` | 数据量过大 | 加 LIMIT/分页，校验输入参数范围 |
| `TimeoutError: Request timed out` | 计算时间 > 120s | 加超时控制，减少默认 k/topN |

#### 4.3.3 数据库索引要求

新增的热门/趋势/新片推荐需要以下索引：

```sql
-- 热门推荐 (COUNT + ORDER BY)
ALTER TABLE users_movies_behaviors 
  ADD INDEX idx_movie_behavior_rate (movie_id, behavior_type, rating);

-- 新片推荐 (ORDER BY release_year)
ALTER TABLE movies 
  ADD INDEX idx_movies_release_year (release_year DESC);

-- 趋势推荐 (WHERE created_at + GROUP BY)
ALTER TABLE users_movies_behaviors 
  ADD INDEX idx_behavior_time (behavior_type, created_at);
```

### 4.4 测试验证

#### 4.4.1 单元测试

```bash
# 启动服务器
cd d:\Code\MovieRecommendSystem
node --max-old-space-size=4096 backend/server.js
```

#### 4.4.2 接口测试

```bash
# 测试热门推荐
curl "http://localhost:3000/api/recommend/popular?page=1&pageSize=5"

# 测试新片推荐
curl "http://localhost:3000/api/recommend/new-releases?page=1&pageSize=5"

# 测试趋势推荐
curl "http://localhost:3000/api/recommend/trending?page=1&pageSize=5&timeRange=7d"

# 测试基于内容推荐（Qdrant）
curl "http://localhost:3000/api/recommend/content-based/1?page=1&pageSize=5"

# 验证现有接口不受影响
curl "http://localhost:3000/api/recommend/user-based/1?k=10&topN=5"
curl "http://localhost:3000/api/recommend/hybrid/1?k=10&topN=5&userWeight=0.5"
```

#### 4.4.3 异常场景测试

| 测试场景 | 预期行为 |
|----------|----------|
| 用户 ID 为负数 | 返回 400，`无效的用户ID` |
| 用户无评分记录 | 返回热门推荐降级（非空数组） |
| pageSize > 100 | 限制到最大值 100 |
| Qdrant 连接失败 | 降级回 SQL 基于内容的推荐 |
| 趋势推荐 timeRange 非法值 | 默认回退到 7d |

---

## 5. 风险与回退方案

### 5.1 风险评估

| 风险 | 概率 | 影响 | 应对方案 |
|------|------|------|----------|
| Qdrant 客户端版本兼容 | 中 | 内容推荐无法工作 | 降级回 SQL 方式；使用 Python 中间件桥接 |
| 热门推荐 COUNT 查询超时 | 高（2500万行） | API 响应慢 | 加索引；预计算热门榜定时更新 |
| Item-Based CF 性能退化 | 中 | 推荐耗时 > 120s | 加 LIMIT 限制循环次数；优先使用 Qdrant |
| 新增路由与前端不兼容 | 低 | 前端 404 | 与前端同步测试，保证字段名一致 |

### 5.2 回退方案

如果修改后出现严重问题：

```bash
# 1. 回退 recommendService.js
git checkout -- backend/src/services/recommendService.js

# 2. 回退 recommendController.js
git checkout -- backend/src/controllers/recommendController.js

# 3. 回退 routes/recommend.js
git checkout -- backend/src/routes/recommend.js
```

### 5.3 增量修改建议

不要一次性修改所有文件，建议分批次提交：

1. **第 1 次提交**: 热门 + 新片 + 趋势推荐（P0-1）
2. **第 2 次提交**: 基于内容推荐 Qdrant 集成（P0-2）
3. **第 3 次提交**: 稀疏数据回退 + 自适应权重（P1-1, P1-2）
4. **第 4 次提交**: 去重 + 超时保护 + 输入校验（P1-3, P0-3, P3-1）
5. **第 5 次提交**: 性能优化（P2-1, P2-2, P2-3）

---

## 附录

### A. 当前数据库 `users_movies_behaviors` 表结构参考

> 需要运行 `DESCRIBE users_movies_behaviors` 确认

```sql
CREATE TABLE users_movies_behaviors (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL,
  movie_id INT NOT NULL,
  behavior_type VARCHAR(20) NOT NULL,  -- 'rate', 'collect', 'view'
  rating DECIMAL(2,1) DEFAULT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  -- 可能字段: rated_at, updated_at
  INDEX idx_user_behavior (user_id, behavior_type, rating),
  INDEX idx_movie_behavior (movie_id, behavior_type, rating)
);
```

### B. 前端已引用的待实现 API

从 `frontend/public/user-dashboard.html` 和前端代码中发现的 API 调用：

| API 路径 | 前端文件 | 状态 |
|----------|----------|------|
| `/api/recommend/popular` | user-dashboard.html | ❌ 未实现 |
| `/api/recommend/new-releases` | user-dashboard.html | ❌ 未实现 |
| `/api/recommend/trending` | user-dashboard.html | ❌ 未实现 |
| `/api/recommend/content-based` | 可能被引用 | ❌ 未实现 |

### C. 相关文件

| 文件 | 说明 |
|------|------|
| `backend/src/services/recommendService.js` | 主修改文件 |
| `backend/src/controllers/recommendController.js` | 辅助修改文件 |
| `backend/src/routes/recommend.js` | 路由注册文件 |
| `backend/src/middleware/cacheMiddleware.js` | 缓存中间件（可选集成） |
| `backend/server.js` | 服务入口（路由挂载验证） |
| `database/init.sql` | 数据库初始化脚本 |
| `docs/backend/recommendation-system-guide.md` | 现有推荐系统文档 |