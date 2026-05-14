/**
 * recommendController.js - 推荐系统 API 控制器
 * 
 * 提供 9 个 API 端点控制器：
 * 1. userBasedRecommend  - User-Based CF
 * 2. itemBasedRecommend  - Item-Based CF
 * 3. hybridRecommend     - 混合推荐（自适应权重）
 * 4. getUserNeighbors    - 邻居查询
 * 5. popularRecommend    - 热门推荐
 * 6. newReleaseRecommend - 新片推荐
 * 7. trendingRecommend   - 趋势推荐
 * 8. contentBasedRecommend - 基于内容推荐（Qdrant）
 * 9. clearCache          - 清除缓存
 * 
 * AI 模型推荐（原 Python 代理）已集成到 Node.js：
 * - /api/recommend/ai -> 使用预训练模型的推荐引擎
 * - /api/recommend/ai/models -> 列出可用算法
 * - /api/recommend/ai/health -> 健康检查
 */
const recommendService = require('../services/recommendService');
const recommendEngine = require('../services/recommendEngine');

// =============================================
// 配置常量
// =============================================
const REQUEST_TIMEOUT = 120000; // 120秒超时
const MAX_PAGE_SIZE = 100;      // 单页最大条数
const MAX_K = 200;              // 邻居数量上限
const MAX_TOP_N = 200;          // 推荐数上限

// 可用算法列表
const AVAILABLE_ALGORITHMS = {
  'hybrid': { name: '混合推荐 (Hybrid CF)', description: '融合 User-Based CF 与 Item-Based CF，支持自适应权重' },
  'user_cf': { name: '基于用户的协同过滤 (User-Based CF)', description: '基于相似用户的评分预测' },
  'item_cf': { name: '基于物品的协同过滤 (Item-Based CF)', description: '基于相似物品的评分预测' },
  'turbo_cf': { name: 'Turbo-CF (K-Means 聚类加速协同过滤)', description: 'K-Means 用户聚类压缩邻居搜索空间，O(U) → O(U/C) 加速' },
  'popular': { name: '热门推荐 (Popular)', description: '基于评分数量和均值的全局热门' },
  'content_based': { name: '基于内容的推荐 (Content-Based)', description: '基于 Qdrant 向量的内容相似度推荐' }
};

// =============================================
// 超时保护
// =============================================

/**
 * 为异步操作添加超时控制
 */
function withTimeout(promise, ms = REQUEST_TIMEOUT) {
  const timeoutPromise = new Promise((_, reject) =>
    setTimeout(() => reject(new Error(`请求超时 (${ms / 1000}秒)`)), ms)
  );
  return Promise.race([promise, timeoutPromise]);
}

// =============================================
// 参数校验工具
// =============================================

/**
 * 校验并规范化用户 ID
 */
function validateUserId(userId) {
  const id = parseInt(userId);
  if (isNaN(id) || id <= 0) {
    return { valid: false, message: '无效的用户ID' };
  }
  return { valid: true, value: id };
}

/**
 * 校验并规范化分页参数
 */
function validatePagination(page, pageSize) {
  let p = parseInt(page) || 1;
  let ps = parseInt(pageSize) || 20;
  if (p < 1) p = 1;
  if (ps < 1) ps = 1;
  if (ps > MAX_PAGE_SIZE) ps = MAX_PAGE_SIZE;
  return { page: p, pageSize: ps };
}

/**
 * 校验并规范化 KNN 参数
 */
function validateKnnParams(k, topN) {
  let kv = parseInt(k) || 20;
  let tn = parseInt(topN) || 20;
  if (kv < 1) kv = 1;
  if (kv > MAX_K) kv = MAX_K;
  if (tn < 1) tn = 1;
  if (tn > MAX_TOP_N) tn = MAX_TOP_N;
  return { k: kv, topN: tn };
}

// =============================================
// User-Based CF 推荐
// =============================================

/**
 * GET /api/recommend/user-based/:userId?k=20&topN=20
 */
