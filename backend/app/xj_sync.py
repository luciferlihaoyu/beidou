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

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

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

#: 纯整数（文档 id 形态）——见 ``_doc_id_arg``
_DOC_ID_INT_RE = re.compile(r"^\d+$")

#: 进程内同步互斥锁（E2 硬约束）。
#:
#: 入方向一轮是「读璇玑树 → 读本地映射 → 插/覆盖 → 提交」的读改写序列；两路并发时会
#: **双双读到空映射**，各插一条同锚镜像、各建一个同名镜像目录。故定时循环、手动
#: ``POST /xuanji/sync``、resolve 强回写凡触发全量对账/批量回写，一律先拿这把锁，
#: 且在锁内完成「读 config → 改库 → commit」。单条目 ``push_after_save`` 不进锁：
#: 按标题 upsert 天然幂等，重复推无害，进锁反而让保存请求排队。
_SYNC_LOCK = asyncio.Lock()


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


# ======================================================================
# 出方向（批E · r3）：本地改动回写璇玑
# ======================================================================


def _doc_id_arg(doc_id):
    """``xj_doc_id`` 是字符串列，而璇玑接口要**数字** id：纯数字串转 int，其余原样透传。"""
    text = str(doc_id).strip()
    return int(text) if _DOC_ID_INT_RE.match(text) else text


class XuanjiReadError(RuntimeError):
    """璇玑**读通道本身**失败（网络/401/协议/解析）——与「文档确定没了」严格区分。

    C1：读不动 ≠ 文档没了。前者只能归 ``failed``（保留 dirty 等重试），若误判成
    ``missing`` 会给条目错打冲突旗：既逼作者白做一次人工裁决，又因「冲突条目不自动推」
    而让这条永久退出批量兜底队列。
    """


async def _resolve_xuanji_doc(config: IntegrationConfig, doc_id) -> dict | None:
    """按锚读璇玑当前文档（E1 的锚点解析）。**三态返回（C1）**：

    - 返回 ``dict`` → 文档在（且未软删）
    - 返回 ``None`` → 读通道说得清清楚楚「没有这篇」（``json:null`` / 已软删）
    - 抛 ``XuanjiReadError`` → 读通道自己坏了，**调用方必须按 failed 处理**

    实测（2026-10-14 探针）：``kb.getDocument {id}`` 返回体形如
    ``{"id":148,"folderId":22,"title":"...","content":"...","format":"markdown",
    "tags":[...],"metadata":{...},"createdAt":"...Z","updatedAt":"2026-08-23T13:18:33.000Z",
    "deletedAt":null,"deletedReason":null,"mergedIntoId":null}``；
    **不存在的 id 返回 ``{"json": null}``（HTTP 200，不抛异常）**，解包即 None。
    """
    from .routers.integrations import _xuanji_call

    try:
        doc = await _xuanji_call(config, "kb.getDocument", {"id": _doc_id_arg(doc_id)})
    except Exception as exc:  # noqa: BLE001  读通道炸了：绝不当成「文档没了」（C1）
        raise XuanjiReadError(f"璇玑文档读取失败（doc_id={doc_id}）") from exc
    if doc is None:
        return None  # 上游明确说没有这篇
    if not isinstance(doc, dict):
        # 形态非预期（既不是 null 也不是对象）→ 无法据此断言「不存在」，归读通道故障
        raise XuanjiReadError(f"璇玑文档返回形态异常（doc_id={doc_id}）")
    if doc.get("deletedAt"):
        return None  # 已软删 → 视同不存在
    return doc


async def _resolve_folder_id(config: IntegrationConfig, doc_id, doc: dict):
    """解析该文档在璇玑侧归属的 folderId（找不到返回 None）。

    实测 ``kb.getDocument`` 返回体**总带 ``folderId`` 键**（未归档为 ``null``），
    故正常路径零额外请求；仅当响应体缺该键（上游形态漂移）才回退 ``kb.getTree``
    找一遍——这正是「若无 folderId 则 getTree 找」的落地形态。
    未归档（``null``）就不传：实测 upsert 省略 folderId **不会**清掉已有目录
    （同标题二次 upsert 后 folderId 仍在），传与不传等价安全。
    """
    if "folderId" in doc:
        return doc.get("folderId")

    from .routers.integrations import _xuanji_call

    try:
        tree = await _xuanji_call(config, "kb.getTree")
    except Exception:  # noqa: BLE001  回退查目录失败不值得让整次回写失败
        return None
    if not isinstance(tree, dict):
        return None
    target = str(doc_id).strip()
    for raw in tree.get("documents") or []:
        if isinstance(raw, dict) and _doc_id(raw) == target:
            return raw.get("folderId")
    return None


