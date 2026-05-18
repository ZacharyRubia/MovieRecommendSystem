/**
 * abTestService.js - A/B 测试核心服务
 * 
 * 功能职责：
 * 1. 实验/策略配置加载与缓存（每分钟增量刷新）
 * 2. 用户分桶算法（MD5 哈希 + mod 100）
 * 3. 固定比例分流映射构建
 * 4. Thompson Sampling 自适应决策
 * 5. Redis 后验参数读写
 * 6. 策略匹配与结果封装
 * 
 * 数据流：
 *   MySQL → abTestService.cache (每60s刷新) → 各接口调用
 *   Redis  → Thompson Sampling 参数（由 Python 脚本写入）
 */

const crypto = require('crypto');
const db = require('../config/db');

// ============================================
// 配置常量
// ============================================
const EXPERIMENT_REFRESH_INTERVAL = 60000; // 60秒刷新一次实验配置
const BUCKET_TOTAL = 100;                  // 总桶数 0-99
const REDIS_KEY_BANDIT_PREFIX = 'ab:bandit:';
const REDIS_KEY_EXPERIMENT_PREFIX = 'ab:exp:';
const BANDIT_UPDATE_INTERVAL = 600000;     // Bandit 参数本地缓存保留 10 分钟

// ============================================
// 内存缓存
// ============================================
let _experimentsCache = [];                // 所有进行中的实验（含策略）
let _bucketMappings = {};                  // { experimentId: { bucketFrom: strategyId, ... } }
let _lastRefreshTime = 0;
let _banditParamsCache = {};               // { experimentId: { strategyId: { alpha, beta } } }
let _banditCacheTimestamps = {};

// ============================================
// MD5 用户分桶
// ============================================

/**
 * 根据 user_id 计算桶号（0 ~ BUCKET_TOTAL-1）
 * 算法：MD5(user_id) → hex → 取前8位 → parseInt → mod 100
 * 保证同一用户始终落入同一桶
 */
function computeBucket(userId) {
  const hash = crypto.createHash('md5').update(String(userId)).digest('hex');
  const prefix = parseInt(hash.substring(0, 8), 16);
  return prefix % BUCKET_TOTAL;
}

// ============================================
// 实验配置加载
// ============================================

/**
 * 从 MySQL 加载所有进行中的实验及其策略
 */
async function loadExperimentsFromDB() {
  const experiments = await db.query(`
    SELECT 
      e.id AS experiment_id,
      e.name AS experiment_name,
      e.status,
      e.split_mode,
      e.start_time,
      e.end_time,
      s.id AS strategy_id,
      s.name AS strategy_name,
      s.algorithm,
      s.traffic_percentage,
      s.weight_source,
      s.bandit_alpha,
      s.bandit_beta,
      s.is_control,
      s.min_traffic,
      s.coldstart_end_time
    FROM ab_experiments e
    INNER JOIN ab_strategies s ON s.experiment_id = e.id
    WHERE e.status = 'running'
      AND (e.start_time IS NULL OR e.start_time <= NOW())
      AND (e.end_time IS NULL OR e.end_time > NOW())
    ORDER BY e.id, s.id
  `);

  // 按实验分组
  const expMap = {};
  for (const row of experiments) {
    if (!expMap[row.experiment_id]) {
      expMap[row.experiment_id] = {
        id: row.experiment_id,
        name: row.experiment_name,
        status: row.status,
        splitMode: row.split_mode,
        startTime: row.start_time,
        endTime: row.end_time,
        strategies: []
      };
    }
    expMap[row.experiment_id].strategies.push({
      id: row.strategy_id,
      name: row.strategy_name,
      algorithm: row.algorithm,
      trafficPercentage: parseFloat(row.traffic_percentage),
      weightSource: row.weight_source,
      alpha: parseFloat(row.bandit_alpha),
      beta: parseFloat(row.bandit_beta),
      isControl: row.is_control === 1,
      minTraffic: parseFloat(row.min_traffic),
      coldstartEndTime: row.coldstart_end_time
    });
  }

  return Object.values(expMap);
}

/**
 * 构建固定比例分桶映射
 * 根据各策略的 trafficPercentage 分配连续桶号区间
 */
