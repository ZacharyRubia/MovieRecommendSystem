# extract_test_subset.py — 数据库数据导出工具

## 概述

从 MySQL 数据库 `MovieRecommendSystem` 中按用户-评分-电影链路导出数据子集，供推荐系统训练（`train_recommend.py`）使用。

### 导出链路

```
用户（users）
  └─ 评分行为（users_movies_behaviors WHERE behavior_type='rate'）
       └─ 电影（movies）
            └─ 评论（comments）
```

### 输出文件（到 `scripts/extract_test_subset_test/`）

| 文件 | 内容 | 用途 |
|------|------|------|
| `test_users.csv` | 用户信息（id, username, email, created_at） | — |
| `test_movies.csv` | 电影信息（id, title, description, release_year, duration, avg_rating） | 关联电影元数据 |
| `test_ratings.csv` | 评分记录（user_id, movie_id, rating, created_at） | **推荐模型训练核心数据** |
| `test_comments.csv` | 评论记录（comment_id, user_id, movie_id, content, created_at） | NLP 语义分析 |

---

## 使用方式

### 1. 全量导出

```bash
cd scripts
python extract_test_subset.py
# 等效于:
python extract_test_subset.py --users 0 --movies 0
```

  - 导出 **所有** 有评分行为的用户
  - 导出 **这些用户评过的所有** 电影
  - 导出 **所有** 评分记录和评论记录

> 💡 **适合场景**：首次搭建、全量训练前导出完整数据集
> ⚠️ 全量用户可达 20 万+，评分可达千万级，导出需数分钟

---

### 2. 部分导出（按用户数 + 按电影数）

```bash
# 示例：导出 1000 个最活跃用户 + 500 部最热门电影
python extract_test_subset.py --users 1000 --movies 500
```

参数说明：

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `--users` | int | 0 | 目标用户数（0 = 全量；>0 时选取前 N 个最活跃用户） |
| `--movies` | int | 0 | 目标电影数（0 = 全量；>0 时选取前 N 个最热门电影） |
| `--seed` | int | 42 | 随机种子（当前版本未使用随机采样，保留参数） |

选取策略：

```
--users 1000:
  → 按评分次数降序排列用户 → 取前 1000 个

--movies 500:
  → 从上述用户评过的电影中，按评分频次降序 → 取前 500 部
```

> **注意**：`--movies` 约束在 `--users` 之后执行，即 **先选用户 → 再选这些用户评过的电影中的热门部分**

---

### 3. 混合导出

```bash
# 全量用户 + 仅 5000 部热门电影（缩小测试集推荐候选范围）
python extract_test_subset.py --users 0 --movies 5000

# 仅 5000 个用户 + 全量涉及电影
python extract_test_subset.py --users 5000 --movies 0
```

---

## 常见使用场景

### 🅰 开发调试（最小测试集）

```bash
python extract_test_subset.py --users 100 --movies 50
```

- 最快导出（几秒完成）
- 适合验证 train_recommend.py 的 pipeline 是否跑通
- 评分数据少，训练秒级完成

### 🅱 资源受限的服务器（中等测试集）

```bash
python extract_test_subset.py --users 10000 --movies 3000
```

- 64GB 内存机器可跑
- 峰值内存约 10-15GB
- 训练可观察到一定推荐质量

### 🅲 完整生产训练（全量导出）

```bash
python extract_test_subset.py
```

- 推荐在 128GB+ 机器上使用
- 配合 `train/train_svd.py` + `train/train_usercf.py` + `train/train_itemcf.py` 优化版模块运行
- 峰值内存约 6-8GB

---

## 与 train_recommend.py 配合

`extract_test_subset.py` 的输出目录 `scripts/extract_test_subset_test/` 与 `train_recommend.py` 的 `DATA_DIR` 配置一致。导出后可直接训练：

```bash
# 步骤一：导出数据
cd scripts
python extract_test_subset.py --users 10000 --movies 3000

# 步骤二：训练模型
cd recommend
python train_recommend.py --skip-eval

# 或分步训练（每步完成后释放内存）
python train/train_svd.py
python train/train_usercf.py
python train/train_itemcf.py
```

---

## 环境要求

- Python ≥ 3.8
- `pip install mysql-connector-python pandas numpy`
- 可访问 `192.168.1.38:3306` MySQL 实例