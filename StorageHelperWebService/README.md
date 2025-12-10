# StorageHelperWebService

前端 Web 服务和用户界面，用于 Home AI Paper Organizer 系统。

## 技术栈

- **Framework**: Next.js 14+ (App Router)
- **UI Library**: Shadcn/ui (Tailwind CSS)
- **State Management**: Zustand
- **Data Fetching**: TanStack Query (React Query)
- **Form Handling**: React Hook Form
- **Language**: TypeScript

## 功能特性

### 已实现
- ✅ 基于用户ID的登录系统（无需密码）
- ✅ 用户验证（通过 DataStorage Service API）
- ✅ Session 管理（Zustand + localStorage）
- ✅ 受保护的路由（AuthGuard）
- ✅ 基础 UI 组件（Button, Input, Card）
- ✅ 仪表板页面（基础结构）

### 开发中
- 🔄 文档上传界面
- 🔄 文档搜索界面
- 🔄 文档管理界面
- 🔄 位置管理界面

## 快速开始

### 安装依赖

```bash
npm install
```

### 配置环境变量

复制 `.env.example` 为 `.env.local` 并配置：

```env
NEXT_PUBLIC_AI_SERVICE_URL=http://localhost:8001
NEXT_PUBLIC_DATA_STORAGE_SERVICE_URL=http://localhost:8000
```

### 运行开发服务器

```bash
npm run dev
```

访问 [http://localhost:3000](http://localhost:3000)

## 项目结构

```
StorageHelperWebService/
├── app/                    # Next.js App Router
│   ├── (dashboard)/       # 受保护的路由组
│   │   ├── layout.tsx     # Dashboard 布局
│   │   └── dashboard/     # 仪表板页面
│   ├── login/             # 登录页面
│   ├── layout.tsx         # 根布局
│   ├── page.tsx           # 首页（重定向到登录）
│   └── globals.css        # 全局样式
├── components/            # React 组件
│   ├── ui/               # Shadcn/ui 基础组件
│   ├── auth-guard.tsx    # 路由保护组件
│   └── providers.tsx    # 全局 Providers
├── lib/                  # 工具和配置
│   ├── api/              # API 客户端
│   │   ├── client.ts     # 基础 API 客户端
│   │   └── auth.ts       # 认证相关 API
│   ├── store/            # Zustand stores
│   │   └── authStore.ts  # 认证状态管理
│   └── utils.ts          # 工具函数
└── types/                # TypeScript 类型定义
```

## API 集成

### AI Orchestration Service
- **Ingestion API**: `POST /api/ingestion`
  - 用于文档上传和处理

### DataStorage Service
- **User Validation**: `GET /api/users/{user_id}`
  - 用于验证用户是否存在
- **User Documents**: `GET /api/users/{user_id}/documents`
  - 用于获取用户的文档列表

## 认证流程

1. 用户在登录页面输入用户ID
2. 前端调用 DataStorage Service 验证用户是否存在
3. 如果用户存在，将用户ID存储到 Zustand store（持久化到 localStorage）
4. 重定向到仪表板
5. 受保护的路由通过 AuthGuard 组件检查认证状态

## 开发说明

### 添加新的 UI 组件

使用 Shadcn/ui CLI 添加组件：

```bash
npx shadcn-ui@latest add [component-name]
```

### 添加新的 API 端点

在 `lib/api/` 目录下创建新的 API 客户端文件，使用 TanStack Query hooks。

### 状态管理

- **服务器状态**（文档、位置等）：使用 TanStack Query
- **客户端状态**（UI 状态、认证）：使用 Zustand

## 构建和部署

### 构建生产版本

```bash
npm run build
```

### 启动生产服务器

```bash
npm start
```

## 许可证

[待定]
