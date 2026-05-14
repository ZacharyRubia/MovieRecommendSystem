# AI 推荐超时 (AbortError) 修复记录

**日期**: 2026-05-14  
**问题现象**:  
```
[推荐] AI 推荐失败: The operation was aborted
[推荐] AI 无结果，降级到热门推荐
AbortError: DOMException: The operation was aborted
    at timeoutId (user-dashboard.html:704)
    at loadAiRecommendations (user-dashboard.html:722)
```

## 排查结论

**`scripts/import/import_recommendations.js` — 不涉及此次问题**

该脚本是离线导入工具，将 Python 训练脚本导出的 CSV 转换为 JSON 模型文件并存入 MySQL 和 Qdrant，不参与在线推荐请求链路。经验证：

| 配置项 | 解析路径 | 正确 |
|--------|----------|:--:|
| `.env` (`../../backend/.env`) | `backend\.env` | ✅ |
| `EXPORT_DIR` (`../../recommend/export`) | `scripts\recommend\export` | ✅ |

---

## 根因分析（3 处独立错误）

### ① 前端首次加载超时太短

- **文件**: `frontend/public/user-dashboard.html:501`
- **代码**: `tryAiRecommendation(userId, 'hybrid', 10, 60000)`
- **原因**: 首次请求需串行流式加载三个 JSON 模型文件（合计 ~91MB），然后再执行 CPU 密集的矩阵运算。60秒不够。
- **模型加载顺序与大小**:

| 预热顺序 | 模型文件 | 大小 | 加载时间（估计） |
|:--:|--------|:---:|:--------------:|
| 1 | `svd_model.json` | 26.0 MB | 流式 ~3-8s + 解析 ~2-5s |
| 2 | `user_cf_model.json` | 0.6 MB | < 1s |
| 3 | `item_cf_model.json` | 64.4 MB | 流式 ~8-15s + 解析 ~5-10s |

- **修复**: `60000` → `120000`，与后端 `REQUEST_TIMEOUT = 120000` 对齐。

### ② 前端 `loadAiRecommendations()` 超时判定脆弱

- **文件**: `frontend/public/user-dashboard.html`
- **位置**: `loadAiRecommendations()` 约第 688 行
- **原代码**:
  ```javascript
  const timeoutMs = (aiModelMeta && aiModelMeta.models) ? 30000 : 120000;
  ```
- **问题**:
  1. `aiModelMeta` 由外部的 `checkAiServiceHealth()` 异步填充，在 `loadAiRecommendations()` 调用时可能仍未赋值
  2. 若 `aiModelMeta = null`，`aiModelMeta.models` 抛出 TypeError，进入 catch 覆盖原本的超时逻辑
  3. 无法区分"首次加载（需要 ~95MB 模型）"和"后续切换算法（仅做计算，30s 足够）"
- **修复**（两处）：
  - 在 `loadAiRecommendations()` 函数内**同步自保**地预加载 `aiModelMeta`（若尚未加载）
  - 改为 `const isFirstLoad = !aiModelMeta || !aiModelMeta.models`
  - 当 `isFirstLoad` 为 `true` 时用 `120000`，否则 `30000`

### ③ 后端并发加载同一模型（无锁）

- **文件**: `backend/src/services/recommendEngine.js`
- **位置**: `loadModelAsync()` 第 46-62 行
- **原逻辑**:
  ```javascript
  async function loadModelAsync(algorithm) {
    if (_models[algorithm]) return _models[algorithm];
    // 无并发保护！warmup 和用户请求同时到达时，各起一个流式解析
    const model = await loadJsonModelAsync(filename);
    _models[algorithm] = model;
    return model;
  }
  ```
- **错误时序**:
  1. `server.js:106`: `setTimeout(() => warmupModels(), 1000)` — 启动串行预热
  2. 约 1s 后用户加载页面，浏览器发出 `GET /api/recommend/ai`
  3. `getRecommendations()` 调用 `loadModelAsync('svd')`
  4. 此时 `warmupModels()` 也正加载 `'svd'`，两者均检查 `_models['svd']` 为 `undefined`
  5. **各启动一个流式解析**，67MB 的 JSON 被 parse 两次 → 耗时翻倍 → 超时
- **修复**: 新增 `_loadingPromises` 并发保护锁
  ```javascript
  const _loadingPromises = {};
  async function loadModelAsync(algorithm) {
    if (_models[algorithm]) return _models[algorithm];
    if (_loadingPromises[algorithm]) {
      console.log(`... (awaiting existing load)`);
      return _loadingPromises[algorithm]; // 共享同一次加载
    }
    _loadingPromises[algorithm] = loadJsonModelAsync(filename).then(model => {
      _models[algorithm] = model;
      delete _loadingPromises[algorithm];
      return model;
    });
    return _loadingPromises[algorithm];
  }
  ```

---

## 修改清单

| # | 文件 | 修改内容 | 类型 |
|:-:|------|---------|:---:|
| 1 | `frontend/public/user-dashboard.html` | `tryAiRecommendation` 超时 60s → 120s | 前端 |
| 2 | `frontend/public/user-dashboard.html` | `loadAiRecommendations()` 添加 `aiModelMeta` 自保预加载 + `isFirstLoad` 判定 | 前端 |
| 3 | `backend/src/services/recommendEngine.js` | 添加 `_loadingPromises` 并发锁 | 后端 |

---

## 验证方法

1. 重启后端服务（使 `recommendEngine.js` 修改生效）:
   ```powershell
   cd backend && npm start
   ```
2. 前端页面 `user-dashboard.html` 静态托管，刷新即可生效
3. 打开浏览器 DevTools → Network 面板，观察 `recommend/ai` 请求耗时
4. 首次访问应在 ~40-60s 内返回正常结果（含模型加载时间）
5. 后续切换算法标签应在 1-5s 内返回（模型已缓存）