async def push_item_to_xuanji(item: LibraryItem, config: IntegrationConfig, db: AsyncSession) -> str:
    """把本地条目正文回写璇玑（出方向 r3）。返回 ``"pushed" | "missing" | "failed"``。

    **E1 锚点铁律**：``document_upsert`` 以**归一化标题**为锚——实测同一标题二次 upsert
    得到**同一个 id**（action ``updated``），换个标题立刻**新建**一条（action ``created``，
    新 id）。故标题一律按 ``xj_doc_id`` 向璇玑解析**当前标题**，**绝不取 ``item.title``**：
    那是作者在北斗起的别名，推过去会在璇玑造出副本。全程不修改 ``item.title``。

    四分支：

    - **读通道失败**（401/网络/协议，``XuanjiReadError``）→ 返回 ``failed``：保留 dirty 与
      基线等下一轮重试，**绝不置 ``xj_conflict``**（C1：读不动 ≠ 文档没了；打错旗既逼作者
      白做一次人工裁决，又因「冲突条目不自动推」让该条永久退出批量兜底队列）
    - 确定解析不到锚（文档已被删/软删，或璇玑侧标题被清空）→ 置 ``xj_conflict``，返回 ``missing``
    - 写通道失败 → 返回 ``failed``，dirty 与基线都不动（留待下轮批量兜底重试）
    - 成功 → 清 ``xj_local_dirty``，基线刷新为**写入后回读**的 updatedAt
      （实测 upsert 返回体只有 ``{"id": N, "action": ...}``、**没有 updatedAt**，
      不回读的话下一轮会把自家刚推的内容当成「璇玑又变了」再覆盖一遍）
    """
    from .routers.integrations import _xuanji_mcp

    doc_id = str(getattr(item, "xj_doc_id", "") or "").strip()
    if not doc_id:
        return "failed"  # 无锚条目不该走到这（调用方都已按 xj_doc_id != "" 过滤）

    try:
        current = await _resolve_xuanji_doc(config, doc_id)
    except XuanjiReadError:
        # C1：读不动只是「这一问没问成」，保留 dirty 等下一轮重试，**绝不打冲突旗**
        logger.warning(
            "璇玑文档读取失败，按 failed 处理（保留 dirty、不置冲突）：条目 %s / 文档 %s",
            item.id,
            doc_id,
            exc_info=True,
        )
        return "failed"

    remote_title = str((current or {}).get("title") or "").strip()
    if current is None or not remote_title:
        item.xj_conflict = True
        await db.commit()
        return "missing"

    args: dict = {"title": remote_title, "content": item.content or ""}
    folder_id = await _resolve_folder_id(config, doc_id, current)
    if folder_id is not None:
        args["folderId"] = folder_id

    try:
        await _xuanji_mcp(config, "document_upsert", args)
    except Exception:  # noqa: BLE001  网络/上游错误：留 dirty 等下一轮，绝不上抛
        logger.warning("璇玑回写失败（条目 %s / 文档 %s）", item.id, doc_id, exc_info=True)
        return "failed"

    try:
        fresh = await _resolve_xuanji_doc(config, doc_id)  # 写入后回读基线
    except XuanjiReadError:
        # 写**已经成功了**：回读失败只影响基线是否前移（下一轮最多幂等空推一次），
        # 绝不能因此判 failed —— 那会把 dirty 白留着、下轮再推一遍还谎报一次失败。
        logger.warning(
            "璇玑写入成功但基线回读失败（条目 %s / 文档 %s）", item.id, doc_id, exc_info=True
        )
        fresh = None

    baseline = _norm_ts((fresh or current).get("updatedAt"))
    if baseline:
        item.xj_doc_updated_at = baseline
    item.xj_local_dirty = False
    await db.commit()
    return "pushed"


