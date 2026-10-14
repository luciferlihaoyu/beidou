"""璇玑知识库 → 北斗资料库 入方向同步引擎（批D · r2）。

语义（《北斗璇玑资料库双向同步-碧霄-v2》口径）：

- 关联锚 = 璇玑文档 id（``LibraryItem.xj_doc_id``）；变更锚 = 璇玑 ``updatedAt``
  （``LibraryItem.xj_doc_updated_at`` 存上次同步的基线）。
- 本地映射**只认 xj_doc_id**，不带 ``source == "xuanji"`` 谓词：用户在资料库保存条目会经
  ``PUT /api/library/items/{id}`` 把 source 重写成 ``ItemIn.source`` 的默认值 ``manual``，
  带该谓词会让条目脱锚，下一轮被当成新文档再插一条重复镜像（C1）。
- 每轮：读璇玑知识库树 → 只取白名单文件夹内的文档 → 与本地映射做四路分流：
    * 本地无此锚 → 新增镜像条目（北斗**公共库**，按璇玑目录名建/复用镜像目录）
    * 璇玑变 且 本地干净 → 覆盖 content 并刷新基线（**标题保留本地值**：本地标题=别名，
      与出方向「标题不回推」对称——璇玑 upsert 以标题为锚，推标题会造副本）
    * 璇玑变 且 本地已改(dirty) → 只置 ``xj_conflict``，两边内容与基线都不动（人工裁）
    * 璇玑未变 且 本地已改(dirty) → 保护不动（把本地改动回写璇玑属**出方向**，批E 负责）
    * 璇玑未变 且 本地干净 → 跳过
- 硬排除 ``XJ_EXCLUDED_FOLDER``（「北斗小说资料」）：那是正向设定集同步的落点，
  纳入白名单会形成自我循环，故即便被误勾也一律跳过。
- 单篇 ``getDocument`` 失败：跳过该篇、计入 ``failed``，其余继续；
  ``getTree`` 失败自然上抛（循环级容错属批E 的调度器，本批不吞）。
- 本批**只做入方向**：不推送、不改 dirty、不擅自清 ``xj_conflict``
  （冲突清理由批E 的 resolve 接口负责）。

时间戳比较前提：两侧都是 ISO 8601 形态（如 ``2026-10-13T10:00:00.000Z``），
字典序与时间序一致，故归一化后直接比较；缺时间戳（None/空串）视为「未变」——
宁可少覆盖，不误覆盖。另有一条防御：纯数字（int/float/数字串）按 **epoch 毫秒**
折算成 UTC ISO，否则「璇玑发 epoch ms、本地存 ISO」混形时真更新会被字典序误判成
「未变」而静默跳过（见 ``_norm_ts``）。
"""

import logging
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import IntegrationConfig, LibraryFolder, LibraryItem

logger = logging.getLogger("beidou.xj_sync")

#: 硬排除目录：正向设定集同步的落点，永不参与反向同步（防自我循环）
XJ_EXCLUDED_FOLDER = "北斗小说资料"

#: 镜像条目标签（区别于手动/导入条目，也便于资料库筛选）
XJ_SYNC_TAG = "璇玑同步"

#: 纯数字时间戳（int/float/数字串）——按 epoch 毫秒折算，见 ``_norm_ts``
_NUMERIC_TS_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


# ---------- 纯函数（无 IO，便于单测）----------


def _epoch_ms_to_iso(value: float) -> str:
    """epoch 毫秒 → UTC ISO（Z 形态，与璇玑 tRPC 的 ``...Z`` 串同形）。

    超出可表示范围（如 1e30 / inf）退回原样字符串，绝不抛。
    """
    try:
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    except (OverflowError, OSError, ValueError):
        return str(value)


