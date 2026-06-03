# 北斗 Zeabur 部署指南

## 方式一：Docker Compose 部署（推荐）

### 1. Fork 仓库
访问 https://github.com/luciferlihaoyu/beidou ，Fork 到自己的账号下。

### 2. 在 Zeabur 创建项目
- 登录 [Zeabur](https://zeabur.com)
- 创建新项目，选择区域（建议亚洲）

### 3. 部署后端服务
- 点击 "Add Service" → "Deploy from Git"
- 选择你 Fork 的 beidou 仓库
- **Root Directory:** `backend`
- **Build Command:** 留空（Zeabur 自动检测 Dockerfile）
- **Start Command:** 留空
- **环境变量：**

| 变量 | 值 | 说明 |
|------|-----|------|
| `APP_NAME` | `北斗` | 应用名 |
| `SECRET_KEY` | 随机生成32位字符串 | 应用密钥 |
| `JWT_SECRET_KEY` | 随机生成32位字符串 | JWT 密钥 |
| `DEFAULT_ADMIN_USERNAME` | `admin` | 默认管理员 |
| `DEFAULT_ADMIN_PASSWORD` | `你的密码` | 管理员密码 |
| `DEFAULT_ADMIN_EMAIL` | `admin@your-domain.com` | 管理员邮箱 |
| `DATABASE_URL` | `sqlite+aiosqlite:///./data/novelwriter.db` | 数据库 |
| `CORS_ORIGINS` | `["https://你的前端域名.zeabur.app"]` | CORS |

### 4. 部署前端服务
- 再次 "Add Service" → "Deploy from Git"
- 同一个 beidou 仓库
- **Root Directory:** `frontend`
- **环境变量：**

| 变量 | 值 | 说明 |
|------|-----|------|
| `VITE_API_BASE` | `https://你的后端域名.zeabur.app/api` | 后端 API 地址 |

### 5. 绑定域名
- 在 Zeabur 控制台为前后端服务分别绑定域名
- 前端域名记下来，填入后端的 `CORS_ORIGINS`

---

## 方式二：单容器部署（简单）

如果不想分开前后端，可以用单容器方式：

### Dockerfile（根目录）
```dockerfile
FROM python:3.12-slim AS backend
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ .

FROM node:20-alpine AS frontend
WORKDIR /app
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY --from=backend /app /app/backend
COPY --from=frontend /app/dist /app/frontend/dist
RUN pip install --no-cache-dir fastapi uvicorn aiofiles python-multipart pyjwt passlib bcrypt sqlalchemy aiosqlite httpx ebooklib python-docx reportlab
EXPOSE 8080
CMD ["sh", "-c "cd /app/backend && uvicorn app.main:app --host 0.0.0.0 --port 8080 & cp -r /app/frontend/dist/* /app/backend/static/ 2>/dev/null; wait"]
```

---

## 方式三：使用预构建镜像

如果不想自己构建，可以用 Docker Hub 上的镜像（需要先推送到 Docker Hub）。

---

## 部署后验证

1. 访问前端域名，应该看到登录页
2. 用 `admin` + 你设置的密码登录
3. 登录后可以看到管理控制台

## 常见问题

**Q: 前端显示白屏？**
A: 检查 `VITE_API_BASE` 环境变量是否正确指向后端

**Q: CORS 错误？**
A: 检查后端 `CORS_ORIGINS` 是否包含前端域名

**Q: 数据库报错？**
A: 确保 Zeabur 有持久化存储（Volume），否则 SQLite 数据会丢失
