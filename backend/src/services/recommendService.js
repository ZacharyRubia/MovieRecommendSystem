/**
 * recommendService.js - KNN 协同过滤推荐算法服务（SQL 驱动版）
 * 
 * 针对 MovieLens 25M 数据集优化：
 * - 不将全量数据加载到内存，而是使用 SQL 聚合
 * - User-Based CF：先通过 SQL 筛选共同评分超过阈值的候选用户，再计算 Pearson 相似度
 * - Item-Based CF：通过 SQL 计算与用户已评分电影相关的候选电影相似度
 * - 新增：热门/新片/趋势推荐、Qdrant 基于内容推荐、自适应混合权重
 * 
 * 性能优化策略：
 * - 使用 SQL JOIN + GROUP BY 替代全量内存加载
 * - 批量加载必要数据到内存（仅邻居用户或候选电影）
 * - 使用 30 分钟缓存放热数据
 */

const { query } = require('../config/db');
const { QdrantClient } = require('@qdrant/js-client-rest');

// =============================================
// Qdrant 客户端
// =============================================
const qdrantClient = new QdrantClient({
  host: process.env.QDRANT_HOST || '192.168.1.38',
  port: parseInt(process.env.QDRANT_PORT) || 6333
});

// =============================================
// 配置常量
// =============================================
const DEFAULT_K = 10;            // KNN 邻居数
const DEFAULT_TOP_N = 10;        // 推荐结果数
const MIN_COMMON_ITEMS = 3;      // 用户间最小共同评分项数
const MIN_RATINGS_FOR_USER = 5;  // 用户最少评分数量
const SIMILARITY_THRESHOLD = 0.01; // 低相似度过滤阈值
const CACHE_TTL = 30 * 60 * 1000; // 缓存30分钟

// =============================================
// 通用工具函数
// =============================================

/**
 * 推荐结果去重，保持评分高的优先
 */
function deduplicateAndStabilize(recommendations) {
  const seen = new Set();
  const result = [];
  for (const item of recommendations) {
    if (!seen.has(item.movieId)) {
      seen.add(item.movieId);
      result.push(item);
    }
  }
  return result;
}

// =============================================
// 用户轻量缓存（仅缓存频繁访问的元数据）
// =============================================
const cache = {
  neighborCache: new Map(),     // userId -> { neighbors, timestamp }
  lastClearTime: Date.now(),

  isValid(timestamp) {
    return (Date.now() - timestamp) < CACHE_TTL;
  },

  getNeighbors(userId) {
    const entry = this.neighborCache.get(userId);
    if (entry && this.isValid(entry.timestamp)) return entry.neighbors;
    return null;
  },

  setNeighbors(userId, neighbors) {
    this.neighborCache.set(userId, { neighbors, timestamp: Date.now() });
  },

  clear() {
    this.neighborCache.clear();
    this.lastClearTime = Date.now();
    console.log('[推荐] 缓存已清除');
  }
};

// =============================================
// 离线缓存读取（数据库缓存表）
// =============================================

const CACHE_TTL_MS = 60 * 60 * 1000; // 离线缓存有效期1小时

/**
 * 从 movie_similarity_caches 读取某部电影的相似电影列表
 */
async function getCachedSimilarMovies(movieId, minSimilarity = 0) {
  const rows = await query(
    'SELECT similar_movies, updated_at FROM movie_similarity_caches WHERE movie_id = ?',
    [movieId]
  );
  if (!rows || rows.length === 0) return null;
  const row = rows[0];
  // 检查缓存是否过期
  if (Date.now() - new Date(row.updated_at).getTime() > CACHE_TTL_MS) {
    return null; // 过期视为无缓存
  }
  try {
    const list = JSON.parse(row.similar_movies);
    if (minSimilarity > 0) {
      return list.filter(item => item.similarity >= minSimilarity);
    }
    return list;
  } catch {
    return null;
  }
}

/**
 * 从 movie_similarity_caches 聚合 Item-Based 推荐
 * 对用户已评分的每部电影，查找其相似电影，按评分加权聚合
 */
