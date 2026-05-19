# 任务计划：简化用户页面 + 接入全部8个模型

## 当前状态分析

### 训练脚本（8个模型）：
1. `train_svd.py` → `svd_model.pkl` ✓ 已导出为JSON
2. `train_usercf_traditional.py` → `user_cf_traditional_model.pkl` ✗ 未导出
3. `train_usercf_improved.py` → `user_cf_improved_model.pkl` ✗ 未导出
4. `train_itemcf_traditional.py` → `item_cf_traditional_model.pkl` ✗ 未导出
5. `train_itemcf_improved.py` → `item_cf_improved_model.pkl` ✗ 未导出
6. `train_turbocf.py` → `turbo_cf_model.pkl` ✓ 已导出
7. `train_slopeone_traditional.py` → `slope_one_traditional_model.pkl` ✗ 未导出
8. `train_slopeone_improved.py` → `slope_one_improved_model.pkl` ✗ 未导出

### 导出脚本 (export_models_to_json.py)：
- 只导出 4 个模型：svd, user_cf, item_cf, turbo_cf
- 且 `user_cf_model.pkl` 和 `item_cf_model.pkl` 不存在于训练脚本输出中
- 需要添加：user_cf_traditional, user_cf_improved, item_cf_traditional, item_cf_improved, slope_one_traditional, slope_one_improved

### 推荐引擎 (recommendEngine.js)：
- 只加载 4 个 JSON 模型：svd, user_cf, item_cf, turbo_cf
- 只有 4 个推荐函数：recommendSVD, recommendUserCF, recommendItemCF, recommendTurboCF
- 需要添加新的推荐函数

### 前端页面 (user-dashboard.html)：
- "推荐给你" → 改为 "混合推荐"
- "AI 混合推荐引擎 推荐 10 部电影" → 改为 "推荐10部电影"
- "🤖 AI 智能推荐 切换不同模型查看推荐结果" → 改为 "普通推荐"
- 4个标签页 → 改为8个标签页

## 执行步骤
- [x] 分析当前架构
- [ ] Step 1: 修改 export_models_to_json.py — 添加新模型导出函数
- [ ] Step 2: 修改 recommendEngine.js — 添加新模型加载 + 推荐函数
- [ ] Step 3: 修改 recommendController.js — 更新算法列表
- [ ] Step 4: 修改 user-dashboard.html — 简化UI + 增加8个算法标签
- [ ] Step 5: 运行测试