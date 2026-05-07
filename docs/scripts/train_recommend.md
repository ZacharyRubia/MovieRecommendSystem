# train_recommend.py - 离线推荐训练脚本

## 概述

`scripts/recommend/train_recommend.py` 是 MovieLens 推荐系统的离线训练入口，实现三种推荐算法：

| 算法 | 类型 | 原理 |
|------|------|------|
| **SVD** | 矩阵分解 | `R ≈ U·Σ·V^T`，通过奇异值分解得到用户隐因子和电影隐因子，预测用户对未评分电影的评分 |
| **User-Based CF** | 协同过滤 | 找到与目标用户评分习惯最相似的 K 个邻居，用邻居的评分加权平均预测 |
| **Item-Based CF** | 协同过滤 | 对用户已评分电影，找到最相似的 K 部电影，加权平均预测评分 |

## 训练流程

```
graph TD
    A[加载 test_ratings.csv / test_movies.csv] --> B[按用户划分 80%/20% 训练/测试集]
    B --> C1[SVD 矩阵分解]
    B --> C2[User-Based CF]
    B --> C3[Item-Based CF]
    C1 --> D[保存 .pkl 模型文件]
    C2 --> D
    C3 --> D
    D --> E[导出 MySQL 缓存数据]
    E --> F1[users_recommendations.csv]
    E --> F2[movies_similarities.csv]
    E --> F3[.sql 脚本]
    E --> F4[.json 文件]
```

## 输出目录

```
scripts/
├── models/               # .pkl 模型文件
│   ├── svd_model.pkl
│   ├── user_cf_model.pkl
│   └── item_cf_model.pkl
├── export/               # 数据库可导入的缓存数据
│   ├── users_recommendations.csv   →  MySQL users_recommendations 表
│   ├── users_recommendations.sql   →  SQL 脚本
│   ├── users_recommendations.json  →  供 save_to_cache.py 导入
│   ├── movies_similarities.csv     →  MySQL movies_similarities 表
│   ├── movies_similarities.sql
│   └── movies_similarities.json
└── recommend/
    ├── train_recommend.py          # 训练脚本（主入口）
    ├── export_recommendations.py   # 模型加载/重导出
    ├── import_recommendations.py   # 旧版 SQL 导入
    ├── save_to_cache.py            # 在线缓存写入工具
    └── recommend_api.py            # 推荐 API（在线调用）
```

## 用法

```bash
# 1. 完整训练 + 导出
cd MovieRecommendSystem
python scripts/recommend/train_recommend.py

# 2. 仅从已有模型导出缓存（不重新训练）
python scripts/recommend/train_recommend.py --export-only

# 3. 训练 + 导出 + 自动导入 MySQL
python scripts/recommend/train_recommend.py --import-db

# 4. 单独导入缓存到 MySQL
python scripts/import_to_mysql.py
python scripts/import_to_mysql.py --dry-run    # 预览
python scripts/import_to_mysql.py --truncate   # 先清空再导入
```

---

## 附录：MySQL 缓存表数据结构

### 表 `users_recommendations`（原 `user_recommend_caches`）

**作用**：存储每个用户的 Top-N 电影推荐列表，供前端实时查询（缓存，避免每次重新计算）

**表结构**（来自 `database/init.sql`）：

