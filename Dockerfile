# ---------- 前端构建 ----------
FROM node:20-alpine AS frontend
WORKDIR /fe
COPY frontend/package.json ./
RUN npm install --no-audit --no-fund
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
    PORT=3000
VOLUME /data
EXPOSE 3000

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
