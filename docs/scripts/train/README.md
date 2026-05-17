# 训练脚本文档

## 概览

本目录包含 8 个算法训练脚本（对应论文 2.2.1-2.2.8 节）和 1 个日志工具模块：

| 脚本 | 对应章节 | 算法 | 并行方式 |
|------|---------|------|---------|
| `train_usercf_traditional.py` | 2.2.1 | 传统 User-CF | 多线程 |
| `train_usercf_improved.py` | 2.2.2 | 改进 User-CF（加权+稳定性因子） | 多线程 |
| `train_itemcf_traditional.py` | 2.2.3 | 传统 Item-CF | **单线程** |
| `train_itemcf_improved.py` | 2.2.4 | 改进 Item-CF（去均值余弦+加权平均） | **单线程** |
| `train_slopeone_traditional.py` | 2.2.5 | 传统 Slope One | 多线程 |
| `train_slopeone_improved.py` | 2.2.6 | 改进 Slope One（邻域筛选） | 多线程 |
| `train_turbocf.py` | 2.2.7 | Turbo-CF（K-Means 用户聚类加速） | 多线程 |
| `train_svd.py` | 2.2.8 | SVD 矩阵分解 | 多线程 |
| `train_logger.py` | - | 日志工具模块 | - |

## 公共功能

### --verbose 详细日志

每个算法脚本均支持 `--verbose` 参数，用于将详细的步骤日志输出到 `logs/verbose/` 目录：

```bash
python scripts/train/train_usercf_traditional.py --verbose
python scripts/train/train_usercf_improved.py --verbose
python scripts/train/train_itemcf_traditional.py --verbose
python scripts/train/train_itemcf_improved.py --verbose
python scripts/train/train_slopeone_traditional.py --verbose
python scripts/train/train_slopeone_improved.py --verbose
python scripts/train/train_turbocf.py --verbose
python scripts/train/train_svd.py --verbose
```

日志文件路径：`logs/verbose/{script_name}_{timestamp}.log`

包含的步骤日志：
- 数据加载开始/完成（记录加载的评分记录数）
- 训练开始/完成（记录关键参数和结果）
- 模型保存开始/完成
- 全部完成（记录总耗时）

### --n-jobs CPU 核心控制

除 Item-CF 外，所有脚本支持 `--n-jobs` 参数控制并行度：

```bash
python scripts/train/train_usercf_traditional.py --n-jobs 4
```

Item-CF 为单线程实现，不使用并行。

## 各脚本详细说明

### 1. 传统 User-CF (`train_usercf_traditional.py`)

对应公式：
- 用户相似度：$w_{uv} = \frac{|N(u) \cap N(v)|}{\sqrt{|N(u)| \cdot |N(v)|}}$
- 评分预测：$P(u,i) = \sum_{v \in S(u,K) \cap N(i)} w_{uv} \cdot r_{vi}$

```bash
python scripts/train/train_usercf_traditional.py --n-neighbors 30
```

### 2. 改进 User-CF (`train_usercf_improved.py`)

对应公式：
- 加权预测：$\hat{r}_{ui} = \mu_u + \frac{\sum_{v \in N_i} \text{sim}(u,v) \cdot (r_{vi} - \mu_v)}{\sum_{v \in N_i} |\text{sim}(u,v)|}$
- 稳定性因子：$w'_{uv} = \frac{\text{sim}(u,v)}{1 + \alpha \cdot \sigma_v}$

```bash
python scripts/train/train_usercf_improved.py --n-neighbors 30 --alpha 0.5
```

### 3. 传统 Item-CF (`train_itemcf_traditional.py`) [单线程]

对应公式：
- 物品相似度：$w_{ij} = \frac{|N(i) \cap N(j)|}{\sqrt{|N(i)| \cdot |N(j)|}}$
- 评分预测：$P(u,j) = \sum_{i \in N(u) \cap S(j,K)} w_{ji} \cdot r_{ui}$

```bash
python scripts/train/train_itemcf_traditional.py --n-neighbors 30
```

### 4. 改进 Item-CF (`train_itemcf_improved.py`) [单线程]

对应公式：
- 去均值余弦相似度
- 加权预测：$\hat{r}_{ui} = \frac{\sum_{j \in N_i} \text{sim}(i,j) \cdot r_{uj}}{\sum_{j \in N_i} |\text{sim}(i,j)|}$
- 最小共同评分用户数阈值（默认 3）

```bash
python scripts/train/train_itemcf_improved.py --n-neighbors 30 --min-overlap 3
```

### 5. 传统 Slope One (`train_slopeone_traditional.py`)

对应公式：
- 全局偏差：$\text{dev}_{ij} = \frac{1}{|U_{ij}|} \sum_{u \in U_{ij}} (r_{ui} - r_{uj})$
- 预测：$\hat{r}_{uj} = \frac{1}{|S(u)|} \sum_{i \in S(u)} (r_{ui} + \text{dev}_{ji})$

```bash
python scripts/train/train_slopeone_traditional.py
```

### 6. 改进 Slope One (`train_slopeone_improved.py`)

对应公式：
- 局部偏差：$\text{dev}_{ij}^{\text{local}} = \frac{1}{|U_{\text{nb}} \cap U_{ij}|} \sum_{u' \in U_{\text{nb}} \cap U_{ij}} (r_{u'i} - r_{u'j})$
- 预测：$\hat{r}_{uj} = \frac{1}{|S(u)|} \sum_{i \in S(u)} (r_{ui} + \text{dev}_{ji}^{\text{local}})$
- 最小共同用户数阈值（默认 3）

```bash
python scripts/train/train_slopeone_improved.py --n-neighbors 50 --min-overlap 3
```

### 7. Turbo-CF (`train_turbocf.py`)

K-Means 用户聚类加速版 User-CF。当用户量超过 $10^4$ 时自动启用。

```bash
python scripts/train/train_turbocf.py --n-clusters 50 --n-neighbors 30 --extend-range 1
```

### 8. SVD 矩阵分解 (`train_svd.py`)

基于 sklearn TruncatedSVD (randomized)，保留 k=50 个隐因子。

```bash
python scripts/train/train_svd.py --n-factors 50
```

## 输出文件

所有模型保存至 `models/` 目录：
- `*.pkl` - pickle 序列化的模型文件
- `*_meta.json` - 模型元数据（含 RMSE、训练时间等）

## 训练数据

训练数据从 `scripts/extract_test_subset_test/test_ratings.csv` 加载。