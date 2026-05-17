# 电影推荐系统技术栈概览

## 系统架构总览

```
┌──────────────────────────────────────────────────────────┐
│                     Frontend (SPA)                        │
│            Vanilla HTML + CSS + JavaScript                │
│              http-server :8080                            │
└────────────────────┬─────────────────────────────────────┘
                     │ HTTP REST API
                     ▼
┌──────────────────────────────────────────────────────────┐
│               Backend (Node.js + Express)                 │
│                  port 3000                                │
│  ┌──────────┬──────────┬──────────┬──────────────────┐   │
│  │ Routes   │Controls  │ Services │ Middleware       │   │
│  │          │          │          │ (Cache, Auth)    │   │
│  └────┬─────┴────┬─────┴────┬─────┴──────────────────┘   │
│       │          │          │                            │
│       ▼          ▼          ▼                            │
│  recommendEngine  recommendService  cacheService         │
│  (JSON模型推理)   (SQL实时协同过滤)  (Redis缓存)         │
└───────┬──────────────┬────────────────┬──────────────────┘
        │              │                │
        ▼              ▼                ▼
   ┌────────┐   ┌──────────┐    ┌──────────────┐
   │ MySQL  │   │  Qdrant  │    │    Redis     │
   │(主存储)│   │(向量检索) │    │   (缓存)     │
   └────────┘   └──────────┘    └──────────────┘
        ▲
        │
   ┌────┴──────────────┐
   │  Python ML Pipeline│
   │ (离线训练 + 数据导入)│
   └───────────────────┘
```

## 技术栈总表

| 层次 | 技术 | 版本/说明 |
|------|------|-----------|
| **运行时** | Node.js | v18+ |
| **后端框架** | Express.js | v4.18 |
| **前端** | Vanilla HTML + CSS + JavaScript | SPA，无框架 |
| **前端服务** | http-server | 静态文件服务器 |
| **关系数据库** | MySQL | 运行于 `192.168.1.38` |
| **向量数据库** | Qdrant | v1.17+，运行于 `192.168.1.38:6333` |
| **缓存** | Redis | 运行于 `192.168.1.39:6379` |
| **ML框架** | scikit-learn | SVD, KNN, Cosine Similarity |
| **NLP模型** | sentence-transformers | all-MiniLM-L6-v2 (384维) |
| **模型加速** | OpenVINO | INT8量化 |
| **编排** | concurrently | 同时启动前后端 |

---

## 一、后端 (Backend)

### 技术选型

- **语言**: JavaScript (Node.js)
- **框架**: Express.js v4.18
- **项目入口**: `backend/server.js` → `backend/src/app.js`
- **端口**: 3000

### 目录结构

```
backend/
├── server.js                  # 入口文件
├── package.json
├── media/                     # 视频文件存储
└── src/
    ├── app.js                 # Express 配置
    ├── config/
    │   └── db.js              # MySQL 连接池配置
    ├── routes/
    │   ├── movies.js          # 电影相关路由
    │   ├── users.js           # 用户相关路由
    │   ├── recommend.js       # 推荐系统路由 (9+3个端点)
    │   ├── admin.js           # 管理员 CRUD 路由
    │   └── transcode.js       # FFmpeg 实时转码路由
    ├── controllers/
    │   ├── moviesController.js
    │   ├── usersController.js
    │   ├── recommendController.js
    │   └── adminController.js
    ├── services/
    │   ├── recommendService.js   # SQL驱动的CF算法引擎
    │   ├── recommendEngine.js    # JSON模型推理引擎
    │   └── cacheService.js       # Redis缓存 + Write-Behind队列
    └── middleware/
        └── cacheMiddleware.js    # Redis缓存中间件
```

### 主要依赖

| 包名 | 用途 |
|------|------|
| express | Web 框架 |
| mysql2 | MySQL 数据库驱动（Promise 风格） |
| @qdrant/js-client-rest | Qdrant 向量数据库客户端 |
| ioredis | Redis 客户端 |
| bcryptjs | 密码哈希 |
| cors | 跨域支持 |
| dotenv | 环境变量加载 |

### 核心架构

采用分层架构：**Routes → Controllers → Services → Database**

- **Routes**: 定义 API 端点，参数校验
- **Controllers**: 请求处理逻辑，超时保护，降级策略
- **Services**: 业务逻辑实现（推荐算法、缓存管理等）
- **Middleware**: 跨切面关注点（缓存、鉴权）

### API 端点概览

