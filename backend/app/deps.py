from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import AiChapterJob, AiProject, Chapter, Novel, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


async def ensure_chapter_in_project(p: AiProject, chapter_id: int | None, db: AsyncSession) -> Chapter:
    """u0a：章节归属校验族的**单一入口**——判定"这一章属于这个项目"，放行返回 Chapter，否则 404。

    病根回顾：deai-report 的短路洞（`if p.novel_id and ...`）被复制成 N 份拷贝散在
    batch.py / ai_factory.py——各处只校验 job.project_id，取章后不再校验章节归属，
    他人书的角色卡/书名元数据被带进上下文。修法若在各处各写一份 if，就是把判据再
    复制一遍；所有调用点必须收敛到本函数（结构闸见 test_u0a_attribution.py）。

    归属语义（两条**正向证据**，任一成立即放行，不是串行"查不到才退回"）：
    - A 项目直接证据：存在 AiChapterJob(project_id == p.id AND chapter_id == 该章)，
      **且该章所挂小说属于项目主人**（Novel.user_id == p.user_id）。
      这张表逐章绑死项目↔章节，是唯一不因解绑/换绑失真的证据。精确到那一条 job
      指向那一章——绝不写成"本项目有任意一条 job 就放行一切章"。
      为什么 A 必须与 owner 合取（u0a 实测裁决，勿"顺手放宽"回字面 A∨B）：job 行
      可被攻击形状污染——间谍在**自己**项目里塞一条 chapter_id 指向他人书的 job
      （外键不校验跨用户归属），字面 A 会把它当真证据放行他人整章正文（monkeypatch
      对拍实证：字面入口对 no_novel/bound/rebound 三档 foreign+job 全 PASS，A∧owner
      全 404；而 tier④ 换绑历史章的 job 目标是本人小说，照常放行不受影响）。
    - B 绑定链证据：p.novel_id is not None **且** chapter.novel_id == p.novel_id。
      NULL 侧以显式 `is not None` 参与；写成 `p.novel_id and ...` 的真值短路就是
      本次要修的原始洞（项目解绑小说后整条件短路成 False → 任何人换个 chapter_id
      读别人整章正文），此形状永不许复发。
    - 两条都不成立 → 404。**绝不 403**（不暴露资源是否存在）。
    - chapter_id 为假值（0 / None，orphan job 的形状）→ 直接 404，不许拿去 db.get。

    换绑回归位（为什么必须有 A 证据）：用户解绑后又重绑一本新书时，SET NULL 只清
    外键、不删历史章节——p.novel_id=N2 而历史章仍挂 N1，一律比对 novel_id 会把
    用户自己的旧章永久判死 404；AiChapterJob 证明该章属本项目，必须放行。
    """
    # 🔴 m1 等价性的支撑闸（复审 m7b 实证）：本行必须是**假值判断** `if not chapter_id`。
    # 下面 B 腿的 `is not None` 与旧写法 `p.novel_id and ...` 之所以语义等价，全靠这里
    # 先把 0/None 拦死——若改成 `chapter_id is None`，id=0 会穿透并拿去 db.get；真库
    # ORM 投影退化时 `db.get(Chapter, 0)` 可能盲返回不等主键的行 → id=0 放行（test_u0a
    # ::test_falsy_chapter_id_never_reaches_stub_leg_or_db_get 以盲桩钉死此形状）。
    if not chapter_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "章节不存在，或不属于本项目")
    # 桩兼容快路（AGENTS.md「假 DB 同形化」教训的正向规避）：本仓库大量替身会话用
    # SimpleNamespace 行 + 队列式 execute。对这类桩真跑 db.get(Chapter, pk) 会因
    # 模型类不是映射而 ArgumentError；execute 盲吐队列又会错位——所以 **p 是
    # SimpleNamespace 形状**（无 __table__，ORM 实例必有）时改走 duck-typed 取章
    # （桩的 rows[Chapter]），A 腿改按属性比对 job 行（rows[AiChapterJob]）。
    # ⚠️ 勘误（u0a-tail 复审取证）：此处旧注释称"owner 合取在桩世界天然成立、跨用户
    # 泄露只可能出现在真库矩阵"——归因**错**。桩世界同样能构造跨项目 job 行（间谍在
    # 他人 project_id 下塞 job），防线不是"场景不可达"而是 `_has_chapter_job_stub._hit`
    # 逐字比对 project_id ∧ chapter_id 两条件本身；由 test_u0a_attribution
    # ::TestStubLegAttacks 直调入口钉死（复审 m5/m6/m8 三靶曾在此全绿，即因旧注释
    # 所述的"天然成立"根本不存在）。
    stub_p = not hasattr(p, "__table__")
    try:
        chapter = await db.get(Chapter, chapter_id)
    except Exception:  # noqa: BLE001  连 get 都备不出的桩 → 保守拒绝
        chapter = None
    if chapter is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "章节不存在，或不属于本项目")
    # B 绑定链证据先行（显式 is not None，短路形状永不许复发）。判序不许倒过来：
    # A 腿要跑一次查询，本仓库大量端点测试用「只备 get() 的最小 FakeDB」——
    # 合法路径若先查 A 会 AttributeError 炸成 500（误伤既有用例）。倒序的语义代价
    # 仅一处理论边界：job 行被硬删后章挂旧小说才走 B 放行；而 AiChapterJob.project_id
    # 是 CASCADE FK（项目没 job 就没任务），该形状实际不可达。
    if p.novel_id is not None and getattr(chapter, "novel_id", None) == p.novel_id:
        return chapter
    # ---- A 项目直接证据（精确到那一条 job 那一章 + owner 合取；换绑历史章的唯一出路）----
    if stub_p:
        # 桩世界没有跨用户概念：章行缺 novel_id（老桩形状）视为同源放行；有则必须
        # 与 p 同库（novel_id 相同）——不同卷的桩章仍要 job 证据。job 两条件逐字保留。
        same_book = getattr(chapter, "novel_id", None) in (None, p.novel_id)
        if same_book and await _has_chapter_job_stub(p, chapter.id, db):
            return chapter
    elif await _chapter_owned_by_user(chapter, p, db) and await _has_chapter_job(p, chapter.id, db):
        return chapter
    raise HTTPException(status.HTTP_404_NOT_FOUND, "章节不存在，或不属于本项目")


