# train_recommend.py 功能总结

## 概述

`train_recommend.py` 是 MovieLens 推荐系统的**推荐算法训练脚本（CPU 极致优化版 v4）**，用于训练多个推荐算法模型并导出推荐缓存数据。

---

## 核心功能

### 1. 数据加载与预处理
- **数据来源**：从 MySQL 数据库导出的评分/电影测试子集 CSV 文件
  - `scripts/extract_test_subset_test/test_ratings.csv` — 评分数据（`user_id`, `movie_id`, `rating` 等）
  - `scripts/extract_test_subset_test/test_movies.csv` — 电影信息
  - > 这些 CSV 由已废弃的 `extract_test_subset.py`（使用 `mysql.connector`）从数据库导出，数据源为通过 `run_import_movies.py` / `run_import_ratings.py` 等脚本导入 MySQL 的 MovieLens 数据集
- 读取评分数据和电影信息
- 构建 `user_id` → 索引、`movie_id` → 索引的双向映射
- 按用户分层划分训练/测试集（默认 80%/20%）

### 2. 三种推荐算法训练

| 算法 | 方法 | 说明 |
|------|------|------|
| **SVD** | sklearn TruncatedSVD (randomized) | 矩阵分解，50 个隐因子，多线程加速 |
| **User-CF** | SVD 投影 + 余弦相似度 | 用 SVD 隐向量做近邻计算，避免 O(n²) 全量 pairwise |
| **Item-CF** | Adjusted Cosine Similarity | 全向量化计算电影相似度矩阵 |

### 3. 模型保存
- 序列化为 `.pkl` 文件，保存至 `scripts/models/`
- 自动生成 `metadata.json`（含训练时间、数据集统计、各模型 RMSE 等）

### 4. 缓存导出（多进程并行）
- **CSV 导出**：`users_recommendations.csv` + `movies_similarities.csv`
- **JSON 导出**：`users_recommendations.json` + `movies_similarities.json`（可导入 Qdrant）
- **SQL 导出**：从 CSV 生成 `REPLACE INTO` 批量 SQL 脚本
- 使用 `ProcessPoolExecutor` 突破 GIL，充分利用多核 CPU

---

## 关键优化（v4 版本亮点）

1. **动态 CPU 核心检测**：自动设置 `OMP_NUM_THREADS`、`MKL_NUM_THREADS` 等环境变量
2. **sklearn TruncatedSVD**：替代 scipy svds，自动多线程
3. **Numba JIT 编译**：加速 `_apply_top_k` 和导出热循环（可选依赖）
4. **ProcessPoolExecutor**：替代 ThreadPoolExecutor，突破 GIL 限制
5. `--skip-eval` 参数：跳过 RMSE 评估，节省约 95% 时间
6. 导出 `batch_size` 自适应增大
7. numpy 矩阵运算 + 批量 JSON 序列化

---

## 命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--skip-eval` | flag | False | 跳过 RMSE 评估，仅训练算法模型 |
| `--n-jobs` | int | CPU 核心数 | 并行工作线程数 |
| `--export-only` | flag | False | 仅从已有模型导出缓存，不重新训练 |
| `--top-n` | int | 20 | 每个用户/电影保留的推荐数量 |

---

## 输出文件

### 模型文件（`scripts/models/`）
- `svd_model.pkl` — SVD 模型
- `user_cf_model.pkl` — User-CF 模型
- `item_cf_model.pkl` — Item-CF 模型
- `metadata.json` — 训练元数据

### 缓存导出（`scripts/export/`）
- `users_recommendations.csv` — 用户推荐 CSV
- `users_recommendations.sql` — 用户推荐 SQL 导入脚本
- `users_recommendations.json` — 用户推荐 JSON
- `movies_similarities.csv` — 电影相似度 CSV
- `movies_similarities.sql` — 电影相似度 SQL 导入脚本
- `movies_similarities.json` — 电影相似度 JSON

---

## 数据流

```
test_ratings.csv ─┬─→ SVD ──→ svd_model.pkl ──→ 用户推荐导出 → CSV / SQL / JSON
                  │
                  ├─→ User-CF ──→ user_cf_model.pkl
                  │
test_movies.csv ──┴─→ Item-CF ──→ item_cf_model.pkl ──→ 电影相似度导出 → CSV / SQL / JSON
```

---

## 环境依赖

- Python 3.12+
- numpy, pandas, scipy, scikit-learn
- Numba（可选，用于 JIT 加速）