# 技能卡包（skillcards）

AI 面板里那些「技能卡」的**完整包**都在这个目录。每张卡是一个目录：

```
skillcards/<slug>/
  SKILL.md              工作手册（YAML frontmatter + 正文），注入 prompt 的主体
  references/*.md       分维度检查清单/框架（手册里按维度指定加载）
  assets/*.md           产出模板（如拆书报告模板）
  scripts/*.py          辅助脚本（当前环境是否可执行见下）
```

## 来源与更新

卡包来自 **luciferlihaoyu/novel-skill-cards** 仓库。本目录下的文件与上游
`skills/<slug>/` **逐字节一致**，北斗侧不对卡内容做改写——要改卡就改上游再同步，
避免出现「仓库里两版内容、不知道哪版生效」。

> ⚠️ 曾经踩过的坑：最初同步时只搬了 `SKILL.md`，把 `references/`、`assets/`、
> `scripts/` 全丢了，而加载代码也从不读它们。结果是手册里写的「按速查表加载
> `references/xxx.md`」这一步**在 web 应用里从未真实发生**——模型看不到磁盘，
> 只能凭手册里的表格描述自由发挥。`tests/test_skill_cards.py` 里有打包完整性
> 测试盯着：手册提到的每个文件都必须存在。

同步方式：把上游 `skills/<slug>/` 整个目录覆盖到 `skillcards/<slug>/`，然后跑
`pytest tests/test_skill_cards.py`。

## 参考文件是怎么被加载的

模型没有文件系统，所以「读文件」由服务端代做（`app/routers/skills.py`）：

1. 解析手册里的**速查表**（`| 开篇、前三章 | \`references/golden-three-chapters.md\` |`），
   得到「关键词 → 参考文件」的映射；
2. 用用户这次的任务文本匹配关键词，只加载命中的那些——问「黄金三章」就只带
   开篇那份 + 强制项，不会把 6 份清单全塞进上下文；
3. 手册里声明「必须/强制」的文件（同行出现该词）**无条件加载**；
4. 一个维度都没命中（用户只说「用这个技能开始工作」）→ 全量加载，否则各维度
   清单全缺席，卡等于废的；
5. 表里写「全部加载」的行（如「全书完整拆书」）→ 连同全部 references 一起加载。

前端 AI 面板会把这份清单显示出来，用户可手动勾选覆盖自动挑选。

注入上限 `MAX_DOC_CHARS` / `MAX_TOTAL_DOC_CHARS`（见 `skills.py`）。超限时**显式
截断并标注**，绝不静默丢弃——静默截断会让模型以为看全了，是最坏的情况。

## 脚本：哪些真的会跑

`scripts/*.py` 是写给「有命令行、能读文件」的 agent 的。北斗不执行任意仓库脚本，
所以：

- **prompt 里会明确声明脚本不可执行**，并要求模型直接读正文做等价分析、
  **不要声称已运行脚本、不要编造脚本输出**（否则它会照着手册编一份统计出来）。
- 其中核心逻辑是**纯函数**的脚本，由 `app/skilltools.py` 在服务端**真实执行**，
  把实测数据作为「工具输出」附进 prompt。当前白名单：

  | 卡 | 脚本 | 做什么 |
  |---|---|---|
  | `style-fingerprint` | `scripts/style_profile.py` | 句长分布、对话占比、标点频率、高频词 |
  | `webnovel-pace-analyzer` | `scripts/pace_scan.py` | 逐章钩子/爽点信号、对话占比 |

  真实测量 ≫ 模型估计。要新增可执行脚本，就往 `skilltools.WHITELIST` 里加一条
  `slug → (脚本相对路径, 纯函数名, 粒度)`；粒度 `corpus` = 整本一起算，
  `chapter` = 逐章算再汇总。白名单之外的一律不执行。

## 单文件卡（兼容）

加载器也支持 `skillcards/<slug>.md` 这种单文件卡（没有参考文件包），方便临时丢
一张卡进来试。`CARD_META`（`app/routers/skills.py`）里登记了每张卡的显示名、
分类和一句话用途——新增卡要同时在这儿登记。
