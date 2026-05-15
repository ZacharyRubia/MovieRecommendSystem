# 多算法推荐系统重构总结

> **日期**: 2026-05-15  
> **目标**: 重构数据库、导入脚本、后端服务和前端，全面支持四种推荐算法（SVD / User-CF / Item-CF / Turbo-CF）  
> **说明**: 移除旧版单算法存储结构，采用复合主键支持多算法并存；统一前/后端算法标识；补全 Turbo-CF 缺失模块

---

## 一、问题背景

原有系统仅设计了一套 `(movie_id, similar_movies)` 和 `(user_id, recommend_movies)` 的单记录存储，存在以下问题：

1. **无法区分算法来源** — 数据库中只存一个 `similar_movies` 列，不能同时保存 Item-CF 和 Turbo-CF 的计算结果。
2. **导入脚本硬编码单算法** — `import_recommendations.js` 和 `import_to_mysql.py` 只读一种 CSV 输出，新增算法需改代码。
3. **后端缓存层引用旧表名** — `recommendService.js` 仍引用 `item_similarity_cache`（单数）等旧表。
4. **Turbo-CF JSON 模型缺失** — `backend/models/` 没有 `turbo_cf_model.json`，Node.js 端无法加载第四种算法。
5. **前端缺 Turbo-CF 入口** — 用户页面的 AI 推荐标签缺少 Turbo-CF 按钮和名称映射。

---

## 二、改动清单（共 9 项）

| # | 文件 | 改动摘要 |
|---|------|----------|
| 1 | `database/init.sql` | 重建两张缓存表：`item_similarity_caches` + `user_recommendation_caches`，采用复合主键 `(id, algorithm)` |
| 2 | `scripts/recommend/train_recommend.py` | `export_item_similarities_csv` 和 `export_users_recommendations_for_algorithm` 输出的 CSV 增加 `algorithm` 列 |
| 3 | `scripts/import/import_recommendations.js` | 完全重写：支持扫描任意算法 CSV、匹配 INSERT/SELECT 新表结构、混合推荐含 TurboCF |
| 4 | `scripts/import/import_to_mysql.py` | 完全重写：多算法扫描、新表名 + 复合主键 INSERT、HTTP POST 触发缓存预热 |
| 5 | `backend/src/services/recommendEngine.js` | 新增 `turbo_cf_model.json` 加载、hybrid 加权组合加入 `turbo_cf` 权重、新表名查询 |
| 6 | `backend/src/controllers/recommendController.js` | 算法列表 `['hybrid','svd','user_cf','item_cf','turbo_cf']` 不变，已完整覆盖 |
| 7 | `backend/src/services/recommendService.js` | 修复 `item_similarity_caches` / `user_recommendation_caches` 旧表名引用、清除旧注释 |
| 8 | `frontend/public/user-dashboard.html` | 添加 Turbo-CF 标签按钮和算法名称映射；后移除 hybrid 按钮（只留 4 种独立算法） |
| 9 | 运行 `export_models_to_json.py` | 将 `scripts/models/turbo_cf_model.pkl` 导出为 `backend/models/turbo_cf_model.json` |

---

## 三、数据库变更详情

### 3.1 旧表（已删除）

```sql
-- item_similarity_cache（单算法，无 algorithm 字段）
DROP TABLE IF EXISTS `item_similarity_cache`;
-- user_recommendation_cache（单算法，无 algorithm 字段）
DROP TABLE IF EXISTS `user_recommendation_cache`;
```

### 3.2 新表结构

**`item_similarity_caches`** — 物品相似度缓存（多算法）

| 列名 | 类型 | 说明 |
|------|------|------|
| `movie_id` | BIGINT UNSIGNED | 电影 ID（复合主键之一） |
| `algorithm` | VARCHAR(20) | 算法标识：`item_cf` / `turbo_cf` / `content_based` |
| `similar_movies` | JSON | 相似电影列表 `[{movie_id, score}]` |
| `updated_at` | TIMESTAMP | 更新时间 |