#### 推荐系统 API (`/api/recommend`)

| 端点 | 算法 | 说明 |
|------|------|------|
| `GET /popular` | 评分热度 | 按评分数量+均分排序 |
| `GET /new-releases` | 最新上映 | 按年份排序 |
| `GET /trending` | 近期热门 | 7/30/90天活跃度 |
| `GET /content-based/:userId` | 基于内容 | Qdrant 向量检索 |
| `GET /user-based/:userId` | User-CF | Pearson 相似度 |
| `GET /item-based/:userId` | Item-CF | Cosine 相似度 |
| `GET /hybrid/:userId` | Hybrid CF | 自适应加权融合 |
| `GET /neighbors/:userId` | 相似用户 | KNN 邻居 |
| `GET /ai` | AI模型推理 | 加载JSON模型文件 |
| `GET /ai/models` | — | 列出可用AI模型 |
| `GET /ai/health` | — | AI服务健康检查 |

#### 通用 API

| 端点 | 说明 |
|------|------|
| `POST /api/register` | 用户注册（首个用户为管理员） |
| `POST /api/login` | 用户登录（角色重定向） |
| `GET/POST/PUT/DELETE /api/users` | 用户 CRUD |
| `GET/POST /api/movies` | 电影列表/详情 |
| `POST /api/movies/:id/rate` | 评分（1-5星） |
| `POST /api/movies/:id/comment` | 评论 |
| `POST /api/movies/:id/view` | 记录观看事件 |
| `GET /api/media/:filename` | 视频流媒体（支持Range） |
| `GET /api/transcode/:filename` | FFmpeg实时转码MKV→MP4 |
| `GET/POST/DELETE /api/admin/*` | 管理员全功能CRUD |
| `GET/POST /api/cache/*` | 缓存管理 |

---

## 二、前端 (Frontend)

### 技术选型

- **框架**: 无框架，纯原生 SPA
- **语言**: HTML5 + CSS3 + JavaScript (ES6+, async/await)
- **静态服务**: http-server，端口 8080
- **构建工具**: 无（无需构建）

### 页面结构

```
frontend/public/
├── index.html               # 登录/注册页
├── user-dashboard.html      # 用户首页 + 推荐展示
├── movie-detail.html        # 电影详情页
├── movie-player.html        # 视频播放页
├── admin-login.html         # 管理员登录
├── admin-dashboard.html     # 管理员仪表盘
├── admin-profile.html       # 管理员信息编辑
└── user-management.html     # 用户管理
```

### 关键实现

- **认证**: `localStorage` 存储当前用户信息
- **API基址**: 根据 `window.location.hostname` 动态拼接 `http://{host}:3000/api`
- **角色重定向**: `role_id === 1` → 管理员页面；`role_id === 2` → 用户页面
- **AI推荐**: 支持 SVD / User-CF / Item-CF / Turbo-CF 四种算法标签切换
- **超时处理**: 首次加载 120s 超时，后续 30s 超时

---

## 三、数据存储技术

### 3.1 MySQL (主存储)

- **主机**: `192.168.1.38`
- **数据库**: `MovieRecommendSystem`
- **字符集**: `utf8mb4`
- **驱动**: `mysql2/promise`（连接池模式）
- **ORM**: 无，使用原生 SQL

#### 表结构

| 表名 | 说明 | 核心字段 |
|------|------|----------|
| `roles` | 角色字典 | id, name (`admin`/`user`) |
| `users` | 用户账号 | id, username, email, password_hash, role_id, avatar_url |
| `movies` | 电影主表 | id, title, description, cover_url, video_url, release_year, duration, avg_rating |
| `genres` | 类型字典 | id, name, code |
| `tags` | 标签字典 | id, name |
| `directors` | 导演信息 | id, name, avatar, description |
| `actors` | 演员信息 | id, name, avatar, description |
| `movies_genres` | 电影-类型关联 | movie_id, genre_id |
| `movies_tags` | 电影-标签关联 | movie_id, tag_id |
| `movies_directors` | 电影-导演关联 | movie_id, director_id |
| `movies_actors` | 电影-演员关联 | movie_id, actor_id, role_name |
| `users_preferred_tags` | 用户偏好标签 | user_id, tag_id |
| `users_movies_behaviors` | 用户行为日志 | user_id, movie_id, action(12种), rating, progress, request_id |
| `comments` | 文本评论 | user_id, movie_id, content, parent_id, is_pinned, request_id |
| `item_similarity_caches` | 物品相似度缓存 | movie_id, algorithm(item_cf/turbo_cf/content_based), similar_movies |
| `user_recommendation_caches` | 用户推荐缓存 | user_id, algorithm(svd/user_cf/item_cf/turbo_cf/hybrid), recommendations |

