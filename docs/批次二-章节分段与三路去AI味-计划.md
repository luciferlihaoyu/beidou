# PLAN 批次二 · 章节分段流水线 + 三路去AI味 + 自包含报告

依据 `PLAN-技能卡融入流水线-碧霄-v1.md` §t6/t7/t8。本文件是**给新鲜子代理看的唯一契约**，
不依赖任何对话上下文。

## 0. 环境（照抄，别自己发明）

```bash
cd /data/dsh/北斗/beidou/backend
PYTHONPATH=/data/dsh/北斗/.pydeps /data/dsh/北斗/.venv-beidou/bin/python -m pytest tests/<文件>.py -q 2>&1 | grep -v "^\[security\]"
```

- 仓库 `/data/dsh/北斗/beidou`，分支 `master`，批次一已入库 `c7fdcad`。
- **禁止全量 pytest**（AGENTS.md：4C/14G，三次压垮宿主记录）。单文件 <30s 可以。
- `[security]` 开头的行是无害噪声，不是错误。
- **子代理不许 commit**：一段一提交由碧霄在验收后统一执行。
- `app/routers/ai_factory.py` 已 3196 行：**禁止整读**，`grep -n` 定位后 `sed -n 'A,Bp'` 取片段。
- 异步测试**没有 pytest-asyncio**（除基线里已用 `@pytest.mark.asyncio` 的文件），新文件统一
  在同步测试里 `asyncio.run(...)`。

## 1. 当前闸位（**必须一项不许红**，越界数必须为 0）

| 层 | 命令里的文件 | 期望 |
|---|---|---|
| 技能侧核心 | `验收-批次一.sh` 的 `LAYER1_SKILL` | **268 passed** |
| 后续阶段 | test_wire_observe + test_schemas + test_t4_wiring + test_smallfixes | **174 passed** |
| 写书链路基线 | 11 个基线文件 | **306 passed** |

验收脚本：`bash /data/dsh/北斗/验收-批次一.sh`（严格模式：**声明的测试文件缺失即失败**，
这是批次一查出我自己写的洞后改的，别退回静默跳过）。
批次二新增文件要**追加进脚本**（见每任务的"接线"小节），否则新代码不在闸内。

## 2. 已核实的事实（**子代理不必重测，但要用真实形状**）

- `app/models.py`：**没有** `chapter_pipeline`，**没有** `stage_json` → 两个都是新列，
  迁移走 `app/db.py` 的 `_ensure_column(conn, 表名, 列名, "类型")`（幂等，已有先例）。
- `app/nightly.py:134`
  `async def _generate_chapter(p, novel, job, chapter, num, db) -> dict`
  （唯一调用点在文件内；批次二改它时**保持签名不变**，行为开关从 `p.chapter_pipeline` 读）。
- `app/anti_llm.py:92 detect(text) -> dict` 返回 `{"score": float, "issues": [...]}`，
  纯本地确定性计算、**不调 LLM**。真实检测维度 **9 个**（另有「太短」是提前短路哨兵）：
  疲劳词 / 套词密度 / 句式单调 / 段落均匀 / 对话标签 / 上帝视角 / 章末总结体 / 禁用句式 / 三连排比。
  阈值是**写在 f-string 里的单点数字**（如「阈值 8」「阈值 5」），**没有**集中定义的区间表。
- `app/routers/ai_extras.py:38` `APIRouter(prefix="/api/ai-factory")`
  （**不存在 `/api/projects` 前缀**，批次一我写错过一次，别再写错）。
  文件导出的现成范式：`:514 export_pack` 用 `StreamingResponse(io.BytesIO(data), media_type=..., headers={"Content-Disposition": "attachment; filename*=UTF-8''..."})`。
- 默认模型配置：天枢 `https://tianshu.xianrealme.com` + `MiniMax-M3`。
  以该端点请求 `{"logprobs": true, "top_logprobs": 1}` → **HTTP 200 但 `choices[0]` 无 logprobs**
  （实测 keys = `['finish_reason','index','message']`）→ **困惑度路在当前模型不可用**。
  该实测 2026-11-13 已完成，不必重测；但代码必须做**能力探测**，不许写死"不可用"。

---

## 3. t6-chapter-stages — 章节内角色接力 + 断点续跑

