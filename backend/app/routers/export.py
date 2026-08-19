"""多格式导出：TXT / HTML / EPUB。按分卷组织目录，章节自动带"第X章"连续编号。"""

import io
import html as html_mod
import uuid
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..deps import get_owned_novel
from ..models import Chapter, Novel, Volume
from ..utils import chapter_display_title, order_chapters, strip_html

router = APIRouter(prefix="/api/novels/{novel_id}/export", tags=["export"])


async def _structure(novel: Novel, db: AsyncSession):
    """返回 [(卷或None, [(章节, 序号), ...]), ...]，按显示顺序。"""
    chapters = (await db.execute(select(Chapter).where(Chapter.novel_id == novel.id))).scalars().all()
    volumes = (await db.execute(select(Volume).where(Volume.novel_id == novel.id))).scalars().all()
    ordered = order_chapters(chapters, volumes)
    volume_map = {v.id: v for v in volumes}

    groups: list[tuple[Volume | None, list[tuple[Chapter, int]]]] = []
    for i, chapter in enumerate(ordered):
        if not strip_html(chapter.content).strip() and not chapter.title:
            continue  # 跳过空章节
        volume = volume_map.get(chapter.volume_id)
        if not groups or groups[-1][0] != volume:
            groups.append((volume, []))
        groups[-1][1].append((chapter, i + 1))
    return groups


def _volume_heading(index: int, volume: Volume | None) -> str | None:
    if volume is None:
        return None
    # 卷名由作者完整命名（如"第一卷 潜龙在渊""作品相关"），导出不再叠加序号
    return volume.title.strip()


def _export_txt(novel: Novel, groups) -> bytes:
    lines = [f"《{novel.title}》", f"作者：{novel.author}" if novel.author else "", ""]
    vol_index = 0
    for volume, chapters in groups:
        if volume is not None:
            vol_index += 1
            lines.append(f"\n\n\n{_volume_heading(vol_index, volume)}\n")
        for chapter, number in chapters:
            lines.append(f"\n\n{chapter_display_title(chapter.title, number)}\n")
            lines.append(strip_html(chapter.content))
    return "\n".join(lines).encode("utf-8")


def _chapter_html(chapter: Chapter, number: int) -> str:
    title = chapter_display_title(chapter.title, number)
    body = chapter.content.strip() or f"<p>{html_mod.escape(strip_html(chapter.content))}</p>"
    return f"<h2>{html_mod.escape(title)}</h2>\n{body}"


_PAGE_CSS = """
body{font-family:'Noto Serif SC','Songti SC',serif;line-height:1.9;margin:2em auto;max-width:40em;padding:0 1em;color:#24272a}
h1{text-align:center}h1.volume{margin-top:3em;border-bottom:2px solid #24272a;padding-bottom:.4em}
h2{margin-top:2.5em;border-bottom:1px solid #ddd;padding-bottom:.3em}
p{text-indent:2em;margin:.6em 0}
"""


