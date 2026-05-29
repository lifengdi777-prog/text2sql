"""请求级身份与数据集归属校验。

身份认证(方案 B,JWT):前端登录后拿到 access_token,后续请求带
  Authorization: Bearer <token>
本文件把「获取当前用户」收敛成一个依赖 get_current_user,业务路由只依赖它。
将来换鉴权方式(企业 SSO / 网关)时,**只改本文件这一个函数**,
所有路由代码一行都不用动。

返回值统一是 str(user.id),与 upload_datasets.user_id 存的值对齐,
ownership 校验(require_owned_dataset)直接比对即可。
"""
from fastapi import Header, HTTPException

from models.upload import UploadDatasetMySQL
from repositories.upload import UploadDatasetRepository
from services.auth import decode_access_token
from services.excel_ingest import get_session_factory


async def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """从 Authorization: Bearer <token> 解析当前用户 id(字符串)。

    缺失 / 格式错误 / token 无效或过期 → 401(由 decode_access_token 抛)。
    """
    prefix = "Bearer "
    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="未登录,请先登录")
    token = authorization[len(prefix):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="未登录,请先登录")
    return decode_access_token(token)


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
