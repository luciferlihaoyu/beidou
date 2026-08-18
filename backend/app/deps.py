from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .db import get_db
from .models import Novel, User
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


async def get_default_ai_config(user: User, db: AsyncSession):
    from .models import AIConfig

    result = await db.execute(
        select(AIConfig).where(AIConfig.user_id == user.id).order_by(AIConfig.is_default.desc(), AIConfig.id)
    )
    config = result.scalars().first()
    if config is None or not config.api_key:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请先在「账号设置」中配置 AI 接口")
    return config


def count_words(text: str) -> int:
    """字数统计：去掉空白后的字符数（网文通行口径）。"""
    import re

    from .utils import strip_html

    plain = strip_html(text)
    return len(re.sub(r"\s", "", plain))
