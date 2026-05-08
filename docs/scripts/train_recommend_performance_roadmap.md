# train_recommend.py 性能提升路线图

> 针对 24 核 96GB CPU 服务器 / 高性能 GPU 服务器的优化方案分析

## 一、当前瓶颈分析（基于 v3 代码）

| 阶段 | 当前实现 | 瓶颈类型 | 可扩展性 |
|------|---------|----------|---------|
| **SVD 训练** | `scipy.sparse.linalg.svds`，密集评分矩阵 `n_users × n_movies` | 内存占用大（全量矩阵），SVD 为 O(n²) | 随用户/电影数平方增长 |
| **User-CF 相似度** | SVD 投影后余弦相似度 `u_norm @ u_norm.T` | 矩阵乘法 O(n²)，但对角线操作序列化 | 中等 |
| **Item-CF 相似度** | `normalized @ normalized.T` + `co_counts` 过滤 | O(n²) 矩阵乘法 + 额外过滤操作 | 中等 |
| **RMSE 评估** | 矩阵乘法一次性预测 | 对 51 万条逐一验证 | 随数据量线性增长 |
| **导出阶段** | batch + ThreadPoolExecutor | I/O + JSON 序列化 | 瓶颈在 I/O 和内存带宽 |
| **全局** | Python + numpy/scipy | GIL 限制（ThreadPoolExecutor 中 numpy 释放 GIL，但仍有 Python 层面开销） | 受 Python 运行时限制 |

---

## 二、服务器选型建议

### 方案 A：24 核纯 CPU 服务器（96GB RAM）

**推荐技术栈：Python + numpy + scikit-learn + Numba + multiprocessing**

#### 理由

1. **当前代码已高度向量化**（矩阵乘法、`np.argpartition` 等），numpy 底层调用 OpenBLAS/MKL，本身就能利用所有核心
2. **24 核可全量利用**：设置 `OMP_NUM_THREADS=24`，numpy 矩阵运算自动使用全部核心
3. **96GB 内存充裕**：可容纳 10 万用户 × 5 万电影的稠密矩阵（`np.float32` 约 20 GB）
4. **数据量级评估**：
   - 1000 用户 × 1000 电影 → 当前约 16 分钟
   - 10 万用户 × 5 万电影 → 需要优化，但仍可接受
5. **不需要 CUDA 的原因**：SVD 和 CF 的计算规模，24 核 CPU + 向量化 + Numba JIT 已足够

#### 具体优化措施

| 优化 | 实现方式 | 预期加速 |
|------|---------|---------|
| **Numba JIT 编译** | 用 `@njit(parallel=True)` 加速 `_apply_top_k`、RMSE 计算等热点循环 | 2-5x |
| **跳过 RMSE 评估** | 训练时只计算相似度矩阵，不计算 RMSE（RMSE 可单独评估） | 95% 阶段时间 |
| **scikit-learn TruncatedSVD** | 替代 `svds`，支持 `algorithm='randomized'`，自动多线程 | 2-3x |
| **导出阶段改用 numpy 批量 I/O** | `np.save` / 内存映射（`np.memmap`）替代逐行 CSV | 5-10x |
| **增加 batch_size** | 从 2000 提升到 10000+，减少线程调度开销 | 1.5-2x |
| **ProcessPoolExecutor** | 将 numpy 密集型计算分配到独立进程，突破 GIL | 2-4x（多进程） |

#### 预期性能（24 核）

| 数据规模 | 当前（12核） | 优化后（24核） |
|---------|------------|--------------|
| 1000 用户 × 1000 电影 | ~16 分钟 | **~3-5 分钟** |
| 10 万用户 × 5 万电影 | 无法运行 | **~2-4 小时**（离线训练可接受） |

---

### 方案 B：GPU 服务器（如 RTX 4090 / A100 / H100）

**推荐技术栈：Python + CuPy (numpy 替代) + cuML (cuDF) + PyTorch**

#### 理由