async function getItemBasedFromCache(targetRatingMap, topN) {
  const movieIds = Array.from(targetRatingMap.keys());
  // 批量查询这些电影的相似缓存
  const placeholders = movieIds.map(() => '?').join(',');
  const rows = await query(
    `SELECT movie_id, similar_movies FROM movie_similarity_caches WHERE movie_id IN (${placeholders})`,
    movieIds
  );
  if (!rows || rows.length === 0) return null;

  const scoreMap = new Map(); // movieId -> { weightedSum, weightSum }
  for (const row of rows) {
    const userRating = targetRatingMap.get(row.movie_id);
    if (!userRating) continue;
    let similar;
    try {
      similar = JSON.parse(row.similar_movies);
    } catch {
      continue;
    }
    for (const item of similar) {
      const mid = item.movie_id;
      const sim = item.similarity || 0;
      if (sim <= 0) continue;
      if (!scoreMap.has(mid)) {
        scoreMap.set(mid, { weightedSum: 0, weightSum: 0 });
      }
      const entry = scoreMap.get(mid);
      entry.weightedSum += sim * userRating;
      entry.weightSum += sim;
    }
  }

  if (scoreMap.size === 0) return null;

  const predictions = [];
  for (const [movieId, { weightedSum, weightSum }] of scoreMap) {
    if (weightSum > 0) {
      predictions.push({ movieId, predictedRating: weightedSum / weightSum });
    }
  }
  predictions.sort((a, b) => b.predictedRating - a.predictedRating);
  return deduplicateAndStabilize(predictions.slice(0, topN));
}

/**
 * 从 user_recommend_caches 读取某用户的推荐列表
 */
async function getCachedUserRecommend(userId) {
  const rows = await query(
    'SELECT recommend_movies, algorithm, updated_at FROM user_recommend_caches WHERE user_id = ?',
    [userId]
  );
  if (!rows || rows.length === 0) return null;
  const row = rows[0];
  // 检查缓存是否过期
  if (Date.now() - new Date(row.updated_at).getTime() > CACHE_TTL_MS) {
    return null;
  }
  try {
    const list = JSON.parse(row.recommend_movies);
    return { items: list, algorithm: row.algorithm };
  } catch {
    return null;
  }
}

// =============================================
// 回退推荐（稀疏数据时使用）
// =============================================

/**
 * 当用户评分数据不足时的回退推荐
 * - 无评分：返回热门推荐
 * - 有少量评分：基于已评分电影的题材推荐同类电影
 */
async function getFallbackRecommendations(limitedRatings, topN = DEFAULT_TOP_N) {
  if (!limitedRatings || limitedRatings.length === 0) {
    // 完全没有评分：返回热门推荐
    const popular = await getPopularRecommendations(1, topN);
    return popular.map(r => ({ movieId: r.movieId, predictedRating: r.predictedRating }));
  }

  // 有少量评分：使用这些评分电影的题材作为推荐依据
  const movieIds = limitedRatings.map(r => r.movie_id);
  const placeholders = movieIds.map(() => '?').join(',');

  // 通过题材相似性找同类电影
  const similarByGenre = await query(`
    SELECT mg2.movie_id, COUNT(*) AS genre_overlap,
           AVG(m.avg_rating) AS avg_rating
    FROM movies_genres mg1
    JOIN movies_genres mg2 ON mg1.genre_id = mg2.genre_id AND mg2.movie_id NOT IN (${placeholders})
    JOIN movies m ON mg2.movie_id = m.id
    WHERE mg1.movie_id IN (${placeholders})
    GROUP BY mg2.movie_id
    HAVING genre_overlap >= 2
    ORDER BY genre_overlap DESC, AVG(m.avg_rating) DESC
    LIMIT ?
  `, [...movieIds, ...movieIds, topN]);

  return similarByGenre.map(r => ({
    movieId: r.movie_id,
    predictedRating: Math.min(5, (parseFloat(r.avg_rating) || 0) + Math.log10(parseInt(r.genre_overlap) + 1) * 0.5)
  }));
}

// =============================================
// 热门推荐
// =============================================

/**
 * 根据评分数量和评分均值计算热门电影
 */
