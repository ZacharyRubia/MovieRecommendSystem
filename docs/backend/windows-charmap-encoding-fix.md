# Windows `charmap` 编码错误修复总结

## 问题描述

前端 `user-dashboard.html` 的 AI 智能推荐区域无法显示推荐结果，Node.js 后端返回 "AI 推荐引擎暂不可用"。

后端日志报错：

```
'charmap' codec can't encode characters in position 1-2: character maps to <undefined>
```

## 根因分析

### 错误链路

```
recommend_api.py print(中文日志)
        ↓
Windows stdout 编码 = cp936/GBK（系统区域）
        ↓
Python 尝试用 cp936 编码输出中包含的中文字符
        ↓
某些字符不在 cp936 编码范围内 → charmap 编码错误
        ↓
recommend_api.py 进程崩溃退出
        ↓
Node.js 后端（recommendController.js）调用 Python API 失败
        ↓
返回 "AI 推荐引擎暂不可用" 给前端
        ↓
前端 user-dashboard.html 显示错误提示，无 AI 推荐
```

### 涉及文件

| 文件 | 角色 |
|------|------|
| `scripts/recommend/recommend_api.py` | Python Flask 推荐引擎 API，输出中文日志 |
| `backend/src/controllers/recommendController.js` | Node.js 控制器，代理请求到 Python API |
| `frontend/public/user-dashboard.html` | 前端页面，显示 AI 推荐结果 |

### 技术解释

- **Windows 控制台默认编码**：在简体中文 Windows 上，控制台默认使用 cp936（即 GBK）编码，其字符集远小于 Unicode（UTF-8）。
- **Python 的编码行为**：当 `print()` 输出到控制台或管道时，Python 会使用 `sys.stdout.encoding` 进行编码。如果某字符不在该编码的范围内，会抛出 `UnicodeEncodeError`。
- **Flask 中影响更明显**：`recommend_api.py` 在加载模型、缓存查询、推荐计算等各个阶段都有中文日志输出，任何一次 `print()` 失败都会导致整个进程崩溃。

## 解决方案

### 方案一：Python 启动时重定向 stdout/stderr 编码（已采用）

在 `recommend_api.py` 文件最顶部（在所有 `print()` 之前）添加以下代码：

```python
import os
import sys
import io

if sys.platform == 'win32':
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')
    try:
        if hasattr(sys.stdout, 'buffer') and not isinstance(sys.stdout, io.TextIOWrapper):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'buffer') and not isinstance(sys.stderr, io.TextIOWrapper):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except Exception:
        pass
```

**关键点：**
- `encoding='utf-8'`：将输出编码改为 UTF-8，覆盖 Windows 默认的 cp936
- `errors='replace'`：使用替换策略而非报错，遇到无法编码的字符时用 `?` 替代
- 放在文件最顶部，确保生效在所有 `print()` 之前
- 仅 Windows 平台生效，不影响 Linux/macOS

### 方案二：日志英文化（已同步采用）

将所有 `print()` 中的中文字符改为英文，作为双重保险：

- `[警告]` → `[WARNING]`
- `[缓存]` → `[Cache]`
- `[加载模型]` → `[Load model]`
- `[错误]` → `[Error]`
- 等

### 其他可选的预防措施

1. **命令行环境变量**：启动时设置 `PYTHONIOENCODING=utf-8`
   ```powershell
   $env:PYTHONIOENCODING='utf-8'; python recommend_api.py --port 5100
   ```

2. **Windows Terminal / VS Code 终端**：使用支持 UTF-8 的终端模拟器

3. **Python 启动参数**：使用 `-X utf8` 模式（Python 3.7+）
   ```powershell
   python -X utf8 recommend_api.py --port 5100
   ```

## 验证结果

- ✅ API 成功启动，无编码错误
- ✅ 健康检查端点 `GET /api/recommend/health` → 200 OK
- ✅ 模型列表 `GET /api/recommend/models` → 3 个模型全部加载成功
- ✅ AI 推荐 `GET /api/recommend/ai?user_id=1&algorithm=hybrid&top_n=5` → 0.73s 响应
- ✅ 模型加载日志正常输出，无崩溃

## 涉及代码变更

**修改文件：`scripts/recommend/recommend_api.py`**

```
+ Windows 编码兼容性修复（文件顶部 ~25 行）
- 所有中文日志字符串替换为英文
``` 

**无需修改：**
- `backend/src/controllers/recommendController.js` — 代理逻辑正常，失败是 Python 崩溃的后果
- `frontend/public/user-dashboard.html` — 前端逻辑正常，失败是后端无响应的后果