function buildBucketMappings(experiments) {
  const mappings = {};
  for (const exp of experiments) {
    if (exp.splitMode === 'bandit') continue; // Bandit 模式不走固定映射

    const expMap = {};
    let start = 0;
    for (const strat of exp.strategies) {
      const end = start + Math.round(strat.trafficPercentage) - 1;
      for (let bucket = start; bucket <= end && bucket < BUCKET_TOTAL; bucket++) {
        expMap[bucket] = strat.id;
      }
      start = end + 1;
    }
    mappings[exp.id] = expMap;
  }
  return mappings;
}

/**
 * 刷新实验缓存（每分钟增量更新）
 */
async function refreshExperimentCache() {
  try {
    const experiments = await loadExperimentsFromDB();
    _experimentsCache = experiments;
    _bucketMappings = buildBucketMappings(experiments);
    _lastRefreshTime = Date.now();
    console.log(`[ABTest] 实验缓存刷新完成: ${experiments.length} 个进行中实验`);
  } catch (err) {
    console.error('[ABTest] 实验缓存刷新失败:', err.message);
  }
}

/**
 * 启动定时刷新任务
 */
function startCacheRefresh() {
  refreshExperimentCache();
  setInterval(refreshExperimentCache, EXPERIMENT_REFRESH_INTERVAL);
  console.log(`[ABTest] 实验缓存定时刷新已启动 (间隔=${EXPERIMENT_REFRESH_INTERVAL / 1000}s)`);
}

// ============================================
// Thompson Sampling
// ============================================

/**
 * 从 Redis 读取 Bandit 参数（带本地缓存降级）
 * 优先读取 Redis 中 Python 脚本写入的最新 α/β
 */
async function getBanditParamsFromRedis(experimentId) {
  // 检查本地缓存是否仍然有效
  if (_banditParamsCache[experimentId] &&
      _banditCacheTimestamps[experimentId] &&
      (Date.now() - _banditCacheTimestamps[experimentId]) < BANDIT_UPDATE_INTERVAL) {
    return _banditParamsCache[experimentId];
  }

  try {
    const redis = require('../config/redis');
    const raw = await redis.get(`${REDIS_KEY_BANDIT_PREFIX}${experimentId}`);
    if (raw) {
      const parsed = JSON.parse(raw);
      _banditParamsCache[experimentId] = parsed;
      _banditCacheTimestamps[experimentId] = Date.now();
      return parsed;
    }
  } catch (err) {
    console.warn(`[ABTest] Redis读取Bandit参数失败(experimentId=${experimentId}):`, err.message);
  }

  // Redis 不可用或数据不存在 → 使用 MySQL 中的默认参数
  const exp = _experimentsCache.find(e => e.id === experimentId);
  if (!exp) return null;

  const params = {};
  for (const strat of exp.strategies) {
    params[strat.id] = { alpha: strat.alpha, beta: strat.beta };
  }
  return params;
}

/**
 * Thompson Sampling 采样
 * 对每个策略从 Beta(alpha, beta) 分布采样，选择最大值对应的策略
 * 
 * @param {Object} params - { strategyId: { alpha, beta } }
 * @returns {number} 选中的 strategyId
 */
function thompsonSample(params) {
  let bestStrategyId = null;
  let bestSample = -Infinity;

  for (const [strategyId, { alpha, beta }] of Object.entries(params)) {
    // Beta 分布采样
    const sample = randomBeta(alpha, beta);
    if (sample > bestSample) {
      bestSample = sample;
      bestStrategyId = parseInt(strategyId);
    }
  }

  return bestStrategyId;
}

/**
 * 生成 Beta 分布随机数（使用 JavaScript 的 rejection sampling）
 * Beta(α, β) 使用 Cheng 1978 的 BB 算法
 */
function randomBeta(alpha, beta) {
  // 对于 α=1, β=1 退化为均匀分布
  if (alpha === 1 && beta === 1) {
    return Math.random();
  }

  // 使用标准的 Beta 生成方法
  const a = alpha;
  const b = beta;

  // 当 α,β 都很小时改用近似
  if (a < 1 && b < 1) {
    // 使用反函数法
    const u = Math.random();
    const v = Math.random();
    const x = Math.pow(u, 1 / a);
    const y = Math.pow(v, 1 / b);
    const sum = x + y;
    if (sum <= 0) return 0;
    return x / sum;
  }

  // 使用 Johnk 方法（适用于 α,β >= 1）
  let x, y;
  do {
    x = Math.pow(Math.random(), 1 / a);
    y = Math.pow(Math.random(), 1 / b);
  } while (x + y > 1);
  return x / (x + y);
}

