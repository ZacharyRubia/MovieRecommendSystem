/**
 * abAnalyzer.js - A/B 测试离线分析引擎
 *
 * 功能：
 * 1. 定时从 users_movies_behaviors 拉取实验数据
 * 2. 按策略计算核心指标（曝光、CTR、平均评分、用户数等）
 * 3. 统计检验：Z-test（比例差）、Chi-square、置信区间（Wilson Score）
 * 4. 贝叶斯获胜概率（Beta 后验 Monte Carlo）
 * 5. 收敛判定：p<0.05 + 样本充足 或 获胜概率>95%
 * 6. 写入 ab_results 表，Bandit 模式更新 ab_strategies.alpha/β
 *
 * 运行方式：在 server.js 调用 startAnalysisLoop() 启动定时任务
 */

const { query } = require('../config/db');

const ANALYSIS_INTERVAL = 5 * 60 * 1000; // 5 分钟
const MIN_SAMPLE_SIZE = 50;              // 每组最少样本量
const SIGNIFICANCE_LEVEL = 0.05;         // 显著性水平
const MONTE_CARLO_SAMPLES = 10000;       // 贝叶斯模拟次数
const LOOKBACK_HOURS = 24;               // 分析窗口

// ============================================
// 数学工具函数
// ============================================

function normalCDF(x) {
  const a1 = 0.254829592;
  const a2 = -0.284496736;
  const a3 = 1.421413741;
  const a4 = -1.453152027;
  const a5 = 1.061405429;
  const p = 0.3275911;
  const sign = x < 0 ? -1 : 1;
  x = Math.abs(x) / Math.sqrt(2);
  const t = 1 / (1 + p * x);
  const y = 1 - (((((a5 * t + a4) * t) + a3) * t + a2) * t + a1) * t * Math.exp(-x * x);
  return 0.5 * (1 + sign * y);
}

function zTestTwoProportions(x1, n1, x2, n2) {
  if (n1 < 2 || n2 < 2) return { z: null, pValue: null };

  const p1 = x1 / n1;
  const p2 = x2 / n2;
  const pPooled = (x1 + x2) / (n1 + n2);
  const se = Math.sqrt(pPooled * (1 - pPooled) * (1 / n1 + 1 / n2));

  if (se < 1e-12) return { z: 0, pValue: 1 };

  const z = (p1 - p2) / se;
  const pValue = 2 * (1 - normalCDF(Math.abs(z)));
  return { z, pValue };
}

function chiSquareTest(x1, n1, x2, n2) {
  if (n1 < 2 || n2 < 2) return { chi2: null, pValue: null };

  const a = x1;
  const b = n1 - x1;
  const c = x2;
  const d = n2 - x2;
  const total = a + b + c + d;
  if (total === 0) return { chi2: null, pValue: null };

  const expA = (a + b) * (a + c) / total;
  const expB = (a + b) * (b + d) / total;
  const expC = (c + d) * (a + c) / total;
  const expD = (c + d) * (b + d) / total;

  const chi2 =
    (expA > 0 ? (a - expA) ** 2 / expA : 0) +
    (expB > 0 ? (b - expB) ** 2 / expB : 0) +
    (expC > 0 ? (c - expC) ** 2 / expC : 0) +
    (expD > 0 ? (d - expD) ** 2 / expD : 0);

  const df = 1;
  const pValue = chiSquarePValue(chi2, df);
  return { chi2, pValue };
}

function chiSquarePValue(chi2, df) {
  if (chi2 <= 0) return 1;
  const m = df / 2;
  let sum = 0, term = 1;
  for (let k = 0; k < 50; k++) {
    if (term < 1e-15) break;
    sum += term;
    term *= (chi2 / 2) / (m + k);
  }
  const gamma = m === 0.5 ? Math.sqrt(Math.PI) : 1;
  return 1 - sum * Math.exp(-chi2 / 2) * Math.pow(chi2 / 2, m) / gamma;
}

function wilsonScoreCI(successes, trials, z = 1.96) {
  if (trials === 0) return { lower: 0, upper: 0 };
  const p = successes / trials;
  const denominator = 1 + z * z / trials;
  const center = (p + z * z / (2 * trials)) / denominator;
  const margin = (z / denominator) * Math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials));
  return {
    lower: Math.max(0, center - margin),
    upper: Math.min(1, center + margin)
  };
}

// ============================================
// Beta 分布 & 贝叶斯获胜概率
// ============================================

function logBeta(a, b) {
  return logGamma(a) + logGamma(b) - logGamma(a + b);
}

