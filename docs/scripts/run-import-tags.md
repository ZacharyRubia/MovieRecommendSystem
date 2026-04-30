# 标签数据导入执行脚本分析 (`run_import_tags.py`)

## 概述

`scripts/run_import_tags.py` 是将 MovieLens 32M 数据集的 `tags.csv` 导入 MovieRecommendSystem 数据库的可执行脚本。它将用户标签数据去重后写入 `tags` 表，建立电影-标签关联到 `movies_tags` 表，并同步用户偏好标签到 `users_preferred_tags` 表供推荐算法使用。

## 与 `import_tags.py` 的关键差异

| 特性 | `import_tags.py` | `run_import_tags.py` |
|------|-----------------|---------------------|
| 数据库驱动 | `mysql-connector-python` | `pymysql`（与后端 server.js 一致） |
| 编码处理 | 无 | 强制 stdout/stderr 为 UTF-8（修复 Windows 控制台乱码） |
| CSV 路径 | 需手动指定 | 自动定位到 `movie data/ml-32m/tags.csv` |
| 执行进度 | 静默执行 | 显示实时百分比、耗时、块编号及累计统计 |
| 标签去重 | 未处理 NaN | `dropna() + strip() + 过滤空字符串` |
| 用户偏好标签 | 仅插入 tag | 同步插入 `users_preferred_tags` 表 |
| 最终统计 | 仅打印插入条数 | 执行 COUNT 查询，展示三张表最终真实数据量 |

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
CSV_PATH = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'tags.csv')
```

- **相对路径**：基于脚本所在位置，向上两级到项目根目录，再向下定位到 CSV
- **前置校验**：运行前检查 CSV 是否存在，不存在则报错退出

### 3. NaN 标签值安全处理

```python
# 丢弃标签为 NaN 的记录，去除标签中的前后空白，丢弃空字符串
chunk = chunk.dropna(subset=['tag'])
chunk['tag'] = chunk['tag'].str.strip()
chunk = chunk[chunk['tag'] != '']
```

- **问题**：`tags.csv` 中存在 `tag` 字段为空（NaN）或纯空白字符串的记录，直接插入 MySQL 会报 `nan can not be used with MySQL` 错误
- **解决方案**：三步骤清洗——先 `dropna()` 移除 NaN，再 `strip()` 去除空白，最后过滤空字符串

### 4. 增量进度追踪

```python
pct = total_processed / total_lines * 100
print(f"\n--- 正在处理第 {chunk_count} 块 ({total_processed:,}/{total_lines:,}, {pct:.1f}%, 已耗时 {elapsed:.0f}s) ---")
```

- 提前扫描 CSV 计算总行数
- 每块处理完输出：块序号、累计处理量、百分比、已耗时间

### 5. 用户偏好标签同步

按任务要求，当用户给某电影打某个 tag 时，将该 (userId, tagId) 对同时插入 `users_preferred_tags` 表：

```python
preferred_set = set()
for row in chunk.itertuples(index=False):
    tag_id = tag_dict.get(row.tag)
    if tag_id is not None:
        preferred_set.add((int(row.userId), tag_id))

preferred_data = list(preferred_set)
if preferred_data:
    preferred_insert_query = """
    INSERT IGNORE INTO users_preferred_tags (user_id, tag_id)
    VALUES (%s, %s)
    """
```

- 使用 `set()` 自动去重，避免同一用户多次打同一标签导致冗余数据
- 使用 `INSERT IGNORE` 保障幂等性

### 6. 分批次批量插入

```python
batch_size = 10000
for i in range(0, len(movie_tag_data), batch_size):
    cursor.executemany(movie_tag_insert_query, movie_tag_data[i:i + batch_size])
    conn.commit()
```

- `movies_tags` 和 `users_preferred_tags` 可能单块包含数万条关联数据
- 每 10,000 条提交一次，平衡事务大小与网络开销

### 7. 最终数据验证

脚本末尾自动执行 COUNT 查询并汇总输出：

```
🎉 全部 tags.csv 数据处理并入库完成！
⏱️  总耗时: 77.59 秒
============================================================
📊 最终数据库统计:
   tags 表:                  131,498 条
   movies_tags 表:           1,010,241 条
   users_preferred_tags 表:  716,284 条
============================================================
```

## 工作流程

```
CSV 文件路径验证 → 扫描 CSV 获取总行数 → 连接数据库
  ↓
[循环分块读取 CSV] ← 每块 50,000 行
  ↓
步骤 A: 数据清洗 (dropna → strip → 过滤空串)
  ↓
步骤 B: users 表批量插入 (INSERT IGNORE)     ← 提取本块唯一 userId，生成虚拟用户
  ↓
步骤 C: tags 表批量插入 (INSERT IGNORE)      ← 提取本块唯一 tag 文本，插入 tag 表
  ↓
步骤 D: 查询 name→id 映射                    ← SELECT id, name FROM tags
  ↓
步骤 E: movies_tags 表批量插入 (INSERT IGNORE) ← 按 movieId + tagId 去重，分批提交
  ↓
步骤 F: users_preferred_tags 表批量插入        ← 按 userId + tagId 去重，分批提交
  ↓
[所有块处理完毕]
  ↓
最终 COUNT 统计 → 断开连接
```

## 使用方法

```bash
# 在项目根目录下直接运行
set PYTHONIOENCODING=utf-8 && python scripts/run_import_tags.py
```

### 前置条件

- Python 3.6+
- 安装依赖：`pip install pymysql pandas`
- CSV 文件位于 `movie data/ml-32m/tags.csv`
- 数据库 `MovieRecommendSystem` 已创建，`tags`、`movies_tags`、`users_preferred_tags` 表已按 `init.sql` 初始化
- `users` 表已有数据或允许插入虚拟用户

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
| 标签已存在 | `tags(name)` 唯一索引冲突 | 相同 tag 名称不会被重复插入 |
| 电影-标签关联重复 | `movies_tags(movie_id, tag_id)` 主键冲突 | 跳过 |
| 用户-标签偏好重复 | `users_preferred_tags(user_id, tag_id)` 主键冲突 | 跳过 |

## 性能考量

- **分块读取**：`chunksize=50000` 控制内存峰值，200 万条标签数据无需全部加载到内存
- **批量插入**：使用 `executemany()` 代替逐行 `execute()`，减少网络往返
- **子批次提交**：对于 `movies_tags` 和 `users_preferred_tags` 的关联数据，每 10,000 条提交一次
- **Set 去重**：使用 `set()` 在内存中自动去重，避免数据库层 `INSERT IGNORE` 带来的无效网络开销

## 依赖

- `pandas` — CSV 数据加载与清洗
- `pymysql` — 数据库连接（与后端 server.js 一致，避免多驱动冲突）
- `os` + `sys` + `io` — 路径管理与编码修复
- `time` — 计时与进度估算