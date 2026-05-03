/**
 * Redis 缓存服务 + Write-Behind 写回队列
 *
 * 缓存策略：
 * - GET 请求结果缓存 5 分钟（Redis TTL），减少数据库查询
 * - 写入操作（Write-Behind）：先存内存队列，立即返回，每 150s 批量刷入数据库
 *   - flushInterval: 每 150 秒（2.5 分钟）检查并刷写一次
 *   - maxBatchSize: 每次最多合并 50 条操作
 * - POST/PUT/DELETE 操作后自动清除相关读缓存（Redis del by pattern）
 */

const Redis = require('ioredis');
const db = require('../config/db');

// ==================== Redis 客户端配置 ====================

const REDIS_HOST = process.env.REDIS_HOST || '192.168.1.39';
const REDIS_PORT = parseInt(process.env.REDIS_PORT || '6379');
const REDIS_PASSWORD = process.env.REDIS_PASSWORD || '';

const redis = new Redis({
  host: REDIS_HOST,
  port: REDIS_PORT,
  password: REDIS_PASSWORD || undefined,
  retryStrategy(times) {
    const delay = Math.min(times * 200, 5000);
    console.log(`[Redis] 连接重试 #${times}，延迟 ${delay}ms`);
    return delay;
  },
  maxRetriesPerRequest: 3,
  lazyConnect: false
});

redis.on('connect', () => {
  console.log(`[Redis] 已连接到 ${REDIS_HOST}:${REDIS_PORT}`);
});

redis.on('error', (err) => {
  console.error(`[Redis] 连接错误: ${err.message}`);
});

redis.on('close', () => {
  console.log('[Redis] 连接已关闭');
});

// ==================== 缓存键前缀常量 ====================

const CACHE_KEYS = {
  MOVIES: 'admin:movies:',
  MOVIE: 'admin:movie:',
  TAGS: 'admin:tags:',
  GENRES: 'admin:genres:',
  DIRECTORS: 'admin:directors:',
  ACTORS: 'admin:actors:',
  COMMENTS: 'admin:comments:',
  USERS: 'users:',
  ADMIN_PROFILE: 'admin:profile:'
};

// ==================== Redis 写回队列 (Write-Behind) ====================

class WriteBehindQueue {
  constructor(options = {}) {
    this.queue = [];
    this.flushInterval = options.flushInterval || 150000; // 默认每 2.5 分钟刷写一次
    this.maxBatchSize = options.maxBatchSize || 50;
    this.isProcessing = false;
    this.totalWritten = 0;
    this.totalErrors = 0;
    this._shutdown = false;

    this._timer = setInterval(() => {
      if (!this._shutdown) {
        this.flush();
      }
    }, this.flushInterval);

    console.log(`[WriteBehind] 写回队列初始化完成，刷写间隔: ${this.flushInterval / 1000}s（2.5分钟），最大批处理: ${this.maxBatchSize}`);
  }

  enqueue(type, sql, params, metadata = {}) {
    const entry = {
      id: `${Date.now()}-${Math.random().toString(36).substr(2, 6)}`,
      type,
      sql,
      params,
      metadata,
      queuedAt: Date.now()
    };

    this.queue.push(entry);

    if (this.queue.length >= this.maxBatchSize * 2 && !this.isProcessing) {
      setImmediate(() => this.flush());
    }

    console.log(`[WriteBehind] 已入队 [#${this.queue.length}] ${type} ${metadata.table || ''} ${metadata.label || ''}`);
    return { queued: true, queueLength: this.queue.length };
  }