async function getPopularRecommendations(page = 1, pageSize = 20, genre = null) {
  const offset = (page - 1) * pageSize;
  let sql = `
    SELECT m.id, m.title, m.release_year, m.avg_rating, m.cover_url,
           COUNT(r.movie_id) AS rating_count
    FROM movies m
    LEFT JOIN users_movies_behaviors r 
      ON m.id = r.movie_id AND r.behavior_type = 'rate'
  `;
  const params = [];
  if (genre) {
    sql += ` JOIN movies_genres mg ON m.id = mg.movie_id
             JOIN genres g ON mg.genre_id = g.id AND g.code = ?`;
    params.push(genre);
  }
  sql += ` GROUP BY m.id
           ORDER BY rating_count DESC, m.avg_rating DESC
           LIMIT ? OFFSET ?`;
  params.push(pageSize, offset);

  const results = await query(sql, params);
  return results.map(r => ({
    movieId: r.id,
    title: r.title,
    releaseYear: r.release_year,
    avgRating: parseFloat(r.avg_rating) || 0,
    predictedRating: Math.min(5, (parseFloat(r.avg_rating) || 0) +
                              Math.log10(parseInt(r.rating_count) + 1) * 0.5),
    coverUrl: r.cover_url || ''
  }));
}

// =============================================
// 新片推荐
// =============================================

/**
 * 按发布日期推荐最新电影
 */
async function getNewReleaseRecommendations(page = 1, pageSize = 20) {
  const offset = (page - 1) * pageSize;
  const results = await query(`
    SELECT id, title, release_year, avg_rating, cover_url
    FROM movies
    ORDER BY release_year DESC, avg_rating DESC
    LIMIT ? OFFSET ?
  `, [pageSize, offset]);

  return results.map(m => ({
    movieId: m.id,
    title: m.title,
    releaseYear: m.release_year,
    avgRating: parseFloat(m.avg_rating) || 0,
    predictedRating: parseFloat(m.avg_rating) || 0,
    coverUrl: m.cover_url || ''
  }));
}

// =============================================
// 趋势推荐
// =============================================

/**
 * 基于近期评分活跃度推荐
 */
async function getTrendingRecommendations(page = 1, pageSize = 20, timeRange = '7d') {
  const offset = (page - 1) * pageSize;
  const daysMap = { '7d': 7, '30d': 30, '90d': 90 };
  const days = daysMap[timeRange] || 7;

  const results = await query(`
    SELECT m.id, m.title, m.release_year, m.avg_rating, m.cover_url,
           COUNT(r.movie_id) AS recent_count
    FROM movies m
    JOIN users_movies_behaviors r ON m.id = r.movie_id
    WHERE r.behavior_type = 'rate'
      AND r.created_at >= DATE_SUB(NOW(), INTERVAL ? DAY)
    GROUP BY m.id
    HAVING recent_count >= 5
    ORDER BY recent_count DESC, m.avg_rating DESC
    LIMIT ? OFFSET ?
  `, [days, pageSize, offset]);

  return results.map(m => ({
    movieId: m.id,
    title: m.title,
    releaseYear: m.release_year,
    avgRating: parseFloat(m.avg_rating) || 0,
    predictedRating: Math.min(5, (parseFloat(m.avg_rating) || 0) +
                              Math.log10(parseInt(m.recent_count)) * 0.3),
    coverUrl: m.cover_url || ''
  }));
}

// =============================================
// 基于内容的推荐（Qdrant 向量检索）
// =============================================

/**
 * 利用 Qdrant 向量检索做基于内容的推荐
 */