async def _has_chapter_job_stub(p, chapter_id: int, db) -> bool:
    """SimpleNamespace 桩世界的 A 腿：只读桩的内存行，绝不插新查询。

    硬约束（AGENTS.md「假 DB 同形化」教训）：队列式桩（BatchDB/SeqDB）的 execute
    按调用顺序消费预置结果——生产在这里多插一次查询就会让后续所有查询错位；盲吐型
    桩（pace FakeDb）会把章行当 job 行喂回来。所以本函数**从不 await db.execute**。
    两个条件（project_id ∧ chapter_id）逐字保留——or 放宽变异由真库腿上的 tier⑥ 钉住。

    判定序（全部保守失败 → False → 404）：
    1. rows 字典桩（FakeDB/SeqDB/_BatchDB/wire_observe.SeqDB）：直接读
       rows[AiChapterJob] 那一格，两条件逐字比对。
    2. 其余未知桩形状：**fail closed**。曾实现过"鸭子嗅探腿"（扫 vars(db)/vars(type(db))
       找 SimpleNamespace 再经 db.get 比对），实测无效后删除——pace 类桩的 job 行闭包在
       get() 方法里，`vars(PaceDb())` 为空、类上只有方法（同形化勘查实测），任何枚举都
       够不着它；且该类桩的合法路径已由 B 腿覆盖（夹具补 novel_id 后 p.novel_id ==
       chapter.novel_id，见 tests/test_wire_nodes.py pace 夹具处的声明）。
       保留空转的嗅探只会引入类变量误捕风险，不如诚实拒绝。
    """
    from .models import AiChapterJob as _ACJ

    def _hit(j) -> bool:
        return getattr(j, "project_id", None) == p.id and getattr(j, "chapter_id", None) == chapter_id

    # 腿 1：表级桩的 rows 字典
    rows = getattr(db, "rows", None)
    if isinstance(rows, dict):
        cand = rows.get(_ACJ)
        cand = [cand] if cand is not None and not isinstance(cand, (list, tuple)) else (cand or [])
        return any(_hit(j) for j in cand)

    # 腿 2：未知形状 → 保守拒绝（理由见 docstring 判定序第 2 条）
    return False


async def _chapter_owned_by_user(chapter: Chapter, p: AiProject, db: AsyncSession) -> bool:
    """A 证据的 owner 合取腿：该章所挂小说是否属于项目主人（Novel.user_id == p.user_id）。

    独立函数与 _has_chapter_job 同形（try/except → 保守 False），理由一致：
    最小 FakeDB 无 execute 时不炸 500 而是关门 404；真库仅在 B 失败后才多跑这一查。
    没有这条腿，攻击者在自己项目里插一条指向他人章节的 AiChapterJob 即可凭字面 A
    放行别人的整本书——job 表的外键只保证"章节存在"，不保证"章节归你"。
    """
    try:
        hit = (
            (
                await db.execute(
                    select(Novel.id).where(
                        Novel.id == chapter.novel_id, Novel.user_id == p.user_id
                    ).limit(1)
                )
            )
            .scalars()
            .first()
        )
        return hit is not None
    except Exception:  # noqa: BLE001  保守拒绝：查不动就当没有证据
        return False


