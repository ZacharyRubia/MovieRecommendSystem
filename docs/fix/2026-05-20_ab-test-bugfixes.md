# A/B 测试详情页 Bug 修复记录

> 日期：2026-05-20 | 关联：`docs/backend/ab-testing-analysis-and-implementation.md`

---

## Bug 1：详情页加载卡死（无限循环）

### 症状
点击实验「详情」按钮后页面永久卡住，浏览器标签页无响应。

### 根因
两个文件中的 Beta 采样函数存在 `while(true)` 死循环：

| 文件 | 函数 | 问题 |
|------|------|------|
| `backend/src/controllers/adminController.js` | `sampleBetaApprox` | `while(true)` 无退出上限 |
| `backend/src/services/abAnalyzer.js` | `sampleBeta` | 同上 |

当输入参数为极端值（NaN、非常大的 α/β）时，`Math.log(1 - u1)` 产生 `-Infinity`，导致接受条件永远不满足，事件循环被永久阻塞。

### 修复

**1. Gamma 分布采样替换 Beta 拒绝采样**

两个文件均新增安全采样函数，用 Gamma 分布叠加（`G1 / (G1 + G2)` β 采样 Beta 分布）：

```javascript
function gammaSample(shape) {
  // Marsaglia-Tsang 算法 (shape ≥ 1) + Ahrens-Dieter 算法 (shape < 1)
  // 每次迭代硬上限 100 次，超出返回 safe fallback
}

function sampleBetaSafe(a, b) {
  // 输入 sanitize：NaN/Infinity → 1，负数 → 0.001
  const alpha = Math.max(Number.isFinite(a) ? a : 1, 0.001);
  const beta  = Math.max(Number.isFinite(b) ? b : 1, 0.001);
  const g1 = gammaSample(alpha);
  const g2 = gammaSample(beta);
  return (g1 + g2) > 1e-12 ? g1 / (g1 + g2) : 0.5;
}
```

**2. Monte Carlo 异步批量处理**

`adminController.js` 中 `bayesianApproxWinProb` 改为 `async`，200 样本为一批，批次间 `setImmediate` 让出事件循环，避免同步 Monte Carlo 阻塞 UI 响应。

修改文件：
- `backend/src/controllers/adminController.js`
- `backend/src/services/abAnalyzer.js`

---

## Bug 2：MySQL `ADD COLUMN IF NOT EXISTS` 语法不兼容

### 症状
```
1064 - You have an error in your SQL syntax near 'IF NOT EXISTS win_probability DOUBLE NULL'
```

### 根因
`ab_results` 表缺少 `win_probability` 列，但低版本 MySQL 不支持 `ADD COLUMN IF NOT EXISTS` 语法。

### 修复
`abAnalyzer.js` 中 INSERT 语句已内置容错：捕获 `ER_BAD_FIELD_ERROR` 后自动去掉 `win_probability` 列重试。

如需手动加列，执行：
```sql
ALTER TABLE ab_results ADD COLUMN win_probability DOUBLE NULL COMMENT '贝叶斯后验获胜概率' AFTER bandit_beta;
```
如果列已存在会报 "Duplicate column" 错误，忽略即可。

---

## Bug 3：前端 `.toFixed()` 崩溃

### 症状
```
加载失败: Cannot read properties of null (reading 'toFixed')
ctr.toFixed is not a function
```

### 根因
后端返回的 `latestMetrics` 中 `p_value`、`ctr_ci_lower`、`win_probability` 等字段为 `null`（而非 `undefined`）。前端守卫用 `!== undefined` 防不住 `null`，直接调用 `.toFixed()` 崩溃。

另外 `ctr` 变量在 fallback 时被赋值为字符串 `'-'`，之后又走 `ctr != null` 分支（字符串非 null）调用 `.toFixed()` → `'-'.toFixed()` 不是函数。

### 修复
两处渲染函数（实验列表 `renderABTestStrategyRow` + 详情弹窗 `showABTestDetail`）全部改为 `!= null && isFinite(v) ? (+v).toFixed(d) : '-'` 模式：

- Stacking row 层新增 `pct(v,d)` / `pctS(v,d)` 安全格式化辅助函数
- 详情弹窗层直接内联 `isFinite` 校验
- `ctr` 变量不再赋 `'-'` 后二次使用

修改文件：`frontend/public/user-management.html`

---

## Bug 4：对照组 p 值 / 获胜概率始终为 "-"

### 症状
对照组 (Control) 的 p 值列和获胜概率列始终显示 `-`，实验组正常。

### 根因
后端 `getExperimentById` 第三遍统计计算时写了：
```javascript
if (controlFb && strat.id !== control.id) { /* 只对非对照组计算 */ }
```
对照组自身被跳过，无人与之对比。

### 修复
新增第四遍处理：找到 CTR 最高的实验组，用对照组与该最佳实验组做 Z-test + Bayesian 获胜概率计算。

```javascript
// 第四遍：为对照组补算 p 值与获胜概率（与最佳实验组对比）
if (control && control.latestMetrics) {
  const cm = control.latestMetrics;
  if (cm.p_value == null) {
    const bestTreatment = exp.strategies
      .filter(s => s.id !== control.id && s.latestMetrics && s.latestMetrics.ctr != null)
      .sort((a, b) => b.latestMetrics.ctr - a.latestMetrics.ctr)[0];
    // ... Z-test + Bayesian
  }
}
```

修改文件：`backend/src/controllers/adminController.js`

---

## Bug 5：α / β 列始终显示 1.0000

### 症状
详情弹窗中 α 和 β 列始终为 `1.0000`，不随行为数据变化。

### 根因
数据库有两套 α/β：

| 来源 | 字段 | 含义 | 典型值 |
|------|------|------|--------|
| 策略表 | `st.bandit_alpha` | Thompson 自适应流量权重 | 始终 1.0 |
| 分析指标 | `m.bandit_alpha` (latestMetrics) | 离线统计 Beta 后验 | 正面次数+1 |

前端取了策略表的 `st.alpha` / `st.beta`（字段名也不对），永远 1.0。

### 修复
改为读取 `m.bandit_alpha` / `m.bandit_beta`。

修改文件：`frontend/public/user-management.html`

---

## 影响范围总结

| 文件 | 改动类型 |
|------|----------|
| `backend/src/controllers/adminController.js` | Beta 采样替换 + 对照组补算 p 值 |
| `backend/src/services/abAnalyzer.js` | Beta 采样替换 |
| `frontend/public/user-management.html` | null/undefined 安全守卫 + α/β 字段映射 |
