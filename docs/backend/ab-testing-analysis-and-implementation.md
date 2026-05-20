# A/B 测试自适应框架 — 分析总结 & 实施记录

> 最后更新：2026-05-20 | 状态：框架骨架已就绪，开始补全核心逻辑

---

## 一、A/B 测试是什么？

以电影推荐系统为例：

```
实验：对比"新版推荐算法"vs"旧版推荐算法"
  ├─ 对照组 (Control)：现有 hybrid 混合推荐
  └─ 实验组 (Treatment)：新的 SVD-only 推荐

50% 用户 → 对照组 → 统计点击率 (CTR)、评分率、观看时长
50% 用户 → 实验组 → 同上

统计检验 → 判断实验组是否显著优于对照组
  是 → 全量推优（100% 流量切到新算法）
  否 → 回退到旧算法
```

### 核心概念

| 术语 | 解释 |
|------|------|
| **实验 (Experiment)** | 一次对比测试，包含多个策略 |
| **策略 (Strategy)** | 参与对比的具体推荐算法方案 |
| **固定分流 (Fixed)** | 按比例随机分配用户，比例不变 |
| **自适应分流 (Bandit)** | Thompson Sampling 自动调权，差策略少流量、好策略多流量 |
| **统计显著性** | p < 0.05 说明只有 5% 概率是巧合，即实验组真的更好 |
| **收敛判定** | 某策略连续获胜概率 > 95% + 实验运行 ≥ 24h → 自动结束 |

---

## 二、两层"自适应"区分

### 第一层：推荐算法内部自适应（已实现）

在 [自适应设计.md](file:///d:/Code/MovieRecommendSystem/docs/design/自适应设计.md) 第 4 节：

```
用户评分数 < 10  → User-CF 权重 0.3, Item-CF 权重 0.7  (新用户倾向物品相似)
用户评分数 10-49 → User-CF 权重 0.5, Item-CF 权重 0.5  (均衡)
用户评分数 ≥ 50  → User-CF 权重 0.7, Item-CF 权重 0.3  (老用户倾向用户相似)
```

这是 Hybrid 推荐的内部分配策略，不涉及 A/B 测试框架。

### 第二层：A/B 测试自适应流量分发（设计完成，正在实现）

在 [在线自适应AB测试框架设计.md](file:///d:/Code/MovieRecommendSystem/docs/design/在线自适应AB测试框架设计.md) 和 [AB测试实施计划.md](file:///d:/Code/MovieRecommendSystem/docs/design/AB测试实施计划.md)：

- Thompson Sampling 建模每种算法的 Beta(α, β) 分布
- 每 30 分钟离线分析：聚合行为数据 → 更新 α/β → 写回 Redis
- 新用户：从 Beta 分布采样选择策略（自动倾向表现好的算法）
- 收敛后自动推全

---

## 三、Thompson Sampling 原理

```
每个策略维护一个 Beta(α, β) 分布：

  α = 1 + 正向事件数（点击/高分/收藏）
  β = 1 + 负向事件数（曝光但无交互）

  期望成功率 = α / (α + β)

  Beta(158, 42) → E[θ] ≈ 0.79 → 这个策略大约 79% 成功率
  Beta(30, 70)  → E[θ] ≈ 0.30 → 这个策略大约 30% 成功率

每次为新用户分配策略：
  ① 从每个策略的 Beta(α, β) 随机采样一个 θ
  ② 选 θ 最大的策略
  ③ 用户后续始终使用同一策略

结果：好的策略自动获得更多流量，差的策略自动减少
```

---

## 四、完整数据流

```
管理员创建实验 → MySQL ab_experiments + ab_strategies
                         │
                         ▼
         Node.js abTestMiddleware (每次推荐请求时触发)
         ├─ user_id → MD5 → 桶号 0-99
         ├─ 查询实验配置 + 用户桶覆盖记录
         ├─ Fixed 模式：桶号查映射表 → 策略 ID
         └─ Bandit 模式：Thompson Sampling → 策略 ID
                         │
                         ▼
         推荐引擎根据 strategy.algorithm 路由
         ├─ 'svd' → recommendSVD()
         ├─ 'user_cf_traditional' → recommendUserCF()
         └─ 'hybrid' → recommendHybridAll()
                         │
                         ▼
         用户行为埋点 → users_movies_behaviors
         (experiment_id, strategy_id 随行写入)
                         │
                         ▼ (每 30 分钟)
         Python 离线分析 (ab_analysis.py)
         ├─ 按实验+策略分组聚合指标 (CTR/评分率/观看时长)
         ├─ 统计检验 (Z 检验/t 检验)
         ├─ 更新 Beta(α, β) → Redis
         └─ 判断收敛 → 自动终止实验
                         │
                         ▼
         Redis: ab:bandit:{exp_id}:{strat_id}:{alpha|beta}
         ← 下一次 Thompson Sampling 读这里
```

---

## 五、实现现状

### 已就绪 ✅

| 组件 | 文件 | 行数 |
|------|------|:--:|
| MySQL 建表 DDL (4 表 + ALTER) | `database/init.sql` | ~100 行 |
| 实验配置服务 (分桶/Thompson/缓存) | `backend/src/services/abTestService.js` | 402 行 |
| 流量分发中间件 | `backend/src/middleware/abTestMiddleware.js` | 87 行 |
| 内部分析数据接口 | `backend/src/routes/abInternal.js` | 153 行 |
| 中间件已注册到 app | `backend/server.js` L107-L116 | ✅ |
| recommenderController 读取 req.experiment | `backend/src/controllers/recommendController.js` L504 | ✅ |

### 本次补全 🔧

| 组件 | 文件 | 说明 |
|------|------|------|
| Redis 客户端引用修复 | `config/redis.js` + `abTestService.js` | 统一用 cacheService.getRedisClient() |
| 推荐引擎策略路由 | `services/recommendEngine.js` | getRecommendations 接受 experiment 参数 |
| Python 离线分析 | `scripts/analysis/ab_analysis.py` | 聚合+检验+参数更新 |
| 统计工具库 | `scripts/analysis/stat_utils.py` | Z 检验/t 检验/置信区间/获胜概率 |

---

## 六、相关文档索引

| 文档 | 说明 |
|------|------|
| [在线自适应AB测试框架设计.md](file:///d:/Code/MovieRecommendSystem/docs/design/在线自适应AB测试框架设计.md) | 架构、模块、数据库、API 设计 |
| [AB测试实施计划.md](file:///d:/Code/MovieRecommendSystem/docs/design/AB测试实施计划.md) | 六阶段实施路线图 + 全部 SQL/代码 |
| [自适应设计.md](file:///d:/Code/MovieRecommendSystem/docs/design/自适应设计.md) | 推荐算法内部自适应权重 + 混合推荐 |
| [recommendation-dataflow-and-algorithms.md](file:///d:/Code/MovieRecommendSystem/docs/backend/recommendation-dataflow-and-algorithms.md) | 8 算法推荐数据流向全景 |
