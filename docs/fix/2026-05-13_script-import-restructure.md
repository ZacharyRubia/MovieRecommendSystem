# 脚本导入子目录重构修复记录

> 日期：2026-05-13
> 目的：将 `scripts/` 根目录下的导入脚本迁移至 `scripts/import/` 子目录后，修复因路径层级变化导致的路径配置错误

---

## 变更背景

将以下 6 个导入脚本从 `scripts/` 根目录迁移至 `scripts/import/` 子目录：

| 文件 | 原路径 | 新路径 |
|------|--------|--------|
| run_import_movies.py | `scripts/run_import_movies.py` | `scripts/import/run_import_movies.py` |
| run_import_ratings.py | `scripts/run_import_ratings.py` | `scripts/import/run_import_ratings.py` |
| run_import_tags.py | `scripts/run_import_tags.py` | `scripts/import/run_import_tags.py` |
| import_to_mysql.py | `scripts/import_to_mysql.py` | `scripts/import/import_to_mysql.py` |
| import_recommendations.js | `scripts/import_recommendations.js` | `scripts/import/import_recommendations.js` |
| extract_test_subset.py | `scripts/extract_test_subset.py` | `scripts/import/extract_test_subset.py` |

迁移后，脚本所在的目录深度从 `scripts/`（深度 2）变为 `scripts/import/`（深度 3）。所有依赖 `os.path.dirname(__file__)` 计算项目根目录的路径配置均需要相应调整。

---

## 修复详情

### 1. `scripts/import/run_import_movies.py`

**问题：** `BASE_DIR` 的 `dirname` 调用次数未随目录深度变化而增加

```
# 修复前（原 scripts/ 层级，深度 2，2 次 dirname → 项目根目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 修复后（scripts/import/ 层级，深度 3，3 次 dirname → 项目根目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- **CSV_PATH** = `BASE_DIR / 'movie data' / 'ml-32m' / 'movies.csv'`（自动跟随 BASE_DIR 修正，无需单独修改）

### 2. `scripts/import/run_import_ratings.py`

同样修复 `BASE_DIR`：

```
# 修复前（2 次 dirname）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 修复后（3 次 dirname）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- **CSV_PATH** 跟随 BASE_DIR 修正

### 3. `scripts/import/run_import_tags.py`

同样修复 `BASE_DIR`：

```
# 修复前（2 次 dirname）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 修复后（3 次 dirname）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- **CSV_PATH** 跟随 BASE_DIR 修正

### 4. `scripts/import/import_to_mysql.py`

同样修复 `BASE_DIR` 和 `EXPORT_DIR`：

```
# 修复前（2 次 dirname）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 修复后（3 次 dirname）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- **EXPORT_DIR** = `BASE_DIR / 'export'`（跟随 BASE_DIR 修正）

### 5. `scripts/import/import_recommendations.js`（Node.js）

相对路径引用修正（`__dirname` 等效于 Python 的 `dirname(__file__)`）：

```
// 修复前（原 scripts/ 层级，需要回退 2 层到项目根目录）
path.join(__dirname, '..', '..', 'backend', '.env')
const EXPORT_DIR = path.join(__dirname, '..', 'export');

// 修复后（scripts/import/ 层级，需要回退 3 层到项目根目录）
path.join(__dirname, '..', '..', '..', 'backend', '.env')
const EXPORT_DIR = path.join(__dirname, '..', '..', 'export');
```

- `.env` 配置加载路径：增加一层 `..`
- **EXPORT_DIR**：增加一层 `..`

### 6. `scripts/import/extract_test_subset.py`

**问题：** `OUTPUT_DIR` 硬编码为 `scripts/extract_test_subset_test/` 的相对路径

```
# 修复前
OUTPUT_DIR = 'scripts/extract_test_subset_test'

# 修复后（通过 BASE_DIR 计算绝对路径，与 train_recommend.py 保持一致）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUTPUT_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')
```

- 改为与 `train_recommend.py` 中的 `DATA_DIR = os.path.join(BASE_DIR, 'extract_test_subset_test')` 一致的路径计算方式

### 7. `scripts/recommend/train_recommend.py`

**问题：** 之前已修正但需要确认 `BASE_DIR` 层级正确

```
# 修复前（2 次 dirname，脚本在 scripts/recommend/，深度 2）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 修复后（3 次 dirname，准确指向项目根目录）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- 脚本位于 `scripts/recommend/` → 深度 2 → 需要 3 次 `dirname` 才能到项目根目录
- 跟随的路径：`DATA_DIR`、`MODEL_DIR`、`EXPORT_DIR`

### 8. `docs/scripts/scripts-summary.md`

更新目录结构反映新的 `scripts/import/` 子目录和 `scripts/recommend/` 子目录，并更新所有命令示例中的脚本路径前缀。

### 9. `.gitignore`

将 `extract_test_subset_test` 的忽略路径更新为 `scripts/extract_test_subset_test/`，以匹配新的输出目录位置。

```
# 修复前
extract_test_subset_test/

# 修复后
scripts/extract_test_subset_test/
```

---

## 未修改的正常文件（确认无需修复）

| 文件 | 说明 |
|------|------|
| `scripts/recommend/train/train_svd.py` | 已使用 3 次 `dirname`（路径深度 3，原版正确） |
| `scripts/recommend/train/train_itemcf.py` | 同上 |
| `scripts/recommend/train/train_usercf.py` | 同上 |
| `scripts/recommend/recommend.py` | 已使用 2 次 `dirname`（路径深度 2，原版正确） |
| `scripts/recommend/export_models_to_json.py` | 使用硬编码路径 `scripts/export/`，非 `os.path.dirname` 方式 |

---

## 提交信息

```
fix: 修复导入脚本迁移至 scripts/import/ 后的路径配置错误

将 6 个导入脚本从 scripts/ 根目录移至 scripts/import/ 子目录后，
脚本深度从 2 变为 3，导致 BASE_DIR 的 dirname 调用次数不足，
所有依赖路径计算均指向了错误的目录层级。

修复内容：
- scripts/import/run_import_movies.py    - BASE_DIR 增加 1 层 dirname
- scripts/import/run_import_ratings.py   - BASE_DIR 增加 1 层 dirname  
- scripts/import/run_import_tags.py      - BASE_DIR 增加 1 层 dirname
- scripts/import/import_to_mysql.py      - BASE_DIR/EXPORT_DIR 增加 1 层 dirname
- scripts/import/import_recommendations.js - EXPORT_DIR/.env 路径增加 1 层 ..
- scripts/import/extract_test_subset.py  - OUTPUT_DIR 改为 BASE_DIR 绝对路径计算
- scripts/recommend/train_recommend.py   - BASE_DIR 增加 1 层 dirname（修复之前遗漏）
- docs/scripts/scripts-summary.md        - 更新目录结构与命令路径
- .gitignore                             - 更新 extract_test_subset_test 忽略路径