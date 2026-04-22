// backend/server.js
const express = require('express');
const app = express();
const PORT = 3000;

// 中间件：解析 JSON 格式的请求体
app.use(express.json());

// 模拟数据库：存储在内存中，服务器重启后数据会丢失
const users = []; // 每个用户结构 { username, password }

// 注册接口
app.post('/api/register', (req, res) => {
  const { username, password } = req.body;

  // 基本校验
  if (!username || !password) {
    return res.status(400).json({ success: false, message: '用户名和密码不能为空' });
  }

  // 检查用户名是否已存在
  const existingUser = users.find(u => u.username === username);
  if (existingUser) {
    return res.status(400).json({ success: false, message: '用户名已存在' });
  }

  // 存储新用户（明文密码，仅为演示）
  users.push({ username, password });
  console.log(`新用户注册成功: ${username}`);
  res.json({ success: true, message: '注册成功' });
});

// 登录接口
app.post('/api/login', (req, res) => {
  const { username, password } = req.body;

  if (!username || !password) {
    return res.status(400).json({ success: false, message: '用户名和密码不能为空' });
  }

  const user = users.find(u => u.username === username && u.password === password);
  if (user) {
    res.json({ success: true, message: '登录成功' });
  } else {
    res.status(401).json({ success: false, message: '用户名或密码错误' });
  }
});

// 启动服务器
app.listen(PORT, () => {
  console.log(`后端服务已启动，访问地址：http://localhost:${PORT}`);
  console.log('可用接口：');
  console.log(`  POST http://localhost:${PORT}/api/register`);
  console.log(`  POST http://localhost:${PORT}/api/login`);
});