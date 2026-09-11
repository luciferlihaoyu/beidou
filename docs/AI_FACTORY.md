# AI 工厂（AI Factory）· 自动化写作模块设计方案

> 状态：方案设计 v2（调研已用真实仓库 README 精读补强）
> 定位：与「人工写作」平行的第二体系——从立项到正文全流程 AI 驱动
> 调研日期：2026-09-10（DeepSeek 联网搜索已通，三个重点项目 README 逐一精读）

---

## 0. 调研结论（已验证的真实项目）

### 0.1 三个重点精读的开源项目

#### ① [YILING0013/AI_NovelGenerator](https://github.com/YILING0013/AI_NovelGenerator) —— 中文圈最活跃（雪花写作法）

**四步流水线**（GUI 按钮即阶段）：
1. **生成设定** → `Novel_setting.txt`（世界观/角色/触发点/伏笔）
2. **生成目录** → `Novel_directory.txt`（章标题+短提示）
3. **生成章节草稿** → 向量检索召回相关上下文保证连贯 → `outline_X.txt` + `chapter_X.txt`
4. **定稿** → 同步更新 **4 个状态文件**：`global_summary.txt`（全局摘要）、`character_state.txt`（角色状态）、向量库、`plot_arcs.txt`（情节弧）

**最值得我们抄的两个设计**：
- **多模型任务路由**（`choose_configs`）：`architecture_llm`（架构）/ `chapter_outline_llm`（章纲）/ `prompt_draft_llm`（草稿）/ `final_chapter_llm`（正文）/ `consistency_review_llm`（审校）分别配不同模型——**便宜模型跑大纲和审校，贵模型只写正文**，成本优化核心手段
- **状态文件四分**：全局摘要 / 角色状态 / 情节弧 / 向量库，每章定稿后全量更新——比单一滚动摘要更完整的长程一致性方案
- 一致性审校是**可选按钮**而非强制步骤（用户自选何时跑）
- dev-2 分支在试**雪花写作法 + 角色弧光理论 + 悬念三要素模型**（大纲方法论可作 v2 选项）

#### ② [GOAT-AI-lab/GOAT-Storytelling-Agent](https://github.com/GOAT-AI-lab/GOAT-Storytelling-Agent) —— 场景级生成范式

**流水线**（代码级 API，可逐阶段人工介入）：
```
init_book_spec(topic)           → Genre/Place/Time/Theme/Tone/POV/Characters/Premise 八字段
→ enhance_book_spec             → 充实设定
→ create_plot_chapters          → 三幕结构（Act 1/2/3）章节大纲
→ enhance_plot_chapters         → 细化
→ split_chapters_into_scenes    → 每章拆场景，场景九字段结构化：
                                   Characters/Place/Time/Event/Conflict/
                                   StoryValue/ValueCharge/Mood/Outcome
→ write_a_scene(逐场景生成)      → 传入 previous_scene 衔接
→ continue_a_scene              → 场景太长时断点续写
```
- **无人监督生成了 20 部中篇**（HF 数据集 `GOAT-AI/generated-novels`）——质量基线已验证
- **核心启示**：章 → 场景的二次拆分让单次生成长度可控（每场景 ~800-1500 字），质量显著优于整章一把梭

#### ③ [ponysb/91Writing](https://github.com/ponysb/91Writing) —— 中文网文工具链最全（Vue3 纯前端）

**直接可抄的交互设计**：
- **上下文手动选择**："AI 上下文连贯性可手动选择多章，默认自动关联前两章"——生成时用户可控关联范围
- **续写字数滑块 200-5000 字**：实时流式输出可随时停止
- **提示词库 + 变量系统**：分类管理（大纲/正文/润色/对话），动态变量替换（小说名/角色/世界观自动填充）
- **Token 计费管理**：按模型、按功能的成本分析 + 预算限额
- **章节三状态**：草稿(橙)/完成(绿)/发表(蓝)
- **拆书分析模块**：导入 TXT/DOCX 逆向分析优秀作品（综合/结构/人物/语言/情节 5 维度）——可作我们 v3 的"风格学习"

### 0.2 框架与闭源产品（形态参考）

