# 推荐系统训练模块内存优化改造总结

> 日期：2026-05-09

## 背景

原始 `train_recommend.py` 在全量数据（20 万用户 × 8 万电影，约 2000 万评分）训练时，在 64 核 128 GB 机器上发生 OOM（内存溢出）。

---

## 根因分析

原始代码中 **6 处密集矩阵操作**导致峰值内存超过 400 GB：

| # | 代码位置 | 矩阵规模 | 内存消耗 | 原因 |
|---|---------|---------|---------|------|
| 1 | item-cf: `np.full((n_movies, n_users), np.nan)` | 83K×200K | **62 GB** | float32 密集矩阵 |
| 2 | item-cf: `np.nan_to_num(movie_user - movie_means[:, None])` | 83K×200K | **62 GB** | 副本 |
| 3 | item-cf: `normalized @ normalized.T` | 83K×83K | **55 GB** | float64 相似度矩阵 |
| 4 | user-cf: `np.full((n_users, n_movies), global_mean)` | 200K×83K | **62 GB** | float32 密集矩阵 |
| 5 | user-cf: `u_norm @ u_norm.T` | **200K×200K** | **320 GB** | **峰值之王** |
| 6 | item-cf: `has_rating @ has_rating.T` | 83K×83K | **55 GB** | co-occurrence |

**结论**：64 核 128 GB 必然 OOM。

---

## 改造方案

将训练拆分为三个独立模块，各自采用不同的内存优化策略：

### 1. SVD 矩阵分解 (`train/train_svd.py`)

| 项目 | 优化前 | 优化后 |
|------|--------|--------|
| 数据存储 | 密集 np.ndarray (62 GB) | CSR 稀疏矩阵 (~200 MB) |
| 算法 | 手动 SVD 迭代 | sklearn TruncatedSVD (randomized) |
| 峰值内存 | > 120 GB | **4-6 GB** |

### 2. User-CF 协同过滤 (`train/train_usercf.py`)

| 项目 | 优化前 | 优化后 |
|------|--------|--------|
| 相似度矩阵 | 200K×200K 密集 (320 GB) | KDTree 索引 + SVD 50 维降维 |
| 数据存储 | 密集 np.ndarray | CSR 稀疏矩阵 |
| 峰值内存 | > 320 GB | **6-8 GB** |

### 3. Item-CF 协同过滤 (`train/train_itemcf.py`)

| 项目 | 优化前 | 优化后 |
|------|--------|--------|
| 数据存储 | 密集 np.ndarray (62 GB) | CSR 稀疏矩阵 |
| 相似度计算 | 全量 83K×83K | **分块计算** (chunk_size=2000) |
| top-K 策略 | 无 | 只保留 top-K 相似电影 |
| 峰值内存 | > 120 GB | **2-3 GB** |

---

## 新增文件

| 文件 | 用途 |
|------|------|
| `scripts/recommend/train/requirements.txt` | Python 依赖清单，`pip install -r requirements.txt` 一键安装 |
| `scripts/recommend/train/setup_dependencies.py` | 依赖检查 + 安装脚本（支持 `--check-only`、`--upgrade`、`--skip-matplotlib`） |
| `docs/scripts/extract_test_subset_guide.md` | `extract_test_subset.py` 数据库数据导出工具使用指南（全量/部分导出） |
| `docs/scripts/train_optimization_summary.md` | 本文件，本次改造总结 |

---

## 使用方式

```bash
# 1. 安装依赖
pip install -r scripts/recommend/train/requirements.txt

# 2. 导出数据
python scripts/extract_test_subset.py --users 10000 --movies 3000

# 3. 训练（三种方式任选）
#    方式 A：一键训练
python scripts/recommend/train_recommend.py --skip-eval

#    方式 B：分步训练（每步完成后释放内存）
python scripts/recommend/train/train_svd.py
python scripts/recommend/train/train_usercf.py
python scripts/recommend/train/train_itemcf.py