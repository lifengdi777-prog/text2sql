"""按需分析报告:对「一次问数结果」生成自包含的 HTML 报告。

入口是结果卡上的「生成分析报告」按钮(POST /report,与「生成图表」按钮同模式):
  rows + 问题 + SQL → 1 次 LLM 结构化分析(标题/总览/关键发现/建议)
                    → 复用 chart_agent 出主图表(ECharts,经 CDN 脚本渲染)
                    → 渲染成一份自包含 HTML,前端新标签页打开,浏览器可直接另存/打印成 PDF。

只分析当前结果集,不做下钻补查 —— 快(~10s)、稳、成本可控;要更丰富的多查询报告再升级。
"""
from __future__ import annotations

import html as html_mod
import json
from datetime import datetime
from typing import Any

from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel

from agent.llm import llm
from agent.prompts import load_prompt
from core.log import logger

# 喂给 LLM 的最大行数(超出给样本 + 行数说明,防 token 失控)
MAX_LLM_ROWS = 50
# 报告明细表最多展示的行数
MAX_TABLE_ROWS = 100
# ECharts 配置里的"元字段"(对齐前端 ChartPanel 的剥离集合),剩下的才是可渲染 option
_CHART_META_KEYS = {
    "chart_type", "compatible_types", "field_map", "metrics", "message", "hint",
    "notice", "original_sql", "row_count", "columns", "rows", "_fallback_reason",
}


class ReportContent(BaseModel):
    title: str
    overview: str
    key_findings: list[str] = []
    suggestions: list[str] = []


async def _analyze(question: str, sql: str | None, rows: list[dict[str, Any]]) -> ReportContent:
    """一次 LLM 结构化调用,产出报告文字内容。"""
    prompt = await load_prompt("analysis_report")
    sample = rows[:MAX_LLM_ROWS]
    data_desc = (
        f"结果共 {len(rows)} 行。" +
        (f"以下为前 {len(sample)} 行样本:" if len(rows) > len(sample) else "全部数据如下:") +
        "\n" + json.dumps(sample, ensure_ascii=False, default=str)
    )
    structured = llm.with_structured_output(ReportContent, method="json_mode")
    return await structured.ainvoke([  # type: ignore
        SystemMessage(content=prompt),
        HumanMessage(content=f"用户问题:{question}\n执行的 SQL:{sql or '(未提供)'}\n{data_desc}"),
    ])


async def _main_chart_option(question: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """复用 chart_agent 出主图表;非 ECharts 类型(table/metric/empty/error)返回 None(明细表兜底)。"""
    from agent.chart_agent import chart_subgraph
    from agent.chart_agent.schemas import ChartAgentState

    try:
        state = ChartAgentState(messages=[HumanMessage(content=question)], sql_result=rows)
        final = await chart_subgraph.ainvoke(state, context=None)
        config = (final or {}).get("chart_config") or {}
        if config.get("chart_type") in ("line", "multi_line", "bar", "stacked_bar", "pie"):
            return {k: v for k, v in config.items() if k not in _CHART_META_KEYS}
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"报告主图生成失败(报告退化为无图):{exc}")
    return None


def _esc(s: Any) -> str:
    return html_mod.escape(str(s if s is not None else "-"))


def _json_for_script(obj: Any) -> str:
    """嵌入 <script> 的 JSON:转义 </ 防止提前闭合标签。"""
    return json.dumps(obj, ensure_ascii=False, default=str).replace("</", "<\\/")


