# 推荐系统数据流向 & 算法全景分析

> 最后更新：2026-05-20 | 状态：全部 8+1 算法正常运行

---

## 一、系统架构总览

```
┌──────────────────────────────────────────────────────────────────┐
│                        前端 (user-dashboard.html)                 │
│  普通推荐: GET /api/recommend/ai?userId=2&algorithm=svd&topN=12  │
│  混合推荐: GET /api/recommend/ai?userId=2&algorithm=hybrid&topN=10│
└──────────────────────────┬───────────────────────────────────────┘
                           │
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│            Node.js 后端 (port 3000)                               │
│                                                                  │
│  recommendController.js                                          │
│   ├─ aiModelRecommend()          参数校验 + 算法路由               │
│   ├─ aiModelList()               返回可用算法列表                  │
│   └─ aiHealthCheck()             健康检查                         │
│                                                                  │
│  recommendEngine.js                核心推理引擎                   │
│   ├─ getRecommendations()       入口：缓存 → 计算 → 写回          │
│   ├─ computeSingleAlgorithm()   单算法调度                        │
│   ├─ recommendHybridAll()       混合加权融合                      │
│   └─ 9 个 recommend*() 函数    各算法实现                        │
│                                                                  │
│  recommendService.js              辅助服务                        │
│   ├─ enrichRecommendations()    补充电影元信息（标题/海报/评分）   │
│   └─ getPopularRecommendations() 热门推荐（降级兜底）             │
└─────────────┬────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     MySQL 缓存层                                  │
│                                                                  │
│  user_recommendation_caches      按用户+算法缓存推荐结果           │
│  item_similarity_caches          电影相似度缓存                    │
│                                                                  │
│  ├─ 读缓存：getCachedRecommendation()    TTL = 1 小时            │
│  └─ 写缓存：saveResultToCache()          REPLACE INTO 异步       │
└─────────────┬────────────────────────────────────────────────────┘
              │
              ▼
┌──────────────────────────────────────────────────────────────────┐
│              backend/models/*.json (8 个预训练模型文件)            │
│                                                                  │
│  svd_model.json                        user_cf_traditional_model.json │
│  user_cf_improved_model.json           item_cf_traditional_model.json │
│  item_cf_improved_model.json           slope_one_traditional_model.json│
│  slope_one_improved_model.json         turbo_cf_model.json            │
└──────────────────────────────────────────────────────────────────┘
```

---

## 二、两条推荐通路

### 2.1 普通推荐（单算法推荐）

**触发条件**：前端下拉框选择具体算法（如 `svd`、`user_cf_traditional`）

**完整数据流**：

```
前端请求
  GET /api/recommend/ai?userId=2&algorithm=svd&topN=12
        │
        ▼
recommendController.aiModelRecommend()
  ├─ [1] 参数校验：userId 有效性、algorithm 白名单检查
  ├─ [2] withTimeout() 超时保护
  └─ [3] recommendEngine.getRecommendations(2, 'svd', 12)
             │
             ├─ Step 1: 查缓存
             │    └─ getCachedRecommendation(2, 'svd')
             │       SELECT * FROM user_recommendation_caches
             │       WHERE user_id=2 AND algorithm='svd'
             │       ├─ 命中 → 立即返回 { fromCache: true, elapsed: 0.001s }
             │       └─ 未命中/过期 → 进入 Step 2
             │
             ├─ Step 2: 加载模型 + 实时计算
             │    ├─ loadModelAsync('svd')  │ 首次：流式读取 JSON (~3MB)
             │    │   └─ 内存缓存 _models    │ 后续：直接返回内存副本
             │    │
             │    ├─ computeSingleAlgorithm('svd', 2, 12)
             │    │   └─ RECOMMEND_FUNCTIONS['svd'](model, 2, 12)
             │    │      └─ recommendSVD(model, 2, 12)
             │    │
             │    └─ 返回到控制器
             │
             └─ Step 3: 异步写回缓存
                  └─ saveResultToCache(2, results, 'svd')
                     REPLACE INTO user_recommendation_caches
                     (user_id, algorithm, recommend_movies, updated_at)
                     VALUES (2, 'svd', '[...]', NOW())
    
      ▼
recommendController 后处理
  ├─ [4] enrichRecommendations()  补充电影标题、海报、平均评分
  ├─ [5] 降级检查：enriched.length === 0 ?
  │    └─ 是 → getPopularRecommendations() 热门兜底
  └─ [6] 返回 JSON
        {
          success: true,
          source: 'ai-model',
          data: {
            userId: 2,
            algorithm: 'svd',
            topN: 12,
            recommendations: [{ movieId, predictedRating, title, poster, ... }],
            degraded: false,
            elapsed: 0.15,
            fromCache: false
          }
        }
```