def _export_html(novel: Novel, groups) -> bytes:
    parts = []
    vol_index = 0
    for volume, chapters in groups:
        if volume is not None:
            vol_index += 1
            parts.append(f"<h1 class=\"volume\">{html_mod.escape(_volume_heading(vol_index, volume))}</h1>")
        parts.extend(_chapter_html(c, n) for c, n in chapters)
    body = "\n".join(parts)
    doc = (
        f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{html_mod.escape(novel.title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body><h1>{html_mod.escape(novel.title)}</h1>"
        f"<p style=\"text-align:center;text-indent:0\">{html_mod.escape(novel.author)}</p>{body}</body></html>"
    )
    return doc.encode("utf-8")


def _export_epub(novel: Novel, groups) -> bytes:
    book_id = f"urn:uuid:{uuid.uuid4()}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    buf = io.BytesIO()

    def xhtml(title: str, body: str) -> str:
        return (
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<!DOCTYPE html>\n"
            f"<html xmlns=\"http://www.w3.org/1999/xhtml\" lang=\"zh-CN\"><head>"
            f"<title>{html_mod.escape(title)}</title><style>{_PAGE_CSS}</style></head>"
            f"<body>{body}</body></html>"
        )

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED
        )
        z.writestr(
            "META-INF/container.xml",
            "<?xml version=\"1.0\"?>\n"
            "<container version=\"1.0\" xmlns=\"urn:oasis:names:tc:opendocument:xmlns:container\">"
            "<rootfiles><rootfile full-path=\"OEBPS/content.opf\" media-type=\"application/oebps-package+xml\"/>"
            "</rootfiles></container>",
        )
        manifest, spine = [], []
        nav_groups: list[tuple[str | None, list[tuple[str, str]]]] = []
        i = 0
        vol_index = 0
        for volume, chapters in groups:
            heading = None
            if volume is not None:
                vol_index += 1
                heading = _volume_heading(vol_index, volume)
            entries = []
            for chapter, number in chapters:
                i += 1
                name = f"chapter-{i}.xhtml"
                title = chapter_display_title(chapter.title, number)
                z.writestr(f"OEBPS/{name}", xhtml(title, _chapter_html(chapter, number)))
                manifest.append(f'<item id="c{i}" href="{name}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="c{i}"/>')
                entries.append((title, name))
            nav_groups.append((heading, entries))

        nav_parts = []
        for heading, entries in nav_groups:
            lis = "".join(f'<li><a href="{href}">{html_mod.escape(t)}</a></li>' for t, href in entries)
            if heading:
                nav_parts.append(f"<li><span>{html_mod.escape(heading)}</span><ol>{lis}</ol></li>")
            else:
                nav_parts.append(lis)
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        z.writestr(
            "OEBPS/nav.xhtml",
            xhtml("目录", f"<nav epub:type=\"toc\" xmlns:epub=\"http://www.idpf.org/2007/ops\"><h1>目录</h1><ol>{''.join(nav_parts)}</ol></nav>"),
        )
        z.writestr(
            "OEBPS/content.opf",
            "<?xml version=\"1.0\" encoding=\"utf-8\"?>\n"
            "<package xmlns=\"http://www.idpf.org/2007/opf\" unique-identifier=\"bid\" version=\"3.0\">"
            "<metadata xmlns:dc=\"http://purl.org/dc/elements/1.1/\">"
            f"<dc:identifier id=\"bid\">{book_id}</dc:identifier>"
            f"<dc:title>{html_mod.escape(novel.title)}</dc:title>"
            f"<dc:creator>{html_mod.escape(novel.author or '佚名')}</dc:creator>"
            f"<dc:language>zh-CN</dc:language>"
            f"<meta property=\"dcterms:modified\">{modified}</meta>"
            "</metadata>"
            f"<manifest>{''.join(manifest)}</manifest>"
            f"<spine>{''.join(spine)}</spine></package>",
        )
    return buf.getvalue()


FORMATS = {
    "txt": ("text/plain; charset=utf-8", _export_txt, ".txt"),
    "html": ("text/html; charset=utf-8", _export_html, ".html"),
    "epub": ("application/epub+zip", _export_epub, ".epub"),
}


@router.get("")
async def export_novel(
    format: str = Query(default="txt", pattern="^(txt|html|epub)$"),
    novel: Novel = Depends(get_owned_novel),
    db: AsyncSession = Depends(get_db),
):
    groups = await _structure(novel, db)
    if not groups:
        raise HTTPException(400, "还没有可导出的章节")
    media_type, builder, ext = FORMATS[format]
    data = builder(novel, groups)
    filename = f"{novel.title}{ext}"
    from urllib.parse import quote

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    if format == "html":
        return Response(content=data, media_type=media_type)
    return StreamingResponse(io.BytesIO(data), media_type=media_type, headers=headers)
