"""FTS5 全文搜索：章节正文（剥离 HTML）建索引，搜索用 snippet() 高亮。

为什么不装 jieba / 不写中文分词：
- 容器是 2C/7.6G web 容器，禁止大规模依赖
- FTS5 自带 unicode61 tokenize 对**单字**索引是 OK 的（"小" 能命中）
- 词组（"小明"）用 phrase query `"小明"` 命中相邻 token
- 用户搜"小明"也能搜到——大多数查询是 2-4 字中文短语，phrase 命中率高

建表：contentless FTS5 表（无 rowid 自管），用外部表 Chapter.id 做 rowid。
每次 chapter 写/删时同步 FTS。
"""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from .utils import strip_html


# 虚拟表 DDL：FTS5 用 unicode61 tokenize（中文按 unicode codepoint 切分）
# rowid 用 chapter.id
_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chapter_fts USING fts5(
    novel_id UNINDEXED,
    title,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""

# B2 资料库 FTS：rowid 用 library_items.id；scope=novel_id（公共库记 -1）
_LIBRARY_FTS_DDL = """
CREATE VIRTUAL TABLE IF NOT EXISTS library_items_fts USING fts5(
    scope UNINDEXED,
    title,
    body,
    tokenize = 'unicode61 remove_diacritics 2'
)
"""


def _strip_for_fts(content: str) -> str:
    """把 Tiptap HTML 转换成纯文本供 FTS 索引（保留段落换行）。"""
    return strip_html(content or "").strip()


async def ensure_fts(conn: AsyncConnection) -> None:
    """创建 FTS5 虚拟表（幂等）。"""
    await conn.execute(text(_FTS_DDL))
    await conn.execute(text(_LIBRARY_FTS_DDL))


async def rebuild_fts_for_novel(db: AsyncSession, novel_id: int) -> int:
    """重建某本小说的 FTS 索引：删旧 + 全量导入章节。

    首次启动时调用一次（把存量 chapter 灌进 FTS）。
    返回导入章节数。
    """
    from .models import Chapter

    # 先清掉
    await db.execute(
        text("DELETE FROM chapter_fts WHERE novel_id = :nid"),
        {"nid": novel_id},
    )
    chapters = (
        await db.execute(
            text("SELECT id, title, content FROM chapters WHERE novel_id = :nid"),
            {"nid": novel_id},
        )
    ).fetchall()
    for row in chapters:
        cid, title, content = row
        body = _strip_for_fts(content or "")
        if not body and not (title or "").strip():
            continue
        await db.execute(
            text(
                "INSERT INTO chapter_fts(rowid, novel_id, title, body) "
                "VALUES (:rid, :nid, :title, :body)"
            ),
            {"rid": cid, "nid": novel_id, "title": title or "", "body": body},
        )
    await db.commit()
    return len(chapters)


async def sync_chapter(db: AsyncSession, chapter_id: int) -> None:
    """单个章节写后调用：upsert 到 FTS。"""
    from .models import Chapter

    chapter = await db.get(Chapter, chapter_id)
    if chapter is None:
        return
    body = _strip_for_fts(chapter.content or "")
    title = (chapter.title or "").strip()
    if not body and not title:
        # 空章节不入索引（但 FTS 留旧行方便查全量）
        await db.execute(
            text("DELETE FROM chapter_fts WHERE rowid = :rid"), {"rid": chapter_id}
        )
        await db.commit()
        return
    # 简单 upsert：先删后插（FTS5 无原生 UPSERT）
    await db.execute(
        text("DELETE FROM chapter_fts WHERE rowid = :rid"), {"rid": chapter_id}
    )
    await db.execute(
        text(
            "INSERT INTO chapter_fts(rowid, novel_id, title, body) "
            "VALUES (:rid, :nid, :title, :body)"
        ),
        {
            "rid": chapter_id,
            "nid": chapter.novel_id,
            "title": title,
            "body": body,
        },
    )
    await db.commit()


async def remove_chapter(db: AsyncSession, chapter_id: int) -> None:
    await db.execute(
        text("DELETE FROM chapter_fts WHERE rowid = :rid"), {"rid": chapter_id}
    )
    await db.commit()


async def search_fts(
    db: AsyncSession, novel_id: int, query: str, limit: int = 30
) -> list[dict]:
    """在 FTS 表里查 novel_id 的章节正文。

    用户输入的 query:
    - 若含 ASCII 字母/数字：原样（unicode61 会按词切）
    - 若纯中文/标点：加双引号变 phrase（要求紧邻）—— 这样"小明"才能命中
      而不是分别命中"小"和"明"

    返回 [{chapter_id, title, snippet, count}]，count 来自 chapter.content
    的子串计数（更准确反映"该章出现多少次"）。
    """
    q = (query or "").strip()
    if not q:
        return []
    has_ascii = any(c.isascii() and c.isalnum() for c in q)
    if not has_ascii:
        # 中文短语 → phrase query
        escaped = q.replace('"', '""')
        match_expr = f'"{escaped}"'
    else:
        match_expr = q

    rows = (
        await db.execute(
            text(
                "SELECT rowid AS chapter_id, title, "
                "  snippet(chapter_fts, 2, '<mark>', '</mark>', '…', 12) AS snippet "
                "FROM chapter_fts "
                "WHERE chapter_fts MATCH :q AND novel_id = :nid "
                "ORDER BY rank LIMIT :lim"
            ),
            {"q": match_expr, "nid": novel_id, "lim": limit},
        )
    ).fetchall()

    # 取出每章 content 算子串次数
    from .models import Chapter

    if not rows:
        return []
    ids = [int(r[0]) for r in rows]
    chapters = (
        await db.execute(select(Chapter).where(Chapter.id.in_(ids)))
    ).scalars().all()
    plain = {c.id: strip_html(c.content or "") for c in chapters}

    out: list[dict] = []
    for row in rows:
        cid = int(row[0])
        body = plain.get(cid, "")
        # 词组匹配：snippet 已展示 1 处；count 用子串数（更直观）
        # 对中文 phrase 搜索：query 出现在 body 中次数
        # ASCII 搜索：可能跨多 token，按 phrase 算次数
        count = body.count(q) if q else 0
        out.append(
            {
                "chapter_id": cid,
                "title": row[1] or "",
                "snippet": row[2] or "",
                "count": count,
            }
        )
    return out


# ---------- B2 资料库 FTS ----------

_PUBLIC_SCOPE = -1  # library_items.novel_id 为 NULL 时在 FTS 里记 -1


async def sync_library_item(db: AsyncSession, item_id: int) -> None:
    """单个资料条目写后调用：upsert 到 FTS。"""
    from .models import LibraryItem

    item = await db.get(LibraryItem, item_id)
    if item is None:
        return
    body = " ".join(
        part for part in [item.content or "", item.tags or "", item.summary or ""] if part
    ).strip()
    title = (item.title or "").strip()
    scope = item.novel_id if item.novel_id is not None else _PUBLIC_SCOPE
    await db.execute(
        text("DELETE FROM library_items_fts WHERE rowid = :rid"), {"rid": item_id}
    )
    if body or title:
        await db.execute(
            text(
                "INSERT INTO library_items_fts(rowid, scope, title, body) "
                "VALUES (:rid, :scope, :title, :body)"
            ),
            {"rid": item_id, "scope": scope, "title": title, "body": body},
        )
    await db.commit()


async def remove_library_item(db: AsyncSession, item_id: int) -> None:
    await db.execute(
        text("DELETE FROM library_items_fts WHERE rowid = :rid"), {"rid": item_id}
    )
    await db.commit()


async def rebuild_fts_for_library(db: AsyncSession) -> int:
    """全量重建资料库 FTS（首次启动/修复用）。返回导入条目数。"""
    from .models import LibraryItem

    await db.execute(text("DELETE FROM library_items_fts"))
    items = (await db.execute(text("SELECT id FROM library_items"))).fetchall()
    for (iid,) in items:
        await sync_library_item(db, int(iid))
    return len(items)


def _match_expr(q: str) -> str:
    """中文/标点 → phrase query；含 ASCII 字母数字 → 原样。"""
    has_ascii = any(c.isascii() and c.isalnum() for c in q)
    if has_ascii:
        return q
    return '"' + q.replace('"', '""') + '"'


async def search_library_fts(
    db: AsyncSession, query: str, novel_id: int | None = None, limit: int = 30
) -> list[dict]:
    """资料库全文搜索。

    novel_id 给了 → 搜「公共库 + 该书专属」；没给 → 搜全部。
    返回 [{id, title, snippet, novel_id, folder_id, tags}]。
    """
    q = (query or "").strip()
    if not q:
        return []
    match = _match_expr(q)
    scope_cond = ""
    params: dict = {"q": match, "lim": limit}
    if novel_id is not None:
        scope_cond = "AND (scope = :nid OR scope = :pub)"
        params["nid"] = novel_id
        params["pub"] = _PUBLIC_SCOPE
    rows = (
        await db.execute(
            text(
                "SELECT rowid AS id, title, "
                "  snippet(library_items_fts, 2, '<mark>', '</mark>', '…', 16) AS snippet "
                "FROM library_items_fts "
                f"WHERE library_items_fts MATCH :q {scope_cond} "
                "ORDER BY rank LIMIT :lim"
            ),
            params,
        )
    ).fetchall()
    if not rows:
        return []
    from .models import LibraryItem

    ids = [int(r[0]) for r in rows]
    items = (await db.execute(select(LibraryItem).where(LibraryItem.id.in_(ids)))).scalars().all()
    meta = {i.id: i for i in items}
    out: list[dict] = []
    for row in rows:
        iid = int(row[0])
        it = meta.get(iid)
        if it is None:
            continue  # FTS 与主表不一致（极端情况），跳过
        out.append(
            {
                "id": iid,
                "title": row[1] or "",
                "snippet": row[2] or "",
                "novel_id": it.novel_id,
                "folder_id": it.folder_id,
                "tags": it.tags or "",
                "source": it.source,
            }
        )
    return out