| 项目 | 借鉴点 |
|---|---|
| [mattparlane/gpt-author](https://github.com/mattparlane/gpt-author) | 最朴素的完整闭环：prompt→梗概→逐章大纲→逐章正文→打包，证明流水线可行 |
| [stanford-oval/storm](https://github.com/stanford-oval/storm) | 调研→大纲树→逐节写作→校验的系统方法论 |
| [SillyTavern](https://github.com/SillyTavern/SillyTavern) | Lorebook 关键词召回 + token 预算分配机制 |
| Sudowrite / Novelcrafter / 彩云小梦 / Midreal | Story Bible、Codex、中文续写体验、互动分支 |

### 0.3 调研后的 6 条设计结论

1. **流水线闭环已被多方验证**（gpt-author / GOAT / AI_NovelGenerator），架构不用发明，把"设定→目录→逐章→定稿"接好即可。
2. **长程一致性的行业标准答案 = 状态文件四分**（全局摘要 + 角色状态 + 情节弧 + 召回），每章定稿后全量更新（AI_NovelGenerator 实证）。
3. **场景级二次拆分显著提升质量**（GOAT 实证：章→场景九字段→逐场景写→超长续写）。v1 先整章生成，v2 引入场景拆分。
4. **多模型任务路由是成本命门**：大纲/审校用便宜模型、正文用贵模型（choose_configs 直接可抄，天枢网关模型池足够）。
5. **人工断点必须有但应可选**：一致性审校做成可选按钮，"全自动模式"做成显式开关（默认关）。
6. **上下文范围用户可控**：默认"最近 2 章 + 状态文件"，允许手动加选任意章节（91Writing 模式）。

---

## 1. 总体原则

1. **两体系分离**：AI 工厂与人工写作共享数据模型（Novel/Chapter/Character/Worldview），但入口、路由、流程、UI 完全独立。AI 生成的内容落到同一套表，人工可随时接管编辑。
2. **AI 主导、人类把关**：每个阶段产出都进入"待确认"状态，用户可：通过 / 重新生成 / 手动编辑后通过。
3. **字数目标可选**（本方案核心要求，见 §4）：总字数 / 卷字数 / 章字数 **全部为可选项**，不填 = 自由模式；填了 = 软约束（注入 prompt + 进度显示），绝不硬性阻断生成。
4. **复用现有资产**：AI 调用走现有 `ai.py` + AIConfig 多提供商体系；知识舱实体直接作为上下文注入源；不引入新 npm/pip 重依赖（不装 langgraph，v1 用自研状态机，理由见 §7）。

---

## 2. 模块架构

```
┌────────────────────────── 北斗 Beidou ──────────────────────────┐
│                                                                 │
│  ┌────────── 人工写作（现有）──────────┐  ┌──── AI 工厂（新）────┐ │
│  │  Bookshelf → Editor               │  │  AIFactory 独立页面  │ │
│  │  章节/知识舱/番茄/废纸篓…          │  │  立项→大纲→生成→审校 │ │
│  └───────────────┬──────────────────┘  └──────────┬───────────┘ │
│                  │      共享数据模型与 AI 基础设施   │             │
│                  ▼                                  ▼             │
│         Novel / Volume / Chapter / Character / WorldviewEntry     │
│         AIConfig（多提供商） / streamPost / 知识舱注入              │
└─────────────────────────────────────────────────────────────────┘
```

- 前端：新增路由 `/factory`，顶栏加「AI 工厂」入口（与书架并列）
- 后端：新增 `backend/app/routers/ai_factory.py`（不改动现有 ai.py）
- 数据：`AiProject` / `AiChapterJob` 两张新表，外键挂到现有 Novel/Chapter

---

## 3. 流水线设计（6 阶段状态机）

```
[立项]  用户输入：一句话创意（必填）+ 字数目标（可选）+ 类型/风格偏好（可选）
   │     AI 产出（book_spec 八字段，参考 GOAT）：
   │       类型 Genre / 时代 Time / 地点 Place / 主题 Theme / 基调 Tone /
   │       视角 POV / 角色群 Characters / 核心梗概 Premise + 书名候选 ×3
   ▼     人工：选一个书名 / 重新生成 / 手动改
[设定]  AI 产出：角色卡 ×N、世界观条目 ×M（写入现有 Character/WorldviewEntry 表）
   ▼     人工：逐条确认 / 删除 / 手动补充
[大纲]  AI 产出：卷-章结构（Volume + Chapter 骨架，章 title + 剧情要点 outline）
   │     注：填了总字数/章数时，AI 按目标拆章；否则默认 3 卷 × 10 章
   │     v2 选项：雪花写作法（一句话→段落→页纲）/ 三幕结构（GOAT 模式）
   ▼     人工：大纲树可编辑（复用现有大纲编辑器）
[生成]  逐章生成（AiChapterJob）：
   │     输入 = 本章大纲 + 状态文件（§3.1）+ 知识舱召回 + 字数目标（若有）
   │     输出 = 流式正文 → 写入 chapter.content（status=writing）
   │     支持：重新生成 / 暂停 / 断点续写 / 人工接管编辑
   │     v2：章内场景级拆分（GOAT 九字段场景卡），逐场景生成
   ▼
[审校]  AI 审校员（可选按钮，参考 AI_NovelGenerator；低温度调用）：
   │     检查 = 与设定冲突 / 与前文矛盾 / 角色状态一致 / 字数达标（若设目标）
   │     产出 = issues 列表（定位到段落）
   ▼     人工：一键修复（AI 按 issues 改写）/ 忽略 / 手动改
[定稿]  章 status=done → **同步更新四个状态文件**（§3.1）→ 进度推进
        → 下一章可生成；全书完成 → 走现有导出管线
```

### 3.1 长上下文策略：状态文件四分（核心，AI_NovelGenerator 实证）

替代单一滚动摘要，每章**定稿时**由 AI 增量更新四份状态（存 AiProject 表 TEXT 字段）：

| 状态文件 | 内容 | 更新时机 | 注入策略 |
|---|---|---|---|
| `global_summary` | 全书滚动摘要（≤1500 字，新旧融合压缩） | 每章定稿 | 每章生成必带 |
| `character_state` | 角色状态表（每角色：位置/目标/伤势/关系变化/口癖） | 每章定稿 | 本章出场角色必带 |
| `plot_arcs` | 伏笔/情节弧台账（埋设章/预期回收/状态） | 每章定稿 | 全量必带（短） |
| 向量召回 | 复用现有 FTS5 + 知识舱关键词召回历史章节片段 | 实时 | 按本章大纲关键词 Top-K≤5 |

每章生成时的上下文组装（预算制）：

```
总预算（按模型上下文，如 64k token）分配：
├─ 系统指令 + 风格要求           ~1k
├─ book_spec（立项八字段，固定）   ~0.5k
├─ 状态文件三件套                 ~3k（摘要1.5k+角色1k+伏笔0.5k）
├─ 本卷大纲 + 本章大纲 + 字数目标  ~1.5k
├─ 知识舱/FTS 召回片段            ~4k
├─ 最近 N 章原文（默认 2 章，用户可手动加选任意章，91Writing 模式） ~12k
└─ 生成空间                      剩余全部
```

### 3.2 多模型任务路由（成本命门，抄 choose_configs）

AI 工厂的每一步可独立配模型（默认全部用当前 AIConfig 的模型）：

| 任务 | 建议模型档位 | 理由 |
|---|---|---|
| `setup_llm` 立项/设定 | 中档（deepseek-v4-flash） | 结构化产出，不需顶级文笔 |
| `outline_llm` 大纲 | 中档 | 结构性强 |
| `chapter_llm` 正文生成 | **高档**（deepseek-v4-pro / claude-sonnet-5） | 文笔决定质量 |
| `summary_llm` 状态文件更新 | 低档（glm-5.3-flash 级） | 压缩任务 |
| `review_llm` 一致性审校 | 低档~中档 | 检查任务 |

立项向导里给高级用户暴露这组路由配置，普通用户用默认。天枢网关模型池已覆盖全部档位。

---

## 4. 字数目标系统（可选三级 · 用户明确要求）

### 4.1 数据模型（全部 Optional）

```python
class AiProject(Base):
    # ……其他字段略
    target_total_words:   int | None = None  # 全书目标字数（可选）
    target_volume_words:  int | None = None  # 每卷目标字数（可选）
    target_chapter_words: int | None = None  # 每章目标字数（可选）
    target_volumes:       int | None = None  # 计划卷数（可选）
    target_chapters:      int | None = None  # 计划章数（可选）
```

### 4.2 行为规则

| 填写情况 | 行为 |
|---|---|
| 全部不填 | **自由模式**：大纲默认 3 卷 × 10 章；生成不注入字数要求；进度页只显示实际字数 |
| 只填总字数 | 大纲阶段 AI 自行拆分卷/章（建议值可改）；每章生成时注入"本章约 X 字" |
| 只填章字数 | 每章生成注入目标；总/卷进度条显示"∞"（无上限） |
| 只填卷数/章数 | 只影响大纲结构，不影响字数 |
| 总字数 + 章数 | 自动建议章字数 = 总 ÷ 章数（显示为建议值，可覆盖） |
| 章字数 + 章数 | 自动建议总字数 = 相乘（建议值，可覆盖） |

### 4.3 软约束语义（重要）

- **prompt 注入**：`本章目标约 {N} 字，允许 ±20% 浮动，剧情完整优先于字数精确`
- **绝不硬截断**：生成超了不砍、不够不补（补写由审校阶段建议）
- **审校检查项**：实际字数偏离目标 >50% 时列一条 warning（不阻塞）
- **UI 进度显示**：`实际 / 目标`（填了目标才显示进度条；未填只显示实际数）

---

## 5. 数据模型

```python
class AiProject(Base):
    __tablename__ = "ai_projects"
    id, user_id, novel_id          # novel_id → 现有 novels 表（生成物落这里）
    status: str                    # draft|setup|outline|writing|reviewing|done|failed
    seed_prompt: str               # 用户的一句话创意
    book_spec_json: str | None     # 立项八字段（Genre/Time/Place/Theme/Tone/POV/Characters/Premise）
    genre: str | None              # 类型（可选）
    style_notes: str | None        # 风格偏好（可选）
    # 字数目标（§4，全部可选）
    target_total_words / target_volume_words / target_chapter_words
    target_volumes / target_chapters
    outline_json: str | None       # AI 产出的大纲快照（JSON）
    # 状态文件四分（§3.1，每章定稿后 AI 增量更新）
    global_summary: str = ""       # 全书滚动摘要
    character_state: str = ""      # 角色状态表 JSON
    plot_arcs: str = ""            # 伏笔/情节弧台账 JSON
    # 多模型任务路由（§3.2，可选，NULL=用默认模型）
    setup_llm / outline_llm / chapter_llm / summary_llm / review_llm: str | None
    # 上下文控制
    context_recent_chapters: int = 2          # 默认带最近 2 章原文
    context_extra_chapters: str = "[]"        # 用户手动加选的章节 id JSON
    auto_mode: bool = False                   # 全自动模式（默认关，每章需人工 approve）
    created_at, updated_at

class AiChapterJob(Base):
    __tablename__ = "ai_chapter_jobs"
    id, project_id, chapter_id
    status: str                    # pending|writing|reviewing|needs_fix|done|failed
    outline: str                   # 本章大纲快照
    summary: str | None            # 本章 200 字摘要（定稿时生成，汇入 global_summary）
    review_issues: str | None      # 审校 issues JSON
    actual_words: int              # 实际字数
    attempt: int                   # 重试次数
    created_at, finished_at
```

---

## 6. API 设计（新 router：`/api/ai-factory`）

```
POST   /api/ai-factory/projects                    # 立项（seed + 可选字数目标）
GET    /api/ai-factory/projects                    # 我的 AI 项目列表
GET    /api/ai-factory/projects/{id}               # 详情（含进度：每章状态+字数）
POST   /api/ai-factory/projects/{id}/setup         # 阶段1：生成书名/简介/角色/世界观
POST   /api/ai-factory/projects/{id}/outline       # 阶段2：生成卷-章大纲
PUT    /api/ai-factory/projects/{id}/outline       # 人工改大纲后保存
POST   /api/ai-factory/projects/{id}/generate/{chapter_id}   # 阶段3：流式生成单章（SSE）
POST   /api/ai-factory/projects/{id}/review/{chapter_id}     # 阶段4：AI 审校
POST   /api/ai-factory/projects/{id}/fix/{chapter_id}        # 按 issues 一键修复
POST   /api/ai-factory/jobs/{job_id}/approve       # 人工确认通过 → done
POST   /api/ai-factory/jobs/{job_id}/regenerate    # 重新生成本章
DELETE /api/ai-factory/projects/{id}               # 删除项目（novel 是否连带删除由参数控制）
```

流式生成复用现有 `streamPost` SSE 模式，与 AIPanel 同一套基础设施。

---

## 7. 前端页面（`/factory`）

1. **项目列表页**：卡片 = 书名 + 状态徽章 + 进度条（实际/目标字数，未设目标则只显示实际）+ 更新时间
2. **立项向导**（3 步）：
   - 第 1 步：一句话创意（必填 textarea）
   - 第 2 步：字数目标（5 个可选数字输入 + 「不填 = 自由模式」提示 + 联动建议值）
   - 第 3 步：类型 / 风格偏好（可选 chips）
3. **工作台页**（项目详情，三栏）：
   - 左：流水线状态（6 阶段步骤条，当前阶段高亮）
   - 中：当前阶段操作区（书名候选 / 大纲树 / 章节生成流式区）
   - 右：章节任务列表（每章 = 状态 + 实际字数/目标 + 操作按钮：生成/审校/修复/接管编辑）
4. **「接管编辑」**：任何章节可一键跳转到现有 Editor 人工改（两体系的桥）

---

## 8. 技术决策（为什么不装 LangGraph）

| 决策 | 理由 |
|---|---|
| v1 自研状态机（DB 状态字段 + 前端轮询/SSE），不引 LangGraph | ① AGENTS.md：本容器禁大规模 pip 安装；② 我们流水线是**线性 6 阶段 + 人工断点**，用不上 LangGraph 的图编排；③ FastAPI + 状态字段已够。v2 若要做"分支剧情并行生成"再评估 |
| AI 调用复用现有 AIConfig | 用户已配好提供商/key；字数注入走 prompt 拼接 |
| 摘要用同一 LLM 生成 | 每章 done 后追加一次小调用（200 字摘要），成本可忽略 |
| 不装新前端依赖 | 步骤条/进度条/卡片全部现有 Radix + Tailwind 拼装 |

---

## 9. 里程碑（与 A/B 批次错峰）

| 批次 | 内容 | 预估 |
|---|---|---|
| M0（先行） | A 级 5 小修 + B 级 5 项 + 人物关系图 | 4-6 天 |
| M1 | AI 工厂骨架：数据表 + 立项向导 + setup/outline 两阶段跑通 | 3-4 天 |
| M2 | 逐章流式生成 + 滚动摘要 + 工作台页 | 3-4 天 |
| M3 | AI 审校 + 一键修复 + 进度看板 | 2-3 天 |
| M4 | 全书导出衔接 + 压测（30 章连续生成） | 1-2 天 |

M1-M4 合计约 **9-13 天**。

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 长程一致性差（角色崩/设定忘） | 滚动摘要 + 知识舱召回 + 审校兜底，三重防线 |
| 单次生成超时/断流 | SSE 断点续传：已生成部分先落库，重试时"续写"而非重来 |
| 成本失控（30 章 × 多阶段调用） | 项目级预算字段（v2）；v1 先在 UI 显示累计 token 消耗 |
| 生成质量不可控 | 人工断点：每章必须 approve 才推进（可关，设置里加"全自动模式"开关，默认关） |
| 与人工写作互相污染 | AI 项目创建独立的 Novel 记录（title 前缀「[AI]」），人工书架里可见可编辑但标记来源 |

---

# 实现记录（v3 · 2026-09-10）

> 本文档上半部分为设计方案；以下为实际交付的实现状态（M1-M7）。
> 设计部分灵感来源：AI_NovelGenerator / GOAT-Storytelling-Agent / webnovel-master（美智子作品，上官婉儿分享，源自 inkos）。

## 架构

```
frontend/src/pages/Factory.tsx            项目列表（新建/导入续写）
frontend/src/pages/FactoryProject.tsx     向导页（市场雷达→立项→设定→大纲→写作）
frontend/src/components/ChapterGenPanel.tsx  章节任务面板（生成/审校/修订/重写）
frontend/src/components/FactoryM3.tsx     批量连跑弹窗 + 追读力仪表盘 + 控制面工具条
frontend/src/components/FactoryM5.tsx     简介弹窗 + 封面弹窗
frontend/src/components/ModelRouteDialog.tsx  五路模型路由
frontend/src/components/ImportDialog.tsx  导入续写

backend/app/routers/ai_factory.py   M1/M2 核心（立项/设定/大纲/逐章生成/定稿/审校）
                                    + 共享：_update_state_files（伏笔章龄戳记）
                                    + _sync_relations_from_chapter（关系图联动）
backend/app/routers/batch.py        M3 批量连跑/增强审校/追读力 + M6 修订闭环
                                    + M7 伏笔提醒端点/lint 端点 + 质量门禁
backend/app/routers/ai_extras.py    M5 简介/市场雷达/封面 prompt+天宫通道
backend/app/routers/ai_import.py    M4 导入续写（分章+逆向真相文件）
backend/app/anti_llm.py             去 AI 味检测引擎（确定性，100 分制）
backend/app/textlint.py             文字规范检测（错别字/敏感词/重复词/标点）
```

## 数据模型关键列

- `AiProject`：status 状态机（draft→setup→outline→writing）；五路路由
  （setup/outline/chapter/summary/review_llm，值格式 `""`/`"id"`/`"id@model"`）；
  字数目标三选（全可选软约束）；状态文件五分（global_summary/character_state/
  plot_arcs[含 planted_chapter 章龄戳]/particle_ledger/subplot_board）；
  author_intent/current_focus 控制面；synopsis_json/market_json/cover_prompt
- `AiChapterJob`：status（pending/writing/done/needs_fix/failed）；
  review_score/review_issues/retention_json/summary/attempt

## 质量闭环（写→审→改）

1. 写：源头注入 ANTI_LLM_RULES + 超期伏笔提醒（自动注入生成 context）
2. 审：review-full = LLM 结构化审校 + anti_llm AI 味检测 + textlint 文字规范
3. 改：revise 一键按意见修订全文 / rewrite-partial 局部重写 / generate(instruction) 整章重写

## 批量连跑守护

- 生成失败 → 硬停（不跳章）
- AI 味 <70 → 自动去味改写（保剧情保字数）
- 质量门禁：最终成稿 AI 味 <60 记 strike，连续 2 章 → 自动暂停（`paused` 事件）

## 伏笔章龄追踪

定稿时 `_stamp_plot_arcs` 给每条伏笔记 planted_chapter（标题匹配旧台账继承）；
`_hook_alerts`：≥8 章未推进黄灯 / ≥15 章红灯；超期伏笔自动注入后续章节生成 prompt。

## 人物关系联动

定稿后 `_sync_relations_from_chapter` 从本章增量抽取关系 → CharacterRelation（source="ai"），
角色名必须匹配现有角色卡，(from,to,relation) 去重，失败不阻塞定稿。

## 外部通道

- 模型清单：`GET /api/ai/configs/{id}/models`（任意 OpenAI 兼容端点，天枢/DeepSeek 通用）
- 封面天宫通道：env `TIANGONG_BASE_URL` + `TIANGONG_SERVICE_KEY`(+`_ID`) 配置后自动提交
  `cover-generate` 任务（经天宫 beidou-external-router，tRPC + service key 认证）
- AList 自动备份：integrations 配置开启后，后台循环每 30 分钟检查、每日一次备份整库 zip

## 测试

`backend/tests/test_ai_factory_pure.py`：16 个纯函数单测
（anti_llm/textlint/分章/JSON 解析/伏笔戳记/章龄/路由格式），单文件 <1s。