- **goal**：一章正文可按「规划→编排→写作→审校→修订→结算」分段接力生成，**每段落盘**，
  中断后重入**跳过已完成段**。默认 `off` 时行为与现状**逐字节一致**。
- **files**
  - 改 `backend/app/models.py`：`AiProject.chapter_pipeline`（TEXT，默认 `"off"`）、
    `AiChapterJob.stage_json`（TEXT，默认 `"{}"`）
  - 改 `backend/app/db.py`：两列 `_ensure_column` 幂等迁移
  - 新建 `backend/app/chapter_stages.py`：段定义 + 执行器
  - 改 `backend/app/nightly.py`：`_generate_chapter` 内按开关分流（`off` 走原路，**原代码一行不动**）
  - 新建 `backend/tests/test_chapter_stages.py`
- **change**
  - 模式：`off`（默认）｜ `standard`（写作→审校→修订）｜ `deep`（六段全跑）
  - 每段产出**立刻**写进 `job.stage_json`，形如 `{"规划": {"text": "...", "at": "..."}}`；
    重入时已有该键的段**直接跳过**（open-skoob 的 `if (checkpoints[key]) continue` 思路）。
  - **段间只传上一段产出**，不重传全量 context（open-skoob 在此有成本缺陷，不学）。
  - 结算段产出喂给**现有** `_update_state_files`，**不许**再调一次模型。
- **红线**
  - `off` 时不得新增任何 prompt 文本、不得新增任何模型调用。
  - 不许改 `skillwire.py` / `skillcards/**` / `schemas.py`（批次一冻结面）。
  - `_WIRE_*`、`_save_project_wire`、`_save_job_wire` 不许碰。
- **verify**
  1. `pytest tests/test_chapter_stages.py -q` → 全绿。
  2. **断点续跑核心证据**：假模型函数计数——第一次跑 N 段调用 N 次；
     第二次重入调用 **0** 次且产出与第一次一致。
  3. **中断模拟**：让第 3 段抛异常，重入时只跑第 3~N 段（断言调用序列 `[3,4,...]`）。
  4. `off` 回归：`pytest tests/test_fix8_wordcount.py -q` 全绿 + 断言 prompt 无分段标记。
  5. 三层闸 268 / 174 / 306 不变。
- **接线**：把 `tests/test_chapter_stages.py` 追加进 `验收-批次一.sh` 的 `LAYER1_LATER`。

## 4. t7-deai-signals — 三路信号 + 文体区间（**诚实口径是重点**）

- **goal**：AI 味检测从「规则单路」升级为「规则 + 困惑度 + 审校师」三路，
  每路**如实标注自己的状态**，缺能力就标 `skipped` / `failed` / `uncalibrated`，**绝不伪造**。
- **files**
  - 新建 `backend/app/deai_signals.py`
  - 改 `backend/app/anti_llm.py`（只做**加法**：新增区间表与状态字段，`detect()` 的
    既有 9 维输出与分数**不许变**，否则基线 306 会红）
  - 新建 `backend/tests/test_deai_signals.py`
- **change**
  - 每路返回统一信封：`{"signal": ..., "status": "ok"|"skipped"|"failed"|"uncalibrated", "value": ..., "detail": ...}`
    （四种状态学 open-skoob 的 `SIGNAL_STATUS_TEXT`，口径要能落到人话）。
  - 第②路（困惑度）：先**能力探测**（发一次带 `logprobs` 的小请求，看响应里到底有没有
    `logprobs` 字段）；没有 → `status="skipped"` 并在 detail 写清"当前模型不返回 logprobs"。
    **禁止**用字数/词频启发式冒充困惑度——这是本任务最重要的一条红线。
  - 第③路（审校师）：LLM 调用，产出 **AI 特征** 与 **人味证据** 两组，每条**必须带原文引文**。
    失败 → `status="failed"`，不许把失败当"没问题"。
  - 文体区间：9 个维度改为 `{value, human_low, human_high, deviation, status}`。
    区间**必须有可追溯标定样本**；本仓库现在**没有样本** → 全部 `status="uncalibrated"`，
    `human_low/human_high` 留 `None`，**不许**拍脑袋填数字冒充标定。
  - 融合分数：确定性纯函数（同输入两次结果逐字节一致），权重写死在常量里并注释来源。
