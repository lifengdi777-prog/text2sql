"""请求级身份与数据集归属校验。

身份认证(方案 B,JWT):前端登录后拿到 access_token,后续请求带
  Authorization: Bearer <token>
本文件把「获取当前用户」收敛成一个依赖 get_current_user,业务路由只依赖它。
将来换鉴权方式(企业 SSO / 网关)时,**只改本文件这一个函数**,
所有路由代码一行都不用动。

返回值统一是 str(user.id),与 upload_datasets.user_id 存的值对齐,
ownership 校验(require_owned_dataset)直接比对即可。
"""
from fastapi import Depends, Header, HTTPException

from models.upload import UploadDatasetMySQL
from repositories.upload import UploadDatasetRepository
from services.auth import decode_access_token, decode_access_token_username, get_user_by_id, is_admin_username
from services.excel_ingest import get_session_factory


def _extract_bearer_token(authorization: str | None) -> str:
    """从 Authorization 头取出 Bearer token;缺失/格式错误 → 401。"""
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="未登录,请先登录")
    token = authorization[len(prefix):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录,请先登录")
    return token


async def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """从 Authorization: Bearer <token> 解析当前用户 id(字符串)。

    缺失 / 格式错误 / token 无效或过期 → 401(由 decode_access_token 抛)。
    """
    return decode_access_token(_extract_bearer_token(authorization))


async def get_current_username(authorization: str | None = Header(default=None)) -> str | None:
    """从 token 解析当前用户名(name claim),供 Langfuse 按名字归类。

    老 token 无 name 字段 → 返回 None(调用方退回用 id)。token 无效/过期 → 401。
    """
    return decode_access_token_username(_extract_bearer_token(authorization))


async def require_admin(user_id: str = Depends(get_current_user)) -> str:
    """管理员校验:用于数据源的增/删/改/重建等管理操作。

    管理员身份的唯一真相源是 conf.yaml 的 auth.admin_usernames(按用户名判,不读库 role)。
    实时按当前配置判,故在配置里增减管理员、重启后立即生效。
    非管理员 → 403。返回 user_id,供路由继续用(日志/审计)。
    """
    user = await get_user_by_id(int(user_id))
    if user is None or not is_admin_username(user.username):
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user_id


async def require_owned_dataset(dataset_id: int, user_id: str) -> UploadDatasetMySQL:
    """加载数据集并校验归属。

    不存在 / 不属于当前用户 → 统一抛 404(不区分两种情况,避免暴露「数据集存在但无权访问」)。

    这是辅助函数而非 FastAPI 依赖:它同时需要 path 里的 dataset_id 和
    依赖注入的 user_id,在各路由内显式调用最直白。
    """
    Session = get_session_factory()
    async with Session() as session:
        repo = UploadDatasetRepository(session)
        ds = await repo.get(dataset_id)
    if ds is None or ds.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"数据集 {dataset_id} 不存在")
    return ds
