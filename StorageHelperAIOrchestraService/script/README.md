# 启动脚本使用说明

本目录包含用于不同环境启动服务的便捷脚本。

## 📁 文件说明

### Windows (PowerShell)
- `start_local.ps1` - 使用本地测试环境配置启动（加载 `.env.local`）
- `start_prod.ps1` - 使用生产环境配置启动（加载 `.env.prod`）

### Linux/Mac (Bash)
- `start_local.sh` - 使用本地测试环境配置启动（加载 `.env.local`）
- `start_prod.sh` - 使用生产环境配置启动（加载 `.env.prod`）

## 🚀 使用方法

### Windows 用户

#### 启动本地测试环境
```powershell
.\script\start_local.ps1
```

#### 启动生产环境
```powershell
.\script\start_prod.ps1
```

如果遇到执行策略限制，运行：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Linux/Mac 用户

首次使用需要添加执行权限：
```bash
chmod +x script/start_local.sh
chmod +x script/start_prod.sh
```

#### 启动本地测试环境
```bash
./script/start_local.sh
```

#### 启动生产环境
```bash
./script/start_prod.sh
```

## 🔧 工作原理

脚本会自动：
1. 设置 `APP_ENV` 环境变量（`local` 或 `prod`）
2. 导航到项目根目录
3. 根据 `APP_ENV` 值加载对应的 `.env` 文件：
   - `APP_ENV=local` → 加载 `.env.local`
   - `APP_ENV=prod` → 加载 `.env.prod`
4. 启动 FastAPI 应用程序

## 📝 环境文件配置

确保在项目根目录有以下文件：

```
StorageHelperAIOrchestraService/
├── .env.local      # 本地测试环境配置
├── .env.prod       # 生产环境配置
└── script/
    ├── start_local.ps1
    ├── start_local.sh
    ├── start_prod.ps1
    └── start_prod.sh
```

## ⚙️ 手动设置环境变量

如果不使用脚本，也可以手动设置环境变量：

### Windows (PowerShell)
```powershell
$env:APP_ENV = "local"
python main.py
```

### Linux/Mac (Bash)
```bash
export APP_ENV=local
python main.py
```

### 或使用一行命令
```bash
# Windows
$env:APP_ENV = "prod"; python main.py

# Linux/Mac
APP_ENV=prod python main.py
```

## 🔍 验证环境

启动服务后，查看日志输出确认加载的配置文件：
```
INFO:app.core.config:Loading configuration from .env.local (APP_ENV=local)
INFO:app.core.config:Application started with environment: local
```

## ⚠️ 注意事项

1. **生产环境警告**：启动生产环境时会显示警告提示
2. **环境文件优先级**：`APP_ENV` > 默认 `.env`
3. **API Keys 安全**：确保 `.env.local` 和 `.env.prod` 已添加到 `.gitignore`
4. **相对路径**：脚本会自动处理路径，可以从任何位置执行

## 🐛 故障排查

### 问题：脚本无法找到 Python
**解决方案**：确保 Python 已安装并添加到 PATH

### 问题：找不到 .env 文件
**解决方案**：检查文件名是否正确（`.env.local` 或 `.env.prod`）

### 问题：PowerShell 禁止运行脚本
**解决方案**：
```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 问题：配置没有生效
**解决方案**：
1. 检查 `APP_ENV` 环境变量是否正确设置
2. 查看启动日志确认加载的配置文件
3. 重启服务以应用新配置