async def _has_chapter_job(p: AiProject, chapter_id: int, db: AsyncSession) -> bool:
    """是否存在 AiChapterJob(project_id == p.id AND chapter_id == 该章)——A 证据本体。

    写成独立函数有两个硬理由，不许"顺手内联"回去：
    1. **桩兼容**：本仓库大量端点测试用「只备 get() 的最小 FakeDB」（无 execute）。
       真库里 B 腿命中时根本不会走到这里；只有 B 不成立才反查 A 一次。查询失败
       （含桩缺方法）一律 False → 上层 404：归属拿不准时永远关门，绝不放行。
    2. **精确性**：where 必须同时钉 project_id 与 chapter_id 两个条件。绝不允许
       写成"本项目有任意一条 job 就放行一切章"——那是 or 放宽变异，tier⑥ 专钉它。
    """
    try:
        hit = (
            (
                await db.execute(
                    select(AiChapterJob.id).where(
                        AiChapterJob.project_id == p.id, AiChapterJob.chapter_id == chapter_id
                    ).limit(1)
                )
            )
            .scalars()
            .first()
        )
        return hit is not None
    except Exception:  # noqa: BLE001  保守拒绝：查不动就当没有证据
        return False


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
) -> User:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
    payload = decode_token(credentials.credentials)
    if payload is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "登录已过期，请重新登录")
    user = await db.get(User, int(payload["sub"]))
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return user


async def get_owned_novel(
    novel_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Novel:
    novel = await db.get(Novel, novel_id)
    if novel is None or (novel.user_id != user.id and user.role != "admin"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "作品不存在")
    return novel


async def get_owned_chapter(
    novel_id: int,
    chapter_id: int,
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
) -> Chapter:
    """校验章节属于当前作品（novel_id 路径参数与 chapter.novel_id 一致）；越权/不存在均 404。"""
    chapter = await db.get(Chapter, chapter_id)
    if chapter is None or chapter.novel_id != novel.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "章节不存在")
    return chapter


async def get_default_ai_config(user: User, db: AsyncSession):
    from .models import AIConfig

    result = await db.execute(
        select(AIConfig).where(AIConfig.user_id == user.id).order_by(AIConfig.is_default.desc(), AIConfig.id)
    )
    config = result.scalars().first()
    if config is None or not config.api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先在「账号设置」中配置 AI 接口")
    return config


async def get_ai_config(user: User, db: AsyncSession, config_id: int | None = None):
    """按请求指定的 config_id 取 AI 配置；未指定时回退到默认配置。"""
    if config_id is None:
        return await get_default_ai_config(user, db)
    from .models import AIConfig

    config = await db.get(AIConfig, config_id)
    if config is None or config.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "AI 配置不存在")
    if not config.api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"配置「{config.name}」还没有填写 API Key")
    return config


def count_words(text: str) -> int:
    """字数统计：去掉空白后的字符数（网文通行口径）。"""
    import re

    from .utils import strip_html

    plain = strip_html(text)
    return len(re.sub(r"\s", "", plain))


def count_words_split(text: str) -> dict[str, int]:
    """双口径字数统计：中文 / English / 总计（去空白所有字符数）。

    - 中文：CJK 统一汉字 + 假名 + 谚文音节（不含标点，每个"字"算 1）。
      Unicode 范围：U+4E00-9FFF（基本汉字）、U+3040-309F（平假名）、
      U+30A0-30FF（片假名）、U+AC00-D7AF（谚文）。中文全角标点不计入。
    - English：连续 ASCII 字母组成的单词数（按 \b 切分）。
    - 总计：去掉所有空白后剩余字符数（含中文/英文/标点/数字等），
      等价于 count_words()，保留三档一致。

    适合中英混写作者在状态栏同时看到「中文 N / English M / 总计 K」三档。
    """
    import re

    from .utils import strip_html

    plain = strip_html(text)
    cjk = len(re.findall(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]", plain))
    en = len(re.findall(r"\b[A-Za-z]+\b", plain))
    total = len(re.sub(r"\s", "", plain))
    return {"cjk": cjk, "en": en, "total": total}
