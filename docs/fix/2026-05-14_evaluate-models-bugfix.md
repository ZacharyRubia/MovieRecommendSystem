# evaluate_models.py Bug 修复总结

## 修复时间
2026-05-14

## 修复的 Bug：`numpy.ndarray` 对象没有 `.multiply()` 方法

### 错误现象

运行 `python evaluate_models.py --test-size 100` 时崩溃：

```
AttributeError: 'numpy.ndarray' object has no attribute 'multiply'
```

### 错误原因

在 `evaluate_models.py` 中，多处代码使用了以下模式对稀疏评分矩阵进行去均值操作：

```python
centered = rating_matrix - user_means[:, None].multiply(
    rating_matrix.astype(bool).astype(np.float32)
)
```

问题在于：
1. `user_means` 是 `numpy.ndarray`（稠密数组）
2. `user_means[:, None]` 将其扩展为二维稠密数组，仍然是 `numpy.ndarray`
3. `numpy.ndarray` 没有 `.multiply()` 方法（该方法是 `scipy.sparse` 矩阵特有的）

### 修复方案

将去均值操作改为使用稀疏矩阵的底层数据操作：

```python
centered = rating_matrix.copy().astype(np.float32)
row_indices = np.repeat(np.arange(n_users), np.diff(rating_matrix.indptr))
centered.data -= user_means[row_indices.astype(np.int32)]
```

**原理**：
- `rating_matrix.indptr` 是 CSR 格式的行指针数组，`np.diff(rating_matrix.indptr)` 得到每行的非零元素数量
- `np.repeat(np.arange(n_users), ...)` 生成每个非零元素对应的行索引
- 直接对 `centered.data`（非零值数组）减去对应的用户均值，实现高效的去均值

### 修改的文件

**文件**: `scripts/recommend/evaluate_models.py`

共修复 4 处：

| 位置 | 类名 | 方法 | 行号（修复后） |
|------|------|------|-----------------|
| 1 | `TraditionalUserCF` | `train()` | ~190行 |
| 2 | `ImprovedUserCF` | `train()` | ~260行 |
| 3 | `ImprovedSlopeOne` | `train()` | ~670行 |
| 4 | `SVDModel` | `train()` | ~800行 |

### 影响范围

- 传统 User-CF（Pearson 相似度）
- 改进 User-CF
- 改进 Slope One（SVD 降维邻居查找）
- SVD 模型训练

### 验证结果

修复后脚本可以正常启动并进入模型训练阶段，所有 4 个模型（TraditionalUserCF、ImprovedUserCF、ImprovedSlopeOne、SVDModel）的 `train()` 方法中的 `.multiply()` 错误已全部消除。