# 墨韵 · 小说写作器

一款轻量级的小说创作辅助工具。帮你管理小说、章节、人物和世界观设定，内置 AI 写作助手。

## 功能

| 模块 | 功能 |
|------|------|
| 📚 书库管理 | 多作品管理，字数/章节统计 |
| ✍️ 编辑器 | 实时写作，字数统计，插入标记 |
| 🤖 AI 助手 | 人物生成 / 世界观设定 / 大纲生成 / 场景扩写 |
| 🔄 模型切换 | 每个 AI 模块可选不同模型（DeepSeek / Kimi / MiniMax 等） |
| 📎 参考材料 | 支持上传 .docx 文件或飞书文档作为 AI 参考 |
| 👤 角色库 | 管理角色人设卡 |
| 🌍 设定集 | 管理世界观设定 |

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/luciferlihaoyu/novelwriter-web.git
cd novelwriter-web

# 安装依赖
pip install -r requirements.txt

# 启动
python3 main.py
```

服务默认运行在 `http://localhost:3000`。

### 环境变量（可选）

| 变量 | 用途 |
|------|------|
| `DEEPSEEK_API_KEY_WEB_NOVEL` | AI 写作（优先） |
| `DEEPSEEK_API_KEY` | AI 写作（备选，与系统共享） |
| `KIMI_API_KEY` | 审核模块（kimi-k2.6） |
| `ZEABUR_AI_HUB_API_KEY` | gemini/claude 等备用模型 |
| `MINIMAX_API_KEY` | MiniMax 备用模型 |
| `FEISHU_APP_ID` | 飞书文档读取 |
| `FEISHU_APP_SECRET` | 飞书文档读取 |

## 部署

### Zeabur（推荐）

1. 连接 GitHub 仓库
2. 设置启动命令：`uvicorn main:app --host 0.0.0.0 --port $PORT`
3. 添加环境变量（见上表）

### 手动部署

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 3000
```

## 技术栈

- **后端：** Python + FastAPI + SQLite
- **前端：** 原生 JavaScript（零依赖）
- **AI 模型：** DeepSeek / Kimi / MiniMax / Gemini 等
- **语言：** 简体中文（全界面）

## 许可证

MIT