async function getContentBasedRecommendations(userId, page = 1, pageSize = 20) {
  // 1. 获取用户评分最高的电影
  const topMovies = await query(`
    SELECT movie_id, rating
    FROM users_movies_behaviors
    WHERE user_id = ? AND behavior_type = 'rate' AND rating IS NOT NULL
    ORDER BY rating DESC
    LIMIT 5
  `, [userId]);

  if (topMovies.length === 0) {
    // 用户无评分时返回热门推荐
    return getPopularRecommendations(page, pageSize);
  }

  // 2. 使用最高评分电影作为 positive 向量查询 Qdrant
  const positiveIds = topMovies.filter(r => r.rating >= 4).map(r => r.movie_id);
  const negativeIds = topMovies.filter(r => r.rating <= 2).map(r => r.movie_id);

  const offset = (page - 1) * pageSize;

  try {
    const searchResult = await qdrantClient.recommend('movies', {
      positive: positiveIds.length > 0 ? positiveIds : [topMovies[0].movie_id],
      negative: negativeIds.length > 0 ? negativeIds : undefined,
      limit: pageSize,
      offset: offset
    });

    // 3. 组装返回结果
    return searchResult.map(r => ({
      movieId: parseInt(r.id),
      title: r.payload?.title || '',
      releaseYear: r.payload?.release_year || 0,
      avgRating: r.payload?.avg_rating || 0,
      predictedRating: parseFloat((r.score || 0).toFixed(2)),
      coverUrl: r.payload?.cover_url || ''
    }));
  } catch (err) {
    console.error('[Qdrant] 向量检索失败，降级为热门推荐:', err.message);
    // Qdrant 连接失败时降级回热门推荐
    return getPopularRecommendations(page, pageSize);
  }
}

// =============================================
// 自适应混合权重
// =============================================

/**
 * 根据用户活跃度动态调整 User-CF 和 Item-CF 权重
 */
async function getAdaptiveWeight(userId) {
  const stats = await query(`
    SELECT COUNT(*) AS rating_count,
           COUNT(DISTINCT movie_id) AS unique_movies
    FROM users_movies_behaviors
    WHERE user_id = ? AND behavior_type = 'rate'
  `, [userId]);

  const count = stats[0]?.rating_count || 0;
  const uniqueMovies = stats[0]?.unique_movies || 0;

  if (count < 10) return 0.3;    // 新用户 → 倾向 Item-CF
  if (count < 50) return 0.5;    // 一般用户 → 均衡
  return 0.7;                     // 活跃用户 → 倾向 User-CF
}

// =============================================
// User-Based Collaborative Filtering
// =============================================

/**
 * 通过 SQL 找到与目标用户评分模式最相似的 K 个用户
 *
 * 策略：
 * 1. 先找出与目标用户有共同评分电影（且共同数 ≥ MIN_COMMON_ITEMS）的所有用户
 * 2. 对于这些候选用户，逐批加载评分数据计算 Pearson 相关系数
 */
