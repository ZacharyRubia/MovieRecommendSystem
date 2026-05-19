# 电影推荐系统 — 全量代码分析总结

## 一、项目概览

全栈电影推荐系统，集成了传统协同过滤算法 + A/B 测试框架 + 向量数据库检索，具备完整的用户管理、电影浏览、评论互动功能。

### 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| 后端 | Node.js + Express.js | REST API 服务 |
| 数据库 | MySQL | 业务数据存储 |
| 缓存 | Redis | API 响应缓存 + Write-Behind 写回队列 |
| 向量库 | Qdrant | 基于内容的电影相似度检索 |
| 前端 | 原生 HTML/CSS/JS | 静态页面，通过 fetch 调用 API |
| 训练脚本 | Python（scikit-learn, pandas, numpy） | 离线模型训练 |
| 分析脚本 | Python（pymysql, redis, scipy） | A/B 测试离线统计分析 |

### 目录结构

```
MovieRecommendSystem/
├── backend/                  # Node.js 后端
│   ├── src/
│   │   ├── config/           # 数据库连接池
│   │   ├── controllers/      # 请求控制器
│   │   ├── middleware/       # A/B 测试中间件 + Redis 缓存中间件
│   │   ├── routes/           # API 路由
│   │   └── services/        # 核心业务服务（推荐引擎、缓存、A/B 测试）
│   ├── models/              # 预训练 JSON 模型文件
│   └── server.js            # 启动入口
├── frontend/
│   └── public/               # 9 个静态 HTML 页面
├── database/
│   └── init.sql              # 数据库初始化（15 张表）
├── scripts/
│   ├── train/                # 8 个推荐算法训练脚本
│   ├── recommend/            # 离线推荐生成 & 导入导出
│   ├── evaluation/           # 模型评估 & 混合权重调优
│   ├── analysis/             # A/B 测试统计分析模块
│   └── import/               # 数据导入（MySQL + Qdrant）
├── docs/                     # 项目文档
├── start.ps1 / start.bat     # 一键启动脚本
└── package.json              # 根 Monorepo 管理
```

---

## 二、后端架构详解

### 2.1 分层设计

后端遵循 MVC 模式，请求处理链路：

```
客户端 → Routes → Middleware → Controllers → Services → MySQL/Redis
                ↑               ↑               ↑
           A/B 测试分流    参数校验超时    推荐引擎/缓存服务
           Redis 缓存
```

### 2.2 路由与 API 端点

| 路由文件 | 前缀 | 功能 |
|---------|------|------|
| [users.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/users.js) | `/api/users` | 用户 CRUD + 管理员登录 |
| [movies.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/movies.js) | `/api/movies` | 电影列表/详情/评分/评论/行为记录 |
| [recommend.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/recommend.js) | `/api/recommend` | 9 个推荐端点（含 A/B 测试） |
| [admin.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/admin.js) | `/api/admin` | 管理后台 CRUD + 实验管理 |
| [abInternal.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/abInternal.js) | `/api/internal` | Python 分析脚本调用的内部接口 |
| [transcode.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/transcode.js) | `/api/transcode` | 视频转码接口 |

### 2.3 中间件