1. **矩阵运算天然适合 GPU**：相似度矩阵 `U @ U.T` 在 GPU 上比 CPU 快 10-50 倍
2. **cuML 提供 GPU 版 SVD**：`cuml.TruncatedSVD` 比 scipy 快 10-100 倍
3. **可扩展到更大数据集**：GPU 显存（24-80 GB）可容纳中型数据集
4. **支持实时训练**：对于增量更新场景，GPU 可在秒级完成全量重算

#### 实现方案对比

| 库 | 安装复杂度 | 性能 | 代码改动量 |
|----|----------|------|-----------|
| **CuPy** | 中等（需显卡驱动 + CUDA Toolkit） | 5-10x（纯 numpy 替换） | 最小（`import cupy as np`） |
| **cuML (RAPIDS)** | 较高（需 conda 或 Docker） | 10-50x（原生 GPU 算法） | 中等（替换 scipy 调用） |
| **PyTorch** | 低（pip install） | 3-10x（需手动实现 SVD） | 较大（重写算法逻辑） |
| **NVIDIA TensorRT** | 高 | 10-100x（推理优化） | 大（仅推理阶段） |

#### 推荐：cuML + CuPy 混合方案

```python
# 代码改动示例
import cupy as cp
from cuml import TruncatedSVD as cumlTruncatedSVD

# SVD 训练（GPU）
svd = cumlTruncatedSVD(n_components=50)
user_features = svd.fit_transform(X)  # 直接在 GPU 上运行

# 相似度矩阵（GPU，CuPy）
sim_matrix = cp.dot(u_norm, u_norm.T)  # 使用 GPU 矩阵乘法
```

#### 预期性能（RTX 4090 vs 24核CPU）

| 阶段 | CPU (24核 + MKL) | GPU (RTX 4090, cuML) | 加速比 |
|------|-----------------|---------------------|-------|
| SVD 训练 (1000×1000) | 1.3 秒 | **<0.01 秒** | 100x+ |
| User-CF 相似度 | 3.7 秒 | **<0.01 秒** | 100x+ |
| Item-CF 相似度 | 0.78 秒 | **<0.01 秒** | 100x+ |
| 导出阶段 | ~140 秒 | **~5-10 秒** | 10-20x |
| **总计** | **~16 分钟** | **<30 秒** | **30x+** |

---

## 三、C/C++ 方案分析

### 使用场景

| 方案 | 适用场景 | 不适用场景 |
|------|---------|-----------|
| **C++ Eigen / Armadillo** | 嵌入式、移动端、无 Python 环境 | 需要频繁迭代算法原型 |
| **CUDA C/C++** | 极致性能、生产级 GPU 加速 | 开发周期长、调试困难 |
| **Cython** | 逐步优化 Python 热点代码 | 不适合大规模重写 |

### 结论：不建议使用 C/C++ 重写

**理由**：
1. **开发成本高**：当前 931 行 Python 代码，用 C++ 重写需 3000-5000 行，且调试周期长
2. **收益有限**：瓶颈在矩阵运算（底层已用 MKL/OpenBLAS/CUDA），Python numpy 调用这些库的性能损失 <5%
3. **维护困难**：团队需要同时维护 C++ 和 Python 两个版本
4. **生态缺失**：Python 有 pandas/numpy/scikit-learn/cuML 等成熟生态，C++ 缺乏等效工具

**例外情况**：如果未来需要**实时推荐（ms 级响应）**，可以考虑：
- 用 C++ Eigen 实现评分预测核函数
- 通过 `pybind11` 暴露给 Python 调用
- 或者直接用 CUDA C++ 实现推理 kernel

---

## 四、推荐架构（多阶段）

### 阶段一：快速优化（当前代码，0-2 天）

```
1. 设置 OMP_NUM_THREADS=24（自动利用 24 核）
2. 添加 --skip-eval 参数跳过 RMSE 评估
3. 导出阶段 batch_size 调至 5000+
4. 使用 np.memmap 替代 CSV 写入
```