async def push_dirty_batch(config: IntegrationConfig, db: AsyncSession) -> dict:
    """批量兜底：把「有锚 + 本地已改 + 未冲突」的条目逐个回写璇玑。

    返回 ``{pushed, missing, failed, kept_dirty}``：

    - ``pushed``：成功（已清 dirty）
    - ``missing``：断锚（已置 conflict，dirty 保留待人工裁）
    - ``failed``：写通道失败（dirty 保留，下一轮自动重试）
    - ``kept_dirty``：跑完仍脏的条数（= ``missing + failed``），给 UI 显示「还剩几条待回写」
    """
    rows = (
        await db.execute(
            select(LibraryItem).where(
                LibraryItem.xj_doc_id.is_not(None),
                LibraryItem.xj_doc_id != "",
                LibraryItem.xj_local_dirty.is_(True),
                LibraryItem.xj_conflict.is_(False),
            )
        )
    ).scalars().all()

    pushed = missing = failed = 0
    for item in rows:
        outcome = await push_item_to_xuanji(item, config, db)
        if outcome == "pushed":
            pushed += 1
        elif outcome == "missing":
            missing += 1
        else:
            failed += 1
    return {"pushed": pushed, "missing": missing, "failed": failed, "kept_dirty": missing + failed}


async def push_after_save(item_id: int, user_id: int) -> None:
    """保存后立即回写（BackgroundTasks 后台任务；仿 t3 定稿钩子）。

    - 自建 ``SessionLocal``：后台任务跑时请求 session 已关，绝不能复用
    - 未启用同步 / 未配 url / 条目无锚 / 条目已删 → 静默返回
    - 全程 try/except 兜底，异常只 logging 一行：回写是锦上添花，绝不打扰保存本身
    - **不持 ``_SYNC_LOCK``**：单条目推送按标题幂等、重复无害；锁只护批量与全对账
    """
    from .db import SessionLocal
    from .models import IntegrationConfig as IC

    try:
        async with SessionLocal() as db:
            item = await db.get(LibraryItem, item_id)
            if item is None or not str(item.xj_doc_id or "").strip():
                return
            config = (
                (await db.execute(select(IC).where(IC.user_id == user_id))).scalars().first()
            )
            if config is None or not config.xj_sync_enabled or not config.xuanji_url:
                return
            await push_item_to_xuanji(item, config, db)
    except Exception:  # noqa: BLE001  后台任务绝不把异常抛回请求链路
        logger.warning("保存后回写璇玑失败（条目 %s）", item_id, exc_info=True)


async def sync_all(config: IntegrationConfig, db: AsyncSession) -> dict:
    """持 ``_SYNC_LOCK`` 跑完整一轮对账：入方向 + dirty 批量兜底（E2 的唯一全局入口）。

    返回入方向 7 键 + ``{pushed, missing, push_failed}``。定时循环与手动
    ``POST /xuanji/sync`` 都走这里，保证同一时刻进程内只有一轮对账在改库。
    """
    async with _SYNC_LOCK:
        out = await sync_from_xuanji(config, db)
        push = await push_dirty_batch(config, db)
    return {
        **out,
        "pushed": push["pushed"],
        "missing": push["missing"],
        "push_failed": push["failed"],
    }


# ======================================================================
# 调度（批E · r5）：到期判定 + 一跳
# ======================================================================


#: 调度失败退避表（进程内）：``config_id → (下次允许尝试的时刻, 连续失败次数)``。
#: 一个持续 401 / 网络不通的 config 若每 60s 撞一次墙只是白打璇玑，故失败后指数退避；
#: 成功即清除。进程内即可——重启后重来一遍对上游无害（幂等读）。
_SYNC_BACKOFF: dict[int, tuple[datetime, int]] = {}

#: 退避基档（分钟）与封顶（分钟）：5 → 10 → 20 → 40 → 60 → 60 …
_BACKOFF_BASE_MIN = 5
_BACKOFF_MAX_MIN = 60


def _backoff_minutes(fail_count: int) -> int:
    """连续失败 ``fail_count`` 次后应退避的分钟数：5 → 10 → 20 → 40 → 60（封顶 1h）。

    纯函数（便于表驱动单测）。``fail_count`` 钳到 ``[1, 10]``：既让 0/负数按首档处理，
    也避免 ``2 ** 大数`` 溢出。任何入参都落在 ``[5, 60]``。
    """
    tries = max(1, min(int(fail_count), 10))
    return min(_BACKOFF_BASE_MIN * (2 ** (tries - 1)), _BACKOFF_MAX_MIN)


