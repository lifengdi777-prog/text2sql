"""请求级身份与数据集归属校验。

当前还没有登录系统,这里是「过渡期」实现:
  身份 = 浏览器首次访问时在 localStorage 生成的匿名 UUID,经请求头 X-Client-Id 带上。

把「获取当前用户」收敛成一个依赖 get_current_user,业务路由只依赖它。
将来接入真鉴权(JWT / 企业 SSO / 网关)时,**只改本文件这一个函数**,
所有路由代码一行都不用动。

⚠️ X-Client-Id 可被伪造,这不是真正的身份认证 —— 它的作用是:
  1) 隔离不同浏览器的数据(各自只看到自己上传的数据集);
  2) 把 ownership 校验的骨架先搭起来,为后续接入真鉴权铺路。
缺该头时归入共享的 anonymous 桶(向后兼容 curl / 老前端)。
"""
from fastapi import Header, HTTPException

from models.upload import UploadDatasetMySQL
from repositories.upload import UploadDatasetRepository
from services.excel_ingest import get_session_factory


async def get_current_user(x_client_id: str | None = Header(default=None)) -> str:
    """从 X-Client-Id 请求头解析当前用户 id;缺失 → anonymous。"""
    cid = (x_client_id or "").strip()
    return cid or "anonymous"


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
