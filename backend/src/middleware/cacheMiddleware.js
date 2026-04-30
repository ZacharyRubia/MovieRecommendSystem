/**
 * Redis 缓存中间件
 *
 * 功能：
 * 1. cacheResponse(keyPrefix, ttlSeconds) — 读缓存：GET 请求自动缓存响应，默认 5 分钟
 * 2. clearCache(keyPrefix) — 写操作后自动清除相关读缓存
 * 3. useWriteBehind(type, tableName, buildQuery) — 写操作使用 Write-Behind 队列
 */

const { cacheService, writeBehindQueue } = require('../services/cacheService');

// 默认缓存 5 分钟（300 秒）
const DEFAULT_TTL_SECONDS = 300;

/**
 * 缓存 GET 请求的响应结果（使用 Redis）
 * @param {string} keyPrefix - 缓存键前缀
 * @param {number} ttlSeconds - 缓存时间（秒），默认 300（5分钟）
 */
function cacheResponse(keyPrefix, ttlSeconds = DEFAULT_TTL_SECONDS) {
  return async (req, res, next) => {
    // 只缓存 GET 请求
    if (req.method !== 'GET') {
      return next();
    }

    // 构建缓存键：前缀 + 请求路径 + 请求参数
    const queryString = Object.keys(req.query).length > 0
      ? '?' + Object.entries(req.query)
          .sort(([a], [b]) => a.localeCompare(b))
          .map(([k, v]) => `${k}=${v}`)
          .join('&')
      : '';
    const cacheKey = `${keyPrefix}${req.path}${queryString}`;

    try {
      // 检查 Redis 缓存
      const cachedData = await cacheService.get(cacheKey);
      if (cachedData !== null) {
        console.log(`[Cache Middleware] 缓存命中: ${cacheKey}`);
        return res.json(cachedData);
      }
    } catch (err) {
      // 缓存查询失败，继续执行原始请求
      console.error(`[Cache Middleware] 缓存查询失败: ${err.message}`);
    }

    // 缓存未命中，拦截 res.json 以缓存响应
    const originalJson = res.json.bind(res);
    res.json = (data) => {
      // 只缓存成功响应
      if (data && data.success !== false) {
        // 异步写入 Redis（不阻塞响应），Redis setex TTL 单位是秒
        cacheService.set(cacheKey, data, ttlSeconds)
          .then(() => console.log(`[Cache Middleware] 缓存已设置: ${cacheKey} (TTL: ${ttlSeconds}s)`))
          .catch(err => console.error(`[Cache Middleware] 缓存设置失败: ${err.message}`));
      }
      return originalJson(data);
    };

    next();
  };
}

/**
 * 清除指定前缀的缓存（使用 Redis）
 * @param {string} keyPrefix - 要清除的缓存键前缀
 */
function clearCache(keyPrefix) {
  return (req, res, next) => {
    const originalJson = res.json.bind(res);

    res.json = (data) => {
      // 操作成功后清除 Redis 缓存
      if (data && data.success !== false) {
        cacheService.delByPrefix(keyPrefix)
          .then(() => {}) // 异步清理，不阻塞响应
          .catch(err => console.error(`[Cache Middleware] 缓存清理失败: ${err.message}`));
      }
      return originalJson(data);
    };

    next();
  };
}

/**
 * Write-Behind 写入中间件
 * 将写入操作放入内存队列，2.5 分钟后批量刷入数据库
 * @param {string} type - 操作类型: 'INSERT' | 'UPDATE' | 'DELETE'
 * @param {string} tableName - 表名
 * @param {function} buildQuery - 函数，接收 (req, resData) 返回 { sql, params, label }
 */
function useWriteBehind(type, tableName, buildQuery) {
  return (req, res, next) => {
    const originalJson = res.json.bind(res);

    res.json = async (data) => {
      // 操作成功后加入写回队列
      if (data && data.success !== false) {
        try {
          const { sql, params, label } = buildQuery(req, data);
          writeBehindQueue.enqueue(type, sql, params, { table: tableName, label });
        } catch (err) {
          console.error(`[WriteBehind] 构建查询失败: ${err.message}`);
        }
      }
      return originalJson(data);
    };

    next();
  };
}

module.exports = { cacheResponse, clearCache, useWriteBehind, DEFAULT_TTL_SECONDS };