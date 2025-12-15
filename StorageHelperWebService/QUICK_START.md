# 快速开始指南

## 安装和运行

### 1. 安装依赖

```bash
cd StorageHelperWebService
npm install
```

### 2. 启动开发服务器

**Windows:**
```powershell
.\scripts\start_local.ps1
```

**Linux/Mac:**
```bash
./scripts/start_local.sh
```

或者直接运行：
```bash
npm run dev
```

### 3. 访问应用

打开浏览器访问：`http://localhost:3000`

## 配置后端 API

确保后端服务（StorageHelperDataStorageService）正在运行在 `http://localhost:8000`。

前端会自动通过 Vite 代理访问 `/api` 路径。

如果需要修改 API 地址，创建 `.env.local` 文件：

```env
VITE_API_BASE_URL=http://localhost:8000/api
```

## 功能说明

### 已实现的功能

✅ 响应式布局（手机端和电脑端）
✅ 文档上传页面
✅ 文档列表页面
✅ 文档详情页面
✅ 用户管理页面
✅ 搜索页面（UI 已完成，需要后端 AI 服务支持）
✅ 设置页面

### 待完善的功能

- [ ] 文档列表的实际数据获取（需要后端 API 支持）
- [ ] 文档搜索的语义搜索功能（需要 AI 服务集成）
- [ ] 文档分类和存储位置管理
- [ ] 图片预览和 OCR 文本显示

## 设计特点

- 🎨 **温暖的家庭配色** - 使用橙色、米色等温暖色调
- 📱 **完全响应式** - 移动端侧边栏自动隐藏，桌面端固定显示
- 🎯 **用户友好** - 清晰的导航和直观的操作流程
- ⚡ **快速加载** - 基于 Vite 的快速开发体验

## 开发建议

1. 确保后端 API 服务已启动
2. 使用浏览器开发者工具查看网络请求
3. 检查控制台是否有错误信息
4. 根据实际 API 响应调整前端代码