function logGamma(x) {
  if (x < 0.5) {
    return Math.log(Math.PI) - Math.log(Math.sin(Math.PI * x)) - logGamma(1 - x);
  }
  x -= 1;
  const c = [
    0.99999999999980993, 676.5203681218851, -1259.1392167224028,
    771.32342877765313, -176.61502916214059, 12.507343278686905,
    -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7
  ];
  let sum = c[0];
  for (let i = 1; i < c.length; i++) sum += c[i] / (x + i);
  const t = x + c.length - 1.5;
  return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(sum);
}

function gammaSample(shape) {
  const a = Math.max(shape, 0.01);
  if (a < 1) {
    let iter = 0;
    const b = (Math.E + a) / Math.E;
    while (iter < 100) {
      iter++;
      const u = Math.random();
      if (u <= 1 / b) {
        const x = Math.pow(u * b, 1 / a);
        if (Math.random() <= Math.exp(-x)) return x;
      } else {
        const x = -Math.log((b - u * b) / a);
        if (Math.random() <= Math.pow(x, a - 1)) return x;
      }
    }
    return a;
  }
  const d = a - 1 / 3;
  const c = 1 / Math.sqrt(9 * d);
  let iter = 0;
  while (iter < 100) {
    iter++;
    let x, v;
    do {
      x = normalRand();
      v = 1 + c * x;
    } while (v <= 0);
    v = v * v * v;
    const u = Math.random();
    if (u < 1 - 0.0331 * x * x * x * x) return d * v;
    if (Math.log(u) < 0.5 * x * x + d * (1 - v + Math.log(v))) return d * v;
  }
  return d;
}

function normalRand() {
  let u1, u2;
  do { u1 = Math.random(); } while (u1 === 0);
  u2 = Math.random();
  return Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
}

function sampleBetaSafe(a, b) {
  const alpha = Math.max(Number.isFinite(a) ? a : 1, 0.001);
  const beta = Math.max(Number.isFinite(b) ? b : 1, 0.001);
  const g1 = gammaSample(alpha);
  const g2 = gammaSample(beta);
  const sum = g1 + g2;
  return sum > 1e-12 ? g1 / sum : 0.5;
}

function bayesianWinProbability(alpha1, beta1, alpha2, beta2) {
  let wins = 0;
  for (let i = 0; i < MONTE_CARLO_SAMPLES; i++) {
    const s1 = sampleBetaSafe(alpha1, beta1);
    const s2 = sampleBetaSafe(alpha2, beta2);
    if (s1 > s2) wins++;
  }
  return wins / MONTE_CARLO_SAMPLES;
}

// ============================================
// 数据查询
// ============================================

async function getExperimentMetrics(experimentId, strategyId, lookbackHours) {
  const rows = await query(
    `SELECT
       COUNT(*) AS total_exposures,
       SUM(CASE WHEN behavior_type IN ('view', 'like', 'collect') OR (behavior_type = 'rate' AND rating >= 4) THEN 1 ELSE 0 END) AS total_clicks,
       SUM(CASE WHEN behavior_type = 'rate' THEN 1 ELSE 0 END) AS total_ratings,
       SUM(CASE WHEN behavior_type = 'collect' THEN 1 ELSE 0 END) AS total_collects,
       COALESCE(SUM(CASE WHEN behavior_type = 'view' THEN progress_seconds ELSE 0 END), 0) AS total_watch_seconds,
       COUNT(DISTINCT user_id) AS unique_users,
       SUM(CASE WHEN behavior_type IN ('view', 'like', 'collect') OR (behavior_type = 'rate' AND rating >= 4) THEN 1 ELSE 0 END) AS positive_events
     FROM users_movies_behaviors
     WHERE experiment_id = ? AND strategy_id = ?
       AND created_at >= DATE_SUB(NOW(), INTERVAL ? HOUR)`,
    [experimentId, strategyId, lookbackHours]
  );
  const r = rows[0];
  const totalExposures = parseInt(r.total_exposures) || 0;
  const totalClicks = parseInt(r.total_clicks) || 0;
  return {
    total_exposures: totalExposures,
    total_clicks: totalClicks,
    total_ratings: parseInt(r.total_ratings) || 0,
    total_collects: parseInt(r.total_collects) || 0,
    total_watch_seconds: parseFloat(r.total_watch_seconds) || 0,
    unique_users: parseInt(r.unique_users) || 0,
    positive_events: parseInt(r.positive_events) || 0,
    ctr: totalExposures > 0 ? totalClicks / totalExposures : null,
    avg_watch_seconds: totalExposures > 0 ? parseFloat(r.total_watch_seconds) / totalExposures : null,
    rating_rate: totalExposures > 0 ? (parseInt(r.total_ratings) || 0) / totalExposures : null,
    collect_rate: totalExposures > 0 ? (parseInt(r.total_collects) || 0) / totalExposures : null
  };
}

