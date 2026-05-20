const fs = require('fs');
const path = require('path');
const { query } = require('../config/db');

const MODELS_DIR = path.join(__dirname, '../../models');
const CACHE_TTL_SECONDS = 60 * 60; // 1 hour

// Model cache
const _models = {};
// 并发加载保护锁：防止 warmupModels() 和首次请求同时加载同一模型
const _loadingPromises = {};

// ============================================================
// 模型文件名映射表（扩展支持全部 7+ 个模型）
// ============================================================
const MODEL_FILE_MAP = {
  svd: 'svd_model.json',
  user_cf: 'user_cf_traditional_model.json',      // 向后兼容旧名
  user_cf_traditional: 'user_cf_traditional_model.json',
  user_cf_improved: 'user_cf_improved_model.json',
  item_cf: 'item_cf_traditional_model.json',      // 向后兼容旧名
  item_cf_traditional: 'item_cf_traditional_model.json',
  item_cf_improved: 'item_cf_improved_model.json',
  slope_one_traditional: 'slope_one_traditional_model.json',
  slope_one_improved: 'slope_one_improved_model.json',
  turbo_cf: 'turbo_cf_model.json',
};

/**
 * 异步加载 JSON 模型，大文件不阻塞事件循环
 */
function loadJsonModelAsync(filename) {
  return new Promise((resolve, reject) => {
    const filepath = path.join(MODELS_DIR, filename);
    if (!fs.existsSync(filepath)) {
      return reject(new Error(`Model file not found: ${filepath}`));
    }
    const stat = fs.statSync(filepath);
    console.log(`[模型加载] ${filename} (${(stat.size / 1024 / 1024).toFixed(1)} MB)`);

    // 使用流式解析避免阻塞事件循环
    const chunks = [];
    const stream = fs.createReadStream(filepath, { encoding: 'utf-8', highWaterMark: 64 * 1024 });
    stream.on('data', chunk => chunks.push(chunk));
    stream.on('end', () => {
      const startTime = Date.now();
      const data = JSON.parse(chunks.join(''));
      console.log(`[模型加载] ${filename} 解析完成 (${Date.now() - startTime}ms)`);
      resolve(data);
    });
    stream.on('error', reject);
  });
}

function loadJsonModelSync(filename) {
  const filepath = path.join(MODELS_DIR, filename);
  if (!fs.existsSync(filepath)) {
    throw new Error(`Model file not found: ${filepath}`);
  }
  const data = JSON.parse(fs.readFileSync(filepath, 'utf-8'));
  return data;
}

async function loadModelAsync(algorithm) {
  if (_models[algorithm]) return _models[algorithm];

  const filename = MODEL_FILE_MAP[algorithm];
  if (!filename) throw new Error(`Unknown algorithm: ${algorithm}`);

  if (_loadingPromises[algorithm]) {
    console.log(`[Load model] ${algorithm}: ${filename} (awaiting existing load)`);
    return _loadingPromises[algorithm];
  }

  console.log(`[Load model] ${algorithm}: ${filename}`);
  _loadingPromises[algorithm] = loadJsonModelAsync(filename).then(model => {
    _models[algorithm] = model;
    delete _loadingPromises[algorithm];
    return model;
  });

  return _loadingPromises[algorithm];
}

function loadModel(algorithm) {
  if (_models[algorithm]) return _models[algorithm];

  const filename = MODEL_FILE_MAP[algorithm];
  if (!filename) throw new Error(`Unknown algorithm: ${algorithm}`);

  console.log(`[Load model sync] ${algorithm}: ${filename}`);
  const model = loadJsonModelSync(filename);
  _models[algorithm] = model;
  return model;
}

/**
 * 预热：在服务器启动时异步预加载所有模型
 */
async function warmupModels() {
  const algorithms = Object.keys(MODEL_FILE_MAP);
  console.log('[预热] 开始预加载推荐模型...');
  for (const algo of algorithms) {
    try {
      await loadModelAsync(algo);
      console.log(`[预热] ${algo} 模型加载完成`);
    } catch (e) {
      console.error(`[预热] ${algo} 模型加载失败:`, e.message);
    }
  }
  console.log('[预热] 所有模型加载完成');
}

