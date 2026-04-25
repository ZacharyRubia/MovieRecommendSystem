# 电影推荐系统

## 项目结构
```
MovieRecommendSystem
├── backend/
│   ├── src/
│   │   ├── config/           # 环境变量、数据库配置
│   │   ├── controllers/      # 控制器
│   │   ├── models/           # 数据模型
│   │   ├── routes/           # 路由
│   │   ├── middleware/       # 中间件
│   │   ├── services/         # 服务
│   │   ├── utils/            # 工具
│   │   └── app.js            # Express 实例配置
│   ├── server.js             # 启动入口
│   └── package.json
├── frontend/
│   ├── public/              # 静态文件和HTML页面
│   │   ├── index.html        # 登录/注册页面
│   │   ├── user-dashboard.html  # 普通用户主页
│   │   ├── user-management.html  # 管理员用户管理页面
│   │   └── admin-login.html  # 管理员登录页面
│   ├── src/
│   │   ├── components/       # React/Vue 组件
│   │   ├── pages/            # 页面级组件
│   │   ├── App.js            # 根组件
│   │   ├── index.js          # 前端入口
│   │   └── styles/           # 全局样式
│   └── package.json
├── database/                 # 数据库初始化脚本
├── scripts/                  # 数据导入脚本
├── .gitignore
├── package.json              # 根目录统一管理脚本
└── README.md
```

## 功能特性

1. **用户注册与登录**
   - 支持用户注册，**第一个注册的用户自动成为管理员**
   - 后续注册的用户默认是普通用户
   - 登录成功后根据用户角色自动跳转：
     - 管理员 → 用户管理页面
     - 普通用户 → 普通用户主页

2. **用户角色管理（管理员功能）**
   - 在管理员页面可以查看所有用户列表
   - 支持将普通用户升级为管理员
   - 支持将管理员降级为普通用户
   - 支持添加、编辑和删除用户

## API接口

### 后端API接口
- `POST /api/register` - 用户注册（自动分配角色）
- `POST /api/login` - 用户登录（返回用户信息，包含角色）
- `POST /api/users/admin/login` - 管理员登录
- `GET /api/users` - 获取所有用户列表
- `GET /api/users/:id` - 获取单个用户信息
- `POST /api/users` - 创建新用户
- `PUT /api/users/:id` - 更新用户信息（支持修改角色）
- `DELETE /api/users/:id` - 删除用户

### 前端页面
- `index.html` - 登录/注册页面
- `user-dashboard.html` - 普通用户主页
- `user-management.html` - 管理员用户管理页面

## 启动项目

### 方式一：一键启动脚本（推荐）

#### Windows 批处理脚本
直接双击运行 `start.bat` 文件，或在命令行执行：
```bash
start.bat
```

#### PowerShell 脚本
在 PowerShell 中执行：
```powershell
.\start.ps1
```

**脚本功能：**
- ✅ 自动检测 Node.js 和 npm 环境
- ✅ 自动安装缺失的依赖（根目录、后端、前端）
- ✅ 同时启动后端和前端服务
- ✅ 清晰的启动进度和服务地址提示

---

### 方式二：手动启动

#### 1. 一键安装所有依赖
```bash
npm run install:all
```

#### 2. 同时启动前后端
```bash
npm run dev
```
后端服务将在 http://localhost:3000 启动
前端服务将在 http://localhost:8080 启动

#### 3. 访问页面
- 登录/注册：http://localhost:8080/index.html
- 普通用户主页：http://localhost:8080/user-dashboard.html
- 用户管理（仅管理员）：http://localhost:8080/user-management.html

## 数据库要求
使用MySQL数据库，执行`database/init.sql`初始化数据库，会自动创建：
- `role` 角色表，预置管理员和普通用户角色
- `users` 用户表，包含role_id字段用于标识用户角色

## 使用流程

1. 首次启动项目后，访问 http://localhost:8080/index.html
2. 注册第一个用户，这个用户会自动成为管理员
3. 使用管理员账号登录，会自动跳转到用户管理页面
4. 在用户管理页面，你可以：
   - 查看所有注册用户
   - 将普通用户升级为管理员
   - 将管理员降级为普通用户
   - 添加新用户、编辑和删除用户
5. 后续注册的用户都是普通用户，登录后会跳转到普通用户主页