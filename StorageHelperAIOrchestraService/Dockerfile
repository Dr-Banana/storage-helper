# 使用 Python 3.11 作为基础镜像
FROM python:3.11-slim

# 设置工作目录
WORKDIR /app

# 安装系统依赖
# - tesseract-ocr: OCR 引擎
# - tesseract-ocr-eng: 英语语言包
# - tesseract-ocr-chi-sim: 简体中文语言包（可选）
# - libtesseract-dev: Tesseract 开发库
# - poppler-utils: PDF 处理工具（用于 PDF 转图像）
# - build-essential: 编译工具（某些 Python 包可能需要）
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-eng \
    tesseract-ocr-chi-sim \
    libtesseract-dev \
    poppler-utils \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 复制 requirements.txt 并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p tmp/documents tmp/embeddings tmp/images tmp/pdfs

# 设置环境变量
ENV PYTHONUNBUFFERED=1
ENV APP_ENV=local

# 暴露端口
EXPOSE 8888

# 启动命令
CMD ["python", "main.py"]
