"""登录鉴权接口(方案 B):注册 / 登录 / 查当前用户。

  POST /auth/register  注册并直接返回 token(免去注册后再登录一次)
  POST /auth/login     登录返回 token
  GET  /auth/me        用 token 查当前用户信息(前端刷新后回填登录态)
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.deps import get_current_user
from services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


# ───────── 出入参模型 ────────────────────────────────────
class RegisterBody(BaseModel):
    username: str
    password: str


class LoginBody(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str = "user"   # 'admin' / 'user',前端据此决定是否显示数据源管理按钮


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


# ───────── 接口 ──────────────────────────────────────────
@router.post("/register", response_model=TokenOut)
async def register(body: RegisterBody) -> TokenOut:
    user = await auth_service.register_user(body.username, body.password)
    token = auth_service.create_access_token(user.id, user.username)
    return TokenOut(access_token=token, user=UserOut(id=user.id, username=user.username, role=auth_service.role_of(user.username)))


@router.post("/login", response_model=TokenOut)
async def login(body: LoginBody) -> TokenOut:
    user = await auth_service.authenticate_user(body.username, body.password)
    token = auth_service.create_access_token(user.id, user.username)
    return TokenOut(access_token=token, user=UserOut(id=user.id, username=user.username, role=auth_service.role_of(user.username)))


@router.get("/me", response_model=UserOut)
async def me(user_id: str = Depends(get_current_user)) -> UserOut:
    # get_current_user 已校验 token 有效;这里回查用户名返回给前端。
    user = await auth_service.get_user_by_id(int(user_id))
    if user is None:
        # token 有效但用户被删了(极少见),按未登录处理。
        raise HTTPException(status_code=401, detail="用户不存在")
    return UserOut(id=user.id, username=user.username, role=auth_service.role_of(user.username))
