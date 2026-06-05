"""「智能助手」编辑子图的 State / Context schema。

继承 WSAgentState(复用 messages/error 等),额外字段是编辑流程的中间状态。
跟问数图(dataset_agent)一个套路,便于统一维护。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from agent.schemas import WSAgentState


class DatasetEditState(WSAgentState):
    """编辑子图运行时状态。指令取自 messages 里最后一条 HumanMessage。"""
    # ── 请求级输入(router 注入)──
    dataset_id: int | None = None
    session_id: int | None = None
    active_sheet: str | None = None      # 用户当前选中的 sheet tab(默认操作对象)
    confirmed: bool = False              # 破坏性操作的二次确认

    # ── parse_intent 填 ──
    intent_kind: str | None = None       # edit / query / chitchat
    # should_continue(继承自父类):edit → True 继续;query/chitchat → False 短路到 END

    # ── generate_sql 填(物化快照,供 LLM + 后续校验/应用复用)──
    current_md: str = ""                 # 喂 LLM 的"当前数据"(结构+样例+真实值参考)
    active_ops: list[str] = []           # 已应用 op 的 SQL(重放基线)
    known_sheets: list[str] = []         # 可引用表(数据 sheet + 会话已建汇总表)
    data_sheets: list[str] = []          # 原始数据 sheet(不可被 CREATE 覆盖)
    generated_sql: str | None = None     # LLM 当前产出的 SQL

    # ── validate_sql 填 ──
    sql_issues: list[str] = []           # 校验/执行问题(非空 → 路由到 correct)
    edit_retry: int = 0                  # 修正次数(封顶 MAX_RETRY)
    normalized_sql: str | None = None    # 校验规范化后的 SQL(真正执行/落库的)
    op_type: str | None = None
    target_sheet: str | None = None
    needs_confirm: bool = False
    terminal: bool = False               # 已发终态卡(引导/错误/查询短路)→ 路由到 END

    # ── validate_sql 试执行后填,供 apply_edit 落库/出卡 ──
    created: bool = False                # 是否新建了汇总 sheet
    edit_summary: dict[str, Any] | None = None   # 变更摘要(出卡用)
    edit_diff: dict[str, Any] | None = None       # 截断后的 diff(出卡用)
    edit_affected: dict[str, Any] | None = None   # 落库的 affected(摘要+明细 changes)
    edit_preview: dict[str, Any] | None = None    # 受影响 sheet 的预览页


class DatasetEditContext(BaseModel):
    """编辑子图运行时上下文。大部分依赖走 module-level singleton,这里只放按请求注入的。"""
    user_id: str = "anonymous"
    model_config = ConfigDict(arbitrary_types_allowed=True)