| 中间件 | 功能 |
|-------|------|
| [cacheMiddleware.js](file:///d:/Code/MovieRecommendSystem/backend/src/middleware/cacheMiddleware.js) | Redis 读缓存（5 分钟 TTL）+ 写后清除缓存 |
| [abTestMiddleware.js](file:///d:/Code/MovieRecommendSystem/backend/src/middleware/abTestMiddleware.js) | A/B 测试流量分发，将实验信息挂载到 `req.experiment` |

### 2.4 控制器

| 控制器 | 核心功能 |
|--------|---------|
| [usersController.js](file:///d:/Code/MovieRecommendSystem/backend/src/controllers/usersController.js) | 用户 CRUD、管理员登录校验 |
| [moviesController.js](file:///d:/Code/MovieRecommendSystem/backend/src/controllers/moviesController.js) | 电影列表（含 ids 批量查询）、详情、评分、评论、行为记录 |
| [adminController.js](file:///d:/Code/MovieRecommendSystem/backend/src/controllers/adminController.js) | 后台 CRUD（电影/标签/导演/题材/演员/评论/实验） |
| [recommendController.js](file:///d:/Code/MovieRecommendSystem/backend/src/controllers/recommendController.js) | 9 种推荐 + AI 模型推荐 + 健康检查，含参数校验/超时/降级 |

### 2.5 核心服务

#### recommendService.js — SQL 驱动的在线推荐

基于 MySQL 聚合查询，无需预训练模型：

- **User-Based CF**：SQL 筛选共同评分邻居 → Pearson 相似度计算
- **Item-Based CF**：利用 `item_similarity_caches` 缓存表加权聚合
- **Hybrid 混合**：User-CF + Item-CF 加权融合（支持自适应权重）
- **热门推荐**：按评分数量和均值降序
- **新片推荐**：按 `release_year` 降序
- **趋势推荐**：基于近期行为热度（7d/30d/90d 窗口）
- **基于内容推荐**：Qdrant 向量检索
- 结果写入 `user_recommendation_caches` 缓存表（异步，不阻塞）

#### recommendEngine.js — 预训练模型推理引擎

加载 JSON 模型文件到内存，提供高性能推理：

- **模型加载**：流式读取大 JSON 文件（不阻塞事件循环），带并发加载保护锁
- **SVD 推荐**：用户隐向量 × 电影隐向量 + 用户均值
- **User-CF**：邻居相似度加权聚合
- **User-CF Improved**：带 alpha 权重调整的改进版
- **Item-CF**：电影相似度矩阵加权
- **Item-CF Improved**：带偏置校正的改进版
- **SlopeOne**：物品偏差聚合
- **Turbo-CF**：K-Means 聚类加速版
- **Hybrid 混合**：多算法加权融合（含新旧权重兼容）
- **MySQL 缓存层**：1 小时 TTL，写回策略
- **预加载**：服务启动后 1s 异步预热所有模型

#### cacheService.js — Redis 缓存 + Write-Behind

- **Redis 读缓存**：GET 结果缓存 5 分钟，过期自动清理
- **Write-Behind 写回队列**：
  - 写入先入内存队列，立即返回
  - 每 150 秒（2.5 分钟）批量刷入 MySQL
  - 最大 50 条/批，失败自动重试 3 次
  - 支持优雅关闭（shutdown 时强制刷完所有待处理数据）

#### abTestService.js — A/B 测试核心服务

- **实验配置缓存**：每 60 秒从 MySQL 增量刷新
- **MD5 用户分桶**：user_id → 桶号（0-99），保证一致性
- **固定比例分流**：按配置百分比分配桶区间
- **Thompson Sampling**：从 Redis 读取 Beta 后验参数做自适应决策
- **降级策略**：Redis 不可用时使用 MySQL 默认参数

---

## 三、推荐算法体系

### 3.1 离线训练模型（Python → JSON → Node.js）

| 算法 | 训练脚本 | 模型文件 | 核心方法 |
|------|---------|---------|---------|
| SVD | [train_svd.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_svd.py) | `svd_model.json` | sklearn TruncatedSVD 矩阵分解 |
| User-CF 传统 | [train_usercf_traditional.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_usercf_traditional.py) | `user_cf_traditional_model.json` | Pearson 相似度 + 邻居加权 |
| User-CF 改进 | [train_usercf_improved.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_usercf_improved.py) | `user_cf_improved_model.json` | alpha 权重调整 |
| Item-CF 传统 | [train_itemcf_traditional.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_itemcf_traditional.py) | `item_cf_traditional_model.json` | 物品相似度矩阵 |
| Item-CF 改进 | [train_itemcf_improved.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_itemcf_improved.py) | `item_cf_improved_model.json` | 偏置校正 |
| SlopeOne 传统 | [train_slopeone_traditional.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_slopeone_traditional.py) | `slope_one_traditional_model.json` | 物品偏差预测 |
| Turbo-CF | [train_turbocf.py](file:///d:/Code/MovieRecommendSystem/scripts/train/train_turbocf.py) | `turbo_cf_model.json` | K-Means 聚类加速 |

训练数据基于 MovieLens 25M 数据集的子集。

### 3.2 推荐函数调度

[recommendEngine.js](file:///d:/Code/MovieRecommendSystem/backend/src/services/recommendEngine.js) 中的 `RECOMMEND_FUNCTIONS` 调度表：

```javascript
const RECOMMEND_FUNCTIONS = {
  svd: recommendSVD,
  user_cf: recommendUserCF,
  user_cf_traditional: recommendUserCF,
  user_cf_improved: recommendUserCFImproved,
  item_cf: recommendItemCF,
  item_cf_traditional: recommendItemCF,
  item_cf_improved: recommendItemCFImproved,
  slope_one_traditional: recommendSlopeOne,
  turbo_cf: recommendTurboCF,
};
```

Hybrid 混合推荐使用加权融合策略，支持新旧权重体系兼容。

### 3.3 降级策略

所有推荐 API 实现 **三级降级**：

1. AI 模型推理结果为空 → 降级为热门推荐
2. 热门推荐失败 → 返回空状态提示
3. AI 推荐请求超时/异常 → 捕获异常后尝试热门推荐兜底

---

## 四、A/B 测试框架

完整的在线自适应 A/B 测试闭环：

### 4.1 框架流程

```
MySQL (实验配置) → Node.js (流量分发) → 用户请求 → 策略命中
                                                        ↓
Python 分析脚本 ← MySQL (行为数据) ← 埋点上报 ← 前端展示
      ↓
Redis (后验参数) → Node.js (Thompson Sampling) → 自适应分流
```

### 4.2 模块职责

| 模块 | 技术 | 功能 |
|------|------|------|
| [abTestService.js](file:///d:/Code/MovieRecommendSystem/backend/src/services/abTestService.js) | Node.js | 分桶、固定分流、Thompson Sampling |
| [abTestMiddleware.js](file:///d:/Code/MovieRecommendSystem/backend/src/middleware/abTestMiddleware.js) | Node.js | 请求拦截、实验信息挂载、响应头附加 |
| [abInternal.js](file:///d:/Code/MovieRecommendSystem/backend/src/routes/abInternal.js) | Node.js | 内部分析数据接口 + Bandit 参数更新 |
| [ab_analysis.py](file:///d:/Code/MovieRecommendSystem/scripts/analysis/ab_analysis.py) | Python | 指标计算、统计检验、后验更新、收敛判定 |
| [config.py](file:///d:/Code/MovieRecommendSystem/scripts/analysis/config.py) | Python | 分析参数配置 |
| [stat_utils.py](file:///d:/Code/MovieRecommendSystem/scripts/analysis/stat_utils.py) | Python | 统计工具函数 |
| [adminController.js](file:///d:/Code/MovieRecommendSystem/backend/src/controllers/adminController.js) | Node.js | 实验/策略 CRUD 管理 |

### 4.3 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 分桶总数 | 100 | 桶号 0-99 |
| 实验刷新间隔 | 60 秒 | 增量加载进行中的实验 |
| 分析运行间隔 | 30 分钟 | Python 脚本周期运行 |
| 行为数据窗口 | 24 小时 | 最近数据参与分析 |
| 冷启动保护 | 2 小时 | 新策略不参与 Bandit 调整 |
| 显著性水平 α | 0.05 | 统计检验阈值 |
| 获胜概率阈值 | 95% | 蒙特卡洛模拟收敛判定 |
| 连续达标周期 | 6 次（3 小时） | 自动推全条件 |

---

## 五、数据库设计

[init.sql](file:///d:/Code/MovieRecommendSystem/database/init.sql) 定义了 15 张表：

| 表名 | 类型 | 说明 |
|------|------|------|
| `roles` | 字典表 | 角色（管理员/普通用户） |
| `users` | 主表 | 用户信息 |
| `tags` | 字典表 | 标签 |
| `directors` | 字典表 | 导演 |
| `actors` | 字典表 | 演员 |
| `genres` | 字典表 | 电影类型 |
| `movies` | 主表 | 电影信息 |
| `users_preferred_tags` | 关联表 | 用户偏好标签 |
| `movies_genres` | 关联表 | 电影-类型 |
| `movies_tags` | 关联表 | 电影-标签 |
| `movies_directors` | 关联表 | 电影-导演 |
| `movies_actors` | 关联表 | 电影-演员 |
| `users_movies_behaviors` | 流水表 | 用户行为事件（幂等） |
| `comments` | 流水表 | 文本评论（支持回复、置顶、幂等） |
| `ab_experiments` | 配置表 | A/B 测试实验 |
| `ab_strategies` | 配置表 | 实验策略 |
| `ab_results` | 结果表 | 实验结果 |
| `user_recommendation_caches` | 缓存表 | 推荐结果缓存 |
| `item_similarity_caches` | 缓存表 | 物品相似度缓存 |

设计亮点：

- **幂等设计**：行为表和评论表使用 `request_id`（UUID）唯一索引，防止重复提交
- **复合主键**：推荐缓存表使用 `(user_id, algorithm)` 复合主键，支持多算法
- **JSON 字段**：`client_env` 存储客户端环境信息
- **级联删除**：所有外键使用 `ON DELETE CASCADE`，保证数据一致性

---

## 六、缓存体系

| 层级 | 组件 | 存储 | TTL | 用途 |
|------|------|------|-----|------|
| L1 | Redis | 内存 | 5 分钟 | API 响应缓存（电影/用户/标签等） |
| L2 | MySQL 缓存表 | 磁盘 | 1 小时 | 推荐结果 + 相似度缓存 |
| L3 | 内存对象 | Node.js 堆 | 30 分钟 | 邻居用户缓存 |
| L4 | 内存模型 | Node.js 堆 | 永久 | 加载的 JSON 模型文件 |
| Write-Behind | 内存队列 | 内存 → MySQL | 150 秒刷写 | 延迟批量写入 |

---

## 七、前端页面

| 页面 | 功能 |
|------|------|
| [index.html](file:///d:/Code/MovieRecommendSystem/frontend/public/index.html) | 登录/注册 |
| [user-dashboard.html](file:///d:/Code/MovieRecommendSystem/frontend/public/user-dashboard.html) | 用户主页：混合推荐 + 8 算法切换 + 电影列表 |
| [movie-detail.html](file:///d:/Code/MovieRecommendSystem/frontend/public/movie-detail.html) | 电影详情、评分、评论 |
| [movie-player.html](file:///d:/Code/MovieRecommendSystem/frontend/public/movie-player.html) | 视频播放器 |
| [admin-dashboard.html](file:///d:/Code/MovieRecommendSystem/frontend/public/admin-dashboard.html) | 管理后台 |
| [admin-login.html](file:///d:/Code/MovieRecommendSystem/frontend/public/admin-login.html) | 管理员登录 |
| [admin-profile.html](file:///d:/Code/MovieRecommendSystem/frontend/public/admin-profile.html) | 管理员个人信息 |
| [user-management.html](file:///d:/Code/MovieRecommendSystem/frontend/public/user-management.html) | 管理员用户管理 |
| [ab-test-dashboard.html](file:///d:/Code/MovieRecommendSystem/frontend/public/ab-test-dashboard.html) | A/B 实验管理仪表盘 |

所有页面使用原生 HTML/CSS/JS，未引入前端框架。

---

## 八、启动方式

```powershell
# 方式一：一键启动
.\start.ps1        # PowerShell
.\start.bat        # CMD

# 方式二：手动启动
npm run install:all    # 安装所有依赖
npm run dev            # 同时启动前后端

# 后端 :3000 | 前端 :8080
```

第一个注册用户自动成为管理员。

---

## 九、推荐过程的完整数据流向

### 9.1 "普通推荐"（8 算法切换）完整链路

用户在前端点击算法标签 → 看到推荐结果，数据经过以下完整链路：

```
用户点击标签 (hybrid/svd/user_cf_traditional/...)
       │
       ▼
[前端] user-dashboard.html ─ switchAiAlgorithm(algorithm)
       │  GET /api/recommend/ai?userId=X&algorithm=Y&topN=12
       │
       ▼
[中间件] abTestMiddleware.js
       │  MD5 分桶 → 命中实验/策略 → 挂载 req.experiment
       │
       ▼
[控制器] recommendController.js ─ aiModelRecommend()
       │  ① 参数校验（userId/algorithm/topN）
       │  ② algorithm 映射（hybrid/svd/user_cf_traditional/...）
       │  ③ 调用推荐引擎
       │
       ▼
[推荐引擎] recommendEngine.js ─ getRecommendations()
       │
       ├── Step 1: 检查 MySQL 缓存表
       │   └── user_recommendation_caches (user_id, algorithm)
       │       ├── 命中 → 直接返回 cached.items
       │       └── 未命中 → Step 2
       │
       ├── Step 2: 加载 JSON 模型（如未加载）
       │   └── loadModelAsync(algorithm)
       │       └── 流式读取 backend/models/<algorithm>_model.json
       │           缓存至内存 _models[algorithm]
       │
       ├── Step 3: 算法推理
       │   └── RECOMMEND_FUNCTIONS[algorithm](model, userId, topN)
       │       ├── svd:             用户隐向量·电影隐向量 + 用户均值
       │       ├── user_cf:         邻居相似度加权聚合
       │       ├── user_cf_improved: 带 alpha 权重调整
       │       ├── item_cf:         电影相似度矩阵加权
       │       ├── item_cf_improved: 带偏置校正
       │       ├── slope_one:       物品偏差聚合
       │       ├── turbo_cf:        K-Means 聚类加速
       │       └── hybrid:          上述全部加权融合
       │
       ├── Step 4: 异步写回 MySQL 缓存
       │   └── saveResultToCache(userId, results, algorithm)
       │
       └── Step 5: 返回 { recommendations, elapsed, fromCache }
       │
       ▼
[控制器] recommendController.js
       │  ④ 自动降级（结果为空 → 热门推荐兜底）
       │  ⑤ enrichRecommendations() — 补充电影元信息
       │
       ▼
[前端] user-dashboard.html ─ renderAiRecommendations()
       │  renderAiRecommendCards() — 渲染卡片
       │  → 再次请求 GET /api/movies?ids=... 补充封面/标题
       │
       ▼
用户看到推荐结果（10 部电影卡片，横向滚动）
```

### 9.2 离线训练 → 模型文件 → 在线推理 数据流

```
┌─────────────────────────────────────────────────────────┐
│                    离线训练阶段 (Python)                   │
├─────────────────────────────────────────────────────────┤
│  scripts/train/train_svd.py                              │
│  scripts/train/train_usercf_traditional.py               │
│  scripts/train/train_usercf_improved.py                   │
│  scripts/train/train_itemcf_traditional.py                │
│  scripts/train/train_itemcf_improved.py                    │
│  scripts/train/train_slopeone_traditional.py              │
│  scripts/train/train_turbocf.py                           │
│  scripts/train/train_slopeone_improved.py                 │
│       │                                                   │
│       ├── 读取 extract_test_subset_test/test_ratings.csv  │
│       ├── 训练算法模型（numpy/scikit-learn）               │
│       └── 输出: scripts/models/<name>.pkl                │
│                                                        │
│  导出 (export_models_to_json.py)                         │
│       │                                                   │
│       └── Pickle → JSON 序列化                             │
│           输出 → backend/models/<name>.json                │
└───────────────────────┬─────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────┐
│                    在线推理阶段 (Node.js)                  │
├─────────────────────────────────────────────────────────┤
│  server.js                                              │
│       │                                                 │
│  启动后 1s → warmupModels()                              │
│       │                                                 │
│       └── 流式读取所有 backend/models/*.json              │
│           解析 → 存入 _models[algorithm]                  │
│                                                        │
│  请求到达 → getRecommendations(userId, algorithm, topN)  │
│       │                                                 │
│       ├── 内存已有模型 → 直接取                           │
│       ├── 内存无模型 → loadModelAsync() → 加载 → 缓存     │
│       ├── 执行推荐函数 → 返回 [{movieId, score}]           │
│       └── 异步写 MySQL 缓存表                              │
└─────────────────────────────────────────────────────────┘
```

### 9.3 各算法推理所需的数据结构

| 算法 | JSON 模型键 | 推理方式 |
|------|------------|---------|
| `svd` | `user2idx`, `movie2idx`, `user_features`, `movie_features`, `user_means` | 用户向量·电影向量点积 + 均值 |
| `user_cf_traditional` | `user_neighbors`, `user_movies`, `user_means`, `all_movies` | 邻居相似度加权聚合 |
| `user_cf_improved` | 同上 + `alpha`, `user_std` | 带 alpha 权重调整的加权聚合 |
| `item_cf_traditional` | `movie_sim_matrix`, `user_movies`, `all_movies` | 已评电影相似度矩阵加权 |
| `item_cf_improved` | 同上 + `user_means` | 带偏置校正的相似度加权 |
| `slope_one_traditional` | `item_deviations`, `user_movies`, `all_movies` | 物品偏差聚合预测 |
| `turbo_cf` | `user_neighbors`, `user_movies`, `user_means`, `n_neighbors` | K-Means 聚类邻居加权 |
| `hybrid` | 组合上述全部 | 多算法加权融合 |

### 9.4 缓存体系在推荐中的作用

```
推荐请求
   │
   ├── L1: MySQL 缓存表 ─── user_recommendation_caches (TTL=1h)
   │     KEY: (user_id, algorithm)
   │     ├── 命中 → 直接返回（<10ms）
   │     └── 未命中 → L2
   │
   ├── L2: 内存模型缓存 ─── _models[algorithm]
   │     ├── 已加载 → 执行推理（~100ms-5s）
   │     └── 未加载 → 流式读 JSON → 推理（首加载 ~3-10s）
   │
   └── 推理完成后 → 异步写 MySQL 缓存（不阻塞响应）
```

### 9.5 降级策略（三级兜底）

```
算法推荐无结果
   │
   ├── 一级降级：热门推荐 (popluarRecommend)
   │     GET /api/recommend/popular?page=1&pageSize=10
   │     ├── 成功 → 返回热门电影
   │     └── 失败 → 二级降级
   │
   ├── 二级降级：空状态提示
   │     "暂无推荐，去评分一些电影吧！🎥"
   │
   └── 超时降级（120s → 30s）
         AbortController 超时中断 → "推荐请求超时，请稍后再试"
```

---

## 十、文档索引

| 文档 | 位置 | 内容 |
|------|------|------|
| 系统概览 | [system-overview.md](file:///d:/Code/MovieRecommendSystem/docs/system-overview.md) | 功能特性与 API 接口 |
| 技术栈 | [tech-stack.md](file:///d:/Code/MovieRecommendSystem/docs/tech-stack.md) | 技术选型说明 |
| 缓存架构 | [cache-first-recommendation-architecture.md](file:///d:/Code/MovieRecommendSystem/docs/backend/cache-first-recommendation-architecture.md) | Redis 缓存策略 |
| 推荐算法实现 | [recommendation-algorithm-implementation-plan.md](file:///d:/Code/MovieRecommendSystem/docs/backend/recommendation-algorithm-implementation-plan.md) | 在线推荐实现 |
| 推荐算法增强 | [recommendation-algorithm-enhancement-plan.md](file:///d:/Code/MovieRecommendSystem/docs/backend/recommendation-algorithm-enhancement-plan.md) | 新增算法规划 |
| AI 模型集成 | [python-to-nodejs-ai-migration.md](file:///d:/Code/MovieRecommendSystem/docs/backend/python-to-nodejs-ai-migration.md) | Python→Node.js 迁移 |
| 训练优化 | [train_optimization_summary.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/train_optimization_summary.md) | 训练脚本优化总结 |
| 推荐导出评估 | [recommend_export_evaluation.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/recommend_export_evaluation.md) | 推荐导出评估 |
| 推荐数据结构 | [推荐数据结构.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/推荐数据结构.md) | MySQL 推荐表设计 |
| 训练推荐 | [train_recommend.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/train_recommend.md) | 离线推荐训练 |
| 训练推荐优化 | [train_recommend_optimization.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/train_recommend_optimization.md) | 性能优化 |
| 训练推荐性能路线图 | [train_recommend_performance_roadmap.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/train_recommend_performance_roadmap.md) | 性能改进规划 |
| 训练推荐总结 | [train_recommend_summary.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/train_recommend_summary.md) | 训练总结 |
| 混合权重评估 | [evaluate_hybrid_weights_summary.md](file:///d:/Code/MovieRecommendSystem/docs/scripts/evaluate_hybrid_weights_summary.md) | 混合权重调优 |
| 系统设计 | [design/4 推荐算法系统设计与改进.md](file:///d:/Code/MovieRecommendSystem/docs/design/4%20%E6%8E%A8%E8%8D%90%E7%AE%97%E6%B3%95%E7%B3%BB%E7%BB%9F%E8%AE%BE%E8%AE%A1%E4%B8%8E%E6%94%B9%E8%BF%9B.md) | 推荐系统设计 |
| A/B 测试设计 | [design/在线自适应AB测试框架设计.md](file:///d:/Code/MovieRecommendSystem/docs/design/%E5%9C%A8%E7%BA%BF%E8%87%AA%E9%80%82%E5%BA%94AB%E6%B5%8B%E8%AF%95%E6%A1%86%E6%9E%B6%E8%AE%BE%E8%AE%A1.md) | AB 测试框架设计 |
| 自适应设计 | [design/自适应设计.md](file:///d:/Code/MovieRecommendSystem/docs/design/%E8%87%AA%E9%80%82%E5%BA%94%E8%AE%BE%E8%AE%A1.md) | 自适应机制 |
| 算法规划 | [design/算法规划.md](file:///d:/Code/MovieRecommendSystem/docs/design/%E7%AE%97%E6%B3%95%E8%A7%84%E5%88%92.md) | 算法演进规划 |
