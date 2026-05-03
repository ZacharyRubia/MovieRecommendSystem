# 分页优化总结

## 背景
项目后端原来可能从数据库拉取全量数据再返回给前端（SELECT * videos LIMIT ... 等问题），导致大数据量下性能低下。本次优化实现了后端分页查询 + Redis 缓存 + 数据库索引的全链路优化。

## 核心改动

### 1. 后端所有列表接口改为 LIMIT/OFFSET 分页

使用 `LIMIT ? OFFSET ?` 语法，每次只查询需要的 N 条数据，避免全表扫描。

**受影响的后端控制器方法：**
- `adminController.getAllTags()` — 标签分页
- `adminController.getAllDirectors()` — 导演分页
- `adminController.getAllActors()` — 演员分页
- `adminController.getAllGenres()` — 题材分页
- `adminController.getAllMovies()` — 电影分页
- `adminController.getAllComments()` — 评论分页
- `adminController.getDashboard()` — 仪表盘统计
- `usersController.getAllUsers()` — 用户分页
- `recommendController.getRecommendations()` — 推荐分页

每个接口统一返回格式：
```json
{
  "success": true,
  "data": {
    "tags": [...],          // 当前页数据
    "page": 1,              // 当前页码
    "limit": 20,            // 每页条数
    "total": 131498         // 总数
  }
}
```

### 2. Redis 缓存粒度优化

**之前（错误的做法）：** 缓存全量数据或大片数据
**之后（正确的做法）：** 按查询条件粒度缓存

Key 设计模式：`admin:{resource}:page:{page}:size:{limit}`

例如：
- `admin:tags:page:1:size:20`
- `admin:movies:page:2:size:10`

缓存 TTL：300s（5分钟）
- 读操作自动缓存
- 写操作自动清除相关缓存（通过 `clearCache` 中间件）

### 3. 数据库索引优化

为排序/筛选字段添加 DESC 索引，加速大数据量下的 OFFSET 查询：

```sql
-- movies 表
INDEX idx_movies_created_at (created_at DESC),
INDEX idx_movies_avg_rating (avg_rating DESC),
INDEX idx_movies_release_year (release_year DESC),

-- tags/genres/directors/actors 表
INDEX idx_{table}_created_at (created_at DESC),

-- users 表
INDEX idx_users_created_at (created_at DESC),
INDEX idx_users_role_id (role_id)
```

### 4. 前端分页适配

**admin-dashboard.html：**
- 修复 `loadDashboard()` 中 `pageSize` → `limit` 传参名
- tags/directors/actors/genres 表格添加分页容器 div
- 添加 `renderSimplePagination()` 通用分页函数，带上一页/下一页按钮
- 分页使用 `PAGE_SIZE_DEFAULT` 常量（20条/页）

**user-management.html：**
- 用户列表改为分页加载
- 修复 dashboard 统计数据的正确显示

## 验证结果

所有分页接口均通过测试：
| API | 总记录数 | 每页条数 | Redis 缓存 Key |
|-----|---------|---------|---------------|
| `/api/admin/tags` | 131,498 | 20 | `admin:tags:page:1:size:20` |
| `/api/admin/movies` | 87,585 | 20 | `admin:movies:page:1:size:20` |
| `/api/admin/genres` | 19 | 20 | `admin:genres:page:1:size:20` |
| `/api/admin/directors` | 0 | 20 | `admin:directors:page:1:size:20` |
| `/api/admin/actors` | 0 | 20 | `admin:actors:page:1:size:20` |
| `/api/users` | N | 20 | 用户接口有独立缓存 |

## 修改文件清单

```
backend/src/controllers/adminController.js   — 所有列表接口增加 LIMIT/OFFSET（主改动）
backend/src/controllers/usersController.js   — getAllUsers 增加分页
backend/src/controllers/recommendController.js — 推荐接口分页
backend/src/middleware/cacheMiddleware.js     — 修复 CACHE_KEYS 冒号分隔符 Bug
backend/src/services/cacheService.js         — 缓存 Key 规范
backend/src/routes/admin.js                  — 路由不变，新增接口已注册
frontend/public/admin-dashboard.html         — tags/directors/actors/genres 分页容器+分页函数
frontend/public/user-management.html         — 用户列表分页、Dashboard 统计修复
database/init.sql                            — 索引优化（参考）