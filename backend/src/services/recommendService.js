/**
 * recommendService.js - KNN 协同过滤推荐算法服务（SQL 驱动版）
 * 
 * 针对 MovieLens 25M 数据集优化：
 * - 不将全量数据加载到内存，而是使用 SQL 聚合
 * - User-Based CF：先通过 SQL 筛选共同评分超过阈值的候选用户，再计算 Pearson 相似度
 * - Item-Based CF：通过 SQL 计算与用户已评分电影相关的候选电影相似度
 * 
 * 性能优化策略：
 * - 使用 SQL JOIN + GROUP BY 替代全量内存加载
 * - 批量加载必要数据到内存（仅邻居用户或候选电影）
 * - 使用 30 分钟缓存放热数据
 */

const { query } = require('../config/db');

// =============================================
// 配置常量
// =============================================
const DEFAULT_K = 10;            // KNN 邻居数
const DEFAULT_TOP_N = 10;        // 推荐结果数
const MIN_COMMON_ITEMS = 3;      // 用户间最小共同评分项数
const MIN_RATINGS_FOR_USER = 5;  // 用户最少评分数量
const CACHE_TTL = 30 * 60 * 1000; // 缓存30分钟

// =============================================
// 轻量缓存（仅缓存频繁访问的元数据）
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

  // 1. 找邻居
  const neighbors = await findKNearestUsers(userId, k);
  if (neighbors.length === 0) return [];

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
  const result = predictions.slice(0, topN);

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
    console.log(`[Item-Based CF] 用户 ${userId} 评分数 ${targetRatings.length} < ${MIN_RATINGS_FOR_USER}`);
    return [];
  }

  const targetRatingMap = new Map();
  for (const r of targetRatings) {
    targetRatingMap.set(r.movie_id, r.rating);
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
  // 使用子查询过滤出目标用户未看过的电影
  const candidateRawData = await query(
    `SELECT um.user_id, um.movie_id, um.rating, 
            tu.rating AS target_rating
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
  // sourceMovieRatings: movieId -> Map<userId, rating>
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

      if (sim <= 0.01) continue;

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
  const result = predictions.slice(0, topN);

  console.log(`[Item-Based CF] 完成: ${result.length} 个推荐, 耗时 ${Date.now() - startTime}ms`);
  return result;
}

// =============================================
// 混合推荐
// =============================================

async function hybridRecommendation(userId, k = DEFAULT_K, topN = DEFAULT_TOP_N, userWeight = 0.5) {
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

  return [...scoreMap.entries()]
    .map(([movieId, score]) => ({ movieId, predictedRating: score }))
    .sort((a, b) => b.predictedRating - a.predictedRating)
    .slice(0, topN);
}

// =============================================
// API 辅助函数
// =============================================

async function enrichRecommendations(recommendations) {
  if (recommendations.length === 0) return [];

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
        predictedRating: parseFloat(rec.predictedRating.toFixed(2)),
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
  clearCache
};