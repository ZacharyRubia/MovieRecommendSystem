const express = require('express');
const router = express.Router();
const moviesController = require('../controllers/moviesController');

// 获取所有电影列表
router.get('/', moviesController.getAllMovies);

// 获取电影详情
router.get('/:id', moviesController.getMovieById);

// 用户评分
router.post('/:movieId/rate', moviesController.rateMovie);

// 获取电影评论列表
router.get('/:movieId/comments', moviesController.getMovieComments);

// 获取用户对电影的评分
router.get('/:movieId/user-rating/:userId', moviesController.getUserRating);

// 获取电影文本评论列表（顶级评论，含回复数）
router.get('/:movieId/text-comments', moviesController.getMovieTextComments);

// 获取评论的回复列表
router.get('/comments/:commentId/replies', moviesController.getCommentReplies);

// 发表文本评论（或回复）
router.post('/:movieId/comment', moviesController.addComment);

// 删除文本评论
router.delete('/comments/:commentId', moviesController.deleteComment);

// 记录用户观看行为
router.post('/:movieId/view', moviesController.recordView);

module.exports = router;