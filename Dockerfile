# ---------- 前端构建 ----------
FROM node:20-alpine AS frontend
WORKDIR /fe
# 必须连 package-lock.json 一起纳入构建上下文，并用 npm ci 严格按锁安装。
# 原写法只 COPY package.json + npm install，会无视锁文件联网解析最新版：
# 2026-10-22 曾因此装出 react@19.3.1 + react-dom@19.3.0 的错配组合，
# 触发 React #527（版本必须完全一致），前端整体白屏。
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- 运行时 ----------
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend /fe/dist ./static

ENV DATA_DIR=/data \
    STATIC_DIR=/app/static \
    PORT=3000 \
    BEIDOU_ENV=production
VOLUME /data
EXPOSE 3000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