#### 幂等性设计

`users_movies_behaviors` 和 `comments` 表使用 `request_id`（UUID）+ `UNIQUE INDEX`，防止重复提交。

### 3.2 Qdrant (向量数据库)

- **主机**: `192.168.1.38:6333`
- **客户端**: `@qdrant/js-client-rest v1.17`
- **集合**: `movies`
- **向量维度**: 384（all-MiniLM-L6-v2 输出）
- **用途**: 基于内容的电影推荐（Content-Based Filtering）

#### 工作流程

1. Python脚本 `import_qdrant.py` 从 MySQL 读取电影标题和类型
2. 经过 sentence-transformers 编码为 384 维向量
3. 存入 Qdrant `movies` 集合
4. 后端 `/api/recommend/content-based/:userId` 从 Qdrant 查询最相似的电影

### 3.3 Redis (缓存)

- **主机**: `192.168.1.39:6379`
- **客户端**: `ioredis`
- **用途**: HTTP 响应缓存 + Write-Behind 延迟写入队列

#### 缓存策略

- **读缓存**: GET 请求缓存，TTL = 5 分钟
- **缓存键前缀**: `admin:movies:*`, `admin:tags:*`, `admin:genres:*`, `admin:directors:*`, `admin:actors:*`, `admin:comments:*`, `users:*`, `admin:profile:*`
- **缓存失效**: 写操作（POST/PUT/DELETE）自动清除相关缓存
- **Write-Behind 队列**: 每 150 秒批量刷新最多 50 条 MySQL 写入（从缓存中间件收集的写操作）

---

## 四、数据处理与模型部署

### 4.1 数据源

**MovieLens 32M 数据集** (`dataset/ml-32m/`)

| 文件 | 内容 |
|------|------|
| `movies.csv` | 约 87,000 部电影 |
| `ratings.csv` | 约 3200 万条评分 |
| `tags.csv` | 约 120 万条标签 |
| `links.csv` | 外部链接映射 |

### 4.2 数据导入流程

```
movies.csv ──→ run_import_movies.py ──→ MySQL (movies + genres)
ratings.csv ─→ run_import_ratings.py ─→ MySQL (users_movies_behaviors)
tags.csv ────→ run_import_tags.py ────→ MySQL (tags + movies_tags)
movies ──────→ import_qdrant.py ──────→ Qdrant (向量化存储)
```

### 4.3 推荐模型体系

系统包含**两套并行的推荐引擎**：

#### 引擎一：SQL 实时计算 (`recommendService.js`)

| 算法 | 原理 | 适用场景 |
|------|------|----------|
| Popular | 评分数量+均分排序 | 冷启动/新用户 |
| New Releases | 按发行年份排序 | 最新电影 |
| Trending | 近期行为活跃度统计 | 热点追踪 |
| Content-Based | Qdrant 向量相似度查询 | 基于用户已评分电影 |
| User-CF | SQL 实现 Pearson 相似度 | 中小规模数据集 |
| Item-CF | SQL 实现 Cosine 相似度 | 中小规模数据集 |
| Hybrid | 自适应加权融合 | 综合推荐 |

#### 引擎二：JSON 模型推理 (`recommendEngine.js`)

| 算法 | 说明 | 模型大小 |
|------|------|----------|
| SVD | TruncatedSVD + user/item bias | ~67MB |
| User-CF | KNN + Pearson，降维存储 | ~30MB |
| Item-CF | Cosine Similarity 相似矩阵 | ~30MB |
| Turbo-CF | K-Means 聚类加速版 CF | ~20MB |
| Hybrid | 加权集成 (0.35/0.20/0.25/0.20) | 依赖上述 |

### 4.4 模型训练管道

```
Python 训练管道 (scripts/train/)
├── train_svd.py        # SVD 矩阵分解 → svd_model.json
├── train_usercf.py     # User-CF → user_cf_model.json
├── train_itemcf.py     # Item-CF → item_cf_model.json
├── train_turbocf.py    # Turbo-CF → turbo_cf_model.json
│
Python 评估管道 (scripts/evaluation/)
├── evaluate_models.py         # 8模型综合评估(RMSE/MAE/Precision/Recall/F1)
└── evaluate_hybrid_weights.py # 混合推荐权重调优
```

