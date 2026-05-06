/**
 * recommend.js - 推荐系统路由
 * 
 * 9 个 API 端点：
 * 1. GET  /api/recommend/popular         - 热门推荐
 * 2. GET  /api/recommend/new-releases    - 新片推荐
 * 3. GET  /api/recommend/trending        - 趋势推荐
 * 4. GET  /api/recommend/content-based/:userId - 基于内容推荐 (Qdrant)
 * 5. GET  /api/recommend/user-based/:userId     - User-Based CF
 * 6. GET  /api/recommend/item-based/:userId     - Item-Based CF
 * 7. GET  /api/recommend/hybrid/:userId         - 混合推荐
 * 8. GET  /api/recommend/neighbors/:userId      - 邻居查询
 * 9. POST /api/recommend/clear-cache            - 清除缓存
 */

const express = require('express');
const router = express.Router();
const http = require('http');
const {
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
} = require('../controllers/recommendController');

// ---- 新增功能（无用户上下文） ----
router.get('/popular', popularRecommend);
router.get('/new-releases', newReleaseRecommend);
router.get('/trending', trendingRecommend);

// ---- 新增功能（有用户上下文） ----
router.get('/content-based/:userId', contentBasedRecommend);

// ---- 协同过滤（CF） ----
router.get('/user-based/:userId', userBasedRecommend);
router.get('/item-based/:userId', itemBasedRecommend);
router.get('/hybrid/:userId', hybridRecommend);
router.get('/neighbors/:userId', getUserNeighbors);

// ---- Python AI 模型推荐（训练好的模型） ----
router.get('/ai', aiModelRecommend);              // GET /api/recommend/ai?userId=1&algorithm=hybrid&topN=10
router.get('/ai/models', aiModelList);            // GET /api/recommend/ai/models
router.get('/ai/health', aiHealthCheck);          // GET /api/recommend/ai/health

// ---- 系统操作 ----
router.post('/clear-cache', clearCache);

module.exports = router;
