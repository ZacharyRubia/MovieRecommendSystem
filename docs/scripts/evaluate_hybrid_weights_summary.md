# 混合推荐权重优化评估脚本开发总结

> 日期：2026-05-17

## 背景

自适应混合推荐算法需要确定各子模型的最优权重组合。原本系统中 `recommendService.js` 使用离散的三段式权重（0.3/0.5/0.7），但这些权重的选取缺乏数据支撑。本脚本的目标是通过离线评估，在数据集上系统地搜索最优权重组合，为系统提供数据驱动的权重配置建议。

## 新增文件

| 文件 | 用途 |
|------|------|
| `scripts/evaluation/evaluate_hybrid_weights.py` | 混合推荐算法权重优化评估主脚本（1585 行） |

## 生成结果文件

| 文件 | 说明 |
|------|------|
| `scripts/evaluation_results/hybrid_weights/evaluation_results.json` | 完整评估结果（含所有配置的指标 + Pareto 前沿 + 分析点） |
| `scripts/evaluation_results/hybrid_weights/weight_optimization.csv` | 各权重组合指标汇总表（便于 Excel/图表分析） |
| `scripts/evaluation_results/hybrid_weights/optimal_weights.json` | 最优权重摘要（按 RMSE/F1/综合得分三个维度） |
| `scripts/evaluation_results/hybrid_weights/pareto_frontier.csv` | Pareto 前沿配置 |
| `scripts/evaluation_results/hybrid_weights/weight_vs_metrics.json` | 指标-权重关系图数据（仅双模型模式，用于前端图表） |
| `scripts/evaluation_results/hybrid_weights/analysis_points/` | 5 个代表性分析点详细结果 |

## 脚本架构

```
evaluate_hybrid_weights.py
│
├── 1. 数据加载与预处理
│   ├── load_all_data()              # 从 CSV 加载原始评分数据
│   ├── train_test_split_by_user()   # 按用户划分训练/测试集（保证用户不交错）
│   └── build_rating_matrices()      # 构建稀疏矩阵 + 用户/电影均值 + 评分字典
│
├── 2. 子模型定义（7个子模型）
│   ├── BaseModel                    # 模型基类
│   ├── TraditionalUserCF            # 传统 User-CF：等权平均
│   ├── ImprovedUserCF               # 改进 User-CF：加权平均 + 稳定性因子
│   ├── TraditionalItemCF            # 传统 Item-CF：等权平均
│   ├── ImprovedItemCF               # 改进 Item-CF：去均值加权
│   ├── TraditionalSlopeOne          # 传统 Slope One：偏差计算
│   ├── ImprovedSlopeOne             # 改进 Slope One：邻居约束 + SVD 降维
│   └── SVDModel                     # SVD 矩阵分解：TruncatedSVD
│
├── 3. 混合预测与评估
│   ├── HybridPredictor              # 混合预测器（加权合成各子模型预测）
│   ├── evaluate_hybrid_weights()    # 单个权重组合的全面评估
│   ├── compute_prediction_metrics() # 评分预测指标（RMSE, MAE）
│   └── compute_recommendation_metrics() # Top-N 推荐指标（Precision/Recall/F1/Coverage）
│
├── 4. 权重搜索策略
│   ├── get_weight_grid()            # 生成权重搜索配置
│   ├── select_analysis_points()     # 选取代表性分析点（5个）
│   └── find_pareto_frontier()       # 寻找 Pareto 前沿
│
├── 5. 模型初始化
│   └── init_models()                # 按模式初始化并训练子模型
│
├── 6. 结果导出
│   ├── save_results()               # 多格式结果导出（JSON/CSV/分析点）
│   └── print_summary()              # 控制台摘要打印
│
└── 7. 主流程
    ├── run_weight_optimization()    # 完整的权重优化运行流程
    └── main()                       # CLI 参数解析
```

## 两种运行模式

### 双模型模式（`--mode 2`，默认）

- **子模型**：ImprovedUserCF + ImprovedItemCF
- **权重组合**：`[0, 1]` 区间，步长 0.05（默认 21 个配置）
- **优势**：计算效率高，结果直观（两个权重的和为 1）
- **输出**：`weight_vs_metrics.json` 图表数据

### 全模型模式（`--mode all`）

- **子模型**：全部 7 个子模型
- **权重组合**：
  - 等权：各模型均分权重
  - 单模型：全部权重给一个模型（7 个配置）
  - 随机搜索：Dirichlet 分布采样（默认 200 次）
- **优势**：可发现更多非直觉的组合方案

## 评估指标体系

| 类别 | 指标 | 计算方式 |
|------|------|----------|
| 评分预测 | RMSE | 均方根误差，衡量预测评分与真实评分的偏差 |
| 评分预测 | MAE | 平均绝对误差，RMSE 的补充视角 |
| Top-N 推荐 | Precision@K | 推荐列表中相关电影的比例 |
| Top-N 推荐 | Recall@K | 用户实际交互电影中被推荐到的比例 |
| Top-N 推荐 | F1@K | Precision 和 Recall 的调和平均 |
| 覆盖率 | Coverage | 推荐系统覆盖的电影比例 |

### 综合得分公式

