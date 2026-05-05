# 推荐算法增强开发计划

> 本文档总结当前推荐系统的增强计划，包括协同过滤算法的工业级落地方案、多路召回架构设计，以及与现有 Qdrant 内容推荐的融合策略。
> 
> 最后更新: 2026-05-03

---

## 目录

1. [算法概念回顾](#1-算法概念回顾)
2. [当前系统分析](#2-当前系统分析)
3. [离线计算方案](#3-离线计算方案)
4. [线上服务架构](#4-线上服务架构)
5. [协同过滤 + Qdrant 多路召回](#5-协同过滤--qdrant-多路召回)
6. [落地路线图](#6-落地路线图)
7. [数据库与基础设施](#7-数据库与基础设施)
8. [附录](#8-附录)

---

## 1. 算法概念回顾

### 1.1 基于用户的协同过滤 (User-Based CF)

**核心逻辑：** 如果用户 $A$ 和用户 $B$ 过去喜欢的物品相似，则系统将 $B$ 喜欢但 $A$ 没看过的物品推荐给 $A$。

#### 计算用户相似度

使用**余弦相似度 (Cosine Similarity)**：

$$w_{uv} = \frac{|N(u) \cap N(v)|}{\sqrt{|N(u)| \cdot |N(v)|}}$$

- $N(u)$：用户 $u$ 有过行为的物品集合
- 分母是几何平均，用于惩罚"购买力过强"的活跃用户对相似度的干扰

#### 预测评分

$$P(u, i) = \sum_{v \in S(u, K) \cap N(i)} w_{uv} \cdot r_{vi}$$

- $S(u, K)$：与用户 $u$ 最相似的 $K$ 个用户
- $N(i)$：对物品 $i$ 有过行为的用户集合
- $r_{vi}$：用户 $v$ 对物品 $i$ 的实际评分

### 1.2 基于物品的协同过滤 (Item-Based CF)

**核心逻辑：** 如果用户 $A$ 喜欢物品 $i$，而物品 $i$ 与物品 $j$ 相似（喜欢这两个物品的用户群体高度重合），则系统将 $j$ 推荐给 $A$。

#### 计算物品相似度

$$w_{ij} = \frac{|N(i) \cap N(j)|}{\sqrt{|N(i)| \cdot |N(j)|}}$$

- $N(i)$：喜欢物品 $i$ 的用户集合
- 实际应用中常对分母微调（加大对热门物品的惩罚）以修正热门效应

#### 预测评分

$$P(u, j) = \sum_{i \in N(u) \cap S(j, K)} w_{ji} \cdot r_{ui}$$

- $S(j, K)$：与物品 $j$ 最相似的 $K$ 个物品集合
- $N(u)$：用户 $u$ 喜欢的物品集合

### 1.3 两种算法的对比

| 维度 | User-Based CF | Item-Based CF |
|------|--------------|--------------|
| 相似度对象 | 用户与用户 | 物品与物品 |
| 适用场景 | 社交属性强、挖掘惊喜推荐 | 电影/电商推荐的主力算法 |
| 稳定性 | 用户口味易变，需频繁更新 | 物品属性稳定，结果可缓存 |
| 冷启动 | 新用户无历史行为难做 | 新物品无交互数据难做 |

---

## 2. 当前系统分析

### 2.1 现有实现概览

当前系统已有协同过滤算法的**实时计算版本**（SQL 驱动），但存在以下不足：

| 组件 | 当前状态 | 问题 |
|------|---------|------|
| User-Based CF | ✅ 已实现（Pearson 相关系数） | 实时计算，大数据量下耗时 30~60s |
| Item-Based CF | ✅ 已实现（Cosine 相似度） | 嵌套循环，计算量大（60~120s） |
| Hybrid | ✅ 已实现（加权融合） | 权重固定，无自适应 |
| Content-Based (Qdrant) | ✅ 向量已导入 | 后端未封装 API |
| 热门/新片/趋势推荐 | ❌ 未实现 | 前端已引用但后端缺失 |
| 离线预计算 | ❌ 未实现 | 每次请求实时计算，缺乏缓存 |
| 多路召回 | ❌ 未实现 | 仅单路 CF 推荐 |

### 2.2 数据规模

| 数据项 | 规模 |
|--------|------|
| 用户行为记录 | ~2500 万行（`users_movies_behaviors`） |
| 电影数量 | 87,585 部 |
| Qdrant 向量 | 87,585 条（384 维，Cosine 距离） |

### 2.3 现有文件结构

| 文件 | 说明 |
|------|------|
| `backend/src/services/recommendService.js` | 算法核心实现（实时 SQL 驱动版） |
| `backend/src/controllers/recommendController.js` | API 请求处理 |
| `backend/src/routes/recommend.js` | 路由注册 |
| `backend/src/middleware/cacheMiddleware.js` | 缓存中间件（Redis 待集成） |
| `scripts/import_qdrant.py` | Qdrant 数据导入脚本 |

---

## 3. 离线计算方案

MovieLens 32M 数据量庞大（千万级评分），**不能**在每次页面刷新时实时计算。标准工业级做法是：**离线计算（T+1 定时任务）+ 线上缓存读取**。

### 3.1 数据准备与矩阵构建

```python
import pandas as pd
import mysql.connector

# 从 MySQL 增量或全量拉取评分记录
query = """
    SELECT user_id, movie_id, rating 
    FROM user_movie_behavior 
    WHERE behavior_type = 'rate'
"""
df = pd.read_sql(query, conn)
```

> **注意：** 单机 Pandas 处理 2500 万行数据可能内存溢出。生产环境中建议：
> - 使用 PySpark 集群处理
> - 或抽取最近半年的活跃数据（减少数据量）
> - 或使用 `implicit` 库的稀疏矩阵格式

### 3.2 Item-Based CF 离线计算流程

```
Step 1: 从 MySQL 拉取全量/增量评分数据
         ↓
Step 2: 构建 用户-物品 评分矩阵（稀疏矩阵格式）
         ↓
Step 3: 计算电影间的余弦相似度矩阵
         ├── 使用 scikit-learn: cosine_similarity
         └── 或使用 implicit: 更适合大规模稀疏矩阵
         ↓
Step 4: 对每部电影，提取 Top-K 最相似电影
         ↓
Step 5: 存入 MySQL 表 movie_similarity_cache
         └── 结构: {movie_id, similar_movies (JSON), updated_at}
         ↓
Step 6: 同时存入 Redis（可选）
         └── Key: movie_sim:{movie_id}
         └── Value: [similar_movie_ids...]
```

**入库表结构：**

```sql
CREATE TABLE movie_similarity_cache (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  movie_id INT NOT NULL UNIQUE,
  similar_movies JSON NOT NULL,    -- [{"movie_id": 123, "similarity": 0.85}, ...]
  top_k INT NOT NULL DEFAULT 20,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_movie_id (movie_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.3 User-Based CF 离线计算流程

```
Step 1: 从 MySQL 拉取用户行为数据
         ↓
Step 2: 计算用户间的相似度矩阵
         ├── 用户量大时计算复杂度 O(n²)，不可行
         └── 推荐使用 ALS 矩阵分解替代（见第 3.4 节）
         ↓
Step 3: (替代方案) 对活跃用户预计算推荐列表
         ↓
Step 4: 存入 MySQL 表 user_recommend_cache
         └── 结构: {user_id, recommend_movies (JSON), updated_at}
```

**入库表结构：**

```sql
CREATE TABLE user_recommend_cache (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL UNIQUE,
  recommend_movies JSON NOT NULL,   -- [{"movie_id": 123, "score": 4.5}, ...]
  algorithm VARCHAR(20) NOT NULL,   -- 'user_cf', 'item_cf', 'hybrid'
  top_n INT NOT NULL DEFAULT 20,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 3.4 进阶方案：矩阵分解 (ALS)

传统的 User/Item CF 在稀疏矩阵下效果打折扣，且计算复杂度极高。**ALS (交替最小二乘法)** 是更好的替代方案。

#### 原理

ALS 将用户-评分矩阵分解为两个低维矩阵的乘积：

$$R_{m \times n} \approx U_{m \times k} \cdot V_{k \times n}$$

- $U$：用户特征矩阵（每行代表一个用户的隐特征向量）
- $V$：物品特征矩阵（每列代表一个物品的隐特征向量）
- $k$：隐特征维度（通常 50~200）

#### 优势

1. **一次性解决问题**：同时得到用户特征向量和物品特征向量
2. **可计算预测评分**：对任意未评分物品可预估评分
3. **可导出相似度**：用户间/物品间的相似度可通过特征向量点积计算
4. **可融入 Qdrant**：将物品特征向量存入 Qdrant 作为新集合，实现极速向量召回

#### 推荐库

```python
# implicit 库（适合大规模稀疏矩阵）
from implicit.als import AlternatingLeastSquares

model = AlternatingLeastSquares(factors=100, iterations=15)
model.fit(user_item_matrix)
```

---

## 4. 线上服务架构

### 4.1 多路召回与排序流程

当用户打开首页"为你推荐"模块时，后端按以下流程处理：

```
┌─────────────────────────────────────────────────────────────────┐
│                    用户请求 (userId = 10)                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Step 1: 多路召回 (Multi-Recall)              │
│                                                                  │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ 第一路: Item-CF │  │ 第二路: User-CF │  │ 第三路: Qdrant  │  │
│  │                 │  │                 │  │  内容向量召回    │  │
│  │ 1. 查用户最近   │  │ 1. 查用户 10 的 │  │ 1. 提取用户最   │  │
│  │    高分3部电影  │  │    离线推荐缓存 │  │    喜欢电影的   │  │
│  │ 2. 查相似电影   │  │ 2. 读取推荐列表 │  │    向量         │  │
│  │    合并去重     │  │                 │  │ 2. Qdrant 相似  │  │
│  └────────┬────────┘  └────────┬────────┘  │    度检索       │  │
│           │                    │           └────────┬────────┘  │
│           └────────────────────┼────────────────────┘           │
│                                ▼                                │
│                   所有召回结果合并 (Union)                        │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│               Step 2: 过滤 (Filter)                             │
│                                                                  │
│  1. 去掉用户已看过的电影                                         │
│  2. 去掉用户点过"不感兴趣"的电影                                │
│  3. 去掉下架/不可用的电影                                       │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│               Step 3: 排序 (Ranking)                            │
│                                                                  │
│  简单方案:                                                      │
│    score = α × CF_Score + β × Content_Score + γ × Popularity   │
│                                                                  │
│  复杂方案: LR / XGBoost / 深度学习排序模型                      │
│                                                                  │
│  取 Top 20 返回                                                 │
└─────────────────────────┬───────────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────────┐
│               Step 4: 组装响应                                  │
│                                                                  │
│  从 movies 表查封面、标题、评分等详情                           │
│  以 JSON 格式返回前端                                           │
└─────────────────────────────────────────────────────────────────┘
```

### 4.2 各召回路线的数据源

| 召回路线 | 数据源 | 延迟 | 特点 |
|---------|--------|------|------|
| Item-Based CF | `movie_similarity_cache` 表 / Redis | < 10ms | 基于用户历史评分物品找相似物品 |
| User-Based CF | `user_recommend_cache` 表 / Redis | < 10ms | 基于相似用户的偏好推荐 |
| Qdrant 内容向量 | Qdrant `movies` 集合 | < 50ms | 基于语义相似度的内容推荐 |
| 热门/新片/趋势 | MySQL `movies` 表 | < 100ms | 冷启动/兜底推荐 |

### 4.3 参数配置

| 参数 | 默认值 | 说明 |
|------|--------|------|
| Item-CF 参考电影数 | 3 | 取用户最近评分最高的 3 部电影 |
| User-CF TopK 邻居数 | 10 | 最相似的 K 个用户 |
| Qdrant 召回数 | 20 | 向量检索返回数量 |
| 混合权重 (α:β:γ) | 0.4 : 0.4 : 0.2 | CF : Content : Popularity |
| 最终返回数 | 20 | 排序后取 Top 20 |

---

## 5. 协同过滤 + Qdrant 多路召回

### 5.1 融合架构

```
                    ┌─────────────────────┐
                    │    API Gateway       │
                    │  /api/recommend      │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
      ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
      │  Item-Based   │ │  User-Based   │ │  Qdrant      │
      │  CF (MySQL)   │ │  CF (MySQL)   │ │  向量检索    │
      └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
             │                │                │
             ▼                ▼                ▼
      ┌──────────────────────────────────────────┐
      │           结果融合层 (Merge & Rank)       │
      │                                          │
      │  1. 合并三路召回结果（去重）              │
      │  2. 按加权公式计算综合得分                │
      │  3. 根据 avg_rating 微调排序              │
      │  4. 返回 Top 20                          │
      └──────────────────────────────────────────┘
```

### 5.2 Qdrant 向量召回的增强作用

| 场景 | 纯 CF 的问题 | 加入 Qdrant 后 |
|------|-------------|---------------|
| **冷启动（新电影）** | 无评分数据，CF 无法推荐 | 通过标题/题材向量相似度可召回 |
| **冷启动（新用户）** | 无历史行为，CF 无法工作 | 可以返回热门+内容推荐兜底 |
| **长尾发现** | 评分少的冷门电影难以被推荐 | 语义相似度可挖掘小众佳作 |
| **实时性** | CF 依赖离线计算，更新周期长 | 新电影导入 Qdrant 后立即可检索 |

### 5.3 Qdrant 进阶用法：存储 ALS 特征向量

当前 Qdrant 存储的是 sentence-transformers 编码的**语义向量**（384 维）。进阶方案是将 ALS 训练出的**物品隐特征向量**也存入 Qdrant：

| Collection | 向量维度 | 模型 | 用途 |
|-----------|---------|------|------|
| `movies` | 384 | all-MiniLM-L6-v2 | 内容语义相似度检索 |
| `movies_als` (新增) | 100~200 | ALS 矩阵分解 | 协同过滤相似度检索 |

这样 Qdrant 就同时承载了**内容相似**和**协同过滤相似**两种召回能力。

---

## 6. 落地路线图

### Phase 1：基础功能补齐（1~2 天）

| 任务 | 描述 | 涉及文件 |
|------|------|---------|
| P1.1 热门推荐 | 基于评分数量和均值的热门电影 | recommendService.js + controller + routes |
| P1.2 新片推荐 | 按 release_year 排序的最新电影 | 同上 |
| P1.3 趋势推荐 | 基于近期评分活跃度的趋势电影 | 同上 |
| P1.4 Qdrant API | 封装 Qdrant 向量检索接口 | recommendService.js（新增 contentBasedCF） |

**产出：** 6 个 API 端点全部可用（3 个兜底 + 1 个 Qdrant + 2 个原有 CF）

### Phase 2：离线计算与缓存（3~5 天）

| 任务 | 描述 | 技术选型 |
|------|------|---------|
| P2.1 创建缓存表 | 建 `movie_similarity_cache` 和 `user_recommend_cache` | MySQL DDL |
| P2.2 Item-Based 离线脚本 | Python 脚本计算电影相似度并入库 | scikit-learn / implicit |
| P2.3 User-Based 离线脚本 | 对活跃用户预计算推荐列表 | implicit ALS |
| P2.4 Redis 缓存集成 | 将计算结果缓存到 Redis | ioredis |

**产出：** 离线计算 pipeline 搭建完成，线上 API 改为读缓存

### Phase 3：多路召回与排序（2~3 天）

| 任务 | 描述 |
|------|------|
| P3.1 召回融合层 | 实现多路召回合并、去重、过滤逻辑 |
| P3.2 排序策略 | 实现加权融合排序（CF + Content + Popularity） |
| P3.3 自适应权重 | 根据用户活跃度动态调整召回权重 |
| P3.4 稀疏数据回退 | 评分不足时自动降级到热门/内容推荐 |

**产出：** 完整的多路召回推荐系统

### Phase 4：性能优化与监控（1~2 天）

| 任务 | 描述 |
|------|------|
| P4.1 请求超时保护 | 添加超时控制和参数校验 |
| P4.2 数据库索引优化 | 确保查询效率 |
| P4.3 日志与监控 | 添加结构化日志和耗时告警 |
| P4.4 异常测试 | 覆盖新用户、超时、Qdrant 不可用等场景 |

---

## 7. 数据库与基础设施

### 7.1 新增缓存表

```sql
-- 电影相似度缓存表（Item-Based CF 离线计算结果）
CREATE TABLE movie_similarity_cache (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  movie_id INT NOT NULL UNIQUE,
  similar_movies JSON NOT NULL,
  top_k INT NOT NULL DEFAULT 20,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_movie_id (movie_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 用户推荐缓存表（User-Based CF 离线计算结果）
CREATE TABLE user_recommend_cache (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_id INT NOT NULL UNIQUE,
  recommend_movies JSON NOT NULL,
  algorithm VARCHAR(20) NOT NULL,
  top_n INT NOT NULL DEFAULT 20,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  INDEX idx_user_id (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 7.2 所需索引

```sql
-- 热门推荐（COUNT + ORDER BY）
ALTER TABLE users_movies_behaviors 
  ADD INDEX idx_movie_behavior_rate (movie_id, behavior_type, rating);

-- 新片推荐（ORDER BY release_year）
ALTER TABLE movies 
  ADD INDEX idx_movies_release_year (release_year DESC);

-- 趋势推荐（WHERE created_at + GROUP BY）
ALTER TABLE users_movies_behaviors 
  ADD INDEX idx_behavior_time (behavior_type, created_at);
```

### 7.3 所需依赖

```bash
# Node.js 后端
npm install @qdrant/js-client-rest ioredis --prefix backend

# Python 离线计算
pip install scikit-learn implicit pandas mysql-connector-python
```

---

## 8. 附录

### A. 相关文件索引

| 文件 | 说明 |
|------|------|
| `backend/src/services/recommendService.js` | 推荐算法核心（需新增离线版 + Qdrant 调用） |
| `backend/src/controllers/recommendController.js` | API 控制器（需新增热门/新片/趋势 handler） |
| `backend/src/routes/recommend.js` | 路由注册（需新增 4 个端点） |
| `backend/src/middleware/cacheMiddleware.js` | 缓存中间件（可集成 Redis） |
| `scripts/run_offline_cf.py` | 待创建：离线 CF 计算脚本 |
| `database/init.sql` | 数据库初始化（需新增缓存表） |

### B. 现有文档

| 文档 | 说明 |
|------|------|
| `docs/backend/recommendation-system-guide.md` | 现有推荐系统指南 |
| `docs/backend/recommendation-algorithm-modification-plan.md` | 推荐算法修改计划（含详细代码示例） |

### C. 修订记录

| 日期 | 版本 | 说明 |
|------|------|------|
| 2026-05-03 | v1.0 | 初始版本 — 增强开发计划 |