// ============================================
// 策略匹配逻辑
// ============================================

/**
 * 为用户解析命中实验的策略
 * 
 * @param {number|string} userId - 用户ID
 * @returns {Array<{ experimentId, strategyId, algorithm, isBandit }>}
 */
async function resolveStrategy(userId) {
  if (!_experimentsCache.length) return [];

  const bucket = computeBucket(userId);
  const results = [];

  for (const exp of _experimentsCache) {
    // 检查该用户在该实验中是否有桶覆盖记录（Bandit 模式已有分配）
    const override = await findBucketOverride(userId, exp.id);
    if (override) {
      const strat = exp.strategies.find(s => s.id === override.strategy_id);
      if (strat) {
        results.push({
          experimentId: exp.id,
          experimentName: exp.name,
          strategyId: strat.id,
          strategyName: strat.name,
          algorithm: strat.algorithm,
          isBandit: exp.splitMode === 'bandit'
        });
        continue;
      }
    }

    if (exp.splitMode === 'bandit') {
      // Bandit 模式 — 使用 Thompson Sampling 为新用户分配策略
      const params = await getBanditParamsFromRedis(exp.id);
      if (!params) continue;

      const selectedStrategyId = thompsonSample(params);
      const selectedStrat = exp.strategies.find(s => s.id === selectedStrategyId);
      if (!selectedStrat) continue;

      // 检查冷启动保护
      if (selectedStrat.coldstartEndTime && new Date(selectedStrat.coldstartEndTime) > new Date()) {
        // 冷启动期内：使用最小流量随机分配
        const minTrafficRatio = selectedStrat.minTraffic / 100;
        if (Math.random() > minTrafficRatio) continue;
      }

      // 记录桶覆盖（异步）
      recordBucketOverride(userId, exp.id, selectedStrategyId, bucket).catch(err =>
        console.warn('[ABTest] 记录桶覆盖失败:', err.message)
      );

      results.push({
        experimentId: exp.id,
        experimentName: exp.name,
        strategyId: selectedStrat.id,
        strategyName: selectedStrat.name,
        algorithm: selectedStrat.algorithm,
        isBandit: true
      });
    } else {
      // 固定比例模式 — 按桶号查映射
      const expMap = _bucketMappings[exp.id];
      if (!expMap) continue;

      const strategyId = expMap[bucket];
      if (!strategyId) continue;

      const strat = exp.strategies.find(s => s.id === strategyId);
      if (!strat) continue;

      results.push({
        experimentId: exp.id,
        experimentName: exp.name,
        strategyId: strat.id,
        strategyName: strat.name,
        algorithm: strat.algorithm,
        isBandit: false
      });
    }
  }

  return results;
}

// ============================================
// 桶覆盖记录（数据库持久化）
// ============================================

/**
 * 查询用户在某实验中的桶覆盖记录
 */
async function findBucketOverride(userId, experimentId) {
  try {
    const rows = await db.query(
      'SELECT strategy_id, bucket_id FROM user_bucket_override WHERE user_id = ? AND experiment_id = ? LIMIT 1',
      [userId, experimentId]
    );
    return rows.length > 0 ? rows[0] : null;
  } catch (err) {
    console.warn('[ABTest] 查询桶覆盖失败:', err.message);
    return null;
  }
}

/**
 * 记录用户的桶覆盖（REPLACE INTO 确保幂等）
 */
async function recordBucketOverride(userId, experimentId, strategyId, bucketId) {
  await db.query(
    `REPLACE INTO user_bucket_override (user_id, experiment_id, strategy_id, bucket_id, assigned_at)
     VALUES (?, ?, ?, ?, NOW())`,
    [userId, experimentId, strategyId, bucketId]
  );
}

// ============================================
// 初始化
// ============================================

// 模块加载时立即启动缓存刷新
startCacheRefresh();

// ============================================
// 导出
// ============================================
module.exports = {
  computeBucket,
  resolveStrategy,
  refreshExperimentCache,
  getBanditParamsFromRedis,
  thompsonSample,
  startCacheRefresh,
  // 测试用
  _getExperimentsCache: () => _experimentsCache,
  _getBucketMappings: () => _bucketMappings
};