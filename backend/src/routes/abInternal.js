/**
 * abInternal.js - 内部分析数据接口
 * 供 Python 离线分析脚本或 Node.js 内部调用
 */
const express = require('express');
const router = express.Router();
const { query } = require('../config/db');
const { getRedisClient } = require('../services/cacheService');

/**
 * GET /api/internal/experiment-data/:id
 * Python 脚本调用：获取指定实验的原始行为数据（分页）
 */
router.get('/experiment-data/:id', async (req, res) => {
  try {
    const { id } = req.params;
    const { page = 1, pageSize = 1000, startTime, endTime } = req.query;
    const offset = (parseInt(page) - 1) * parseInt(pageSize);

    // 验证实验存在
    const experiments = await query('SELECT id, name FROM ab_experiments WHERE id = ?', [id]);
    if (experiments.length === 0) {
      return res.status(404).json({ success: false, message: '实验不存在' });
    }

    let sql = `
      SELECT b.*, m.title AS movie_title
      FROM users_movies_behaviors b
      LEFT JOIN movies m ON b.movie_id = m.id
      WHERE b.experiment_id = ?
    `;
    const params = [id];

    if (startTime) {
      sql += ' AND b.created_at >= ?';
      params.push(startTime);
    }
    if (endTime) {
      sql += ' AND b.created_at <= ?';
      params.push(endTime);
    }

    // 获取总数
    const countResult = await query(
      `SELECT COUNT(*) AS total FROM users_movies_behaviors WHERE experiment_id = ?`,
      [id]
    );
    const total = countResult[0].total;

    sql += ' ORDER BY b.created_at DESC LIMIT ? OFFSET ?';
    params.push(parseInt(pageSize), offset);

    const behaviors = await query(sql, params);

    res.json({
      success: true,
      data: {
        experimentId: parseInt(id),
        experimentName: experiments[0].name,
        behaviors,
        pagination: {
          page: parseInt(page),
          pageSize: parseInt(pageSize),
          total,
          totalPages: Math.ceil(total / parseInt(pageSize))
        }
      }
    });
  } catch (err) {
    console.error('获取实验数据失败:', err);
    res.status(500).json({ success: false, message: '获取实验数据失败: ' + err.message });
  }
});

/**
 * POST /api/internal/update-bandit-params
 * Python 脚本调用：将更新后的各策略 Beta 参数写入 Redis
 */
router.post('/update-bandit-params', async (req, res) => {
  try {
    const { experimentId, strategies } = req.body;

    if (!experimentId || !strategies || !Array.isArray(strategies)) {
      return res.status(400).json({ success: false, message: '参数不完整' });
    }

    const redis = getRedisClient();
    if (!redis) {
      return res.status(503).json({ success: false, message: 'Redis 不可用' });
    }

    const pipeline = redis.pipeline();
    for (const s of strategies) {
      const key = `ab:bandit:${experimentId}:${s.strategyId}`;
      pipeline.hmset(key, {
        alpha: s.alpha || 1,
        beta: s.beta || 1,
        updatedAt: new Date().toISOString()
      });
      // 同时更新数据库
      await query(
        'UPDATE ab_strategies SET bandit_alpha = ?, bandit_beta = ? WHERE id = ?',
        [s.alpha || 1, s.beta || 1, s.strategyId]
      );
    }
    await pipeline.exec();

    res.json({ success: true, message: 'Bandit 参数更新成功' });
  } catch (err) {
    console.error('更新 Bandit 参数失败:', err);
    res.status(500).json({ success: false, message: '更新 Bandit 参数失败: ' + err.message });
  }
});

/**
 * GET /api/internal/bandit-params/:expId
 * 获取指定实验的各策略 Bandit 后验参数（备用接口）
 */
router.get('/bandit-params/:expId', async (req, res) => {
  try {
    const { expId } = req.params;

    const strategies = await query(
      'SELECT id, name, bandit_alpha, bandit_beta FROM ab_strategies WHERE experiment_id = ? AND bandit_alpha IS NOT NULL',
      [expId]
    );

    const redis = getRedisClient();
    if (redis) {
      const pipeline = redis.pipeline();
      for (const s of strategies) {
        const key = `ab:bandit:${expId}:${s.id}`;
        pipeline.hgetall(key);
      }
      const redisResults = await pipeline.exec();
      if (redisResults) {
        for (let i = 0; i < strategies.length; i++) {
          if (redisResults[i] && redisResults[i][1]) {
            strategies[i].redisAlpha = parseFloat(redisResults[i][1].alpha) || strategies[i].bandit_alpha;
            strategies[i].redisBeta = parseFloat(redisResults[i][1].beta) || strategies[i].bandit_beta;
          }
        }
      }
    }

    res.json({ success: true, data: strategies });
  } catch (err) {
    console.error('获取 Bandit 参数失败:', err);
    res.status(500).json({ success: false, message: '获取 Bandit 参数失败' });
  }
});

module.exports = router;