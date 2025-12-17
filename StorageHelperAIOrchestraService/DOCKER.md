# Docker 部署指南

本文档介绍如何使用 Docker 部署 StorageHelperAIOrchestraService。

## 📋 前置要求

- Docker Engine 20.10+ 或 Docker Desktop
- Docker Compose 1.29+（可选，但推荐）

## 🚀 快速开始

### 方法一：使用 Docker Compose（推荐）

1. **确保配置文件存在**

   确保项目根目录下有 `.env.local` 文件，并已配置好 API Key：
   ```env
   GEMINI_EMBEDDING_API_KEY=your_actual_embedding_api_key
   GEMINI_LLM_API_KEY=your_actual_llm_api_key
   ```

   > 注意：`.env.local` 文件会被自动挂载到容器中，应用会读取此文件中的配置。

2. **启动服务**

   ```bash
   docker-compose up -d
   ```

3. **查看日志**

   ```bash
   docker-compose logs -f
   ```

4. **停止服务**

   ```bash
   docker-compose down
   ```

### 方法二：使用 Docker 命令

1. **构建镜像**

   ```bash
   docker build -t storage-helper-ai-orchestra:latest .
   ```

2. **运行容器**

   挂载 `.env.local` 文件并运行：
   ```bash
   docker run -d \
     --name storage-helper-ai-orchestra \
     -p 8888:8888 \
     -e APP_ENV=local \
     -v $(pwd)/.env.local:/app/.env.local:ro \
     storage-helper-ai-orchestra:latest
   ```

   或者直接通过环境变量传递配置（不推荐，建议使用 `.env.local` 文件）：
   ```bash
   docker run -d \
     --name storage-helper-ai-orchestra \
     -p 8888:8888 \
     -e APP_ENV=local \
     -e GEMINI_EMBEDDING_API_KEY=your_embedding_api_key \
     -e GEMINI_LLM_API_KEY=your_llm_api_key \
     -e TESSERACT_LANG=eng \
     -e STORAGE_SERVICE_URL=http://host.docker.internal:8000/internal \
     storage-helper-ai-orchestra:latest
   ```

3. **查看日志**

   ```bash
   docker logs -f storage-helper-ai-orchestra
   ```

4. **停止容器**

   ```bash
   docker stop storage-helper-ai-orchestra
   docker rm storage-helper-ai-orchestra
   ```

## 🔧 配置说明

### 环境变量

主要环境变量说明：

| 变量名 | 说明 | 默认值 | 必需 |
|--------|------|--------|------|
| `APP_ENV` | 环境类型（local/prod） | `local` | 是 |
| `GEMINI_EMBEDDING_API_KEY` | Gemini Embedding API Key | - | 是 |
| `GEMINI_LLM_API_KEY` | Gemini LLM API Key | - | 是 |
| `TESSERACT_LANG` | OCR 语言 | `eng` | 否 |
| `STORAGE_SERVICE_URL` | Storage Service URL | `http://localhost:8000/internal` | 否 |
| `OCR_ENABLE_PREPROCESSING` | 启用 OCR 预处理 | `True` | 否 |
| `VISION_ENABLE` | 启用 Vision API | `True` | 否 |

> **注意**：使用 Docker Compose 时，所有配置应写在 `.env.local` 文件中，该文件会被自动挂载到容器中。应用会根据 `APP_ENV=local` 自动读取 `.env.local` 文件。

### 端口映射

默认端口：`8888`

如需修改端口，在 `docker-compose.yml` 中修改：
```yaml
ports:
  - "8888:8888"  # 格式：宿主机端口:容器端口
```

### 数据持久化

如果需要持久化临时文件（文档、嵌入向量等），可以在 `docker-compose.yml` 中挂载 `tmp` 目录：

```yaml
volumes:
  - ./tmp:/app/tmp
```

## 🌐 网络配置

### 与其他服务通信

如果 StorageHelperDataStorageService 也在 Docker 中运行：

1. **使用 Docker Compose 网络**（推荐）

   在 `docker-compose.yml` 中添加：
   ```yaml
   services:
     ai-orchestra-service:
       # ... 其他配置
       networks:
         - storage-helper-network
       environment:
         - STORAGE_SERVICE_URL=http://storage-service:8000/internal
   
   networks:
     storage-helper-network:
       external: true  # 如果网络已存在
       # 或
       # name: storage-helper-network  # 创建新网络
   ```

2. **使用 host.docker.internal**（Storage Service 在宿主机）

   ```yaml
   environment:
     - STORAGE_SERVICE_URL=http://host.docker.internal:8000/internal
   ```

## 🧪 测试

服务启动后，访问以下端点验证：

1. **健康检查**
   ```bash
   curl http://localhost:8888/
   ```

2. **API 文档**
   打开浏览器访问：`http://localhost:8888/docs`

3. **测试文档处理**
   ```bash
   curl -X POST http://localhost:8888/api/v1/ingestion \
     -H "Content-Type: application/json" \
     -d '{
       "image_url": "path/to/test.pdf",
       "owner_id": 1,
       "file_type": "pdf"
     }'
   ```

## 🐛 故障排除

### 1. Tesseract 未找到

如果遇到 Tesseract 相关的错误，检查容器内是否已安装：

```bash
docker exec storage-helper-ai-orchestra tesseract --version
```

### 2. API Key 错误

确保环境变量正确设置：

```bash
docker exec storage-helper-ai-orchestra env | grep GEMINI
```

### 3. 端口被占用

如果端口 8888 已被占用，修改 `docker-compose.yml` 中的端口映射。

### 4. 查看详细日志

```bash
docker-compose logs -f ai-orchestra-service
```

### 5. 进入容器调试

```bash
docker exec -it storage-helper-ai-orchestra /bin/bash
```

## 📦 镜像优化

### 多阶段构建（可选）

如果需要减小镜像大小，可以使用多阶段构建：

```dockerfile
# 构建阶段
FROM python:3.11-slim as builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --user --no-cache-dir -r requirements.txt

# 运行阶段
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y \
    tesseract-ocr tesseract-ocr-eng poppler-utils \
    && rm -rf /var/lib/apt/lists/*
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local/bin:$PATH
CMD ["python", "main.py"]
```

## 🔒 安全建议

1. **不要将 API Key 提交到 Git**
   - 使用 `.env` 文件（已在 `.gitignore` 中）
   - 或使用 Docker secrets（生产环境）

2. **使用非 root 用户运行**（可选）

   ```dockerfile
   RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
   USER appuser
   ```

3. **限制资源使用**

   在 `docker-compose.yml` 中添加：
   ```yaml
   deploy:
     resources:
       limits:
         cpus: '2'
         memory: 2G
   ```

## 📚 相关文档

- [README.md](README.md) - 项目总体说明
- [.env.local](.env.local) - 本地环境配置文件（Docker 默认使用此文件）
- [API 文档](http://localhost:8888/docs) - 启动后的 Swagger 文档

## 💡 使用提示

- Docker Compose 会自动挂载 `.env.local` 文件到容器中
- 确保 `.env.local` 文件存在并已配置好所有必需的 API Key
- 如果需要使用生产环境配置，可以修改 `docker-compose.yml` 中的 `APP_ENV=prod` 并挂载 `.env.prod` 文件
