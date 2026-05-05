# 测试子集提取脚本说明

## 概述

`backend/test/extract_test_subset.py` 用于从 MovieRecommendSystem 数据库中提取一个可复现的测试子集，便于在开发、测试和离线推荐评估中使用。

## 文件位置

| 项目 | 路径 |
|------|------|
| 脚本 | `backend/test/extract_test_subset.py` |
| 输出目录 | `backend/test/extract_test_subset_test/` |
| 文档 | `docs/backend/test/extract_test_subset.md` |

## 提取策略

### 用户选取（活跃优先）
- 从 `users_movies_behaviors` 表中统计每个用户的评分数量
- 按评分数量降序排列，取前 N 个最活跃用户（默认 1000）

### 电影选取（热门优先）
- 收集选定用户评分过的所有电影 ID
- 从 `users_movies_behaviors` 表中统计每部电影在这些用户中的出现频率
- 按频率降序排列，取前 M 部热门电影（默认 1000）

### 行为数据过滤
- 评分：保留选定用户对选定电影的评分记录
- 评论：保留选定用户对选定电影的评论记录

## 输出文件

| 文件 | 内容 | 字段 |
|------|------|------|
| `test_users.csv` | 用户基本信息 | user_id, username, email, created_at |
| `test_movies.csv` | 电影基本信息 | movie_id, title, description, release_year, duration, avg_rating, created_at |
| `test_ratings.csv` | 评分数据 | user_id, movie_id, rating, created_at |
| `test_comments.csv` | 评论数据 | comment_id, user_id, movie_id, parent_id, content, created_at |

## 命令行参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--users` | 目标用户数 | 1000 |
| `--movies` | 目标电影数 | 1000 |
| `--seed` | 随机种子（当前用户选取未涉及随机，保留给后续扩展） | 42 |

## 使用示例

```bash
# 从 backend/test/ 目录运行
cd backend/test
python extract_test_subset.py

# 自定义抽取规模
python extract_test_subset.py --users 500 --movies 500
```

## 依赖

- Python >= 3.8
- mysql-connector-python
- pandas
- numpy

## 注意事项

1. 脚本默认连接 `192.168.1.38:3306` 上的 `MovieRecommendSystem` 数据库
2. 如需修改数据库连接信息，请编辑脚本开头的 `DB_CONFIG` 字典
3. 评论数据目前只有 4 条记录，测试子集中可能无评论数据
4. 输出路径为 `backend/test/extract_test_subset_test/`，每次运行会覆盖同名文件