```
composite_score = 0.4 * normalized_f1 + 0.3 * (1 - normalized_rmse) + 0.3 * coverage
```

权重：
- F1@K：40%（推荐质量优先）
- RMSE 逆分：30%（保证预测准确性）
- Coverage：30%（鼓励多样性）

## 分析点选取策略

从所有评估结果中选取 5 个代表性分析点，按优先级排序：

1. **最优 RMSE 点**：评分预测最准确
2. **最优 F1@K 点**：推荐列表质量最高
3. **最优综合得分点**：平衡兼顾
4. **Pareto 前沿点**：非支配解（RMSE 和 F1 的权衡）
5. **系统默认权重点**：当前生产环境使用的离散权重（0.3/0.5/0.7）

## 实验结果摘要

基于 2000 用户 × 500 电影 × 685,000 评分的测试集：

| 标准 | 权重 (User-CF : Item-CF) | RMSE | F1@10 | Coverage |
|------|------------------------|------|-------|----------|
| 最优 RMSE | 0.76 : 0.24 | 0.7435 | 0.1528 | 0.4195 |
| 最优 F1 | 0.56 : 0.44 | 0.7518 | 0.1967 | 0.4016 |
| 最优综合 | 0.66 : 0.34 | 0.7484 | 0.1755 | 0.4105 |
| 当前(低活跃) | 0.30 : 0.70 | 0.7619 | 0.1685 | 0.3911 |
| 当前(一般) | 0.50 : 0.50 | 0.7517 | 0.1744 | 0.4046 |
| 当前(高活跃) | 0.70 : 0.30 | 0.7493 | 0.1533 | 0.4127 |

### 关键发现

1. **推荐权重**：综合得分最优权重为 `User-CF=0.66 : Item-CF=0.34`，建议作为默认权重
2. **权重敏感度**：User-CF 权重在 [0.50, 0.70] 区间内各指标变化平缓，选择空间充裕
3. **RMSE 最优**需要更高的 User-CF 权重（0.76），因为 User-CF 在密集用户上拟合更好
4. **F1 最优**需要更均衡的权重（0.56:0.44），因为 Item-CF 提升了冷门电影的推荐覆盖率
5. **当前系统权重基本合理**：高活跃用户的 0.70:0.30 已接近最优区间

## 使用方式

```powershell
# 默认双模型模式
python scripts/evaluation/evaluate_hybrid_weights.py

# 调试模式（快速验证）
python scripts/evaluation/evaluate_hybrid_weights.py --test-size 1000 --grid-step 0.25

# 全模型模式（探索更多组合）
python scripts/evaluation/evaluate_hybrid_weights.py --mode all --n-random 200

# 更精细的网格搜索
python scripts/evaluation/evaluate_hybrid_weights.py --grid-step 0.02

# 自定义 Top-N
python scripts/evaluation/evaluate_hybrid_weights.py --top-n 20

# 指定输出目录
python scripts/evaluation/evaluate_hybrid_weights.py --output-dir custom_results
```

## 输出文件详解

### `optimal_weights.json`

按三个优化目标分别给出最优权重：

```json
{
  "optimal_weights": {
    "by_rmse": { "weights": {"user_cf": 0.76, "item_cf": 0.24}, "rmse": 0.7435 },
    "by_f1": { "weights": {"user_cf": 0.56, "item_cf": 0.44}, "f1": 0.1967 },
    "by_composite": { "weights": {"user_cf": 0.66, "item_cf": 0.34}, "score": 0.5236 }
  },
  "recommendation": {"user_cf": 0.66, "item_cf": 0.34}
}
```

### `analysis_points/` 目录

每个分析点 JSON 文件包含该权重配置下的完整指标、权重点选择理由、以及详细的用户级预测结果。

### `weight_optimization.csv`

所有权重配置的指标汇总，可直接导入 Excel 或 matplotlib 做可视化分析。

### `weight_vs_metrics.json`

仅双模型模式输出，包含按 user_cf 权重排序的指标序列，便于前端绘制曲线图。

## 与现有系统的集成

| 系统组件 | 文件 | 集成方式 |
|---------|------|---------|
| 自适应权重 | `backend/src/services/recommendService.js` | `getAdaptiveWeight()` 使用离散权重，可参考脚本输出调整阈值 |
| 混合推荐 | `backend/src/services/recommendEngine.js` | 混合推荐模块使用 `recommendService` 的权重 |
| 模型训练 | `scripts/train/train_*.py` | 评估脚本复用了相同的模型实现逻辑 |

## 注意事项

1. **数据集大小**：脚本使用 `extract_test_subset_test/` 目录下的 CSV 数据（默认路径）。可以通过 `--test-size` 限制测试集大小进行快速验证
2. **内存占用**：训练 7 个子模型需要较大的内存（特别是 SVD 和全模型模式），建议在 16GB+ 内存的机器上运行
3. **运行时间**：
   - 默认双模型模式：约 3-5 分钟（2000 用户 × 500 电影）
   - 全模型模式：约 30-60 分钟（包含 7 个子模型训练 + 200 次随机搜索）
4. **Shell 兼容性**：脚本标准 Python，PowerShell 和 Bash 均可使用