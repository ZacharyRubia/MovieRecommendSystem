const express = require('express');
const router = express.Router();
const {
  getAllUsers,
  getUserById,
  createUser,
  updateUser,
  deleteUser,
  adminLogin
} = require('../controllers/usersController');

// 管理员登录
router.post('/admin/login', adminLogin);

// 获取所有用户
router.get('/', getAllUsers);

// 获取单个用户
router.get('/:id', getUserById);

// 创建新用户
router.post('/', createUser);

// 更新用户信息
router.put('/:id', updateUser);

// 删除用户
router.delete('/:id', deleteUser);

module.exports = router;