复合主键：`(movie_id, algorithm)`，允许每部电影每种算法独立存储相似度列表。

**`user_recommendation_caches`** — 用户推荐缓存（多算法）

| 列名 | 类型 | 说明 |
|------|------|------|
| `user_id` | BIGINT UNSIGNED | 用户 ID（复合主键之一） |
| `algorithm` | VARCHAR(20) | 算法标识：`svd` / `user_cf` / `item_cf` / `turbo_cf` / `hybrid` |
| `recommend_movies` | JSON | 推荐列表 `[{movie_id, score}]` |
| `updated_at` | TIMESTAMP | 更新时间 |

复合主键：`(user_id, algorithm)`，每个用户每种算法独立存储推荐结果。

> 混合推荐（hybrid）不复存在独立表，而是作为 `user_recommendation_caches` 中 `algorithm='hybrid'` 的一行。

---

## 四、导入脚本重构

### 4.1 `import_recommendations.js`

**改动点**：
- `readCSVFilesForAlgorithm(algorithm)` — 扫描 `scripts/recommend/output/recommend_{algorithm}.csv`
- `loadToMySQL()` 循环 5 种算法：`['svd','user_cf','item_cf','turbo_cf','hybrid']`
- SQL：`INSERT INTO user_recommendation_caches (user_id, algorithm, recommend_movies) VALUES ... ON DUPLICATE KEY UPDATE`
- 相似度导入同理

### 4.2 `import_to_mysql.py`

**改动点**：
- 新增 `sim_algorithms = ['item_cf', 'turbo_cf']`
- 新增 `rec_algorithms = ['svd', 'user_cf', 'item_cf', 'turbo_cf', 'hybrid']`
- 循环读取 `output/similarity_{alg}.csv` 和 `output/recommend_{alg}.csv`
- SQL 匹配新表名 `item_similarity_caches` / `user_recommendation_caches`
- 导入完成后 `POST /api/recommend/refresh-cache` 触发后端缓存预热

---

## 五、后端服务重构

### 5.1 算法权重配置（hybrid 混合推荐）

```javascript
// recommendEngine.js
this.hybridWeights = {
  svd: 0.30,
  user_cf: 0.20,
  item_cf: 0.25,
  turbo_cf: 0.25  // ← 新增
};
```

### 5.2 SQL 查询适配

```sql
-- 旧（单表/无 algorithm）
SELECT similar_movies FROM item_similarity_cache WHERE movie_id = ?
SELECT recommend_movies FROM user_recommendation_cache WHERE user_id = ?

-- 新（复合主键 + algorithm）
SELECT similar_movies FROM item_similarity_caches WHERE movie_id = ? AND algorithm = ?
SELECT recommend_movies FROM user_recommendation_caches WHERE user_id = ? AND algorithm = ?
```

---

## 六、前端改动

### 6.1 移除混合推荐按钮（按用户要求）

```html
<!-- 删除前 -->
<button class="ai-tab-btn active" data-algorithm="hybrid">混合推荐</button>
<button class="ai-tab-btn" data-algorithm="svd">SVD 模型</button>
<button class="ai-tab-btn" data-algorithm="user_cf">User-CF 模型</button>
<button class="ai-tab-btn" data-algorithm="item_cf">Item-CF 模型</button>
<button class="ai-tab-btn" data-algorithm="turbo_cf">Turbo-CF 模型</button>

<!-- 删除后 -->
<button class="ai-tab-btn active" data-algorithm="svd">SVD 模型</button>
<button class="ai-tab-btn" data-algorithm="user_cf">User-CF 模型</button>
<button class="ai-tab-btn" data-algorithm="item_cf">Item-CF 模型</button>
<button class="ai-tab-btn" data-algorithm="turbo_cf">Turbo-CF 模型</button>
```

### 6.2 默认算法调整

```javascript
// 旧
let currentAiAlgorithm = 'hybrid';
// 新
let currentAiAlgorithm = 'svd';
```

### 6.3 算法名称映射