async function findKNearestUsers(userId, k = DEFAULT_K) {
  // 检查缓存
  const cached = cache.getNeighbors(userId);
  if (cached) return cached;

  // --- 步骤 1: 获取目标用户评分摘要 ---
  const targetRatings = await query(
    'SELECT movie_id, rating FROM users_movies_behaviors WHERE user_id = ? AND behavior_type = \'rate\' AND rating IS NOT NULL',
    [userId]
  );

  if (targetRatings.length < MIN_RATINGS_FOR_USER) {
    console.log(`[User-Based CF] 用户 ${userId} 评分数 ${targetRatings.length} < ${MIN_RATINGS_FOR_USER}，数据不足`);
    return [];
  }

  const targetRatingMap = new Map();
  for (const r of targetRatings) {
    targetRatingMap.set(r.movie_id, r.rating);
  }

  // --- 步骤 2: SQL 筛选候选用户（至少有 MIN_COMMON_ITEMS 部共同评分电影） ---
  // 使用 JOIN 找到所有与目标用户评过相同电影的用户，按共同电影数排序
  const candidateUsers = await query(`
    SELECT
      u2.user_id,
      COUNT(*) AS common_count,
      AVG(u2.rating) AS avg_rating
    FROM users_movies_behaviors u1
    JOIN users_movies_behaviors u2
      ON u1.movie_id = u2.movie_id AND u2.user_id != u1.user_id
    WHERE u1.user_id = ?
      AND u1.behavior_type = 'rate' AND u1.rating IS NOT NULL
      AND u2.behavior_type = 'rate' AND u2.rating IS NOT NULL
    GROUP BY u2.user_id
    HAVING common_count >= ?
    ORDER BY common_count DESC
    LIMIT 500
  `, [userId, MIN_COMMON_ITEMS]);

  if (candidateUsers.length === 0) {
    console.log(`[User-Based CF] 未找到与用户 ${userId} 有 ${MIN_COMMON_ITEMS} 部以上共同评分电影的候选用户`);
    return [];
  }

  console.log(`[User-Based CF] 候选邻居: ${candidateUsers.length} 个用户`);

  // --- 步骤 3: 批量加载候选用户的评分数据 ---
  const candidateIds = candidateUsers.map(c => c.user_id);
  const similarities = [];

  // 分批次加载（每批 100 个候选用户），计算相似度
  const BATCH_SIZE = 100;
  for (let i = 0; i < candidateIds.length; i += BATCH_SIZE) {
    const batchIds = candidateIds.slice(i, i + BATCH_SIZE);
    const placeholders = batchIds.map(() => '?').join(',');

    const batchRatings = await query(
      `SELECT user_id, movie_id, rating
       FROM users_movies_behaviors
       WHERE user_id IN (${placeholders}) AND behavior_type = 'rate' AND rating IS NOT NULL`,
      batchIds
    );

    // 构建 batch 用户的评分 Map
    const batchRatingMap = new Map();
    for (const r of batchRatings) {
      if (!batchRatingMap.has(r.user_id)) {
        batchRatingMap.set(r.user_id, new Map());
      }
      batchRatingMap.get(r.user_id).set(r.movie_id, r.rating);
    }

    // 计算每个候选用户与目标用户的 Pearson 相关系数
    for (const cid of batchIds) {
      const otherRatings = batchRatingMap.get(cid);
      if (!otherRatings) continue;

      const sim = computePearson(targetRatingMap, otherRatings);
      if (sim > 0) {
        similarities.push({ userId: cid, similarity: sim });
      }
    }
  }

  // --- 步骤 4: 排序并取 Top-K ---
  similarities.sort((a, b) => b.similarity - a.similarity);
  const result = similarities.slice(0, k);

  // 存入缓存
  cache.setNeighbors(userId, result);
  return result;
}

/**
 * 计算两个用户的 Pearson 相关系数
 */
function computePearson(ratingsA, ratingsB) {
  const common = [];
  for (const [movieId, rating] of ratingsA) {
    if (ratingsB.has(movieId)) {
      common.push({ a: rating, b: ratingsB.get(movieId) });
    }
  }

  const n = common.length;
  if (n < MIN_COMMON_ITEMS) return 0;

  let sumA = 0, sumB = 0;
  for (const { a, b } of common) { sumA += a; sumB += b; }
  const meanA = sumA / n;
  const meanB = sumB / n;

  let num = 0, denomA = 0, denomB = 0;
  for (const { a, b } of common) {
    const dA = a - meanA, dB = b - meanB;
    num += dA * dB;
    denomA += dA * dA;
    denomB += dB * dB;
  }

  if (denomA === 0 || denomB === 0) return 0;
  return Math.max(0, num / Math.sqrt(denomA * denomB));
}

/**
 * User-Based Collaborative Filtering
 */
