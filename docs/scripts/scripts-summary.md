# Scripts 目录脚本汇总

> 更新时间：2026-05-07

## 目录结构

```
scripts/
├── extract_test_subset.py     # [活跃] 从 MySQL 全量导出评分/电影/评论数据到 CSV（供 train_recommend.py 使用）
├── run_import_movies.py       # [活跃] 导入电影数据（MySQL pymysql）
├── run_import_ratings.py      # [活跃] 导入评分数据（MySQL pymysql）
├── run_import_tags.py         # [活跃] 导入标签数据（MySQL pymysql）
├── import_qdrant.py           # [活跃] 电影向量化 → Qdrant（Content-Based）
├── import_qdrant_remaining.py # [活跃] 补录未同步电影至 Qdrant
│
└── recommend/                 # 推荐系统子模块
    ├── train_recommend.py          # [活跃] 训练 SVD / User-CF / Item-CF 模型
    ├── recommend.py                # [活跃] 推荐引擎（CLI 模式）
    ├── recommend_api.py            # [活跃] Flask REST API 推荐服务（端口 5100）
    ├── export_recommendations.py   # [活跃] 推荐结果导出为 CSV
    ├── import_recommendations.py   # [活跃] 推荐结果导入 MySQL
    ├── save_to_cache.py            # [活跃] 推荐结果写入 MySQL 缓存表
    ├── test_recommend.py           # [活跃] 推荐系统评估测试
    ├── test_recommend_pipeline.ps1 # [活跃] 端到端管道测试
    └── test_pipeline_simple.ps1    # [活跃] 简化版端到端测试
```

---

## 一、数据导入脚本

### 1. `run_import_movies.py`
- **用途**：从 MovieLens `movies.csv` 导入电影数据到 MySQL
- **写入表**：`movies`、`genres`、`movies_genres`
- **数据库驱动**：PyMySQL（与后端一致）
- **数据路径**：`movie data/ml-32m/movies.csv`
- **用法**：`python scripts/run_import_movies.py`

### 2. `run_import_ratings.py`
- **用途**：从 MovieLens `ratings.csv` 导入评分数据到 MySQL
- **写入表**：`users`、`users_movies_behaviors`、`movies.avg_rating`
- **特性**：分块读取（chunk_size=100000）、生成虚拟用户
- **数据库驱动**：PyMySQL
- **用法**：`python scripts/run_import_ratings.py`

### 3. `run_import_tags.py`
- **用途**：从 MovieLens `tags.csv` 导入标签数据到 MySQL
- **写入表**：`tags`、`movies_tags`、`users_preferred_tags`
- **特性**：分块读取（chunk_size=50000）
- **数据库驱动**：PyMySQL
- **用法**：`python scripts/run_import_tags.py`

---

## 二、向量化导入脚本（Qdrant）

### 4. `import_qdrant.py`
- **用途**：将 MySQL 电影数据向量化后导入 Qdrant（Content-Based 检索）
- **向量模型**：`sentence-transformers`（`paraphrase-MiniLM-L6-v2` 或 `all-MiniLM-L6-v2`）
- **目标集合**：Qdrant `movies` 集合
- **特性**：在 MySQL 记录 `vector_synced_at` 时间戳、支持断点续传
- **用法**：`python scripts/import_qdrant.py`

### 5. `import_qdrant_remaining.py`
- **用途**：补录首次未同步到 Qdrant 的剩余电影
- **判断依据**：MySQL `vector_synced_at IS NULL`
- **用法**：`python scripts/import_qdrant_remaining.py`

---

## 三、推荐系统训练脚本

### 6. `train_recommend.py`
- **用途**：训练三种推荐算法模型
- **算法**：
  - **SVD (矩阵分解)**：基于奇异值分解的协同过滤
  - **User-CF (基于用户的协同过滤)**：寻找相似用户
  - **Item-CF (基于物品的协同过滤)**：寻找相似物品
- **数据来源**：`scripts/extract_test_subset_test/`
- **模型输出**：`scripts/models/`（`svd_model.pkl`、`user_cf_model.pkl`、`item_cf_model.pkl`）
- **用法**：`python scripts/recommend/train_recommend.py`

