# 北斗 (Beidou) — 合并 Dockerfile
# 前后端打包在一个容器中，适合 Zeabur 等平台单服务部署

# ── Stage 1: 构建前端 ──
FROM node:20-alpine AS frontend-builder
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

# ── Stage 2: 最终镜像 ──
FROM python:3.12-slim
WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端代码
COPY backend/ .

# 复制前端构建产物到后端的 static 目录
COPY --from=frontend-builder /app/dist /app/static

# 创建数据目录
RUN mkdir -p /app/data

# 环境变量（可在 Zeabur 中覆盖）
ENV APP_NAME=北斗 \
    APP_VERSION=1.0.0 \
    DATABASE_URL=sqlite+aiosqlite:///./data/novelwriter.db \
    SECRET_KEY=change-me-in-production \
    JWT_SECRET_KEY=change-me-in-production \
    DEFAULT_ADMIN_USERNAME=admin \
    DEFAULT_ADMIN_PASSWORD=admin123 \
    DEFAULT_ADMIN_EMAIL=admin@beidou.local \
    CORS_ORIGINS='["*"]'

EXPOSE 8080

# 启动：后端 uvicorn + 静态文件服务
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