### 阶段二：CPU 深度优化（2-5 天）

```
1. 引入 Numba JIT 加速 _apply_top_k 和导出逻辑
2. 用 scikit-learn TruncatedSVD(randomized) 替换 svds
3. 使用 ProcessPoolExecutor 替代 ThreadPoolExecutor
4. 导出改用二进制格式（.npy）加 JSON 序列化
```

### 阶段三：GPU 加速（5-10 天）—— 推荐最终方案

```
1. 安装 RAPIDS (cuML + cuDF + CuPy)
2. 替换:
   - np →  cp (CuPy)
   - scipy.svds → cuml.TruncatedSVD
   - pd.DataFrame → cudf.DataFrame
3. 导出阶段保持 CPU 处理（I/O 瓶颈在磁盘非 GPU）
```

### 阶段四：分布式（可选，>10 天）

```
1. 使用 Dask 或 Ray 进行分布式计算
2. 数据分片 → 多节点并行训练 → 汇总模型
3. 适用于 100 万+ 用户量级
```

---

## 五、最终建议

### 优先推荐：Python + CuPy/cuML（GPU 方案）

```
服务器配置：
  GPU: NVIDIA RTX 4090 (24GB) 或 A100 (80GB)
  CPU: EPYC/Xeon 16 核以上
  内存: 64-128 GB
  显存: 24GB+（决定最大数据规模）

性能预期：
  1000用户×1000电影:  <30 秒（全量训练+导出）
  10万用户×5万电影:  <30 分钟（离线训练）
  预测速度:  <1ms/用户（实时推荐）
```

### 如果预算限制只能选 CPU 服务器

```
服务器配置：
  CPU: 24核 EPYC/Xeon
  内存: 96GB
  SSD: NVMe（导出阶段 I/O 瓶颈）

性能预期：
  1000用户×1000电影:  ~3-5 分钟（跳过 RMSE 评估）
  10万用户×5万电影:  ~2-4 小时（离线训练，可接受）
  预测速度:  ~10ms/用户（numpy 矩阵运算）
```

### 不推荐方案

| 方案 | 理由 |
|------|------|
| **纯 C/C++ 重写** | 开发成本高、维护困难、收益有限 |
| **纯 CUDA C/C++** | 除非需要 ms 级实时推理，否则 Python + cuML 已足够 |
| **放弃 Python 完全迁移** | 生态缺失，不利于团队协作和快速迭代 |

---

## 六、一句话总结

```
当前代码 → 设置环境变量 + 跳过评估 + 调整 batch = 3-5 分钟（24核CPU）
         → 引入 CuPy/cuML + GPU = <30 秒（RTX 4090）
         → C/C++ 重写 = 不必要（收益低，成本高）
保持 Python 生态，利用 numpy/cuML 底层的高性能实现，是最优选择。
```

---

## 附录：关键性能调优参数

### CPU 版（scripts/recommend/train_recommend.py 顶层）

```python
# 24核专用配置
_N_CPUS = 24
os.environ["OMP_NUM_THREADS"]       = "24"   # OpenBLAS/MKL 并行
os.environ["MKL_NUM_THREADS"]       = "24"   # Intel MKL 并行
os.environ["OPENBLAS_NUM_THREADS"]  = "24"   # OpenBLAS 并行
os.environ["NUMEXPR_NUM_THREADS"]   = "24"   # NumExpr 并行
os.environ["VECLIB_MAXIMUM_THREADS"]= "24"   # Accelerate 框架 (macOS)
os.environ["MKL_DYNAMIC"]           = "FALSE" # 禁止 MKL 动态调整线程数
```

### GPU 版（环境要求）

```bash
# RAPIDS 安装（推荐 conda）
conda create -n rapids python=3.12
conda install -c rapidsai -c conda-forge cuml cudf cupy
# 或 Docker
docker pull rapidsai/rapidsai:24.10-cuda12.2-runtime-ubuntu22.04-py3.12