async function userBasedCF(userId, k = DEFAULT_K, topN = DEFAULT_TOP_N) {
  console.log(`[User-Based CF] 用户 ${userId}, K=${k}, TopN=${topN}`);
  const startTime = Date.now();

  // 0. 尝试读取离线缓存（由 ALS 离线计算写入）
  const cached = await getCachedUserRecommend(userId);
  if (cached && cached.algorithm === 'als') {
    console.log(`[User-Based CF] 使用离线缓存 (ALS), 耗时 ${Date.now() - startTime}ms`);
    return cached.items.map(item => ({
      movieId: item.movie_id,
      predictedRating: item.score
    }));
  }

  // 1. 找邻居
  const neighbors = await findKNearestUsers(userId, k);

  // 稀疏数据回退：邻居不足时使用回退推荐
  if (neighbors.length === 0) {
    console.log(`[User-Based CF] 用户 ${userId} 邻居不足，回退到题材推荐`);
    const targetRatings = await query(
      'SELECT movie_id, rating FROM users_movies_behaviors WHERE user_id = ? AND behavior_type = \'rate\' AND rating IS NOT NULL',
      [userId]
    );
    const fallback = await getFallbackRecommendations(targetRatings, topN);
    console.log(`[User-Based CF] 回退完成: ${fallback.length} 个推荐, 耗时 ${Date.now() - startTime}ms`);
    return fallback;
  }

  console.log(`[User-Based CF] 最相似用户: ${neighbors[0].userId} (相似度: ${neighbors[0].similarity.toFixed(4)})`);

  // 2. 收集邻居评过的电影（排除目标用户已看过的）
  // 获取目标用户已看过的电影
  const targetMovies = await query(
    'SELECT movie_id FROM users_movies_behaviors WHERE user_id = ? AND behavior_type = \'rate\'',
    [userId]
  );
  const watchedSet = new Set(targetMovies.map(r => r.movie_id));

  // 获取邻居的评分数据
  const neighborIds = neighbors.map(n => n.userId);
  const placeholders = neighborIds.map(() => '?').join(',');
  const neighborRatings = await query(
    `SELECT user_id, movie_id, rating
     FROM users_movies_behaviors
     WHERE user_id IN (${placeholders}) AND behavior_type = 'rate' AND rating IS NOT NULL`,
    neighborIds
  );

  // 构建邻居评分 Map
  const neighborRatingMap = new Map();
  for (const r of neighborRatings) {
    if (!neighborRatingMap.has(r.user_id)) {
      neighborRatingMap.set(r.user_id, new Map());
    }
    neighborRatingMap.get(r.user_id).set(r.movie_id, r.rating);
  }

  // 构建邻居相似度查找
  const neighborSimMap = new Map();
  for (const n of neighbors) {
    neighborSimMap.set(n.userId, n.similarity);
  }

  // 3. 聚合预测评分
  const candidateMovies = new Map(); // movieId -> { weightedSum, simSum }
  for (const [nid, ratings] of neighborRatingMap) {
    const sim = neighborSimMap.get(nid);
    if (!sim) continue;

    for (const [movieId, rating] of ratings) {
      if (watchedSet.has(movieId)) continue;

      if (!candidateMovies.has(movieId)) {
        candidateMovies.set(movieId, { weightedSum: 0, simSum: 0 });
      }
      const entry = candidateMovies.get(movieId);
      entry.weightedSum += sim * rating;
      entry.simSum += sim;
    }
  }

  // 4. 计算预测评分并排序
  const predictions = [];
  for (const [movieId, { weightedSum, simSum }] of candidateMovies) {
    if (simSum > 0) {
      predictions.push({ movieId, predictedRating: weightedSum / simSum });
    }
  }

  predictions.sort((a, b) => b.predictedRating - a.predictedRating);
  const result = deduplicateAndStabilize(predictions.slice(0, topN));

  console.log(`[User-Based CF] 完成: ${result.length} 个推荐, 耗时 ${Date.now() - startTime}ms`);
  return result;
}

// =============================================
// Item-Based Collaborative Filtering
// =============================================

/**
 * Item-Based Collaborative Filtering
 *
 * 策略：对用户评分过的每部电影 i，在 SQL 中找出其他也评过 i 的用户评分过的其他电影 j，
 * 通过 Cosine 相似度计算 i 与 j 的相关性，聚合所有候选电影 j 的加权评分
 */
