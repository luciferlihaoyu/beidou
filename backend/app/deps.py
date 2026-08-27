from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import Chapter, Novel, User
from .security import decode_token

bearer = HTTPBearer(auto_error=False)


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
