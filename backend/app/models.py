from datetime import datetime, timedelta, timezone

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role: Mapped[str] = mapped_column(String(16), default="author")  # admin / author
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Novel(Base):
    __tablename__ = "novels"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    author: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    genre: Mapped[str] = mapped_column(String(50), default="")
    status: Mapped[str] = mapped_column(String(20), default="连载中")  # 连载中 / 已完结 / 暂停
    cover_color: Mapped[str] = mapped_column(String(20), default="#004EFF")
    daily_goal: Mapped[int] = mapped_column(default=0)  # 每日码字目标（0 = 不设定）
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    chapters: Mapped[list["Chapter"]] = relationship(
        back_populates="novel", cascade="all, delete-orphan", order_by="Chapter.sort_order"
    )


class Volume(Base):
    """分卷。卷内章节按 sort_order 排序；章节全局序号由显示顺序计算，不落库。"""

    __tablename__ = "volumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Chapter(Base):
    __tablename__ = "chapters"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    volume_id: Mapped[int | None] = mapped_column(
        ForeignKey("volumes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # title 只存自定义名（如"夜探王府"），"第X章"序号前缀由系统按排序生成
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")  # HTML
    sort_order: Mapped[int] = mapped_column(default=0)
    word_count: Mapped[int] = mapped_column(default=0)
    # 章节状态：draft（草稿） / writing（写作中） / done（已完成）；默认 draft
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # 章节标签：自由输入，存 JSON 数组字符串（SQLite 无原生数组）；默认 []
    tags: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)

    novel: Mapped[Novel] = relationship(back_populates="chapters")


class Character(Base):
    __tablename__ = "characters"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    role: Mapped[str] = mapped_column(String(50), default="配角")  # 主角 / 配角 / 反派 ...
    tags: Mapped[str] = mapped_column(String(300), default="")  # 逗号分隔
    description: Mapped[str] = mapped_column(Text, default="")
    relations: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WorldviewEntry(Base):
    __tablename__ = "worldview_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    category: Mapped[str] = mapped_column(String(50), default="其他")  # 势力 / 地理 / 历史 / 规则 / 其他
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Foreshadowing(Base):
    __tablename__ = "foreshadowings"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="未回收")  # 未回收 / 进行中 / 已回收
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OutlineNode(Base):
    __tablename__ = "outline_nodes"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("outline_nodes.id", ondelete="CASCADE"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    sort_order: Mapped[int] = mapped_column(default=0)


class AIConfig(Base):
    __tablename__ = "ai_configs"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_ai_config_user_name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    base_url: Mapped[str] = mapped_column(String(300), default="https://api.deepseek.com")
    api_key: Mapped[str] = mapped_column(String(300), default="")
    model: Mapped[str] = mapped_column(String(100), default="deepseek-chat")
    is_default: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class WritingStat(Base):
    """每日码字统计：保存章节正文时按字数正增量累计（北京时间）。"""

    __tablename__ = "writing_stats"
    __table_args__ = (UniqueConstraint("novel_id", "date", name="uq_writing_stat_novel_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    words: Mapped[int] = mapped_column(default=0)


class WritingHourlyStat(Base):
    """按时段（每小时 0-23）统计字数（P4-1 写作时段分析）。

    与 WritingStat 同步写：保存章节时 record_writing 同时累计日总和与时段。
    主键 (novel_id, date, hour) — 同一小时内多次写作累加在一行。
    """

    __tablename__ = "writing_hourly_stats"
    __table_args__ = (UniqueConstraint("novel_id", "date", "hour", name="uq_writing_hourly_stat_pk"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    date: Mapped[str] = mapped_column(String(10))  # YYYY-MM-DD
    hour: Mapped[int] = mapped_column(default=0)  # 0-23
    words: Mapped[int] = mapped_column(default=0)


class PomoLog(Base):
    """番茄钟完成事件：用户点「完成番茄」时上报一行；多端/移动共享。

    - phase="write"：完成一个 25min 写作番茄（计入今日番茄数）
    - phase="break"：完成 5min 休息（v1 仅记录，不计入番茄数）
    - completed_at：UTC 时刻（前端按本地时区统计"今日"）
    """

    __tablename__ = "pomo_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    novel_id: Mapped[int | None] = mapped_column(ForeignKey("novels.id", ondelete="SET NULL"), index=True, default=None)
    phase: Mapped[str] = mapped_column(String(10), default="write")  # write | break
    duration_min: Mapped[int] = mapped_column(default=25)
    completed_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class RecycleBin(Base):
    """废纸篓（P4-2）：删除的实体（章节/人物/设定/伏笔）入档 30 天后自动清理。

    - kind: chapter | character | setting | foreshadow
    - payload: 原始字段 JSON 字符串（恢复时反序列化重建）
    - original_id: 实体原始 ID（恢复后不再是新 ID，是原 ID？v1 直接新建，丢掉原 ID）
    - expires_at: 默认 30 天后；后台启动 / 调用列表时 lazy 清理
    """

    __tablename__ = "recycle_bin"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    novel_id: Mapped[int | None] = mapped_column(ForeignKey("novels.id", ondelete="SET NULL"), index=True, default=None)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")  # 显示用：章节标题 / 人物名
    payload: Mapped[str] = mapped_column(Text)  # JSON 字符串
    deleted_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc), index=True)
    expires_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc) + timedelta(days=30), index=True)


class LibraryFolder(Base):
    """资料库目录。novel_id 为 NULL 表示公共库，否则为某本小说的专属库。"""

    __tablename__ = "library_folders"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int | None] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), nullable=True, index=True
    )
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_folders.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class LibraryItem(Base):
    """资料库条目（文本资料）。novel_id 为 NULL 表示公共库。"""

    __tablename__ = "library_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int | None] = mapped_column(
        ForeignKey("novels.id", ondelete="CASCADE"), nullable=True, index=True
    )
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("library_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(String(300), default="")  # 逗号分隔
    summary: Mapped[str] = mapped_column(String(500), default="")
    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual / agent / import / xuanji
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class IntegrationConfig(Base):
    """第三方集成配置（每用户一行）：AList 备份 / 璇玑知识库对接。"""

    __tablename__ = "integration_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    alist_url: Mapped[str] = mapped_column(String(300), default="")
    alist_username: Mapped[str] = mapped_column(String(100), default="")
    alist_password: Mapped[str] = mapped_column(String(300), default="")
    alist_root: Mapped[str] = mapped_column(String(200), default="/beidou")
    xuanji_url: Mapped[str] = mapped_column(String(300), default="")
    xuanji_api_key: Mapped[str] = mapped_column(String(300), default="")
    auto_backup_enabled: Mapped[bool] = mapped_column(default=False)  # 每日自动备份到 AList
    last_backup_at: Mapped[str] = mapped_column(String(20), default="")  # ISO 日期串，判重
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class ChapterSnapshot(Base):
    """章节快照/存稿点：单章节粒度的内容存档。

    - content 存原始 HTML（编辑器内容）
    - content_text 派生的纯文本，用于 diff 渲染（避免 HTML 标签噪声）
    - content_hash: sha256(chapter.content)，去重用
    - trigger: auto / manual / pre_rollback
    """

    __tablename__ = "chapter_snapshots"
    __table_args__ = (
        Index("ix_snap_chapter_created", "chapter_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    content: Mapped[str] = mapped_column(Text)  # HTML
    content_text: Mapped[str] = mapped_column(Text)  # 纯文本，diff 用
    word_count: Mapped[int] = mapped_column(default=0)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)  # sha256 hex
    label: Mapped[str] = mapped_column(String(100), default="")  # manual 存稿点名
    trigger: Mapped[str] = mapped_column(String(16))  # auto / manual / pre_rollback
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)


class AiProject(Base):
    """AI 工厂项目：从立项到正文的自动化写作流水线（与人工写作并行的第二体系）。

    状态机：draft → setup → outline → writing → reviewing → done（failed 可回退）
    生成物落到关联的 Novel（标题带 [AI] 前缀），章节进现有 chapters 表。

    长程一致性 = 状态文件四分（AI_NovelGenerator 实证）：
    global_summary（滚动摘要）/ character_state（角色状态表 JSON）/
    plot_arcs（伏笔台账 JSON）/ 另加 FTS 向量召回——每章定稿后 AI 增量更新前三件。
    """

    __tablename__ = "ai_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    novel_id: Mapped[int | None] = mapped_column(
        ForeignKey("novels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="draft")
    seed_prompt: Mapped[str] = mapped_column(Text)  # 用户一句话创意
    book_spec_json: Mapped[str] = mapped_column(Text, default="")  # 立项八字段 JSON
    genre: Mapped[str] = mapped_column(String(50), default="")
    style_notes: Mapped[str] = mapped_column(Text, default="")
    # 字数目标（全部可选，软约束：prompt 注入 + 进度展示，绝不硬截断）
    target_total_words: Mapped[int | None] = mapped_column(nullable=True)
    target_volume_words: Mapped[int | None] = mapped_column(nullable=True)
    target_chapter_words: Mapped[int | None] = mapped_column(nullable=True)
    target_volumes: Mapped[int | None] = mapped_column(nullable=True)
    target_chapters: Mapped[int | None] = mapped_column(nullable=True)
    outline_json: Mapped[str] = mapped_column(Text, default="")  # AI 大纲快照
    # 状态文件四分
    global_summary: Mapped[str] = mapped_column(Text, default="")
    character_state: Mapped[str] = mapped_column(Text, default="")  # JSON
    plot_arcs: Mapped[str] = mapped_column(Text, default="")  # JSON
    # 多模型任务路由（None = 用默认 AIConfig；格式为 AIConfig.id 的字符串）
    setup_llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    outline_llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chapter_llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    summary_llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    review_llm: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 上下文控制
    context_recent_chapters: Mapped[int] = mapped_column(default=2)
    context_extra_chapters: Mapped[str] = mapped_column(Text, default="[]")  # chapter id JSON
    auto_mode: Mapped[bool] = mapped_column(default=False)  # 全自动（默认关，每章人工确认）
    # 控制面（M3，借鉴 webnovel-master/inkos 的输入治理：意图先编译再写作）
    author_intent: Mapped[str] = mapped_column(Text, default="")  # 长期作者意图：主题/风格定位/禁忌
    current_focus: Mapped[str] = mapped_column(Text, default="")  # 当前阶段焦点：近几章重点/避免倾向
    # 真相文件扩展（M3，7 真相文件思想的北斗裁剪版）
    particle_ledger: Mapped[str] = mapped_column(Text, default="")  # 资源账本：金钱/物品/等级数值
    # M5
    synopsis_json: Mapped[str] = mapped_column(Text, default="")  # 多版本简介 {short,standard,promotion,douyin}
    market_json: Mapped[str] = mapped_column(Text, default="")  # 市场调研报告（选题用）
    cover_prompt: Mapped[str] = mapped_column(Text, default="")  # 封面绘图 prompt（中文描述+英文 prompt）
    kb_query: Mapped[str] = mapped_column(String(200), default="")  # 璇玑知识源检索词（空=不启用）
    subplot_board: Mapped[str] = mapped_column(Text, default="")  # 支线进度板：A/B/C 线状态
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class AiChapterJob(Base):
    """AI 工厂单章任务：生成/审校/定稿的状态与产物快照。"""

    __tablename__ = "ai_chapter_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("ai_projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending|writing|reviewing|needs_fix|done|failed
    outline: Mapped[str] = mapped_column(Text, default="")  # 本章大纲快照
    summary: Mapped[str] = mapped_column(Text, default="")  # 本章 200 字摘要（汇入滚动摘要）
    review_issues: Mapped[str] = mapped_column(Text, default="")  # 审校 issues JSON
    review_score: Mapped[int | None] = mapped_column(nullable=True)  # 28 维审校总分（M3）
    retention_json: Mapped[str] = mapped_column(Text, default="")  # 追读力提取 JSON：hooks/cool_points（M3）
    actual_words: Mapped[int] = mapped_column(default=0)
    attempt: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)


class CharacterRelation(Base):
    """人物关系（C 级：人物关系图）。

    - 一条记录表示 from_character → to_character 的一条有向关系
    - relation：关系名（如"师徒"/"仇敌"/"兄妹"）；description：补充说明
    - source：manual（手动添加） / ai（AI 从正文+角色卡抽取）
    """

    __tablename__ = "character_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    novel_id: Mapped[int] = mapped_column(ForeignKey("novels.id", ondelete="CASCADE"), index=True)
    from_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    to_character_id: Mapped[int] = mapped_column(
        ForeignKey("characters.id", ondelete="CASCADE"), index=True
    )
    relation: Mapped[str] = mapped_column(String(50))
    description: Mapped[str] = mapped_column(String(300), default="")
    source: Mapped[str] = mapped_column(String(10), default="manual")  # manual / ai
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