  async flush() {
    if (this.isProcessing || this.queue.length === 0 || this._shutdown) return;

    this.isProcessing = true;
    const batch = this.queue.splice(0, this.maxBatchSize);
    let successCount = 0;
    let failCount = 0;

    try {
      for (const entry of batch) {
        try {
          await db.query(entry.sql, entry.params);
          this.totalWritten++;
          successCount++;
        } catch (err) {
          this.totalErrors++;
          failCount++;
          console.error(`[WriteBehind] 执行失败: ${entry.type} ${entry.metadata.label || entry.sql.slice(0, 60)} - ${err.message}`);

          // 重试 3 次
          let retried = false;
          for (let attempt = 1; attempt <= 3; attempt++) {
            try {
              await new Promise(resolve => setTimeout(resolve, attempt * 200));
              await db.query(entry.sql, entry.params);
              this.totalWritten++;
              this.totalErrors--;
              successCount++;
              failCount--;
              retried = true;
              console.log(`[WriteBehind] 重试第 ${attempt} 次成功: ${entry.metadata.label || entry.sql.slice(0, 60)}`);
              break;
            } catch (retryErr) {
              console.error(`[WriteBehind] 重试第 ${attempt} 次失败: ${retryErr.message}`);
            }
          }

          if (!retried) {
            if ((entry.retryCount || 0) < 3) {
              entry.retryCount = (entry.retryCount || 0) + 1;
              this.queue.push(entry);
              console.log(`[WriteBehind] 放回队列末尾重试: ${entry.metadata.label || entry.sql.slice(0, 60)}`);
            } else {
              console.error(`[WriteBehind] 最终放弃（已重试3次）: ${entry.metadata.label || entry.sql.slice(0, 60)}`);
            }
          }
        }
      }

      console.log(`[WriteBehind] 刷写完成: 成功 ${successCount}/${batch.length} 条，队列剩余 ${this.queue.length} 条`);
    } catch (err) {
      console.error(`[WriteBehind] 刷写出错: ${err.message}`);
    } finally {
      this.isProcessing = false;
    }
  }

  getStats() {
    return {
      pendingCount: this.queue.length,
      isProcessing: this.isProcessing,
      totalWritten: this.totalWritten,
      totalErrors: this.totalErrors,
      flushInterval: `${this.flushInterval / 1000}s（${this.flushInterval / 60000}分钟）`
    };
  }

  async flushAll() {
    console.log(`[WriteBehind] 强制刷写所有待处理操作 (${this.queue.length} 条)...`);
    await this.flush();
    while (this.queue.length > 0) {
      await this.flush();
    }
    console.log('[WriteBehind] 所有操作已刷写完成');
  }

  async shutdown() {
    this._shutdown = true;
    console.log('[WriteBehind] 关闭写回队列...');
    await this.flushAll();
    if (this._timer) {
      clearInterval(this._timer);
      this._timer = null;
    }
    console.log('[WriteBehind] 写回队列已关闭');
  }
}

// ==================== Redis 缓存服务 ====================

class RedisCache {
  constructor() {
    this.defaultTTL = 300; // 默认缓存时间 5 分钟（秒）
    this.hitCount = 0;
    this.missCount = 0;

    console.log(`[CacheService] Redis 缓存服务初始化完成，默认 TTL: ${this.defaultTTL}s (5分钟)`);
  }

  /**
   * 设置缓存（Redis SET 带 TTL）
   * @param {string} key - 缓存键
   * @param {*} value - 缓存值
   * @param {number} ttl - 过期时间（秒），默认 5 分钟
   */
  async set(key, value, ttl = this.defaultTTL) {
    try {
      const serialized = JSON.stringify(value);
      await redis.setex(key, ttl, serialized);
    } catch (err) {
      console.error(`[Redis] SET 失败: ${key} - ${err.message}`);
    }
  }

  /**
   * 获取缓存
   * @param {string} key - 缓存键
   * @returns {*|null} 缓存值，如果不存在或已过期返回 null
   */
  async get(key) {
    try {
      const data = await redis.get(key);
      if (data === null) {
        this.missCount++;
        return null;
      }
      this.hitCount++;
      return JSON.parse(data);
    } catch (err) {
      console.error(`[Redis] GET 失败: ${key} - ${err.message}`);
      this.missCount++;
      return null;
    }
  }

  /**
   * 删除指定缓存
   */
  async del(key) {
    try {
      await redis.del(key);
    } catch (err) {
      console.error(`[Redis] DEL 失败: ${key} - ${err.message}`);
    }
  }