### 7. `recommend.py`
- **用途**：推荐引擎（CLI 模式），为指定用户生成 Top-N 推荐
- **算法支持**：SVD / User-CF / Item-CF / Hybrid
- **用法**：
  ```
  python recommend.py <user_id> [--algorithm svd|user_cf|item_cf|hybrid] [--top_n 10]
  python recommend.py --interactive
  ```

### 8. `recommend_api.py`
- **用途**：Flask REST API 服务，为 Node.js 后端提供推荐接口
- **端口**：5100
- **缓存优先策略**：先查 MySQL 缓存 → 无缓存则实时计算 → 异步写入缓存
- **API 端点**：`/api/recommend/ai?userId=1&algorithm=hybrid&topN=10`
- **用法**：`python scripts/recommend/recommend_api.py [--port 5100]`

---

## 四、推荐结果导出/导入/缓存

### 9. `export_recommendations.py`
- **用途**：将离线训练的推荐结果导出为 CSV 文件
- **导出类型**：`--type user`（用户推荐）/ `--type movie`（电影相似度）
- **输出格式**：CSV，配合 `LOAD DATA INFILE` 批量导入 MySQL
- **用法**：`python export_recommendations.py [--type user|movie] [--top_n 30]`

### 10. `import_recommendations.py`
- **用途**：将 `export_recommendations.py` 导出的 CSV 导入 MySQL
- **导入方式**：`LOAD DATA INFILE`（高性能）或逐行 INSERT（备选）
- **用法**：`python import_recommendations.py [--user recs.csv] [--movie sims.csv]`

### 11. `save_to_cache.py`
- **用途**：将推荐结果写入 MySQL 缓存表（`users_recommendations` / `movies_similarities`）
- **可作为模块被 `recommend_api.py` 调用**
- **用法**：`python save_to_cache.py --user-id 1 --algorithm hybrid --input recs.json`

---

## 五、测试与评估

### 12. `test_recommend.py`
- **用途**：推荐系统全面评估测试
- **评估指标**：RMSE、MAE、Precision@K、Recall@K、Coverage、Diversity
- **可视化**：生成评估图表
- **用法**：
  ```
  python test_recommend.py           # 全面评估
  python test_recommend.py --quick   # 快速评估
  python test_recommend.py --demo    # 仅展示推荐样例
  ```

### 13. `test_recommend_pipeline.ps1`（PowerShell）
- **用途**：推荐系统端到端 E2E 测试脚本
- **测试链路**：Python AI 推荐服务 (5100) → Node.js 后端代理 (3000)
- **测试模型**：SVD / User-CF / Item-CF / Hybrid
- **用法**：`.\test_recommend_pipeline.ps1`

### 14. `test_pipeline_simple.ps1`（PowerShell）
- **用途**：简化的 E2E 测试（步骤更少，快速验证）
- **用法**：`.\test_pipeline_simple.ps1`

---

## 六、已删除的过时脚本

以下脚本已被删除，理由如下：

| 脚本 | 删除原因 |
|------|----------|
| `import_movies.py` | 被 `run_import_movies.py` 替代（用 `mysql.connector` 而非 `pymysql`） |
| `import_ratings.py` | 被 `run_import_ratings.py` 替代（`mysql.connector`） |
| `import_tags.py` | 被 `run_import_tags.py` 替代（`mysql.connector`） |
| `add_request_id_column.py` | 一次性 DDL 迁移脚本，已执行完毕 |
| `check_db_status.py` | 调试诊断脚本，使用旧的 `mysql.connector`，无维护价值 |
| `check_schema.py` | 仅查看单表结构的极小诊断脚本，使用频率低 |
| `recommend/extract_test_subset.py` | 使用 `mysql.connector`，功能已整合到 `extract_test_subset.py` |

<task_progress>
- [x] 读取并分析所有 scripts 目录下的脚本
- [x] 识别过时/废弃的脚本
- [x] 生成 scripts-summary.md 总结文档
- [ ] 删除过时脚本
</task_progress>