// ============================================================
// SVD Recommendation
// ============================================================
function recommendSVD(model, userId, topN = 10) {
  const { user2idx, movie2idx, user_features, movie_features, user_means } = model;

  const uidStr = String(userId);
  if (!(uidStr in user2idx)) return [];

  const uIdx = user2idx[uidStr];
  const userMean = user_means[uIdx] || 0;

  const predictions = [];
  const entries = Object.entries(movie2idx);

  const maxCandidates = Math.min(entries.length, 10000);
  const sampledEntries = entries.slice(0, maxCandidates);

  for (const [midStr, mIdx] of sampledEntries) {
    let dot = 0;
    const uFeat = user_features[uIdx];
    const mFeat = movie_features[mIdx];
    if (!uFeat || !mFeat) continue;
    const len = Math.min(uFeat.length, mFeat.length);
    for (let i = 0; i < len; i++) {
      dot += uFeat[i] * mFeat[i];
    }
    const pred = dot + userMean;
    predictions.push({ movieId: parseInt(midStr, 10), score: pred });
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// User-Based CF Recommendation (通用: traditional & improved)
// 数据结构来自 export_models_to_json.py:
//   user_neighbors: {userIdStr: [[neighborId, sim], ...]}
//   user_movies: {userIdStr: [movieId, ...]}
//   user_means: {userIdStr: meanRating}
//   all_movies: [movieId, ...]
// ============================================================
function recommendUserCF(model, userId, topN = 10) {
  const { user_neighbors, user_movies, user_means, all_movies } = model;

  const uidStr = String(userId);
  if (!(uidStr in user_movies)) return [];

  const ratedMovies = new Set((user_movies[uidStr] || []).map(String));
  const neighbors = user_neighbors[uidStr] || [];

  if (neighbors.length === 0) return [];

  const uidMean = user_means[uidStr] || 3.5;
  const n_neighbors = model.n_neighbors || 30;
  const topNeighbors = neighbors.slice(0, n_neighbors);

  // 限制候选电影数
  const maxMovieCandidates = Math.min((all_movies || []).length, 5000);
  const movieCandidates = (all_movies || []).slice(0, maxMovieCandidates);
  const predictions = [];

  for (const mid of movieCandidates) {
    const midStr = String(mid);
    if (ratedMovies.has(midStr)) continue;

    let num = 0, den = 0;
    for (const [nuid, sim] of topNeighbors) {
      const nuidStr = String(nuid);
      const nMovies = user_movies[nuidStr];
      if (!nMovies) continue;
      // 检查邻居是否评分过该电影
      if (nMovies.includes(mid)) {
        // 使用 user_means + weighted avg
        const nMean = user_means[nuidStr] || 3.5;
        // 由于我们没有邻居的具体评分值，使用简化的方式
        // 用邻居的平均分 + 调整量来估算
        num += sim;
        den += Math.abs(sim);
      }
    }

    if (den > 0) {
      predictions.push({ movieId: mid, score: uidMean + num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// User-CF Improved Recommendation (含 alpha 参数)
// 同 traditional 结构，额外有 user_std 和 alpha
// ============================================================
function recommendUserCFImproved(model, userId, topN = 10) {
  const { user_neighbors, user_movies, user_means, all_movies, alpha } = model;

  const uidStr = String(userId);
  if (!(uidStr in user_movies)) return [];

  const ratedMovies = new Set((user_movies[uidStr] || []).map(String));
  const neighbors = user_neighbors[uidStr] || [];

  if (neighbors.length === 0) return [];

  const uidMean = user_means[uidStr] || 3.5;
  const n_neighbors = model.n_neighbors || 30;
  const topNeighbors = neighbors.slice(0, n_neighbors);

  const maxMovieCandidates = Math.min((all_movies || []).length, 5000);
  const movieCandidates = (all_movies || []).slice(0, maxMovieCandidates);
  const predictions = [];

  // improved 版本使用 alpha 调整权重
  const a = alpha !== undefined ? alpha : 0.5;

  for (const mid of movieCandidates) {
    const midStr = String(mid);
    if (ratedMovies.has(midStr)) continue;

    let num = 0, den = 0;
    let commonCount = 0;
    for (const [nuid, sim] of topNeighbors) {
      const nuidStr = String(nuid);
      const nMovies = user_movies[nuidStr];
      if (!nMovies) continue;
      if (nMovies.includes(mid)) {
        const nMean = user_means[nuidStr] || 3.5;
        // improved: 使用调整的相似度权重
        const adjustedSim = sim * (a + (1 - a) * (1 / (1 + commonCount)));
        num += adjustedSim;
        den += Math.abs(adjustedSim);
        commonCount++;
      }
    }

    if (den > 0) {
      predictions.push({ movieId: mid, score: uidMean + num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// Item-Based CF Recommendation
// 数据结构:
//   movie_sim_matrix: {movieIdStr: {neighborMovieIdStr: sim, ...}}
//   all_movies: [movieId, ...]
// ============================================================
function recommendItemCF(model, userId, topN = 10) {
  const { movie_sim_matrix, user_movies, all_movies } = model;

  // 用户评分过的电影列表
  const uidStr = String(userId);
  let userRatedMovies = [];
  if (user_movies && uidStr in user_movies) {
    userRatedMovies = user_movies[uidStr].map(String);
  }
  const userRatedSet = new Set(userRatedMovies);

  // 如果没有用户评分记录，尝试用 all_movies
  if (userRatedMovies.length === 0) {
    return [];
  }

  const candidateMoviesList = all_movies || Object.keys(movie_sim_matrix || {});
  const maxCandidates = Math.min(candidateMoviesList.length, 5000);
  const candidateMovies = candidateMoviesList
    .filter(m => {
      const mStr = String(m);
      return !userRatedSet.has(mStr);
    })
    .slice(0, maxCandidates);

  const predictions = [];

  for (const mid of candidateMovies) {
    const midStr = String(mid);
    const simDict = movie_sim_matrix[midStr];
    if (!simDict || Object.keys(simDict).length === 0) continue;

    let num = 0, den = 0;
    let count = 0;
    const n_neighbors = model.n_neighbors || 30;

    for (const ratedMidStr of userRatedMovies) {
      const sim = simDict[ratedMidStr];
      if (sim !== undefined && sim > 0) {
        num += sim;
        den += Math.abs(sim);
        count++;
        if (count >= n_neighbors) break;
      }
    }

    if (den > 0) {
      predictions.push({ movieId: parseInt(midStr, 10), score: num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// Item-CF Improved Recommendation (含 user_means 偏置校正)
// ============================================================
function recommendItemCFImproved(model, userId, topN = 10) {
  const { movie_sim_matrix, user_movies, user_means, all_movies } = model;

  const uidStr = String(userId);
  let userRatedMovies = [];
  if (user_movies && uidStr in user_movies) {
    userRatedMovies = user_movies[uidStr].map(String);
  }
  const userRatedSet = new Set(userRatedMovies);

  if (userRatedMovies.length === 0) return [];

  const uidMean = (user_means && user_means[uidStr]) || 3.5;

  const candidateMoviesList = all_movies || Object.keys(movie_sim_matrix || {});
  const maxCandidates = Math.min(candidateMoviesList.length, 5000);
  const candidateMovies = candidateMoviesList
    .filter(m => {
      const mStr = String(m);
      return !userRatedSet.has(mStr);
    })
    .slice(0, maxCandidates);

  const predictions = [];

  for (const mid of candidateMovies) {
    const midStr = String(mid);
    const simDict = movie_sim_matrix[midStr];
    if (!simDict || Object.keys(simDict).length === 0) continue;

    let num = 0, den = 0;
    let count = 0;
    const n_neighbors = model.n_neighbors || 30;

    for (const ratedMidStr of userRatedMovies) {
      const sim = simDict[ratedMidStr];
      if (sim !== undefined && sim > 0) {
        // improved: 使用用户均值的偏置校正
        num += sim * (sim + uidMean * 0.1);
        den += Math.abs(sim);
        count++;
        if (count >= n_neighbors) break;
      }
    }

    if (den > 0) {
      predictions.push({ movieId: parseInt(midStr, 10), score: num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// Slope-One Traditional Recommendation
// 数据结构:
//   item_deviations: {movieIdStr: {otherMovieIdStr: deviation, ...}}
//   all_movies: [movieId, ...]
// ============================================================
function recommendSlopeOne(model, userId, topN = 10) {
  const { item_deviations, user_movies, all_movies } = model;

  const uidStr = String(userId);
  let userRatedMovies = [];
  if (user_movies && uidStr in user_movies) {
    userRatedMovies = user_movies[uidStr].map(String);
  }
  const userRatedSet = new Set(userRatedMovies);

  if (userRatedMovies.length === 0) return [];

  const candidateMoviesList = all_movies || Object.keys(item_deviations || {});
  const maxCandidates = Math.min(candidateMoviesList.length, 2000); // slope-one 计算量大
  const candidateMovies = candidateMoviesList
    .filter(m => {
      const mStr = String(m);
      return !userRatedSet.has(mStr);
    })
    .slice(0, maxCandidates);

  const predictions = [];

  for (const mid of candidateMovies) {
    const midStr = String(mid);
    const devDict = item_deviations[midStr];
    if (!devDict || Object.keys(devDict).length === 0) continue;

    let num = 0, den = 0;
    for (const ratedMidStr of userRatedMovies) {
      const dev = devDict[ratedMidStr];
      if (dev !== undefined) {
        num += dev;
        den += 1;
      }
    }

    if (den > 0) {
      predictions.push({ movieId: parseInt(midStr, 10), score: num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// Slope-One Improved Recommendation (基于邻域筛选)
// 数据结构:
//   item_deviations: {movieIdStr: {otherMovieIdStr: deviation, ...}}
//   user_neighbors: {userIdStr: [[neighborId, sim], ...]}
//   user_movies: {userIdStr: [movieId, ...]}
//   user_means: {userIdStr: meanRating}
//   all_movies: [movieId, ...]
// ============================================================
function recommendSlopeOneImproved(model, userId, topN = 10) {
  const { item_deviations, user_neighbors, user_movies, user_means, all_movies } = model;

  const uidStr = String(userId);
  if (!(uidStr in user_movies)) return [];

  const userRatedList = (user_movies[uidStr] || []).map(String);
  const ratedMovies = new Set(userRatedList);

  if (userRatedList.length === 0) return [];

  const neighbors = user_neighbors[uidStr] || [];
  if (neighbors.length === 0) {
    return recommendSlopeOne(model, userId, topN);
  }

  const n_neighbors = model.n_neighbors || 30;
  const topNeighbors = neighbors.slice(0, n_neighbors);

  // 构建邻域电影集合：邻居评分过的所有电影
  const neighborMovieSet = new Set();
  for (const [nuid] of topNeighbors) {
    const nMovies = user_movies[String(nuid)];
    if (nMovies) {
      for (const mid of nMovies) {
        neighborMovieSet.add(String(mid));
      }
    }
  }

  // 筛选用户评分电影中也在邻域集合中的电影
  const filteredRatedMovies = userRatedList.filter(m => neighborMovieSet.has(m));
  if (filteredRatedMovies.length === 0) {
    return recommendSlopeOne(model, userId, topN);
  }

  const uidMean = user_means[uidStr] || 3.5;
  const candidateMoviesList = all_movies || Object.keys(item_deviations || {});
  const maxCandidates = Math.min(candidateMoviesList.length, 2000);
  const candidateMovies = candidateMoviesList
    .filter(m => {
      const mStr = String(m);
      return !ratedMovies.has(mStr);
    })
    .slice(0, maxCandidates);

  const predictions = [];

  for (const mid of candidateMovies) {
    const midStr = String(mid);
    const devDict = item_deviations[midStr];
    if (!devDict || Object.keys(devDict).length === 0) continue;

    let num = 0, den = 0;
    for (const ratedMidStr of filteredRatedMovies) {
      const dev = devDict[ratedMidStr];
      if (dev !== undefined) {
        num += uidMean + dev;
        den += 1;
      }
    }

    if (den > 0) {
      predictions.push({ movieId: parseInt(midStr, 10), score: num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// Turbo-CF Recommendation (K-Means 聚类加速协同过滤)
// ============================================================
function recommendTurboCF(model, userId, topN = 10) {
  const { user_neighbors, user_movies, user_means, n_neighbors } = model;

  const uidStr = String(userId);
  if (!(uidStr in user_movies)) return [];

  const ratedMovies = new Set((user_movies[uidStr] || []).map(String));
  const neighbors = user_neighbors[uidStr] || [];

  if (neighbors.length === 0) return [];

  const uidMean = user_means[uidStr] || 3.5;
  const neighborLimit = Math.min(neighbors.length, n_neighbors || 30);
  const topNeighbors = neighbors.slice(0, neighborLimit);

  // 收集邻居评分过的所有候选电影
  const candidateScores = {};

  for (const [nuid, sim] of topNeighbors) {
    const nuidStr = String(nuid);
    const nMovies = user_movies[nuidStr];
    if (!nMovies) continue;

    for (const mid of nMovies) {
      const midStr = String(mid);
      if (ratedMovies.has(midStr)) continue;
      if (!candidateScores[midStr]) candidateScores[midStr] = { num: 0, den: 0 };
      candidateScores[midStr].num += sim;
      candidateScores[midStr].den += Math.abs(sim);
    }
  }

  const predictions = [];
  for (const [midStr, { num, den }] of Object.entries(candidateScores)) {
    if (den > 0) {
      predictions.push({
        movieId: parseInt(midStr, 10),
        score: uidMean + num / den
      });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// 推荐函数调度表
// ============================================================
const RECOMMEND_FUNCTIONS = {
  svd: recommendSVD,
  user_cf: recommendUserCF,
  user_cf_traditional: recommendUserCF,
  user_cf_improved: recommendUserCFImproved,
  item_cf: recommendItemCF,
  item_cf_traditional: recommendItemCF,
  item_cf_improved: recommendItemCFImproved,
  slope_one_traditional: recommendSlopeOne,
  slope_one_improved: recommendSlopeOneImproved,
  turbo_cf: recommendTurboCF,
};

// ============================================================
// Hybrid Recommendation (组合所有可用算法)
// ============================================================
function recommendHybridAll(modelMap, userId, topN = 10) {
  // 默认权重: svd + user_cf + item_cf + turbo_cf + slope_one
  const weights = {
    svd: 0.22,
    user_cf: 0.13,
    item_cf: 0.13,
    turbo_cf: 0.18,
    slope_one_traditional: 0.08,
    slope_one_improved: 0.08,
    user_cf_improved: 0.08,
    item_cf_improved: 0.10,
  };

  // 旧版兼容: 只有4个基础算法时的混合
  const legacyWeights = {
    svd: 0.35,
    user_cf: 0.20,
    item_cf: 0.25,
    turbo_cf: 0.20,
  };

  const nCandidates = topN * 3;
  const scoreMap = {};
  const weightSumMap = {};

  // 确定使用哪些算法: 检查已加载的模型
  const availableAlgos = Object.keys(weights).filter(algo => modelMap[algo] || _models[algo]);
  const hasAdvancedAlgos = availableAlgos.includes('slope_one_traditional') ||
    availableAlgos.includes('slope_one_improved') ||
    availableAlgos.includes('user_cf_improved');

  const activeWeights = hasAdvancedAlgos ? weights : legacyWeights;

  const addScores = (results, w) => {
    for (const r of results) {
      const key = r.movieId;
      scoreMap[key] = (scoreMap[key] || 0) + r.score * w;
      weightSumMap[key] = (weightSumMap[key] || 0) + w;
    }
  };

  for (const [algo, weight] of Object.entries(activeWeights)) {
    const model = modelMap[algo] || _models[algo];
    const func = RECOMMEND_FUNCTIONS[algo];
    if (model && func) {
      try {
        const results = func(model, userId, nCandidates);
        if (results.length > 0) {
          addScores(results, weight);
        }
      } catch (e) {
        console.error(`  ${algo} recommend failed:`, e.message);
      }
    }
  }

  const finalScores = [];
  for (const [mid, totalScore] of Object.entries(scoreMap)) {
    if (weightSumMap[mid] > 0) {
      finalScores.push({ movieId: parseInt(mid, 10), score: totalScore / weightSumMap[mid] });
    }
  }

  finalScores.sort((a, b) => b.score - a.score);
  return finalScores.slice(0, topN);
}

// ============================================================
// MySQL Cache Layer
// ============================================================
async function getCachedRecommendation(userId, algorithm) {
  try {
    const rows = await query(
      "SELECT recommend_movies, algorithm, updated_at FROM user_recommendation_caches WHERE user_id = ? AND algorithm = ? LIMIT 1",
      [userId, algorithm]
    );
    if (rows.length === 0) return null;

    const row = rows[0];
    const updatedAt = new Date(row.updated_at).getTime();
    const ageSeconds = (Date.now() - updatedAt) / 1000;

    if (ageSeconds > CACHE_TTL_SECONDS) {
      console.log(`[Cache] User ${userId} cache expired (${ageSeconds.toFixed(0)}s > ${CACHE_TTL_SECONDS}s)`);
      return null;
    }

    const items = typeof row.recommend_movies === 'string'
      ? JSON.parse(row.recommend_movies)
      : row.recommend_movies;

    console.log(`[Cache] Hit user ${userId}, algo: ${row.algorithm}, items: ${items.length}`);
    return { items, algorithm: row.algorithm };
  } catch (e) {
    console.error(`[Cache] Query failed: ${e.message}`);
    return null;
  }
}

/**
 * 查询电影相似度缓存（item_similarity_caches）
 */
async function getCachedMovieSimilarity(movieId, algorithm = 'item_cf') {
  try {
    const rows = await query(
      "SELECT similar_movies, algorithm, updated_at FROM item_similarity_caches WHERE movie_id = ? AND algorithm = ? LIMIT 1",
      [movieId, algorithm]
    );
    if (rows.length === 0) return null;

    const row = rows[0];
    const items = typeof row.similar_movies === 'string'
      ? JSON.parse(row.similar_movies)
      : row.similar_movies;

    return { items, algorithm: row.algorithm, movieId };
  } catch (e) {
    console.error(`[Cache] Movie similarity query failed: ${e.message}`);
    return null;
  }
}

async function saveResultToCache(userId, recommendations, algorithm) {
  try {
    const items = recommendations.map(r => ({
      movie_id: r.movieId,
      score: Math.round(r.score * 10000) / 10000
    }));
    const recommendJson = JSON.stringify(items);

    await query(
      "REPLACE INTO user_recommendation_caches (user_id, algorithm, recommend_movies, updated_at) VALUES (?, ?, ?, NOW())",
      [userId, algorithm, recommendJson]
    );
    console.log(`[Cache] Saved user ${userId}, algo: ${algorithm}, items: ${items.length}`);
  } catch (e) {
    console.error(`[Cache] Save failed: ${e.message}`);
  }
}

// ============================================================
// 计算推荐结果（单算法）
// ============================================================
async function computeSingleAlgorithm(algorithm, userId, topN) {
  const model = await loadModelAsync(algorithm);
  const func = RECOMMEND_FUNCTIONS[algorithm];
  if (!func) throw new Error(`No recommend function for algorithm: ${algorithm}`);
  return func(model, userId, topN);
}

// ============================================================
// Main Entry Point
// ============================================================
/**
 * 根据实验策略解析实际使用的算法
 * @param {string} defaultAlgo 用户请求的算法
 * @param {Array|null} experiments 中间件注入的实验命中信息
 * @returns {string} 实际使用的算法名
 */
function resolveAlgorithmFromExperiment(defaultAlgo, experiments) {
  if (!experiments || !Array.isArray(experiments) || experiments.length === 0) {
    return defaultAlgo;
  }

  const firstExp = experiments[0];
  const strategyAlgorithm = firstExp.algorithm;

  if (!strategyAlgorithm || typeof strategyAlgorithm !== 'string') {
    return defaultAlgo;
  }

  const algoMap = {
    svd: 'svd',
    svd_v2: 'svd',
    user_cf: 'user_cf',
    user_cf_traditional: 'user_cf_traditional',
    user_cf_improved: 'user_cf_improved',
    user_cf_v2: 'user_cf_improved',
    item_cf: 'item_cf',
    item_cf_traditional: 'item_cf_traditional',
    item_cf_improved: 'item_cf_improved',
    slope_one_traditional: 'slope_one_traditional',
    slope_one_improved: 'slope_one_improved',
    turbo_cf: 'turbo_cf',
    hybrid: 'hybrid',
    hybrid_v2: 'hybrid',
    hybrid_v3: 'hybrid'
  };

  const resolved = algoMap[strategyAlgorithm];
  if (resolved && RECOMMEND_FUNCTIONS[resolved]) {
    console.log(`[Engine] A/B实验路由: 实验"${firstExp.experimentName}" 策略"${firstExp.strategyName}" → 算法 ${resolved}`);
    return resolved;
  }

  console.log(`[Engine] A/B实验策略"${strategyAlgorithm}"未匹配到推荐函数，使用默认算法 ${defaultAlgo}`);
  return defaultAlgo;
}

async function getRecommendations(userId, algorithm = 'hybrid', topN = 10, skipCache = false) {
  return getRecommendationsV2(userId, algorithm, topN, { skipCache });
}

/**
 * 推荐入口 V2 — 支持实验策略路由
 * @param {number} userId
 * @param {string} algorithm 默认算法
 * @param {number} topN
 * @param {Object} options
 * @param {boolean} options.skipCache
 * @param {Array} options.experiment 中间件注入的实验命中信息
 */
async function getRecommendationsV2(userId, algorithm = 'hybrid', topN = 10, options = {}) {
  const { skipCache = false, experiment = null } = options;

  if (topN < 1 || topN > 100) topN = 10;

  const resolvedAlgo = resolveAlgorithmFromExperiment(algorithm, experiment);

  // Step 1: Try cache
  if (!skipCache) {
    const cached = await getCachedRecommendation(userId, resolvedAlgo);
    if (cached) {
      return {
        userId,
        algorithm: resolvedAlgo,
        topN: Math.min(topN, cached.items.length),
        elapsed: 0.001,
        total: cached.items.length,
        recommendations: cached.items.slice(0, topN).map(item => ({
          movieId: item.movie_id,
          predictedRating: item.score
        })),
        fromCache: true,
        experiment
      };
    }
  }

  // Step 2: Real-time compute
  const startTime = Date.now();
  let results;

  if (resolvedAlgo === 'hybrid') {
    const modelPromises = {};
    for (const algo of Object.keys(MODEL_FILE_MAP)) {
      if (_models[algo]) {
        modelPromises[algo] = Promise.resolve(_models[algo]);
      } else {
        modelPromises[algo] = loadModelAsync(algo).catch(() => null);
      }
    }
    const models = {};
    const entries = Object.entries(modelPromises);
    for (const [algo, promise] of entries) {
      try {
        models[algo] = await promise;
      } catch (e) {
        // 跳过加载失败的模型
      }
    }
    results = recommendHybridAll(models, userId, topN);
  } else if (RECOMMEND_FUNCTIONS[resolvedAlgo]) {
    results = await computeSingleAlgorithm(resolvedAlgo, userId, topN);
  } else {
    throw new Error(`Unknown algorithm: ${resolvedAlgo}`);
  }

  const elapsed = (Date.now() - startTime) / 1000;

  const recommendations = results.map(r => ({
    movieId: r.movieId,
    predictedRating: Math.round(r.score * 10000) / 10000
  }));

  if (results.length >= topN / 2) {
    saveResultToCache(userId, results, resolvedAlgo).catch(e =>
      console.error('[Cache] Async save failed:', e.message)
    );
  }

  return {
    userId,
    algorithm: resolvedAlgo,
    topN,
    elapsed: Math.round(elapsed * 1000) / 1000,
    total: recommendations.length,
    recommendations,
    fromCache: false,
    experiment
  };
}

module.exports = { getRecommendations, getRecommendationsV2, loadModel, warmupModels };