```sql
CREATE TABLE `users_recommendations` (
  `user_id` bigint NOT NULL,
  `recommend_movies` json DEFAULT NULL,
  `algorithm` varchar(50) DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**存储的数据示例**：

| user_id | recommend_movies (JSON) | algorithm | updated_at |
|---------|------------------------|-----------|------------|
| 1 | `[{"score": 0.9967, "movie_id": 11}, {"score": 0.0168, "movie_id": 10}, {"score": -0.0248, "movie_id": 2}]` | als | 2026-05-03 |
| 2 | `[{"score": 0.9967, "movie_id": 11}, {"score": 0.0168, "movie_id": 10}, {"score": -0.0248, "movie_id": 2}]` | als | 2026-05-03 |
| 3 | `[{"score": 0.9967, "movie_id": 11}, {"score": 0.0168, "movie_id": 10}, {"score": -0.0248, "movie_id": 2}]` | als | 2026-05-03 |

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `user_id` | BIGINT (PK) | 用户唯一标识 |
| `recommend_movies` | JSON | **推荐电影列表**，数组结构：`[{"movie_id": int, "score": float}]`，按 `score` 降序排列 |
| `algorithm` | VARCHAR(50) | 生成该推荐的算法标识（如 `svd`, `user_cf`, `item_cf`, `hybrid`, `als`） |
| `updated_at` | TIMESTAMP | 最后更新时间 |

**JSON 内部结构**：
```json
[
  {"movie_id": 11, "score": 0.9967},    // 最推荐，预测评分 0.9967
  {"movie_id": 10, "score": 0.0168},    // 次推荐
  {"movie_id": 2,  "score": -0.0248}    // 低相关（负分表示不推荐）
]
```

**数据来源**：
- **离线**：`train_recommend.py` 中的 `export_users_recommendations_csv()`，使用 SVD 模型的隐因子向量计算所有用户对所有电影的预测评分，取 Top-N
- **在线**：`save_to_cache.py` 中的 `save_user_recommendation()`，由推荐 API 实时计算后写回

---

### 表 `movies_similarities`（原 `movie_similarity_caches`）

**作用**：存储每部电影最相似的 Top-N 电影列表，用于"相似电影推荐"、"猜你喜欢"等功能

**表结构**（来自 `database/init.sql`）：

```sql
CREATE TABLE `movies_similarities` (
  `movie_id` bigint NOT NULL,
  `similar_movies` json DEFAULT NULL,
  `updated_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`movie_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**存储的数据示例**：

| movie_id | similar_movies (JSON) | updated_at |
|----------|----------------------|------------|
| 2 | `[{"movie_id": 10, "similarity": 1.0}, {"movie_id": 2, "similarity": 1.0}, {"movie_id": 62, "similarity": 1.0}]` | 2026-05-03 |
| 10 | `[{"movie_id": 10, "similarity": 1.0}, {"movie_id": 2, "similarity": 1.0}, {"movie_id": 62, "similarity": 1.0}]` | 2026-05-03 |
| 11 | `[{"movie_id": 10, "similarity": 1.0}, {"movie_id": 2, "similarity": 1.0}, {"movie_id": 62, "similarity": 1.0}]` | 2026-05-03 |
| 17 | `[{"movie_id": 10, "similarity": 0.7809}, {"movie_id": 2, "similarity": 0.7809}, {"movie_id": 62, "similarity": 0.7809}]` | 2026-05-03 |
| 25 | `[{"movie_id": 3078, "similarity": 1.0}, {"movie_id": 3030, "similarity": 1.0}, {"movie_id": 2997, "similarity": 1.0}]` | 2026-05-03 |

**字段说明**：

| 字段 | 类型 | 说明 |
|------|------|------|
| `movie_id` | BIGINT (PK) | 电影唯一标识 |
| `similar_movies` | JSON | **相似电影列表**，数组结构：`[{"movie_id": int, "similarity": float}]`，按 `similarity` 降序排列 |
| `updated_at` | TIMESTAMP | 最后更新时间 |

**JSON 内部结构**：
```json
[
  {"movie_id": 10, "similarity": 1.0},    // 完全相似（可能是同一部电影或 100% 相关）
  {"movie_id": 2,  "similarity": 1.0},
  {"movie_id": 62, "similarity": 1.0}
]
```

> **注意**：示例数据中多个电影的 `similarity` 为 1.0（100% 相似），这表明该数据可能是**占位数据**或**测试数据**。Item-CF 算法在实际运行时，两部电影只有被完全相同的用户群评分才会得到相似度 1.0，真实场景中极少出现。正式训练后的数据中，相似度应在 0~1 之间且各不相同。

**数据来源**：
- **离线**：`train_recommend.py` 中的 `export_movies_similarities_csv()`，从 Item-CF 模型的 `movie_sim_matrix` 提取每部电影相似度 Top-N
- **在线**：`save_to_cache.py` 中的 `save_movie_similarity()`，由推荐 API 实时计算后写回

---

### 两张表的关系图

```
users_recommendations                      movies_similarities
┌─────────────────────┐                    ┌─────────────────────┐
│ user_id  (PK)       │                    │ movie_id    (PK)    │
│ recommend_movies     │  ────→ 前端       │ similar_movies      │  ────→ 前端
│   [                  │    用户推荐        │   [                 │    相似电影
│    {movie_id, score} │                    │    {movie_id, score}│
│   ]                  │                    │   ]                 │
│ algorithm            │                    │ updated_at          │
│ updated_at           │                    └─────────────────────┘
└─────────────────────┘

  ↑ 离线训练写入                          ↑ 离线训练写入
  ↑ 在线 API 更新                         ↑ 在线 API 更新
```

### 注意事项

1. **JSON 字段名差异**：`save_to_cache.py` 中电影相似度的 JSON key 使用 `score`（兼容 `similarity`），而 `train_recommend.py` 导出的 CSV 中用户推荐使用 `score`、电影相似度使用 `score` 或 `similarity`。后端读取时需兼容两种命名。

2. **占位数据**：上面示例中的 all-1.0 相似度数据是占位/测试数据，表明数据库表已创建但尚未用正式模型结果更新。

3. **算法标签**：示例中 `algorithm='als'`（交替最小二乘法）与当前代码中 SVD/UserCF/ItemCF 不同，说明早期系统使用过 ALS 算法。