  /**
   * 按前缀批量删除缓存（使用 SCAN 匹配）
   */
  async delByPrefix(prefix) {
    try {
      let cursor = '0';
      let deletedCount = 0;

      do {
        const result = await redis.scan(cursor, 'MATCH', `${prefix}*`, 'COUNT', 100);
        cursor = result[0];
        const keys = result[1];

        if (keys.length > 0) {
          await redis.del(...keys);
          deletedCount += keys.length;
        }
      } while (cursor !== '0');

      if (deletedCount > 0) {
        console.log(`[CacheService] 清除了 ${deletedCount} 个前缀为 "${prefix}" 的 Redis 缓存`);
      }
    } catch (err) {
      console.error(`[Redis] 按前缀删除失败: ${prefix} - ${err.message}`);
    }
  }

  /**
   * 清空所有匹配的缓存键
   */
  async flushAll() {
    try {
      let cursor = '0';
      let deletedCount = 0;

      do {
        const result = await redis.scan(cursor, 'MATCH', 'admin:*', 'COUNT', 200);
        cursor = result[0];
        const keys = result[1];

        if (keys.length > 0) {
          await redis.del(...keys);
          deletedCount += keys.length;
        }
      } while (cursor !== '0');

      // 也清除 users 缓存
      let cursor2 = '0';
      do {
        const result = await redis.scan(cursor2, 'MATCH', 'users:*', 'COUNT', 200);
        cursor2 = result[0];
        const keys = result[1];
        if (keys.length > 0) {
          await redis.del(...keys);
          deletedCount += keys.length;
        }
      } while (cursor2 !== '0');

      console.log(`[CacheService] 已清空所有 Redis 缓存（共 ${deletedCount} 条）`);
    } catch (err) {
      console.error(`[Redis] 清空所有缓存失败: ${err.message}`);
    }
  }

  /**
   * 获取缓存统计信息（通过 SCAN 扫描获取）
   */
  async getStats() {
    try {
      const info = await redis.info('keyspace');
      const entries = [];

      let cursor = '0';
      do {
        const result = await redis.scan(cursor, 'MATCH', '*', 'COUNT', 500);
        cursor = result[0];
        for (const key of result[1]) {
          const ttl = await redis.ttl(key);
          entries.push({ key, ttl: ttl >= 0 ? `${ttl}s` : 'no expiry' });
        }
      } while (cursor !== '0');

      return {
        enabled: true,
        backend: 'Redis',
        host: `${REDIS_HOST}:${REDIS_PORT}`,
        totalEntries: entries.length,
        hitCount: this.hitCount,
        missCount: this.missCount,
        hitRate: this.hitCount + this.missCount > 0
          ? (this.hitCount / (this.hitCount + this.missCount) * 100).toFixed(1) + '%'
          : 'N/A',
        entries
      };
    } catch (err) {
      console.error(`[Redis] 获取统计信息失败: ${err.message}`);
      return {
        enabled: false,
        backend: 'Redis',
        error: err.message,
        totalEntries: 0,
        hitCount: this.hitCount,
        missCount: this.missCount,
        hitRate: 'N/A',
        entries: []
      };
    }
  }

  /**
   * 测试 Redis 连接是否正常
   */
  async ping() {
    try {
      const result = await redis.ping();
      return result === 'PONG';
    } catch (err) {
      console.error(`[Redis] PING 失败: ${err.message}`);
      return false;
    }
  }

  /**
   * 关闭 Redis 连接
   */
  async shutdown() {
    try {
      await redis.quit();
      console.log('[CacheService] Redis 连接已关闭');
    } catch (err) {
      console.error(`[CacheService] 关闭 Redis 失败: ${err.message}`);
    }
  }
}

// 创建单例
const cacheService = new RedisCache();
const writeBehindQueue = new WriteBehindQueue();

// 初始化时测试连接
(async () => {
  const alive = await cacheService.ping();
  if (alive) {
    console.log('[CacheService] Redis 连接测试通过 ✓');
  } else {
    console.warn('[CacheService] Redis 连接测试失败！缓存将不可用');
  }
})();

module.exports = { cacheService, writeBehindQueue, CACHE_KEYS };