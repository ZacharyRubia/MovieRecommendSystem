const express = require('express');
const router = express.Router();
const userController = require('../controllers/usersController');
const { cacheResponse, clearCache } = require('../middleware/cacheMiddleware');
const { CACHE_KEYS } = require('../services/cacheService');

// ==================== 用户管理 ====================

// 获取用户列表（缓存 5 分钟）
router.get('/', cacheResponse(CACHE_KEYS.USERS), userController.getAllUsers);

// 获取单个用户（缓存 5 分钟）
router.get('/:id', cacheResponse(CACHE_KEYS.USERS), userController.getUserById);

// 创建用户（清除用户缓存）
router.post('/', clearCache(CACHE_KEYS.USERS), userController.createUser);

// 更新用户（清除用户缓存）
router.put('/:id', clearCache(CACHE_KEYS.USERS), userController.updateUser);

// 删除用户（清除用户缓存）
router.delete('/:id', clearCache(CACHE_KEYS.USERS), userController.deleteUser);

// 管理员登录（不缓存路由，使用 POST /api/users/admin/login）
router.post('/admin/login', userController.adminLogin);

module.exports = router;