def _norm_ts(value) -> str:
    """时间戳归一化为可比较字符串。

    前提：两侧都是 ISO 8601 形态，字典序 == 时间序，故归一化后直接比较。

    防御（m3）：纯数字（int/float/数字串）按 **epoch 毫秒**折算成 UTC ISO ——
    否则「璇玑发 epoch ms、本地存 ISO」混形时，``str(1791194400000) < "2026-..."``
    会把真更新误判成「未变」而静默跳过。已给出的 ISO 串一律原样保留（不重写），
    下一轮比较用的基线是归一化后的形态，故两种形态各自收敛、不会反复误判。
    ``bool`` 是 ``int`` 子类，但不当时刻处理。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).strip()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (int, float)):
        return _epoch_ms_to_iso(float(value))
    text = str(value).strip()
    return _epoch_ms_to_iso(float(text)) if _NUMERIC_TS_RE.match(text) else text


def _doc_id(doc) -> str:
    """取文档关联锚（字符串化）；缺 id → 空串（无锚，四路分流均不体现）。"""
    if not isinstance(doc, dict):
        return ""
    raw = doc.get("id")
    return "" if raw is None else str(raw).strip()


def _parse_folder_names(raw) -> set[str]:
    """解析逗号分隔的文件夹白名单：支持全角逗号、去空白、丢空项、剔除硬排除目录。"""
    if not raw:
        return set()
    names = {part.strip() for part in str(raw).replace("，", ",").split(",") if part.strip()}
    names.discard(XJ_EXCLUDED_FOLDER)
    return names


def _diff_in(tree_docs: list[dict], local_map: dict[str, dict]) -> tuple[list, list, list, list]:
    """入方向四路分流（纯函数，不碰数据库）。

    - ``tree_docs``：白名单文件夹内的璇玑文档（至少含 id/title/updatedAt/folderId）。
    - ``local_map``：键为 ``xj_doc_id``；值至少含 ``item_updated_at``（基线）
      与 ``dirty``（本地是否已改未回写），可再带 content/item_id 等调试字段。

    返回 ``(new_docs, to_overwrite, to_protect, to_conflict)``，四类互斥、各自保序：

    - ``new_docs``：本地无此锚 → 新增镜像
    - ``to_overwrite``：璇玑变 + 本地干净 → 覆盖并刷基线
    - ``to_protect``：璇玑未变 + 本地已改 → 保护不动（回写是出方向的事）
    - ``to_conflict``：璇玑变 + 本地已改 → 标冲突，两边都不动

    本地有锚、璇玑未变、本地也干净 → 不进任何一类（即 skipped，由调用方按残余计数）。
    非 dict 条目与缺 id 的条目没有锚，四类均不体现；本地映射条目不是 dict 时
    按「无此锚」处理（视作新增）。
    """
    new_docs: list[dict] = []
    to_overwrite: list[dict] = []
    to_protect: list[dict] = []
    to_conflict: list[dict] = []
    if not isinstance(tree_docs, (list, tuple)):
        return new_docs, to_overwrite, to_protect, to_conflict
    mapping = local_map if isinstance(local_map, dict) else {}
    for doc in tree_docs:
        doc_id = _doc_id(doc)
        if not doc_id:  # 非 dict 或无 id
            continue
        local = mapping.get(doc_id)
        if not isinstance(local, dict):
            new_docs.append(doc)
            continue
        dirty = bool(local.get("dirty"))
        changed = _norm_ts(doc.get("updatedAt")) > _norm_ts(local.get("item_updated_at"))
        if changed:
            (to_conflict if dirty else to_overwrite).append(doc)
        elif dirty:
            to_protect.append(doc)
    return new_docs, to_overwrite, to_protect, to_conflict


def _select_tree_docs(tree: dict, whitelist: set[str]) -> tuple[list[dict], list[dict]]:
    """按名匹配白名单文件夹（硬排除 ``XJ_EXCLUDED_FOLDER``），把树内文档归组。

    返回 ``(tree_docs, folders)``：

    - tree_docs 每项带 ``folderId`` / ``folderName``（供建/复用镜像目录与四路分流）；
    - folders 是白名单命中目录的统计，**按 folder_id 聚合**（m5）：同名不同父的两个
      目录各占一行、各自计数，不会因为同名把文档数合并成虚高的一行；同一 id 在树里
      重复出现只算一次。每项 ``{"id", "name", "docs"}``，按树内首次出现顺序，
      含零文档目录（说明「这个勾选的目录里没有文档」）。
    """
    order: list[str] = []
    meta: dict[str, dict] = {}  # 文件夹 key（str(id)）→ {"id": 原始 id, "name": 名}
    for folder in tree.get("folders") or []:
        if not isinstance(folder, dict):
            continue
        fid = folder.get("id")
        name = str(folder.get("name") or "").strip()
        # 硬排除优先于白名单：即便被误勾也绝不纳入
        if fid is None or not name or name == XJ_EXCLUDED_FOLDER or name not in whitelist:
            continue
        key = str(fid)
        if key not in meta:
            order.append(key)
        meta[key] = {"id": fid, "name": name}

    tree_docs: list[dict] = []
    for raw in tree.get("documents") or []:
        if not isinstance(raw, dict):
            continue
        fid = raw.get("folderId")
        if fid is None:
            continue
        folder_name = meta.get(str(fid), {}).get("name")
        if not folder_name:
            continue
        tree_docs.append(
            {
                "id": raw.get("id"),
                "title": raw.get("title") or "",
                "updatedAt": raw.get("updatedAt"),
                "folderId": fid,
                "folderName": folder_name,
            }
        )

    counts: dict[str, int] = {}
    for doc in tree_docs:
        key = str(doc["folderId"])
        counts[key] = counts.get(key, 0) + 1
    folders = [{**meta[key], "docs": counts.get(key, 0)} for key in order]
    return tree_docs, folders


# ---------- IO 辅助 ----------


async def _fetch_document(config: IntegrationConfig, doc: dict) -> dict | None:
    """取单篇全文；失败或空响应返回 None（调用方计入 failed 并继续，绝不炸整批）。"""
    # 函数内 import：与 ai_factory._recall_own_spec 同口径，便于测试直接 monkeypatch 模块属性
    from .routers.integrations import _xuanji_call

    try:
        full = await _xuanji_call(config, "kb.getDocument", {"id": doc.get("id")})
    except Exception:  # noqa: BLE001  单篇失败不阻塞整批
        return None
    return full if isinstance(full, dict) else None


async def _ensure_mirror_folder(db: AsyncSession, name: str) -> LibraryFolder | None:
    """在北斗公共库里按名找镜像目录，不存在则创建（novel_id=None 即公共库）。"""
    name = (name or "").strip()[:100]
    if not name:
        return None
    existing = (
        await db.execute(
            select(LibraryFolder)
            .where(
                LibraryFolder.novel_id.is_(None),
                LibraryFolder.parent_id.is_(None),
                LibraryFolder.name == name,
            )
            .order_by(LibraryFolder.id)
        )
    ).scalars().first()
    if existing is not None:
        return existing
    folder = LibraryFolder(novel_id=None, parent_id=None, name=name)
    db.add(folder)
    await db.flush()  # 取自增 id，供条目挂载
    return folder


# ---------- 主入口 ----------


async def sync_from_xuanji(config: IntegrationConfig, db: AsyncSession) -> dict:
    """跑一轮璇玑 → 北斗入方向同步。

    返回 ``{"added", "updated", "protected", "conflicts", "skipped", "failed", "folders"}``；
    成功一轮会把 ``config.xj_sync_last_at`` 写成 UTC ISO 时刻。
    """
    # 函数内 import：便于测试 monkeypatch，也避免与路由层形成导入环
    from .routers.integrations import _xuanji_call

    tree = await _xuanji_call(config, "kb.getTree")  # 失败自然上抛（批E 的循环兜）
    if not isinstance(tree, dict):
        raise HTTPException(400, "璇玑返回格式异常")

    whitelist = _parse_folder_names(getattr(config, "xj_sync_folders", ""))
    tree_docs, folders_out = _select_tree_docs(tree, whitelist)

    # 本地映射只按 xj_doc_id 圈定（C1）：**不得**加 source == "xuanji" 谓词——
    # 用户在资料库保存条目会经 PUT /api/library/items/{id} 把 source 重写成 ItemIn 的
    # 默认值 manual，带上该谓词条目就脱锚，下一轮会被当成新文档再插一条重复镜像。
    rows = (
        await db.execute(
            select(LibraryItem).where(
                LibraryItem.xj_doc_id.is_not(None),
                LibraryItem.xj_doc_id != "",
            )
        )
    ).scalars().all()
    items_by_doc: dict[str, LibraryItem] = {str(it.xj_doc_id): it for it in rows}
    local_map = {
        doc_id: {
            "item_updated_at": it.xj_doc_updated_at,
            "dirty": bool(it.xj_local_dirty),
            "content": it.content,
            "item_id": it.id,
        }
        for doc_id, it in items_by_doc.items()
    }

    new_docs, to_overwrite, to_protect, to_conflict = _diff_in(tree_docs, local_map)

    added = 0
    updated = 0
    failed = 0
    touched: list[LibraryItem] = []

    # 新增：拉全文 → 复用/新建同名镜像目录 → 插条目（记锚与基线）
    for doc in new_docs:
        full = await _fetch_document(config, doc)
        if full is None:
            failed += 1
            continue
        folder = await _ensure_mirror_folder(db, str(doc.get("folderName") or ""))
        item = LibraryItem(
            novel_id=None,
            folder_id=folder.id if folder is not None else None,
            title=str(full.get("title") or doc.get("title") or "")[:200],
            content=full.get("content") or "",
            tags=XJ_SYNC_TAG,
            summary="",
            source="xuanji",
            xj_doc_id=_doc_id(doc),
            xj_doc_updated_at=_norm_ts(doc.get("updatedAt")),
        )
        db.add(item)
        touched.append(item)
        added += 1

    # 覆盖：本地干净而璇玑变了 → 原位更新 content 并刷新基线
    # ⚠ 标题不动（碧霄口径 2026-10-14）：本地标题=别名，与出方向「标题不回推」对称；
    #   璇玑 upsert 以标题为锚，入方向改写本地标题只会让作者起的别名被抹掉。
    for doc in to_overwrite:
        item = items_by_doc.get(_doc_id(doc))
        if item is None:  # 理论不可达：to_overwrite 由本地映射产生
            continue
        full = await _fetch_document(config, doc)
        if full is None:
            failed += 1
            continue
        item.content = full.get("content") or ""
        item.xj_doc_updated_at = _norm_ts(doc.get("updatedAt"))
        touched.append(item)
        updated += 1

    # 冲突：本地脏 + 璇玑变 → 只标旗，内容与基线都不动（人工裁归批E 的 resolve）
    for doc in to_conflict:
        item = items_by_doc.get(_doc_id(doc))
        if item is None:
            continue
        item.xj_conflict = True

    await db.flush()
    touched_ids = [it.id for it in touched]
    config.xj_sync_last_at = datetime.now(timezone.utc).isoformat()
    await db.commit()

    # B2 资料库 FTS：镜像条目也要可被 /api/library/search 搜到（best-effort，失败不影响同步结果）
    if touched_ids:
        try:
            from .search_fts import sync_library_item

            for item_id in touched_ids:
                await sync_library_item(db, item_id)
        except Exception:  # noqa: BLE001  FTS 不可用不阻塞同步
            logger.debug("资料库 FTS 同步失败（不影响本轮同步结果）", exc_info=True)

    bucketed = {_doc_id(d) for d in (*new_docs, *to_overwrite, *to_protect, *to_conflict)}
    skipped = sum(1 for doc in tree_docs if _doc_id(doc) and _doc_id(doc) not in bucketed)

    return {
        "added": added,
        "updated": updated,
        "protected": len(to_protect),
        "conflicts": len(to_conflict),
        "skipped": skipped,
        "failed": failed,
        "folders": folders_out,
    }