**支持的算法 ID**（12 个白名单）：

| 算法 ID | 前端名称 | 实际调用 |
|---------|---------|---------|
| `svd` | SVD | recommendSVD |
| `user_cf` / `user_cf_traditional` | 传统 User-CF | recommendUserCF |
| `user_cf_improved` | 改进 User-CF | recommendUserCFImproved |
| `item_cf` / `item_cf_traditional` | 传统 Item-CF | recommendItemCF |
| `item_cf_improved` | 改进 Item-CF | recommendItemCFImproved |
| `slope_one_traditional` | 传统 Slope One | recommendSlopeOne |
| `slope_one_improved` | 改进 Slope One | recommendSlopeOneImproved |
| `turbo_cf` | Turbo-CF | recommendTurboCF |
| `hybrid` | 混合推荐 | recommendHybridAll |
| `popular` | 热门推荐 | getPopularRecommendations |
| `content_based` | 基于内容推荐 | contentBasedRecommend |

---

### 2.2 混合推荐（Hybrid）

**触发条件**：`algorithm=hybrid`（前端默认选项）

**核心函数**：[recommendHybridAll()](file:///d:/Code/MovieRecommendSystem/backend/src/services/recommendEngine.js#L577-L640)

**权重分配**（8 算法 + 旧版 4 算法兼容）：

```
新版权重（8 算法）          旧版权重（4 算法兼容）
─────────────────────      ─────────────────────
svd:                  0.22  svd:         0.35
turbo_cf:             0.18  item_cf:     0.25
user_cf:              0.13  turbo_cf:    0.20
item_cf:              0.13  user_cf:     0.20
item_cf_improved:     0.10
slope_one_traditional: 0.08
slope_one_improved:    0.08
user_cf_improved:      0.08
─────────────────────      ─────────────────────
总计：                 1.00  总计：        1.00
```

**融合算法**（加权平均）：

```
对每部候选电影 m：
  totalScore(m) = Σ (算法_i 的预测分 × 权重_i)
  weightSum(m)  = Σ (权重_i)                     // 仅计算给出结果的算法
  finalScore(m) = totalScore(m) / weightSum(m)   // 归一化

按 finalScore 降序排序，取 Top-N
```

**执行流程**：

```
getRecommendations(userId=2, algorithm='hybrid', topN=10)
  │
  ├─ 并行加载 8 个模型 JSON
  │   for each algo in [svd, user_cf, ..., turbo_cf]:
  │     loadModelAsync(algo)           // 首次 ~0.5s, 后续 ~0ms
  │
  ├─ recommendHybridAll(models, 2, 10)
  │   │
  │   ├─ 检测可用算法 → 选择新版 or 旧版权重
  │   │
  │   ├─ 并行执行 8 个算法（各自 topN=30 候选）
  │   │   for each (algo, weight):
  │   │     results = RECOMMEND_FUNCTIONS[algo](model, 2, 30)
  │   │     if results.length > 0:
  │   │       addScores(results, weight)  // 累加到 scoreMap
  │   │
  │   ├─ 加权融合
  │   │   for each movieId in scoreMap:
  │   │     finalScore = scoreMap[mid] / weightSumMap[mid]
  │   │
  │   └─ 排序 → 取 Top-10
  │
  └─ 写回缓存（算法='hybrid'）
```

---

## 三、训练数据管道（完整链路）

```
┌──────────────────────────────────────────────────────────────────┐
│                        [Phase 1] 数据提取                         │
│                                                                  │
│  MySQL 生产库 (users_movies_behaviors / movies)                  │
│       │                                                          │
│       │ scripts/export/extract_test_subset.py                    │
│       │   --users 2000 --movies 5000 --include-users "1,2,3"    │
│       ▼                                                          │
│  scripts/extract_test_subset_test/                               │
│     ├── test_ratings.csv    (评分数据)                            │
│     ├── test_movies.csv     (电影元数据)                          │
│     ├── test_users.csv      (用户数据)                            │
│     └── test_comments.csv   (评论数据)                            │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                        [Phase 2] 训练                             │
│                                                                  │
│  scripts/train/run_all_trains.ps1 -Sequential (PowerShell)       │
│  scripts/train/run_all_trains.bat  --sequential (Bat)            │
│  scripts/train/run_all_trains.sh   --sequential (Linux)          │
│                                                                  │
│  顺序执行 8 个训练脚本：                                          │
│    1. train_svd.py                  ├─ load_data() 读取 CSV      │
│    2. train_turbocf.py              ├─ 构建稀疏矩阵/映射表        │
│    3. train_usercf_traditional.py   ├─ 训练算法 + RMSE 评估      │
│    4. train_usercf_improved.py      ├─ save_model() → .pkl       │
│    5. train_itemcf_traditional.py   └─ save_model() → _meta.json │
│    6. train_itemcf_improved.py                                   │
│    7. train_slopeone_traditional.py  (自动 --skip-rmse)          │
│    8. train_slopeone_improved.py     (自动 --skip-rmse)          │
│       │                                                          │
│       ▼                                                          │
│  scripts/models/*.pkl (8 个 pickle 模型文件)                     │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                        [Phase 3] 导出                             │
│                                                                  │
│  scripts/export/export_models_to_json.py                         │
│    --model-dir  ../models                                        │
│    --output-dir ../../backend/models                             │
│    --data-dir   ../extract_test_subset_test                      │
│                                                                  │
│  遍历 8 个 .pkl 文件：                                           │
│    ├─ pickle.load() 读取                                         │
│    ├─ convert_numpy() 转换 numpy → Python 原生                   │
│    ├─ 提取核心字段（user2idx、movie_features、item_deviations…）  │
│    ├─ [备选] build_user_movies_from_csv() 补全 user_movies       │
│    └─ json.dump() 写入 backend/models/                           │
│       │                                                          │
│       ▼                                                          │
│  backend/models/*.json (8 个 JSON 模型文件，后端直接读取)         │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                        [Phase 4] 后端加载                         │
│                                                                  │
│  recommendEngine.js                                              │
│    ├─ warmupModels()        服务器启动时预加载全部模型              │
│    └─ loadModelAsync(algo) 首次请求时流式加载 JSON               │
│       ├─ 内存缓存 _models[algo] = parsedJSON                     │
│       └─ 后续请求直接返回 _models[algo]（0ms）                   │
└──────────────────────────────────────────────────────────────────┘
```

> **注意**：Phase 3（JSON 导出）已在 `run_all_trains.*` 脚本末尾自动执行，训练完成后模型立即对后端可用。

---

## 四、三级降级策略

每个推荐请求经过 **3 层保护**，确保始终有结果返回：

```
用户请求推荐
    │
    ▼
[第 1 层] 模型推理
    ├─ 成功 → 返回 AI 推荐结果
    ├─ 结果为空 → 降级
    └─ 异常抛出 → 降级
          │
          ▼
[第 2 层] 热门推荐 (getPopularRecommendations)
    ├─ 成功 → 返回热门电影列表
    └─ 失败 → 降级
          │
          ▼
[第 3 层] 空状态兜底
    └─ 返回 { success: false, message: "推荐失败" }
```

**降级触发条件**：
- 用户不在模型训练集中（`user_movies` / `user2idx` 无此用户）
- 用户无评分记录（`userRatedMovies.length === 0`）
- 模型计算异常（超时、加载失败、内存不足）
- 相似度矩阵中无数剧关联

---

## 五、算法矩阵

### 5.1 算法概览

| # | 算法 | 类型 | 核心思想 | 训练耗时(跳过RMSE) | 模型大小 |
|---|------|------|---------|-------------------|---------|
| 1 | **SVD** | 矩阵分解 | TruncatedSVD 降维，用户/电影隐向量点积 + 用户偏置 | ~1-3 min | ~3 MB |
| 2 | **User-CF Traditional** | 近邻协同 | 余弦相似度 (二元交互)，邻居加权 + 用户均值校正 | ~3-5 min | ~9 MB |
| 3 | **User-CF Improved** | 近邻协同 | 同传统 + alpha 参数自适应调整相似度权重 | ~3-5 min | ~9 MB |
| 4 | **Item-CF Traditional** | 近邻协同 | 用户交互矩阵余弦相似度，物品相似度加权聚合 | ~5-10 min | ~8 MB |
| 5 | **Item-CF Improved** | 近邻协同 | 同传统 + 用户均值偏置校正 | ~5-10 min | ~8 MB |
| 6 | **Slope One Traditional** | 偏差预测 | 全局物品偏差矩阵，dev_ij = mean(r_ui - r_uj) | ~20-40 min | ~34 MB |
| 7 | **Slope One Improved** | 偏差预测 | 邻域用户筛选 + 全局偏差 + min_common 阈值过滤 | ~2-3 min | ~36 MB |
| 8 | **Turbo-CF** | 聚类加速 | K-Means 用户聚类 + 簇内 Item-CF，Turbo 模式加速 | ~3-5 min | ~9 MB |
| — | **Hybrid** | 融合 | 加权平均融合上述全部 8 个算法 | — | — |

### 5.2 模型数据字段对照

| 字段 | SVD | User-CF Trad. | User-CF Impr. | Item-CF Trad. | Item-CF Impr. | SlopeOne Trad. | SlopeOne Impr. | Turbo-CF |
|------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| user2idx | ● | ● | ● | ● | ● | ● | ● | — |
| movie2idx | ● | ● | ● | ● | ● | ● | ● | — |
| idx2user / idx2movie | ● | ● | ● | ● | ● | ● | ● | — |
| user_features / movie_features | ● | — | — | — | — | — | — | — |
| user_means | ● | ● | ● | — | ● | — | — | ● |
| user_movies | — | ● | ● | ● | ● | ● | ● | ● |
| user_neighbors | — | ● | ● | — | — | — | ● | ● |
| all_movies / all_users | — | ● | ● | ● | ● | ● | ● | ● |
| movie_sim_matrix | — | — | — | ● | ● | — | — | — |
| item_deviations | — | — | — | — | — | ● | ● | — |
| item_similarities | — | — | — | ● | ● | — | — | — |
| alpha / user_std | — | — | ● | — | — | — | — | — |
| n_neighbors | — | ● | ● | ● | ● | — | ● | ● |
| centroids / n_clusters | — | — | — | — | — | — | — | ● |
| turbo_enabled | — | — | — | — | — | — | — | ● |

### 5.3 算法技术细节

#### SVD — 矩阵分解
```
预测: r̂_ui = user_features[u] · movie_features[i] + user_means[u]
实现: TruncatedSVD(n_components=50, random_state=42)
输入: CSR 稀疏矩阵 (去用户均值后)
输出: user_features (2003×50), movie_features (1000×50)
```

**优点**：可发现用户-电影的深层隐语义关联，即使无共同评分也能预测。
**限制**：冷启动用户需额外处理（当前 user2idx 无该用户时返回空）。

#### User-CF Traditional — 用户相似度协同
```
相似度: w_uv = |N(u) ∩ N(v)| / sqrt(|N(u)| × |N(v)|)   （基于二元交互）
预测:   r̂_ui = user_mean[u] + Σ(w_uv × sim) / Σ(|sim|)
邻居数:  n_neighbors = 30
```

**优点**：直观理解，响应社会推荐逻辑（"与你相似的人喜欢"）。
**限制**：用户行为稀疏时邻居少，推荐多样性下降。

#### User-CF Improved — 自适应权重
```
相似度调整: adjusted_sim = sim × [α + (1-α) / (1 + common_count)]
  α = 0.5 时：第一位邻居权重 = sim×1.0, 第二位 = sim×0.75, 第三位 = sim×0.67...
```

**优点**：防止第一个邻居对结果影响过大，提高推荐多样性。

#### Item-CF Traditional — 物品相似度协同
```
相似度: 余弦相似度（基于用户交互矩阵的列向量）
预测:   r̂_ui = Σ(sim(i, j) × user_rating[j]) / Σ(|sim(i, j)|)
        j ∈ S(u)的评分历史, sim(i, j) > 0
邻居数:  n_neighbors = 30
```

**优点**：物品相似度可预计算，推理速度快（O(用户评分数 × 30)），适合实时推荐。

#### Item-CF Improved — 偏置校正
```
预测: r̂_ui = Σ(sim(i, j) × (sim(i, j) + user_mean[u] × 0.1)) / Σ(|sim(i, j)|)
```

**优点**：用户偏置校正减少"低分用户推荐高估"问题。

#### Slope One Traditional — 全局偏差预测
```
偏差: dev(i, j) = mean(r_ui - r_uj)    对所有共同评分用户取均值
预测: r̂_ui = mean(r_uj + dev(i, j))    j ∈ S(u)的已评分物品
矩阵: 1000×1000 偏差矩阵, 999,000 有效物品对
```

**优点**：简洁优雅，偏差矩阵一次计算可全局复用。
**限制**：原始 O(N²) 嵌套循环；偏差矩阵 ~34MB JSON 加载慢。

#### Slope One Improved — 邻域过滤
```
改进点:
  [1] 保留全局偏差矩阵（非每用户局部计算）
  [2] 为目标用户找 M 个最相似邻居
  [3] 预测时只使用邻居共同评分的物品对
  [4] freq >= min_common=3 阈值过滤噪声
```

**训练时间对比**：原版 2 小时+ → 改进版 2-3 分钟（30倍+ 提升）

#### Turbo-CF — 聚类加速
```
流程:
  [1] K-Means 聚类用户 (n_clusters=50)
  [2] 簇内计算 Item-CF 相似度
  [3] 推荐时优先使用簇内邻居（Turbo 模式）
  [4] 如果 Turbo 未启用 → 回退为 User-CF 模式
```

**优点**：将 O(N²) 用户相似度降为 O(K×M²)（K=簇数, M=簇内用户数），适合海量用户。

---

## 六、关键数据模型对照

| 层级 | 路径 | 格式 | 大小(总计) | 消费者 |
|------|------|------|-----------|--------|
| 原始数据 | `scripts/extract_test_subset_test/test_ratings.csv` | CSV | ~30 MB | 训练脚本 |
| 训练产物 | `scripts/models/*.pkl` | Pickle | ~40 MB | 导出脚本 |
| 后端模型 | `backend/models/*.json` | JSON | ~80 MB | recommendEngine.js |
| 请求缓存 | `user_recommendation_caches` | MySQL JSON | N/A | 所有推荐请求 |

---

## 七、性能预期

| 场景 | 耗时 | 说明 |
|------|------|------|
| 缓存命中 | ~3 ms | MySQL 读取 + JSON 解析 |
| 首次 AI (单算法) | ~150 ms | 加载 JSON (~3MB) + 计算 |
| 后续 AI (单算法) | ~50 ms | 模型已在内存 |
| 首次 Hybrid | ~600 ms | 加载 8 个模型 + 8 路并列 |
| 后续 Hybrid | ~200 ms | 全部模型已在内存 |
| SlopeOne Traditional | ~3-5 s | 1000×1000 偏差矩阵查找 |
| SlopeOne Improved | ~500 ms | 邻域缩小查找范围 |

---

## 八、相关文档

| 文档 | 说明 |
|------|------|
| [cache-first-recommendation-architecture.md](file:///d:/Code/MovieRecommendSystem/docs/backend/cache-first-recommendation-architecture.md) | 缓存优先架构设计 |
| [python-to-nodejs-ai-migration.md](file:///d:/Code/MovieRecommendSystem/docs/backend/python-to-nodejs-ai-migration.md) | AI 引擎 Python→Node.js 迁移记录 |
| [recommendation-algorithm-implementation-plan.md](file:///d:/Code/MovieRecommendSystem/docs/backend/recommendation-algorithm-implementation-plan.md) | 算法实现计划 |
| [../scripts/train_optimization_summary.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/train_optimization_summary.md) | 训练优化总结 |
| [../fix/2026-05-19_user-dashboard-simplify-and-model-integration.md](file:///d:/Code/MovieRecommendSystem/docs/fix/2026-05-19_user-dashboard-simplify-and-model-integration.md) | 8 模型集成记录 |