- **红线**
  - 报告/文案里**不许**出现"AI 概率""是 AI 生成的可能性"这类越界表述。
  - 不许真调外部模型来做测试（测试全部用假 `_chat_text`）；真实冒烟由碧霄另跑一次。
  - `anti_llm.detect()` 既有返回值不许变（基线在盯）。
- **verify**
  1. `pytest tests/test_deai_signals.py -q` → 全绿。
  2. 覆盖：① logprobs 缺失 → 降级 `skipped` 且 detail 说清原因；② 同样输入两次融合分逐字节一致；
     ③ 文案不含「AI 概率」；④ 未标定维度显示 `uncalibrated` 而不是假区间。
  3. 三层闸不变。
- **接线**：追加 `tests/test_deai_signals.py` 进 `LAYER1_LATER`。

## 5. t8-deai-report — 自包含 HTML 报告

- **goal**：检测结果导出**单个自包含 HTML**，双击可开、Ctrl+P 能存 PDF，无任何外部依赖。
- **files**
  - 新建 `backend/app/deai_report.py`（`build_report_html(result, meta) -> str`）
  - 改 `backend/app/routers/ai_extras.py`：新增 `GET /api/ai-factory/projects/{project_id}/deai-report`
  - 新建 `backend/tests/test_deai_report.py`
- **change**
  - 内联 `<style>`；**不许** `<script>`、不许外链 `http(s)` 资源、不许 `on*=` 事件属性。
  - 所有插值文本一律 `html.escape`（含原文引文——原文里的 `<script>` 必须变成可见文本）。
  - 版式顺序：覆盖率证据 → 三路信号表（状态/值/权重/说明）→ 审校师证据（AI/人味分组带引文）
    → 文体指标对照表 → 规则命中（按严重度分组）→ 检测原文。
  - 页首固定一句：**「实验风险分不是 AI 生成概率」**。
  - `skipped`/`failed`/`uncalibrated` 三种状态在表格里**要看得见**（不许只显示成功的）。
- **verify**
  1. `pytest tests/test_deai_report.py -q` → 全绿。
  2. 覆盖：① 合法 HTML 且无 `<script`/无外部 `http` 引用；② 原文中的 `<script>` 被转义成可见文本；
     ③ 固定输入产出**字节稳定**（两次 `build_report_html` 全等）；④ 三种诚实状态都出现在 HTML 里。
  3. 端点返回 `text/html; charset=utf-8` 且带 `Content-Disposition`（学 `export_pack`）。
  4. 三层闸不变。
- **接线**：追加 `tests/test_deai_report.py` 进 `LAYER1_LATER`。

---

## 6. 执行与验收次序（**碧霄亲自主持，一段一提交**）

1. t6 → 我验收 → commit ①（`feat(factory): t6 章节分段流水线`）
2. t7 → 我验收 → commit ②（`feat(deai): t7 三路信号`）
3. t8 → 我验收 → commit ③（`feat(deai): t8 自包含 HTML 报告`）
4. 三段全绿后派**独立评审**（天演，未见实现对话），关键判据做**变异自证**；
   评审出的 critical 修完再 commit ④（`fix: 批次二评审修复`）。
5. 真实模型冒烟各一次（t6 一次分段生成、t7 一次能力探测），记录 token 用量。
6. 全部做完 → 向用户汇报，**上线问题之后单独谈**（用户 2026-11-14 指令）。

## 7. 诚实纪律（批次一用血换的，第二批照样生效）

- **先红再绿**：新判据必须给出"改实现前它红"的真实输出；天生绿的要**明说是天生绿**，
  并靠变异证明它能咬。
- **变异要单点、要合法语法、要还原**：锚点先 `assert s.count(anchor)==1`；
  跑完 `diff -q` 确认逐字节还原；语法错导致的红**不算数**，作废重做。
- **期望值硬编码**，不许从实现读；测试输入要有区分力（档位别落在退化区）。
- **改了契约就要重挑输入档位**（批次一 C-1：契约变了、测试还留在 1500 的退化区，
  给我演了一出"543 passed 全绿"的假成功）。
- **声明的测试文件不许静默缺席**：新增测试文件必须进验收脚本。
- 判据要**盯对地方**：批次一 4 条 major 全是"我以为测试在盯，其实盯的是另一侧"。
  新增行为时先问一句：**这条性质现在被哪条断言守着？**
