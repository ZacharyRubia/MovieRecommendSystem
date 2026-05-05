# 离线推荐算法实现与缓存集成

## 背景

为解决在线实时推荐在冷启动、大用户量下的性能压力，引入了**离线推荐算法 + 数据库缓存**架构。离线计算脚本定期运行，将计算结果写入 MySQL 缓存表，后端推荐接口优先读取缓存，有则直接返回，无则降级到实时计算。

## 新增文件

### `scripts/offline/compute_item_cf.py`

**Item-Based Collaborative Filtering 离线计算脚本**

- **算法**：余弦相似度（Cosine Similarity），对每部电影计算与其最相似的 Top-K 电影
- **计算方式**：利用 `sklearn.metrics.pairwise.cosine_similarity` 批量计算电影-电影相似度矩阵
- **输入**：从 `users_movies_behaviors` 表读取用户评分数据（支持 `--limit` 控制数据量）
- **输出**：写入 `movie_similarity_caches` 表（movie_id, similar_movies JSON, updated_at）
- **参数**：
  - `--limit`：加载评分数（默认 500）
  - `--topk`：每部电影保留的最相似电影数（默认 5）
  - `--batch`：批量处理尺寸（默认 100）
  - `--min-ratings`：电影最少被评分次数（默认 2）
- **依赖**：`pip install numpy scipy scikit-learn mysql-connector-python`

### `scripts/offline/compute_als.py`

**ALS Matrix Factorization 离线计算脚本**

- **算法**：交替最小二乘法（Alternating Least Squares），基于隐式反馈模型
- **库**：`implicit` 库的 `AlternatingLeastSquares`
- **输入**：从 `users_movies_behaviors` 表读取评分数据
- **输出**：写入 `user_recommend_caches` 表（user_id, recommend_movies JSON, algorithm='als', updated_at）
- **参数**：
  - `--limit`：加载评分数（默认 500）
  - `--factors`：隐因子数（默认 10）
  - `--iter`：迭代次数（默认 15）
  - `--reg`：正则化系数（默认 0.1）
  - `--topk`：每用户推荐的电影数（默认 5）
- **依赖**：`pip install numpy scipy implicit mysql-connector-python`

## 修改文件

### `backend/src/services/recommendService.js`

在原有在线实时推荐逻辑中新增以下函数：

| 函数 | 作用 |
|------|------|
| `getCachedSimilarMovies(movieId)` | 读取单部电影的离线相似缓存 |
| `getItemBasedFromCache(targetRatingMap, topN)` | 批量读取用户已评分电影的相似缓存，按评分加权聚合得到 Item-Based 推荐结果 |
| `getCachedUserRecommend(userId)` | 读取用户级别的离线推荐缓存（由 ALS 产出） |

**影响流程**：

1. **User-Based CF** (`userBasedCF`)：查找离线 `user_recommend_caches`，有且 algorithm='als' 时直接返回，否则进入实时 KNN 计算
2. **Item-Based CF** (`itemBasedCF`)：对用户已评分的每部电影查找 `movie_similarity_caches`，聚合所有相似电影按评分加权排序，有缓存时直接返回，否则进入实时 SQL 计算

缓存过期时间：1小时（`CACHE_TTL_MS = 60 * 60 * 1000`）

## 数据库表结构

### `movie_similarity_caches`

| 列 | 类型 | 说明 |
|----|------|------|
| movie_id | bigint unsigned PK | 电影 ID，外键 → movies(id) |
| similar_movies | JSON | 相似电影列表 `[{movie_id, similarity}]` |
| updated_at | timestamp | 更新时间 |

### `user_recommend_caches`

| 列 | 类型 | 说明 |
|----|------|------|
| user_id | bigint unsigned PK | 用户 ID，外键 → users(id) |
| recommend_movies | JSON | 推荐电影列表 `[{movie_id, score}]` |
| algorithm | varchar(20) | 算法标识 ('als' 等) |
| updated_at | timestamp | 更新时间 |

## 运行测试

### 测试 Item-CF（200 条评分）

```bash
python scripts/offline/compute_item_cf.py --limit 200 --topk 3 --min-ratings 1
```

结果：196 部电影成功写入 `movie_similarity_caches`，耗时 25.5s。

### 测试 ALS（200 条评分）

```bash
python scripts/offline/compute_als.py --limit 200 --factors 5 --topk 3 --iter 10
```

结果：3 个用户成功写入 `user_recommend_caches`，耗时 0.9s。

### 后端测试

```bash
cd backend && npm start
```

后端正常启动。后续通过 API 调用 `/api/recommend/user-cf` 或 `/api/recommend/item-cf` 时会自动读取离线缓存。

## 后续优化建议

1. **调度自动化**：使用 cron 或 APScheduler 定期调度两个脚本
2. **全量计算**：去掉 `--limit` 限制，使用全部评分数据计算
3. **模型评估**：随机分割训练/测试集，计算 RMSE 评估 ALS 效果
4. **混合权重融合**：离线 ALS 结果与在线 User-CF 结果加权合并
5. **Redis 缓存预热**：离线计算完成后将结果刷入 Redis 加快访问