async function itemBasedCF(userId, k = DEFAULT_K, topN = DEFAULT_TOP_N) {
  console.log(`[Item-Based CF] 用户 ${userId}, K=${k}, TopN=${topN}`);
  const startTime = Date.now();

  // 1. 获取用户评分数据
  const targetRatings = await query(
    `SELECT movie_id, rating
     FROM users_movies_behaviors
     WHERE user_id = ? AND behavior_type = 'rate' AND rating IS NOT NULL`,
    [userId]
  );

  if (targetRatings.length < MIN_RATINGS_FOR_USER) {
    console.log(`[Item-Based CF] 用户 ${userId} 评分数 ${targetRatings.length} < ${MIN_RATINGS_FOR_USER}，回退到题材推荐`);
    const fallback = await getFallbackRecommendations(targetRatings, topN);
    console.log(`[Item-Based CF] 回退完成: ${fallback.length} 个推荐, 耗时 ${Date.now() - startTime}ms`);
    return fallback;
  }

  const targetRatingMap = new Map();
  for (const r of targetRatings) {
    targetRatingMap.set(r.movie_id, r.rating);
  }

  // 0. 尝试使用离线 movie_similarity_caches 缓存
  const cachedResults = await getItemBasedFromCache(targetRatingMap, topN);
  if (cachedResults) {
    console.log(`[Item-Based CF] 使用离线缓存 (item_cf), 耗时 ${Date.now() - startTime}ms`);
    return cachedResults;
  }

  // 2. 对用户评分过的每部电影，找出评分过它的所有用户
  const ratedMovieIds = targetRatings.map(r => r.movie_id);
  const placeholders = ratedMovieIds.map(() => '?').join(',');

  // 2a. 获取这些电影的评分用户列表
  const coRatingUsers = await query(
    `SELECT DISTINCT user_id
     FROM users_movies_behaviors
     WHERE movie_id IN (${placeholders})
       AND behavior_type = 'rate' AND rating IS NOT NULL
       AND user_id != ?`,
    [...ratedMovieIds, userId]
  );
  const coUserIds = coRatingUsers.map(r => r.user_id);

  if (coUserIds.length === 0) {
    console.log(`[Item-Based CF] 未找到共同评分用户`);
    return [];
  }

  // 2b. 获取这些共同用户评过的其他电影（排除用户已看过的）
  const coUserPlaceholders = coUserIds.map(() => '?').join(',');
  const moviePlaceholders = ratedMovieIds.map(() => '?').join(',');

  // 查询共同用户对其他电影的评分，以及目标用户对该电影的评分（用于排除已看过的）
  const candidateRawData = await query(
    `SELECT um.user_id, um.movie_id, um.rating, tu.rating AS target_rating
     FROM users_movies_behaviors um
     LEFT JOIN users_movies_behaviors tu
       ON tu.movie_id = um.movie_id AND tu.user_id = ? AND tu.behavior_type = 'rate'
     WHERE um.user_id IN (${coUserPlaceholders})
       AND um.movie_id NOT IN (${moviePlaceholders})
       AND um.behavior_type = 'rate' AND um.rating IS NOT NULL
     ORDER BY um.movie_id, um.user_id`,
    [userId, ...coUserIds, ...ratedMovieIds]
  );

  if (candidateRawData.length === 0) {
    console.log(`[Item-Based CF] 无候选电影`);
    return [];
  }

  // 3. 构建数据结构
  // candidateRatings: movieId -> Map<userId, rating>
  const candidateRatings = new Map();
  for (const r of candidateRawData) {
    if (!candidateRatings.has(r.movie_id)) {
      candidateRatings.set(r.movie_id, new Map());
    }
    candidateRatings.get(r.movie_id).set(r.user_id, r.rating);
  }

  // 4. 获取每部用户评分电影的评分用户数据（用于计算 Cosine 相似度）
  const sourceMovieRaw = await query(
    `SELECT movie_id, user_id, rating
     FROM users_movies_behaviors
     WHERE movie_id IN (${moviePlaceholders})
       AND behavior_type = 'rate' AND rating IS NOT NULL`,
    ratedMovieIds
  );

  const sourceMovieRatings = new Map();
  for (const r of sourceMovieRaw) {
    if (!sourceMovieRatings.has(r.movie_id)) {
      sourceMovieRatings.set(r.movie_id, new Map());
    }
    sourceMovieRatings.get(r.movie_id).set(r.user_id, r.rating);
  }

  // 5. 计算 Cosine 相似度并聚合推荐
  const candidateScores = new Map(); // movieId -> { weightedSum, simSum }

  for (const [ratedMovieId, userRating] of targetRatingMap) {
    const sourceRatings = sourceMovieRatings.get(ratedMovieId);
    if (!sourceRatings || sourceRatings.size < MIN_COMMON_ITEMS) continue;

    for (const [candidateMovieId, candidateUserRatings] of candidateRatings) {
      // 计算 Cosine 相似度
      let dot = 0, normA = 0, normB = 0;
      for (const [uid, r] of sourceRatings) {
        normA += r * r;
        if (candidateUserRatings.has(uid)) dot += r * candidateUserRatings.get(uid);
      }
      for (const [, r] of candidateUserRatings) normB += r * r;
      if (normA === 0 || normB === 0) continue;
      const sim = dot / (Math.sqrt(normA) * Math.sqrt(normB));

      if (sim <= SIMILARITY_THRESHOLD) continue;

      if (!candidateScores.has(candidateMovieId)) {
        candidateScores.set(candidateMovieId, { weightedSum: 0, simSum: 0 });
      }
      const entry = candidateScores.get(candidateMovieId);
      entry.weightedSum += sim * userRating;
      entry.simSum += sim;
    }
  }

  // 6. 计算预测评分并排序
  const predictions = [];
  for (const [movieId, { weightedSum, simSum }] of candidateScores) {
    if (simSum > 0) {
      predictions.push({ movieId, predictedRating: weightedSum / simSum });
    }
  }

  predictions.sort((a, b) => b.predictedRating - a.predictedRating);
  const result = deduplicateAndStabilize(predictions.slice(0, topN));

  console.log(`[Item-Based CF] 完成: ${result.length} 个推荐, 耗时 ${Date.now() - startTime}ms`);
  return result;
}