```javascript
const algorithmNames = {
  'svd': 'SVD 模型',
  'user_cf': 'User-CF 模型',
  'item_cf': 'Item-CF 模型',
  'turbo_cf': 'Turbo-CF 模型'
};
```

---

## 七、Turbo-CF JSON 模型导出

**问题**：`train_turbocf.py` 输出 `turbo_cf_model.pkl`，Node.js 无法直接读取 Pickle 格式。  
**解决**：运行 `export_models_to_json.py` 并指定正确路径：

```powershell
python scripts/export/export_models_to_json.py `
    --model-dir scripts/models `
    --output-dir backend/models
```

**验证**：

```
backend/models/
├── svd_model.json       (2.7 MB)
├── user_cf_model.json   (  3 KB)
├── item_cf_model.json   (4.5 MB)
└── turbo_cf_model.json  (6.0 MB)   ★ 新增
```

---

## 八、系统调优建议

### 8.1 前端首次加载超时

后端加载 JSON 模型（合计 ~13 MB）需要时间，前端已设置：
- **首次加载**（模型列表未加载时）：120 秒超时
- **非首次**（模型已缓存）：30 秒超时

### 8.2 后续扩展算法

如需新增一种算法（如 `content_based`）：
1. `database/init.sql` — 已有通用算法字段，无需改表结构
2. `scripts/train/` — 添加训练脚本，输出 `_model.pkl`
3. `scripts/export/export_models_to_json.py` — 添加导出处理（如果 Node.js 端需要）
4. `scripts/recommend/train_recommend.py` — 在 `algorithms` 列表中注册
5. `scripts/import/import_recommendations.js` / `import_to_mysql.py` — 在算法循环列表添加
6. `backend/src/services/recommendEngine.js` — 加载 JSON 型号 + hybrid 加权
7. `frontend/public/user-dashboard.html` — 添加 UI 按钮

---

## 九、总结

本次重构以**低成本改动**实现了**多算法并行支持**：

| 维度 | 旧系统 | 新系统 |
|------|--------|--------|
| 数据库表数 | 2 张（单算法） | 2 张（多算法，复合主键） |
| 支持算法 | 3 种（隐式） | 4+1 种（svd / user_cf / item_cf / turbo_cf / hybrid） |
| 导入脚本 | 单算法硬编码 | 多算法循环扫描 |
| 后端模型文件 | 3 个 JSON | 4 个 JSON（+turbo_cf_model.json） |
| 前端 UI | 3 个按钮（含 hybrid） | 4 个按钮（svd / user_cf / item_cf / turbo_cf） |
| 混合推荐 | 独立逻辑 | 作为 `user_recommendation_caches` 的 hybrid 行 |

---

## 十、同步总结

本次重构全面升级推荐系统以支持 **4 种独立算法（SVD / User-CF / Item-CF / Turbo-CF）**，核心改动覆盖六层：**数据库层** — 重建 `item_similarity_caches` 和 `user_recommendation_caches` 两张缓存表，采用 `(id, algorithm)` 复合主键，允许每部电影/每个用户为每种算法独立存储结果；**训练导出层** — CSV 输出增加 `algorithm` 列，每种算法生成独立文件；**导入层** — `import_recommendations.js` 和 `import_to_mysql.py` 重写为多算法循环扫描，补齐 Turbo-CF + hybrid 数据入库；**后端服务层** — `recommendEngine.js` 新增 `turbo_cf_model.json` 加载，hybrid 加权加入 0.25 Turbo-CF 权重，`recommendService.js` 修复旧表名引用；**模型文件层** — 运行 `export_models_to_json.py` 导出缺失的 `turbo_cf_model.json`（6.0 MB），确保 Node.js 端可加载全部 4 种模型；**前端 UI 层** — AI 推荐标签移除混合推荐按钮，保留 SVD / User-CF / Item-CF / Turbo-CF 四个独立切换入口，默认算法改为 svd。系统现可在线计算+数据库缓存双通道运行 5 种算法（4 独立 + 1 混合），新增算法仅需在算法循环列表注册即可扩展。
