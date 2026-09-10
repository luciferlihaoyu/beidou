# AI 工厂（AI Factory）· 自动化写作模块设计方案

> 状态：方案设计（待批准开工）
> 定位：与「人工写作」平行的第二体系——从立项到正文全流程 AI 驱动
> 调研日期：2026-08（基于模型知识，外部搜索通道暂不可用，仓库链接需人工复核）

---

## 0. 调研结论（真实项目）

### 0.1 直接对标的开源项目

| 项目 | 地址 | 真实性 | 借鉴点 |
|---|---|---|---|
| **gpt-author** | github.com/mattparlane/gpt-author | ✅ 真实（2023 HN 热榜） | **完整 pipeline 参考**：prompt → plot 梗概 → 逐章大纲 → 逐章正文 → SD 插图 → epub 打包。流程朴素但闭环，证明"单次编排跑通全书"可行 |
| **Stanford STORM** | github.com/stanford-oval/storm | ✅ 真实（斯坦福 OVAL 实验室） | **最系统的长文自动生成方法**：perspective 调研 → 大纲树 → 逐节写作 → 引用校验。对应小说 = 设定调研 → 大纲树 → 逐章生成 → 一致性审校 |
| **SillyTavern** | github.com/SillyTavern/SillyTavern | ✅ 真实 | **Lorebook / World Info 机制**：按关键词触发的设定注入 + token 预算管理 + 深度插入位置控制。直接对标我们的知识舱增强 |
| **novelWriter** | github.com/vkbo/novelWriter | ✅ 真实 | 非 AI，纯小说写作软件。**章节-场景二级结构、字数目标系统**（它也有 project word target）值得对照 |
| **AutoGen** | github.com/microsoft/autogen | ✅ 真实 | 多 agent 对话编排：author/critic/editor 三角模式 |
| **CrewAI** | github.com/crewAIInc/crewAI | ✅ 真实 | 角色化 agent 团队 + task 串行编排，适合"主编/写手/审校"分工 |
| **LangGraph** | github.com/langchain-ai/langgraph | ✅ 真实 | 状态图工作流，支持**人工断点（human-in-the-loop）**——我们"AI 生成 + 人工确认"模式的天然载体 |

### 0.2 闭源产品（形态参考，不可借鉴代码）

| 产品 | 借鉴点 |
|---|---|
| **Sudowrite** | Story Bible（角色/世界观知识库与正文联动）；Write/Describe/Expand/Rewrite 快捷动作（我们 BubbleMenu 已有雏形） |
| **Novelcrafter** | Codex 系统 = 知识舱；按场景（scene）而非按章组织正文 |
| **彩云小梦**（彩云科技） | 中文续写体验标杆：续写 N 条候选、世界观词条、风格选择 |
| **Midreal** | 互动小说：分支选择 + 自动推进，"剧情走向选择"交互可参考 |
| **蛙蛙写作** | 中文网文向：一键成书流程（书名→简介→大纲→正文） |

### 0.3 调研对我们的 4 个核心启示

1. **流水线闭环已被验证**（gpt-author / STORM）：不需要发明新架构，把"大纲→逐章→审校"接好即可。
2. **长上下文是最大技术痛点**：100k+ 字小说远超单次上下文。解法共识 = **大纲分层注入 + 前情摘要滚动压缩**（Rolling Summary），SillyTavern 的 token 预算分配可直接抄。
3. **人工断点必须有**：全自动一次跑完质量不可控。LangGraph 的 interrupt/approve 模式是行业标准。
4. **多 agent 分工优于单 agent 长 prompt**：主编（结构）/ 写手（正文）/ 审校（一致性）三角色，AutoGen/CrewAI 范式。

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
   │     AI 产出：书名候选 ×3、简介、类型标签、主角人设草案、世界观骨架
   ▼     人工：选一个书名 / 重新生成 / 手动改
[设定]  AI 产出：角色卡 ×N、世界观条目 ×M（写入现有 Character/WorldviewEntry 表）
   ▼     人工：逐条确认 / 删除 / 手动补充
[大纲]  AI 产出：卷-章结构（Volume + Chapter 骨架，章 title + 剧情要点 outline）
   │     注：填了总字数/章数时，AI 按目标拆章；否则默认 3 卷 × 10 章
   ▼     人工：大纲树可编辑（复用现有大纲编辑器）
[生成]  逐章生成（AiChapterJob）：
   │     输入 = 本章大纲 + 前文滚动摘要 + 知识舱相关条目 + 字数目标（若有）
   │     输出 = 流式正文 → 写入 chapter.content（status=writing）
   │     支持：重新生成 / 暂停 / 人工接管编辑
   ▼
[审校]  AI 审校员（独立调用，低温度）：
   │     检查 = 与设定冲突 / 与前文矛盾 / 角色口癖一致 / 字数达标（若设目标）
   │     产出 = issues 列表（定位到段落）
   ▼     人工：一键修复（AI 按 issues 改写）/ 忽略 / 手动改
[完成]  章 status=done → 进度推进 → 下一章可生成；全书完成 → 走现有导出管线
```

### 3.1 长上下文策略（核心技术点）

每章生成时的上下文组装（预算制，仿 SillyTavern）：

```
总预算（如 32k token）分配：
├─ 系统指令 + 风格要求          ~1k
├─ 全书梗概（立项产出，固定）     ~0.5k
├─ 本卷大纲                     ~1k
├─ 本章大纲 + 字数目标           ~0.5k
├─ 知识舱命中条目（按本章大纲关键词召回，Top-K≤10） ~4k
├─ 前文滚动摘要（每章完成后 AI 压缩成 ~200 字摘要，最近 5 章全文 + 更早只留摘要） ~8k
└─ 生成空间                     剩余全部
```

- 新增表 `AiChapterJob.summary`：每章完成后生成 200 字摘要，供后续章节注入
- 摘要链 = Rolling Summary，解决 100k+ 字超长程一致性

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
    genre: str | None              # 类型（可选）
    style_notes: str | None        # 风格偏好（可选）
    # 字数目标（§4，全部可选）
    target_total_words / target_volume_words / target_chapter_words
    target_volumes / target_chapters
    outline_json: str | None       # AI 产出的大纲快照（JSON）
    created_at, updated_at

class AiChapterJob(Base):
    __tablename__ = "ai_chapter_jobs"
    id, project_id, chapter_id
    status: str                    # pending|writing|reviewing|needs_fix|done|failed
    outline: str                   # 本章大纲快照
    summary: str | None            # 完成后生成的 200 字滚动摘要
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
