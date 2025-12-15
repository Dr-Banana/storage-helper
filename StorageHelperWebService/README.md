# StorageHelperWebService

家庭文件存储和管理助手的前端应用。

## 特性

- 🎨 **现代化设计** - 采用温暖的家庭风格配色方案
- 📱 **响应式布局** - 完美支持手机端和电脑端
- 🚀 **快速开发** - 基于 Vite + React + TypeScript
- 🎯 **用户友好** - 直观的界面和流畅的交互体验

## 技术栈

- **React 18** - UI 框架
- **TypeScript** - 类型安全
- **Vite** - 构建工具
- **Tailwind CSS** - 样式框架
- **React Router** - 路由管理
- **Axios** - HTTP 客户端
- **Lucide React** - 图标库

## 开始使用

### 安装依赖

```bash
npm install
```

### 开发模式

```bash
npm run dev
```

应用将在 `http://localhost:3000` 启动。

### 构建生产版本

```bash
npm run build
```

构建产物将输出到 `dist` 目录。

### 预览生产构建

```bash
npm run preview
```

## 环境配置

创建 `.env.local` 文件（可选）：

```env
VITE_API_BASE_URL=http://localhost:8000/api
```

如果不设置，默认使用 `/api`（通过 Vite 代理到 `http://localhost:8000`）。

## 项目结构

```
src/
├── api/              # API 客户端和服务
│   ├── client.ts    # Axios 配置
│   └── services.ts  # API 服务函数
├── components/      # 可复用组件
│   └── Layout.tsx   # 主布局组件
├── pages/           # 页面组件
│   ├── HomePage.tsx
│   ├── DocumentsPage.tsx
│   ├── DocumentDetailPage.tsx
│   ├── UploadPage.tsx
│   ├── SearchPage.tsx
│   ├── UsersPage.tsx
│   └── SettingsPage.tsx
├── App.tsx          # 应用入口和路由配置
├── main.tsx         # React 入口
└── index.css        # 全局样式
```

## 功能页面

- **首页** (`/`) - 仪表盘和快速操作
- **文档列表** (`/documents`) - 浏览所有文档
- **文档详情** (`/documents/:id`) - 查看文档详细信息
- **上传文档** (`/upload`) - 上传新文档
- **搜索** (`/search`) - 智能搜索文档
- **用户管理** (`/users`) - 管理用户
- **设置** (`/settings`) - 应用设置

## 配色方案

采用温暖的家庭风格配色：

- **主色**: 温暖的橙色 (`home-primary`)
- **辅助色**: 柔和的蓝色 (`home-secondary`)
- **背景**: 奶油色和米白色 (`home-background`)
- **文字**: 深棕色 (`home-text`)
- **成功**: 柔和的绿色 (`home-success`)
- **警告**: 温暖的黄色 (`home-warning`)
- **错误**: 柔和的红色 (`home-error`)

## 开发说明

### API 集成

前端通过 `/api` 路径访问后端 API。在开发模式下，Vite 会自动代理请求到 `http://localhost:8000`。

### 响应式设计

- 移动端：侧边栏自动隐藏，通过菜单按钮打开
- 平板端：适配中等屏幕尺寸
- 桌面端：侧边栏固定显示，充分利用屏幕空间

### 组件开发

所有组件都使用 TypeScript 编写，确保类型安全。样式使用 Tailwind CSS 工具类，保持一致性。

## 许可证

Part of the Home AI Paper Organizer project.
