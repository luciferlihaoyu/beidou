"""多格式导出：TXT / HTML / EPUB。"""

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
from ..models import Chapter, Novel
from ..utils import strip_html

router = APIRouter(prefix="/api/novels/{novel_id}/export", tags=["export"])


async def _chapters(novel: Novel, db: AsyncSession) -> list[Chapter]:
    result = await db.execute(
        select(Chapter).where(Chapter.novel_id == novel.id).order_by(Chapter.sort_order, Chapter.id)
    )
    return [c for c in result.scalars().all() if strip_html(c.content).strip() or c.title]


def _export_txt(novel: Novel, chapters: list[Chapter]) -> bytes:
    lines = [f"《{novel.title}》", f"作者：{novel.author}" if novel.author else "", ""]
    for chapter in chapters:
        lines.append(f"\n\n{chapter.title}\n")
        lines.append(strip_html(chapter.content))
    return "\n".join(lines).encode("utf-8")


def _chapter_html(chapter: Chapter) -> str:
    body = chapter.content.strip() or f"<p>{html_mod.escape(strip_html(chapter.content))}</p>"
    return f"<h2>{html_mod.escape(chapter.title)}</h2>\n{body}"


_PAGE_CSS = """
body{font-family:'Noto Serif SC','Songti SC',serif;line-height:1.9;margin:2em auto;max-width:40em;padding:0 1em;color:#24272a}
h1{text-align:center}h2{margin-top:2.5em;border-bottom:1px solid #ddd;padding-bottom:.3em}
p{text-indent:2em;margin:.6em 0}
"""


def _export_html(novel: Novel, chapters: list[Chapter]) -> bytes:
    body = "\n".join(_chapter_html(c) for c in chapters)
    doc = (
        f"<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        f"<title>{html_mod.escape(novel.title)}</title><style>{_PAGE_CSS}</style></head>"
        f"<body><h1>{html_mod.escape(novel.title)}</h1>"
        f"<p style=\"text-align:center;text-indent:0\">{html_mod.escape(novel.author)}</p>{body}</body></html>"
    )
    return doc.encode("utf-8")


def _export_epub(novel: Novel, chapters: list[Chapter]) -> bytes:
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
        manifest, spine, toc_entries = [], [], []
        for i, chapter in enumerate(chapters):
            name = f"chapter-{i + 1}.xhtml"
            z.writestr(f"OEBPS/{name}", xhtml(chapter.title, _chapter_html(chapter)))
            manifest.append(f'<item id="c{i + 1}" href="{name}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="c{i + 1}"/>')
            toc_entries.append((chapter.title, name))

        nav_lis = "".join(
            f'<li><a href="{href}">{html_mod.escape(title)}</a></li>' for title, href in toc_entries
        )
        manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        z.writestr(
            "OEBPS/nav.xhtml",
            xhtml("目录", f"<nav epub:type=\"toc\" xmlns:epub=\"http://www.idpf.org/2007/ops\"><h1>目录</h1><ol>{nav_lis}</ol></nav>"),
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
    chapters = await _chapters(novel, db)
    if not chapters:
        raise HTTPException(400, "还没有可导出的章节")
    media_type, builder, ext = FORMATS[format]
    data = builder(novel, chapters)
    filename = f"{novel.title}{ext}"
    from urllib.parse import quote

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    if format == "html":
        return Response(content=data, media_type=media_type)
    return StreamingResponse(io.BytesIO(data), media_type=media_type, headers=headers)
