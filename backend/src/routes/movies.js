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

// 记录用户观看行为
router.post('/:movieId/view', moviesController.recordView);

module.exports = router;