def _parse_iso(raw: str) -> datetime:
    """ISO 8601 解析：末尾 ``Z`` 归一成 ``+00:00``（兼容未内置 Z 的旧运行时）。"""
    text = str(raw).strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def _xj_sync_due(last_at: str, interval_min: int, now: datetime) -> bool:
    """到期判定（m6 硬约束）：**一律 ``fromisoformat`` 解析比较，绝不字符串比**。

    ``xj_sync_last_at`` 落库是 ``datetime.now(timezone.utc).isoformat()`` 的 ``+00:00``
    形态，而璇玑侧基线是 ``...Z`` 形态——字符串比较在带偏移/不同形态时会给出反向结论
    （例：``2026-10-14T12:30:00+02:00`` 实为 10:30Z，比 ``12:00Z`` **早**，字符串比却说「晚」）。

    - 空串 / ``None``（从未同步）→ 到期
    - 畸形串（解析失败）→ 视为到期：宁可多跑一轮，不可永不跑（fail-open）
    - ``interval_min`` 非正 / 非数 → 按 1 分钟兜底，避免 0 变成「每轮都跑」的猛敲
    """
    raw = ("" if last_at is None else str(last_at)).strip()
    if not raw:
        return True
    try:
        last = _parse_iso(raw)
    except (TypeError, ValueError):
        return True

    try:
        interval = int(interval_min)
    except (TypeError, ValueError):
        interval = 10
    if interval <= 0:
        interval = 1

    if last.tzinfo is None:  # 裸时间按 UTC（落库形态都带 tz，此处只作防御）
        last = last.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last) >= timedelta(minutes=interval)


async def _xj_sync_tick(now: datetime | None = None) -> int:
    """调度一跳：给「启用 + 配了 url/folders + 到点」的 config 各跑一轮同步。返回执行数。

    与 ``routers/integrations.auto_backup_tick`` 同款：自建 ``SessionLocal``、
    单 config 异常只 ``logging.warning`` 后继续下一个。目标清单先快照成纯值再逐个
    重取——单份失败要 ``rollback``，而 rollback 会 expire 会话内所有对象，届时再去
    读已失效的 config 属性会炸（async 下是 MissingGreenlet）。

    **失败退避（M3）**：失败过的 config 记进 ``_SYNC_BACKOFF``，退避期内直接跳过、
    连璇玑都不碰；连续失败 5→10→20→40→60min 封顶；一旦成功立即清除。
    """
    from .db import SessionLocal

    moment = now or datetime.now(timezone.utc)
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(IntegrationConfig).where(
                    IntegrationConfig.xj_sync_enabled.is_(True),
                    IntegrationConfig.xuanji_url != "",
                    IntegrationConfig.xj_sync_folders != "",
                )
            )
        ).scalars().all()
        targets = [
            (cfg.id, cfg.xj_sync_last_at, cfg.xj_sync_interval_min) for cfg in rows
        ]

        ran = 0
        for cfg_id, last_at, interval in targets:
            backoff = _SYNC_BACKOFF.get(cfg_id)
            if backoff is not None and moment < backoff[0]:
                continue  # 退避期内：别再撞墙（省一次无效上游请求）
            if not _xj_sync_due(last_at, interval, moment):
                continue
            try:
                config = await db.get(IntegrationConfig, cfg_id)
                if config is None:
                    continue
                await sync_all(config, db)
                ran += 1
                _SYNC_BACKOFF.pop(cfg_id, None)  # 成功即清除退避
            except Exception:  # noqa: BLE001  单个 config 失败不拖垮其余
                await db.rollback()
                fails = (backoff[1] if backoff is not None else 0) + 1
                _SYNC_BACKOFF[cfg_id] = (
                    moment + timedelta(minutes=_backoff_minutes(fails)),
                    fails,
                )
                logger.warning(
                    "璇玑定时同步失败（config=%s，连续 %s 次，退避 %s 分钟）",
                    cfg_id,
                    fails,
                    _backoff_minutes(fails),
                    exc_info=True,
                )
        return ran
