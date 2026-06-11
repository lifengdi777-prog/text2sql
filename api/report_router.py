"""按需分析报告接口:结果卡上点「生成分析报告」时调用(与 /chart 按钮同模式)。

POST /report  Body: {rows: [...], query: "...", sql: "..."}
  → 返回自包含 HTML(text/html),前端新标签页打开,浏览器可另存/打印成 PDF。
"""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from api.deps import get_current_user
from core.log import logger
from core.rate_limit import llm_rate_limiter
from services.report import build_report_html

router = APIRouter()


class ReportBody(BaseModel):
    rows: list[dict] = []
    query: str = ""
    sql: str | None = None


@router.post("/report")
async def generate_report(body: ReportBody, user_id: str = Depends(get_current_user)):
    """对给定结果行生成分析报告 HTML。空结果直接 400,不浪费 LLM 调用。"""
    if not body.rows:
        raise HTTPException(status_code=400, detail="没有可分析的数据")
    # 按用户限流(与问数/图表共享配额):报告含 LLM 分析 + 图表选型两次调用
    async with llm_rate_limiter.slot(user_id):
        try:
            html = await build_report_html(body.query, body.sql, body.rows)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"生成分析报告失败:{exc}")
            raise HTTPException(status_code=500, detail="报告生成失败,请稍后重试")
    return HTMLResponse(content=html)
