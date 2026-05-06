"""Authentication API routes"""
from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models.user import User, AuthToken
from models.settings import AppSetting

router = APIRouter(prefix="/api/auth", tags=["auth"])


def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "未登录")
    token_str = authorization.split(" ", 1)[1]
    token = db.query(AuthToken).filter(AuthToken.token == token_str).first()
    if not token:
        raise HTTPException(401, "登录已过期")
    if token.is_expired():
        db.delete(token)
        db.commit()
        raise HTTPException(401, "登录已过期")
    user = db.query(User).filter(User.id == token.user_id).first()
    if not user:
        raise HTTPException(401, "用户不存在")
    if user.status != "active":
        raise HTTPException(403, "账号未激活")
    return user


def require_admin(user: User = Depends(get_current_user)):
    if user.role != "admin":
        raise HTTPException(403, "需要管理员权限")
    return user


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str


class SettingsUpdate(BaseModel):
    registration_open: bool


class ApproveRequest(BaseModel):
    action: str


def _is_registration_open(db: Session) -> bool:
    setting = db.query(AppSetting).filter(AppSetting.key == "registration_open").first()
    if not setting:
        return True
    return setting.value.lower() != "false"


@router.post("/login")
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == req.username).first()
    if not user or not user.check_password(req.password):
        raise HTTPException(401, "用户名或密码错误")
    if user.status != "active":
        raise HTTPException(403, "账号尚未激活")
    db.query(AuthToken).filter(AuthToken.user_id == user.id).delete()
    token = AuthToken.generate(user.id)
    db.add(token)
    db.commit()
    return {"token": token.token, "user": user.to_dict()}


@router.post("/register")
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if not _is_registration_open(db):
        raise HTTPException(403, "当前暂不开放注册")
    existing = db.query(User).filter(User.username == req.username).first()
    if existing:
        raise HTTPException(400, "用户名已存在")
    if len(req.username) < 2:
        raise HTTPException(400, "用户名至少2个字符")
    if len(req.password) < 4:
        raise HTTPException(400, "密码至少4个字符")
    user = User(username=req.username, role="user", status="pending")
    user.set_password(req.password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"ok": True, "message": "注册成功，请等待管理员审核"}


@router.get("/me")
def get_me(user: User = Depends(get_current_user)):
    return user.to_dict()


@router.get("/pending")
def list_pending(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    pending = db.query(User).filter(User.status == "pending").all()
    return [u.to_dict() for u in pending]


@router.post("/approve/{user_id}")
def approve_user(
    user_id: int,
    req: ApproveRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise HTTPException(404, "用户不存在")
    if req.action == "approve":
        target.status = "active"
    elif req.action == "reject":
        db.delete(target)
    else:
        raise HTTPException(400, "无效操作")
    db.commit()
    return {"ok": True}


@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    setting = db.query(AppSetting).filter(AppSetting.key == "registration_open").first()
    open_val = True if not setting else setting.value.lower() != "false"
    return {"registration_open": open_val}


@router.post("/settings")
def update_settings(
    req: SettingsUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    setting = db.query(AppSetting).filter(AppSetting.key == "registration_open").first()
    if not setting:
        setting = AppSetting(key="registration_open", value=str(req.registration_open))
        db.add(setting)
    else:
        setting.value = str(req.registration_open)
    db.commit()
    return {"ok": True, "registration_open": req.registration_open}
