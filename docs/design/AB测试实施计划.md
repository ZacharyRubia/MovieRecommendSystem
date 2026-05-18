# 在线自适应 A/B 测试框架 — 实施计划

> **文档版本:** 1.0  
> **更新日期:** 2026-05-18  
> **总览:** 从设计文档到代码落地的完整实施路线图，包含阶段划分、任务拆解、文件清单、依赖关系、优先级与工作量估算  
> **关联文档:** `docs/design/在线自适应AB测试框架设计.md`

---

## 目录

- [1. 实施路线总览](#1-实施路线总览)
- [2. 阶段一：数据库层（基础建设）](#2-阶段一数据库层基础建设)
- [3. 阶段二：后端中间件与服务层](#3-阶段二后端中间件与服务层)
- [4. 阶段三：Python 离线分析模块](#4-阶段三python-离线分析模块)
- [5. 阶段四：自适应流量决策模块](#5-阶段四自适应流量决策模块)
- [6. 阶段五：API 接口与管理面板](#6-阶段五api-接口与管理面板)
- [7. 阶段六：测试与上线](#7-阶段六测试与上线)
- [8. 任务依赖图](#8-任务依赖图)
- [9. 工作量估算汇总](#9-工作量估算汇总)
- [10. 实施风险与应对策略](#10-实施风险与应对策略)

---

## 1. 实施路线总览

### 1.1 整体阶段划分

| 阶段 | 名称 | 核心交付 | 预估工期 | 前置依赖 |
|:----:|------|----------|:--------:|:--------:|
| **P1** | 数据库层 | `init.sql` 新增 4 张 A/B 表 + 1 张扩展字段 ALTER | 1 天 | 无 |
| **P2** | 后端中间件与服务层 | 流量分发中间件、实验配置管理、推荐路由切换 | 3-4 天 | P1 |
| **P3** | Python 离线分析模块 | `ab_analysis.py` 统计分析、Bandit 参数更新脚本 | 3 天 | P1 |
| **P4** | 自适应流量决策 | Thompson Sampling、收敛判定、冷启动保护 | 2 天 | P2 + P3 |
| **P5** | API 接口与管理面板 | 实验管理 API + 前端管理面板页面 | 3-4 天 | P2 |
| **P6** | 测试与上线 | 内测 1% → 5-10% → 全量，监控告警部署 | 2 天 | P1~P5 |

**总预估工期：14~17 天（含并行任务）**

### 1.2 实施优先级

```
P1: 🚨 最高优先级（数据库基础，阻塞后续所有任务）
P2: 🚨 高优先级（核心中间件，影响推荐主链路）
P3: 🚨 高优先级（离线分析，与 P2 可并行）
P4:   ⚠️ 中优先级（依赖 P2 + P3 完成）
P5:   ⚠️ 中优先级（管理与可视化，依赖 P2）
P6:   ✅ 正常优先级（集成测试）
```

### 1.3 并行建议

```
Week 1
  Mon-Tue: P1 (数据库) + 环境准备
  Wed-Sat: P2 (后端) ←→ P3 (Python脚本) 并行开发
Week 2
  Mon-Tue: P4 (自适应模块) — 集成 P2+P3
  Wed-Fri: P5 (管理面板) — 与 P4 并行
  Sat-Sun: P6 (测试+上线)
```

---

## 2. 阶段一：数据库层（基础建设）

### 2.1 概述

在设计文档 `init.sql` 基础上，新增 A/B 测试专用表，并扩展现有行为表。

### 2.2 任务清单

| # | 任务 | 文件 | 说明 | 优先级 | 预估工时 |
|:-:|------|------|------|:------:|:--------:|
| 1.1 | 创建 `ab_experiments` 表 | `database/init.sql` | 实验元信息表（状态、分流模式、起止时间） | P0 | 0.5h |
| 1.2 | 创建 `ab_strategies` 表 | `database/init.sql` | 策略配置表（算法标识、初始权重、最小流量） | P0 | 0.5h |
| 1.3 | 创建 `ab_results` 表 | `database/init.sql` | 分析结果表（各策略指标、p值、置信区间、获胜概率） | P0 | 0.5h |
| 1.4 | 创建 `user_bucket_override` 表 | `database/init.sql` | 用户分桶覆盖表（自适应模式动态指定策略） | P0 | 0.5h |
| 1.5 | ALTER `users_movies_behaviors` 扩展字段 | `database/init.sql` 或迁移脚本 | 新增 `experiment_id`、`strategy_id`、复合索引 | P0 | 0.5h |
| 1.6 | 创建索引优化查询 | `database/init.sql` | `ab_results` 的复合索引、`users_movies_behaviors` 实验索引 | P1 | 0.5h |

### 2.3 SQL 代码实现

#### 新增表（追加到 `init.sql` 末尾）

```sql
-- ============================================
-- 19. A/B 实验配置表 (ab_experiments)
-- ============================================
CREATE TABLE IF NOT EXISTS `ab_experiments` (
    `id`                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '实验ID',
    `name`              VARCHAR(100) NOT NULL COMMENT '实验名称',
    `description`       TEXT COMMENT '实验描述',
    `status`            ENUM('draft', 'running', 'stopped', 'archived') NOT NULL DEFAULT 'draft'
                        COMMENT '状态：草稿/运行中/已停止/已归档',
    `split_mode`        ENUM('fixed', 'bandit') NOT NULL DEFAULT 'fixed'
                        COMMENT '分流模式：fixed=固定比例, bandit=自适应Bandit',
    `start_time`        DATETIME NOT NULL COMMENT '开始时间',
    `end_time`          DATETIME DEFAULT NULL COMMENT '结束时间（为空不自动结束）',
    `created_at`        TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    `updated_at`        TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX `idx_status` (`status`),
    INDEX `idx_time` (`start_time`, `end_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='A/B 实验配置表';

-- ============================================
-- 20. 实验策略配置表 (ab_strategies)
-- ============================================
CREATE TABLE IF NOT EXISTS `ab_strategies` (
    `id`                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '策略ID',
    `experiment_id`     BIGINT UNSIGNED NOT NULL COMMENT '所属实验ID',
    `name`              VARCHAR(100) NOT NULL COMMENT '策略名称',
    `algorithm_key`     VARCHAR(50) NOT NULL COMMENT '推荐算法标识（如 user_cf_v2, hybrid_v3）',
    `initial_weight`    DECIMAL(5,4) NOT NULL DEFAULT 0.0000 COMMENT '初始流量权重 (0~1)',
    `weight_source`     ENUM('initial', 'adaptive', 'manual') NOT NULL DEFAULT 'initial'
                        COMMENT '当前权重来源',
    `min_traffic`       DECIMAL(5,4) DEFAULT 0.0500 COMMENT '最小流量下限',
    `config_json`       JSON DEFAULT NULL COMMENT '策略专属配置（算法参数等）',
    `created_at`        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`experiment_id`) REFERENCES `ab_experiments`(`id`) ON DELETE CASCADE,
    INDEX `idx_exp` (`experiment_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实验策略配置表';

-- ============================================
-- 21. 实验分析结果表 (ab_results)
-- ============================================
CREATE TABLE IF NOT EXISTS `ab_results` (
    `id`                BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY COMMENT '记录ID',
    `experiment_id`     BIGINT UNSIGNED NOT NULL COMMENT '实验ID',
    `strategy_id`       BIGINT UNSIGNED NOT NULL COMMENT '策略ID',
    `analysis_time`     DATETIME NOT NULL COMMENT '分析时间点',
    `window_start`      DATETIME NOT NULL COMMENT '数据窗口起始',
    `window_end`        DATETIME NOT NULL COMMENT '数据窗口结束',
    `impressions`       BIGINT UNSIGNED DEFAULT 0 COMMENT '曝光次数',
    `clicks`            BIGINT UNSIGNED DEFAULT 0 COMMENT '点击次数',
    `ctr`               DECIMAL(8,6) DEFAULT NULL COMMENT '点击率',
    `ctr_ci_lower`      DECIMAL(8,6) DEFAULT NULL COMMENT 'CTR 95% 置信区间下限',
    `ctr_ci_upper`      DECIMAL(8,6) DEFAULT NULL COMMENT 'CTR 95% 置信区间上限',
    `avg_watch_duration` DECIMAL(10,4) DEFAULT NULL COMMENT '人均观看时长（秒）',
    `rating_rate`       DECIMAL(8,6) DEFAULT NULL COMMENT '评分率',
    `favorite_rate`     DECIMAL(8,6) DEFAULT NULL COMMENT '收藏率',
    `p_value`           DECIMAL(10,8) DEFAULT NULL COMMENT 'vs 对照组的 p 值',
    `is_winner`         BOOLEAN DEFAULT FALSE COMMENT '是否显著优胜',
    `win_probability`   DECIMAL(8,6) DEFAULT NULL COMMENT '获胜概率（Bandit模式）',
    `sample_size`       INT UNSIGNED DEFAULT 0 COMMENT '参与用户数',
    `is_converged`      BOOLEAN DEFAULT FALSE COMMENT '是否已收敛',
    `created_at`        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (`experiment_id`) REFERENCES `ab_experiments`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`strategy_id`) REFERENCES `ab_strategies`(`id`) ON DELETE CASCADE,
    INDEX `idx_exp_strat_time` (`experiment_id`, `strategy_id`, `analysis_time`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='实验分析结果表';

-- ============================================
-- 22. 用户分桶覆盖表 (user_bucket_override)
-- ============================================
CREATE TABLE IF NOT EXISTS `user_bucket_override` (
    `user_id`           BIGINT UNSIGNED NOT NULL COMMENT '用户ID',
    `experiment_id`     BIGINT UNSIGNED NOT NULL COMMENT '实验ID',
    `strategy_id`       BIGINT UNSIGNED NOT NULL COMMENT '策略ID',
    `assigned_at`       TIMESTAMP DEFAULT CURRENT_TIMESTAMP COMMENT '分配时间',
    `expires_at`        DATETIME DEFAULT NULL COMMENT '过期时间',
    PRIMARY KEY (`user_id`, `experiment_id`),
    FOREIGN KEY (`experiment_id`) REFERENCES `ab_experiments`(`id`) ON DELETE CASCADE,
    FOREIGN KEY (`strategy_id`) REFERENCES `ab_strategies`(`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='用户分桶覆盖表（自适应模式下动态指定策略）';

-- ============================================
-- 23. ALTER users_movies_behaviors 扩展实验字段
-- ============================================
ALTER TABLE `users_movies_behaviors`
    ADD COLUMN IF NOT EXISTS `experiment_id` BIGINT UNSIGNED DEFAULT NULL
        COMMENT '命中的实验ID，未参与实验为 NULL',
    ADD COLUMN IF NOT EXISTS `strategy_id`   BIGINT UNSIGNED DEFAULT NULL
        COMMENT '命中的策略ID',
    ADD INDEX IF NOT EXISTS `idx_experiment` (`experiment_id`, `strategy_id`);
```

**注意**: MySQL 5.7 不支持 `ADD COLUMN IF NOT EXISTS`，需使用存储过程或手工执行迁移脚本。建议：

```bash
# 方式一：手动执行（生产环境推荐）
echo "ALTER TABLE users_movies_behaviors ADD COLUMN experiment_id BIGINT UNSIGNED DEFAULT NULL COMMENT '命中的实验ID';" | mysql MovieRecommendSystem
echo "ALTER TABLE users_movies_behaviors ADD COLUMN strategy_id BIGINT UNSIGNED DEFAULT NULL COMMENT '命中的策略ID';" | mysql MovieRecommendSystem
echo "ALTER TABLE users_movies_behaviors ADD INDEX idx_experiment (experiment_id, strategy_id);" | mysql MovieRecommendSystem

# 方式二：使用 Node.js 迁移脚本 (建议：创建 backend/migrations/ 目录)
node backend/migrations/20260518_add_ab_testing_fields.js
```

### 2.4 验收标准

- [ ] `ab_experiments`、`ab_strategies`、`ab_results`、`user_bucket_override` 四张表创建成功
- [ ] `users_movies_behaviors` 成功扩展 `experiment_id` 和 `strategy_id` 字段
- [ ] 外键约束正确，级联删除行为符合预期
- [ ] 索引覆盖常用查询模式

---

## 3. 阶段二：后端中间件与服务层

### 3.1 文件清单

| # | 文件路径 | 职责 | 新增/修改 |
|:-:|----------|------|:---------:|
| 2.1 | `backend/src/middleware/abTestMiddleware.js` | 流量分发中间件（核心） | **新增** |
| 2.2 | `backend/src/services/abTestService.js` | 实验配置管理、分桶逻辑、Thompson Sampling | **新增** |
| 2.3 | `backend/src/services/recommendEngine.js` | 修改: 根据 strategy_id 路由推荐算法 | **修改** |
| 2.4 | `backend/src/routes/recommendRoutes.js` | 修改: 响应增加 experiment 字段 | **修改** |
| 2.5 | `backend/server.js` | 注册 abTestMiddleware 中间件 | **修改** |
| 2.6 | `backend/src/config/abTest.js` | 配置文件：Redis 连接、定时器间隔等 | **新增** |
| 2.7 | `backend/src/redis/client.js` | Redis 客户端初始化（如无则新建） | **新增** |

### 3.2 详细实现步骤

#### 步骤 2.1: Redis 客户端初始化 `backend/src/redis/client.js`

```javascript
const Redis = require('ioredis');
const config = require('../config/abTest');

let client = null;

function getRedisClient() {
  if (!client) {
    client = new Redis({
      host: config.redis.host || 'localhost',
      port: config.redis.port || 6379,
      db: config.redis.db || 0,
      retryStrategy: (times) => Math.min(times * 50, 2000),
    });

    client.on('error', (err) => {
      console.error('[Redis] Connection error:', err);
    });
  }
  return client;
}

module.exports = { getRedisClient };
```

#### 步骤 2.2: 配置文件 `backend/src/config/abTest.js`

```javascript
module.exports = {
  redis: {
    host: process.env.REDIS_HOST || 'localhost',
    port: parseInt(process.env.REDIS_PORT || '6379'),
    db: 0,
  },
  abTest: {
    bucketTotal: 100,                     // 总桶数
    refreshIntervalMs: 60000,             // 实验配置刷新间隔（1分钟）
    batchWindowMs: 600000,                // Thompson Sampling 批处理窗口（10分钟）
    coldStartMinutes: 120,                // 冷启动保护期（2小时）
    convergencePeriods: 6,                // 收敛判定连续周期数
    convergenceWinProb: 0.95,             // 收敛获胜概率阈值
    minExperimentHours: 24,               // 最小运行时间（小时）
    minTrafficDefault: 0.05,              // 默认最小流量
    significanceLevel: 0.05,              // 显著性水平
  },
  db: {
    host: process.env.DB_HOST || 'localhost',
    port: parseInt(process.env.DB_PORT || '3306'),
    database: 'MovieRecommendSystem',
    user: process.env.DB_USER || 'root',
    password: process.env.DB_PASSWORD || '',
  },
};
```

#### 步骤 2.3: 实验配置服务 `backend/src/services/abTestService.js`

此文件是核心，包含以下主要功能：

**模块结构**：

```javascript
// abTestService.js - 导出接口
module.exports = {
  // 初始化：从 DB 加载实验配置，设置定时刷新
  init,

  // 获取所有进行中的实验
  getActiveExperiments,

  // 计算用户桶号
  getBucket,

  // 根据桶号查找命中的策略
  getStrategyByBucket,

  // 读取 Redis 后验参数，执行 Thompson Sampling
  thompsonSample,

  // 查询用户分桶覆盖
  getBucketOverride,

  // 设置用户分桶覆盖
  setBucketOverride,

  // 构建桶映射表
  buildBucketMap,
};
```

**关键方法实现细节**：

```javascript
// 2.3.1: init — 从 DB 加载实验配置并定时刷新
async function init() {
  await loadActiveExperiments();
  // 每分钟增量更新
  setInterval(async () => {
    try {
      await refreshExperiments();
    } catch (err) {
      console.error('[ABTest] Refresh experiments error:', err);
    }
  }, config.abTest.refreshIntervalMs);

  // 订阅 Redis 即时更新通知
  const redis = getRedisClient();
  redis.subscribe('ab:experiment:update', (err, count) => { /* ... */ });
  redis.on('message', (channel, message) => {
    if (channel === 'ab:experiment:update') {
      const { experimentId } = JSON.parse(message);
      refreshSingleExperiment(experimentId);
    }
  });
}

// 2.3.2: loadActiveExperiments — 从 MySQL 加载
async function loadActiveExperiments() {
  const rows = await db.query(`
    SELECT e.*, s.id AS strategy_id, s.name AS strategy_name,
           s.algorithm_key, s.initial_weight, s.min_traffic, s.weight_source
    FROM ab_experiments e
    LEFT JOIN ab_strategies s ON s.experiment_id = e.id
    WHERE e.status = 'running' AND NOW() BETWEEN e.start_time AND COALESCE(e.end_time, '2099-12-31')
  `);
  // 组装成 Map<experimentId, { ...strategies, bucketMap }>
  experimentsCache = buildCache(rows);
  buildAllBucketMaps();
}

// 2.3.3: getBucket — MD5 哈希分桶
const crypto = require('crypto');
function getBucket(userId) {
  const hash = crypto.createHash('md5').update(String(userId)).digest('hex');
  // 取前 8 位十六进制 → 32位整数 → mod 100
  const bucket = parseInt(hash.substring(0, 8), 16) % config.abTest.bucketTotal;
  return bucket;
}

// 2.3.4: thompsonSample — 读取 Redis 后验参数并采样
async function thompsonSample(experimentId) {
  const exp = experimentsCache.get(experimentId);
  if (!exp) return null;

  const samples = [];
  for (const s of exp.strategies) {
    const alpha = parseFloat(await redis.get(`ab:bandit:${experimentId}:${s.id}:alpha`)) || 1;
    const beta  = parseFloat(await redis.get(`ab:bandit:${experimentId}:${s.id}:beta`))  || 1;
    const theta = betaRandom(alpha, beta);
    samples.push({ strategyId: s.id, theta });
  }

  samples.sort((a, b) => b.theta - a.theta);
  return samples[0].strategyId;
}

// Beta 分布采样 (利用 Gamma 分布)
function betaRandom(alpha, beta) {
  const x = gammaRandom(alpha, 1);
  const y = gammaRandom(beta, 1);
  return x / (x + y);
}

function gammaRandom(shape, scale) {
  // Marsaglia and Tsang's method
  const d = shape - 1 / 3;
  const c = 1 / Math.sqrt(9 * d);
  while (true) {
    let x, v;
    do {
      x = normalRandom();
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    const u = Math.random();
    if (u < 1 - 0.0331 * x * x * x * x) return d * v * scale;
    if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v * scale;
  }
}

function normalRandom() {
  // Box-Muller transform
  const u = Math.random() || 0.0001;
  const v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
```

#### 步骤 2.4: 流量分发中间件 `backend/src/middleware/abTestMiddleware.js`

```javascript
const abTestService = require('../services/abTestService');

async function abTestMiddleware(req, res, next) {
  // 1. 获取用户标识
  const userId = req.user?.id || req.headers['x-device-fingerprint'] || req.sessionID;
  if (!userId) return next();

  // 2. 计算桶号
  const bucket = abTestService.getBucket(userId);

  // 3. 遍历进行中的实验
  const experiments = abTestService.getActiveExperiments();
  const experimentResult = {};

  for (const [expId, exp] of experiments) {
    let strategyId = null;

    try {
      if (exp.splitMode === 'fixed') {
        // 固定模式：桶号查映射表
        const hit = exp.bucketMap[bucket];
        if (hit) strategyId = hit;
      } else {
        // Bandit 模式
        const override = await abTestService.getBucketOverride(userId, expId);
        if (override) {
          strategyId = override;
        } else {
          // 检查冷启动保护期
          const isCold = isInColdStart(exp);
          if (isCold) {
            // 保护期内使用 initial_weight 策略
            strategyId = getColdStartStrategy(exp);
          } else {
            // Thompson Sampling
            strategyId = await abTestService.thompsonSample(expId);
          }
          // 记录覆盖
          await abTestService.setBucketOverride(userId, expId, strategyId);
        }
      }
    } catch (err) {
      console.error(`[ABTest] Experiment ${expId} routing error:`, err);
      continue;
    }

    if (strategyId) {
      experimentResult[expId] = strategyId;
    }
  }

  // 4. 挂载到请求
  req.experiment = experimentResult;
  res.set('X-Experiment-Id', JSON.stringify(experimentResult));
  next();
}

function isInColdStart(exp) {
  // 实验开始后的 2 小时内为冷启动保护期
  const elapsedMs = Date.now() - new Date(exp.startTime).getTime();
  return elapsedMs < 120 * 60 * 1000;
}

function getColdStartStrategy(exp) {
  // 在冷启动保护期内，按 initial_weight 概率随机选择
  const strategies = exp.strategies;
  const weights = strategies.map(s => s.initialWeight);
  const total = weights.reduce((a, b) => a + b, 0);
  let r = Math.random() * total;
  for (let i = 0; i < strategies.length; i++) {
    r -= weights[i];
    if (r <= 0) return strategies[i].id;
  }
  return strategies[strategies.length - 1].id;
}

module.exports = abTestMiddleware;
```

#### 步骤 2.5: 修改推荐引擎 `backend/src/services/recommendEngine.js`

```javascript
// 在 getRecommendations 方法中增加策略路由逻辑
async function getRecommendations(userId, count, options = {}) {
  const strategyId = options.strategyId;

  if (strategyId) {
    // 从实验策略映射获取算法标识
    const strategy = abTestService.getStrategyById(strategyId);
    if (strategy) {
      // 根据 algorithm_key 路由到具体推荐算法
      return routeByAlgorithm(userId, count, strategy.algorithmKey);
    }
  }

  // 默认走当前混合推荐
  return hybridRecommend(userId, count);
}

// 算法路由表
const algorithmRouter = {
  'hybrid_v1':    (uid, cnt) => hybridRecommend(uid, cnt),
  'hybrid_v2':    (uid, cnt) => hybridV2Recommend(uid, cnt),
  'user_cf_v2':   (uid, cnt) => userCFImprovedRecommend(uid, cnt),
  'item_cf_v2':   (uid, cnt) => itemCFImprovedRecommend(uid, cnt),
  'svd_v1':       (uid, cnt) => svdRecommend(uid, cnt),
  'dl_v1':        (uid, cnt) => deepLearningRecommend(uid, cnt),
  // 预留扩展
};

function routeByAlgorithm(userId, count, algorithmKey) {
  const handler = algorithmRouter[algorithmKey];
  if (handler) return handler(userId, count);
  // Fallback
  return hybridRecommend(userId, count);
}
```

#### 步骤 2.6: 注册中间件 `backend/server.js`

```javascript
const abTestMiddleware = require('./src/middleware/abTestMiddleware');
const abTestService = require('./src/services/abTestService');

// 启动时初始化实验配置
abTestService.init().catch(err => {
  console.error('[ABTest] Init failed:', err);
});

// 注册中间件（认证之后、推荐路由之前）
app.use('/api/recommend', authMiddleware, abTestMiddleware, recommendRoutes);
```

### 3.3 验收标准

- [ ] 中间件正确解析用户 ID，计算桶号
- [ ] 同一用户多次请求返回相同策略（固定模式测试）
- [ ] Bandit 模式为新用户触发 Thompson Sampling 并记录 override
- [ ] 推荐引擎根据 strategy_id 正确路由到对应算法
- [ ] 响应头包含 `X-Experiment-Id`
- [ ] 定时刷新实验配置，新增/修改实验 1 分钟内生效

---

## 4. 阶段三：Python 离线分析模块

### 4.1 文件清单

| # | 文件路径 | 职责 | 新增/修改 |
|:-:|----------|------|:---------:|
| 3.1 | `scripts/analysis/ab_analysis.py` | 主分析模块：数据加载、指标计算、统计检验、参数更新 | **新增** |
| 3.2 | `scripts/analysis/stat_utils.py` | 统计工具函数（Z 检验、t 检验、置信区间、样本量估算） | **新增** |
| 3.3 | `scripts/analysis/run_ab_test.py` | 调度入口：遍历进行中实验，串行执行分析 | **新增** |
| 3.4 | `scripts/analysis/requirements.txt` | 依赖管理（pandas, scipy, pymysql, redis 等） | **新增** |
| 3.5 | `scripts/analysis/config.py` | 配置文件（数据库连接、Red is 连接、分析参数） | **新增** |

### 4.2 详细实现

#### 步骤 3.1: 配置文件 `scripts/analysis/config.py`

```python
import os

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': int(os.getenv('DB_PORT', 3306)),
    'database': 'MovieRecommendSystem',
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', ''),
}

REDIS_CONFIG = {
    'host': os.getenv('REDIS_HOST', 'localhost'),
    'port': int(os.getenv('REDIS_PORT', 6379)),
    'db': 0,
}

ANALYSIS_CONFIG = {
    'window_hours': 24,              # 分析窗口（小时）
    'cold_start_minutes': 120,       # 冷启动保护期（分钟）
    'significance_level': 0.05,      # 显著性水平
    'power': 0.8,                    # 统计功效
    'simulations': 10000,            # 蒙特卡洛模拟次数
    'convergence_periods': 6,        # 收敛连续周期数
    'convergence_win_prob': 0.95,    # 收敛概率阈值
    'min_experiment_hours': 24,      # 最小运行时间
    'positive_event_types': [        # 正向事件行为类型
        'click', 'rate', 'favorite', 'play'
    ],
    'positive_rating_threshold': 4,  # 评分≥4视为正向事件
}

STRATEGY_ALGORITHM_MAP = {
    1: 'hybrid_v1',
    2: 'user_cf_v2',
    3: 'dl_v1',
}
```

#### 步骤 3.2: 统计工具函数 `scripts/analysis/stat_utils.py`

```python
import math
import numpy as np
from scipy import stats

def compute_proportion_ci(success, total, z=1.96):
    """
    计算比例的 95% 置信区间（Wilson Score 区间）
    适用于小样本，比正态近似更稳健
    """
    if total == 0:
        return 0, 0
    rate = success / total
    denominator = 1 + z**2 / total
    center = (rate + z**2 / (2*total)) / denominator
    margin = z * math.sqrt((rate*(1-rate)/total + z**2/(4*total**2))) / denominator
    return max(0, center - margin), min(1, center + margin)

def two_proportion_z_test(ctrl_success, ctrl_total, test_success, test_total):
    """
    两样本比例 Z 检验
    返回: (z_stat, p_value)
    """
    if ctrl_total == 0 or test_total == 0:
        return 0, 1.0
    p1 = ctrl_success / ctrl_total
    p2 = test_success / test_total
    p_pool = (ctrl_success + test_success) / (ctrl_total + test_total)
    se = math.sqrt(p_pool * (1 - p_pool) * (1/ctrl_total + 1/test_total))
    if se == 0:
        return 0, 1.0
    z = (p2 - p1) / se
    p_value = 2 * (1 - stats.norm.cdf(abs(z)))
    return z, p_value

def mean_comparison_test(ctrl_values, test_values):
    """
    均值比较检验：
    - 两组样本量 > 30: 独立样本 t 检验
    - 否则: Mann-Whitney U 检验
    返回: (stat, p_value)
    """
    if len(ctrl_values) > 30 and len(test_values) > 30:
        stat, p_value = stats.ttest_ind(ctrl_values, test_values)
    else:
        stat, p_value = stats.mannwhitneyu(ctrl_values, test_values, alternative='two-sided')
    return stat, p_value

def minimum_sample_size(baseline_rate, min_effect=0.1, alpha=0.05, power=0.8):
    """
    估算最小样本量（每组所需用户数）
    基于比例检验的近似公式
    """
    z_alpha = stats.norm.ppf(1 - alpha/2)
    z_beta = stats.norm.ppf(power)
    p_avg = baseline_rate * (2 + min_effect) / 2
    n = ((z_alpha + z_beta)**2 * 2 * p_avg * (1 - p_avg)) / (baseline_rate * min_effect)**2
    return max(30, math.ceil(n))

def compute_win_probability(strategies_params, simulations=10000):
    """
    蒙特卡洛模拟计算各策略的获胜概率
    strategies_params: list of (strategy_id, alpha, beta)
    返回: { strategy_id: win_probability }
    """
    np.random.seed()
    wins = {sid: 0 for sid, _, _ in strategies_params}
    for _ in range(simulations):
        samples = []
        for sid, alpha, beta in strategies_params:
            theta = np.random.beta(alpha, beta)
            samples.append((sid, theta))
        winner = max(samples, key=lambda x: x[1])[0]
        wins[winner] += 1
    return {sid: count / simulations for sid, count in wins.items()}
```

#### 步骤 3.3: 主分析模块 `scripts/analysis/ab_analysis.py`

```python
#!/usr/bin/env python3
"""
A/B 测试离线分析模块
职责：加载数据 → 计算指标 → 统计检验 → 更新 Bandit 参数 → 判定收敛
"""

import pymysql
import redis
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from config import DB_CONFIG, REDIS_CONFIG, ANALYSIS_CONFIG
from stat_utils import (
    compute_proportion_ci,
    two_proportion_z_test,
    mean_comparison_test,
    minimum_sample_size,
    compute_win_probability,
)


class ABAnalyzer:
    def __init__(self):
        self.db = pymysql.connect(**DB_CONFIG)
        self.redis_client = redis.Redis(**REDIS_CONFIG)
        self.config = ANALYSIS_CONFIG

    def run_all_experiments(self):
        """遍历所有进行中的实验并执行分析"""
        experiments = self._get_active_experiments()
        for exp in experiments:
            try:
                self._analyze_experiment(exp)
            except Exception as e:
                print(f"[ABTest] Error analyzing experiment {exp['id']}: {e}")

    def _get_active_experiments(self):
        """获取所有进行中的实验及其策略"""
        with self.db.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("""
                SELECT e.id, e.name, e.split_mode, e.start_time, e.end_time,
                       s.id AS strategy_id, s.name AS strategy_name,
                       s.algorithm_key, s.initial_weight, s.min_traffic
                FROM ab_experiments e
                JOIN ab_strategies s ON s.experiment_id = e.id
                WHERE e.status = 'running'
                  AND NOW() >= e.start_time
                  AND (e.end_time IS NULL OR NOW() <= e.end_time)
            """)
            rows = cursor.fetchall()

        # 按实验 ID 分组
        experiments = {}
        for row in rows:
            eid = row['id']
            if eid not in experiments:
                experiments[eid] = {
                    'id': eid,
                    'name': row['name'],
                    'split_mode': row['split_mode'],
                    'start_time': row['start_time'],
                    'end_time': row['end_time'],
                    'strategies': [],
                }
            experiments[eid]['strategies'].append(row)
        return list(experiments.values())

    def _analyze_experiment(self, experiment):
        """对单个实验执行完整分析流程"""
        exp_id = experiment['id']
        print(f"[ABTest] Analyzing experiment {exp_id}: {experiment['name']}")

        # 计算时间窗口（最近 24 小时，排除冷启动保护期）
        window_end = datetime.now()
        window_start = window_end - timedelta(hours=self.config['window_hours'])
        cold_end = experiment['start_time'] + timedelta(minutes=self.config['cold_start_minutes'])

        if window_start < cold_end:
            window_start = cold_end
            print(f"  [ABTest] Window adjusted to cold start end: {window_start}")

        if window_start >= window_end:
            print(f"  [ABTest] Experiment still in cold start, skip")
            return

        # 加载行为数据
        behaviors = self._load_behaviors(exp_id, window_start, window_end)
        if behaviors.empty:
            print(f"  [ABTest] No behavior data in window")
            return

        # 按策略分组计算指标
        results = []
        for sid in behaviors['strategy_id'].unique():
            df_strat = behaviors[behaviors['strategy_id'] == sid]
            result = self._compute_metrics(sid, df_strat)
            results.append(result)

        # 找对照组（策略 ID 最小的作为对照）
        results.sort(key=lambda r: r['strategy_id'])
        control = results[0]

        # 统计检验：每个实验组 vs 对照组
        for result in results[1:]:
            z_stat, p_value = two_proportion_z_test(
                control['clicks'], control['impressions'],
                result['clicks'], result['impressions']
            )
            result['p_value'] = p_value
            result['is_winner'] = (p_value < self.config['significance_level']
                                   and result['ctr'] > control['ctr'])

        # 自适应参数更新（Bandit 模式）
        if experiment['split_mode'] == 'bandit':
            self._update_bandit_params(exp_id, results, behaviors)

        # 收敛判定
        converged = self._check_convergence(exp_id, results)

        # 存储结果
        self._save_results(exp_id, window_start, window_end, results, converged)

        print(f"  [ABTest] Analysis complete: {len(results)} strategies analyzed")

    def _load_behaviors(self, exp_id, window_start, window_end):
        """从 MySQL 读取行为数据"""
        query = """
            SELECT user_id, movie_id, behavior_type, rating,
                   duration_seconds, created_at, strategy_id
            FROM users_movies_behaviors
            WHERE experiment_id = %s
              AND created_at BETWEEN %s AND %s
              AND strategy_id IS NOT NULL
        """
        return pd.read_sql(query, self.db, params=(exp_id, window_start, window_end))

    def _compute_metrics(self, strategy_id, df):
        """计算单个策略的指标"""
        total_users = df['user_id'].nunique()
        impressions = len(df[df['behavior_type'] == 'exposure'])
        clicks = len(df[df['behavior_type'] == 'click'])
        favorites = len(df[df['behavior_type'] == 'favorite'])
        ratings = len(df[df['behavior_type'] == 'rate'])
        watch_durations = df[df['behavior_type'] == 'play']['duration_seconds']

        ctr = clicks / impressions if impressions > 0 else 0
        ctr_ci_lower, ctr_ci_upper = compute_proportion_ci(clicks, impressions)
        rating_rate = ratings / impressions if impressions > 0 else 0
        favorite_rate = favorites / impressions if impressions > 0 else 0
        avg_watch = watch_durations.mean() if len(watch_durations) > 0 else 0

        return {
            'strategy_id': strategy_id,
            'impressions': impressions,
            'clicks': clicks,
            'ctr': round(ctr, 6),
            'ctr_ci_lower': round(ctr_ci_lower, 6),
            'ctr_ci_upper': round(ctr_ci_upper, 6),
            'avg_watch_duration': round(avg_watch, 4),
            'rating_rate': round(rating_rate, 6),
            'favorite_rate': round(favorite_rate, 6),
            'sample_size': total_users,
            'p_value': None,
            'is_winner': False,
        }

    def _update_bandit_params(self, exp_id, results, behaviors):
        """更新 Bandit 模式的 Beta 后验参数"""
        for result in results:
            sid = result['strategy_id']
            df_s = behaviors[behaviors['strategy_id'] == sid]

            # 正向事件计数
            positive_events = 0
            for _, row in df_s.iterrows():
                bt = row['behavior_type']
                if bt in self.config['positive_event_types']:
                    if bt == 'rate' and row.get('rating', 0) < self.config['positive_rating_threshold']:
                        continue
                    positive_events += 1

            # 总曝光数（以 exposure 类型为准）
            total_exposures = len(df_s[df_s['behavior_type'] == 'exposure'])

            # Beta 参数更新：α = 1 + 正向事件，β = 1 + 总曝光 - 正向事件
            alpha = 1 + positive_events
            beta = 1 + max(0, total_exposures - positive_events)

            # 写入 Redis
            self.redis_client.set(f"ab:bandit:{exp_id}:{sid}:alpha", alpha)
            self.redis_client.set(f"ab:bandit:{exp_id}:{sid}:beta", beta)

            # 发布通知
            self.redis_client.publish("ab:bandit:update", str({
                'experiment_id': exp_id,
                'strategy_id': sid,
                'alpha': alpha,
                'beta': beta,
            }))

            print(f"  [ABTest] Bandit params updated: strategy={sid}, alpha={alpha}, beta={beta}")

    def _check_convergence(self, exp_id, results):
        """判定实验是否收敛"""
        if len(results) < 2:
            return False

        strategies_params = []
        for result in results:
            sid = result['strategy_id']
            alpha = float(self.redis_client.get(f"ab:bandit:{exp_id}:{sid}:alpha") or 1)
            beta = float(self.redis_client.get(f"ab:bandit:{exp_id}:{sid}:beta") or 1)
            strategies_params.append((sid, alpha, beta))

        win_probs = compute_win_probability(strategies_params, self.config['simulations'])

        # 记录获胜概率到结果
        for result in results:
            result['win_probability'] = round(win_probs.get(result['strategy_id'], 0), 6)

        # 寻找获胜概率最高的策略
        best_strategy = max(win_probs, key=win_probs.get)

        # 检查实验运行时长
        exp = self._get_experiment_by_id(exp_id)
        if exp:
            elapsed_hours = (datetime.now() - exp['start_time']).total_seconds() / 3600
            if elapsed_hours < self.config['min_experiment_hours']:
                print(f"  [ABTest] Experiment running {elapsed_hours:.1f}h < {self.config['min_experiment_hours']}h min, skip convergence")
                return False

        # 检查获胜概率是否连续达标（简化：仅检查当前周期）
        # 实际操作应检查 ab_results 表最近 N 条记录
        if win_probs[best_strategy] >= self.config['convergence_win_prob']:
            print(f"  [ABTest] Convergence detected! Winner: strategy {best_strategy}, prob={win_probs[best_strategy]:.4f}")
            # 通知 Node.js 终止实验
            self.redis_client.publish("ab:experiment:stop", str({
                'experiment_id': exp_id,
                'winner_strategy_id': best_strategy,
            }))
            return True

        return False

    def _save_results(self, exp_id, window_start, window_end, results, converged):
        """将分析结果写入 ab_results 表"""
        now = datetime.now()
        with self.db.cursor() as cursor:
            for r in results:
                cursor.execute("""
                    INSERT INTO ab_results
                        (experiment_id, strategy_id, analysis_time, window_start, window_end,
                         impressions, clicks, ctr, ctr_ci_lower, ctr_ci_upper,
                         avg_watch_duration, rating_rate, favorite_rate,
                         p_value, is_winner, win_probability, sample_size, is_converged)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (
                    exp_id, r['strategy_id'], now, window_start, window_end,
                    r['impressions'], r['clicks'], r['ctr'],
                    r['ctr_ci_lower'], r['ctr_ci_upper'],
                    r['avg_watch_duration'], r['rating_rate'], r['favorite_rate'],
                    r.get('p_value'), r.get('is_winner', False),
                    r.get('win_probability'), r['sample_size'], converged,
                ))
            self.db.commit()

    def _get_experiment_by_id(self, exp_id):
        with self.db.cursor(pymysql.cursors.DictCursor) as cursor:
            cursor.execute("SELECT * FROM ab_experiments WHERE id = %s", (exp_id,))
            return cursor.fetchone()


if __name__ == '__main__':
    analyzer = ABAnalyzer()
    analyzer.run_all_experiments()
```

#### 步骤 3.4: 调度入口 `scripts/analysis/run_ab_test.py`

```python
#!/usr/bin/env python3
"""
A/B 测试分析调度入口
通过 crontab 或 Celery 定期调用
"""

import sys
import os
import logging
from datetime import datetime

# 添加当前目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ab_analysis import ABAnalyzer

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f'logs/ab_analysis_{datetime.now().strftime("%Y%m%d")}.log'),
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)


def main():
    logger.info("=" * 60)
    logger.info("A/B Test Analysis Starting...")
    start = datetime.now()

    try:
        analyzer = ABAnalyzer()
        analyzer.run_all_experiments()
        elapsed = (datetime.now() - start).total_seconds()
        logger.info(f"A/B Test Analysis Complete. Elapsed: {elapsed:.2f}s")
    except Exception as e:
        logger.error(f"A/B Test Analysis Failed: {e}", exc_info=True)
        sys.exit(1)


if __name__ == '__main__':
    main()
```

#### 步骤 3.5: 依赖文件 `scripts/analysis/requirements.txt`

```
pandas>=1.3.0
numpy>=1.21.0
scipy>=1.7.0
pymysql>=1.0.2
redis>=4.0.0
python-dateutil>=2.8.0
```

### 4.3 Crontab 配置

```bash
# 每 30 分钟执行一次 A/B 测试分析
*/30 * * * * cd /path/to/MovieRecommendSystem && python scripts/analysis/run_ab_test.py >> logs/ab_analysis.log 2>&1
```

### 4.4 验收标准

- [ ] `run_ab_test.py` 能正确连接 MySQL 和 Redis
- [ ] 成功加载指定时间窗口的行为数据
- [ ] 正确计算各策略的 CTR、人均观看时长等指标
- [ ] 统计检验结果合理（p 值、置信区间）
- [ ] Bandit 参数成功写入 Redis
- [ ] 结果写入 `ab_results` 表

---

## 5. 阶段四：自适应流量决策模块

### 5.1 说明

自适应流量决策的核心逻辑已在阶段二（`abTestService.js`）和阶段三（`ab_analysis.py`）中分别实现。本阶段主要完成 **衔接与集成**：

- **Node.js 端**：Thompson Sampling、批量采样缓存、冷启动保护（已在 3.2 节实现）
- **Python 端**：后验参数更新、获胜概率计算、收敛判定（已在 4.2 节实现）
- **本阶段**：补全衔接代码，实现完整的自动推全流程

### 5.2 任务清单

| # | 任务 | 文件 | 说明 | 优先级 | 预估工时 |
|:-:|------|------|------|:------:|:--------:|
| 4.1 | 自动推全处理 | `backend/src/services/abTestService.js` | 监听 Redis `ab:experiment:stop` 消息，自动更新实验状态 | P1 | 1h |
| 4.2 | 批量采样缓存 | `backend/src/services/abTestService.js` | 实现批处理窗口缓存逻辑 | P1 | 1h |
| 4.3 | 异常保护（断崖下降检测） | `backend/src/services/abTestService.js` | 实时监控策略 CTR，异常时自动降级 | P2 | 2h |
| 4.4 | Redis 数据结构一致性 | `backend/src/services/abTestService.js` + `scripts/analysis/` | 确保 Key 命名、TTL 策略一致 | P1 | 0.5h |

### 5.3 关键代码

#### 自动推全处理（追加到 `abTestService.js`）

```javascript
// 订阅 Redis 实验终止通知
function subscribeExperimentStop() {
  const redis = getRedisClient();
  redis.subscribe('ab:experiment:stop');
  redis.on('message', async (channel, message) => {
    if (channel === 'ab:experiment:stop') {
      const { experimentId, winnerStrategyId } = JSON.parse(message);
      console.log(`[ABTest] Auto-stop experiment ${experimentId}, winner: ${winnerStrategyId}`);

      try {
        // 1. 更新 ab_experiments 状态
        await db.query(
          'UPDATE ab_experiments SET status = ? WHERE id = ?',
          ['stopped', experimentId]
        );

        // 2. 更新优胜策略权重为 100%
        await db.query(
          'UPDATE ab_strategies SET initial_weight = 1.0, weight_source = ? WHERE id = ?',
          ['adaptive', winnerStrategyId]
        );

        // 3. 刷新内存缓存
        await refreshExperiments();

        // 4. 清理用户分桶覆盖（实验结束，所有用户都使用优胜策略）
        await db.query(
          'DELETE FROM user_bucket_override WHERE experiment_id = ?',
          [experimentId]
        );

        console.log(`[ABTest] Experiment ${experimentId} auto-stopped, strategy ${winnerStrategyId} promoted`);
      } catch (err) {
        console.error(`[ABTest] Auto-stop failed for experiment ${experimentId}:`, err);
      }
    }
  });
}

// 在 init() 中调用
init() {
  await loadActiveExperiments();
  subscribeExperimentStop();  // <-- 新增
  // ... 其余初始化代码
}
```

#### 异常保护（断崖下降检测）

```javascript
// 在 abTestService.js 中实现降级检测
const ctrlThreshold = 0.5; // CTR 低于基线 50% 则触发降级

async function checkAbnormalDrop(experimentId) {
  const exp = experimentsCache.get(experimentId);
  if (!exp || exp.strategies.length < 2) return;

  // 从 ab_results 获取最近一次分析结果
  const results = await db.query(`
    SELECT strategy_id, ctr, is_winner
    FROM ab_results
    WHERE experiment_id = ? AND analysis_time = (
      SELECT MAX(analysis_time) FROM ab_results WHERE experiment_id = ?
    )
  `, [experimentId, experimentId]);

  if (results.length < 2) return;

  // 对照组 CTR
  const control = results.find(r => !r.is_winner && r.ctr !== null);
  if (!control) return;

  for (const r of results) {
    if (r.ctr !== null && control.ctr > 0 && r.ctr < control.ctr * ctrlThreshold) {
      console.warn(`[ABTest] Abnormal CTR drop detected! Experiment ${experimentId}, Strategy ${r.strategy_id}: ${r.ctr} vs baseline ${control.ctr}`);

      // 自动停止实验，回退到对照组
      const redis = getRedisClient();
      redis.publish('ab:experiment:stop', JSON.stringify({
        experimentId,
        winnerStrategyId: control.strategy_id,
        reason: 'abnormal_drop',
      }));
      return;
    }
  }
}

// 可集成到每 5 分钟的定时任务中
setInterval(async () => {
  for (const expId of experimentsCache.keys()) {
    await checkAbnormalDrop(expId);
  }
}, 5 * 60 * 1000);
```

### 5.4 验收标准

- [ ] Python 分析脚本发布 `ab:experiment:stop` 后，Node.js 端正确更新实验状态
- [ ] 批量采样缓存（10 分钟窗口）正常工作
- [ ] 断崖下降检测在模拟异常数据时正确触发降级

---

## 6. 阶段五：API 接口与管理面板

### 6.1 文件清单

| # | 文件路径 | 职责 | 新增/修改 |
|:-:|----------|------|:---------:|
| 5.1 | `backend/src/routes/adminExperimentRoutes.js` | 实验管理 API 路由定义 | **新增** |
| 5.2 | `backend/src/controllers/experimentController.js` | 实验管理控制器 | **新增** |
| 5.3 | `backend/src/services/experimentService.js` | 实验增删改查、起停、归档等业务逻辑 | **新增** |
| 5.4 | `backend/src/routes/internalRoutes.js` | 内部数据接口 | **新增** |
| 5.5 | `backend/src/controllers/internalController.js` | 内部接口控制器 | **新增** |
| 5.6 | `backend/server.js` | 注册新路由 | **修改** |
| 5.7 | `frontend/public/ab-test-admin.html` | 实验管理前端页面 | **新增** |

### 6.2 API 接口实现

#### 步骤 5.1: 实验管理路由 `backend/src/routes/adminExperimentRoutes.js`

```javascript
const express = require('express');
const router = express.Router();
const experimentController = require('../controllers/experimentController');

// 创建实验
router.post('/', experimentController.createExperiment);

// 修改实验
router.put('/:id', experimentController.updateExperiment);

// 获取所有实验
router.get('/', experimentController.getAllExperiments);

// 获取单个实验详情
router.get('/:id', experimentController.getExperimentDetail);

// 手动终止实验
router.post('/:id/stop', experimentController.stopExperiment);

// 归档实验
router.post('/:id/archive', experimentController.archiveExperiment);

// 获取实验指标（看板数据）
router.get('/:id/metrics', experimentController.getExperimentMetrics);

module.exports = router;
```

#### 步骤 5.2: 控制器 `backend/src/controllers/experimentController.js`

```javascript
const experimentService = require('../services/experimentService');

exports.createExperiment = async (req, res) => {
  try {
    const result = await experimentService.create(req.body);
    res.json({ code: 200, data: result });
  } catch (err) {
    res.status(400).json({ code: 400, message: err.message });
  }
};

exports.updateExperiment = async (req, res) => {
  try {
    const result = await experimentService.update(req.params.id, req.body);
    res.json({ code: 200, data: result });
  } catch (err) {
    res.status(400).json({ code: 400, message: err.message });
  }
};

exports.getAllExperiments = async (req, res) => {
  try {
    const filter = { status: req.query.status };
    const experiments = await experimentService.list(filter);
    res.json({ code: 200, data: experiments });
  } catch (err) {
    res.status(500).json({ code: 500, message: err.message });
  }
};

exports.getExperimentDetail = async (req, res) => {
  try {
    const detail = await experimentService.getDetail(req.params.id);
    res.json({ code: 200, data: detail });
  } catch (err) {
    res.status(404).json({ code: 404, message: err.message });
  }
};

exports.stopExperiment = async (req, res) => {
  try {
    const { action, winnerStrategyId } = req.body;
    const result = await experimentService.stop(req.params.id, action, winnerStrategyId);
    res.json({ code: 200, data: result });
  } catch (err) {
    res.status(400).json({ code: 400, message: err.message });
  }
};

exports.archiveExperiment = async (req, res) => {
  try {
    const result = await experimentService.archive(req.params.id);
    res.json({ code: 200, data: result });
  } catch (err) {
    res.status(400).json({ code: 400, message: err.message });
  }
};

exports.getExperimentMetrics = async (req, res) => {
  try {
    const metrics = await experimentService.getMetrics(req.params.id);
    res.json({ code: 200, data: metrics });
  } catch (err) {
    res.status(500).json({ code: 500, message: err.message });
  }
};
```

#### 步骤 5.3: 业务服务 `backend/src/services/experimentService.js`

```javascript
const db = require('../db');  // 假设已存在数据库连接模块

exports.create = async (data) => {
  const { name, description, splitMode, startTime, endTime, strategies } = data;

  // 1. 创建实验
  const expResult = await db.query(
    `INSERT INTO ab_experiments (name, description, split_mode, start_time, end_time, status)
     VALUES (?, ?, ?, ?, ?, 'draft')`,
    [name, description, splitMode, startTime, endTime]
  );
  const expId = expResult.insertId;

  // 2. 创建策略
  for (const s of strategies) {
    await db.query(
      `INSERT INTO ab_strategies (experiment_id, name, algorithm_key, initial_weight, min_traffic)
       VALUES (?, ?, ?, ?, ?)`,
      [expId, s.name, s.algorithmKey, s.initialWeight, s.minTraffic || 0.05]
    );
  }

  // 3. 通知服务刷新缓存
  const redis = require('../redis/client').getRedisClient();
  redis.publish('ab:experiment:update', JSON.stringify({ experimentId: expId }));

  return { id: expId, status: 'draft' };
};

exports.list = async (filter = {}) => {
  let query = 'SELECT * FROM ab_experiments';
  const params = [];

  if (filter.status) {
    query += ' WHERE status = ?';
    params.push(filter.status);
  }
  query += ' ORDER BY created_at DESC';

  return await db.query(query, params);
};

exports.getDetail = async (id) => {
  const [experiment] = await db.query(
    'SELECT * FROM ab_experiments WHERE id = ?', [id]
  );
  if (!experiment) throw new Error('Experiment not found');

  // 获取策略列表及其最新指标
  const strategies = await db.query(
    `SELECT s.*, r.ctr, r.ctr_ci_lower, r.ctr_ci_upper,
            r.avg_watch_duration, r.win_probability, r.sample_size, r.is_winner
     FROM ab_strategies s
     LEFT JOIN ab_results r ON r.strategy_id = s.id
       AND r.analysis_time = (SELECT MAX(analysis_time) FROM ab_results WHERE strategy_id = s.id)
     WHERE s.experiment_id = ?
     ORDER BY s.id`,
    [id]
  );

  return { ...experiment, strategies };
};

exports.stop = async (id, action = 'keep', winnerStrategyId = null) => {
  const [experiment] = await db.query(
    'SELECT * FROM ab_experiments WHERE id = ?', [id]
  );
  if (!experiment) throw new Error('Experiment not found');
  if (experiment.status !== 'running') throw new Error('Experiment is not running');

  if (action === 'promote' && winnerStrategyId) {
    // 推全优胜策略
    await db.query('UPDATE ab_strategies SET initial_weight = 1.0, weight_source = ? WHERE id = ?',
      ['manual', winnerStrategyId]);
  } else if (action === 'rollback') {
    // 回退到默认（对照组）
    const [ctrl] = await db.query(
      'SELECT id FROM ab_strategies WHERE experiment_id = ? ORDER BY id LIMIT 1', [id]
    );
    if (ctrl) {
      await db.query('UPDATE ab_strategies SET initial_weight = 1.0, weight_source = ? WHERE id = ?',
        ['manual', ctrl.id]);
    }
  }

  await db.query('UPDATE ab_experiments SET status = ? WHERE id = ?', ['stopped', id]);

  // 通知刷新
  const redis = require('../redis/client').getRedisClient();
  redis.publish('ab:experiment:update', JSON.stringify({ experimentId: id }));

  return { id, status: 'stopped', promotedStrategyId: winnerStrategyId };
};

exports.archive = async (id) => {
  await db.query('UPDATE ab_experiments SET status = ? WHERE id = ?', ['archived', id]);
  return { id, status: 'archived' };
};

exports.getMetrics = async (id) => {
  // 获取趋势数据：最近 48 小时内每 30 分钟一个数据点
  const metrics = await db.query(`
    SELECT r.strategy_id, s.name, r.analysis_time, r.ctr, r.avg_watch_duration,
           r.rating_rate, r.favorite_rate, r.win_probability, r.sample_size
    FROM ab_results r
    JOIN ab_strategies s ON s.id = r.strategy_id
    WHERE r.experiment_id = ?
      AND r.analysis_time >= DATE_SUB(NOW(), INTERVAL 48 HOUR)
    ORDER BY r.analysis_time`, [id]
  );

  // 按策略分组
  const grouped = {};
  for (const row of metrics) {
    if (!grouped[row.strategy_id]) {
      grouped[row.strategy_id] = {
        strategyId: row.strategy_id,
        name: row.name,
        currentMetrics: {},
        trendData: [],
      };
    }
    grouped[row.strategy_id].trendData.push({
      time: row.analysis_time,
      ctr: row.ctr,
      avgWatchDuration: row.avg_watch_duration,
    });
  }

  // 取最新指标作为当前指标
  for (const sid of Object.keys(grouped)) {
    const last = await db.query(`
      SELECT ctr, ctr_ci_lower, ctr_ci_upper, avg_watch_duration,
             rating_rate, favorite_rate, win_probability, sample_size
      FROM ab_results
      WHERE strategy_id = ?
      ORDER BY analysis_time DESC LIMIT 1`, [sid]
    );
    if (last.length > 0) {
      grouped[sid].currentMetrics = last[0];
    }
  }

  return { experimentId: id, strategies: Object.values(grouped) };
};
```

#### 步骤 5.4: 注册路由到 `server.js`

```javascript
const adminExperimentRoutes = require('./src/routes/adminExperimentRoutes');
const internalRoutes = require('./src/routes/internalRoutes');

// 实验管理 API（需管理员权限）
app.use('/api/admin/experiments', authMiddleware, adminRoleMiddleware, adminExperimentRoutes);

// 内部数据接口（无认证或使用内部 Token）
app.use('/api/internal', internalApiTokenMiddleware, internalRoutes);
```

### 6.3 前端管理面板

提供简单的 HTML 管理页面，位于 `frontend/public/ab-test-admin.html`，包含：

- 实验列表展示（表格）
- 创建实验表单
- 实验详情与指标看板（Chart.js 趋势图）
- 手动终止/归档操作按钮

### 6.4 验收标准

- [ ] 创建实验 API 正确写入 4 张表
- [ ] 获取实验详情 API 返回各策略实时指标
- [ ] 手动终止实验 API 支持 promote / rollback / keep 三种模式
- [ ] 内部数据接口返回正确分页数据
- [ ] 指标看板 API 返回趋势数据

---

## 7. 阶段六：测试与上线

### 7.1 测试计划

| 阶段 | 测试内容 | 测试环境 | 流量 |
|:----:|----------|----------|:----:|
| **单元测试** | 中间件分桶一致性、Thompson Sampling 正确性 | 本地 | — |
| **集成测试** | 全链路：配置→路由→埋点→分析→调权 | 测试服务器 | 1% |
| **灰度测试** | 内部推荐算法对比实验 | 生产 | 1%→5% |
| **全量上线** | 多实验并行运行 | 生产 | 按需 |

### 7.2 测试用例清单

| # | 测试用例 | 预期结果 | 所属阶段 |
|:-:|----------|----------|:--------:|
| T1 | 同一用户请求多次，固定模式返回相同策略 | 策略 ID 一致 | 单元测试 |
| T2 | 未登录用户（设备指纹）正常分桶 | 返回策略，无报错 | 单元测试 |
| T3 | 创建实验后 1 分钟内，新请求命中新实验策略 | 缓存刷新生效 | 集成测试 |
| T4 | 行为表写入携带正确的 experiment_id 和 strategy_id | 数据库记录正确 | 集成测试 |
| T5 | Python 分析脚本运行后，Redis 中 Bandit 参数更新 | 参数值 > 1 | 集成测试 |
| T6 | Bandit 模式下，新用户策略分配不重复 | 覆盖记录正确 | 集成测试 |
| T7 | 收敛判定满足条件后，实验自动停止并推全 | 状态更新、权重更新 | 集成测试 |
| T8 | 断崖下降模拟（CTR 低于基线 50%），自动降级 | 实验停止、回退对照组 | 灰度测试 |
| T9 | 冷启动保护期内，新策略不参与 Bandit 调整 | 权重保持不变 | 单元测试 |
| T10 | 多实验并行互不干扰 | 各自的实验 ID、策略 ID 路由正确 | 灰度测试 |

### 7.3 监控指标

| 指标 | 采集方式 | 告警阈值 |
|------|----------|----------|
| 中间件处理耗时 | Node.js 日志（P99） | > 50ms |
| Redis 连接数 | `INFO clients` | > 100 |
| Python 分析耗时 | 日志（每次运行） | > 300s |
| 实验配置刷新延迟 | `updated_at` 与当前时间差 | > 120s |
| 行为表埋点丢失率 | `experiment_id IS NULL` 比例 | > 5% |

### 7.4 回滚方案

```bash
# 方案一：关闭中间件（最快）
# 在 server.js 中注释中间件注册，重启服务
# app.use('/api/recommend', authMiddleware, abTestMiddleware, recommendRoutes);
# => app.use('/api/recommend', authMiddleware, recommendRoutes);

# 方案二：停止所有实验（不重启服务）
mysql -e "UPDATE ab_experiments SET status = 'stopped' WHERE status = 'running'"

# 方案三：回滚数据库
mysql < database/rollback_ab_testing.sql
```

### 7.5 上线检查清单

- [ ] 所有 4 张新表已创建，字段类型、外键、索引正确
- [ ] `users_movies_behaviors` 扩展字段已添加
- [ ] Redis 服务运行正常
- [ ] Node.js 启动日志无错误
- [ ] Python 脚本首次运行成功
- [ ] 监控告警已配置
- [ ] 回滚方案已就绪
- [ ] 实验管理 API 可访问

---

## 8. 任务依赖图

```
P1 (数据库层)
  │
  ├──→ P2 (后端中间件) ──→ P4 (自适应决策)
  │         │
  │         └──→ P5 (API接口与管理面板)
  │
  └──→ P3 (Python分析模块) ──→ P4 (自适应决策)
                                  │
                                  └──→ P6 (测试上线)
```

**关键路径**（最长依赖链）：P1 → P2/P3 → P4 → P6 = **8-10 天**

**可并行任务**：
- P2 (后端) ↔ P3 (Python)：完全并行开发
- P5 (管理面板) 与 P4 (自适应)：部分并行

---

## 9. 工作量估算汇总

| 阶段 | 任务数 | 文件数 | 预估工时 | 依赖 |
|:----:|:------:|:------:|:--------:|:----:|
| P1 数据库层 | 6 | 1 (`init.sql` 修改) | 3h | 无 |
| P2 后端中间件 | 6 | 7 文件 | 16h | P1 |
| P3 Python 分析模块 | 5 | 5 文件 | 14h | P1 |
| P4 自适应决策 | 4 | 2 文件 (追加代码) | 5h | P2 + P3 |
| P5 API 与管理面板 | 7 | 7 文件 | 14h | P2 |
| P6 测试与上线 | — | — | 10h | P1~P5 |
| **合计** | **28** | **~22 文件** | **~62h** | |

**按人天估算**（1 人天 = 6 有效工时）：
- 单人实施：≈ 10 个工作日（2 周）
- 两人并行：≈ 6 个工作日（1 周 + 集成测试）

---

## 10. 实施风险与应对策略

| 风险 | 影响 | 概率 | 应对策略 |
|------|:----:|:----:|----------|
| 数据库迁移导致服务中断 | 高 | 低 | 使用在线 DDL 工具（pt-osc），在低峰期执行 |
| Redis 宕机导致自适应分流不可用 | 中 | 低 | 降级为固定比例模式，服务不中断 |
| 统计检验误判（假阳性率升高） | 中 | 中 | 多重比较校正（Bonferroni），降低单次显著性水平 |
| Bandit 算法初期探索不足 | 中 | 中 | 冷启动保护期 + 最小流量下限 |
| 埋点丢失导致分析不准确 | 高 | 低 | 服务端自动写入曝光，减少前端依赖 |
| 多实验并行导致性能下降 | 低 | 中 | 每个实验使用独立 Map，O(1) 查找；压测后设定实验数量上限 |
| Python 分析脚本运行超时 | 中 | 低 | 设置超时中断，分页读取数据，避免大结果集 |

---

> **文档版本记录**
>
> | 版本 | 日期 | 修改内容 |
> |------|------|----------|
> | 1.0 | 2026-05-18 | 初稿，完整实施路线图 |

---

**关联文档：**
- 设计总纲：`docs/design/在线自适应AB测试框架设计.md`
- 算法规划：`docs/design/算法规划.md`