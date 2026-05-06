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
 */
const recommendService = require('../services/recommendService');
const http = require('http');

// =============================================
// 配置常量
// =============================================
const REQUEST_TIMEOUT = 120000; // 120秒超时
const MAX_PAGE_SIZE = 100;      // 单页最大条数
const MAX_K = 200;              // 邻居数量上限
const MAX_TOP_N = 200;          // 推荐数上限

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
// Python AI 模型推荐（代理到 Python Flask 服务）
// =============================================

/**
 * AI 推荐服务配置
 */
const AI_RECOMMEND_HOST = process.env.AI_RECOMMEND_HOST || '127.0.0.1';
const AI_RECOMMEND_PORT = parseInt(process.env.AI_RECOMMEND_PORT || '5100');
const AI_REQUEST_TIMEOUT = 60000; // 60秒超时

/**
 * 调用 Python AI 推荐 API
 */
function callAiRecommendApi(endpoint, params) {
  return new Promise((resolve, reject) => {
    const url = new URL(`/api/recommend/${endpoint}`, `http://${AI_RECOMMEND_HOST}:${AI_RECOMMEND_PORT}`);
    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        url.searchParams.append(key, value);
      });
    }

    const req = http.get(url, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve(parsed);
        } catch (e) {
          reject(new Error(`解析 Python API 响应失败: ${e.message}`));
        }
      });
    });

    req.on('error', (e) => {
      reject(new Error(`连接 Python AI 服务失败 (${AI_RECOMMEND_HOST}:${AI_RECOMMEND_PORT}): ${e.message}。请确保已启动 recommend_api.py`));
    });

    req.setTimeout(AI_REQUEST_TIMEOUT, () => {
      req.destroy();
      reject(new Error('Python AI 服务请求超时'));
    });
  });
}

/**
 * GET /api/recommend/ai?userId=1&algorithm=hybrid&topN=10
 * AI 模型推荐（代理到 Python Flask 服务）
 */
async function aiModelRecommend(req, res) {
  try {
    const { userId, algorithm, topN } = req.query;
    if (!userId) {
      return res.status(400).json({ success: false, message: '缺少 userId 参数' });
    }

    // 注意：Python API 使用 user_id / top_n 参数名
    const params = { 
      user_id: userId, 
      algorithm: algorithm || 'hybrid', 
      top_n: topN || '10' 
    };
    const result = await callAiRecommendApi('ai', params);

    if (!result.success) {
      return res.status(500).json({ success: false, message: result.message });
    }

    // 将 Python 返回的数据透传给前端
    res.json({
      success: true,
      source: 'ai-model',
      data: result.data
    });
  } catch (error) {
    console.error('[AI 模型推荐] 失败:', error.message);
    // 如果 Python 服务不可用，降级返回友好提示
    res.status(503).json({
      success: false,
      source: 'ai-model',
      message: 'AI 推荐引擎暂不可用，请确认 recommend_api.py 已启动。',
      detail: error.message
    });
  }
}

/**
 * GET /api/recommend/ai/models
 * 列出可用 AI 模型
 */
async function aiModelList(req, res) {
  try {
    const result = await callAiRecommendApi('models', null);
    if (!result.success) {
      return res.status(500).json({ success: false, message: result.message });
    }
    res.json(result);
  } catch (error) {
    console.error('[AI 模型列表] 失败:', error.message);
    res.status(503).json({ success: false, message: 'AI 推荐引擎暂不可用', detail: error.message });
  }
}

/**
 * GET /api/recommend/ai/health
 * AI 服务健康检查
 */
async function aiHealthCheck(req, res) {
  try {
    const result = await callAiRecommendApi('health', null);
    if (!result.success) {
      return res.status(500).json({ success: false, message: result.message });
    }
    res.json(result);
  } catch (error) {
    res.status(503).json({ success: false, message: 'AI 推荐引擎暂不可用', detail: error.message });
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
