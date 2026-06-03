# ✦ 北斗 (Beidou) — AI 网文创作工作台

> 前世抟土造人，今生敲码创世

北斗是一个全功能网文创作 Web 平台，集成 AI 辅助创作、知识库脑图管理、多格式导出、NAS 备份等功能。采用中国风 + 科技星空 UI 设计风格。

---

## ✨ 核心功能

### 📝 富文本编辑器
- TipTap 编辑器，支持 Markdown 快捷键
- 章节管理（拖拽排序、树形目录）
- 实时字数统计 + 写作目标
- 自动保存，防丢失
- 沉浸写作模式

### 🤖 AI 辅助创作
- WebSocket 流式对话（实时打字效果）
- 多 Agent 切换（可配置不同 AI 助手）
- **AI 续写** — 根据当前章节自动续写
- **AI 大纲** — 根据设定生成大纲建议
- **AI 审查** — 检查逻辑矛盾和设定冲突
- 支持任意 OpenAI 兼容 API（DeepSeek、GPT、Claude 等）

### 🧠 知识库脑图
- Canvas 自绘力导向图谱（类 Obsidian Graph View）
- 节点按类型着色：角色(金)、世界观(蓝)、剧情(绿)、伏笔(紫)
- 拖拽节点、滚轮缩放、搜索筛选
- 知识条目关联管理

### 📖 设定系统
- 三级大纲编辑器（卷→章→场景）
- 角色卡片（性格标签、关系网络）
- 世界观分类管理（势力/地理/历史/规则）
- 伏笔追踪（状态：未回收/已回收/进行中）

### 📤 多格式导出
- TXT / EPUB / DOCX / PDF / HTML
- 自动按章节分割
- 自定义排版

### 💾 备份与存储
- 本地 IndexedDB 离线存储
- WebDAV NAS 备份（群晖/威联通等）
- 手动/自动备份 + 历史记录

### 👥 用户系统
- 管理员默认账号
- 用户注册需管理员审批
- 角色权限：管理员/编辑/作者/读者

### 🎛️ 管理控制台
- Agent 管理（CRUD + 测试连通性）
- 模型配置（多提供商、测试、设默认）
- 数据库统计 + 备份
- 用户管理（审批/角色/禁用）
- 备份管理（WebDAV 配置/手动备份）

---

## 🎨 设计风格

**中国风 + 科技星空**

- 深蓝星空背景 + CSS 粒子动画
- 金色/琥珀色点缀（北斗七星意象）
- 毛玻璃效果卡片（Glassmorphism）
- 中文衬线字体标题
- 响应式设计，移动端适配

---

## 🛠️ 技术栈

| 层级 | 技术 |
|------|------|
| **前端** | React 18 + TypeScript + Vite + Tailwind CSS + shadcn/ui |
| **编辑器** | TipTap (ProseMirror) |
| **状态管理** | Zustand |
| **后端** | Python 3.12 + FastAPI + SQLAlchemy 2.0 (async) |
| **数据库** | SQLite + Alembic 迁移 |
| **AI 通信** | WebSocket 流式 + httpx |
| **部署** | Docker Compose + Nginx |

---

## 🚀 快速开始

### Docker 部署（推荐）

```bash
# 1. 克隆仓库
git clone https://github.com/luciferlihaoyu/beidou.git
cd beidou

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 修改密钥和默认密码

# 3. 一键启动
docker compose up -d

# 4. 访问
open http://localhost:8080
```

### 本地开发

**后端：**
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --port 8000
```

**前端：**
```bash
cd frontend
npm install
npm run dev
# 访问 http://localhost:5173
```

### 默认管理员

- 用户名：`admin`
- 密码：`admin123`
- 请在首次登录后立即修改密码

---

## 📁 项目结构

```
beidou/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/                # API 路由（12 个模块）
│   │   │   ├── auth.py         # 认证（注册/登录/JWT）
│   │   │   ├── novels.py       # 小说 CRUD
│   │   │   ├── chapters.py     # 章节管理
│   │   │   ├── settings.py     # 设定（大纲/角色/世界观/伏笔）
│   │   │   ├── ai.py           # AI 聊天 + 续写/大纲/审查
│   │   │   ├── agents.py       # Agent 管理
│   │   │   ├── models_config.py # 模型配置
│   │   │   ├── knowledge.py    # 知识库 + 脑图数据
│   │   │   ├── export.py       # 多格式导出
│   │   │   ├── backup.py       # WebDAV 备份
│   │   │   ├── database.py     # 数据库统计
│   │   │   └── admin.py        # 用户管理
│   │   ├── models/             # ORM 模型
│   │   ├── schemas/            # Pydantic Schema
│   │   ├── core/               # 配置 + 安全
│   │   └── services/           # 启动服务
│   ├── alembic/                # 数据库迁移
│   └── requirements.txt
├── frontend/                   # React 前端
│   ├── src/
│   │   ├── components/         # 组件
│   │   │   ├── editor/         # TipTap 编辑器
│   │   │   ├── ai/             # AI 对话面板
│   │   │   ├── knowledge/      # 知识库脑图
│   │   │   ├── settings/       # 设定组件
│   │   │   ├── layout/         # 布局（侧边栏+顶栏）
│   │   │   └── ui/             # 基础 UI 组件
│   │   ├── pages/              # 页面（13 个）
│   │   ├── store/              # Zustand 状态
│   │   └── lib/                # API 客户端
│   └── package.json
├── docker-compose.yml          # Docker 部署
├── .env.example                # 环境变量模板
└── README.md                   # 本文件
```

---

## 🔌 API 文档

启动后端后访问：`http://localhost:8000/docs`（Swagger UI）

### 主要 API 端点

| 模块 | 路径 | 说明 |
|------|------|------|
| 认证 | `POST /api/auth/register` | 注册 |
| | `POST /api/auth/login` | 登录 |
| 小说 | `GET/POST /api/novels` | 小说列表/创建 |
| 章节 | `GET/POST /api/novels/{id}/chapters` | 章节管理 |
| 设定 | `GET /api/novels/{id}/settings/outline` | 大纲树 |
| AI | `WS /api/ai/chat` | 流式对话 |
| | `POST /api/ai/continue` | AI 续写 |
| | `POST /api/ai/outline` | AI 大纲 |
| | `POST /api/ai/review` | AI 审查 |
| 知识库 | `GET /api/knowledge-bases/{id}/graph` | 脑图数据 |
| 导出 | `GET /api/novels/{id}/export?format=epub` | 导出小说 |
| 备份 | `POST /api/backup/webdav` | WebDAV 备份 |

---

## 🌟 特色亮点

1. **多 Agent 协同** — 可配置多个 AI Agent，各有独立系统提示词和模型绑定
2. **知识图谱** — 类 Obsidian 的力导向脑图，可视化知识关联
3. **全格式导出** — 一键导出 TXT/EPUB/DOCX/PDF，自动分章
4. **NAS 备份** — 支持 WebDAV 协议，连接群晖/威联通等 NAS 设备
5. **中国风 UI** — 深蓝星空 + 金色点缀 + 毛玻璃效果，东方美学与科技感融合

---

## 📄 许可证

MIT License

---

## 🙏 致谢

参考项目：
- [webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer) — Claude Code 长篇网文系统
- [novelWriter](https://github.com/vkbo/novelWriter) — 纯文本小说编辑器
- [StoryForge](https://github.com/yuanbw2025/storyforge) — AI 小说创作工作台
- [StoryForge-AI](https://github.com/eemotionn/StoryForge-AI) — 多 Agent 协同母版

---

✦ **北斗** — 由美智子（女娲）与 L 共同打造