def _render_html(content: ReportContent, chart_option: dict[str, Any] | None,
                 rows: list[dict[str, Any]], question: str, sql: str | None) -> str:
    """组装自包含 HTML(内联 CSS;ECharts 走 CDN,加载失败仅图表区隐藏,其余内容不受影响)。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    columns = list(rows[0].keys()) if rows else []
    shown = rows[:MAX_TABLE_ROWS]

    findings = "\n".join(
        f'<li>{_esc(t)}</li>' for t in content.key_findings) or "<li>(无)</li>"
    suggestions = "\n".join(
        f'<li>{_esc(t)}</li>' for t in content.suggestions) or "<li>(无)</li>"
    thead = "".join(f"<th>{_esc(c)}</th>" for c in columns)
    tbody = "\n".join(
        "<tr>" + "".join(f"<td>{_esc(r.get(c))}</td>" for c in columns) + "</tr>" for r in shown)
    table_note = (f"<p class='note'>共 {len(rows)} 行,仅展示前 {len(shown)} 行</p>"
                  if len(rows) > len(shown) else "")

    chart_block = ""
    if chart_option is not None:
        chart_block = f"""
  <section>
    <h2>图表</h2>
    <div id="chart" style="width:100%;height:420px"></div>
  </section>
  <script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"
          onerror="document.getElementById('chart').parentElement.style.display='none'"></script>
  <script>
    if (window.echarts) {{
      echarts.init(document.getElementById('chart')).setOption({_json_for_script(chart_option)});
    }}
  </script>"""

    sql_block = ""
    if sql:
        sql_block = f"""
  <details class="sql">
    <summary>查看执行的 SQL</summary>
    <pre>{_esc(sql)}</pre>
  </details>"""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_esc(content.title)}</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; padding: 40px 16px 64px; background: #f1f5f9; color: #1e293b;
         font: 14px/1.9 "PingFang SC", "Microsoft YaHei", system-ui, sans-serif; }}
  .page {{ max-width: 860px; margin: 0 auto; background: #fff; border-radius: 20px;
           padding: 44px 48px 40px; box-shadow: 0 18px 40px rgba(148,163,184,.18); }}
  .brand {{ font-size: 12px; letter-spacing: .35em; color: #0284c7; font-weight: 600;
            text-transform: uppercase; }}
  h1 {{ margin: 6px 0 4px; font-size: 26px; letter-spacing: .01em; }}
  .meta {{ color: #64748b; font-size: 12px; margin-bottom: 28px; }}
  h2 {{ font-size: 16px; margin: 32px 0 12px; padding-left: 10px;
        border-left: 4px solid #0ea5e9; }}
  .overview {{ background: #f0f9ff; border: 1px solid #e0f2fe; border-radius: 14px;
               padding: 14px 18px; }}
  ul {{ margin: 0; padding-left: 22px; }}
  li {{ margin: 6px 0; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
  th {{ background: #0f172a; color: #f1f5f9; text-align: left; padding: 9px 12px;
        font-weight: 500; white-space: nowrap; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #e2e8f0; }}
  tr:hover td {{ background: #f8fafc; }}
  .note {{ color: #94a3b8; font-size: 12px; }}
  .sql summary {{ cursor: pointer; color: #0284c7; font-size: 13px; margin-top: 24px; }}
  .sql pre {{ background: #0f172a; color: #e2e8f0; border-radius: 12px; padding: 14px 16px;
              overflow-x: auto; font-size: 12px; line-height: 1.7; }}
  footer {{ margin-top: 36px; padding-top: 14px; border-top: 1px solid #e2e8f0;
            color: #94a3b8; font-size: 12px; display: flex; justify-content: space-between; }}
  @media print {{ body {{ background: #fff; padding: 0; }}
                  .page {{ box-shadow: none; border-radius: 0; }} }}
</style>
</head>
<body>
<div class="page">
  <div class="brand">Wenshu · Analysis Report</div>
  <h1>{_esc(content.title)}</h1>
  <p class="meta">分析问题:{_esc(question)} &nbsp;·&nbsp; 生成时间:{now}</p>

  <section>
    <h2>总览</h2>
    <p class="overview">{_esc(content.overview)}</p>
  </section>
{chart_block}
  <section>
    <h2>关键发现</h2>
    <ul>{findings}</ul>
  </section>

  <section>
    <h2>建议</h2>
    <ul>{suggestions}</ul>
  </section>

  <section>
    <h2>数据明细</h2>
    <table><thead><tr>{thead}</tr></thead><tbody>{tbody}</tbody></table>
    {table_note}
  </section>
{sql_block}
  <footer><span>本报告由问数 Wenshu 基于查询结果自动生成</span><span>{now}</span></footer>
</div>
</body>
</html>"""


async def build_report_html(question: str, sql: str | None, rows: list[dict[str, Any]]) -> str:
    """报告生成主入口:LLM 分析与主图表并行执行,最后渲染 HTML。"""
    import asyncio

    content, chart_option = await asyncio.gather(
        _analyze(question, sql, rows),
        _main_chart_option(question, rows),
    )
    return _render_html(content, chart_option, rows, question, sql)
