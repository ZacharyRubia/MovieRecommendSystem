/**
 * recommendController.js - 推荐系统 API 控制器
 */
const recommendService = require('../services/recommendService');

/**
 * User-Based CF 推荐
 * GET /api/recommend/user-based/:userId?k=20&topN=20
 */
async function userBasedRecommend(req, res) {
  try {
    const userId = parseInt(req.params.userId);
    const k = parseInt(req.query.k) || 20;
    const topN = parseInt(req.query.topN) || 20;

    if (isNaN(userId) || userId <= 0) {
      return res.status(400).json({ success: false, message: '无效的用户ID' });
    }

    const recommendations = await recommendService.userBasedCF(userId, k, topN);
    const enriched = await recommendService.enrichRecommendations(recommendations);

    res.json({
      success: true,
      data: {
        userId,
        algorithm: 'user-based-cf',
        k,
        total: enriched.length,
        recommendations: enriched
      }
    });
  } catch (error) {
    console.error('[User-Based CF] 推荐失败:', error);
    res.status(500).json({ success: false, message: '推荐失败: ' + error.message });
  }
}

/**
 * Item-Based CF 推荐
 * GET /api/recommend/item-based/:userId?k=20&topN=20
 */
async function itemBasedRecommend(req, res) {
  try {
    const userId = parseInt(req.params.userId);
    const k = parseInt(req.query.k) || 20;
    const topN = parseInt(req.query.topN) || 20;

    if (isNaN(userId) || userId <= 0) {
      return res.status(400).json({ success: false, message: '无效的用户ID' });
    }

    const recommendations = await recommendService.itemBasedCF(userId, k, topN);
    const enriched = await recommendService.enrichRecommendations(recommendations);

    res.json({
      success: true,
      data: {
        userId,
        algorithm: 'item-based-cf',
        k,
        total: enriched.length,
        recommendations: enriched
      }
    });
  } catch (error) {
    console.error('[Item-Based CF] 推荐失败:', error);
    res.status(500).json({ success: false, message: '推荐失败: ' + error.message });
  }
}

/**
 * 混合推荐（User-Based + Item-Based）
 * GET /api/recommend/hybrid/:userId?k=20&topN=20&userWeight=0.5
 */
async function hybridRecommend(req, res) {
  try {
    const userId = parseInt(req.params.userId);
    const k = parseInt(req.query.k) || 20;
    const topN = parseInt(req.query.topN) || 20;
    const userWeight = parseFloat(req.query.userWeight) || 0.5;

    if (isNaN(userId) || userId <= 0) {
      return res.status(400).json({ success: false, message: '无效的用户ID' });
    }

    const recommendations = await recommendService.hybridRecommendation(userId, k, topN, userWeight);
    const enriched = await recommendService.enrichRecommendations(recommendations);

    res.json({
      success: true,
      data: {
        userId,
        algorithm: 'hybrid',
        k,
        userWeight,
        itemWeight: 1 - userWeight,
        total: enriched.length,
        recommendations: enriched
      }
    });
  } catch (error) {
    console.error('[Hybrid] 推荐失败:', error);
    res.status(500).json({ success: false, message: '推荐失败: ' + error.message });
  }
}

/**
 * 获取用户的最相似邻居
 * GET /api/recommend/neighbors/:userId?k=20
 */
async function getUserNeighbors(req, res) {
  try {
    const userId = parseInt(req.params.userId);
    const k = parseInt(req.query.k) || 20;

    if (isNaN(userId) || userId <= 0) {
      return res.status(400).json({ success: false, message: '无效的用户ID' });
    }

    const neighbors = await recommendService.findKNearestUsers(userId, k);

    res.json({
      success: true,
      data: {
        userId,
        total: neighbors.length,
        neighbors
      }
    });
  } catch (error) {
    console.error('[获取邻居] 失败:', error);
    res.status(500).json({ success: false, message: '获取邻居失败: ' + error.message });
  }
}

/**
 * 清除推荐缓存
 * POST /api/recommend/clear-cache
 */
async function clearCache(req, res) {
  try {
    recommendService.clearCache();
    res.json({ success: true, message: '推荐缓存已清除' });
  } catch (error) {
    res.status(500).json({ success: false, message: '清除缓存失败: ' + error.message });
  }
}

module.exports = {
  userBasedRecommend,
  itemBasedRecommend,
  hybridRecommend,
  getUserNeighbors,
  clearCache
};