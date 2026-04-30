const express = require('express');
const router = express.Router();
const adminController = require('../controllers/adminController');
const { cacheResponse, clearCache } = require('../middleware/cacheMiddleware');
const { CACHE_KEYS } = require('../services/cacheService');

// ==================== 电影管理 ====================
router.get('/movies', cacheResponse(CACHE_KEYS.MOVIES), adminController.getAllMovies);
router.get('/movies/:id', cacheResponse(CACHE_KEYS.MOVIE), adminController.getMovieById);
router.post('/movies', clearCache(CACHE_KEYS.MOVIES), clearCache(CACHE_KEYS.MOVIE), adminController.createMovie);
router.put('/movies/:id', clearCache(CACHE_KEYS.MOVIES), clearCache(CACHE_KEYS.MOVIE), adminController.updateMovie);
router.delete('/movies/:id', clearCache(CACHE_KEYS.MOVIES), clearCache(CACHE_KEYS.MOVIE), adminController.deleteMovie);
router.post('/movies/batch-import', clearCache(CACHE_KEYS.MOVIES), clearCache(CACHE_KEYS.MOVIE), adminController.batchImportMovies);

// ==================== 标签管理 ====================
router.get('/tags', cacheResponse(CACHE_KEYS.TAGS), adminController.getAllTags);
router.post('/tags', clearCache(CACHE_KEYS.TAGS), adminController.createTag);
router.put('/tags/:id', clearCache(CACHE_KEYS.TAGS), adminController.updateTag);
router.delete('/tags/:id', clearCache(CACHE_KEYS.TAGS), adminController.deleteTag);

// ==================== 导演管理 ====================
router.get('/directors', cacheResponse(CACHE_KEYS.DIRECTORS), adminController.getAllDirectors);
router.post('/directors', clearCache(CACHE_KEYS.DIRECTORS), adminController.createDirector);
router.put('/directors/:id', clearCache(CACHE_KEYS.DIRECTORS), adminController.updateDirector);
router.delete('/directors/:id', clearCache(CACHE_KEYS.DIRECTORS), adminController.deleteDirector);

// ==================== 题材管理 ====================
router.get('/genres', cacheResponse(CACHE_KEYS.GENRES), adminController.getAllGenres);
router.post('/genres', clearCache(CACHE_KEYS.GENRES), adminController.createGenre);
router.put('/genres/:id', clearCache(CACHE_KEYS.GENRES), adminController.updateGenre);
router.delete('/genres/:id', clearCache(CACHE_KEYS.GENRES), adminController.deleteGenre);

// ==================== 演员管理 ====================
router.get('/actors', cacheResponse(CACHE_KEYS.ACTORS), adminController.getAllActors);
router.post('/actors', clearCache(CACHE_KEYS.ACTORS), adminController.createActor);
router.put('/actors/:id', clearCache(CACHE_KEYS.ACTORS), adminController.updateActor);
router.delete('/actors/:id', clearCache(CACHE_KEYS.ACTORS), adminController.deleteActor);

// ==================== 评论管理 ====================
router.get('/comments', cacheResponse(CACHE_KEYS.COMMENTS), adminController.getAllComments);
router.delete('/comments/:id', clearCache(CACHE_KEYS.COMMENTS), adminController.deleteComment);
router.put('/comments/:id/pin', clearCache(CACHE_KEYS.COMMENTS), adminController.togglePinComment);

// ==================== 管理员个人信息 ====================
router.get('/profile/:id', cacheResponse(CACHE_KEYS.ADMIN_PROFILE), adminController.getAdminProfile);
router.put('/profile/:id', clearCache(CACHE_KEYS.ADMIN_PROFILE), adminController.updateAdminProfile);
router.put('/profile/:id/password', clearCache(CACHE_KEYS.ADMIN_PROFILE), adminController.changeAdminPassword);

module.exports = router;