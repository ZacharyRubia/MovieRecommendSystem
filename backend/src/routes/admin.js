const express = require('express');
const router = express.Router();
const adminController = require('../controllers/adminController');

// ==================== 电影管理 ====================
router.get('/movies', adminController.getAllMovies);
router.get('/movies/:id', adminController.getMovieById);
router.post('/movies', adminController.createMovie);
router.put('/movies/:id', adminController.updateMovie);
router.delete('/movies/:id', adminController.deleteMovie);
router.post('/movies/batch-import', adminController.batchImportMovies);

// ==================== 标签管理 ====================
router.get('/tags', adminController.getAllTags);
router.post('/tags', adminController.createTag);
router.put('/tags/:id', adminController.updateTag);
router.delete('/tags/:id', adminController.deleteTag);

// ==================== 导演管理 ====================
router.get('/directors', adminController.getAllDirectors);
router.post('/directors', adminController.createDirector);
router.put('/directors/:id', adminController.updateDirector);
router.delete('/directors/:id', adminController.deleteDirector);

// ==================== 题材管理 ====================
router.get('/genres', adminController.getAllGenres);
router.post('/genres', adminController.createGenre);
router.put('/genres/:id', adminController.updateGenre);
router.delete('/genres/:id', adminController.deleteGenre);

// ==================== 演员管理 ====================
router.get('/actors', adminController.getAllActors);
router.post('/actors', adminController.createActor);
router.put('/actors/:id', adminController.updateActor);
router.delete('/actors/:id', adminController.deleteActor);

// ==================== 评论管理 ====================
router.get('/comments', adminController.getAllComments);
router.delete('/comments/:id', adminController.deleteComment);

// ==================== 管理员个人信息 ====================
router.get('/profile/:id', adminController.getAdminProfile);
router.put('/profile/:id', adminController.updateAdminProfile);
router.put('/profile/:id/password', adminController.changeAdminPassword);

module.exports = router;
