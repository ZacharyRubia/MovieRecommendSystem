# MySQL2 LIMIT/OFFSET 参数类型错误修复记录

## 问题概述

**错误代码**: `ER_WRONG_ARGUMENTS` (MySQL 错误号: 1210)

**错误消息**: `Incorrect arguments to mysqld_stmt_execute`

**发生场景**: 使用 mysql2 的 `pool.execute()` 执行带 `LIMIT ? OFFSET ?` 参数化查询时。

---

## 错误现象

### 前端表现
```
HTTP 500 Internal Server Error
页面显示: "获取电影列表失败"
```

### 后端控制台日志
```
========== [ERROR] 获取电影列表失败 ==========
[ERROR] 错误名称: Error
[ERROR] 错误消息: Incorrect arguments to mysqld_stmt_execute
[ERROR] 错误堆栈: Error: Incorrect arguments to mysqld_stmt_execute
    at query (D:\Code\MovieRecommendSystem\backend\src\config\db.js:17:29)
    at MovieModel.findAll (D:\Code\MovieRecommendSystem\backend\src\models\Movie.js:8:12)
[ERROR] SQL错误代码: ER_WRONG_ARGUMENTS
[ERROR] SQL错误编号: 1210
==================================================
```

---

## 根本原因分析

### 问题代码 (`backend/src/config/db.js`)
```javascript
// ❌ 错误写法
async function query(sql, params) {
  const [rows] = await pool.execute(sql, params);  // 使用预编译语句
  return rows;
}
```

### 原因详解

`mysql2` 驱动的 `execute()` 和 `query()` 实现机制不同：

| 方法 | 实现方式 | LIMIT/OFFSET 兼容性 |
|------|---------|---------------------|
| `pool.execute()` | **预编译语句 (Prepared Statement)**<br>SQL先发送给MySQL编译，参数后绑定 | ❌ **有Bug**<br>LIMIT/OFFSET 期望 MySQL INT 类型，<br>但 JS Number 被识别为其他类型 |
| `pool.query()` | **普通查询 (Text Protocol)**<br>参数在客户端转义后拼接成完整SQL | ✅ **无问题**<br>参数正确转换为整数 |

### 触发条件
1. 使用 `pool.execute()` 方法
2. SQL 包含 `LIMIT ?` 或 `OFFSET ?` 占位符
3. 传入的参数是 JavaScript Number 类型

---

## 修复方案

### 修改文件: `backend/src/config/db.js`

```javascript
// ✅ 修复后 - 使用 query() 替代 execute()
async function query(sql, params) {
  const [rows] = await pool.query(sql, params);
  return rows;
}
```

### 完整修复后的文件
```javascript
const mysql = require('mysql2/promise');

// 创建连接池
const pool = mysql.createPool({
  host: process.env.DB_HOST || '192.168.200.128',
  user: process.env.DB_USER || 'newuser',
  password: process.env.DB_PASSWORD || 'yourpassword',
  database: process.env.DB_NAME || 'MovieRecommendSystem',
  waitForConnections: true,
  connectionLimit: 10,
  queueLimit: 0,
  charset: 'utf8mb4'
});

// 导出查询辅助函数
// 使用 query() 而不是 execute() 避免 LIMIT/OFFSET 参数类型问题
async function query(sql, params) {
  const [rows] = await pool.query(sql, params);
  return rows;
}

module.exports = { pool, query };
```

---

## 两种方法对比表

| 对比项 | `pool.query()` | `pool.execute()` |
|--------|---------------|------------------|
| **协议类型** | 文本协议 | 二进制协议 |
| **SQL注入防护** | ✅ 安全（客户端转义） | ✅ 安全（服务端绑定） |
| **LIMIT 参数** | ✅ 正常工作 | ❌ 类型错误 |
| **单次查询性能** | ⚡ 更快 | 🐢 略慢（两次网络往返） |
| **重复查询性能** | 🐢 每次都解析 | ⚡ 缓存语句后更快 |
| **内存占用** | 低 | 高（服务端缓存语句） |
| **适用场景** | 大多数Web应用 | 大量重复相同查询 |

---

## 排查过程回顾

### 第1步：添加调试日志
在 Controller 中添加详细的 DEBUG 日志输出，确认：
- 请求参数正确解析：`page=1, limit=12, offset=0`
- 错误发生在 `Movie.findAll()` 数据库调用阶段
- 排除了参数解析错误的可能性

### 第2步：定位错误点
错误堆栈指向 `db.js:17`，即 `pool.execute()` 调用处。

### 第3步：验证Bug
根据错误码 `ER_WRONG_ARGUMENTS`，确认这是 mysql2 驱动的已知问题。

### 第4步：修复验证
替换为 `pool.query()` 后，查询正常返回结果。

---

## 相关链接

- [mysql2 GitHub Issue #1023](https://github.com/sidorares/node-mysql2/issues/1023) - LIMIT 参数类型问题
- [mysql2 文档 - Prepared Statements](https://github.com/sidorares/node-mysql2#prepared-statements)

---

## 避坑指南

### 推荐做法
1. **默认使用 `pool.query()`** - 适用于绝大多数场景
2. **只有在确实需要预编译语句时才用 `execute()`** - 如大量重复相同查询
3. **如果必须用 `execute()` 且需要 LIMIT** - 将参数转成字符串或直接拼接

### 不推荐做法
```javascript
// ❌ 不要这样做（有风险）
const [rows] = await pool.execute(
  'SELECT * FROM table LIMIT ? OFFSET ?',
  [limit, offset]
);
```

---

## 文件变更记录

| 文件路径 | 修改内容 | 修改日期 |
|---------|---------|---------|
| `backend/src/config/db.js` | 将 `pool.execute()` 改为 `pool.query()` | 2026-04-26 |
| `backend/src/controllers/moviesController.js` | 添加 DEBUG 调试日志 | 2026-04-26 |

---

## 后续建议

1. **保留调试日志** - DEBUG 日志可暂时保留，便于后续排查其他问题
2. **考虑性能优化** - 如果后续有大量重复查询需求，可考虑针对特定SQL使用 `execute()`
3. **添加集成测试** - 覆盖分页查询场景，防止回归