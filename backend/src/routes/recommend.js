const express = require('express');
const router = express.Router();
const recommendController = require('../controllers/recommendController');

// User-Based Collaborative Filtering 推荐
router.get('/user-based/:userId', recommendController.userBasedRecommend);

// Item-Based Collaborative Filtering 推荐
router.get('/item-based/:userId', recommendController.itemBasedRecommend);

// 混合推荐（User-Based + Item-Based）
router.get('/hybrid/:userId', recommendController.hybridRecommend);

// 获取用户最相似的邻居
router.get('/neighbors/:userId', recommendController.getUserNeighbors);

// 清除推荐缓存
router.post('/clear-cache', recommendController.clearCache);

module.exports = router;