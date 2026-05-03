const User = require('../models/User');
const bcrypt = require('bcryptjs');

// 获取所有用户（分页）
const getAllUsers = async (req, res) => {
  try {
    const page = Math.max(1, parseInt(req.query.page, 10) || 1);
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit, 10) || 20));

    const users = await User.findAll(page, limit);
    // 不返回密码哈希字段
    const usersWithoutPassword = users.map(user => {
      const { password_hash, ...rest } = user;
      return rest;
    });
    const total = await User.count();

    res.json({
      success: true,
      data: usersWithoutPassword,
      total,
      page,
      pageSize: limit,
      totalPages: Math.ceil(total / limit)
    });
  } catch (error) {
    console.error('获取用户列表失败:', error);
    res.status(500).json({ success: false, message: '获取用户列表失败' });
  }
};

// 获取单个用户信息
const getUserById = async (req, res) => {
  try {
    const { id } = req.params;
    const user = await User.findById(id);
    if (!user) {
      return res.status(404).json({ success: false, message: '用户不存在' });
    }
    const { password_hash, ...userWithoutPassword } = user;
    res.json({ success: true, data: userWithoutPassword });
  } catch (error) {
    console.error('获取用户信息失败:', error);
    res.status(500).json({ success: false, message: '获取用户信息失败' });
  }
};

// 创建新用户
const createUser = async (req, res) => {
  try {
    const { username, email, password, role_id = 2, avatar_url = '' } = req.body;

    // 基本校验
    if (!username || !password) {
      return res.status(400).json({ success: false, message: '用户名和密码不能为空' });
    }

    // 检查用户名是否已存在
    const existingUsername = await User.findByUsername(username);
    if (existingUsername) {
      return res.status(400).json({ success: false, message: '用户名已存在' });
    }

    // 如果提供了邮箱，检查邮箱是否已存在
    if (email) {
      const existingEmail = await User.findByEmail(email);
      if (existingEmail) {
        return res.status(400).json({ success: false, message: '邮箱已被注册' });
      }
    }

    // 加密密码
    const hashedPassword = await bcrypt.hash(password, 10);

    // 创建用户
    const userId = await User.create({
      username,
      email,
      password_hash: hashedPassword,
      role_id,
      avatar_url
    });

    res.json({
      success: true,
      message: '创建用户成功',
      data: { id: userId, username, email, role_id, avatar_url }
    });
  } catch (error) {
    console.error('创建用户失败:', error);
    res.status(500).json({ success: false, message: '创建用户失败' });
  }
};

// 更新用户信息
const updateUser = async (req, res) => {
  try {
    const { id } = req.params;
    const { email, avatar_url, role_id, password } = req.body;

    // 检查用户是否存在
    const user = await User.findById(id);
    if (!user) {
      return res.status(404).json({ success: false, message: '用户不存在' });
    }

    // 如果更新邮箱，检查是否被其他用户占用
    if (email && email !== user.email) {
      const existingEmail = await User.findByEmail(email);
      if (existingEmail && existingEmail.id !== parseInt(id)) {
        return res.status(400).json({ success: false, message: '邮箱已被其他用户使用' });
      }
    }

    // 准备更新数据
    const updateData = { email, avatar_url, role_id };
    if (password) {
      updateData.password_hash = await bcrypt.hash(password, 10);
    }

    await User.update(id, updateData);
    res.json({ success: true, message: '更新用户成功' });
  } catch (error) {
    console.error('更新用户失败:', error);
    res.status(500).json({ success: false, message: '更新用户失败' });
  }
};

// 删除用户
const deleteUser = async (req, res) => {
  try {
    const { id } = req.params;

    // 检查用户是否存在
    const user = await User.findById(id);
    if (!user) {
      return res.status(404).json({ success: false, message: '用户不存在' });
    }

    await User.delete(id);
    res.json({ success: true, message: '删除用户成功' });
  } catch (error) {
    console.error('删除用户失败:', error);
    res.status(500).json({ success: false, message: '删除用户失败' });
  }
};

// 管理员登录
const adminLogin = async (req, res) => {
  try {
    const { username, password } = req.body;

    if (!username || !password) {
      return res.status(400).json({ success: false, message: '用户名和密码不能为空' });
    }

    const user = await User.findByUsername(username);
    if (!user) {
      return res.status(401).json({ success: false, message: '用户名或密码错误' });
    }

    // 验证密码
    const isPasswordValid = await bcrypt.compare(password, user.password_hash);
    if (!isPasswordValid) {
      return res.status(401).json({ success: false, message: '用户名或密码错误' });
    }

    // 检查是否是管理员 (role_id = 1 为管理员)
    if (user.role_id !== 1) {
      return res.status(403).json({ success: false, message: '需要管理员权限' });
    }

    const { password_hash: _, ...userWithoutPassword } = user;
    res.json({
      success: true,
      message: '登录成功',
      data: userWithoutPassword
    });
  } catch (error) {
    console.error('管理员登录失败:', error);
    res.status(500).json({ success: false, message: '登录失败' });
  }
};

module.exports = {
  getAllUsers,
  getUserById,
  createUser,
  updateUser,
  deleteUser,
  adminLogin
};