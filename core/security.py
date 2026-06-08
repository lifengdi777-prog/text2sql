"""启动时的安全自检:检测仍在用的「开发默认值」并显著告警。

只告警、不拒启 —— 避免本地开发被卡;但日志里给出醒目提示,
提醒上生产前务必收敛(JWT secret / CORS / 管理员默认密码)。
"""
from __future__ import annotations

from conf.app_config import DEFAULT_JWT_SECRET, app_config
from core.log import logger


def log_security_warnings() -> None:
    """检查配置级的不安全默认值(JWT secret、CORS 放开)并告警。

    管理员默认密码的告警在 services.auth.ensure_admin_user 里(那里有 DB 上下文)。
    """
    warnings: list[str] = []

    # 1) JWT secret 仍是开发默认值 → token 可被伪造
    if app_config.auth.secret == DEFAULT_JWT_SECRET:
        warnings.append(
            "JWT secret 仍是开发默认值,token 可被伪造!"
            "请在 app_config.yaml 的 auth.secret 设一个随机强密钥。"
        )

    # 2) CORS 放开到任意域 → 跨域风险(生产应收敛到明确域名)
    if "*" in app_config.cors.allow_origins:
        warnings.append(
            "CORS 允许任意域(allow_origins=['*'])。"
            "生产请在 app_config.yaml 的 cors.allow_origins 填明确的前端域名。"
        )

    if warnings:
        # 不用 emoji:Windows GBK 控制台会因非 GBK 字符触发 UnicodeEncodeError。
        lines = "\n".join(f"  [!] {w}" for w in warnings)
        logger.warning(
            "\n========== 安全自检:发现开发默认配置(上生产前请处理)==========\n"
            f"{lines}\n"
            "==============================================================="
        )