// =============================================
// 混合推荐
// =============================================

async function hybridRecommendation(userId, k = DEFAULT_K, topN = DEFAULT_TOP_N, userWeight = null) {
  // 如果 userWeight 未指定或为 null，使用自适应权重
  if (userWeight === null) {
    userWeight = await getAdaptiveWeight(userId);
  }

  const [userResults, itemResults] = await Promise.all([
    userBasedCF(userId, k, topN * 2),
    itemBasedCF(userId, k, topN * 2)
  ]);

  const itemWeight = 1 - userWeight;
  const scoreMap = new Map();

  for (const r of userResults) {
    scoreMap.set(r.movieId, r.predictedRating * userWeight);
  }
  for (const r of itemResults) {
    scoreMap.set(r.movieId, (scoreMap.get(r.movieId) || 0) + r.predictedRating * itemWeight);
  }

  const merged = [...scoreMap.entries()]
    .map(([movieId, score]) => ({ movieId, predictedRating: score }))
    .sort((a, b) => b.predictedRating - a.predictedRating)
    .slice(0, topN);

  return deduplicateAndStabilize(merged);
}

// =============================================
// API 辅助函数
// =============================================

async function enrichRecommendations(recommendations) {
  if (!recommendations || recommendations.length === 0) return [];

  const movieIds = recommendations.map(r => r.movieId);
  const placeholders = movieIds.map(() => '?').join(',');
  const movies = await query(
    `SELECT id, title, release_year, avg_rating, cover_url FROM movies WHERE id IN (${placeholders})`,
    movieIds
  );

  const movieMap = new Map();
  for (const m of movies) movieMap.set(m.id, m);

  return recommendations
    .map(rec => {
      const movie = movieMap.get(rec.movieId);
      if (!movie) return null;
      return {
        movieId: rec.movieId,
        title: movie.title,
        releaseYear: movie.release_year,
        avgRating: parseFloat(movie.avg_rating) || 0,
        predictedRating: typeof rec.predictedRating === 'number' ? parseFloat(rec.predictedRating.toFixed(2)) : 0,
        coverUrl: movie.cover_url || ''
      };
    })
    .filter(Boolean);
}

function clearCache() {
  cache.clear();
  console.log('[推荐] 缓存已清除');
}

module.exports = {
  userBasedCF,
  itemBasedCF,
  hybridRecommendation,
  findKNearestUsers,
  enrichRecommendations,
  clearCache,
  // 新增导出
  getPopularRecommendations,
  getNewReleaseRecommendations,
  getTrendingRecommendations,
  getContentBasedRecommendations,
  getFallbackRecommendations,
  deduplicateAndStabilize
};