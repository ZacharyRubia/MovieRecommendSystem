# 评分数据导入执行脚本分析 (`run_import_ratings.py`)

## 概述

`scripts/run_import_ratings.py` 是 `import_ratings.py` 的增强版可执行包装脚本，将 MovieLens 32M 数据集的 `ratings.csv` 导入 MovieRecommendSystem 数据库。相比原始脚本，它在工程化、可观测性和兼容性上做了多重改进，并新增了幂等性设计。

## 与 `import_ratings.py` 的关键差异

| 特性 | `import_ratings.py` | `run_import_ratings.py` |
|------|--------------------|------------------------|
| 数据库驱动 | `mysql-connector-python` | `pymysql`（与后端 server.js 一致） |
| 编码处理 | 无 | 强制 stdout/stderr 为 UTF-8（修复 Windows 控制台乱码） |
| CSV 路径 | 需手动指定 | 自动定位到 `movie data/ml-32m/ratings.csv` |
| 执行进度 | 静默执行 | 显示实时百分比、耗时、块编号 |
| 用户 ID 类型 | `numpy.int64` 原始值 | 显式 `int()` 转换，兼容性更强 |
| 密码哈希 | 硬编码占位符 | 预生成 bcryptjs 哈希 (`123test` 对应 `$2b$10$...`) |
| 空块处理 | 无条件执行插入 | 检查 behavior_data 是否为空，跳过空块插入 |
| 最终统计 | 仅打印插入条数 | 执行 COUNT 查询，展示四张表最终真实数据量 + 有评分电影数 |

## 工程改进细节

### 1. 编码兼容性（Windows 专修）

```python
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
```

- **问题**：Windows 默认控制台编码为 GBK/cp936，打印中文或特殊字符会触发 `UnicodeEncodeError`
- **解决方案**：运行时检测 stdout 编码，非 UTF-8 则重新包装为 UTF-8 输出流

### 2. 路径自动发现

```python
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'ratings.csv')
```

- **相对路径**：基于脚本所在位置，向上两级到项目根目录，再向下定位到 CSV
- **前置校验**：运行前检查 CSV 是否存在，不存在则报错退出

### 3. 预生成密码哈希

```python
DEFAULT_PASSWORD_HASH = '$2b$10$J/NVJL/Y14G5OISjosvI6e65Q38QFciIib.ls5ayMVeQWlHNzmHxC'
```

- 使用后端 `bcryptjs.hashSync('123test', 10)` 预生成，所有虚拟用户密码统一为 `123test`
- 避免在导入脚本中引入 `bcrypt` 依赖

### 4. 增量进度追踪

```python
pct = total_processed / total_lines * 100
print(f"\n--- 正在处理第 {chunk_count} 块 ({total_processed:,}/{total_lines:,}, {pct:.1f}%, 已耗时 {elapsed:.0f}s) ---")
```

- 提前扫描 CSV 计算总行数
- 每块处理完输出：块序号、累计处理量、百分比、已耗时间

### 5. request_id 幂等生成

```python
str_concat = (
    chunk['userId'].astype(str)
    + chunk['movieId'].astype(str)
    + chunk['timestamp'].astype(str)
)
chunk['request_id'] = str_concat.apply(
    lambda x: hashlib.md5(x.encode('utf-8')).hexdigest()
)
```

- `request_id = MD5(userId + movieId + timestamp)` 全局唯一
- 利用 `users_movies_behaviors` 表的 `uk_request_id` 唯一索引，实现幂等插入
- 向量化字符串拼接 + `apply(md5)` 兼顾性能与可读性

### 6. 空块安全防护

```python
if behavior_data:
    cursor.executemany(behavior_insert_query, behavior_data)
    conn.commit()
else:
    print(f"   ⚠️  本块无有效评分数据，跳过行为插入")
```

- 如果某一块中所有行 `rating` 均为空，则跳过插入，避免空列表传给 `executemany` 引发异常

### 7. 最终数据验证

脚本末尾自动执行 COUNT 查询并汇总输出：

```
🎉 全部 ratings.csv 数据处理并入库完成！
============================================================
📊 最终数据库统计:
   users 表:                 162541 条
   users_movies_behaviors 表: 32000097 条
   其中评分行为:               32000097 条
   有评分的电影数:             59047 部
============================================================
```

## 工作流程

```
CSV 文件路径验证 → 扫描 CSV 获取总行数 → 连接数据库
  ↓
[循环分块读取 CSV] ← 每块 100,000 行
  ↓
步骤 A: users 表批量插入 (INSERT IGNORE)     ← 提取本块唯一 userId，生成虚拟用户
  ↓
步骤 B: 生成 request_id (MD5) + 时间戳转换
  ↓
步骤 C: users_movies_behaviors 表批量插入   ← behavior_type='rate', INSERT IGNORE
  ↓
[所有块处理完毕]
  ↓
步骤 D: 全量 JOIN 聚合更新 movies.avg_rating   ← AVG(rating) + ROUND(2)
  ↓
最终 COUNT 统计 → 断开连接
```

## 使用方法

```bash
# 在项目根目录下直接运行
set PYTHONIOENCODING=utf-8 && python scripts/run_import_ratings.py
```

### 前置条件

- Python 3.6+
- 安装依赖：`pip install pymysql pandas`
- CSV 文件位于 `movie data/ml-32m/ratings.csv`
- 数据库 `MovieRecommendSystem` 已创建，`users`、`users_movies_behaviors`、`movies` 表已按 `init.sql` 初始化
- `movies` 表已有数据（外键约束要求被引用的 movie_id 必须存在）

### 数据库配置

脚本内置配置（可根据部署环境修改）：

```python
DB_CONFIG = {
    'host': '192.168.1.38',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}
```

## 幂等性保障

所有写入操作均使用 `INSERT IGNORE`，脚本可安全重复执行：

| 场景 | 保护机制 | 说明 |
|------|---------|------|
| 用户已存在 | `users(id)` 主键冲突 | `INSERT IGNORE` 跳过 |
| 行为记录重复 | `users_movies_behaviors(uk_request_id)` 唯一索引冲突 | 相同 `userId + movieId + timestamp` 生成的 `MD5` 重复，跳过 |
| 电影平均分重复更新 | `UPDATE` 是纯幂等操作 | 多次执行结果一致 |
| 外键约束违反 | `INSERT IGNORE` 忽略 | 如果 movieId 在 movies 表中不存在，该条评分被自动跳过，起数据清洗作用 |

## 性能考量

- **分块读取**：`chunksize=100000` 控制内存峰值，32M 条评分数据无需全部加载到内存
- **批量插入**：使用 `executemany()` 代替逐行 `execute()`，减少网络往返
- **全量聚合**：单条 `JOIN + GROUP BY` SQL 替代逐电影循环 UPDATE，性能提升数个数量级
- **向量化运算**：使用 pandas 向量化字符串拼接 + `apply`，比逐行 `for` 循环快 10-100 倍

## 依赖

- `pandas` — CSV 数据加载与清洗
- `pymysql` — 数据库连接（与后端 server.js 一致，避免多驱动冲突）
- `hashlib` — 生成 MD5 幂等 request_id
- `os` + `sys` + `io` — 路径管理与编码修复
- `math` — 保留扩展（与 run_import_movies 风格一致）
- `time` — 计时与进度估算