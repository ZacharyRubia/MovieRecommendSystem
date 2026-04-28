# 电影列表分页配置（普通用户每页15条）

## 概述

将前端普通用户电影列表的每页显示数量从原值调整为 **15 条/页**，以优化浏览体验和数据加载效率。

## 涉及文件

### 后端
- `backend/src/controllers/moviesController.js` — 分页查询核心逻辑，`getMovieList` 方法

### 前端（普通用户）
- `frontend/public/user-dashboard.html` — 普通用户仪表盘，电影列表渲染与分页交互

## 修改详情

### 1. 后端控制器 (`backend/src/controllers/moviesController.js`)

在 `getMovieList` 方法中，接收前端传来的 `limit` 参数并直接使用。如果前端未传 `limit`，则使用默认值 `15`。

```javascript
// 获取分页参数
const page = parseInt(req.query.page) || 1;
const limit = parseInt(req.query.limit) || 15;  // 默认每页15条
const offset = (page - 1) * limit;
```

该参数会传递给 SQL 查询：
```sql
SELECT * FROM movies ORDER BY created_at DESC LIMIT ? OFFSET ?
```

同时查询电影总数用于计算总页数：
```javascript
const totalResult = await query('SELECT COUNT(*) as total FROM movies');
const total = totalResult[0].total;
const totalPages = Math.ceil(total / limit);
```

### 2. 前端仪表盘 (`frontend/public/user-dashboard.html`)

在请求电影列表时传入 `limit=15` 参数：

```javascript
const res = await fetch(`${API_BASE}/movies?page=${currentPage}&limit=15`);
```

分页组件根据返回的 `totalPages` 动态渲染页数按钮，支持：
- 上一页 / 下一页切换
- 页码按钮跳转
- 当前页高亮

## 验证结果

服务端日志确认分页参数正确生效：

```
[DEBUG] 请求URL: /api/movies?page=1&limit=15
[DEBUG] 查询到的电影数量: 15
[DEBUG] 电影总数: 87585
[DEBUG] 计算总页数: 5839
[DEBUG] 返回响应:
  pagination: {
    page: 1,
    limit: 15,
    total: 87585,
    totalPages: 5839
  }
```

- **每页显示**：15 条电影记录
- **总记录数**：87,585 条
- **总页数**：5,839 页

## 数据流

```
用户打开仪表盘
  ↓
user-dashboard.html 发起 GET /api/movies?page=1&limit=15
  ↓
moviesController.getMovieList 解析 page=1, limit=15
  ↓
计算 offset=(1-1)*15=0，执行 SQL: SELECT ... LIMIT 15 OFFSET 0
  ↓
返回 15 条电影 + 分页元数据（total: 87585, totalPages: 5839）
  ↓
前端渲染电影卡片列表 + 分页按钮
```

## 后续可扩展

- 可在前端增加每页数量下拉选择器（如 15 / 30 / 50），让用户自定义
- 管理员后台可使用不同的分页大小（如 20 条/页）
- 可增加缓存机制减少重复查询总量