# 修复：评论列表获取失败 — 缺少 `is_pinned` 列

## 问题描述

普通用户无法查看电影下的评论（包括自己刚刚发表的评论），获取评论列表时接口返回失败。但新发表的评论可以正常存储到后端 `comments` 表中。

## 根因分析

### `comments` 表缺少 `is_pinned` 列

后端的 `Comment.findByMovieId` 方法执行如下 SQL：

```sql
SELECT 
  c.id, c.user_id, u.username, c.content, c.parent_id,
  c.is_pinned,             -- ← 引用 is_pinned 列
  c.created_at, c.updated_at,
  (SELECT COUNT(*) FROM comments WHERE parent_id = c.id) AS reply_count
FROM comments c
JOIN users u ON c.user_id = u.id
WHERE c.movie_id = ? AND c.parent_id IS NULL
ORDER BY c.is_pinned DESC, c.created_at DESC   -- ← 引用 is_pinned 列
LIMIT ? OFFSET ?
```

而数据库中实际的 `comments` 表结构为：

| 列名 | 类型 | 说明 |
|------|------|------|
| id | bigint unsigned | 主键 |
| user_id | bigint unsigned | 用户ID |
| movie_id | bigint unsigned | 电影ID |
| parent_id | bigint unsigned | 父评论ID |
| content | text | 评论内容 |
| request_id | varchar(64) | 幂等请求ID |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |

**缺少 `is_pinned` 列**，导致 SQL 执行时报错：

```
Unknown column 'c.is_pinned' in 'field list'
```

错误被 `catch` 捕获后返回 `"获取评论失败"`。

### 为什么新增评论正常？

`Comment.create` 方法的 INSERT 语句仅向 `content`、`user_id`、`movie_id`、`parent_id`、`request_id` 等列写入数据，**不涉及 `is_pinned`**，因此可以正常执行。

## 修复操作

在 `comments` 表中添加 `is_pinned` 列：

```sql
ALTER TABLE comments 
ADD COLUMN is_pinned TINYINT(1) NOT NULL DEFAULT 0 AFTER content;
```

### 验证

修复后 `comments` 表结构：

| 列名 | 类型 | 说明 |
|------|------|------|
| id | bigint unsigned | 主键 |
| user_id | bigint unsigned | 用户ID |
| movie_id | bigint unsigned | 电影ID |
| parent_id | bigint unsigned | 父评论ID |
| content | text | 评论内容 |
| **is_pinned** | **tinyint(1)** | **是否置顶（0:否, 1:是）** |
| request_id | varchar(64) | 幂等请求ID |
| created_at | timestamp | 创建时间 |
| updated_at | timestamp | 更新时间 |

`findByMovieId` 查询成功返回空数组（无评论时），不再报错。

## 相关文件

| 文件 | 说明 | 是否需要修改 |
|------|------|-------------|
| `database/init.sql` | 数据库初始化 DDL，已包含 `is_pinned` 列定义 | ✅ 无需修改 |
| `backend/src/models/Comment.js` | 评论模型，SQL 引用 `is_pinned` | ✅ 无需修改 |
| `backend/server.js` | 后端入口 | ✅ 无需修改 |

## 预防措施

- 数据库 DDL 变更应通过 `ALTER TABLE` 在已有环境同步执行，而不仅限于 `init.sql`
- `init.sql` 已在创建 `comments` 表时包含 `is_pinned` 列，新部署的环境不会出现此问题
- 现有环境迁移脚本可考虑增加检查：对比 `init.sql` 定义与实际表结构，自动补全缺失列