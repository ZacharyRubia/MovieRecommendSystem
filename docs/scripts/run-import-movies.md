# 电影数据导入执行脚本分析 (`run_import_movies.py`)

## 概述

`scripts/run_import_movies.py` 是 `import_movies.py` 的增强版可执行包装脚本，将 MovieLens 32M 数据集的 `movies.csv` 导入 MovieRecommendSystem 数据库。相比原始脚本，它在工程化、可观测性和兼容性上做了多重改进。

## 与 `import_movies.py` 的关键差异

| 特性 | `import_movies.py` | `run_import_movies.py` |
|------|--------------------|------------------------|
| 数据库驱动 | `mysql-connector-python` | `pymysql`（与后端 server.js 一致） |
| 编码处理 | 无 | 强制 stdout/stderr 为 UTF-8（修复 Windows 控制台乱码） |
| CSV 路径 | 需手动指定 | 自动定位到 `movie data/ml-32m/movies.csv` |
| 执行进度 | 静默执行 | 详细的控制台进度输出 |
| NaN 处理 | `pandas.where` | 显式 `math.isnan` 判断，类型更安全 |
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
CSV_PATH = os.path.join(BASE_DIR, 'movie data', 'ml-32m', 'movies.csv')
```

- **相对路径**：基于脚本所在位置，向上两级到项目根目录，再向下定位到 CSV
- **前置校验**：运行前检查 CSV 是否存在，不存在则报错退出

### 3. 安全的 NaN 处理

```python
if isinstance(year, float) and math.isnan(year):
    year = None
```

- 相比 `pandas.where` 隐式转换，使用 `math.isnan` 显式判断更可靠
- 避免 PyMySQL 无法序列化 `float('nan')` 导致 `TypeError`

### 4. 批处理进度追踪

```python
total_batches = (len(movie_tag_data) + batch_size - 1) // batch_size
for i in range(0, len(movie_tag_data), batch_size):
    batch_num = i // batch_size + 1
    print(f"   - 批次 {batch_num}/{total_batches}: 已写入 {total_inserted}/{len(movie_tag_data)} 条...")
```

- 预计算总批次数
- 每批提交后实时输出进度（当前批次 / 总批次 + 累计写入量）

### 5. 最终数据验证

步骤 A/B/C 完成后，脚本自动执行 COUNT 查询并汇总输出：

```
🎉 全部 movies.csv 数据处理并入库完成！
============================================================
📊 最终数据库统计:
   movie 表:     87585 条记录
   tag 表:       19 条记录
   movie_tag 表: 147090 条关联
============================================================
```

## 工作流程

```
CSV 文件路径验证 → 数据加载与清洗 → 连接数据库
  ↓
movie 表写入 (87,585 条)      ← INSERT IGNORE
  ↓
tag 表写入 (19 个题材)        ← 过滤 + 去重 + INSERT IGNORE
  ↓
movie_tag 表写入 (147,090 条)  ← 分批 10,000 条 + INSERT IGNORE
  ↓
最终数据统计 → 断开连接
```

## 使用方法

```bash
# 在项目根目录下直接运行
set PYTHONIOENCODING=utf-8 && python scripts/run_import_movies.py
```

### 前置条件

- Python 3.6+
- 安装依赖：`pip install pymysql pandas`
- CSV 文件位于 `movie data/ml-32m/movies.csv`
- 数据库 `MovieRecommendSystem` 已创建，`movie`、`tag`、`movie_tag` 表已按 `init.sql` 初始化

### 数据库配置

脚本内置配置（可根据部署环境修改）：

```python
DB_CONFIG = {
    'host': '192.168.200.128',
    'port': 3306,
    'user': 'newuser',
    'password': 'yourpassword',
    'database': 'MovieRecommendSystem',
    'charset': 'utf8mb4'
}
```

## 幂等性保障

三张表均使用 `INSERT IGNORE`，脚本可安全重复执行：

- **移动 id 重复** → 忽略（PK 冲突）
- **tag name 重复** → 忽略（唯一索引冲突）
- **movie_tag 关联重复** → 忽略（唯一索引 `(movie_id, tag_id)` 冲突）

## 依赖

- `pandas` — CSV 数据加载与清洗
- `pymysql` — 数据库连接（与后端 server.js 一致，避免多驱动冲突）
- `os` + `sys` + `io` — 路径管理与编码修复
- `math` — NaN 值检测