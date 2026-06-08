"""轻量登录鉴权(方案 B):用户名 + 密码 + JWT。

职责:
  1) 密码:bcrypt 加盐哈希 / 校验(明文密码永不落库);
  2) JWT:签发 / 解码 access_token(sub = 用户 id 字符串);
  3) 业务:注册 / 登录(复用 upload 库的 sessionmaker + UserRepository)。

设计取舍(够用即可,不做企业级 SSO):
  - token 无状态:服务端不存 session,改 secret 即可让全部 token 失效;
  - 用户表跟 upload_datasets 同库(db_upload),共用一套连接池;
  - datasets.user_id 存的就是这里的 str(user.id),登录后归属判断天然对齐。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException
from sqlalchemy import text

from conf.app_config import app_config
from models.user import UserMySQL
from repositories.user import UserRepository
from services.excel_ingest import get_session_factory

# bcrypt 单次最多处理 72 字节,超出部分会被静默截断;先截断避免长密码踩坑。
_BCRYPT_MAX_BYTES = 72


# ───────── 密码哈希 ─────────────────────────────────────
def hash_password(password: str) -> str:
    """bcrypt 加盐哈希,返回 60 字符串(含算法/cost/盐/摘要)。"""
    raw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """校验明文密码与库里哈希是否匹配。"""
    raw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    try:
        return bcrypt.checkpw(raw, password_hash.encode("utf-8"))
    except ValueError:
        # 库里哈希格式异常(脏数据)时不抛栈,直接判失败。
        return False


# ───────── JWT 签发 / 解码 ───────────────────────────────
def create_access_token(user_id: int, username: str) -> str:
    """签发 access_token,sub=用户 id 字符串,name=用户名,带过期时间。"""
    cfg = app_config.auth
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "name": username,  # 用户名:供 Langfuse 等按可读名字归类 trace
        "iat": now,
        "exp": now + timedelta(minutes=cfg.access_token_expire_minutes),
    }
    return jwt.encode(payload, cfg.secret, algorithm=cfg.algorithm)


def _decode_payload(token: str) -> dict:
    """解码并校验 token 签名/有效期,返回完整 payload。无效/过期 → 401。"""
    cfg = app_config.auth
    try:
        return jwt.decode(token, cfg.secret, algorithms=[cfg.algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="登录已过期,请重新登录")
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="无效的登录凭证")


def decode_access_token(token: str) -> str:
    """解码 token,返回 sub(用户 id 字符串)。无效/过期 → 401。"""
    sub = _decode_payload(token).get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="无效的登录凭证")
    return str(sub)


def decode_access_token_username(token: str) -> str | None:
    """解码 token,返回 name(用户名)。老 token 无该字段 → None。无效/过期 → 401。"""
    name = _decode_payload(token).get("name")
    return str(name) if name else None


# ───────── 注册 / 登录 ───────────────────────────────────
async def register_user(username: str, password: str) -> UserMySQL:
    """注册新用户。用户名已存在 → 409。"""
    username = (username or "").strip()
    if not (3 <= len(username) <= 32):
        raise HTTPException(status_code=400, detail="用户名长度需为 3-32 个字符")
    if len(password or "") < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")

    Session = get_session_factory()
    async with Session() as session:
        repo = UserRepository(session)
        if await repo.get_by_username(username) is not None:
            raise HTTPException(status_code=409, detail="该用户名已被注册")
        user = await repo.create(username, hash_password(password))
        await session.commit()
        return user


async def authenticate_user(username: str, password: str) -> UserMySQL:
    """登录校验。用户名不存在或密码错误 → 统一 401(不暴露是哪一个)。"""
    username = (username or "").strip()
    Session = get_session_factory()
    async with Session() as session:
        repo = UserRepository(session)
        user = await repo.get_by_username(username)
    if user is None or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return user


async def get_user_by_id(user_id: int) -> UserMySQL | None:
    """按 id 取用户(get_current_user 解出 sub 后回查用)。"""
    Session = get_session_factory()
    async with Session() as session:
        repo = UserRepository(session)
        return await repo.get_by_id(user_id)


# ───────── 启动迁移 + 管理员引导 ──────────────────────────
# 历史默认密码:仅用于「存量 admin 仍是弱口令」的兼容告警,不再作为新建口令。
_LEGACY_DEFAULT_PASSWORDS = ("admin123", "admin")


async def ensure_admin_user() -> None:
    """启动时:给 users 表幂等补 role 列,并确保存在管理员账号 admin。

    - 旧库(users 无 role 列)→ ALTER 补列;新库 ensure_app_tables 已按模型建好该列。
    - 不存在 admin → 创建。初始密码来源(无全网通用默认口令):
        1) 环境变量 WENSHU_ADMIN_PASSWORD(≥6 位)→ 用它;
        2) 否则随机生成强密码,并在日志里**打印一次**(下次启动不再显示)。
    - 已存在 admin → 只确保 role=admin,**绝不重置密码**;若仍是历史弱口令则告警。
    """
    from core.log import logger

    Session = get_session_factory()
    async with Session() as session:
        cols = set((await session.execute(text(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='users'"
        ))).scalars().all())
        if cols and "role" not in cols:
            await session.execute(text(
                "ALTER TABLE users ADD COLUMN role VARCHAR(16) NOT NULL DEFAULT 'user'"
            ))
            await session.commit()

        repo = UserRepository(session)
        admin = await repo.get_by_username("admin")

        if admin is None:
            # 首次创建:优先用配置 auth.admin_password,否则随机生成并打印一次。
            cfg_pwd = (app_config.auth.admin_password or "").strip()
            if len(cfg_pwd) >= 6:
                init_pwd, from_cfg = cfg_pwd, True
            else:
                if cfg_pwd:
                    logger.warning("auth.admin_password 少于 6 位,已忽略,改用随机密码。")
                init_pwd, from_cfg = secrets.token_urlsafe(12), False
            admin = await repo.create("admin", hash_password(init_pwd))
            admin.role = "admin"
            await session.commit()
            if from_cfg:
                logger.info("已用配置 auth.admin_password 初始化管理员 admin。")
            else:
                logger.warning(
                    "\n================= 已创建管理员账号 =================\n"
                    "  用户名: admin\n"
                    f"  初始密码(仅本次打印,请立即保存): {init_pwd}\n"
                    "  下次启动不再显示;可在 conf/app_config.yaml 的 auth.admin_password 指定。\n"
                    "==================================================="
                )
            return

        # 已存在:只确保是管理员,绝不动密码。
        admin.role = "admin"
        await session.commit()
        # 兼容告警:存量 admin 仍是历史弱口令 → 提醒修改。
        if any(verify_password(p, admin.password_hash) for p in _LEGACY_DEFAULT_PASSWORDS):
            logger.warning(
                "管理员 admin 仍是历史默认弱口令,请尽快修改(当前无应用内改密,"
                "可直接改库 users.password_hash,或后续补改密接口)。"
            )
