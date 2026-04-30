const express = require('express');
const router = express.Router();
const moviesController = require('../controllers/moviesController');
const { cacheResponse, clearCache } = require('../middleware/cacheMiddleware');
const { CACHE_KEYS } = require('../services/cacheService');

// 获取所有电影列表（缓存 5 分钟）
router.get('/', cacheResponse(CACHE_KEYS.MOVIES), moviesController.getAllMovies);

// 获取电影详情（缓存 5 分钟）
router.get('/:id', cacheResponse(CACHE_KEYS.MOVIE), moviesController.getMovieById);

// 用户评分（清除电影详情缓存）
router.post('/:movieId/rate', clearCache(CACHE_KEYS.MOVIE), moviesController.rateMovie);

// 获取电影评论列表（缓存 5 分钟）
router.get('/:movieId/comments', cacheResponse(CACHE_KEYS.COMMENTS), moviesController.getMovieComments);

// 获取用户对电影的评分（不缓存，用户专属数据）
router.get('/:movieId/user-rating/:userId', moviesController.getUserRating);

// 获取电影文本评论列表（缓存 5 分钟）
router.get('/:movieId/text-comments', cacheResponse(CACHE_KEYS.COMMENTS), moviesController.getMovieTextComments);

// 获取评论的回复列表（不缓存）
router.get('/comments/:commentId/replies', moviesController.getCommentReplies);

// 发表文本评论（清除电影和评论相关缓存）
router.post('/:movieId/comment', clearCache(CACHE_KEYS.MOVIES), clearCache(CACHE_KEYS.COMMENTS), moviesController.addComment);

// 删除文本评论（清除电影和评论相关缓存）
router.delete('/comments/:commentId', clearCache(CACHE_KEYS.MOVIES), clearCache(CACHE_KEYS.COMMENTS), moviesController.deleteComment);

// 记录用户观看行为（不缓存）
router.post('/:movieId/view', moviesController.recordView);

module.exports = router;