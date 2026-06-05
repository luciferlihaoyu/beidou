# 北斗 (Beidou) 全面审查 + 迭代 Spec

**日期：** 2026-06-05
**状态：** 待 L 确认

---

## 一、已修复问题（2个）

| # | 问题 | 修复 |
|---|------|------|
| 1 | Dockerfile ENV 多行语法不兼容 Zeabur | 合并为单条 ENV + 续行符 |
| 2 | bcrypt 版本与 passlib 不兼容 | 固定 bcrypt==4.0.1 |

---

## 二、严重问题（必须修复才能正常运行）

### S1: 启动时 session 管理 bug
**文件：** `backend/app/main.py:30-34`
**问题：** `async_session_factory(bind=conn)` 用法错误。`async_sessionmaker` 不接受 `bind` 参数（SQLAlchemy 2.0 API 变更），会导致启动报错。
**修复：** 直接用 `async_session_factory()` 创建独立 session。

### S2: CORS 默认值 `["*"]` 在 Dockerfile 中不安全
**文件：** `Dockerfile` + `backend/app/core/config.py`
**问题：** `CORS_ORIGINS='["*"]'` 允许所有域名跨域访问，生产环境应限制。
**修复：** Dockerfile 默认值改为 `[]`，用户在 Zeabur 环境变量中设置实际域名。

---

## 三、安全问题

### SEC1: WebSocket 无认证
**文件：** `backend/app/api/ai.py` (WebSocket endpoint)
**问题：** `/api/ai/chat` WebSocket 端点没有验证 JWT token，任何人可以连接并消耗 AI 额度。
**修复：** 客户端连接时传 token 参数，服务端验证。

### SEC2: 默认密钥硬编码
**文件：** `backend/app/core/config.py`
**问题：** `SECRET_KEY` 和 `JWT_SECRET_KEY` 默认值为 `change-me-in-production`，如果用户未设置环境变量，JWT 签名可被猜测。
**修复：** 启动时检测默认值并打印警告日志，或强制要求设置。

### SEC3: API Key 明文存储
**文件：** `backend/app/models/model_config.py`
**问题：** LLM API Key 以明文存在数据库中。
**修复：** 使用 Fernet 对称加密存储，读取时解密。（本期可暂不处理，标记 TODO）

---

## 四、代码质量问题

### Q1: `_call_ai` 返回类型不一致
**文件：** `backend/app/api/ai.py`
**问题：** `_call_ai` 返回 `dict`（`{"response": content}`），但 `ai_continue`/`ai_outline`/`ai_review` 又包了一层 `{"response": response_text}`，导致嵌套 `{"response": {"response": "..."}}`。
**修复：** `_call_ai` 直接返回 `str`，各端点自行包装。

### Q2: 前端 EditorPage 直接用 fetch 而非 apiFetch
**文件：** `frontend/src/pages/EditorPage.tsx:87-106`
**问题：** `callAiAction` 直接用 `fetch` 而不是封装好的 `apiFetch`，token 管理不一致。
**修复：** 改用 `apiFetch` 或 `aiApi`。

### Q3: 前端 export 下载用 fetch + blob 绕过
**文件：** `frontend/src/lib/api.ts` (exportApi)
**问题：** 下载功能用 fetch + blob + 临时链接，复杂且容易出错。
**修复：** 简化为 `window.open` 带 auth header（或保持现状但加错误处理）。

### Q4: 缺少错误边界
**文件：** `frontend/src/App.tsx`
**问题：** 没有 React Error Boundary，组件崩溃会导致白屏。
**修复：** 添加全局 ErrorBoundary 包裹。

### Q5: 日志不足
**文件：** 多个后端文件
**问题：** 部分异常只 `logger.exception` 没有请求上下文（user_id, path 等）。
**修复：** 添加请求 ID 中间件 + 结构化日志。

---

## 五、功能缺失（本期建议新增）

### F1: 健康检查增强
当前 `/api/health` 只返回 `{"status": "ok"}`，应包含数据库连接状态、版本号、运行时间。

### F2: 数据库迁移支持
有 `alembic/` 目录但 `alembic.ini` 可能未正确配置。启动时 `create_all` 在生产环境不安全（不会处理 schema 变更）。
**修复：** 配置 alembic，启动时运行 `alembic upgrade head`。

### F3: 章节拖拽排序
前端有 `GripVertical` 图标但没有实现拖拽排序逻辑。
**修复：** 集成 `@dnd-kit/sortable` 实现拖拽。

### F4: 小说搜索和筛选
小说列表页没有搜索功能。
**修复：** 后端加 `?q=` 参数，前端加搜索框。

---

## 六、任务拆解

| 任务 | 优先级 | 预计时间 | 涉及文件 |
|------|--------|----------|----------|
| T1: 修复 startup session bug | P0 | 5min | main.py |
| T2: 修复 `_call_ai` 返回值嵌套 | P0 | 10min | ai.py |
| T3: WebSocket 认证 | P1 | 15min | ai.py, api.ts |
| T4: 添加 Error Boundary | P1 | 10min | App.tsx, new ErrorBoundary.tsx |
| T5: 前端 EditorPage 用 apiFetch | P1 | 10min | EditorPage.tsx |
| T6: 健康检查增强 | P2 | 10min | main.py |
| T7: CORS 默认值收紧 | P2 | 5min | Dockerfile, config.py |
| T8: 默认密钥警告 | P2 | 5min | config.py |
| T9: 章节拖拽排序 | P3 | 20min | EditorPage.tsx, package.json |
| T10: 小说搜索 | P3 | 15min | novels.py, NovelListPage.tsx |

---

## 七、执行计划

1. 先修 P0（T1, T2）— 部署必须
2. 再修 P1（T3, T4, T5）— 安全 + 稳定性
3. 最后 P2/P3 — 按 L 确认范围

**请 L 确认：**
- 上述问题和修复方案是否 OK？
- 哪些任务本期做，哪些推后？
- 有没有遗漏的需求？
