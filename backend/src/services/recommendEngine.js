const fs = require('fs');
const path = require('path');
const { query } = require('../config/db');

const MODELS_DIR = path.join(__dirname, '../../models');
const CACHE_TTL_SECONDS = 60 * 60; // 1 hour

// Model cache
const _models = {};

function loadJsonModel(filename) {
  const filepath = path.join(MODELS_DIR, filename);
  if (!fs.existsSync(filepath)) {
    throw new Error(`Model file not found: ${filepath}`);
  }
  const data = JSON.parse(fs.readFileSync(filepath, 'utf-8'));
  return data;
}

function loadModel(algorithm) {
  if (_models[algorithm]) return _models[algorithm];

  const modelMap = {
    svd: 'svd_model.json',
    user_cf: 'user_cf_model.json',
    item_cf: 'item_cf_model.json',
  };

  const filename = modelMap[algorithm];
  if (!filename) throw new Error(`Unknown algorithm: ${algorithm}`);

  console.log(`[Load model] ${algorithm}: ${filename}`);
  const model = loadJsonModel(filename);
  _models[algorithm] = model;
  return model;
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
  for (const [midStr, mIdx] of Object.entries(movie2idx)) {
    let dot = 0;
    const uFeat = user_features[uIdx];
    const mFeat = movie_features[mIdx];
    for (let i = 0; i < uFeat.length; i++) {
      dot += uFeat[i] * mFeat[i];
    }
    const pred = dot + userMean;
    predictions.push({ movieId: parseInt(midStr, 10), score: pred });
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// User-Based CF Recommendation
// ============================================================
function recommendUserCF(model, userId, topN = 10) {
  const { user_ratings, user_sim_matrix, user_mean_rating, all_movies, n_neighbors } = model;

  const uidStr = String(userId);
  if (!(uidStr in user_ratings)) return [];

  const ratedMovies = new Set(Object.keys(user_ratings[uidStr]));
  const simUsers = user_sim_matrix[uidStr] || {};
  const neighborIds = Object.keys(simUsers)
    .filter(nuid => nuid in user_ratings)
    .sort((a, b) => simUsers[b] - simUsers[a])
    .slice(0, n_neighbors || 30);

  if (neighborIds.length === 0) return [];

  const uidMean = user_mean_rating[uidStr] || 3.5;
  const predictions = [];

  for (const mid of all_movies) {
    const midStr = String(mid);
    if (ratedMovies.has(midStr)) continue;

    let num = 0, den = 0;
    for (const nuid of neighborIds) {
      const rating = user_ratings[nuid]?.[midStr];
      if (rating != null) {
        const nMean = user_mean_rating[nuid] || 3.5;
        num += simUsers[nuid] * (rating - nMean);
        den += Math.abs(simUsers[nuid]);
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
// ============================================================
function recommendItemCF(model, userId, topN = 10) {
  const { user_movies, movie_sim_matrix, movie_ratings, movie_mean_rating, n_neighbors } = model;

  const uidStr = String(userId);
  if (!(uidStr in user_movies)) return [];

  const userRated = new Set(user_movies[uidStr].map(String));
  const allMoviesSet = new Set(Object.keys(movie_ratings));
  const candidateMovies = [...allMoviesSet].filter(m => !userRated.has(m));

  const predictions = [];
  for (const midStr of candidateMovies) {
    const simMovies = movie_sim_matrix[midStr] || {};
    if (Object.keys(simMovies).length === 0) continue;

    const neighbors = [];
    for (const rmidStr of userRated) {
      const sim = simMovies[rmidStr];
      if (sim != null && sim > 0) {
        const rating = movie_ratings[rmidStr]?.[uidStr];
        if (rating != null) {
          neighbors.push({ sim, rating });
        }
      }
    }

    if (neighbors.length === 0) continue;

    neighbors.sort((a, b) => b.sim - a.sim);
    const topNeighbors = neighbors.slice(0, n_neighbors || 30);

    let num = 0, den = 0;
    for (const n of topNeighbors) {
      num += n.sim * n.rating;
      den += Math.abs(n.sim);
    }

    if (den > 0) {
      predictions.push({ movieId: parseInt(midStr, 10), score: num / den });
    }
  }

  predictions.sort((a, b) => b.score - a.score);
  return predictions.slice(0, topN);
}

// ============================================================
// Hybrid Recommendation
// ============================================================
function recommendHybrid(modelSVD, modelUserCF, modelItemCF, userId, topN = 10, weights) {
  if (!weights) weights = { svd: 0.4, user_cf: 0.3, item_cf: 0.3 };

  const nCandidates = topN * 3;

  let svdResults = [];
  let userCFResults = [];
  let itemCFResults = [];

  try { svdResults = recommendSVD(modelSVD, userId, nCandidates); }
  catch (e) { console.error('  SVD recommend failed:', e.message); }

  try { userCFResults = recommendUserCF(modelUserCF, userId, nCandidates); }
  catch (e) { console.error('  User-CF recommend failed:', e.message); }

  try { itemCFResults = recommendItemCF(modelItemCF, userId, nCandidates); }
  catch (e) { console.error('  Item-CF recommend failed:', e.message); }

  const scoreMap = {};
  const weightSumMap = {};

  const addScores = (results, w) => {
    for (const r of results) {
      const key = r.movieId;
      scoreMap[key] = (scoreMap[key] || 0) + r.score * w;
      weightSumMap[key] = (weightSumMap[key] || 0) + w;
    }
  };

  addScores(svdResults, weights.svd);
  addScores(userCFResults, weights.user_cf);
  addScores(itemCFResults, weights.item_cf);

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
      "SELECT recommend_movies, algorithm, updated_at FROM users_recommendations WHERE user_id = ? AND algorithm = ? ORDER BY updated_at DESC LIMIT 1",
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

async function saveResultToCache(userId, recommendations, algorithm) {
  try {
    const items = recommendations.map(r => ({
      movie_id: r.movieId,
      score: Math.round(r.score * 10000) / 10000
    }));
    const recommendJson = JSON.stringify(items);

    await query(
      "INSERT INTO users_recommendations (user_id, algorithm, recommend_movies, updated_at) VALUES (?, ?, ?, NOW()) ON DUPLICATE KEY UPDATE recommend_movies = VALUES(recommend_movies), updated_at = NOW()",
      [userId, algorithm, recommendJson]
    );
    console.log(`[Cache] Saved user ${userId}, algo: ${algorithm}, items: ${items.length}`);
  } catch (e) {
    console.error(`[Cache] Save failed: ${e.message}`);
  }
}

// ============================================================
// Main Entry Point
// ============================================================
async function getRecommendations(userId, algorithm = 'hybrid', topN = 10, skipCache = false) {
  if (topN < 1 || topN > 100) topN = 10;

  // Step 1: Try cache
  if (!skipCache) {
    const cached = await getCachedRecommendation(userId, algorithm);
    if (cached) {
      return {
        userId,
        algorithm: cached.algorithm,
        topN: Math.min(topN, cached.items.length),
        elapsed: 0.001,
        total: cached.items.length,
        recommendations: cached.items.slice(0, topN).map(item => ({
          movieId: item.movie_id,
          predictedRating: item.score
        })),
        fromCache: true
      };
    }
  }

  // Step 2: Real-time compute
  const startTime = Date.now();

  let results;
  if (algorithm === 'hybrid') {
    const modelSVD = loadModel('svd');
    const modelUserCF = loadModel('user_cf');
    const modelItemCF = loadModel('item_cf');
    results = recommendHybrid(modelSVD, modelUserCF, modelItemCF, userId, topN);
  } else if (algorithm === 'svd') {
    results = recommendSVD(loadModel('svd'), userId, topN);
  } else if (algorithm === 'user_cf') {
    results = recommendUserCF(loadModel('user_cf'), userId, topN);
  } else if (algorithm === 'item_cf') {
    results = recommendItemCF(loadModel('item_cf'), userId, topN);
  } else {
    throw new Error(`Unknown algorithm: ${algorithm}`);
  }

  const elapsed = (Date.now() - startTime) / 1000;

  const recommendations = results.map(r => ({
    movieId: r.movieId,
    predictedRating: Math.round(r.score * 10000) / 10000
  }));

  // Step 3: Write back to cache (async)
  if (results.length >= topN / 2) {
    saveResultToCache(userId, results, algorithm).catch(e =>
      console.error('[Cache] Async save failed:', e.message)
    );
  }

  return {
    userId,
    algorithm,
    topN,
    elapsed: Math.round(elapsed * 1000) / 1000,
    total: recommendations.length,
    recommendations,
    fromCache: false
  };
}

module.exports = { getRecommendations, loadModel };