// ============================================
// 核心分析函数
// ============================================

async function analyzeExperiment(exp) {
  console.log(`[AB Analyzer] 分析实验 #${exp.id}: ${exp.name}`);

  const strategies = await query(
    'SELECT * FROM ab_strategies WHERE experiment_id = ? ORDER BY id', [exp.id]
  );
  if (strategies.length < 2) return null;

  const control = strategies.find(s => s.is_control === 1) || strategies[0];
  const treatments = strategies.filter(s => s.id !== control.id);

  const allMetrics = {};

  for (const strat of strategies) {
    const metrics = await getExperimentMetrics(exp.id, strat.id, LOOKBACK_HOURS);
    allMetrics[strat.id] = metrics;
  }

  const controlMetrics = allMetrics[control.id];

  for (const strat of strategies) {
    const metrics = allMetrics[strat.id];
    const ci = wilsonScoreCI(metrics.total_clicks, metrics.total_exposures);

    let pValue = null;
    let zScore = null;
    let chi2 = null;
    let isWinner = 0;
    let isConverged = 0;
    let sampleSizeSufficient = 0;
    let winProbability = null;

    if (strat.id !== control.id && controlMetrics.total_exposures > 0) {
      const zResult = zTestTwoProportions(
        metrics.total_clicks, metrics.total_exposures,
        controlMetrics.total_clicks, controlMetrics.total_exposures
      );
      zScore = zResult.z;
      pValue = zResult.pValue;

      const chiResult = chiSquareTest(
        metrics.total_clicks, metrics.total_exposures,
        controlMetrics.total_clicks, controlMetrics.total_exposures
      );
      chi2 = chiResult.chi2;

      const alpha1 = metrics.positive_events + 1;
      const beta1 = Math.max(metrics.total_exposures - metrics.positive_events, 0) + 1;
      const alpha2 = controlMetrics.positive_events + 1;
      const beta2 = Math.max(controlMetrics.total_exposures - controlMetrics.positive_events, 0) + 1;

      winProbability = bayesianWinProbability(alpha1, beta1, alpha2, beta2);

      sampleSizeSufficient =
        metrics.total_exposures >= MIN_SAMPLE_SIZE &&
        controlMetrics.total_exposures >= MIN_SAMPLE_SIZE ? 1 : 0;

      if (sampleSizeSufficient && pValue !== null && pValue < SIGNIFICANCE_LEVEL && metrics.ctr > controlMetrics.ctr) {
        isWinner = 1;
      }

      if (sampleSizeSufficient && (
        (pValue !== null && pValue < SIGNIFICANCE_LEVEL) ||
        winProbability > 0.95
      )) {
        isConverged = 1;
      }
    }

    const banditAlpha = winProbability !== null
      ? metrics.positive_events + 1
      : parseFloat(strat.bandit_alpha) || 1;

    const banditBeta = winProbability !== null
      ? Math.max(metrics.total_exposures - metrics.positive_events, 0) + 1
      : parseFloat(strat.bandit_beta) || 1;

    const insertResults = async () => {
      // baseCols/placeholders 用于参数化查询
      const paramCols = [
        'experiment_id', 'strategy_id', 'total_exposures', 'total_clicks',
        'total_ratings', 'total_collects', 'total_watch_seconds', 'unique_users',
        'ctr', 'ctr_ci_lower', 'ctr_ci_upper', 'avg_watch_seconds',
        'rating_rate', 'collect_rate', 'positive_events',
        'p_value', 'is_winner', 'is_converged', 'sample_size_sufficient',
        'bandit_alpha', 'bandit_beta', 'win_probability'
      ];
      const paramVals = [
        exp.id, strat.id, metrics.total_exposures, metrics.total_clicks,
        metrics.total_ratings, metrics.total_collects, metrics.total_watch_seconds, metrics.unique_users,
        metrics.ctr, ci.lower, ci.upper, metrics.avg_watch_seconds,
        metrics.rating_rate, metrics.collect_rate, metrics.positive_events,
        pValue, isWinner, isConverged, sampleSizeSufficient,
        banditAlpha, banditBeta, winProbability
      ];
      const placeholders = paramCols.map(() => '?').join(',');
      const sql = `INSERT INTO ab_results (analysis_time, period_start, period_end, ${paramCols.join(',')})
        VALUES (NOW(), DATE_SUB(NOW(), INTERVAL ${LOOKBACK_HOURS} HOUR), NOW(), ${placeholders})`;
      try {
        await query(sql, paramVals);
      } catch (err) {
        if (err.code === 'ER_BAD_FIELD_ERROR') {
          const trimmedCols = paramCols.slice(0, -1);
          const trimmedVals = paramVals.slice(0, -1);
          const trimmedPH = trimmedCols.map(() => '?').join(',');
          const trimmedSQL = `INSERT INTO ab_results (analysis_time, period_start, period_end, ${trimmedCols.join(',')})
            VALUES (NOW(), DATE_SUB(NOW(), INTERVAL ${LOOKBACK_HOURS} HOUR), NOW(), ${trimmedPH})`;
          await query(trimmedSQL, trimmedVals);
        } else {
          throw err;
        }
      }
    };

    await insertResults();

    if (exp.split_mode === 'bandit') {
      await query(
        'UPDATE ab_strategies SET bandit_alpha = ?, bandit_beta = ? WHERE id = ?',
        [banditAlpha, banditBeta, strat.id]
      );
    }
  }

  return { experimentId: exp.id, strategies: allMetrics };
}