- **训练环境依赖**: numpy, scipy, pandas, scikit-learn, threadpoolctl, matplotlib
- **训练数据**: MovieLens 32M（约 3200 万评分）
- **输出格式**: JSON 文件，由 Node.js 推理引擎加载
- **内存需求**: SVD 训练约需 8-10GB 内存

### 4.5 模型部署架构

```
离线训练层 (Python)
    │
    ▼
模型导出 (JSON 文件) ──────────────────────────────────┐
    │                                                    │
    ▼                                                    ▼
Node.js 推理引擎 (recommendEngine.js)         MySQL 缓存层
    │                                          (user_recommendation_caches)
    │                                                    │
    └─────────────┬──────────────────────────────────────┘
                  ▼
            API 响应 (recommendController.js)
```

**推理流程**:
1. 服务器启动时异步加载所有 JSON 模型文件（流式解析大文件）
2. 用户请求到来 → 检查 MySQL 缓存（1小时 TTL）
3. 缓存命中 → 直接返回
4. 缓存未命中 → 执行模型推理 → 写入 MySQL 缓存 → 返回结果
5. 缓存邻居计算结果（内存中 30 分钟 TTL）

### 4.6 NLP 语义模型

| 项目 | 说明 |
|------|------|
| 模型 | all-MiniLM-L6-v2 (sentence-transformers) |
| 维度 | 384 |
| 架构 | BERT base (6层, 12注意力头) |
| 训练数据 | 11.7 亿句子对 (contrastive learning) |
| 位置 | `models/` 和 `models/all-MiniLM-L6-v2/` |

**部署格式（三种）**:

| 格式 | 文件 |
|------|------|
| PyTorch | `pytorch_model.bin` |
| TensorFlow | `tf_model.h5` |
| OpenVINO | `openvino_model.xml` + `.bin`（含 INT8 量化版本） |

### 4.7 缓存与性能优化

| 层次 | 技术 | TTL | 用途 |
|------|------|-----|------|
| L1 内存 | JS 对象 (Map) | 30 分钟 | 邻居计算结果 |
| L2 Redis | KV 存储 | 5 分钟 | GET 响应缓存 |
| L3 MySQL | 推荐缓存表 | 1 小时 | 持久化推荐结果 |
| Write-Behind | Redis → MySQL | 150秒/批 | 延迟写入融合 |

---

## 五、部署方案

### 当前部署

- **后端**: `node server.js` → 端口 3000
- **前端**: `npx http-server public -p 8080`
- **数据库**: MySQL + Qdrant 运行于 `192.168.1.38`（外部服务器）
- **缓存**: Redis 运行于 `192.168.1.39`（外部服务器）
- **无容器化**: 当前无 Docker 配置

### 启动方式

```bash
# 安装所有依赖
npm run install:all

# 同时启动前后端
npm run dev

# 或分别启动
npm run start:backend   # Node.js Express :3000
npm run dev:frontend    # http-server :8080
```

---

## 六、文档体系

项目包含丰富的文档：43 个 markdown 文件，位于 `docs/` 目录：

| 目录 | 内容 |
|------|------|
| `docs/backend/` | 架构设计、算法方案、BUG修复记录 |
| `docs/frontend/` | 前端指南（管理端、分页、视频配置） |
| `docs/design/` | 系统设计报告（算法、流程、自适应设计） |
| `docs/scripts/` | 脚本使用指南（训练、评估、数据导入） |
| `docs/fix/` | BUG修复日志（导入重构、AI超时、多算法支持） |

---

## 七、架构亮点总结

1. **双引擎推荐**: SQL 实时计算（小数据量灵活推荐）+ JSON 预训练模型（大数据量高性能推理）
2. **全链路缓存**: 内存 → Redis → MySQL 三级缓存 + Write-Behind 延迟写入
3. **Python → Node.js 迁移**: 原始推荐系统在 Python 中实现，已迁移至 Node.js 原生推理
4. **幂等设计**: `request_id` + 唯一索引防止重复提交
5. **多算法支持**: 8+ 种推荐算法可切换（Popular, New, Trending, User-CF, Item-CF, SVD, Turbo-CF, Hybrid, Content-Based）
6. **OpenVINO 加速**: 支持 INT8 量化模型加速推理
7. **视频流媒体**: 支持 HTTP Range 分片播放 + FFmpeg 实时转码
