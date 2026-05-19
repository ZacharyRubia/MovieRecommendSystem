# 用户页面简化 & 全部8个模型接入 — 修改总结

## 修改目标

1. **简化用户页面UI文本** — 缩短按钮/标题文字，提高易读性
2. **接入全部 8 个模型** — 将 `scripts/train/` 目录下所有训练脚本生成的模型从前端下拉框可选

---

## 涉及的修改文件

### 1. `scripts/export/export_models_to_json.py`

**变更**: 扩展导出函数，支持全部 7 个模型文件

| 模型文件名 | 导出函数 |
|---|---|
| `svd_model.pkl` | `export_svd_model` |
| `user_cf_traditional_model.pkl` | `export_user_cf_traditional_model` |
| `user_cf_improved_model.pkl` | `export_user_cf_improved_model` |
| `item_cf_traditional_model.pkl` | `export_item_cf_traditional_model` |
| `item_cf_improved_model.pkl` | `export_item_cf_improved_model` |
| `slope_one_traditional_model.pkl` | `export_slope_one_traditional_model` |
| `turbo_cf_model.pkl` | `export_turbo_cf_model` |

**关键实现**: 每个导出函数将 pickle 模型的内部数据结构（user_means, item_means, similarity matrix, biases, clusters 等）序列化为 JSON，供 Node.js 原生加载。

---

### 2. `backend/src/services/recommendEngine.js`

**变更**: 加载全部模型并修正数据结构匹配

- **MODEL_FILE_MAP** 扩展为包含全部 7 个模型 + 2 个向后兼容别名（`user_cf→user_cf_traditional`, `item_cf→item_cf_traditional`）
- **数据结构修正**: 将引擎输出字段从 `filePath` 统一为 `models`（与 `getRecommendations` 返回格式匹配）
- **冷启动 fallback**: 当模型无数据或用户无评分时，自动降级为热门推荐，且 **popular 降级也返回标准 `{ movieId, predictedRating }` 格式**，防止 enrichRecommendations 报错

---

### 3. `backend/src/controllers/recommendController.js`

**变更**: 暴露全部算法 ID

- `AVAILABLE_ALGORITHMS` 添加全部 12 条算法（含 hybrid, popular, content_based 等）
- `aiModelRecommend()` 的 `supportedAlgorithms` 数组包含全部 8 个模型 + hybrid
- 支持算法列表返回 12 种算法供前端选择

---

### 4. `frontend/public/user-dashboard.html`

**变更**: UI 简化 + 下拉框接入全部模型

**UI 文本修改**:

| 修改前 | 修改后 |
|---|---|
| `推荐给你 AI 混合推荐引擎` | `混合推荐` (h3) |
| `来源: AI 混合推荐 📊 推荐 10 部电影` | `推荐10部电影` (卡片源文本) |
| `🤖 AI 智能推荐 切换不同模型查看推荐结果` | `普通推荐` (h3) |

**算法下拉框选项** (从 3 个扩展到 10 个):

```
hybrid, svd, user_cf_traditional, user_cf_improved,
item_cf_traditional, item_cf_improved, slope_one_traditional,
turbo_cf, popular, content_based
```

---

## 测试结果

```
[models] OK count=12
  ✓ popular   (10 recs, 20.5s)
  ✓ hybrid    (10 recs, 18.0s)
  ✓ svd       (10 recs, 17.5s)
  ✓ user_cf_traditional (10 recs, 16.3s)
  ✓ user_cf_improved    (10 recs, 15.6s)
  ✓ item_cf_traditional (10 recs, 15.9s)
  ✓ item_cf_improved    (10 recs, 16.5s)
  ✓ slope_one_traditional (10 recs, 15.8s)
  ✓ turbo_cf  (10 recs, 15.6s)
Passed: 9/9
```

前端页面: 200 OK, 包含 `混合推荐`, `推荐10部电影`, `普通推荐` 文本

---

## 架构图

```
scripts/train/ (Python pickle 训练)
      ↓
export_models_to_json.py (Pickle → JSON 转换)
      ↓
backend/models/ (JSON 模型文件: svd_model.json, user_cf_traditional_model.json, ...)
      ↓
recommendEngine.js (Node.js 加载 JSON, 执行预测)
      ↓
recommendController.js (API 路由 /api/recommend/ai)
      ↓
user-dashboard.html (前端下拉框选择算法 → AJAX 请求)
```

## 注意事项

- 模型 JSON 文件约 67MB 总量，首次加载较慢（~15-20s），之后缓存到内存
- 冷启动用户无评分数据时自动降级为热门推荐
- 每个算法 API 有 120 秒超时保护