async function checkConvergenceAndStop(expId) {
  const results = await query(
    `SELECT r.*, s.is_control
     FROM ab_results r
     JOIN ab_strategies s ON r.strategy_id = s.id
     WHERE r.experiment_id = ?
     ORDER BY r.analysis_time DESC
     LIMIT ?`,
    [expId, 10]
  );

  const latest = {};
  for (const row of results) {
    if (!latest[row.strategy_id]) {
      latest[row.strategy_id] = row;
    }
  }

  const converged = Object.values(latest).some(r => r.is_converged === 1);
  if (!converged) return;

  const exp = await query('SELECT * FROM ab_experiments WHERE id = ?', [expId]);
  if (exp.length === 0 || exp[0].status !== 'running') return;

  const winner = Object.values(latest).find(r => r.is_winner === 1);
  if (!winner) return;

  console.log(`[AB Analyzer] 实验 #${expId} 已收敛，优胜策略 #${winner.strategy_id}`);

  await query(
    `UPDATE ab_experiments SET status = 'stopped', winner_strategy_id = ?, end_time = NOW() WHERE id = ?`,
    [winner.strategy_id, expId]
  );

  await query(
    `UPDATE ab_strategies SET traffic_percentage = 0 WHERE experiment_id = ? AND id != ?`,
    [expId, winner.strategy_id]
  );
  await query(
    `UPDATE ab_strategies SET traffic_percentage = 100, is_winner = 1 WHERE id = ?`,
    [winner.strategy_id]
  );
}

// ============================================
// 主循环
// ============================================

async function runAnalysisOnce() {
  try {
    const experiments = await query(`
      SELECT * FROM ab_experiments
      WHERE status = 'running'
        AND (start_time IS NULL OR start_time <= NOW())
        AND (end_time IS NULL OR end_time > NOW())
    `);

    if (experiments.length === 0) {
      console.log('[AB Analyzer] 无进行中实验');
      return;
    }

    console.log(`[AB Analyzer] 分析 ${experiments.length} 个进行中实验...`);

    for (const exp of experiments) {
      try {
        await analyzeExperiment(exp);
        await checkConvergenceAndStop(exp.id);
      } catch (e) {
        console.error(`[AB Analyzer] 实验 ${exp.id} 分析失败:`, e.message);
      }
    }

    const expIds = experiments.map(e => e.id).join(',');
    console.log(`[AB Analyzer] 本轮分析完成: 实验 [${expIds}]`);
  } catch (err) {
    console.error('[AB Analyzer] 分析循环失败:', err.message);
  }
}

let _intervalHandle = null;

function startAnalysisLoop() {
  console.log(`[AB Analyzer] 启动定时分析 (间隔=${ANALYSIS_INTERVAL / 1000}s)`);
  runAnalysisOnce();
  _intervalHandle = setInterval(runAnalysisOnce, ANALYSIS_INTERVAL);
}

function stopAnalysisLoop() {
  if (_intervalHandle) {
    clearInterval(_intervalHandle);
    _intervalHandle = null;
    console.log('[AB Analyzer] 已停止');
  }
}

module.exports = { startAnalysisLoop, stopAnalysisLoop, runAnalysisOnce };
