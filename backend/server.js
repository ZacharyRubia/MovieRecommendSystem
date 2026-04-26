// backend/server.js - 启动入口
require('dotenv').config();
const express = require('express');
const cors = require('cors');
const bcrypt = require('bcryptjs');
const { query } = require('./src/config/db');
const usersRouter = require('./src/routes/users');
const moviesRouter = require('./src/routes/movies');

const app = express();
const PORT = process.env.PORT || 3000;

// 中间件
app.use(cors()); // 允许跨域请求
app.use(express.json()); // 解析 JSON 格式的请求体

// 静态文件服务 - 提供前端页面
const path = require('path');
app.use(express.static(path.join(__dirname, '../frontend/public')));
app.use('/public', express.static(path.join(__dirname, '../frontend/public')));

// 用户路由
app.use('/api/users', usersRouter);
// 电影路由
app.use('/api/movies', moviesRouter);

// 用户注册接口 - 第一个注册用户自动成为管理员
app.post('/api/register', async (req, res) => {
  try {
    const { username, password } = req.body;

    // 基本校验
    if (!username || !password) {
      return res.status(400).json({ success: false, message: '用户名和密码不能为空' });
    }

    // 检查用户名是否已存在
    const existingUser = await query('SELECT * FROM users WHERE username = ?', [username]);
    if (existingUser.length > 0) {
      return res.status(400).json({ success: false, message: '用户名已存在' });
    }

    // 获取当前用户总数，决定新用户角色
    const userCountResult = await query('SELECT COUNT(*) as count FROM users', []);
    const userCount = userCountResult[0].count;
    // 第一个用户 role_id = 1（管理员），后续用户 role_id = 2（普通用户）
    const roleId = userCount === 0 ? 1 : 2;

    // 加密密码
    const hashedPassword = await bcrypt.hash(password, 10);

    // 创建新用户
    const result = await query(
      'INSERT INTO users (username, password_hash, role_id) VALUES (?, ?, ?)',
      [username, hashedPassword, roleId]
    );

    console.log(`新用户注册成功: ${username}, 角色: ${roleId === 1 ? '管理员' : '普通用户'}`);
    res.json({ 
      success: true, 
      message: '注册成功',
      data: { id: result.insertId, username, role_id: roleId }
    });
  } catch (error) {
    console.error('注册失败:', error);
    res.status(500).json({ success: false, message: '注册失败: ' + error.message });
  }
});

// 用户登录接口 - 根据用户角色返回不同信息
app.post('/api/login', async (req, res) => {
  try {
    const { username, password } = req.body;

    if (!username || !password) {
      return res.status(400).json({ success: false, message: '用户名和密码不能为空' });
    }

    // 查找用户
    const users = await query('SELECT * FROM users WHERE username = ?', [username]);
    if (users.length === 0) {
      return res.status(401).json({ success: false, message: '用户名或密码错误' });
    }

    const user = users[0];
    
    // 验证密码
    const isPasswordValid = await bcrypt.compare(password, user.password_hash);
    if (!isPasswordValid) {
      return res.status(401).json({ success: false, message: '用户名或密码错误' });
    }

    // 不返回密码哈希
    const { password_hash, ...userWithoutPassword } = user;
    
    res.json({ 
      success: true, 
      message: '登录成功',
      data: userWithoutPassword
    });
  } catch (error) {
    console.error('登录失败:', error);
    res.status(500).json({ success: false, message: '登录失败: ' + error.message });
  }
});

// 启动服务器
app.listen(PORT, () => {
  console.log(`后端服务已启动，访问地址：http://localhost:${PORT}`);
  console.log('可用接口：');
  console.log(`  POST http://localhost:${PORT}/api/register`);
  console.log(`  POST http://localhost:${PORT}/api/login`);
  console.log(`  POST http://localhost:${PORT}/api/users/admin/login`);
  console.log(`  GET  http://localhost:${PORT}/api/users`);
  console.log(`  POST http://localhost:${PORT}/api/users`);
  console.log(`  PUT  http://localhost:${PORT}/api/users/:id`);
  console.log(`  DELETE http://localhost:${PORT}/api/users/:id`);
  console.log(`  GET  http://localhost:${PORT}/api/movies`);
  console.log(`  GET  http://localhost:${PORT}/api/movies/:id`);
  console.log(`  POST http://localhost:${PORT}/api/movies/:movieId/rate`);
});
