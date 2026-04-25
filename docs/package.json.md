## 一、脚本（scripts）字段

这些脚本提供了**统一操作入口**，让你在根目录就能管理前后端，而不需要频繁 `cd` 切换目录。

### 1. `"install:all"`

```
"install:all": "cd backend && npm install && cd ../frontend && npm install"
```

- **作用**：依次进入 `backend` 和 `frontend` 目录，分别执行 `npm install`，安装各自依赖。
- **使用场景**：首次克隆项目后，运行 `npm run install:all` 一次性安装所有依赖。
- **缺点**：串行执行，速度较慢。更好的方式是使用 `npm workspaces` 或 `concurrently` 并行安装，但这里用了简单的 `cd && npm install` 也能工作。

### 2. `"start:backend"`

```
"start:backend": "cd backend && npm start"
```



- **作用**：进入 `backend` 目录，执行 `npm start`（通常对应 `node server.js`）。
- **使用场景**：单独启动后端服务（例如调试后端时）。

### 3. `"dev:frontend"`

```
"dev:frontend": "cd frontend && npm run dev"
```



- **作用**：进入 `frontend` 目录，执行 `npm run dev`（前端开发服务器，如 Vite/Webpack 的热更新模式）。
- **使用场景**：单独启动前端开发环境。

### 4. `"dev"`（核心）

```
"dev": "concurrently \"npm run start:backend\" \"npm run dev:frontend\""
```

- **作用**：**同时启动后端和前端**，使用 `concurrently` 这个工具在一个终端窗口里并行运行两个命令。
- **效果**：你会看到后端和前端两条日志混在一起输出（但可以区分），按 `Ctrl+C` 会同时停止两个进程。
- **为什么需要 `concurrently`**：普通的 `&` 在 Windows 上可能不工作，且无法方便地同时终止。`concurrently` 跨平台且功能更强。



## 二、工作流程示例

1. **新成员克隆项目后**：

   ```
   npm run install:all   # 安装前后端所有依赖
   ```

   

2. **日常开发**：

   ```
   npm run dev           # 一键启动全栈项目
   ```

   此时浏览器访问前端地址（如 `http://localhost:5173`），前端会向后端 API（如 `http://localhost:3000`）发送请求。

3. **仅调试后端**：

   ```
   npm run start:backend
   ```

   

4. **仅调试前端**：

   bash

   ```
   npm run dev:frontend
   ```

   

## 三、如何改进（可选建议）

如果你想更专业地管理 monorepo，可以考虑：

1. **使用 npm workspaces**（需要 Node.js 16+）：
   - 在根 `package.json` 中添加 `"workspaces": ["backend", "frontend"]`
   - 删除 `install:all` 脚本，直接运行 `npm install` 即可自动安装所有子项目依赖（并提升公共包）。
   - 使用 `npm run dev --workspace=backend` 等命令。
2. **使用更强大的工具**：pnpm workspaces、Turborepo、Lerna 等。