"""SSO 联邦认证接收端（P1-3）：GET /sso/launch。

接收天宫（Tiangang）签发的短期一次性 JWT，校验通过后按北斗本地登录
机制（HS256 JWT Bearer token，见 security.create_token）建立登录态，
302 跳转首页 "/"。

SSO Launch 协议 v1（固定契约，不得更改）：
- 密钥：环境变量 TIANGONG_SSO_SECRET；未配置 → HTTP 501 {"detail":"SSO 未配置"}
- 端点：GET /sso/launch?token=<jwt>
- JWT：HS256，claims {typ:"sso-launch", sub, username?, role, app:"beidou",
  iat, exp=iat+120, jti}
- 校验顺序：SSO 配置 → token 存在 → 验签+exp（任何失败 → 401
  {"detail":"凭证无效或已过期"}）→ typ==="sso-launch" 且 app==="beidou"
  （否则 401）→ jti 一次性（模块级内存 dict {jti: exp}，命中已存在 → 401，
  顺手清理过期项，TTL 到各自 exp）。
- 通过 → 按北斗自身登录流程同样机制建本地登录态（与 /api/auth/login
  返回的 token 同款：HS256 本地 JWT，前端存 localStorage 后以
  Authorization: Bearer 使用）→ 302 跳转 "/?sso_token=<local_token>"。
- 任何失败不建立登录态。

取舍说明：
- 本地用户自动建档：SSO 首次登录时若本地无对应 username 用户，自动创建
  （password_hash 置不可验证占位符 "!"，verify_password 恒 False，杜绝
  密码登录）；role 仅在天宫声明 admin 时映射为 admin，其余一律 author。
- 已存在用户不更新角色（v1 不做角色同步）。
- token 经 URL query 传递是 302 无响应体下的最小衔接方案；前端拿到后应
  立即用 history.replaceState 清除 URL 中的 sso_token，避免残留历史记录。
"""

import time

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..db import get_db
from ..models import User
from ..security import create_token

router = APIRouter(prefix="/sso", tags=["sso"])

# jti 一次性缓存：{jti: exp_unix}。仅进程内存（v1 单实例足够），
# TTL 各自到 exp；每次使用前清理已过期项，避免误判复用并控制内存上限。
_USED_JTIS: dict[str, int] = {}

_SSO_ALGORITHM = "HS256"

_INVALID_MSG = "凭证无效或已过期"


def _cleanup_expired_jtis(now: int) -> None:
    """移除已过期（exp <= now）的 jti 记录。"""
    for jti in [j for j, exp in _USED_JTIS.items() if exp <= now]:
        _USED_JTIS.pop(jti, None)


async def _resolve_or_create_user(db: AsyncSession, payload: dict) -> User:
    """按 username（缺省回退 sub）解析本地用户；不存在则自动建档（SSO 免密）。"""
    sub = payload.get("sub")
    username = (payload.get("username") or str(sub)).strip()[:64]
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if user is not None:
        return user
    role = "admin" if payload.get("role") == "admin" else "author"
    user = User(
        username=username,
        # 占位符：verify_password 拆分失败恒为 False，SSO 用户不能走密码登录
        password_hash="!",
        role=role,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.get("/launch")
async def sso_launch(
    token: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
):
    # 1) SSO 配置：未配置 → 501
    if not settings.tiangong_sso_secret:
        return JSONResponse(status_code=501, content={"detail": "SSO 未配置"})

    # 2) token 存在
    if not token:
        raise HTTPException(401, _INVALID_MSG)

    # 3) 验签 + exp（PyJWT 自动校验 exp）
    try:
        payload = jwt.decode(token, settings.tiangong_sso_secret, algorithms=[_SSO_ALGORITHM])
    except jwt.PyJWTError:
        raise HTTPException(401, _INVALID_MSG)

    # 4) typ + app 定向校验
    if payload.get("typ") != "sso-launch" or payload.get("app") != "beidou":
        raise HTTPException(401, _INVALID_MSG)

    # 必填 claims：sub / exp / jti（缺失视为无效凭证）
    if payload.get("sub") is None:
        raise HTTPException(401, _INVALID_MSG)
    exp = payload.get("exp")
    jti = payload.get("jti")
    if not isinstance(exp, int) or not isinstance(jti, str) or not jti:
        raise HTTPException(401, _INVALID_MSG)

    # 5) jti 一次性：先清理过期项，命中已存在 → 401；否则登记（TTL=各自 exp）
    now = int(time.time())
    _cleanup_expired_jtis(now)
    if jti in _USED_JTIS:
        raise HTTPException(401, _INVALID_MSG)
    _USED_JTIS[jti] = exp

    # 6) 建立北斗本地登录态（与 /api/auth/login 同机制：HS256 本地 token）
    user = await _resolve_or_create_user(db, payload)
    local_token = create_token(user.id, user.username)
    return RedirectResponse(url=f"/?sso_token={local_token}", status_code=302)