async function userBasedRecommend(req, res) {
  try {
    const userIdValid = validateUserId(req.params.userId);
    if (!userIdValid.valid) {
      return res.status(400).json({ success: false, message: userIdValid.message });
    }
    const { k, topN } = validateKnnParams(req.query.k, req.query.topN);

    const recommendations = await withTimeout(
      recommendService.userBasedCF(userIdValid.value, k, topN)
    );
    const enriched = await recommendService.enrichRecommendations(recommendations);

    res.json({
      success: true,
      data: {
        userId: userIdValid.value,
        algorithm: 'user-based-cf',
        k,
        total: enriched.length,
        recommendations: enriched
      }
    });
  } catch (error) {
    console.error('[User-Based CF] 推荐失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '推荐失败: ' + error.message });
  }
}

// =============================================
// Item-Based CF 推荐
// =============================================

/**
 * GET /api/recommend/item-based/:userId?k=20&topN=20
 */
async function itemBasedRecommend(req, res) {
  try {
    const userIdValid = validateUserId(req.params.userId);
    if (!userIdValid.valid) {
      return res.status(400).json({ success: false, message: userIdValid.message });
    }
    const { k, topN } = validateKnnParams(req.query.k, req.query.topN);

    const recommendations = await withTimeout(
      recommendService.itemBasedCF(userIdValid.value, k, topN)
    );
    const enriched = await recommendService.enrichRecommendations(recommendations);

    res.json({
      success: true,
      data: {
        userId: userIdValid.value,
        algorithm: 'item-based-cf',
        k,
        total: enriched.length,
        recommendations: enriched
      }
    });
  } catch (error) {
    console.error('[Item-Based CF] 推荐失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '推荐失败: ' + error.message });
  }
}

// =============================================
// 混合推荐
// =============================================

/**
 * GET /api/recommend/hybrid/:userId?k=20&topN=20&userWeight=0.5
 * userWeight 为 null 时自动使用自适应权重
 */
async function hybridRecommend(req, res) {
  try {
    const userIdValid = validateUserId(req.params.userId);
    if (!userIdValid.valid) {
      return res.status(400).json({ success: false, message: userIdValid.message });
    }
    const { k, topN } = validateKnnParams(req.query.k, req.query.topN);
    const userWeight = req.query.userWeight !== undefined ? parseFloat(req.query.userWeight) : null;

    // 如果传了 userWeight，校验范围
    let finalUserWeight = userWeight;
    let usedAdaptive = false;
    if (finalUserWeight !== null) {
      if (isNaN(finalUserWeight) || finalUserWeight < 0 || finalUserWeight > 1) {
        return res.status(400).json({ success: false, message: 'userWeight 必须在 0~1 之间' });
      }
    } else {
      usedAdaptive = true;
    }

    const recommendations = await withTimeout(
      recommendService.hybridRecommendation(userIdValid.value, k, topN, finalUserWeight)
    );
    const enriched = await recommendService.enrichRecommendations(recommendations);

    res.json({
      success: true,
      data: {
        userId: userIdValid.value,
        algorithm: 'hybrid',
        k,
        userWeight: usedAdaptive ? 'auto' : (finalUserWeight || 0.5),
        itemWeight: usedAdaptive ? 'auto' : (1 - (finalUserWeight || 0.5)),
        adaptiveWeight: usedAdaptive,
        total: enriched.length,
        recommendations: enriched
      }
    });
  } catch (error) {
    console.error('[Hybrid] 推荐失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '推荐失败: ' + error.message });
  }
}

// =============================================
// 获取用户最相似邻居
// =============================================

/**
 * GET /api/recommend/neighbors/:userId?k=20
 */
async function getUserNeighbors(req, res) {
  try {
    const userIdValid = validateUserId(req.params.userId);
    if (!userIdValid.valid) {
      return res.status(400).json({ success: false, message: userIdValid.message });
    }
    const { k } = validateKnnParams(req.query.k, 10);

    const neighbors = await withTimeout(
      recommendService.findKNearestUsers(userIdValid.value, k)
    );

    res.json({
      success: true,
      data: {
        userId: userIdValid.value,
        total: neighbors.length,
        neighbors
      }
    });
  } catch (error) {
    console.error('[获取邻居] 失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '获取邻居失败: ' + error.message });
  }
}

// =============================================
// 热门推荐
// =============================================

/**
 * GET /api/recommend/popular?page=1&pageSize=20&genre=Action
 */
async function popularRecommend(req, res) {
  try {
    const { page, pageSize } = validatePagination(req.query.page, req.query.pageSize);
    const genre = req.query.genre || null;

    // genre 参数校验：只允许字母
    if (genre && !/^[a-zA-Z]+$/.test(genre)) {
      return res.status(400).json({ success: false, message: '无效的题材参数' });
    }

    const recommendations = await withTimeout(
      recommendService.getPopularRecommendations(page, pageSize, genre)
    );

    res.json({
      success: true,
      data: {
        algorithm: 'popular',
        page,
        pageSize,
        total: recommendations.length,
        recommendations
      }
    });
  } catch (error) {
    console.error('[热门推荐] 失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '获取热门推荐失败: ' + error.message });
  }
}

// =============================================
// 新片推荐
// =============================================

/**
 * GET /api/recommend/new-releases?page=1&pageSize=20
 */
async function newReleaseRecommend(req, res) {
  try {
    const { page, pageSize } = validatePagination(req.query.page, req.query.pageSize);

    const recommendations = await withTimeout(
      recommendService.getNewReleaseRecommendations(page, pageSize)
    );

    res.json({
      success: true,
      data: {
        algorithm: 'new-releases',
        page,
        pageSize,
        total: recommendations.length,
        recommendations
      }
    });
  } catch (error) {
    console.error('[新片推荐] 失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '获取新片推荐失败: ' + error.message });
  }
}

// =============================================
// 趋势推荐
// =============================================

/**
 * GET /api/recommend/trending?page=1&pageSize=20&timeRange=7d
 * timeRange: 7d | 30d | 90d
 */
async function trendingRecommend(req, res) {
  try {
    const { page, pageSize } = validatePagination(req.query.page, req.query.pageSize);
    const timeRange = req.query.timeRange || '7d';

    // timeRange 参数校验
    if (!['7d', '30d', '90d'].includes(timeRange)) {
      return res.status(400).json({ success: false, message: 'timeRange 必须为 7d、30d 或 90d' });
    }

    const recommendations = await withTimeout(
      recommendService.getTrendingRecommendations(page, pageSize, timeRange)
    );

    res.json({
      success: true,
      data: {
        algorithm: 'trending',
        page,
        pageSize,
        timeRange,
        total: recommendations.length,
        recommendations
      }
    });
  } catch (error) {
    console.error('[趋势推荐] 失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '获取趋势推荐失败: ' + error.message });
  }
}

// =============================================
// 基于内容推荐（Qdrant）
// =============================================

/**
 * GET /api/recommend/content-based/:userId?page=1&pageSize=20
 */
async function contentBasedRecommend(req, res) {
  try {
    const userIdValid = validateUserId(req.params.userId);
    if (!userIdValid.valid) {
      return res.status(400).json({ success: false, message: userIdValid.message });
    }
    const { page, pageSize } = validatePagination(req.query.page, req.query.pageSize);

    const recommendations = await withTimeout(
      recommendService.getContentBasedRecommendations(userIdValid.value, page, pageSize)
    );

    res.json({
      success: true,
      data: {
        userId: userIdValid.value,
        algorithm: 'content-based',
        page,
        pageSize,
        total: recommendations.length,
        recommendations
      }
    });
  } catch (error) {
    console.error('[基于内容推荐] 失败:', error.message);
    const statusCode = error.message.includes('超时') ? 504 : 500;
    res.status(statusCode).json({ success: false, message: '获取基于内容推荐失败: ' + error.message });
  }
}

// =============================================
// 清除推荐缓存
// =============================================

/**
 * POST /api/recommend/clear-cache
 */
async function clearCache(req, res) {
  try {
    recommendService.clearCache();
    res.json({ success: true, message: '推荐缓存已清除' });
  } catch (error) {
    console.error('[清除缓存] 失败:', error.message);
    res.status(500).json({ success: false, message: '清除缓存失败: ' + error.message });
  }
}

// =============================================
// AI 模型推荐（已集成到 Node.js，移除 Python 代理）
// =============================================

/**
 * 将算法的 Python 命名（user_cf/item_cf/hybrid）映射到 recommendService 方法
 * 
 * 注意：hybrid 分别调用 User-CF 和 Item-CF，各自带独立超时：
 * - User-CF: 首次较慢（约 60-90s），后续有缓存很快（<100ms）
 * - Item-CF: 数据量大时较慢，超时后降级为只用 User-CF
 */
/**
 * GET /api/recommend/ai?userId=1&algorithm=hybrid&topN=10
 * 
 * 使用预训练模型文件（JSON）进行快速推荐：
 * - algorithm: hybrid | svd | user_cf | item_cf (默认: hybrid)
 * - 支持 MySQL 缓存，减少重复计算
 * - 无用户数据时自动降级为热门推荐
 * - 响应格式兼容原前端
 */
async function aiModelRecommend(req, res) {
  try {
    const { userId, algorithm, topN } = req.query;

    if (!userId) {
      return res.status(400).json({ success: false, message: '缺少 userId 参数' });
    }

    const uid = parseInt(userId);
    if (isNaN(uid) || uid <= 0) {
      return res.status(400).json({ success: false, message: '无效的 userId' });
    }

    const algo = (algorithm || 'hybrid').toLowerCase();
    const n = parseInt(topN) || 10;

    // 支持的算法列表
    const supportedAlgorithms = ['hybrid', 'svd', 'user_cf', 'item_cf', 'turbo_cf'];
    const effectiveAlgo = supportedAlgorithms.includes(algo) ? algo : 'hybrid';

    console.log(`[AI 推荐] 用户 ${uid}, 算法 ${effectiveAlgo}, Top-N ${n}`);

  // 使用预训练模型引擎进行快速推荐
  // 引擎通过 JSON 模型文件预测（首次加载 ~67MB 模型可能较慢），使用超时保护
  const result = await withTimeout(
    recommendEngine.getRecommendations(uid, effectiveAlgo, n),
    REQUEST_TIMEOUT
  );

  let recommendations = result.recommendations.map(r => ({
    movieId: r.movieId,
    predictedRating: r.predictedRating
  }));

  // 补充电影元信息
  let enriched = await recommendService.enrichRecommendations(recommendations);

  // AI 引擎无结果时，自动降级为热门推荐
  let degraded = false;
  let effectiveAlgoDisplay = effectiveAlgo;
  if (enriched.length === 0) {
    console.log(`[AI 推荐] 用户 ${uid} 无 AI 结果，降级到热门推荐`);
    const fallbackTopN = Math.max(n, 10);
    const popular = await recommendService.getPopularRecommendations(1, fallbackTopN);
    enriched = await recommendService.enrichRecommendations(
      popular.map(r => ({ movieId: r.movieId, predictedRating: r.predictedRating }))
    );
    degraded = true;
    effectiveAlgoDisplay = 'popular';
  }

  // 返回格式兼容前端（含 source: 'ai-model' 标识）
  res.json({
    success: true,
    source: 'ai-model',
    data: {
      userId: uid,
      algorithm: effectiveAlgoDisplay,
      topN: n,
      total: enriched.length,
      recommendations: enriched,
      degraded,
      elapsed: result.elapsed,
      fromCache: result.fromCache
    }
  });
  } catch (error) {
    console.error('[AI 推荐] 失败:', error.message);

    // 尝试返回热门推荐作为最终降级
    try {
      const topN = parseInt(req.query.topN) || 10;
      const popular = await recommendService.getPopularRecommendations(1, topN);
      const enriched = await recommendService.enrichRecommendations(
        popular.map(r => ({ movieId: r.movieId, predictedRating: r.predictedRating }))
      );

      return res.json({
        success: true,
        source: 'ai-model',
        message: 'AI 推荐已降级为热门推荐（原始错误: ' + error.message + '）',
        data: {
          userId: parseInt(req.query.userId) || 0,
          algorithm: 'popular',
          topN: topN,
          total: enriched.length,
          recommendations: enriched,
          degraded: true
        }
      });
    } catch (fallbackErr) {
      res.status(500).json({
        success: false,
        source: 'ai-model',
        message: '推荐失败，降级也失败: ' + error.message
      });
    }
  }
}

/**
 * GET /api/recommend/ai/models
 * 列出可用 AI 模型（算法）
 */
async function aiModelList(req, res) {
  try {
    const models = Object.entries(AVAILABLE_ALGORITHMS).map(([id, info]) => ({
      id,
      name: info.name,
      description: info.description,
      type: 'nodejs-native'
    }));

    res.json({
      success: true,
      data: {
        total: models.length,
        models
      }
    });
  } catch (error) {
    res.status(500).json({ success: false, message: '获取算法列表失败: ' + error.message });
  }
}

/**
 * GET /api/recommend/ai/health
 * AI 服务健康检查 - 检查数据库连接
 */
async function aiHealthCheck(req, res) {
  try {
    const { query } = require('../config/db');
    await query('SELECT 1');
    res.json({
      success: true,
      message: 'AI 推荐引擎运行中（Node.js 原生实现）',
      status: 'healthy',
      availableAlgorithms: Object.keys(AVAILABLE_ALGORITHMS)
    });
  } catch (error) {
    res.status(503).json({
      success: false,
      message: 'AI 推荐引擎异常: ' + error.message,
      status: 'unhealthy'
    });
  }
}

module.exports = {
  userBasedRecommend,
  itemBasedRecommend,
  hybridRecommend,
  getUserNeighbors,
  popularRecommend,
  newReleaseRecommend,
  trendingRecommend,
  contentBasedRecommend,
  clearCache,
  aiModelRecommend,
  aiModelList,
  aiHealthCheck
};