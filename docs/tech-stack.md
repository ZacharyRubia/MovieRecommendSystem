# 电影推荐系统技术栈介绍

## 整体技术栈

| 层次 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 后端运行时 | Node.js | v18+ | JavaScript 服务端运行环境 |
| 后端框架 | Express.js | v4.18 | 轻量级 Web 框架 |
| 前端 | 原生 HTML/CSS/JS | ES6+ | SPA 单页应用，无框架依赖 |
| 前端服务 | http-server | — | 零配置静态文件服务器 |
| 关系数据库 | MySQL | 8.x | 主存储，运行于 `192.168.1.38` |
| 向量数据库 | Qdrant | v1.17 | 语义向量检索，运行于 `192.168.1.38:6333` |
| 内存缓存 | Redis | — | 响应缓存 + 写入队列，运行于 `192.168.1.39:6379` |
| 机器学习 | scikit-learn | ≥1.0 | SVD、KNN、余弦相似度等算法 |
| 语义模型 | sentence-transformers | — | all-MiniLM-L6-v2，384 维嵌入 |
| 模型加速 | OpenVINO | — | INT8 量化推理优化 |
| 进程编排 | concurrently | — | 同时启动前后端 |

---

## 后端技术

**Node.js + Express.js**

采用经典三层架构：Routes（路由层）→ Controllers（控制层）→ Services（业务层），中间穿插 Middleware 处理横切关注点。

核心依赖：

- **mysql2** — MySQL 数据库驱动，使用 Promise 风格的连接池
- **@qdrant/js-client-rest** — Qdrant 向量数据库官方客户端
- **ioredis** — Redis 客户端，支持集群、Pipeline、发布订阅
- **bcryptjs** — 密码哈希加密
- **cors** / **dotenv** — 跨域配置与环境变量管理

服务器提供 RESTful API，同时承担视频文件流媒体服务，支持 HTTP Range 分片播放和 FFmpeg 实时转码。

---

## 前端技术

**原生 JavaScript SPA**

不使用任何前端框架（React/Vue/Angular），所有页面为纯 HTML + CSS + JavaScript。采用 ES6 `async/await` 和 `fetch` API 与后端通信。

用户认证信息存储在 `localStorage`，根据 `role_id` 进行管理员/普通用户的角色路由。

---

## 数据存储

### MySQL — 主存储

运行于独立服务器 `192.168.1.38`，数据库名为 `MovieRecommendSystem`，字符集 `utf8mb4`。

核心表包括：用户、电影、演员/导演/类型/标签字典、多对多关联表、用户行为日志（含 12 种交互类型）、评论表、推荐缓存表。

使用 `request_id`（UUID）+ 唯一索引实现幂等性，防止重复提交。

### Qdrant — 向量数据库

运行于 `192.168.1.38:6333`，存储 `movies` 集合，每条记录对应一部电影的 384 维语义向量。

向量由 **all-MiniLM-L6-v2** 模型从电影标题和类型信息编码生成，用于基于内容的相似推荐。

### Redis — 缓存层

运行于独立服务器 `192.168.1.39:6379`，承担双重职责：

1. **响应缓存**：GET 请求结果缓存 5 分钟，写操作自动失效
2. **Write-Behind 队列**：缓存写操作，每 150 秒批量刷新到 MySQL，最多 50 条/批

---

## 数据处理与机器学习

### 数据来源

使用 **MovieLens 32M** 公开数据集，包含约 8.7 万部电影、3200 万条评分、120 万条标签。

### ETL 流程

数据经 Python 脚本清洗后写入 MySQL：

- `movies.csv` → 电影表 + 类型表
- `ratings.csv` → 用户行为表（评分事件）
- `tags.csv` → 标签表 + 电影-标签关联表
- 电影文本信息 → all-MiniLM-L6-v2 编码 → Qdrant

### 推荐算法体系

系统内置**两套并行的推荐引擎**：

**引擎一：SQL 实时计算**

直接在 MySQL 中通过 JOIN 和聚合查询计算推荐结果，适用于中小规模数据集。包括：热门推荐、最新上映、近期趋势（7/30/90天）、基于内容（Qdrant 查询）、User-CF（Pearson 相似度）、Item-CF（余弦相似度）、混合推荐。

**引擎二：JSON 预训练模型**

Python 离线训练完成后导出为 JSON 格式，由 Node.js 在服务启动时异步加载推理。包括四种算法：

- **SVD** — 矩阵分解，捕获用户和电影的潜在因子
- **User-CF** — 基于用户的协同过滤（KNN + Pearson）
- **Item-CF** — 基于物品的协同过滤（余弦相似度）
- **Turbo-CF** — K-Means 聚类加速版协同过滤
- **Hybrid** — 加权集成（SVD 0.35 + Item-CF 0.25 + Turbo-CF 0.20 + User-CF 0.20）

推理结果写入 MySQL 缓存（1 小时 TTL），同时内存中缓存邻居计算结果（30 分钟 TTL）。

### NLP 语义模型

**all-MiniLM-L6-v2** 是 sentence-transformers 提供的轻量级语义嵌入模型：

- 架构：6 层 BERT，12 个注意力头，384 维输出
- 训练：11.7 亿句子对的对比学习
- 特点：体积小、推理快、语义表征能力强

项目同时提供 PyTorch、TensorFlow、OpenVINO（含 INT8 量化）三种部署格式。

### 模型评估

Python 评估脚本对 8 种模型组合进行 RMSE、MAE、Precision、Recall、F1、Coverage 指标评测，并支持混合权重的自动化调优。

---

## 系统部署

当前为**裸机部署**，无容器化方案：

```bash
# 安装依赖
npm run install:all

# 同时启动前后端
npm run dev
```

- 后端：Express 监听端口 3000
- 前端：http-server 监听端口 8080
- 数据库：MySQL + Qdrant 位于 `192.168.1.38`
- 缓存：Redis 位于 `192.168.1.39`
