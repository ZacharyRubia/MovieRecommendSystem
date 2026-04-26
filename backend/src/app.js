// backend/src/app.js - Express 实例配置
require('dotenv').config();
const express = require('express');
const cors = require('cors');
const usersRouter = require('./routes/users');
const moviesRouter = require('./routes/movies');

const app = express();

// 中间件配置
app.use(cors()); // 允许跨域请求

// 支持 Private Network Access (PNA) - 解决从公网IP访问localhost的CORS问题
app.use((req, res, next) => {
  res.setHeader('Access-Control-Allow-Private-Network', 'true');
  if (req.method === 'OPTIONS') {
    res.setHeader('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
    res.setHeader('Access-Control-Allow-Headers', 'Content-Type, Authorization');
    return res.status(200).end();
  }
  next();
});

app.use(express.json()); // 解析 JSON 格式的请求体

// API 路由
app.use('/api/users', usersRouter);
app.use('/api/movies', moviesRouter);

module.exports = app;