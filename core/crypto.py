"""数据源连接密码的对称加解密(Fernet)。

密钥来源:环境变量 WENSHU_DS_SECRET;没配则回退复用 auth.secret(本地开发够用,
生产应单独配 WENSHU_DS_SECRET,且不进 git)。
Fernet 要 32 字节 urlsafe-base64 key,这里用 sha256(secret) 派生,任意字符串都能当 secret。
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet

from conf.app_config import app_config


def _fernet() -> Fernet:
    secret = os.environ.get("WENSHU_DS_SECRET") or app_config.auth.secret
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)


def encrypt(plaintext: str) -> str:
    """明文密码 → 加密 token(存库)。"""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """加密 token → 明文密码(连接时用)。"""
    return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
